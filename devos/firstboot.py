"""devos-firstboot — runs at every login (XDG autostart); does real work only once per user.

1. Creates ~/Developer.
2. Installs editor AI extensions for this user when online (VS Code / VSCodium profiles are per user).
3. Runs the full developer health check in the background and notifies the user with the
   *real* result: READY, ACTION REQUIRED, or "Developer Environment Setup incomplete".
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys

from . import health, paths


def online() -> bool:
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=5):
            return True
    except OSError:
        return False


def notify(title: str, body: str, action: str | None = None) -> None:
    if not shutil.which("notify-send"):
        return
    cmd = ["notify-send", "--app-name=DevOS", "--icon=devos-center", title, body]
    if action:
        cmd += ["--action=open=" + action, "--wait"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.stdout.strip() == "open":
            subprocess.Popen(["devos-center"], start_new_session=True)
    else:
        subprocess.run(cmd, check=False)


def main() -> int:
    state = paths.user_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    marker = state / "firstboot-done"
    paths.developer_dir().mkdir(exist_ok=True)

    is_first = not marker.exists()
    if is_first and online():
        from .provision.handlers import install_editor_extensions_for
        install_editor_extensions_for(None)

    report = health.run_all()
    health.save_user_report(report)
    if is_first or report["overall"] != "READY":
        if report["setup_incomplete"] and online():
            notify("Developer Environment Setup incomplete",
                   "Some official developer tools still need to be downloaded.", action="Complete setup")
        elif report["overall"] == "READY":
            notify("Developer Environment: READY", "All required developer tools passed their health checks.")
        else:
            failing = ", ".join(report["required_failing"][:6])
            notify("Developer Environment: ACTION REQUIRED", f"Not working: {failing}", action="Open Developer Center")
    marker.write_text(json.dumps({"overall": report["overall"], "at": report["generated"]}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
