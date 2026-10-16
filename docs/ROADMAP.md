# DevOS roadmap

Status markers: **done** (in the tree and tested in QEMU), *next*, later.

## Milestone 1 — boots, schedules, runs userspace — **done**
UEFI bootloader, higher-half kernel, interrupts, memory, paging, preemptive scheduler,
syscalls, ELF loader, ring-3 init, boot screen, build/test tooling, language policy.

## Milestone 2 — a real system core — *next*
* ACPI (MADT) + I/O APIC, SMP bring-up (AP trampoline in asm), per-CPU run queues
* SSE/AVX state save (FXSAVE/XSAVE) so userspace can use floating point
* Handles + capabilities, IPC channels (message passing), shared memory
* VFS, initramfs (tar), devfs; `open/read/write/close/stat` syscalls
* Process creation from userspace (`spawn`), waiting, signals/notifications
* Userspace `init` → service manager, logging daemon, crash handler

## Milestone 3 — drivers and storage
* PCI/PCIe enumeration, driver framework in Rust (userspace drivers over IPC where possible)
* NVMe, AHCI, virtio-blk/net/gpu, USB (xHCI) + HID keyboard/mouse
* Filesystem (DevOS native FS + FAT32/ext4 read), persistent install

## Milestone 4 — network and UI
* Network stack in Rust: Ethernet, ARP, IPv4/IPv6, ICMP, UDP, TCP, DHCP, DNS, sockets
* Intel/Realtek Ethernet, then Wi-Fi
* DevUI toolkit (window, button, text, input, list, table, tree, menu, dialog, layout, theme,
  animation, accessibility) and a compositor; Developer Center, AI Center, Vibe Coding as native apps

## Milestone 5 — developer platform
* `std` port for `x86_64-unknown-devos`, Rust/LLVM/Clang toolchains targeting DevOS, `dpm`
  package manager with signed packages, `devos-update`
* Hypervisor (VT-x/AMD-V in Rust) for the **Linux Runtime** and **Windows VM** — this is how
  Linux/Windows developer tools (IDEs, SDKs, browsers) become usable on DevOS
* AI provider SDK layer in Rust (OpenAI, Anthropic, Google, local models)

## Honest scope note
The full workstation (VS Code, JetBrains IDEs, Android Studio, Chrome, Docker, Flutter …) depends
on Milestone 5. Until then, the archived Linux-based prototype in `legacy/linux-workstation`
documents that earlier approach; it is not part of DevOS.
