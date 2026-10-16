//! Kernel console: every line goes to COM1 and, once graphics are up, to the boot screen.

use alloc::string::String;
use core::fmt::{self, Write};

use crate::arch::serial;
use crate::sync::IrqLock;
use crate::ui::boot_screen::{BootScreen, LineKind};

static SCREEN: IrqLock<Option<BootScreen>> = IrqLock::new(None);
static USER_LINE: IrqLock<String> = IrqLock::new(String::new());

pub fn attach_screen(bs: BootScreen) {
    *SCREEN.lock() = Some(bs);
}

struct Serial;

impl Write for Serial {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        serial::write_str(s);
        Ok(())
    }
}

pub fn print(args: fmt::Arguments, kind: LineKind) {
    let mut line = String::new();
    let _ = line.write_fmt(args);
    crate::arch::without_interrupts(|| {
        serial::write_str(&line);
        serial::write_str("\n");
    });
    if let Some(bs) = SCREEN.lock().as_mut() {
        bs.log(kind, &line);
    }
}

/// Early output before the heap exists (serial only, no allocation).
pub fn early(args: fmt::Arguments) {
    let _ = Serial.write_fmt(args);
    serial::write_str("\n");
}

pub fn progress(value: u32) {
    if let Some(bs) = SCREEN.lock().as_mut() {
        bs.set_progress(value);
    }
}

pub fn footer(text: &str) {
    if let Some(bs) = SCREEN.lock().as_mut() {
        bs.footer(text);
    }
}

/// Output of user processes: complete lines are shown on screen, everything on serial.
pub fn write_user(bytes: &[u8]) {
    let text = String::from_utf8_lossy(bytes);
    crate::arch::without_interrupts(|| serial::write_str(&text));
    let mut done = alloc::vec::Vec::new();
    {
        let mut pending = USER_LINE.lock();
        for ch in text.chars() {
            if ch == '\n' {
                done.push(core::mem::take(&mut *pending));
            } else {
                pending.push(ch);
            }
        }
    }
    if let Some(bs) = SCREEN.lock().as_mut() {
        for l in done {
            bs.log(LineKind::User, &l);
        }
    }
}

#[macro_export]
macro_rules! kprintln {
    ($($arg:tt)*) => { $crate::console::print(format_args!($($arg)*), $crate::ui::boot_screen::LineKind::Info) };
}

#[macro_export]
macro_rules! kok {
    ($($arg:tt)*) => { $crate::console::print(format_args!($($arg)*), $crate::ui::boot_screen::LineKind::Ok) };
}

#[macro_export]
macro_rules! kwarn {
    ($($arg:tt)*) => { $crate::console::print(format_args!($($arg)*), $crate::ui::boot_screen::LineKind::Warn) };
}

#[macro_export]
macro_rules! kearly {
    ($($arg:tt)*) => { $crate::console::early(format_args!($($arg)*)) };
}
