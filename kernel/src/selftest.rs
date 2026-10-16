//! Boot-time kernel self tests (`--features selftest`), run before init starts.

use alloc::boxed::Box;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicU64, Ordering};

use crate::mm::paging::{AddressSpace, USER, WRITABLE};
use crate::mm::{frame, PAGE_SIZE};
use crate::{kok, sched};

fn check(cond: bool, what: &str) {
    if !cond {
        panic!("selftest failed: {}", what);
    }
}

pub fn run() {
    // physical frames
    let (_, free_before) = frame::stats();
    let frames: Vec<u64> = (0..256).map(|_| frame::alloc().expect("frame")).collect();
    check(frames.iter().all(|f| f % PAGE_SIZE == 0), "frames are page aligned");
    let mut sorted = frames.clone();
    sorted.sort_unstable();
    sorted.dedup();
    check(sorted.len() == frames.len(), "frames are unique");
    frames.iter().for_each(|&f| frame::free(f));
    check(frame::stats().1 == free_before, "frames are returned");
    kok!("selftest: frame allocator (256 alloc/free, unique, aligned)");

    // heap: mixed sizes/alignments, then everything freed and reusable
    let mut boxes: Vec<Box<[u8]>> = Vec::new();
    for i in 0..2000usize {
        let len = (i * 37) % 3000 + 1;
        let mut b = alloc::vec![0u8; len].into_boxed_slice();
        b[len - 1] = (i % 251) as u8;
        boxes.push(b);
    }
    for (i, b) in boxes.iter().enumerate() {
        check(b[b.len() - 1] == (i % 251) as u8, "heap contents intact");
    }
    drop(boxes);
    let big = alloc::vec![7u8; 8 << 20];
    check(big[(8 << 20) - 1] == 7, "8 MiB allocation after churn");
    drop(big);
    #[repr(align(4096))]
    struct Aligned([u8; 64]);
    let a = Box::new(Aligned([1; 64]));
    check((&*a as *const Aligned as usize) % 4096 == 0, "4 KiB aligned allocation");
    kok!("selftest: heap (2000 mixed allocations, 8 MiB block, 4 KiB alignment)");

    // paging + user copies
    let mut space = AddressSpace::new_user().expect("address space");
    let va = 0x40_0000;
    space.map_new(va, 2, USER | WRITABLE).expect("map");
    check(space.translate(va).is_some() && space.translate(va + 2 * PAGE_SIZE).is_none(), "translate");
    let msg = b"cross-page user copy test";
    check(space.copy_to_user(va + PAGE_SIZE - 5, msg), "copy_to_user across pages");
    let mut back = [0u8; 25];
    check(space.copy_from_user(va + PAGE_SIZE - 5, &mut back) && &back == msg, "copy_from_user");
    check(!space.copy_from_user(0x7FFF_0000_0000, &mut back), "unmapped user memory is rejected");
    check(!space.copy_from_user(0xFFFF_8000_0000_0000, &mut back), "kernel memory is rejected");
    drop(space);
    kok!("selftest: paging (map, translate, user copies, kernel/unmapped rejected)");

    // scheduler: two threads sleep and count
    static COUNT: AtomicU64 = AtomicU64::new(0);
    extern "C" fn worker(n: u64) -> ! {
        for _ in 0..n {
            COUNT.fetch_add(1, Ordering::SeqCst);
            sched::sleep_ms(10);
        }
        sched::exit_current(0)
    }
    sched::spawn("worker-a", 0, worker, 5, None);
    sched::spawn("worker-b", 0, worker, 5, None);
    let start = sched::uptime_ms();
    while COUNT.load(Ordering::SeqCst) < 10 && sched::uptime_ms() - start < 5000 {
        sched::sleep_ms(10);
    }
    check(COUNT.load(Ordering::SeqCst) == 10, "two sleeping kernel threads made progress");
    kok!("selftest: scheduler (preemption, sleep, thread exit) in {} ms", sched::uptime_ms() - start);
}
