//! Preemptive round-robin scheduler (single CPU for now).
//!
//! Every thread owns a kernel stack. A switch saves callee-saved registers and RFLAGS
//! on the outgoing stack (`switch_context`, assembly), so threads can be preempted from
//! the timer interrupt, from a blocking system call, or yield voluntarily.

use alloc::boxed::Box;
use alloc::collections::VecDeque;
use alloc::string::String;
use alloc::vec::Vec;
use core::arch::global_asm;
use core::sync::atomic::{AtomicU64, Ordering};

use crate::arch::{self, apic::TICK_HZ, syscall};
use crate::mm::paging::{self, AddressSpace};
use crate::sync::IrqLock;

pub mod process;

const KSTACK_SIZE: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Ready,
    Running,
    Sleeping { until_tick: u64 },
    Dead,
}

pub struct Thread {
    pub tid: u64,
    pub pid: u64,
    pub name: String,
    pub state: State,
    rsp: u64,
    kstack: Box<[u8]>,
    pub space: Option<AddressSpace>,
    pub exit_code: i64,
}

impl Thread {
    fn kstack_top(&self) -> u64 {
        (self.kstack.as_ptr() as u64 + self.kstack.len() as u64) & !0xF
    }
}

struct Scheduler {
    threads: Vec<Box<Thread>>,
    ready: VecDeque<u64>,
    current: u64,
    next_tid: u64,
}

static SCHED: IrqLock<Scheduler> =
    IrqLock::new(Scheduler { threads: Vec::new(), ready: VecDeque::new(), current: 0, next_tid: 1 });
static TICKS: AtomicU64 = AtomicU64::new(0);
static EXIT_HOOK: IrqLock<Option<fn(u64, i64)>> = IrqLock::new(None);

global_asm!(
    r#"
.section .text
.global switch_context
switch_context:
    // rdi = &mut old rsp, rsi = new rsp
    push rbp
    push rbx
    push r12
    push r13
    push r14
    push r15
    pushfq
    mov [rdi], rsp
    mov rsp, rsi
    popfq
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    pop rbp
    ret

.global thread_trampoline
thread_trampoline:
    // first run of a thread: r12 = entry, r13 = argument
    sti
    mov rdi, r13
    call r12
    ud2
"#
);

unsafe extern "C" {
    fn switch_context(old_rsp: *mut u64, new_rsp: u64);
    fn thread_trampoline();
}

impl Scheduler {
    fn get(&mut self, tid: u64) -> Option<&mut Box<Thread>> {
        self.threads.iter_mut().find(|t| t.tid == tid)
    }
}

/// Registers the boot flow as thread 0 (the idle thread once boot completes).
pub fn init() {
    let mut s = SCHED.lock();
    s.threads.push(Box::new(Thread {
        tid: 0,
        pid: 0,
        name: String::from("idle"),
        state: State::Running,
        rsp: 0,
        kstack: Box::new([]),
        space: None,
        exit_code: 0,
    }));
    s.current = 0;
}

/// Creates a kernel thread running `entry(arg)`; `space` makes it a user process thread.
pub fn spawn(name: &str, pid: u64, entry: extern "C" fn(u64) -> !, arg: u64, space: Option<AddressSpace>) -> u64 {
    let kstack = alloc::vec![0u8; KSTACK_SIZE].into_boxed_slice();
    let mut t = Box::new(Thread {
        tid: 0,
        pid,
        name: String::from(name),
        state: State::Ready,
        rsp: 0,
        kstack,
        space,
        exit_code: 0,
    });
    // Initial frame consumed by switch_context: rflags, r15, r14, r13, r12, rbx, rbp, ret
    let top = t.kstack_top();
    let frame: [u64; 8] = [0x2, 0, 0, arg, entry as usize as u64, 0, 0, thread_trampoline as usize as u64];
    let rsp = top - 8 - (frame.len() as u64) * 8; // keep `ret` landing 16-byte aligned + 8
    // SAFETY: the frame fits inside the new thread's own stack.
    unsafe { core::ptr::copy_nonoverlapping(frame.as_ptr(), rsp as *mut u64, frame.len()) };
    t.rsp = rsp;
    let mut s = SCHED.lock();
    let tid = s.next_tid;
    s.next_tid += 1;
    t.tid = tid;
    if t.pid == 0 {
        t.pid = tid;
    }
    s.threads.push(t);
    s.ready.push_back(tid);
    tid
}

/// Picks the next ready thread and switches to it (no-op if none is ready).
pub fn schedule() {
    arch::without_interrupts(|| {
        let (old_rsp, new_rsp, cr3, kstack_top) = {
            let mut s = SCHED.lock();
            // reap dead threads other than the current one
            let cur = s.current;
            s.threads.retain(|t| t.state != State::Dead || t.tid == cur);
            let cur_running = s.get(cur).is_some_and(|t| t.state == State::Running);
            // Nothing ready: keep running, or fall back to the idle thread if blocked/dead.
            let next = match s.ready.pop_front() {
                Some(n) => n,
                None if !cur_running && cur != 0 => 0,
                None => return,
            };
            if next == cur {
                if let Some(t) = s.get(cur) {
                    t.state = State::Running;
                }
                return;
            }
            if let Some(t) = s.get(cur) {
                if t.state == State::Running {
                    t.state = State::Ready;
                    let tid = t.tid;
                    if tid != 0 {
                        s.ready.push_back(tid);
                    }
                }
            }
            let cur_ptr: *mut u64 = match s.get(cur) {
                Some(t) => &mut t.rsp,
                None => return,
            };
            let n = s.get(next).expect("ready thread vanished");
            n.state = State::Running;
            let cr3 = n.space.as_ref().map(|a| a.pml4).unwrap_or_else(paging::kernel_pml4);
            let info = (cur_ptr, n.rsp, cr3, n.kstack_top());
            s.current = next;
            #[cfg(feature = "trace-sched")]
            crate::kearly!("switch {} -> {} (ready {:?})", cur, next, s.ready);
            info
        };
        syscall::set_kernel_stack(kstack_top);
        if arch::read_cr3() & !0xFFF != cr3 {
            // SAFETY: every address space shares the kernel half.
            unsafe { arch::write_cr3(cr3) };
        }
        // SAFETY: both stacks belong to live threads; the Box<Thread> keeps `old_rsp` valid.
        unsafe { switch_context(old_rsp, new_rsp) };
    });
}

pub fn yield_now() {
    schedule();
}

/// Called from the timer interrupt (interrupts disabled).
pub fn timer_tick() {
    let now = TICKS.fetch_add(1, Ordering::Relaxed) + 1;
    {
        let mut s = SCHED.lock();
        let mut woken = Vec::new();
        for t in s.threads.iter_mut() {
            if let State::Sleeping { until_tick } = t.state {
                if until_tick <= now {
                    t.state = State::Ready;
                    woken.push(t.tid);
                }
            }
        }
        s.ready.extend(woken);
    }
    schedule();
}

pub fn ticks() -> u64 {
    TICKS.load(Ordering::Relaxed)
}

pub fn uptime_ms() -> u64 {
    ticks() * 1000 / TICK_HZ
}

pub fn sleep_ms(ms: u64) {
    let until = ticks() + ms.div_ceil(1000 / TICK_HZ).max(1);
    {
        let mut s = SCHED.lock();
        let cur = s.current;
        if let Some(t) = s.get(cur) {
            t.state = State::Sleeping { until_tick: until };
        }
    }
    while ticks() < until {
        schedule();
        if ticks() < until {
            arch::enable_interrupts();
            arch::halt();
        }
    }
}

pub fn current_pid() -> u64 {
    let mut s = SCHED.lock();
    let cur = s.current;
    s.get(cur).map(|t| t.pid).unwrap_or(0)
}

pub fn thread_count() -> u64 {
    SCHED.lock().threads.iter().filter(|t| t.state != State::Dead).count() as u64
}

pub fn set_exit_hook(hook: fn(u64, i64)) {
    *EXIT_HOOK.lock() = Some(hook);
}

/// Runs `f` with the current thread's address space, if it has one.
pub fn with_current_space<R>(f: impl FnOnce(&AddressSpace) -> R) -> Option<R> {
    let mut s = SCHED.lock();
    let cur = s.current;
    s.get(cur).and_then(|t| t.space.as_ref()).map(f)
}

/// Terminates the current thread (and its address space) and never returns.
pub fn exit_current(code: i64) -> ! {
    let pid = {
        let mut s = SCHED.lock();
        let cur = s.current;
        let t = s.get(cur).expect("current thread");
        t.state = State::Dead;
        t.exit_code = code;
        // Leave the dying address space before its frames are freed.
        // SAFETY: the kernel PML4 maps everything the kernel needs.
        unsafe { arch::write_cr3(paging::kernel_pml4()) };
        drop(t.space.take());
        t.pid
    };
    if let Some(hook) = *EXIT_HOOK.lock() {
        hook(pid, code);
    }
    loop {
        schedule();
        arch::enable_interrupts();
        arch::halt();
    }
}

/// The idle loop: the boot thread ends up here once initialisation is complete.
pub fn idle() -> ! {
    loop {
        schedule();
        arch::enable_interrupts();
        arch::halt();
    }
}
