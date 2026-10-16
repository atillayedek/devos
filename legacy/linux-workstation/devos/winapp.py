"""Windows executable analysis: decides how DevOS should run a .exe / .msi.

Categories (a heuristic recommendation, always shown as such to the user):
  wine      "Wine compatible"      — plain Win32/Win64 GUI/console apps, MSI packages
  bottles   "Bottles recommended"  — .NET apps or DirectX/Vulkan games that benefit
                                     from a managed prefix with DXVK/VKD3D/runtimes
  vm        "Windows VM required"  — kernel drivers, anti-cheat, Hyper-V/WSL tooling,
                                     and products known not to run under Wine
                                     (e.g. Visual Studio, SQL Server, Office 365 click-to-run)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

WINE, BOTTLES, VM = "wine", "bottles", "vm"
LABELS = {WINE: "Wine compatible", BOTTLES: "Bottles recommended", VM: "Windows VM required"}

# Products whose installers/apps are known not to work under Wine.
VM_PRODUCTS = [
    (r"visual studio(?! code)", "Visual Studio does not run under Wine; use the Windows Developer VM"),
    (r"sql server", "SQL Server requires Windows services and drivers"),
    (r"microsoft office|office 365|click-to-run", "Microsoft 365 Click-to-Run is not supported by Wine"),
    (r"hyper-v|windows subsystem for linux|wsl", "Requires Windows virtualization features"),
    (r"windows sdk|windows driver kit|\bwdk\b", "Windows SDK/WDK tooling targets a real Windows install"),
    (r"adobe (photoshop|premiere|after effects|illustrator) 20(2[2-9]|[3-9]\d)", "Recent Adobe Creative Cloud apps do not work under Wine"),
]
ANTI_CHEAT = [b"EasyAntiCheat", b"BEService", b"BattlEye", b"vgk.sys", b"Vanguard", b"xigncode", b"nProtect",
              b"FACEIT", b"EAAntiCheat", b"mhyprot"]
GPU_IMPORTS = {"d3d9.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "vulkan-1.dll", "d3dx9_43.dll", "xinput1_3.dll"}
KERNEL_IMPORTS = {"ntoskrnl.exe", "hal.dll", "fltmgr.sys", "ndis.sys", "wdfldr.sys"}


@dataclass
class Analysis:
    path: str
    category: str
    label: str
    reasons: list[str] = field(default_factory=list)
    kind: str = ""
    arch: str = ""
    product: str = ""
    is_dotnet: bool = False
    is_installer: bool = False


def _version_strings(pe) -> dict[str, str]:
    out = {}
    for fileinfo in getattr(pe, "FileInfo", []) or []:
        for entry in fileinfo:
            for st in getattr(entry, "StringTable", []) or []:
                for k, v in st.entries.items():
                    out[k.decode(errors="ignore")] = v.decode(errors="ignore")
    return out


def analyze(path: str | Path) -> Analysis:
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == ".msi" or data[:8] == bytes.fromhex("d0cf11e0a1b11ae1"):
        return _analyze_msi(path, data)
    if data[:2] != b"MZ":
        raise ValueError(f"{path.name} is not a Windows executable")
    import pefile  # python3-pefile
    pe = pefile.PE(data=data, fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR"],
    ])
    machine = pe.FILE_HEADER.Machine
    arch = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, hex(machine))
    subsystem = pe.OPTIONAL_HEADER.Subsystem
    imports = {e.dll.decode(errors="ignore").lower() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])}
    clr = pe.OPTIONAL_HEADER.DATA_DIRECTORY[14]
    is_dotnet = clr.VirtualAddress != 0 and clr.Size != 0
    info = _version_strings(pe)
    product = " ".join(filter(None, [info.get("ProductName"), info.get("FileDescription")])).strip()
    is_installer = bool(re.search(r"setup|install|bootstrapper", product + " " + path.name, re.I)) or \
        b"Inno Setup" in data or b"Nullsoft" in data or b"InstallShield" in data

    res = Analysis(str(path), WINE, LABELS[WINE], kind="PE", arch=arch, product=product,
                   is_dotnet=is_dotnet, is_installer=is_installer)
    haystack = f"{product} {path.name}".lower()

    if subsystem == 1 or path.suffix.lower() == ".sys" or imports & KERNEL_IMPORTS:
        return _vm(res, "Kernel-mode driver: Wine cannot load Windows drivers")
    if arch == "arm64":
        return _vm(res, "Windows on ARM binary")
    for pattern, why in VM_PRODUCTS:
        if re.search(pattern, haystack):
            return _vm(res, why)
    hits = [s.decode() for s in ANTI_CHEAT if s in data]
    if hits:
        return _vm(res, f"Kernel anti-cheat detected ({', '.join(hits)}); may not run even in a VM")
    if is_dotnet:
        res.reasons.append(".NET Framework application: Bottles installs the .NET runtime into an isolated prefix")
        res.category, res.label = BOTTLES, LABELS[BOTTLES]
    gpu = sorted(imports & GPU_IMPORTS)
    if gpu:
        res.reasons.append(f"Uses {', '.join(gpu)}: Bottles' gaming environment provides DXVK/VKD3D")
        res.category, res.label = BOTTLES, LABELS[BOTTLES]
    if res.category == WINE:
        res.reasons.append(f"Standard {arch} {'console' if subsystem == 3 else 'GUI'} application")
    return res


def _vm(res: Analysis, why: str) -> Analysis:
    res.category, res.label = VM, LABELS[VM]
    res.reasons.append(why)
    return res


def _analyze_msi(path: Path, data: bytes) -> Analysis:
    res = Analysis(str(path), WINE, LABELS[WINE], kind="MSI", is_installer=True)
    text = data[:4_000_000].decode("utf-16-le", errors="ignore") + data[:4_000_000].decode("latin-1", errors="ignore")
    for pattern, why in VM_PRODUCTS:
        if re.search(pattern, text, re.I):
            return _vm(res, why)
    res.reasons.append("Windows Installer package: runs with `wine msiexec /i`")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="devos-winapp-analyze")
    ap.add_argument("file")
    args = ap.parse_args(argv)
    print(json.dumps(asdict(analyze(args.file)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
