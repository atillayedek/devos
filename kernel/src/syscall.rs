//! System call implementations (dispatched from the assembly entry in arch::syscall).

use alloc::vec;

use devos_abi::{nr, Errno, SysInfo, STDERR, STDOUT};

use crate::{console, mm, sched};

fn err(e: Errno) -> i64 {
    -(e as i64)
}

#[unsafe(no_mangle)]
extern "C" fn syscall_dispatch(n: u64, a0: u64, a1: u64, a2: u64, _a3: u64, _a4: u64) -> i64 {
    match n {
        nr::WRITE => sys_write(a0, a1, a2),
        nr::EXIT => sched::exit_current(a0 as i32 as i64),
        nr::YIELD => {
            sched::yield_now();
            0
        }
        nr::GETPID => sched::current_pid() as i64,
        nr::UPTIME_MS => sched::uptime_ms() as i64,
        nr::SLEEP_MS => {
            sched::sleep_ms(a0.min(3_600_000));
            0
        }
        nr::SYSINFO => sys_sysinfo(a0),
        _ => err(Errno::BadSyscall),
    }
}

fn sys_write(fd: u64, ptr: u64, len: u64) -> i64 {
    if fd != STDOUT && fd != STDERR {
        return err(Errno::BadFd);
    }
    if len > 64 * 1024 {
        return err(Errno::Invalid);
    }
    let mut buf = vec![0u8; len as usize];
    let ok = sched::with_current_space(|s| s.copy_from_user(ptr, &mut buf)).unwrap_or(false);
    if !ok {
        return err(Errno::Fault);
    }
    console::write_user(&buf);
    len as i64
}

fn sys_sysinfo(ptr: u64) -> i64 {
    let m = mm::stats();
    let info = SysInfo {
        total_memory: m.total,
        free_memory: m.free,
        uptime_ms: sched::uptime_ms(),
        threads: sched::thread_count(),
        cpus: 1,
    };
    // SAFETY: SysInfo is plain-old-data with no padding-sensitive invariants.
    let bytes = unsafe {
        core::slice::from_raw_parts(&info as *const SysInfo as *const u8, size_of::<SysInfo>())
    };
    if sched::with_current_space(|s| s.copy_to_user(ptr, bytes)).unwrap_or(false) { 0 } else { err(Errno::Fault) }
}
