"""Windows Development Environment — a QEMU/KVM Windows VM managed through libvirt.

DevOS does not ship Windows. The user supplies their own Windows ISO and license;
DevOS creates the VM with UEFI + Secure Boot + TPM 2.0 (Windows 11 requirements),
VirtIO disk/network, SPICE clipboard + USB redirection, and shares ~/Developer
with the guest over virtiofs. Visual Studio runs *inside* this VM; DevOS never
claims it runs natively on Linux.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

CONNECT = "qemu:///system"
VM_NAME = "devos-windows"
VIRTIO_WIN_URL = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
VS_BOOTSTRAPPERS = {
    "Visual Studio 2026 Community": "https://aka.ms/vs/18/stable/vs_community.exe",
    "Visual Studio 2022 Community": "https://aka.ms/vs/17/release/vs_community.exe",
}
SHARE_TAG = "devos-developer"
Progress = Callable[[int, int], None]


def data_dir() -> Path:
    d = Path.home() / ".local" / "share" / "devos" / "vm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def share_dir() -> Path:
    d = paths.developer_dir()
    (d / "WindowsShare").mkdir(parents=True, exist_ok=True)
    return d


def virsh(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", "-c", CONNECT, *args], capture_output=True, text=True, check=check)


def state() -> str:
    res = virsh("domstate", VM_NAME)
    return res.stdout.strip() if res.returncode == 0 else "not created"


def prerequisites() -> list[str]:
    problems = []
    if not Path("/dev/kvm").exists():
        problems.append("KVM is unavailable: enable VT-x/AMD-V in your firmware settings")
    elif not os.access("/dev/kvm", os.R_OK | os.W_OK):
        problems.append("Your account cannot use /dev/kvm (log out and back in after installation)")
    for tool in ("virsh", "virt-install", "virt-viewer"):
        if not shutil.which(tool):
            problems.append(f"{tool} is not installed")
    if shutil.which("virsh"):
        res = virsh("uri")
        if res.returncode != 0:
            problems.append("libvirt is not reachable: " + (res.stderr.strip() or "is the user in group 'libvirt'?"))
    return problems


def ensure_default_network() -> None:
    info = virsh("net-info", "default")
    if info.returncode != 0:
        raise RuntimeError("libvirt network 'default' is missing")
    if re.search(r"Active:\s+no", info.stdout):
        virsh("net-start", "default", check=True)
    if re.search(r"Autostart:\s+no", info.stdout):
        virsh("net-autostart", "default", check=True)


def _download(url: str, dest: Path, progress: Progress | None, sha256: str | None = None) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "DevOS/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        final_url = resp.geturl()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    # Microsoft's CDN path embeds the file's SHA-256 (…/<64 hex>/vs_Community.exe)
    embedded = re.search(r"/([0-9a-f]{64})/[^/]+$", final_url)
    expected = sha256 or (embedded.group(1) if embedded else None)
    if expected and h.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {url}")
    tmp.replace(dest)
    return dest


def fetch_virtio_win(progress: Progress | None = None) -> Path:
    dest = data_dir() / "virtio-win.iso"
    if dest.exists() and dest.stat().st_size > 100_000_000:
        return dest
    return _download(VIRTIO_WIN_URL, dest, progress)


def grant_qemu_access(path: Path) -> None:
    """Let the libvirt-qemu user read an ISO in the user's home (home dirs are 0700)."""
    path = path.resolve()
    subprocess.run(["setfacl", "-m", "u:libvirt-qemu:r", str(path)], check=True)
    for parent in path.parents:
        if parent == Path("/"):
            break
        if os.stat(parent).st_uid == os.getuid():
            subprocess.run(["setfacl", "-m", "u:libvirt-qemu:x", str(parent)], check=True)


def build_virt_install(iso: Path, virtio: Path, ram_mb: int, vcpus: int, disk_gb: int) -> list[str]:
    share = share_dir()
    return [
        "virt-install", "--connect", CONNECT, "--name", VM_NAME,
        # detect the Windows version from the ISO via libosinfo, assume Windows 11 otherwise
        "--osinfo", "detect=on,name=win11",
        "--memory", str(ram_mb), "--vcpus", str(vcpus), "--cpu", "host-passthrough",
        "--machine", "q35",
        "--boot", "uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes,"
                  "firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=yes",
        "--tpm", "backend.type=emulator,backend.version=2.0,model=tpm-crb",
        "--features", "hyperv.relaxed.state=on,hyperv.vapic.state=on,hyperv.spinlocks.state=on,"
                      "hyperv.spinlocks.retries=8191,smm.state=on",
        "--clock", "offset=localtime,hypervclock_present=yes",
        "--disk", f"size={disk_gb},bus=virtio,format=qcow2,cache=none,discard=unmap",
        "--disk", f"path={iso},device=cdrom,bus=sata",
        "--disk", f"path={virtio},device=cdrom,bus=sata",
        "--network", "network=default,model=virtio",
        "--graphics", "spice,listen=none", "--video", "qxl",
        "--channel", "spicevmc,target.type=virtio,target.name=com.redhat.spice.0",
        "--redirdev", "usb,type=spicevmc", "--redirdev", "usb,type=spicevmc",
        "--memorybacking", "source.type=memfd,access.mode=shared",
        "--filesystem", f"source.dir={share},target.dir={SHARE_TAG},driver.type=virtiofs",
        "--sound", "ich9", "--input", "tablet,bus=usb",
        "--noautoconsole", "--wait", "0",
    ]


def create(iso: Path, ram_mb: int, vcpus: int, disk_gb: int, progress: Progress | None = None) -> str:
    problems = prerequisites()
    if problems:
        raise RuntimeError("; ".join(problems))
    if state() != "not created":
        raise RuntimeError(f"VM '{VM_NAME}' already exists")
    if not iso.is_file():
        raise RuntimeError(f"{iso} does not exist")
    virtio = fetch_virtio_win(progress)
    for p in (iso, virtio):
        grant_qemu_access(p)
    ensure_default_network()
    cmd = build_virt_install(iso, virtio, ram_mb, vcpus, disk_gb)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout


def start() -> None:
    if state() != "running":
        res = virsh("start", VM_NAME)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())


def open_viewer() -> None:
    subprocess.Popen(["virt-viewer", "--connect", CONNECT, "--attach", "--wait", VM_NAME],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def shutdown() -> None:
    virsh("shutdown", VM_NAME)


def send_visual_studio_installer(edition: str, progress: Progress | None = None) -> Path:
    """Download Microsoft's official bootstrapper into the folder shared with the VM."""
    url = VS_BOOTSTRAPPERS[edition]
    dest = share_dir() / "WindowsShare" / f"{edition.replace(' ', '_')}_vs_community.exe"
    return _download(url, dest, progress)


GUEST_SETUP = """DevOS Windows Development Environment — guest setup
====================================================

Inside Windows (one time):
1. Open the 'virtio-win' CD drive and run virtio-win-guest-tools.exe
   (VirtIO drivers, SPICE agent for clipboard/resolution, QEMU guest agent).
2. Install WinFsp (https://winfsp.dev/rel/) and start the "VirtIO-FS Service"
   (services.msc -> VirtIO-FS Service -> Startup type: Automatic -> Start).
   Your Linux ~/Developer folder then appears as drive Z:.
3. Run Z:\\WindowsShare\\Visual_Studio_*_vs_community.exe to install Visual Studio.

USB devices: in the viewer use File > USB device selection.
"""


def write_guest_instructions() -> Path:
    p = share_dir() / "WindowsShare" / "README-DevOS-Windows-VM.txt"
    p.write_text(GUEST_SETUP.replace("\n", "\r\n"))
    return p
