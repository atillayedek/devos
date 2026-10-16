//! The DevOS kernel.
#![no_std]
#![no_main]

extern crate alloc;

mod arch {
    pub mod x86_64;
    pub use x86_64::*;
}
mod console;
mod mm;
mod sched;
#[cfg(feature = "selftest")]
mod selftest;
mod sync;
mod syscall;
mod ui;

use core::panic::PanicInfo;
use core::sync::atomic::{AtomicU64, Ordering};

use devos_bootinfo::{BootInfo, MAGIC, VERSION};

static INIT_PID: AtomicU64 = AtomicU64::new(0);

/// Entry from devos-boot: `rdi` = &BootInfo (direct map), stack set up, interrupts off.
#[unsafe(no_mangle)]
#[unsafe(link_section = ".text.entry")]
pub extern "sysv64" fn _start(boot: &'static BootInfo) -> ! {
    arch::serial::init();
    kearly!("DevOS kernel {} starting", env!("CARGO_PKG_VERSION"));
    if boot.magic != MAGIC || boot.version != VERSION {
        kearly!("invalid boot information (magic {:#x}, version {})", boot.magic, boot.version);
        arch::halt_forever();
    }
    arch::init_cpu_features();
    arch::gdt::init();
    arch::idt::init();
    let mem = mm::init(boot);
    kearly!("memory: {} MiB usable, {} MiB free, heap {} MiB", mem.total >> 20, mem.free >> 20, mem.heap >> 20);

    // From here on the heap exists: bring up the screen.
    if let Some(screen) = ui::Screen::new(&boot.framebuffer) {
        console::attach_screen(ui::boot_screen::BootScreen::new(screen));
    }
    kok!("DevOS kernel {} - x86_64, Rust", env!("CARGO_PKG_VERSION"));
    console::progress(15);
    kok!("CPU: long mode, NX, write-protect, syscall enabled; GDT/TSS and IDT loaded");
    kok!(
        "Memory: {} MiB usable, {} MiB free, {} MiB kernel heap",
        mem.total >> 20,
        mem.free >> 20,
        mem.heap >> 20
    );
    console::progress(35);
    if boot.framebuffer.width > 0 {
        kok!("Display: {}x{} framebuffer", boot.framebuffer.width, boot.framebuffer.height);
    } else {
        kwarn!("Display: no framebuffer, serial console only");
    }

    sched::init();
    arch::syscall::init();
    let per_10ms = arch::apic::init();
    kok!("Timer: local APIC at {} Hz ({} ticks per 10 ms)", arch::apic::TICK_HZ, per_10ms);
    console::progress(55);
    sched::set_exit_hook(on_process_exit);
    arch::enable_interrupts();
    kok!("Scheduler: preemptive round-robin running");
    console::progress(70);

    #[cfg(feature = "selftest")]
    selftest::run();

    // SAFETY: the bootloader placed init.elf in kernel-owned memory of init_len bytes.
    let init = unsafe {
        core::slice::from_raw_parts(mm::phys_to_virt(boot.init_phys) as *const u8, boot.init_len as usize)
    };
    match sched::process::spawn_elf("init", init) {
        Ok(pid) => {
            INIT_PID.store(pid, Ordering::Relaxed);
            kok!("Userspace: started /devos/init.elf as pid {}", pid);
        }
        Err(e) => kwarn!("Userspace: cannot start init: {:?}", e),
    }
    console::progress(100);
    sched::spawn("statusbar", 0, status_thread, 0, None);
    sched::idle()
}

/// Keeps the boot screen footer up to date (a kernel thread, exercising sleep + preemption).
extern "C" fn status_thread(_: u64) -> ! {
    loop {
        let m = mm::stats();
        let up = sched::uptime_ms() / 1000;
        let text = alloc::format!(
            "DevOS 0.1  ·  x86_64  ·  {} / {} MiB free  ·  {} threads  ·  up {}:{:02}",
            m.free >> 20,
            m.total >> 20,
            sched::thread_count(),
            up / 60,
            up % 60
        );
        console::footer(&text);
        sched::sleep_ms(1000);
    }
}

fn on_process_exit(pid: u64, code: i64) {
    if code == 0 {
        kok!("process {} exited with code 0", pid);
    } else {
        kwarn!("process {} exited with code {}", pid, code);
    }
    #[cfg(feature = "selftest")]
    if pid == INIT_PID.load(Ordering::Relaxed) {
        kearly!("SELFTEST {} init exit code {}", if code == 0 { "PASS" } else { "FAIL" }, code);
        arch::qemu_exit(if code == 0 { 0x10 } else { 0x11 });
    }
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    arch::disable_interrupts();
    kearly!("KERNEL PANIC: {}", info);
    #[cfg(feature = "selftest")]
    arch::qemu_exit(0x11);
    #[cfg(not(feature = "selftest"))]
    arch::halt_forever()
}
