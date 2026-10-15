#!/usr/bin/env python3
"""Calamares job: provision the DevOS developer workstation inside the installed target.

Full Online  -> /usr/lib/devos/calamares-provision runs devos-provision in the target chroot.
Full Offline -> nothing is downloaded; Developer Center offers "Complete setup" later.
Tool failures never break the OS installation: they are recorded and shown honestly
in Developer Center ("ACTION REQUIRED") with a one-click repair.
"""

import subprocess

import libcalamares

STAGES = ["packages", "languages", "android", "flutter", "containers", "virtualization", "windows",
          "browsers", "ides", "jetbrains", "ai", "cloud", "git", "environment", "desktop", "health",
          "cleanup", "snapshot"]


def pretty_name():
    return "Installing developer tools"


def pretty_status_message():
    return _status


_status = "Preparing developer tools"


def run():
    global _status
    gs = libcalamares.globalstorage
    mode = (gs.value("packagechooser_devosmode") or "offline").strip()
    root = gs.value("rootMountPoint")
    user = gs.value("username")
    if not root or not user:
        return ("DevOS provisioning", "Installer state is missing the target root or user name.")
    if "online" not in mode.split(","):
        libcalamares.utils.debug("DevOS: offline installation selected, skipping online provisioning")
        return None

    proc = subprocess.Popen(["/usr/lib/devos/calamares-provision", root, user],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        libcalamares.utils.debug(line)
        if "== [" in line or "== stage " in line:
            stage = line.split("== ")[-1].strip("[] ").split("]")[0].replace("stage ", "")
            if stage in STAGES:
                libcalamares.job.setprogress(STAGES.index(stage) / len(STAGES))
            _status = line.split("== ", 1)[-1]
    rc = proc.wait()
    libcalamares.job.setprogress(1.0)
    if rc == 3:
        libcalamares.utils.warning("DevOS: no internet during installation; online tools deferred to first login")
    elif rc != 0:
        libcalamares.utils.warning(f"DevOS: provisioning finished with failures (exit {rc}); "
                                   "see /var/log/devos/provision.log on the installed system")
    return None
