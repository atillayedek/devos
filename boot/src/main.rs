//! devos-boot — UEFI bootloader for DevOS.
//!
//! 1. Loads `\devos\kernel.elf` and `\devos\init.elf` from the boot partition.
//! 2. Loads the kernel's PT_LOAD segments into fresh physical pages.
//! 3. Builds 4-level page tables: identity map + direct map ([`PHYS_OFFSET`]) of all
//!    physical memory (2 MiB pages), the kernel at its link address, the kernel stack.
//! 4. Exits boot services, converts the UEFI memory map, and jumps to the kernel.
#![no_std]
#![no_main]

extern crate alloc;

use alloc::vec::Vec;
use core::arch::asm;
use core::ptr;

use devos_bootinfo::{
    BootInfo, Framebuffer, MemoryKind, MemoryRegion, PixelFormat, KERNEL_STACK_SIZE, MAGIC, PHYS_OFFSET, VERSION,
};
use log::info;
use uefi::boot::{self, AllocateType, MemoryType};
use uefi::mem::memory_map::MemoryMap;
use uefi::prelude::*;
use uefi::proto::console::gop::{GraphicsOutput, PixelFormat as GopFormat};
use uefi::table::cfg::ConfigTableEntry;
use xmas_elf::program::Type as PhType;
use xmas_elf::ElfFile;

const PAGE: u64 = 4096;
const HUGE: u64 = 2 * 1024 * 1024;
/// Memory the kernel owns from the start (image, stack, page tables, boot info, init).
const KERNEL_MEM: MemoryType = MemoryType::custom(0x8000_0001);
const KERNEL_STACK_TOP: u64 = 0xFFFF_FFFF_C000_0000;

const P_PRESENT: u64 = 1;
const P_WRITE: u64 = 1 << 1;
const P_HUGE: u64 = 1 << 7;
const P_NX: u64 = 1 << 63;

#[entry]
fn main() -> Status {
    uefi::helpers::init().expect("uefi init");
    info!("DevOS boot loader {}", env!("CARGO_PKG_VERSION"));

    let kernel = read_file(cstr16!("\\devos\\kernel.elf"));
    let init = read_file(cstr16!("\\devos\\init.elf"));
    let fb = framebuffer();
    let rsdp = uefi::system::with_config_table(|t| {
        t.iter().find(|e| e.guid == ConfigTableEntry::ACPI2_GUID).map(|e| e.address as u64).unwrap_or(0)
    });

    let mut pt = PageTables::new();
    let max_phys = highest_physical_address().max(fb.phys_addr + fb.size);
    pt.map_physical(max_phys);

    let elf = ElfFile::new(&kernel).expect("kernel.elf is not a valid ELF file");
    let entry = elf.header.pt2.entry_point();
    let (kstart, kend) = load_kernel(&elf, &mut pt);

    // Kernel stack (the page below it stays unmapped as a guard page)
    let stack_pages = KERNEL_STACK_SIZE / PAGE;
    let stack = alloc_pages(stack_pages);
    for i in 0..stack_pages {
        pt.map_4k(KERNEL_STACK_TOP - KERNEL_STACK_SIZE + i * PAGE, stack + i * PAGE, P_WRITE | P_NX);
    }

    // init image in kernel-owned memory
    let init_pages = (init.len() as u64).div_ceil(PAGE);
    let init_phys = alloc_pages(init_pages);
    // SAFETY: freshly allocated pages, identity-mapped by the firmware, large enough.
    unsafe { ptr::copy_nonoverlapping(init.as_ptr(), init_phys as *mut u8, init.len()) };

    // Boot info + room for the final memory map (allocated before ExitBootServices).
    let region_capacity = boot::memory_map(MemoryType::LOADER_DATA).map(|m| m.len()).unwrap_or(128) * 2 + 64;
    let bi_bytes = size_of::<BootInfo>() as u64 + (region_capacity * size_of::<MemoryRegion>()) as u64;
    let bi_phys = alloc_pages(bi_bytes.div_ceil(PAGE));
    let regions_phys = bi_phys + size_of::<BootInfo>() as u64;

    info!(
        "kernel {:#x}..{:#x} entry {:#x}, init {} bytes, framebuffer {}x{}",
        kstart, kend, entry, init.len(), fb.width, fb.height
    );
    let init_len = init.len() as u64;
    drop(kernel);
    drop(init);

    // SAFETY: no boot services are used after this point.
    let mmap = unsafe { boot::exit_boot_services(None) };
    let mut count = 0usize;
    for d in mmap.entries() {
        if count == region_capacity {
            break;
        }
        let kind = match d.ty {
            MemoryType::CONVENTIONAL | MemoryType::BOOT_SERVICES_CODE | MemoryType::BOOT_SERVICES_DATA => {
                MemoryKind::Usable
            }
            MemoryType::LOADER_CODE | MemoryType::LOADER_DATA => MemoryKind::BootloaderReclaimable,
            KERNEL_MEM => MemoryKind::Kernel,
            MemoryType::ACPI_RECLAIM => MemoryKind::AcpiReclaimable,
            MemoryType::ACPI_NON_VOLATILE => MemoryKind::AcpiNvs,
            MemoryType::MMIO | MemoryType::MMIO_PORT_SPACE => MemoryKind::Mmio,
            _ => MemoryKind::Reserved,
        };
        let region = MemoryRegion { start: d.phys_start, len: d.page_count * PAGE, kind, _pad: 0 };
        // SAFETY: regions_phys has room for region_capacity entries.
        unsafe { (regions_phys as *mut MemoryRegion).add(count).write(region) };
        count += 1;
    }
    let info = BootInfo {
        magic: MAGIC,
        version: VERSION,
        _pad: 0,
        phys_offset: PHYS_OFFSET,
        framebuffer: fb,
        memory_regions: PHYS_OFFSET + regions_phys,
        memory_region_count: count as u64,
        rsdp_phys: rsdp,
        kernel_phys_start: kstart,
        kernel_phys_end: kend,
        init_phys,
        init_len,
    };
    // SAFETY: bi_phys is a kernel-owned page sized for BootInfo.
    unsafe { (bi_phys as *mut BootInfo).write(info) };

    // SAFETY: the new page tables identity-map this code, map the kernel at its link
    // address and the stack at KERNEL_STACK_TOP; nothing after the CR3 write touches
    // firmware state.
    unsafe {
        asm!(
            "mov cr3, {pml4}",
            "mov rsp, {stack}",
            "xor ebp, ebp",
            "jmp {entry}",
            pml4 = in(reg) pt.pml4,
            stack = in(reg) KERNEL_STACK_TOP,
            entry = in(reg) entry,
            in("rdi") PHYS_OFFSET + bi_phys,
            options(noreturn)
        );
    }
}

fn read_file(path: &uefi::CStr16) -> Vec<u8> {
    let fs = boot::get_image_file_system(boot::image_handle()).expect("boot file system");
    let mut fs = uefi::fs::FileSystem::new(fs);
    fs.read(path).unwrap_or_else(|e| panic!("cannot read {}: {:?}", path, e))
}

fn framebuffer() -> Framebuffer {
    let none = Framebuffer { phys_addr: 0, size: 0, width: 0, height: 0, stride: 0, format: PixelFormat::None };
    let Ok(handle) = boot::get_handle_for_protocol::<GraphicsOutput>() else { return none };
    let Ok(mut gop) = boot::open_protocol_exclusive::<GraphicsOutput>(handle) else { return none };
    // Prefer 1920x1080, otherwise the largest mode not above it.
    let best = gop
        .modes()
        .filter(|m| matches!(m.info().pixel_format(), GopFormat::Rgb | GopFormat::Bgr))
        .filter(|m| m.info().resolution().0 <= 1920 && m.info().resolution().1 <= 1080)
        .max_by_key(|m| m.info().resolution().0 * m.info().resolution().1);
    if let Some(mode) = best {
        let _ = gop.set_mode(&mode);
    }
    let mi = gop.current_mode_info();
    let format = match mi.pixel_format() {
        GopFormat::Rgb => PixelFormat::Rgb,
        GopFormat::Bgr => PixelFormat::Bgr,
        _ => return none,
    };
    let mut fbuf = gop.frame_buffer();
    Framebuffer {
        phys_addr: fbuf.as_mut_ptr() as u64,
        size: fbuf.size() as u64,
        width: mi.resolution().0 as u32,
        height: mi.resolution().1 as u32,
        stride: mi.stride() as u32,
        format,
    }
}

fn highest_physical_address() -> u64 {
    let map = boot::memory_map(MemoryType::LOADER_DATA).expect("memory map");
    map.entries().map(|d| d.phys_start + d.page_count * PAGE).max().unwrap_or(4 << 30).max(4 << 30)
}

fn alloc_pages(count: u64) -> u64 {
    let p = boot::allocate_pages(AllocateType::AnyPages, KERNEL_MEM, count as usize).expect("out of memory");
    let addr = p.as_ptr() as u64;
    // SAFETY: freshly allocated, identity-mapped pages.
    unsafe { ptr::write_bytes(addr as *mut u8, 0, (count * PAGE) as usize) };
    addr
}

fn load_kernel(elf: &ElfFile, pt: &mut PageTables) -> (u64, u64) {
    let (mut lo, mut hi) = (u64::MAX, 0u64);
    for ph in elf.program_iter() {
        if ph.get_type() != Ok(PhType::Load) || ph.mem_size() == 0 {
            continue;
        }
        let vstart = ph.virtual_addr() & !(PAGE - 1);
        let vend = (ph.virtual_addr() + ph.mem_size()).div_ceil(PAGE) * PAGE;
        let pages = (vend - vstart) / PAGE;
        let phys = alloc_pages(pages);
        let off_in_page = ph.virtual_addr() - vstart;
        let data = &elf.input[ph.offset() as usize..(ph.offset() + ph.file_size()) as usize];
        // SAFETY: phys has `pages` zeroed pages; the copy stays inside them.
        unsafe { ptr::copy_nonoverlapping(data.as_ptr(), (phys + off_in_page) as *mut u8, data.len()) };
        let mut flags = 0;
        if ph.flags().is_write() {
            flags |= P_WRITE;
        }
        if !ph.flags().is_execute() {
            flags |= P_NX;
        }
        for i in 0..pages {
            pt.map_4k(vstart + i * PAGE, phys + i * PAGE, flags);
        }
        lo = lo.min(phys);
        hi = hi.max(phys + pages * PAGE);
    }
    (lo, hi)
}

/// Builder for the kernel's initial 4-level page tables.
struct PageTables {
    pml4: u64,
}

impl PageTables {
    fn new() -> Self {
        PageTables { pml4: alloc_pages(1) }
    }

    fn table(entry: *mut u64) -> u64 {
        // SAFETY: entries belong to page tables allocated by alloc_pages (identity-mapped).
        unsafe {
            if *entry & P_PRESENT == 0 {
                *entry = alloc_pages(1) | P_PRESENT | P_WRITE;
            }
            *entry & 0x000F_FFFF_FFFF_F000
        }
    }

    fn entry(table: u64, index: u64) -> *mut u64 {
        (table + index * 8) as *mut u64
    }

    fn map_4k(&mut self, virt: u64, phys: u64, flags: u64) {
        let pdpt = Self::table(Self::entry(self.pml4, (virt >> 39) & 511));
        let pd = Self::table(Self::entry(pdpt, (virt >> 30) & 511));
        let pt = Self::table(Self::entry(pd, (virt >> 21) & 511));
        // SAFETY: valid page table entry pointer.
        unsafe { *Self::entry(pt, (virt >> 12) & 511) = phys | flags | P_PRESENT };
    }

    fn map_2m(&mut self, virt: u64, phys: u64, flags: u64) {
        let pdpt = Self::table(Self::entry(self.pml4, (virt >> 39) & 511));
        let pd = Self::table(Self::entry(pdpt, (virt >> 30) & 511));
        // SAFETY: valid page table entry pointer.
        unsafe { *Self::entry(pd, (virt >> 21) & 511) = phys | flags | P_PRESENT | P_HUGE };
    }

    /// Identity map + direct map of [0, max) with 2 MiB pages.
    fn map_physical(&mut self, max: u64) {
        let end = max.div_ceil(HUGE) * HUGE;
        let mut p = 0;
        while p < end {
            self.map_2m(p, p, P_WRITE);
            self.map_2m(PHYS_OFFSET + p, p, P_WRITE | P_NX);
            p += HUGE;
        }
    }
}
