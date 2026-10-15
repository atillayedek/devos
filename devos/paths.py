"""Filesystem locations shared by every DevOS component."""

import os
from pathlib import Path

MANIFEST = Path(os.environ.get("DEVOS_MANIFEST", "/usr/share/devos/tools.yaml"))
STATE_DIR = Path("/var/lib/devos")
LOG_DIR = Path("/var/log/devos")
ETC_DIR = Path("/etc/devos")
ACCEPTED_LICENSES = ETC_DIR / "accepted-licenses.json"
SYSTEM_HEALTH = STATE_DIR / "health.json"
OPT = Path("/opt")
DEVOS_OPT = OPT / "devos"
NPM_PREFIX = DEVOS_OPT / "npm"
UV_TOOL_DIR = DEVOS_OPT / "uv-tools"
UV_PYTHON_DIR = DEVOS_OPT / "uv-python"
BIN_DIR = Path("/usr/local/bin")
APPS_DIR = Path("/usr/share/applications")
KEYRINGS_DIR = Path("/usr/share/keyrings")
SOURCES_DIR = Path("/etc/apt/sources.list.d")
ANDROID_HOME = OPT / "android-sdk"
FLUTTER_HOME = OPT / "flutter"
JETBRAINS_DIR = OPT / "jetbrains"
DEV_GROUP = "devos-dev"


def user_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "devos"


def developer_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Developer"
