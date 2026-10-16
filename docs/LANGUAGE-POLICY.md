# DevOS language policy

DevOS is a **Rust-first operating system** with its own kernel (no Linux kernel).
The technology stack is **Rust + x86_64 Assembly**. This policy is enforced by
`cargo xtask lint-languages`, which runs as part of every `cargo xtask build`.

## Priority for new code

1. **Rust** — default for everything
2. **x86_64 Assembly** — only where the hardware requires it
3. **C** — only for ports / compatibility that technically require it
4. **C++** — only when porting existing large C++ projects
5. **Python** — build / tooling only
6. **TypeScript** — web tooling / extensions only (preferred over JavaScript)
7. **Bash** — build / bootstrap only

## Layer → language

| Layer | Directory | Allowed |
|---|---|---|
| Bootloader | `boot/` | Rust; Assembly for CPU/UEFI hand-off (`asm!`/`global_asm!`) |
| Kernel (memory, paging, scheduler, processes, threads, IPC, syscalls, VFS, filesystems, network stack, driver framework, device manager, security, hypervisor) | `kernel/` | Rust; Assembly for boot entry, interrupt/exception/syscall entry, context switch, SMP start, VM entry/exit |
| Drivers (NVMe, AHCI, USB, HID, Ethernet, Wi-Fi, Bluetooth, audio, GPU, PCI/PCIe) | `kernel/src/drivers/`, `drivers/` | Rust |
| Shared ABI / boot protocol | `libs/` | Rust |
| Userspace runtime + services (init, service/network/device/login manager, package daemon, updater, account/credential manager, logging, crash handler) | `userland/`, `libs/` | Rust |
| Native CLIs (`devos`, `dpm`, `devos-doctor`, `devos-update`, `devos-sdk`, `devos-vm`, `devos-ai`) | `userland/` | Rust |
| Native UI toolkit **DevUI** and native apps (Developer Center, AI Center, Vibe Coding, Installer UI) | `userland/`, `libs/devui` | Rust |
| AI provider SDK layer (OpenAI, Anthropic, Google, local models) | `libs/` | Rust |
| Build / image / test tooling | `xtask/` (Rust), `scripts/` | Rust preferred; Python and Bash allowed |
| Ports of existing C / C++ projects | `ports/<project>/` | C, C++ (each port documents why) |
| Web tools, docs UI, IDE extensions | `web/` | TypeScript (JavaScript only when unavoidable) |
| Archived Linux-based prototype | `legacy/` | frozen — not part of DevOS |

## Rules

* Assembly lives inside Rust files (`core::arch::asm!` / `global_asm!`) next to the Rust code
  that uses it; standalone `.S`/`.asm` files are allowed only in `kernel/` and `boot/`.
* No C or C++ outside `ports/`. A port needs `ports/<name>/PORT.md` stating the license and why
  a Rust implementation is not used.
* No Python, shell or JavaScript in kernel, boot, drivers, libraries or userspace services.
* `unsafe` Rust is confined to the smallest possible scope and every `unsafe` block carries a
  `// SAFETY:` comment.

## Compiler targets

* Kernel: `x86_64-unknown-none` (+ kernel code model, static relocation, `kernel/linker.ld`).
* Bootloader: `x86_64-unknown-uefi`.
* Userspace: **`x86_64-unknown-devos`** — `targets/x86_64-unknown-devos.json`, built with
  `-Zbuild-std`. LLVM/Clang and GCC target support (`x86_64-devos` triple, sysroot layout) will
  be added in `toolchain/` when the C/C++ port layer starts.
