//! DevOS system call ABI (x86_64).
//!
//! `syscall` instruction; number in `rax`, arguments in `rdi, rsi, rdx, r10, r8, r9`,
//! result in `rax`. Negative results are `-Errno`. `rcx` and `r11` are clobbered.
#![no_std]

pub mod nr {
    pub const WRITE: u64 = 1;
    pub const EXIT: u64 = 2;
    pub const YIELD: u64 = 3;
    pub const GETPID: u64 = 4;
    pub const UPTIME_MS: u64 = 5;
    pub const SLEEP_MS: u64 = 6;
    pub const SYSINFO: u64 = 7;
}

/// Standard file descriptors until the VFS exists.
pub const STDOUT: u64 = 1;
pub const STDERR: u64 = 2;

#[repr(i64)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Errno {
    BadSyscall = 1,
    BadFd = 2,
    Fault = 3,
    Invalid = 4,
}

impl Errno {
    pub fn from_ret(ret: i64) -> Option<Errno> {
        match -ret {
            1 => Some(Errno::BadSyscall),
            2 => Some(Errno::BadFd),
            3 => Some(Errno::Fault),
            4 => Some(Errno::Invalid),
            _ => None,
        }
    }
}

/// Filled by `SYSINFO`.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct SysInfo {
    pub total_memory: u64,
    pub free_memory: u64,
    pub uptime_ms: u64,
    pub threads: u64,
    pub cpus: u64,
}

/// Lowest address user programs may use; everything at or above `USER_END` is kernel space.
pub const USER_START: u64 = 0x0000_0000_0040_0000;
pub const USER_END: u64 = 0x0000_8000_0000_0000;
pub const USER_STACK_TOP: u64 = 0x0000_7FFF_FFFF_0000;
pub const USER_STACK_SIZE: u64 = 256 * 1024;
