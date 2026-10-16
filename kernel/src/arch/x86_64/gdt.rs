//! Global descriptor table and task state segment.
//!
//! Selector layout is dictated by `syscall`/`sysret`: kernel CS, kernel SS, then
//! user SS directly followed by user CS (STAR[63:48] + 8 / + 16).

use core::arch::asm;
use core::mem::size_of;
use core::ptr::addr_of;

pub const KERNEL_CS: u16 = 0x08;
pub const KERNEL_SS: u16 = 0x10;
pub const USER_SS: u16 = 0x18 | 3;
pub const USER_CS: u16 = 0x20 | 3;
const TSS_SEL: u16 = 0x28;

/// Interrupt stack table slot used for double faults.
pub const IST_DOUBLE_FAULT: u8 = 1;

#[repr(C, packed(4))]
pub struct Tss {
    _r0: u32,
    pub rsp: [u64; 3],
    _r1: u64,
    pub ist: [u64; 7],
    _r2: u64,
    _r3: u16,
    iomap_base: u16,
}

static mut TSS: Tss = Tss { _r0: 0, rsp: [0; 3], _r1: 0, ist: [0; 7], _r2: 0, _r3: 0, iomap_base: size_of::<Tss>() as u16 };
static mut GDT: [u64; 7] = [0; 7];

#[repr(align(16))]
struct Stack([u8; 16 * 1024]);
static mut DOUBLE_FAULT_STACK: Stack = Stack([0; 16 * 1024]);

#[repr(C, packed)]
struct Pointer {
    limit: u16,
    base: u64,
}

pub fn init() {
    // SAFETY: called once on the boot CPU before interrupts are enabled; nothing else
    // references GDT/TSS yet.
    unsafe {
        TSS.ist[(IST_DOUBLE_FAULT - 1) as usize] = addr_of!(DOUBLE_FAULT_STACK) as u64 + 16 * 1024;
        let tss = addr_of!(TSS) as u64;
        let limit = (size_of::<Tss>() - 1) as u64;
        GDT = [
            0,
            0x00AF_9A00_0000_FFFF, // kernel code: present, ring 0, executable, long mode
            0x00CF_9200_0000_FFFF, // kernel data
            0x00CF_F200_0000_FFFF, // user data (ring 3)
            0x00AF_FA00_0000_FFFF, // user code (ring 3, long mode)
            // 64-bit TSS descriptor (two slots)
            (limit & 0xFFFF) | ((tss & 0xFF_FFFF) << 16) | (0x89 << 40) | (((limit >> 16) & 0xF) << 48)
                | (((tss >> 24) & 0xFF) << 56),
            tss >> 32,
        ];
        let ptr = Pointer { limit: (size_of::<[u64; 7]>() - 1) as u16, base: addr_of!(GDT) as u64 };
        asm!(
            "lgdt [{ptr}]",
            "push {cs}",
            "lea {tmp}, [rip + 2f]",
            "push {tmp}",
            "retfq",
            "2:",
            "mov ds, {ss:x}",
            "mov es, {ss:x}",
            "mov ss, {ss:x}",
            "xor {tmp:e}, {tmp:e}",
            "mov fs, {tmp:x}",
            "mov gs, {tmp:x}",
            "ltr {tss:x}",
            ptr = in(reg) &ptr,
            cs = in(reg) KERNEL_CS as u64,
            ss = in(reg) KERNEL_SS as u64,
            tss = in(reg) TSS_SEL as u64,
            tmp = out(reg) _,
        );
    }
}

/// Kernel stack used when an interrupt or exception arrives in ring 3.
pub fn set_kernel_stack(top: u64) {
    // SAFETY: single 64-bit store; the CPU only reads it on a privilege change.
    unsafe { TSS.rsp[0] = top };
}
