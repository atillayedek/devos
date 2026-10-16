//! Memory management: physical frames, the kernel heap and virtual address spaces.

pub mod frame;
pub mod heap;
pub mod paging;

use devos_bootinfo::{BootInfo, PHYS_OFFSET};

pub const PAGE_SIZE: u64 = 4096;

#[inline]
pub fn phys_to_virt(phys: u64) -> u64 {
    phys + PHYS_OFFSET
}

#[inline]
pub fn virt_to_phys_direct(virt: u64) -> u64 {
    virt - PHYS_OFFSET
}

pub struct MemoryStats {
    pub total: u64,
    pub free: u64,
    pub heap: u64,
}

pub fn init(boot: &BootInfo) -> MemoryStats {
    frame::init(boot.regions());
    let heap = heap::init();
    paging::init();
    stats_with_heap(heap)
}

pub fn stats() -> MemoryStats {
    stats_with_heap(heap::size())
}

fn stats_with_heap(heap: u64) -> MemoryStats {
    let (total, free) = frame::stats();
    MemoryStats { total, free, heap }
}
