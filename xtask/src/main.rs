//! `cargo xtask` — DevOS build tool.
//!
//!   cargo xtask lint-languages        enforce docs/LANGUAGE-POLICY.md
//!   cargo xtask build [--selftest]    bootloader + kernel + userland -> target/devos/esp
//!   cargo xtask image [--selftest]    + FAT ESP image, GPT disk image, UEFI ISO
//!   cargo xtask test                  self-test kernel in QEMU, checks the serial log
//!   cargo xtask run [--screenshot F]  boot the image in QEMU (headless), optional screenshot

mod image;
mod lint;
mod qemu;

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

pub fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

pub fn out_dir() -> PathBuf {
    root().join("target").join("devos")
}

fn cargo(dir: &str, args: &[&str]) -> Result<(), String> {
    let status = Command::new(std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()))
        .current_dir(root().join(dir))
        // the parent cargo's toolchain variables must not leak into the nested builds
        .env_remove("RUSTUP_TOOLCHAIN")
        .env_remove("RUSTC")
        .env_remove("CARGO")
        .args(args)
        .status()
        .map_err(|e| format!("cannot run cargo in {dir}: {e}"))?;
    if status.success() { Ok(()) } else { Err(format!("cargo {} failed in {dir}", args.join(" "))) }
}

/// Installs the pinned toolchain (rust-toolchain.toml) once, up front. Letting rustup install
/// it lazily from the parallel rustc invocations of a build races and corrupts the toolchain.
fn ensure_toolchain() -> Result<(), String> {
    let status = Command::new("rustup")
        .current_dir(root())
        .env_remove("RUSTUP_TOOLCHAIN")
        .args(["toolchain", "install"])
        .status()
        .map_err(|e| format!("cannot run rustup: {e}"))?;
    if status.success() { Ok(()) } else { Err("rustup could not install the pinned toolchain".into()) }
}

pub fn build(selftest: bool) -> Result<PathBuf, String> {
    lint::run()?;
    ensure_toolchain()?;
    cargo("boot", &["build", "--release", "--target", "x86_64-unknown-uefi"])?;
    // extra kernel features, e.g. DEVOS_KERNEL_FEATURES=trace-sched
    let mut features: Vec<String> = std::env::var("DEVOS_KERNEL_FEATURES")
        .map(|f| f.split(',').filter(|s| !s.is_empty()).map(String::from).collect())
        .unwrap_or_default();
    if selftest {
        features.push("selftest".into());
    }
    let joined = features.join(",");
    let mut kargs = vec!["build", "--release"];
    if !joined.is_empty() {
        kargs.extend(["--features", joined.as_str()]);
    }
    cargo("kernel", &kargs)?;
    cargo("userland", &["build", "--release"])?;

    let esp = out_dir().join(if selftest { "esp-selftest" } else { "esp" });
    let _ = std::fs::remove_dir_all(&esp);
    std::fs::create_dir_all(esp.join("EFI/BOOT")).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(esp.join("devos")).map_err(|e| e.to_string())?;
    let copy = |from: PathBuf, to: PathBuf| {
        std::fs::copy(&from, &to).map(|_| ()).map_err(|e| format!("{}: {e}", from.display()))
    };
    copy(root().join("boot/target/x86_64-unknown-uefi/release/devos-boot.efi"), esp.join("EFI/BOOT/BOOTX64.EFI"))?;
    copy(root().join("kernel/target/x86_64-unknown-none/release/kernel"), esp.join("devos/kernel.elf"))?;
    copy(root().join("userland/target/x86_64-unknown-devos/release/init"), esp.join("devos/init.elf"))?;
    println!("xtask: boot files in {}", esp.display());
    Ok(esp)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let has = |f: &str| args.iter().any(|a| a == f);
    let value = |f: &str| args.iter().position(|a| a == f).and_then(|i| args.get(i + 1)).cloned();
    let result = match args.first().map(String::as_str) {
        Some("lint-languages") => lint::run(),
        Some("build") => build(has("--selftest")).map(|_| ()),
        Some("image") => build(has("--selftest")).and_then(|esp| image::make_all(&esp, has("--selftest"))),
        Some("test") => qemu::selftest(),
        Some("run") => build(false)
            .and_then(|esp| image::make_all(&esp, false))
            .and_then(|_| qemu::run(value("--screenshot"), value("--seconds").and_then(|s| s.parse().ok()))),
        _ => {
            eprintln!("usage: cargo xtask <lint-languages|build|image|test|run> [--selftest] [--screenshot FILE.png]");
            Err(String::from("unknown command"))
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("xtask: error: {e}");
            ExitCode::FAILURE
        }
    }
}
