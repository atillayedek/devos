//! x86_64 architecture support: the only place the kernel talks to the CPU directly.

pub mod apic;
pub mod gdt;
pub mod idt;
pub mod serial;
pub mod syscall;

use core::arch::asm;

#[inline]
pub fn outb(port: u16, value: u8) {
    // SAFETY: port I/O has no memory effects; callers choose ports owned by their device.
    unsafe { asm!("out dx, al", in("dx") port, in("al") value, options(nomem, nostack, preserves_flags)) }
}

#[inline]
pub fn inb(port: u16) -> u8 {
    let v: u8;
    // SAFETY: see outb.
    unsafe { asm!("in al, dx", out("al") v, in("dx") port, options(nomem, nostack, preserves_flags)) }
    v
}

#[inline]
#[allow(dead_code)] // isa-debug-exit in self-test builds
pub fn outl(port: u16, value: u32) {
    // SAFETY: see outb.
    unsafe { asm!("out dx, eax", in("dx") port, in("eax") value, options(nomem, nostack, preserves_flags)) }
}

#[inline]
pub fn rdmsr(msr: u32) -> u64 {
    let (lo, hi): (u32, u32);
    // SAFETY: reading model-specific registers the kernel knows to exist.
    unsafe { asm!("rdmsr", in("ecx") msr, out("eax") lo, out("edx") hi, options(nomem, nostack, preserves_flags)) }
    ((hi as u64) << 32) | lo as u64
}

#[inline]
pub fn wrmsr(msr: u32, value: u64) {
    // SAFETY: callers write architecturally defined MSRs with valid values.
    unsafe {
        asm!("wrmsr", in("ecx") msr, in("eax") value as u32, in("edx") (value >> 32) as u32,
             options(nostack, preserves_flags))
    }
}

#[inline]
pub fn read_cr2() -> u64 {
    let v;
    // SAFETY: reading CR2 has no side effects.
    unsafe { asm!("mov {}, cr2", out(reg) v, options(nomem, nostack, preserves_flags)) }
    v
}

#[inline]
pub fn read_cr3() -> u64 {
    let v;
    // SAFETY: reading CR3 has no side effects.
    unsafe { asm!("mov {}, cr3", out(reg) v, options(nomem, nostack, preserves_flags)) }
    v
}

/// # Safety
/// `pml4` must be a valid top-level page table that maps the running kernel.
#[inline]
pub unsafe fn write_cr3(pml4: u64) {
    // SAFETY: guaranteed by the caller.
    unsafe { asm!("mov cr3, {}", in(reg) pml4, options(nostack, preserves_flags)) }
}

#[inline]
pub fn invlpg(addr: u64) {
    // SAFETY: invalidating a TLB entry is always safe.
    unsafe { asm!("invlpg [{}]", in(reg) addr, options(nostack, preserves_flags)) }
}

#[inline]
pub fn interrupts_enabled() -> bool {
    let flags: u64;
    // SAFETY: reading RFLAGS.
    unsafe { asm!("pushfq; pop {}", out(reg) flags, options(nomem, preserves_flags)) }
    flags & (1 << 9) != 0
}

#[inline]
pub fn enable_interrupts() {
    // SAFETY: the IDT is installed before interrupts are first enabled.
    unsafe { asm!("sti", options(nomem, nostack)) }
}

#[inline]
pub fn disable_interrupts() {
    // SAFETY: masking interrupts is always safe.
    unsafe { asm!("cli", options(nomem, nostack)) }
}

/// Runs `f` with interrupts masked, restoring the previous state afterwards.
pub fn without_interrupts<R>(f: impl FnOnce() -> R) -> R {
    let was = interrupts_enabled();
    disable_interrupts();
    let r = f();
    if was {
        enable_interrupts();
    }
    r
}

#[inline]
pub fn halt() {
    // SAFETY: hlt waits for the next interrupt.
    unsafe { asm!("hlt", options(nomem, nostack, preserves_flags)) }
}

pub fn halt_forever() -> ! {
    loop {
        disable_interrupts();
        halt();
    }
}

/// Enables SSE, write-protect, NX and the `syscall` instruction.
pub fn init_cpu_features() {
    const EFER: u32 = 0xC000_0080;
    wrmsr(EFER, rdmsr(EFER) | (1 << 0) | (1 << 11)); // SCE | NXE
    // SAFETY: setting CR0.WP (honour read-only pages in ring 0) keeps the kernel's own
    // mappings intact; the kernel is built without SSE, so no FPU state is touched.
    unsafe {
        asm!("mov rax, cr0", "or rax, 0x10000", "mov cr0, rax", out("rax") _, options(nostack));
    }
}

/// QEMU `isa-debug-exit` device (only used by self-test builds).
#[allow(dead_code)]
pub fn qemu_exit(code: u32) -> ! {
    outl(0xF4, code);
    halt_forever()
}
