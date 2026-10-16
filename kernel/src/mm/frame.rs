//! Physical frame allocator: one bit per 4 KiB frame (1 = in use).

use devos_bootinfo::{MemoryKind, MemoryRegion};

use super::{phys_to_virt, PAGE_SIZE};
use crate::sync::IrqLock;

struct Bitmap {
    bits: *mut u64,
    frames: u64,
    free: u64,
    usable_total: u64,
    hint: u64,
}

// SAFETY: the bitmap lives in kernel-owned memory and is only accessed under the lock.
unsafe impl Send for Bitmap {}

static FRAMES: IrqLock<Bitmap> =
    IrqLock::new(Bitmap { bits: core::ptr::null_mut(), frames: 0, free: 0, usable_total: 0, hint: 0 });

impl Bitmap {
    fn set(&mut self, frame: u64, used: bool) {
        // SAFETY: frame < self.frames, so the word index is inside the bitmap.
        let word = unsafe { &mut *self.bits.add((frame / 64) as usize) };
        let mask = 1u64 << (frame % 64);
        let was = *word & mask != 0;
        if used && !was {
            *word |= mask;
            self.free -= 1;
        } else if !used && was {
            *word &= !mask;
            self.free += 1;
        }
    }

    fn is_used(&self, frame: u64) -> bool {
        // SAFETY: frame < self.frames.
        unsafe { *self.bits.add((frame / 64) as usize) & (1 << (frame % 64)) != 0 }
    }
}

pub fn init(regions: &[MemoryRegion]) {
    let top = regions
        .iter()
        .filter(|r| matches!(r.kind, MemoryKind::Usable | MemoryKind::BootloaderReclaimable | MemoryKind::Kernel))
        .map(|r| r.start + r.len)
        .max()
        .expect("no usable memory");
    let frames = top / PAGE_SIZE;
    let bytes = frames.div_ceil(64) * 8;
    // Place the bitmap in the first usable region large enough to hold it.
    let home = regions
        .iter()
        .find(|r| r.kind == MemoryKind::Usable && r.len >= bytes + PAGE_SIZE && r.start >= 0x10_0000)
        .expect("no room for the frame bitmap");
    let bits = phys_to_virt(home.start) as *mut u64;
    // Everything starts as used; usable regions are then released.
    // SAFETY: `home` is usable RAM, direct-mapped, at least `bytes` long.
    unsafe { core::ptr::write_bytes(bits as *mut u8, 0xFF, bytes as usize) };
    let mut bm = FRAMES.lock();
    *bm = Bitmap { bits, frames, free: 0, usable_total: 0, hint: 0 };
    for r in regions.iter().filter(|r| r.kind == MemoryKind::Usable) {
        let first = r.start.div_ceil(PAGE_SIZE);
        let last = (r.start + r.len) / PAGE_SIZE;
        for f in first..last {
            bm.set(f, false);
        }
        bm.usable_total += last.saturating_sub(first);
    }
    // Never hand out the first MiB (real-mode structures, SMP trampolines later)
    for f in 0..(0x10_0000 / PAGE_SIZE).min(frames) {
        bm.set(f, true);
    }
    // The bitmap's own frames
    let first = home.start / PAGE_SIZE;
    for f in first..first + bytes.div_ceil(PAGE_SIZE) {
        bm.set(f, true);
    }
}

/// Allocates one zeroed frame; returns its physical address.
pub fn alloc() -> Option<u64> {
    alloc_contiguous(1)
}

/// Allocates `count` physically contiguous, zeroed frames.
pub fn alloc_contiguous(count: u64) -> Option<u64> {
    let mut bm = FRAMES.lock();
    let n = bm.frames;
    let mut start = bm.hint;
    let mut scanned = 0;
    while scanned < n {
        if start + count > n {
            start = 0;
        }
        let mut run = 0;
        while run < count && !bm.is_used(start + run) {
            run += 1;
        }
        if run == count {
            for f in start..start + count {
                bm.set(f, true);
            }
            bm.hint = start + count;
            drop(bm);
            let phys = start * PAGE_SIZE;
            // SAFETY: freshly allocated frames, direct-mapped.
            unsafe { core::ptr::write_bytes(phys_to_virt(phys) as *mut u8, 0, (count * PAGE_SIZE) as usize) };
            return Some(phys);
        }
        start += run + 1;
        scanned += run + 1;
    }
    None
}

pub fn free(phys: u64) {
    let mut bm = FRAMES.lock();
    let f = phys / PAGE_SIZE;
    assert!(f < bm.frames && bm.is_used(f), "double free of frame {:#x}", phys);
    bm.set(f, false);
}

/// (total usable bytes, free bytes)
pub fn stats() -> (u64, u64) {
    let bm = FRAMES.lock();
    (bm.usable_total * PAGE_SIZE, bm.free * PAGE_SIZE)
}
