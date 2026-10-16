//! Kernel locking. `IrqLock` masks interrupts while held so an interrupt handler can
//! never deadlock against the code it interrupted.

use core::cell::UnsafeCell;
use core::ops::{Deref, DerefMut};
use core::sync::atomic::{AtomicBool, Ordering};

use crate::arch::{disable_interrupts, enable_interrupts, interrupts_enabled};

pub struct IrqLock<T> {
    locked: AtomicBool,
    value: UnsafeCell<T>,
}

// SAFETY: access to `value` is serialised by `locked`.
unsafe impl<T: Send> Sync for IrqLock<T> {}
unsafe impl<T: Send> Send for IrqLock<T> {}

impl<T> IrqLock<T> {
    pub const fn new(value: T) -> Self {
        IrqLock { locked: AtomicBool::new(false), value: UnsafeCell::new(value) }
    }

    pub fn lock(&self) -> IrqGuard<'_, T> {
        let restore = interrupts_enabled();
        disable_interrupts();
        while self.locked.compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed).is_err() {
            core::hint::spin_loop();
        }
        IrqGuard { lock: self, restore }
    }
}

pub struct IrqGuard<'a, T> {
    lock: &'a IrqLock<T>,
    restore: bool,
}

impl<T> Deref for IrqGuard<'_, T> {
    type Target = T;
    fn deref(&self) -> &T {
        // SAFETY: the guard proves exclusive access.
        unsafe { &*self.lock.value.get() }
    }
}

impl<T> DerefMut for IrqGuard<'_, T> {
    fn deref_mut(&mut self) -> &mut T {
        // SAFETY: the guard proves exclusive access.
        unsafe { &mut *self.lock.value.get() }
    }
}

impl<T> Drop for IrqGuard<'_, T> {
    fn drop(&mut self) {
        self.lock.locked.store(false, Ordering::Release);
        if self.restore {
            enable_interrupts();
        }
    }
}
