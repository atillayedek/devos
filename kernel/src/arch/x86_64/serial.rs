//! 16550 UART on COM1 — the kernel log during early boot and in headless machines.

use super::{inb, outb};

const COM1: u16 = 0x3F8;

pub fn init() {
    outb(COM1 + 1, 0x00); // no interrupts
    outb(COM1 + 3, 0x80); // DLAB
    outb(COM1, 0x01); // 115200 baud
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03); // 8N1
    outb(COM1 + 2, 0xC7); // FIFO
    outb(COM1 + 4, 0x0B);
}

pub fn write_byte(b: u8) {
    if b == b'\n' {
        write_byte(b'\r');
    }
    let mut spins = 0;
    while inb(COM1 + 5) & 0x20 == 0 && spins < 100_000 {
        spins += 1;
    }
    outb(COM1, b);
}

pub fn write_str(s: &str) {
    for b in s.bytes() {
        write_byte(b);
    }
}
