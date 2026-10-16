//! Interrupt descriptor table, the assembly entry stubs and the Rust dispatcher.

use core::arch::{asm, global_asm};
use core::mem::size_of;
use core::ptr::addr_of;

use super::gdt::{IST_DOUBLE_FAULT, KERNEL_CS};

pub const VECTOR_TIMER: u64 = 32;
pub const VECTOR_SPURIOUS: u64 = 255;

/// Register state saved by `isr_common` (lowest address first).
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct TrapFrame {
    pub r15: u64,
    pub r14: u64,
    pub r13: u64,
    pub r12: u64,
    pub r11: u64,
    pub r10: u64,
    pub r9: u64,
    pub r8: u64,
    pub rbp: u64,
    pub rdi: u64,
    pub rsi: u64,
    pub rdx: u64,
    pub rcx: u64,
    pub rbx: u64,
    pub rax: u64,
    pub vector: u64,
    pub error: u64,
    pub rip: u64,
    pub cs: u64,
    pub rflags: u64,
    pub rsp: u64,
    pub ss: u64,
}

impl TrapFrame {
    pub fn from_user(&self) -> bool {
        self.cs & 3 == 3
    }
}

// Entry stubs. Every stub pushes a uniform (error code, vector) pair, then isr_common
// saves the general-purpose registers, switches GS when coming from ring 3 and calls
// `interrupt_dispatch(&mut TrapFrame)`.
global_asm!(
    r#"
.macro ISR_NOERR n
isr_\n:
    push 0
    push \n
    jmp isr_common
.endm
.macro ISR_ERR n
isr_\n:
    push \n
    jmp isr_common
.endm

.section .text
isr_common:
    test qword ptr [rsp + 24], 3
    jz 1f
    swapgs
1:
    push rax
    push rbx
    push rcx
    push rdx
    push rsi
    push rdi
    push rbp
    push r8
    push r9
    push r10
    push r11
    push r12
    push r13
    push r14
    push r15
    mov rdi, rsp
    cld
    call interrupt_dispatch
    pop r15
    pop r14
    pop r13
    pop r12
    pop r11
    pop r10
    pop r9
    pop r8
    pop rbp
    pop rdi
    pop rsi
    pop rdx
    pop rcx
    pop rbx
    pop rax
    test qword ptr [rsp + 24], 3
    jz 2f
    swapgs
2:
    add rsp, 16
    iretq

ISR_NOERR 0
ISR_NOERR 1
ISR_NOERR 2
ISR_NOERR 3
ISR_NOERR 4
ISR_NOERR 5
ISR_NOERR 6
ISR_NOERR 7
ISR_ERR   8
ISR_NOERR 9
ISR_ERR   10
ISR_ERR   11
ISR_ERR   12
ISR_ERR   13
ISR_ERR   14
ISR_NOERR 15
ISR_NOERR 16
ISR_ERR   17
ISR_NOERR 18
ISR_NOERR 19
ISR_NOERR 20
ISR_ERR   21
ISR_NOERR 22
ISR_NOERR 23
ISR_NOERR 24
ISR_NOERR 25
ISR_NOERR 26
ISR_NOERR 27
ISR_NOERR 28
ISR_ERR   29
ISR_ERR   30
ISR_NOERR 31
ISR_NOERR 32
ISR_NOERR 33
ISR_NOERR 34
ISR_NOERR 35
ISR_NOERR 36
ISR_NOERR 37
ISR_NOERR 38
ISR_NOERR 39
ISR_NOERR 40
ISR_NOERR 41
ISR_NOERR 42
ISR_NOERR 43
ISR_NOERR 44
ISR_NOERR 45
ISR_NOERR 46
ISR_NOERR 47
ISR_NOERR 255

.section .rodata
.balign 8
.global isr_table
isr_table:
    .quad isr_0, isr_1, isr_2, isr_3, isr_4, isr_5, isr_6, isr_7
    .quad isr_8, isr_9, isr_10, isr_11, isr_12, isr_13, isr_14, isr_15
    .quad isr_16, isr_17, isr_18, isr_19, isr_20, isr_21, isr_22, isr_23
    .quad isr_24, isr_25, isr_26, isr_27, isr_28, isr_29, isr_30, isr_31
    .quad isr_32, isr_33, isr_34, isr_35, isr_36, isr_37, isr_38, isr_39
    .quad isr_40, isr_41, isr_42, isr_43, isr_44, isr_45, isr_46, isr_47
    .quad isr_255
.section .text
"#
);

unsafe extern "C" {
    static isr_table: [u64; 49];
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Gate {
    off_lo: u16,
    sel: u16,
    ist: u8,
    attr: u8,
    off_mid: u16,
    off_hi: u32,
    _r: u32,
}

static mut IDT: [Gate; 256] = [Gate { off_lo: 0, sel: 0, ist: 0, attr: 0, off_mid: 0, off_hi: 0, _r: 0 }; 256];

fn gate(handler: u64, ist: u8) -> Gate {
    Gate {
        off_lo: handler as u16,
        sel: KERNEL_CS,
        ist,
        attr: 0x8E, // present, ring 0, 64-bit interrupt gate
        off_mid: (handler >> 16) as u16,
        off_hi: (handler >> 32) as u32,
        _r: 0,
    }
}

pub fn init() {
    // SAFETY: runs once on the boot CPU with interrupts disabled.
    unsafe {
        let table = &*addr_of!(isr_table);
        for v in 0..48usize {
            let ist = if v == 8 { IST_DOUBLE_FAULT } else { 0 };
            IDT[v] = gate(table[v], ist);
        }
        IDT[255] = gate(table[48], 0);
        #[repr(C, packed)]
        struct Pointer {
            limit: u16,
            base: u64,
        }
        let ptr = Pointer { limit: (size_of::<[Gate; 256]>() - 1) as u16, base: addr_of!(IDT) as u64 };
        asm!("lidt [{}]", in(reg) &ptr, options(readonly, nostack, preserves_flags));
    }
}

const EXCEPTIONS: [&str; 32] = [
    "divide error", "debug", "NMI", "breakpoint", "overflow", "bound range", "invalid opcode",
    "device not available", "double fault", "coprocessor overrun", "invalid TSS", "segment not present",
    "stack fault", "general protection", "page fault", "reserved", "x87 FP", "alignment check",
    "machine check", "SIMD FP", "virtualization", "control protection", "reserved", "reserved",
    "reserved", "reserved", "reserved", "reserved", "hypervisor injection", "VMM communication",
    "security", "reserved",
];

#[unsafe(no_mangle)]
extern "C" fn interrupt_dispatch(frame: &mut TrapFrame) {
    match frame.vector {
        0..=31 => exception(frame),
        VECTOR_TIMER => {
            super::apic::eoi();
            crate::sched::timer_tick();
        }
        VECTOR_SPURIOUS => {}
        _ => super::apic::eoi(),
    }
}

fn exception(frame: &mut TrapFrame) {
    let name = EXCEPTIONS[frame.vector as usize];
    if frame.from_user() {
        let extra = if frame.vector == 14 { super::read_cr2() } else { 0 };
        crate::kprintln!(
            "process {} killed: {} at {:#x} (error {:#x}, address {:#x})",
            crate::sched::current_pid(),
            name,
            frame.rip,
            frame.error,
            extra
        );
        crate::sched::exit_current(-(frame.vector as i64) - 128);
    }
    if frame.vector == 14 {
        panic!("kernel page fault at {:#x} accessing {:#x} (error {:#x})", frame.rip, super::read_cr2(), frame.error);
    }
    panic!("kernel exception: {} at {:#x} (error {:#x})\n{:#x?}", name, frame.rip, frame.error, frame);
}
