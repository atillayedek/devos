//! `syscall`/`sysret` entry, per-CPU data and the first jump into ring 3.

use core::arch::global_asm;
use core::ptr::addr_of_mut;

use super::gdt::{KERNEL_CS, USER_CS, USER_SS};
use super::wrmsr;

const STAR: u32 = 0xC000_0081;
const LSTAR: u32 = 0xC000_0082;
const SFMASK: u32 = 0xC000_0084;
const GS_BASE: u32 = 0xC000_0101;
const KERNEL_GS_BASE: u32 = 0xC000_0102;

/// Per-CPU block, reached through GS while in the kernel. Field offsets are used by
/// the assembly below.
#[repr(C)]
pub struct PerCpu {
    pub self_ptr: u64,
    /// offset 8: top of the current thread's kernel stack
    pub kernel_rsp: u64,
    /// offset 16: scratch slot for the user stack pointer during `syscall`
    pub user_rsp: u64,
}

static mut PERCPU: PerCpu = PerCpu { self_ptr: 0, kernel_rsp: 0, user_rsp: 0 };

global_asm!(
    r#"
.section .text
.global syscall_entry
syscall_entry:
    swapgs
    mov gs:[16], rsp
    mov rsp, gs:[8]
    push qword ptr gs:[16]   // user rsp
    push r11                 // user rflags
    push rcx                 // user rip
    push rdi
    push rsi
    push rdx
    push r10
    push r8
    push r9
    push 0                   // keep the stack 16-byte aligned for the call
    mov r9, r8
    mov r8, r10
    mov rcx, rdx
    mov rdx, rsi
    mov rsi, rdi
    mov rdi, rax
    sti
    call syscall_dispatch
    cli
    add rsp, 8
    pop r9
    pop r8
    pop r10
    pop rdx
    pop rsi
    pop rdi
    pop rcx
    pop r11
    pop rsp
    swapgs
    sysretq

.global enter_user
enter_user:
    // rdi = user rip, rsi = user rsp
    cli
    push {user_ss}
    push rsi
    push 0x202               // IF set
    push {user_cs}
    push rdi
    xor eax, eax
    xor ebx, ebx
    xor ecx, ecx
    xor edx, edx
    xor esi, esi
    xor edi, edi
    xor ebp, ebp
    xor r8d, r8d
    xor r9d, r9d
    xor r10d, r10d
    xor r11d, r11d
    xor r12d, r12d
    xor r13d, r13d
    xor r14d, r14d
    xor r15d, r15d
    swapgs
    iretq
"#,
    user_ss = const USER_SS as u64,
    user_cs = const USER_CS as u64,
);

unsafe extern "C" {
    fn syscall_entry();
    fn enter_user(rip: u64, rsp: u64) -> !;
}

pub fn init() {
    // SAFETY: single boot CPU, before the first user thread runs.
    unsafe {
        let p = addr_of_mut!(PERCPU);
        (*p).self_ptr = p as u64;
        wrmsr(GS_BASE, p as u64);
        wrmsr(KERNEL_GS_BASE, 0);
    }
    // sysret: CS = base + 16, SS = base + 8 (base = user data selector - 8, RPL 3)
    let sysret_base = (USER_SS as u64 - 8) & !3 | 3;
    wrmsr(STAR, (sysret_base << 48) | ((KERNEL_CS as u64) << 32));
    wrmsr(LSTAR, syscall_entry as *const () as u64);
    // Mask IF, TF, DF and AC on entry
    wrmsr(SFMASK, 0x200 | 0x100 | 0x400 | 0x4_0000);
}

pub fn set_kernel_stack(top: u64) {
    // SAFETY: single 64-bit store read only by syscall_entry on this CPU.
    unsafe { (*addr_of_mut!(PERCPU)).kernel_rsp = top };
    super::gdt::set_kernel_stack(top);
}

/// Leaves the kernel for ring 3 at `rip` with stack `rsp`.
pub fn jump_to_user(rip: u64, rsp: u64) -> ! {
    // SAFETY: the caller's address space maps `rip` executable and `rsp` writable for
    // the user; the kernel stack for returns is set in PERCPU/TSS.
    unsafe { enter_user(rip, rsp) }
}

