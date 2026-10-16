//! Local APIC: the 8259 PICs are retired, the LAPIC timer drives preemption.

use core::sync::atomic::{AtomicU64, Ordering};

use super::{inb, outb, rdmsr};
use crate::mm::phys_to_virt;

const IA32_APIC_BASE: u32 = 0x1B;
const REG_EOI: u64 = 0x0B0;
const REG_SVR: u64 = 0x0F0;
const REG_LVT_TIMER: u64 = 0x320;
const REG_TIMER_INIT: u64 = 0x380;
const REG_TIMER_CURRENT: u64 = 0x390;
const REG_TIMER_DIV: u64 = 0x3E0;

pub const TICK_HZ: u64 = 100;
static BASE: AtomicU64 = AtomicU64::new(0);

fn reg(offset: u64) -> *mut u32 {
    (BASE.load(Ordering::Relaxed) + offset) as *mut u32
}

fn read(offset: u64) -> u32 {
    // SAFETY: LAPIC registers are 32-bit MMIO inside the direct-mapped APIC page.
    unsafe { reg(offset).read_volatile() }
}

fn write(offset: u64, v: u32) {
    // SAFETY: see read().
    unsafe { reg(offset).write_volatile(v) }
}

pub fn eoi() {
    write(REG_EOI, 0);
}

fn disable_pic() {
    // Remap the 8259s away from the exception vectors, then mask every line.
    outb(0x20, 0x11);
    outb(0xA0, 0x11);
    outb(0x21, 0x20);
    outb(0xA1, 0x28);
    outb(0x21, 4);
    outb(0xA1, 2);
    outb(0x21, 1);
    outb(0xA1, 1);
    outb(0x21, 0xFF);
    outb(0xA1, 0xFF);
}

/// Measures LAPIC timer ticks per 10 ms against PIT channel 2.
fn calibrate() -> u32 {
    const PIT_HZ: u32 = 1_193_182;
    let count = PIT_HZ / 100;
    outb(0x61, (inb(0x61) & 0xFD) | 1); // gate on, speaker off
    outb(0x43, 0xB2); // channel 2, lo/hi, mode 1 (one-shot)
    outb(0x42, count as u8);
    let _ = inb(0x60);
    outb(0x42, (count >> 8) as u8);
    let g = inb(0x61) & 0xFE;
    outb(0x61, g); // restart the one-shot
    outb(0x61, g | 1);
    write(REG_TIMER_INIT, u32::MAX);
    while inb(0x61) & 0x20 == 0 {}
    let elapsed = u32::MAX - read(REG_TIMER_CURRENT);
    write(REG_TIMER_INIT, 0);
    elapsed
}

pub fn init() -> u32 {
    disable_pic();
    let phys = rdmsr(IA32_APIC_BASE) & 0xFFFF_F000;
    BASE.store(phys_to_virt(phys), Ordering::Relaxed);
    write(REG_SVR, 0x100 | super::idt::VECTOR_SPURIOUS as u32); // software-enable
    write(REG_TIMER_DIV, 0x3); // divide by 16
    let per_10ms = calibrate();
    let per_tick = per_10ms * (100 / TICK_HZ as u32);
    write(REG_LVT_TIMER, super::idt::VECTOR_TIMER as u32 | (1 << 17)); // periodic
    write(REG_TIMER_INIT, per_tick);
    per_10ms
}
