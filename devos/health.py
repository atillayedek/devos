"""devos-health — real health checks for every tool in the manifest.

A tool is "working" only when every one of its checks passes: the binary runs
and reports a version, the desktop entry resolves to a runnable program, the
engine answers, etc. A binary merely existing is never enough. The overall
status is READY only when every *required* tool is working.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import manifest as manifest_mod, paths

WORKING, FAILED, MISSING = "working", "failed", "missing"
EXTRA_PATH = ["/opt/devos/npm/bin", "/opt/flutter/bin", "/opt/android-sdk/cmdline-tools/latest/bin",
              "/opt/android-sdk/platform-tools", "/opt/android-sdk/emulator", "/usr/local/go/bin",
              "/opt/rust/cargo/bin", "/opt/dotnet"]


class _Absent(Exception):
    """The thing being checked does not exist at all (as opposed to being broken)."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    parts = env.get("PATH", "/usr/bin:/bin").split(":")
    home = os.path.expanduser("~")
    for p in EXTRA_PATH + [f"{home}/.local/bin", f"{home}/.cargo/bin", f"{home}/go/bin"]:
        if p not in parts:
            parts.append(p)
    env["PATH"] = ":".join(parts)
    env.setdefault("ANDROID_HOME", str(paths.ANDROID_HOME))
    env.setdefault("ANDROID_SDK_ROOT", str(paths.ANDROID_HOME))
    env.setdefault("JAVA_HOME", "/usr/lib/jvm/default-java")
    env.setdefault("RUSTUP_HOME", "/opt/rust/rustup")
    env.setdefault("DOTNET_ROOT", "/opt/dotnet")
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["FLUTTER_SUPPRESS_ANALYTICS"] = "true"
    env["NO_COLOR"] = "1"
    return env


def _run(argv: list[str], user: str | None, timeout: int = 60) -> tuple[int, str]:
    env = _env()
    if user and os.geteuid() == 0 and user != "root":
        # login shell so /etc/profile.d/devos-env.sh applies exactly as for the user
        cmd = ["runuser", "-l", user, "-c", shlex.join(argv)]
        exe = argv[0]
    else:
        exe = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0], path=env["PATH"])
        if not exe:
            raise _Absent(f"{argv[0]}: command not found")
        cmd = [exe, *argv[1:]]
    try:
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise _Absent(f"{exe}: not found") from exc
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 127 and "not found" in out:
        raise _Absent(out.strip().splitlines()[-1] if out.strip() else f"{argv[0]}: not found")
    return res.returncode, out


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


# --------------------------------------------------------------- check kinds
def check_cmd(spec: dict, user: str | None) -> tuple[bool, str]:
    code, out = _run([str(a) for a in spec["cmd"]], user, timeout=spec.get("timeout", 90))
    if code != 0:
        return False, f"exit {code}: {_first_line(out)}"
    if spec.get("expect") and not re.search(spec["expect"], out, re.M):
        return False, f"unexpected output: {_first_line(out)}"
    return True, _first_line(out)


def _desktop_dirs() -> list[Path]:
    dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    dirs += ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share")]
    return [Path(d) / "applications" for d in dirs]


def check_desktop(spec: dict, user: str | None) -> tuple[bool, str]:
    desktop_id = spec["desktop"]
    for d in _desktop_dirs():
        f = d / desktop_id
        if f.is_file():
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            cp.read(f, encoding="utf-8")
            entry = cp["Desktop Entry"] if cp.has_section("Desktop Entry") else {}
            exec_line = entry.get("TryExec") or entry.get("Exec", "")
            if not exec_line:
                return False, f"{f}: no Exec"
            prog = shlex.split(exec_line)[0]
            resolved = prog if os.path.isabs(prog) else shutil.which(prog, path=_env()["PATH"])
            if not resolved or not os.access(resolved, os.X_OK):
                return False, f"{desktop_id}: launcher {prog} is not executable"
            return True, f"menu entry {desktop_id} -> {resolved}"
    raise _Absent(f"menu entry {desktop_id} not installed")


def special_kvm(_arg, user) -> tuple[bool, str]:
    cpuinfo = Path("/proc/cpuinfo").read_text()
    if not re.search(r"\b(vmx|svm)\b", cpuinfo):
        return False, "CPU virtualization (VT-x/AMD-V) unsupported or disabled in firmware"
    kvm = Path("/dev/kvm")
    if not kvm.exists():
        return False, "/dev/kvm missing (enable VT-x/AMD-V in BIOS/UEFI, then reboot)"
    if user and os.geteuid() == 0:
        code, out = _run(["test", "-r", "/dev/kvm", "-a", "-w", "/dev/kvm"], user)
        ok = code == 0
    else:
        ok = os.access(kvm, os.R_OK | os.W_OK)
    return (True, "hardware acceleration available") if ok else (False, "no access to /dev/kvm (add user to group 'kvm')")


def special_network(_arg, user) -> tuple[bool, str]:
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=6):
            return True, "online (deb.debian.org:443 reachable)"
    except OSError as exc:
        return False, f"offline: {exc}"


def special_docker(_arg, user) -> tuple[bool, str]:
    code, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"], user, timeout=60)
    if code != 0:
        return False, f"engine not reachable: {_first_line(out)}"
    return True, f"engine {_first_line(out)}"


def special_podman(_arg, user) -> tuple[bool, str]:
    code, out = _run(["podman", "info", "--format", "{{.Version.Version}}"], user, timeout=60)
    if code != 0:
        return False, f"podman info failed: {_first_line(out)}"
    return True, f"podman {_first_line(out)}"


FLUTTER_REQUIRED = ("Flutter", "Android toolchain", "Linux toolchain", "Chrome")


def special_flutter(_arg, user) -> tuple[bool, str]:
    code, out = _run(["flutter", "doctor", "-v"], user, timeout=240)
    states = {}
    for line in out.splitlines():
        m = re.match(r"^\[(.)\] (.+?)(?: \(|$| -|\s\[)", line)
        if m:
            states[m.group(2).strip()] = m.group(1)
    problems = []
    for name in FLUTTER_REQUIRED:
        key = next((k for k in states if k.startswith(name)), None)
        if key is None:
            problems.append(f"{name}: not reported")
        elif states[key] not in ("✓", "√"):
            problems.append(f"{key}: {states[key]}")
    if code != 0 and not states:
        return False, f"flutter doctor failed: {_first_line(out)}"
    return (not problems), ("flutter doctor: all required toolchains OK" if not problems else "; ".join(problems))


def special_android_sdk(_arg, user) -> tuple[bool, str]:
    home = paths.ANDROID_HOME
    if not home.exists():
        raise _Absent(f"{home} missing")
    need = {
        "platform-tools": home / "platform-tools" / "adb",
        "platform": next(iter(sorted((home / "platforms").glob("android-*"))), None) if (home / "platforms").exists() else None,
        "build-tools": next(iter(sorted((home / "build-tools").glob("*"))), None) if (home / "build-tools").exists() else None,
        "emulator": home / "emulator" / "emulator",
        "cmdline-tools": home / "cmdline-tools" / "latest" / "bin" / "sdkmanager",
        "license": home / "licenses" / "android-sdk-license",
    }
    missing = [k for k, p in need.items() if not p or not Path(p).exists()]
    if missing:
        return False, "missing: " + ", ".join(missing)
    plats = sorted(p.name for p in (home / "platforms").glob("android-*"))
    return True, f"{home} ({', '.join(plats)})"


def special_jetbrains(arg, user) -> tuple[bool, str]:
    base = paths.JETBRAINS_DIR / arg
    info_file = base / "product-info.json"
    if not info_file.exists():
        raise _Absent(f"{base} not installed")
    info = json.loads(info_file.read_text())
    launcher = base / info["launch"][0]["launcherPath"]
    if not os.access(launcher, os.X_OK):
        return False, f"launcher {launcher} not executable"
    java = base / "jbr" / "bin" / "java"
    if java.exists():
        code, out = _run([str(java), "-version"], user, timeout=60)
        if code != 0:
            return False, f"bundled runtime broken: {_first_line(out)}"
    return True, f"{info.get('name', arg)} {info.get('version', '')} (build {info.get('buildNumber', '?')})"


def special_path(arg, user) -> tuple[bool, str]:
    p = Path(arg)
    if not p.exists():
        raise _Absent(f"{arg} not found")
    return True, arg


def special_unit(arg, user) -> tuple[bool, str]:
    for d in ("/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"):
        if (Path(d) / arg).exists():
            state = subprocess.run(["systemctl", "is-active", arg], capture_output=True, text=True).stdout.strip()
            return True, f"{arg} installed ({state or 'unknown'})"
    raise _Absent(f"{arg} not installed")


def special_flatpak(arg, user) -> tuple[bool, str]:
    code, out = _run(["flatpak", "info", "--system", arg], None, timeout=30)
    if code != 0:
        raise _Absent(f"flatpak {arg} not installed")
    ver = re.search(r"Version:\s*(\S+)", out)
    return True, f"{arg} {ver.group(1) if ver else ''}".strip()


AI_EXTENSIONS = {"github.copilot", "anthropic.claude-code", "openai.chatgpt", "continue.continue",
                 "google.geminicodeassist"}


def special_editor_extensions(arg, user) -> tuple[bool, str]:
    found = []
    for editor in ("code", "codium"):
        try:
            code, out = _run([editor, "--list-extensions"], user, timeout=60)
        except _Absent:
            continue
        if code == 0:
            exts = {line.strip().lower() for line in out.splitlines()}
            found += [f"{editor}:{e}" for e in sorted(exts & AI_EXTENSIONS)]
    if not found:
        raise _Absent("no AI editor extension installed for this user")
    return True, ", ".join(found)


SPECIALS = {
    "kvm": special_kvm, "network": special_network, "docker": special_docker, "podman": special_podman,
    "flutter": special_flutter, "android-sdk": special_android_sdk, "jetbrains": special_jetbrains,
    "path": special_path, "unit": special_unit, "flatpak": special_flatpak,
    "editor-extensions": special_editor_extensions,
}


def run_check(spec: dict, user: str | None) -> dict:
    try:
        if "cmd" in spec:
            kind, (ok, detail) = "cmd", check_cmd(spec, user)
        elif "desktop" in spec:
            kind, (ok, detail) = "desktop", check_desktop(spec, user)
        else:
            name, _, arg = spec["special"].partition(":")
            kind, (ok, detail) = name, SPECIALS[name](arg, user)
        return {"kind": kind, "ok": ok, "absent": False, "detail": detail}
    except _Absent as exc:
        return {"kind": next(iter(spec)), "ok": False, "absent": True, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a crashing check is a failed check
        return {"kind": next(iter(spec)), "ok": False, "absent": False, "detail": f"check error: {exc}"}


def check_tool(tool: manifest_mod.Tool, user: str | None = None) -> dict:
    checks = [run_check(spec, user) for spec in tool.health]
    if all(c["ok"] for c in checks):
        status = WORKING
    elif all(c["absent"] for c in checks if not c["ok"]) and checks and checks[0]["absent"]:
        status = MISSING
    else:
        status = FAILED
    version = next((c["detail"] for c in checks if c["ok"] and c["kind"] == "cmd"), "")
    return {
        "id": tool.id, "name": tool.name, "category": tool.category, "required": tool.required,
        "redistributable": tool.redistributable, "status": status, "version": version, "checks": checks,
        "desktop": tool.desktop,
    }


def run_all(user: str | None = None, tool_ids: list[str] | None = None) -> dict:
    man = manifest_mod.load()
    tools = [t for t in man.tools if not tool_ids or t.id in tool_ids]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda t: check_tool(t, user), tools))
    required_bad = [r for r in results if r["required"] and r["status"] != WORKING]
    setup_incomplete = any(r["status"] == MISSING and not r["redistributable"] for r in results)
    return {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "user": user or os.environ.get("USER", ""),
        "overall": "READY" if not required_bad and not tool_ids else ("ACTION REQUIRED" if required_bad else "PARTIAL"),
        "setup_incomplete": setup_incomplete,
        "required_failing": [r["id"] for r in required_bad],
        "tools": results,
    }


def save_user_report(report: dict) -> Path:
    d = paths.user_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = d / "health.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="devos-health", description="Verify the DevOS developer environment.")
    ap.add_argument("--json", action="store_true", help="print the JSON report")
    ap.add_argument("--tool", nargs="*", help="only these tool ids")
    ap.add_argument("--user", help="(root) run checks as this user")
    ap.add_argument("--save", action="store_true", help="store the report for Developer Center")
    args = ap.parse_args(argv)
    report = run_all(user=args.user, tool_ids=args.tool)
    if args.save:
        save_user_report(report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        marks = {WORKING: "OK  ", FAILED: "FAIL", MISSING: "MISS"}
        for r in report["tools"]:
            req = "*" if r["required"] else " "
            detail = r["version"] or next((c["detail"] for c in r["checks"] if not c["ok"]), "")
            print(f"[{marks[r['status']]}]{req} {r['name'][:58]:58} {detail}")
        print(f"\nDeveloper Environment: {report['overall']}")
        if report["setup_incomplete"]:
            print("Developer Environment Setup incomplete: run Developer Center > Complete setup")
    return 0 if report["overall"] in ("READY", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
