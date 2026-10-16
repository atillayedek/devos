//! Virtual memory: 4-level page tables accessed through the direct map.
//!
//! The kernel half (PML4 entries 256..512) is shared by every address space; user
//! address spaces own their lower half and every frame mapped there.

use alloc::vec::Vec;

use super::{frame, phys_to_virt, PAGE_SIZE};
use crate::arch;
use crate::sync::IrqLock;

pub const PRESENT: u64 = 1;
pub const WRITABLE: u64 = 1 << 1;
pub const USER: u64 = 1 << 2;
pub const HUGE: u64 = 1 << 7;
pub const NO_EXECUTE: u64 = 1 << 63;
const ADDR_MASK: u64 = 0x000F_FFFF_FFFF_F000;

static KERNEL_PML4: IrqLock<u64> = IrqLock::new(0);

fn table(phys: u64) -> &'static mut [u64; 512] {
    // SAFETY: `phys` is a page-table frame owned by the paging code, direct-mapped.
    unsafe { &mut *(phys_to_virt(phys) as *mut [u64; 512]) }
}

/// Adopts the bootloader's tables as the kernel address space and removes the
/// identity map of low memory (everything the kernel needs is in the higher half).
pub fn init() {
    let pml4 = arch::read_cr3() & ADDR_MASK;
    let t = table(pml4);
    for e in t.iter_mut().take(256) {
        *e = 0;
    }
    // SAFETY: reloading the same PML4 flushes the dropped identity mappings.
    unsafe { arch::write_cr3(pml4) };
    *KERNEL_PML4.lock() = pml4;
}

pub fn kernel_pml4() -> u64 {
    *KERNEL_PML4.lock()
}

pub struct AddressSpace {
    pub pml4: u64,
    /// Every frame owned by this address space (page tables and mapped user memory).
    owned: Vec<u64>,
}

impl AddressSpace {
    /// A fresh user address space sharing the kernel half.
    pub fn new_user() -> Option<AddressSpace> {
        let pml4 = frame::alloc()?;
        let kernel = table(kernel_pml4());
        let t = table(pml4);
        t[256..].copy_from_slice(&kernel[256..]);
        Some(AddressSpace { pml4, owned: alloc::vec![pml4] })
    }

    fn next_level(&mut self, entry: &mut u64, user: bool) -> Option<u64> {
        if *entry & PRESENT == 0 {
            let f = frame::alloc()?;
            self.owned.push(f);
            *entry = f | PRESENT | WRITABLE | if user { USER } else { 0 };
        }
        assert!(*entry & HUGE == 0, "huge page in the middle of a 4 KiB mapping");
        Some(*entry & ADDR_MASK)
    }

    /// Maps one 4 KiB page.
    pub fn map(&mut self, virt: u64, phys: u64, flags: u64) -> Option<()> {
        let user = flags & USER != 0;
        let l4 = table(self.pml4);
        let l3 = self.next_level(&mut l4[((virt >> 39) & 511) as usize], user)?;
        let l2 = self.next_level(&mut table(l3)[((virt >> 30) & 511) as usize], user)?;
        let l1 = self.next_level(&mut table(l2)[((virt >> 21) & 511) as usize], user)?;
        table(l1)[((virt >> 12) & 511) as usize] = (phys & ADDR_MASK) | flags | PRESENT;
        arch::invlpg(virt);
        Some(())
    }

    /// Allocates, zeroes and maps `pages` frames at `virt`; the frames belong to this space.
    pub fn map_new(&mut self, virt: u64, pages: u64, flags: u64) -> Option<()> {
        for i in 0..pages {
            let f = frame::alloc()?;
            self.owned.push(f);
            self.map(virt + i * PAGE_SIZE, f, flags)?;
        }
        Some(())
    }

    /// Physical address and flags of the page containing `virt`, if mapped.
    pub fn translate(&self, virt: u64) -> Option<(u64, u64)> {
        let mut t = self.pml4;
        for shift in [39u64, 30, 21] {
            let e = table(t)[((virt >> shift) & 511) as usize];
            if e & PRESENT == 0 {
                return None;
            }
            if e & HUGE != 0 {
                let size = 1u64 << shift;
                return Some(((e & ADDR_MASK & !(size - 1)) + (virt & (size - 1)), e));
            }
            t = e & ADDR_MASK;
        }
        let e = table(t)[((virt >> 12) & 511) as usize];
        if e & PRESENT == 0 {
            return None;
        }
        Some(((e & ADDR_MASK) + (virt & (PAGE_SIZE - 1)), e))
    }

    /// Copies user memory into `buf`, failing if any byte is not user-readable.
    pub fn copy_from_user(&self, addr: u64, buf: &mut [u8]) -> bool {
        let len = buf.len() as u64;
        if addr.checked_add(len).is_none_or(|end| end > devos_abi::USER_END) {
            return false;
        }
        let mut done = 0u64;
        while done < len {
            let va = addr + done;
            let Some((pa, flags)) = self.translate(va) else { return false };
            if flags & USER == 0 {
                return false;
            }
            let chunk = (PAGE_SIZE - (va & (PAGE_SIZE - 1))).min(len - done);
            // SAFETY: `pa` is a mapped user frame, direct-mapped; chunk stays within the page.
            unsafe {
                core::ptr::copy_nonoverlapping(phys_to_virt(pa) as *const u8,
                                               buf[done as usize..].as_mut_ptr(), chunk as usize)
            };
            done += chunk;
        }
        true
    }

    /// Copies `data` into writable user memory at `addr`.
    pub fn copy_to_user(&self, addr: u64, data: &[u8]) -> bool {
        let len = data.len() as u64;
        if addr.checked_add(len).is_none_or(|end| end > devos_abi::USER_END) {
            return false;
        }
        let mut done = 0u64;
        while done < len {
            let va = addr + done;
            let Some((pa, flags)) = self.translate(va) else { return false };
            if flags & USER == 0 || flags & WRITABLE == 0 {
                return false;
            }
            let chunk = (PAGE_SIZE - (va & (PAGE_SIZE - 1))).min(len - done);
            // SAFETY: as in copy_from_user, the target is a writable user page.
            unsafe {
                core::ptr::copy_nonoverlapping(data[done as usize..].as_ptr(),
                                               phys_to_virt(pa) as *mut u8, chunk as usize)
            };
            done += chunk;
        }
        true
    }
}

impl Drop for AddressSpace {
    fn drop(&mut self) {
        for f in self.owned.drain(..) {
            frame::free(f);
        }
    }
}
