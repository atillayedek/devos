"""devos-provision — installs and repairs the DevOS developer workstation.

Used in three places:
  * ISO build (DevOS-Full-Offline): `--scope redistributable` bakes every tool whose
    license permits redistribution into the live filesystem.
  * Calamares (Full Online install): `--scope all` inside the installed target,
    after the user accepted the third-party licenses.
  * Developer Center: `--only-missing` / `--tools ID` to complete setup or repair.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import health, manifest as manifest_mod, paths
from . import handlers
from .util import (ProvisionError, add_apt_repo, apt_install, apt_update, debconf_set, dpkg_installed, ensure_group,
                   run)

log = logging.getLogger("devos.provision")


def load_accepted() -> list[str]:
    try:
        data = json.loads(paths.ACCEPTED_LICENSES.read_text())
        return list(data.get("accepted", []))
    except (OSError, ValueError):
        return []


def record_acceptance(tool_ids: list[str], via: str) -> None:
    accepted = sorted(set(load_accepted()) | set(tool_ids))
    paths.ETC_DIR.mkdir(parents=True, exist_ok=True)
    paths.ACCEPTED_LICENSES.write_text(json.dumps({
        "accepted": accepted,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "via": via,
    }, indent=2) + "\n")


# -------------------------------------------------------------- per-method
def install_tool(tool: manifest_mod.Tool, man: manifest_mod.Manifest, ctx: handlers.Context) -> None:
    inst = tool.install
    method = tool.method
    if method == "apt":
        missing = [p for p in inst["packages"] if not dpkg_installed(p.split(":")[0])]
        if missing:
            apt_install(missing)
    elif method == "apt-repo":
        add_apt_repo(inst["repo"], man.repos[inst["repo"]])
        apt_update()
        if inst.get("debconf"):
            debconf_set(inst["debconf"])
        apt_install(inst["packages"])
        if inst.get("npm"):
            npm_install(inst["npm"])
    elif method == "npm":
        npm_install(inst["packages"])
    elif method == "uv-tool":
        for pkg in inst["packages"]:
            cmd = ["uv", "tool", "install", "--force"]
            if inst.get("python"):
                cmd += ["--python", inst["python"]]
            run(cmd + [pkg])
    elif method == "flatpak":
        run(["flatpak", "remote-add", "--system", "--if-not-exists", "flathub",
             "https://dl.flathub.org/repo/flathub.flatpakrepo"])
        run(["flatpak", "install", "--system", "-y", "--noninteractive", "flathub", inst["app"]])
    elif method == "handler":
        fn = handlers.INSTALLERS.get(inst["handler"])
        if not fn:
            raise ProvisionError(f"no handler {inst['handler']}")
        fn(tool, ctx)
    else:  # pragma: no cover - guarded by manifest validation
        raise ProvisionError(f"unknown method {method}")


def npm_install(packages: list[str]) -> None:
    npm = shutil.which("npm", path=handlers_path()) or "/opt/node/bin/npm"
    if not Path(npm).exists():
        raise ProvisionError("Node.js is not installed (the 'node' tool must be provisioned first)")
    paths.NPM_PREFIX.mkdir(parents=True, exist_ok=True)
    run([npm, "install", "--global", "--prefix", str(paths.NPM_PREFIX), "--no-audit", *packages])
    bindir = paths.NPM_PREFIX / "bin"
    for entry in bindir.iterdir():
        dst = paths.BIN_DIR / entry.name
        if dst.is_symlink() or not dst.exists():
            dst.unlink(missing_ok=True)
            dst.symlink_to(entry)


def handlers_path() -> str:
    from .util import provision_env
    return provision_env()["PATH"]


# ----------------------------------------------------------- special stages
def stage_environment(ctx) -> None:
    ensure_group(paths.DEV_GROUP)
    env_file = Path("/etc/profile.d/devos-env.sh")
    if not env_file.exists():
        raise ProvisionError(f"{env_file} missing — the DevOS base package is not installed correctly")
    # Flutter needs a Chrome executable for web; prefer Google Chrome, fall back to Chromium.
    chrome = shutil.which("google-chrome-stable") or shutil.which("chromium")
    if chrome:
        Path("/etc/devos").mkdir(parents=True, exist_ok=True)
        Path("/etc/devos/chrome-executable").write_text(chrome + "\n")


def stage_desktop(ctx) -> None:
    run(["update-desktop-database", str(paths.APPS_DIR)], check=False)
    run(["gtk-update-icon-cache", "-q", "-t", "-f", "/usr/share/icons/hicolor"], check=False)


def stage_health(ctx) -> dict:
    report = health.run_all(user=ctx.target_user)
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    paths.SYSTEM_HEALTH.write_text(json.dumps(report, indent=2) + "\n")
    if ctx.target_user and shutil.which("flutter", path=handlers_path()):
        logf = paths.LOG_DIR / "flutter-doctor.log"
        res = run(["flutter", "doctor", "-v"], user=ctx.target_user, check=False, capture=True,
                  env={"CHROME_EXECUTABLE": _chrome()})
        logf.write_text(res.stdout or "")
    return report


def _chrome() -> str:
    try:
        return Path("/etc/devos/chrome-executable").read_text().strip()
    except OSError:
        return "/usr/bin/chromium"


def stage_cleanup(ctx) -> None:
    run(["apt-get", "clean"], check=False)
    for d in ("/var/cache/devos/downloads", "/var/cache/devos/aws-cli", "/var/cache/devos/gocache",
              "/var/cache/devos/gopath", "/root/.npm", "/root/.cache/uv", "/root/.cache/pip"):
        shutil.rmtree(d, ignore_errors=True)


def stage_snapshot(ctx) -> None:
    """Record the installed state; on btrfs also take a read-only root snapshot."""
    snap = paths.STATE_DIR / "install-snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    sel = subprocess.run(["dpkg", "--get-selections"], capture_output=True, text=True).stdout
    (snap / "dpkg-selections.txt").write_text(sel)
    versions = {}
    for marker in Path("/opt").rglob(".devos-version"):
        if marker.parent.parent == Path("/opt") or marker.parent.parent.parent == Path("/opt"):
            versions[str(marker.parent)] = marker.read_text().strip()
    (snap / "tool-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
    fstype = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "/"], capture_output=True, text=True).stdout.strip()
    if fstype == "btrfs" and Path("/.snapshots").is_dir():
        name = "/.snapshots/devos-post-install"
        if not Path(name).exists():
            run(["btrfs", "subvolume", "snapshot", "-r", "/", name], check=False)


SPECIAL_STAGES = {
    "environment": stage_environment,
    "desktop": stage_desktop,
    "health": stage_health,
    "cleanup": stage_cleanup,
    "snapshot": stage_snapshot,
}


# --------------------------------------------------------------------- main
def select_tools(man, scope: str, only: list[str] | None, accepted: list[str]):
    chosen = []
    skipped = []
    for tool in man.tools:
        if only and tool.id not in only:
            continue
        if scope == "redistributable" and not tool.redistributable:
            continue
        if scope == "proprietary" and tool.redistributable:
            continue
        if not tool.redistributable and tool.id not in accepted:
            skipped.append((tool.id, "license not accepted"))
            continue
        chosen.append(tool)
    return chosen, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="devos-provision", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=["all", "redistributable", "proprietary"], default="all")
    ap.add_argument("--tools", nargs="*", help="only these tool ids")
    ap.add_argument("--stages", nargs="*", help="only these stages")
    ap.add_argument("--only-missing", action="store_true", help="skip tools whose health checks already pass")
    ap.add_argument("--target-user", help="desktop user for per-user steps (AVD, editor extensions, flutter doctor)")
    ap.add_argument("--accept-licenses", nargs="*", metavar="TOOL",
                    help="record the user's acceptance of these tools' licenses ('all' = every listed tool)")
    ap.add_argument("--accepted-via", default="cli")
    ap.add_argument("--offline-bundle", action="store_true", help="ISO build: no per-user or service steps")
    ap.add_argument("--resolve-only", action="store_true", help="resolve download URLs + checksums, install nothing")
    ap.add_argument("--report", type=Path, default=paths.STATE_DIR / "provision-report.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    paths.LOG_DIR.mkdir(parents=True, exist_ok=True) if os.geteuid() == 0 else None
    handlers_ = [logging.StreamHandler(sys.stdout)]
    if os.geteuid() == 0 and not args.resolve_only:
        handlers_.append(logging.FileHandler(paths.LOG_DIR / "provision.log"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers_)

    man = manifest_mod.load()
    if args.accept_licenses:
        ids = [t.id for t in man.proprietary()] if "all" in args.accept_licenses else args.accept_licenses
        record_acceptance(ids, args.accepted_via)
    accepted = load_accepted()
    ctx = handlers.Context(target_user=args.target_user, accept_licenses=True, accepted=accepted,
                           offline_bundle=args.offline_bundle)

    if args.resolve_only:
        # validating sources needs no license acceptance: nothing is installed
        return resolve_only(select_tools(man, args.scope, args.tools, [t.id for t in man.tools])[0], ctx)
    tools, skipped = select_tools(man, args.scope, args.tools, accepted)
    if os.geteuid() != 0:
        log.error("devos-provision must run as root (use pkexec)")
        return 2

    results: dict[str, dict] = {tid: {"status": "skipped", "reason": why} for tid, why in skipped}
    stages = args.stages or man.stages
    for stage in man.stages:
        if stage not in stages:
            continue
        if stage in SPECIAL_STAGES:
            if args.offline_bundle and stage in ("health", "snapshot"):
                continue
            if args.tools and stage not in ("desktop", "environment"):
                continue
            log.info("== stage %s", stage)
            try:
                SPECIAL_STAGES[stage](ctx)
            except Exception as exc:  # noqa: BLE001 - report every failure, keep provisioning
                log.error("stage %s failed: %s", stage, exc)
                results[f"stage:{stage}"] = {"status": "failed", "error": str(exc)}
            continue
        for tool in [t for t in tools if t.stage == stage]:
            if args.offline_bundle and tool.install.get("handler") in ("android_system_image", "editor_extensions"):
                continue
            if args.only_missing:
                check = health.check_tool(tool, user=args.target_user)
                if check["status"] == "working":
                    results[tool.id] = {"status": "already-working"}
                    continue
            log.info("== [%s] installing %s", stage, tool.name)
            try:
                install_tool(tool, man, ctx)
                results[tool.id] = {"status": "installed"}
            except Exception as exc:  # noqa: BLE001
                log.error("%s failed: %s", tool.id, exc)
                results[tool.id] = {"status": "failed", "error": str(exc)}

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({
        "finished": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": args.scope,
        "results": results,
    }, indent=2) + "\n")
    failed = [k for k, v in results.items() if v["status"] == "failed"]
    log.info("provisioning finished: %d installed, %d failed, %d skipped",
             sum(v["status"] == "installed" for v in results.values()), len(failed),
             sum(v["status"] == "skipped" for v in results.values()))
    if failed:
        log.error("failed: %s", ", ".join(failed))
    return 1 if failed else 0


def resolve_only(tools, ctx) -> int:
    bad = 0
    for tool in tools:
        if tool.method != "handler" or tool.install["handler"] not in handlers.RESOLVERS:
            continue
        try:
            for art in handlers.RESOLVERS[tool.install["handler"]](tool, ctx):
                digest = art.sha256 or art.sha512 or art.sha1 or ("gpg-signature" if art.unverified else None)
                print(f"OK   {tool.id:22} {art.version:28} {art.url}  [{'verified' if digest else 'NO DIGEST'}]")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"FAIL {tool.id:22} {exc}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
