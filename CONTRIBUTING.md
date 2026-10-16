# Contributing to DevOS

Thanks for helping build DevOS! You do not need kernel experience to contribute — docs,
tests, tooling, the UI and userspace programs all need work.

## Ground rules

1. **Language policy.** Read [docs/LANGUAGE-POLICY.md](docs/LANGUAGE-POLICY.md). New system
   code is Rust; Assembly only for CPU-level work (`asm!` / `global_asm!` next to the Rust that
   uses it); C/C++ only for ports in `ports/<name>/` with a `PORT.md`. CI enforces this with
   `cargo xtask lint-languages`.
2. **Every `unsafe` block needs a `// SAFETY:` comment** explaining why it is sound. The lint
   rejects undocumented `unsafe` in `kernel/`, `boot/`, `libs/` and `userland/`.
3. **No fake functionality.** Don't add placeholders that pretend something works. If a feature
   is partial, say so in code comments and the PR description.
4. **Tests.** Kernel features get a check in `kernel/src/selftest.rs`; `cargo xtask test` must
   pass. Pure logic in `libs/` or `xtask/` gets normal `#[test]`s.
5. **Small, focused pull requests** are reviewed fastest.

## Development setup

```bash
rustup show
```
Installs the pinned nightly toolchain and targets from `rust-toolchain.toml`.

```bash
cargo xtask test
```
Builds bootloader, kernel and userland and runs the self-test kernel in QEMU (needs Docker).

```bash
cargo xtask run --screenshot boot.png
```
Boots the normal image and saves a screenshot of the framebuffer.

Useful while debugging: `DEVOS_KERNEL_FEATURES=trace-sched cargo xtask test` logs every
context switch to the serial console (`target/devos/serial.log`).

## Making a change

1. Fork the repository and create a branch: `git checkout -b feature/short-name`.
2. Keep commits focused; write messages in the imperative ("Add PCI enumeration").
3. Run `cargo xtask test` (and `cargo fmt` in the crate you touched).
4. Open a pull request using the template. Describe what changed, how you tested it, and
   what is not done yet.

## Where to start

* Issues labelled **good first issue** and **help wanted**.
* The next milestone in [docs/ROADMAP.md](docs/ROADMAP.md).
* Architecture overview: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licensing of contributions

DevOS is MIT-licensed. By submitting a contribution you agree that it is your own work (or you
have the right to submit it) and that it is licensed under the MIT license. Do not copy code
from projects with incompatible licenses (e.g. GPL) into DevOS.

---

### Türkçe kısa rehber

1. Depoyu fork'layın, yeni bir dal açın.
2. Kodu **Rust** ile yazın; Assembly yalnızca CPU seviyesindeki işler için. Her `unsafe` blok
   `// SAFETY:` açıklaması içermeli.
3. `cargo xtask test` çalıştırın (Docker gerekir), geçtiğinden emin olun.
4. Pull request açın; ne değiştiğini ve nasıl test ettiğinizi yazın.
Katkılarınız MIT lisansı altında yayınlanır.
