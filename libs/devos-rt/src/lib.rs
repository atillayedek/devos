//! Runtime for native DevOS programs: entry point, system calls, console output.
#![no_std]

use core::arch::asm;
use core::fmt;

pub use devos_abi as abi;
use devos_abi::nr;

/// Raw system call with up to three arguments.
#[inline(always)]
pub fn syscall3(n: u64, a0: u64, a1: u64, a2: u64) -> i64 {
    let ret: i64;
    // SAFETY: the DevOS syscall ABI clobbers only rax (result), rcx and r11.
    unsafe {
        asm!("syscall", inlateout("rax") n as i64 => ret, in("rdi") a0, in("rsi") a1, in("rdx") a2,
             lateout("rcx") _, lateout("r11") _, options(nostack));
    }
    ret
}

pub fn write(fd: u64, bytes: &[u8]) -> i64 {
    syscall3(nr::WRITE, fd, bytes.as_ptr() as u64, bytes.len() as u64)
}

pub fn exit(code: i32) -> ! {
    syscall3(nr::EXIT, code as u64, 0, 0);
    unreachable!("exit returned")
}

pub fn yield_now() {
    syscall3(nr::YIELD, 0, 0, 0);
}

pub fn getpid() -> u64 {
    syscall3(nr::GETPID, 0, 0, 0) as u64
}

pub fn uptime_ms() -> u64 {
    syscall3(nr::UPTIME_MS, 0, 0, 0) as u64
}

pub fn sleep_ms(ms: u64) {
    syscall3(nr::SLEEP_MS, ms, 0, 0);
}

pub fn sysinfo() -> abi::SysInfo {
    let mut info = abi::SysInfo::default();
    syscall3(nr::SYSINFO, &mut info as *mut _ as u64, 0, 0);
    info
}

pub struct Stdout;

impl fmt::Write for Stdout {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        if write(abi::STDOUT, s.as_bytes()) < 0 { Err(fmt::Error) } else { Ok(()) }
    }
}

#[macro_export]
macro_rules! print {
    ($($arg:tt)*) => {{ let _ = core::fmt::Write::write_fmt(&mut $crate::Stdout, format_args!($($arg)*)); }};
}

#[macro_export]
macro_rules! println {
    () => { $crate::print!("\n") };
    ($($arg:tt)*) => {{ $crate::print!($($arg)*); $crate::print!("\n"); }};
}

/// Declares the program's `main`: `devos_rt::entry!(main);` with `fn main() -> i32`.
#[macro_export]
macro_rules! entry {
    ($main:path) => {
        #[unsafe(no_mangle)]
        pub extern "C" fn _start() -> ! {
            let f: fn() -> i32 = $main;
            $crate::exit(f())
        }
    };
}

#[panic_handler]
fn panic(info: &core::panic::PanicInfo) -> ! {
    println!("panic: {}", info);
    exit(101)
}
