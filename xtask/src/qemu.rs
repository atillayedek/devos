//! QEMU (and other Linux-hosted build tools) run in the `devos-tools` container so the
//! build works the same on Windows, macOS and Linux hosts.


use std::process::Command;
use std::time::Duration;

use crate::{image, out_dir, root};

const TOOLS_IMAGE: &str = "devos-tools:1";

fn ensure_tools_image() -> Result<(), String> {
    let exists = Command::new("docker").args(["image", "inspect", TOOLS_IMAGE]).output()
        .map(|o| o.status.success()).unwrap_or(false);
    if exists {
        return Ok(());
    }
    println!("xtask: building {TOOLS_IMAGE} (qemu, OVMF, xorriso)");
    let status = Command::new("docker")
        .args(["build", "-t", TOOLS_IMAGE, "-f"])
        .arg(root().join("tools/Dockerfile"))
        .arg(root().join("tools"))
        .status()
        .map_err(|e| format!("docker: {e}"))?;
    if status.success() { Ok(()) } else { Err("cannot build the devos-tools image".into()) }
}

fn mount_arg() -> String {
    format!("{}:/work", out_dir().display())
}

/// Runs a command in the tools container with target/devos mounted at /work.
pub fn tools_run(cmd: &[&str]) -> Result<String, String> {
    ensure_tools_image()?;
    let out = Command::new("docker")
        .env("MSYS_NO_PATHCONV", "1")
        .args(["run", "--rm", "-v", &mount_arg(), "-w", "/work", TOOLS_IMAGE])
        .args(cmd)
        .output()
        .map_err(|e| format!("docker: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).to_string() + &String::from_utf8_lossy(&out.stderr);
    if out.status.success() { Ok(text) } else { Err(format!("{} failed:\n{text}", cmd[0])) }
}

fn qemu_script(disk: &str, extra: &str, seconds: u64, screenshot: bool) -> String {
    format!(
        r#"set -e
cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/vars.fd
timeout {seconds} qemu-system-x86_64 -machine q35 -cpu max -m 1024 -smp 1 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=/tmp/vars.fd \
  -drive format=raw,file=/work/{disk} \
  -serial file:/work/serial.log -display none -vga std \
  -monitor unix:/tmp/mon.sock,server,nowait -no-reboot {extra} &
QPID=$!
{shot}
rc=0; wait $QPID || rc=$?; echo "qemu exit $rc"
"#,
        shot = if screenshot {
            format!("sleep {}; echo 'screendump /work/screen.ppm' | socat - unix-connect:/tmp/mon.sock >/dev/null; sleep 2; pnmtopng /work/screen.ppm > /work/screen.png; rm -f /work/screen.ppm; kill $QPID 2>/dev/null || true", seconds.saturating_sub(8).max(5))
        } else {
            String::new()
        }
    )
}

pub fn run(screenshot: Option<String>, seconds: Option<u64>) -> Result<(), String> {
    let secs = seconds.unwrap_or(40);
    let _ = std::fs::remove_file(out_dir().join("serial.log"));
    let out = tools_run(&["bash", "-c", &qemu_script("devos.img", "", secs, screenshot.is_some())]);
    print_serial();
    if let Some(dest) = screenshot {
        let src = out_dir().join("screen.png");
        std::fs::copy(&src, &dest).map_err(|e| format!("screenshot: {e}"))?;
        println!("xtask: screenshot saved to {dest}");
    }
    out.map(|_| ())
}

fn print_serial() {
    if let Ok(log) = std::fs::read_to_string(out_dir().join("serial.log")) {
        println!("----- serial -----\n{}\n------------------", log.trim_end());
    }
}

/// Boots the self-test kernel; it exits QEMU through isa-debug-exit (0x10 -> 33 = pass).
pub fn selftest() -> Result<(), String> {
    let esp = crate::build(true)?;
    image::make_all(&esp, true)?;
    let _ = std::fs::remove_file(out_dir().join("serial.log"));
    let started = std::time::Instant::now();
    let out = tools_run(&["bash", "-c", &qemu_script("devos-selftest.img",
        "-device isa-debug-exit,iobase=0xf4,iosize=0x04", 180, false)])
        .unwrap_or_else(|e| e);
    print_serial();
    let log = std::fs::read_to_string(out_dir().join("serial.log")).unwrap_or_default();
    let elapsed = started.elapsed();
    let passed = out.contains("qemu exit 33") && log.contains("SELFTEST PASS");
    std::thread::sleep(Duration::from_millis(10));
    if passed {
        println!("xtask: SELFTEST PASSED in {:.1}s", elapsed.as_secs_f32());
        Ok(())
    } else {
        Err(format!("self test failed ({})", out.lines().last().unwrap_or("no output")))
    }
}

