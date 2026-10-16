//! User processes: ELF64 loading into a fresh address space and the jump to ring 3.

use alloc::boxed::Box;
use core::sync::atomic::{AtomicU64, Ordering};

use devos_abi::{USER_END, USER_STACK_SIZE, USER_STACK_TOP, USER_START};

use crate::arch::syscall;
use crate::mm::paging::{AddressSpace, NO_EXECUTE, USER, WRITABLE};
use crate::mm::{phys_to_virt, PAGE_SIZE};

static NEXT_PID: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
pub enum LoadError {
    NotElf,
    WrongArch,
    BadSegment,
    OutOfMemory,
}

struct Segment {
    vaddr: u64,
    offset: u64,
    filesz: u64,
    memsz: u64,
    flags: u32,
}

fn u16_at(b: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([b[o], b[o + 1]])
}
fn u32_at(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(b[o..o + 4].try_into().unwrap())
}
fn u64_at(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(b[o..o + 8].try_into().unwrap())
}

/// Validates the ELF header and returns (entry, PT_LOAD segments).
fn parse(image: &[u8]) -> Result<(u64, alloc::vec::Vec<Segment>), LoadError> {
    if image.len() < 64 || &image[..4] != b"\x7fELF" {
        return Err(LoadError::NotElf);
    }
    // 64-bit, little endian, executable, x86_64
    if image[4] != 2 || image[5] != 1 || u16_at(image, 16) != 2 || u16_at(image, 18) != 0x3E {
        return Err(LoadError::WrongArch);
    }
    let entry = u64_at(image, 24);
    let phoff = u64_at(image, 32) as usize;
    let phentsize = u16_at(image, 54) as usize;
    let phnum = u16_at(image, 56) as usize;
    let mut segs = alloc::vec::Vec::new();
    for i in 0..phnum {
        let o = phoff + i * phentsize;
        if o + 56 > image.len() {
            return Err(LoadError::BadSegment);
        }
        if u32_at(image, o) != 1 {
            continue; // not PT_LOAD
        }
        let seg = Segment {
            flags: u32_at(image, o + 4),
            offset: u64_at(image, o + 8),
            vaddr: u64_at(image, o + 16),
            filesz: u64_at(image, o + 32),
            memsz: u64_at(image, o + 40),
        };
        let end = seg.vaddr.checked_add(seg.memsz).ok_or(LoadError::BadSegment)?;
        if seg.vaddr < USER_START || end > USER_END || seg.filesz > seg.memsz
            || seg.offset + seg.filesz > image.len() as u64
        {
            return Err(LoadError::BadSegment);
        }
        segs.push(seg);
    }
    if !(USER_START..USER_END).contains(&entry) {
        return Err(LoadError::BadSegment);
    }
    Ok((entry, segs))
}

struct Start {
    entry: u64,
}

/// Loads `image` and starts it as a new process; returns the pid.
pub fn spawn_elf(name: &str, image: &[u8]) -> Result<u64, LoadError> {
    let (entry, segs) = parse(image)?;
    let mut space = AddressSpace::new_user().ok_or(LoadError::OutOfMemory)?;
    for s in &segs {
        let start = s.vaddr & !(PAGE_SIZE - 1);
        let end = (s.vaddr + s.memsz).div_ceil(PAGE_SIZE) * PAGE_SIZE;
        let mut flags = USER;
        if s.flags & 2 != 0 {
            flags |= WRITABLE;
        }
        if s.flags & 1 == 0 {
            flags |= NO_EXECUTE;
        }
        for page in (start..end).step_by(PAGE_SIZE as usize) {
            if space.translate(page).is_none() {
                space.map_new(page, 1, flags).ok_or(LoadError::OutOfMemory)?;
            }
        }
        // copy file contents through the direct map (pages may be read-only for the user)
        let data = &image[s.offset as usize..(s.offset + s.filesz) as usize];
        for (i, chunk) in data.chunks(PAGE_SIZE as usize).enumerate() {
            let va = s.vaddr + (i as u64) * PAGE_SIZE;
            let mut copied = 0usize;
            while copied < chunk.len() {
                let (pa, _) = space.translate(va + copied as u64).ok_or(LoadError::BadSegment)?;
                let room = (PAGE_SIZE - ((va + copied as u64) & (PAGE_SIZE - 1))) as usize;
                let n = room.min(chunk.len() - copied);
                // SAFETY: `pa` is a frame this address space owns; n stays inside the page.
                unsafe {
                    core::ptr::copy_nonoverlapping(chunk[copied..].as_ptr(), phys_to_virt(pa) as *mut u8, n)
                };
                copied += n;
            }
        }
    }
    let stack_pages = USER_STACK_SIZE / PAGE_SIZE;
    space
        .map_new(USER_STACK_TOP - USER_STACK_SIZE, stack_pages, USER | WRITABLE | NO_EXECUTE)
        .ok_or(LoadError::OutOfMemory)?;
    let pid = NEXT_PID.fetch_add(1, Ordering::Relaxed) + 100;
    let start = Box::into_raw(Box::new(Start { entry }));
    super::spawn(name, pid, user_thread_entry, start as u64, Some(space));
    Ok(pid)
}

extern "C" fn user_thread_entry(arg: u64) -> ! {
    // SAFETY: `arg` is the Box<Start> leaked by spawn_elf for this thread only.
    let start = unsafe { Box::from_raw(arg as *mut Start) };
    let entry = start.entry;
    drop(start);
    syscall::jump_to_user(entry, USER_STACK_TOP - 16)
}
