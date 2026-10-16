//! /devos/init — the first program the DevOS kernel starts in ring 3.
#![no_std]
#![no_main]

use devos_rt::{getpid, println, sleep_ms, sysinfo, uptime_ms, yield_now};

devos_rt::entry!(main);

fn main() -> i32 {
    println!("init: hello from DevOS userspace (pid {})", getpid());
    let info = sysinfo();
    println!(
        "init: {} MiB memory, {} MiB free, {} threads, {} CPU",
        info.total_memory >> 20,
        info.free_memory >> 20,
        info.threads,
        info.cpus
    );
    let start = uptime_ms();
    for i in 1..=3 {
        sleep_ms(100);
        yield_now();
        println!("init: tick {} at +{} ms", i, uptime_ms() - start);
    }
    println!("init: system services will start here (service manager, device manager, login)");
    0
}
