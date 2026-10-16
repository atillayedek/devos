# DevOS architecture (kernel 0.1)

## Boot flow

```
UEFI firmware ─► \EFI\BOOT\BOOTX64.EFI (devos-boot, Rust)
   reads \devos\kernel.elf, \devos\init.elf          (uefi::fs)
   sets the GOP mode (≤ 1920×1080), finds the ACPI 2.0 RSDP
   allocates kernel memory as custom type 0x80000001 (kernel-owned)
   builds page tables:
       identity map of [0, max phys)            2 MiB pages   (dropped by the kernel)
       direct map at 0xFFFF_8000_0000_0000      2 MiB pages, NX
       kernel segments at their link address    4 KiB pages, W^X from ELF flags
       kernel stack below 0xFFFF_FFFF_C000_0000 (unmapped guard page)
   ExitBootServices, memory map → BootInfo (libs/devos-bootinfo)
   asm: mov cr3; mov rsp; jmp kernel _start(rdi = &BootInfo)
```

## Kernel

| Subsystem | File | Notes |
|---|---|---|
| CPU setup | `arch/x86_64/mod.rs` | EFER.NXE/SCE, CR0.WP, port I/O, MSRs |
| GDT/TSS | `arch/x86_64/gdt.rs` | selector order required by `sysret`; IST1 for double faults |
| Interrupts | `arch/x86_64/idt.rs` | assembly stubs push a uniform `TrapFrame`, `swapgs` when coming from ring 3; user faults kill the process, kernel faults panic with the frame |
| Timer | `arch/x86_64/apic.rs` | 8259 remapped + masked, LAPIC calibrated against PIT channel 2, periodic 100 Hz |
| System calls | `arch/x86_64/syscall.rs`, `syscall.rs` | `syscall` → swapgs → per-CPU kernel stack → `syscall_dispatch`; interrupts enabled during syscalls so they can block |
| Frames | `mm/frame.rs` | bitmap over all RAM, first MiB reserved, contiguous allocation |
| Heap | `mm/heap.rs` | address-ordered free list with header + coalescing, 1/8 of RAM (16–256 MiB) |
| Paging | `mm/paging.rs` | `AddressSpace` shares the kernel half, owns its user frames (freed on drop); user copies validate every page's USER/WRITABLE bits |
| Scheduler | `sched/mod.rs` | per-thread 64 KiB kernel stacks, `switch_context` in assembly, round-robin preemption from the timer, sleep queue, idle fallback, reaping |
| Processes | `sched/process.rs` | ELF64 loader (no external crate), W^X user segments, 256 KiB user stack, `iretq` into ring 3 |
| Console/UI | `console.rs`, `ui/` | serial + framebuffer; pre-rendered background/card, anti-aliased shapes, DevUI tokens in `ui/theme.rs` |

Self tests (`--features selftest`) run at boot and exit QEMU through `isa-debug-exit`.

## Address space

| Range | Use |
|---|---|
| `0x0000_0000_0040_0000` – `0x0000_7FFF_FFFF_0000` | user programs, user stack at the top |
| `0xFFFF_8000_0000_0000` + phys | direct map of physical memory |
| `0xFFFF_FFFF_8000_0000` | kernel image |
| `0xFFFF_FFFF_BFFC_0000` – `0xFFFF_FFFF_C000_0000` | boot kernel stack |
