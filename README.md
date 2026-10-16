# DevOS

DevOS, **sıfırdan yazılan kendi kernel'i olan, Rust-first bir işletim sistemidir.**
Linux kernel'i kullanılmaz. Teknoloji yığını **Rust + x86_64 Assembly**'dir; hangi katmanda
hangi dilin kullanılabileceği [docs/LANGUAGE-POLICY.md](docs/LANGUAGE-POLICY.md) içinde tanımlıdır
ve her derlemede `cargo xtask lint-languages` ile zorunlu kılınır.

![DevOS boot screen](docs/images/boot-screen.png)

## Bugün çalışan

| Katman | Durum |
|---|---|
| **devos-boot** (Rust, UEFI) | ESP'den `kernel.elf` + `init.elf` yükler, 4 seviyeli sayfa tablolarını kurar (identity + direct map + higher-half kernel + guard-page'li yığın), GOP framebuffer, ACPI RSDP, bellek haritası; CR3/yığın geçişi tek bir `asm!` bloğu |
| **Kernel** (Rust + asm) | GDT/TSS (double-fault IST), IDT + assembly kesme giriş/çıkış kodu, istisna raporlama, bitmap fiziksel çerçeve ayırıcı, birleştiren serbest liste heap'i, sayfalama ve kullanıcı adres alanları, 8259 kapatma + PIT ile kalibre edilen LAPIC zamanlayıcı, assembly bağlam geçişli **preemptive** zamanlayıcı, uyku, `syscall`/`sysret` girişi (swapgs + per-CPU), ELF64 yükleyici, ring 3 süreçler, güvenli `copy_from_user`/`copy_to_user` |
| **Syscall ABI** | `write`, `exit`, `yield`, `getpid`, `uptime_ms`, `sleep_ms`, `sysinfo` (`libs/devos-abi`) |
| **Userspace** | `x86_64-unknown-devos` hedefi (`targets/`), `devos-rt` çalışma zamanı, ring 3'te `/devos/init` |
| **Görüntü** | DevUI tasarım dilinde açılış ekranı: degrade arka plan, anti-aliased logo, Noto Sans Mono, canlı sistem kartı, ilerleme çubuğu, durum çubuğu |
| **Araçlar** | `cargo xtask`: politika denetimi, derleme, FAT32 ESP + GPT disk imajı + UEFI ISO, QEMU self-test, ekran görüntüsü |

## Derleme ve çalıştırma

Gereksinimler: Rust (`rust-toolchain.toml` nightly'yi sabitler), Docker (QEMU, OVMF ve xorriso
`tools/Dockerfile` konteynerinde çalışır).

```bash
cargo xtask test
```
Kernel self-test'leri (çerçeve ayırıcı, heap, sayfalama, zamanlayıcı) + ring 3 `init`, QEMU/UEFI'de.

```bash
cargo xtask run --screenshot boot.png
```
`target/devos/devos.img` (GPT, `dd`/Rufus ile USB'ye yazılabilir) ve `target/devos/devos.iso`
(UEFI El Torito, isohybrid) üretir, QEMU'da açar ve ekran görüntüsü alır.

## Depo yapısı

```
boot/            devos-boot — UEFI bootloader (Rust)
kernel/          DevOS kernel (Rust + asm!/global_asm!)
  src/arch/      x86_64: GDT, IDT/kesmeler, APIC, syscall girişi, seri port
  src/mm/        fiziksel çerçeveler, heap, sayfalama
  src/sched/     zamanlayıcı, bağlam geçişi, süreçler/ELF
  src/ui/        framebuffer grafikleri + DevUI tasarım token'ları
libs/            devos-bootinfo (boot protokolü), devos-abi (syscall ABI), devos-rt (userspace runtime)
userland/        native DevOS programları (x86_64-unknown-devos)
targets/         x86_64-unknown-devos.json
xtask/           derleme aracı (Rust)
tools/           QEMU/OVMF/xorriso araç konteyneri
docs/            dil politikası, mimari, yol haritası
legacy/          dondurulmuş Linux tabanlı ilk prototip (DevOS'un parçası değildir)
```

Mimari: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Yol haritası: [docs/ROADMAP.md](docs/ROADMAP.md)
