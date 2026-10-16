//! DevOS boot protocol: the structure devos-boot hands to the kernel.
//!
//! Address space at kernel entry (built by the bootloader):
//! * all physical memory is mapped at [`PHYS_OFFSET`] (direct map) *and* identity-mapped;
//!   the kernel drops the identity map once it has its own page tables,
//! * the kernel ELF is mapped at its link address (higher half),
//! * `rsp` points to the top of a [`KERNEL_STACK_SIZE`] stack, `rdi` holds `&BootInfo`.
#![no_std]

/// "DEVOSBI1" — identifies a valid [`BootInfo`].
pub const MAGIC: u64 = u64::from_le_bytes(*b"DEVOSBI1");
pub const VERSION: u32 = 1;
/// Base of the direct map of physical memory.
pub const PHYS_OFFSET: u64 = 0xFFFF_8000_0000_0000;
pub const KERNEL_STACK_SIZE: u64 = 256 * 1024;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct BootInfo {
    pub magic: u64,
    pub version: u32,
    pub _pad: u32,
    pub phys_offset: u64,
    pub framebuffer: Framebuffer,
    /// Virtual address (direct map) of the memory region array.
    pub memory_regions: u64,
    pub memory_region_count: u64,
    /// Physical address of the ACPI 2.0 RSDP, 0 if absent.
    pub rsdp_phys: u64,
    pub kernel_phys_start: u64,
    pub kernel_phys_end: u64,
    /// The first userspace program (`/devos/init.elf`), physical address and size.
    pub init_phys: u64,
    pub init_len: u64,
}

impl BootInfo {
    pub fn regions(&self) -> &[MemoryRegion] {
        // SAFETY: the bootloader stores `memory_region_count` initialised regions at
        // `memory_regions`, in kernel-reserved memory that is never reused.
        unsafe {
            core::slice::from_raw_parts(self.memory_regions as *const MemoryRegion, self.memory_region_count as usize)
        }
    }
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct Framebuffer {
    pub phys_addr: u64,
    pub size: u64,
    pub width: u32,
    pub height: u32,
    /// Pixels per scan line.
    pub stride: u32,
    pub format: PixelFormat,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PixelFormat {
    /// 32-bit, byte order R, G, B, reserved.
    Rgb = 0,
    /// 32-bit, byte order B, G, R, reserved.
    Bgr = 1,
    /// No linear framebuffer (headless).
    None = 2,
}

#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemoryKind {
    Usable = 1,
    /// Bootloader data; free once the kernel no longer needs `BootInfo`.
    BootloaderReclaimable = 2,
    /// Kernel image, stack, initial page tables, boot info, init image.
    Kernel = 3,
    AcpiReclaimable = 4,
    AcpiNvs = 5,
    Reserved = 6,
    Mmio = 7,
    Framebuffer = 8,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct MemoryRegion {
    pub start: u64,
    pub len: u64,
    pub kind: MemoryKind,
    pub _pad: u32,
}
