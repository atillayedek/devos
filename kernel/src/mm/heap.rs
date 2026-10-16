//! Kernel heap: an address-ordered free list with coalescing, carved out of a
//! physically contiguous block reached through the direct map.

use core::alloc::{GlobalAlloc, Layout};
use core::ptr::null_mut;

use super::{frame, phys_to_virt, PAGE_SIZE};
use crate::sync::IrqLock;

const MIN_BLOCK: usize = 32;

#[repr(C)]
struct FreeBlock {
    size: usize,
    next: *mut FreeBlock,
}

struct Heap {
    head: *mut FreeBlock,
    size: usize,
}

// SAFETY: the free list is only touched under the lock.
unsafe impl Send for Heap {}

static HEAP: IrqLock<Heap> = IrqLock::new(Heap { head: null_mut(), size: 0 });

pub struct KernelAllocator;

#[global_allocator]
static ALLOCATOR: KernelAllocator = KernelAllocator;

/// Sizes the heap at 1/8 of free memory, between 16 MiB and 256 MiB.
pub fn init() -> u64 {
    let (_, free) = frame::stats();
    let bytes = (free / 8).clamp(16 << 20, 256 << 20) & !(PAGE_SIZE - 1);
    let phys = frame::alloc_contiguous(bytes / PAGE_SIZE).expect("cannot reserve the kernel heap");
    let start = phys_to_virt(phys) as *mut FreeBlock;
    // SAFETY: freshly allocated, direct-mapped and exclusively owned.
    unsafe { start.write(FreeBlock { size: bytes as usize, next: null_mut() }) };
    let mut h = HEAP.lock();
    h.head = start;
    h.size = bytes as usize;
    bytes
}

pub fn size() -> u64 {
    HEAP.lock().size as u64
}

/// Every block carries a one-word header storing its total size, placed right before
/// the aligned pointer handed out.
fn needed(layout: Layout) -> (usize, usize) {
    let align = layout.align().max(16);
    let size = (layout.size() + 16).max(MIN_BLOCK).next_multiple_of(16);
    (size, align)
}

unsafe impl GlobalAlloc for KernelAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let (size, align) = needed(layout);
        let mut h = HEAP.lock();
        let mut prev: *mut FreeBlock = null_mut();
        let mut cur = h.head;
        // SAFETY: every node in the list is a valid free block inside the heap.
        unsafe {
            while !cur.is_null() {
                let block_start = cur as usize;
                let block_end = block_start + (*cur).size;
                let mut user = (block_start + 16).next_multiple_of(align);
                // the front padding must be either zero or big enough to stay a free block
                while user - 16 - block_start != 0 && user - 16 - block_start < MIN_BLOCK {
                    user += align;
                }
                let pad = user - 16 - block_start;
                let pad_ok = true;
                let alloc_end = user - 16 + size;
                if pad_ok && alloc_end <= block_end {
                    let next = (*cur).next;
                    let tail = block_end - alloc_end;
                    let mut used_end = alloc_end;
                    let mut after = next;
                    if tail >= MIN_BLOCK {
                        let t = alloc_end as *mut FreeBlock;
                        t.write(FreeBlock { size: tail, next });
                        after = t;
                    } else {
                        used_end = block_end;
                    }
                    if pad > 0 {
                        (*cur).size = pad;
                        (*cur).next = after;
                    } else if prev.is_null() {
                        h.head = after;
                    } else {
                        (*prev).next = after;
                    }
                    let header = (user - 16) as *mut usize;
                    header.write(used_end - (user - 16));
                    return user as *mut u8;
                }
                prev = cur;
                cur = (*cur).next;
            }
        }
        null_mut()
    }

    unsafe fn dealloc(&self, ptr: *mut u8, _layout: Layout) {
        let start = ptr as usize - 16;
        // SAFETY: the header written by alloc() precedes every pointer we handed out.
        let size = unsafe { *(start as *const usize) };
        let mut h = HEAP.lock();
        // SAFETY: list nodes are valid free blocks; insertion keeps address order.
        unsafe {
            let mut prev: *mut FreeBlock = null_mut();
            let mut cur = h.head;
            while !cur.is_null() && (cur as usize) < start {
                prev = cur;
                cur = (*cur).next;
            }
            let node = start as *mut FreeBlock;
            node.write(FreeBlock { size, next: cur });
            if prev.is_null() {
                h.head = node;
            } else {
                (*prev).next = node;
            }
            // merge with the following block
            if !cur.is_null() && start + size == cur as usize {
                (*node).size += (*cur).size;
                (*node).next = (*cur).next;
            }
            // merge with the preceding block
            if !prev.is_null() && prev as usize + (*prev).size == start {
                (*prev).size += (*node).size;
                (*prev).next = (*node).next;
            }
        }
    }
}
