"""Low-level helpers for provisioning: HTTP, checksums, archives, apt, processes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

from .. import paths

log = logging.getLogger("devos.provision")

USER_AGENT = "DevOS-Provisioner/1.0 (+https://github.com/devos)"
CACHE_DIR = Path(os.environ.get("DEVOS_DOWNLOAD_CACHE", "/var/cache/devos/downloads"))


class ProvisionError(RuntimeError):
    pass


# --------------------------------------------------------------------- HTTP
def _request(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", USER_AGENT)
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return req


def fetch_bytes(url: str, retries: int = 4, headers: dict[str, str] | None = None) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url, headers=headers), timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                break
            time.sleep(2 ** attempt)
    raise ProvisionError(f"GET {url} failed: {last}")


def fetch_text(url: str, **kw) -> str:
    return fetch_bytes(url, **kw).decode("utf-8")


def fetch_json(url: str, **kw):
    return json.loads(fetch_bytes(url, headers={"Accept": "application/json"}, **kw))


def resolve_redirect(url: str) -> str:
    """Return the final URL after redirects (used for github .../releases/latest)."""
    with urllib.request.urlopen(_request(url, method="HEAD"), timeout=60) as resp:
        return resp.geturl()


def github_latest_tag(repo: str) -> str:
    final = resolve_redirect(f"https://github.com/{repo}/releases/latest")
    match = re.search(r"/releases/tag/([^/?#]+)$", final)
    if not match:
        raise ProvisionError(f"cannot determine latest release of {repo} (got {final})")
    return urllib.request.unquote(match.group(1))


def github_release_asset_digest(repo: str, tag: str, asset_name: str) -> str | None:
    """GitHub publishes a sha256 digest for every release asset in its API."""
    data = fetch_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    for asset in data.get("assets", []):
        if asset["name"] == asset_name and asset.get("digest", "").startswith("sha256:"):
            return asset["digest"].split(":", 1)[1]
    return None


def github_find_asset(repo: str, pattern: str, include_prereleases: bool = False) -> tuple[str, str, str, str]:
    """Newest release with an asset matching `pattern`; returns (tag, name, url, sha256 digest)."""
    releases = fetch_json(f"https://api.github.com/repos/{repo}/releases?per_page=20")
    for rel in releases:
        if rel.get("draft") or (rel.get("prerelease") and not include_prereleases):
            continue
        for asset in rel.get("assets", []):
            if re.fullmatch(pattern, asset["name"]) and asset.get("digest", "").startswith("sha256:"):
                return rel["tag_name"], asset["name"], asset["browser_download_url"], asset["digest"].split(":", 1)[1]
    raise ProvisionError(f"no release asset matching {pattern} in {repo}")


def checksum_from_sums(text: str, filename: str) -> str:
    """Pick a digest for `filename` out of a SHA256SUMS-style file."""
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*").split("/")[-1] == filename:
            return parts[0].lower()
    # single-digest files (e.g. foo.sha256 containing only the hash)
    stripped = text.strip().split()
    if len(stripped) == 1 and re.fullmatch(r"[0-9a-fA-F]{40,128}", stripped[0]):
        return stripped[0].lower()
    if len(stripped) == 2 and re.fullmatch(r"[0-9a-fA-F]{40,128}", stripped[0]):
        return stripped[0].lower()
    raise ProvisionError(f"no checksum for {filename} in checksum file")


def download(url: str, *, sha256: str | None = None, sha1: str | None = None,
             sha512: str | None = None, name: str | None = None, allow_unverified: bool = False) -> Path:
    """Download into the cache, verifying the vendor-published digest.

    Every download must be verified unless the vendor publishes no digest at all,
    in which case the caller must pass allow_unverified=True explicitly (HTTPS only).
    """
    if not url.startswith("https://"):
        raise ProvisionError(f"refusing non-HTTPS download: {url}")
    expected = [(h, v) for h, v in (("sha256", sha256), ("sha1", sha1), ("sha512", sha512)) if v]
    if not expected and not allow_unverified:
        raise ProvisionError(f"no checksum available for {url}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / (name or url.rstrip("/").split("/")[-1].split("?")[0])
    if dest.exists() and expected and _verify(dest, expected):
        log.info("cached %s", dest.name)
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    last: Exception | None = None
    for attempt in range(4):
        try:
            log.info("downloading %s", url)
            with urllib.request.urlopen(_request(url), timeout=120) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                done, next_report = 0, 0.0
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    if total and done / total >= next_report:
                        log.info("  %s: %d%% of %.1f MB", dest.name, 100 * done // total, total / 1e6)
                        next_report += 0.25
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    else:
        raise ProvisionError(f"download failed: {url}: {last}")
    if expected and not _verify(tmp, expected):
        tmp.unlink(missing_ok=True)
        raise ProvisionError(f"checksum mismatch for {url}")
    tmp.replace(dest)
    return dest


def _verify(path: Path, expected: list[tuple[str, str]]) -> bool:
    for algo, digest in expected:
        h = hashlib.new(algo)
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() != digest.lower():
            log.error("%s mismatch for %s: expected %s got %s", algo, path.name, digest, h.hexdigest())
            return False
    return True


# ------------------------------------------------------------------ archives
def extract(archive: Path, dest: Path, strip_components: int = 0) -> None:
    """Extract tar.* or zip into dest (replacing it), optionally stripping leading dirs."""
    staging = Path(tempfile.mkdtemp(prefix="devos-extract-", dir=str(dest.parent) if dest.parent.exists() else None))
    try:
        name = archive.name
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    target = staging / info.filename
                    if not str(target.resolve()).startswith(str(staging.resolve())):
                        raise ProvisionError(f"unsafe path in {name}: {info.filename}")
                    zf.extract(info, staging)
                    mode = (info.external_attr >> 16) & 0o777
                    if mode:
                        os.chmod(target, mode)
        elif name.endswith((".tar.zst", ".tzst")):
            subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(staging)], check=True)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(staging, filter="tar")
        root = staging
        for _ in range(strip_components):
            entries = [p for p in root.iterdir()]
            if len(entries) != 1 or not entries[0].is_dir():
                raise ProvisionError(f"cannot strip component from {name}: {[e.name for e in entries]}")
            root = entries[0]
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ----------------------------------------------------------------- processes
def run(cmd: list[str] | str, *, env: dict[str, str] | None = None, cwd: str | Path | None = None,
        user: str | None = None, check: bool = True, capture: bool = False, input_text: str | None = None,
        timeout: int | None = None) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = ["bash", "-c", cmd]
    full_env = dict(os.environ)
    full_env.update(provision_env())
    if env:
        full_env.update(env)
    if user and user != "root":
        # runuser keeps us inside the chroot and drops privileges cleanly
        keep = ",".join(k for k in (env or {}))
        prefix = ["runuser", "-u", user, "--"]
        if keep:
            prefix = ["runuser", f"--whitelist-environment={keep}", "-u", user, "--"]
        cmd = prefix + ["env", f"HOME={home_of(user)}", f"PATH={full_env['PATH']}"] + cmd
    log.info("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, env=full_env, cwd=cwd, text=True, input=input_text, timeout=timeout,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None)
    if check and result.returncode != 0:
        out = f"\n{result.stdout}" if capture and result.stdout else ""
        raise ProvisionError(f"command failed ({result.returncode}): {' '.join(cmd)}{out}")
    return result


def home_of(user: str) -> str:
    import pwd
    return pwd.getpwnam(user).pw_dir


def provision_env() -> dict[str, str]:
    """PATH and SDK variables identical to /etc/profile.d/devos-env.sh."""
    path_parts = [
        str(paths.NPM_PREFIX / "bin"),
        "/opt/node/bin",
        str(paths.FLUTTER_HOME / "bin"),
        str(paths.ANDROID_HOME / "cmdline-tools/latest/bin"),
        str(paths.ANDROID_HOME / "platform-tools"),
        str(paths.ANDROID_HOME / "emulator"),
        "/usr/local/go/bin",
        "/opt/rust/cargo/bin",
        "/opt/dotnet",
        "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
    ]
    return {
        "PATH": ":".join(path_parts),
        "ANDROID_HOME": str(paths.ANDROID_HOME),
        "ANDROID_SDK_ROOT": str(paths.ANDROID_HOME),
        "JAVA_HOME": "/usr/lib/jvm/default-java",
        "RUSTUP_HOME": "/opt/rust/rustup",
        "CARGO_HOME": "/opt/rust/cargo",
        "DOTNET_ROOT": "/opt/dotnet",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DEBIAN_FRONTEND": "noninteractive",
        "UV_TOOL_DIR": str(paths.UV_TOOL_DIR),
        "UV_TOOL_BIN_DIR": str(paths.BIN_DIR),
        "UV_PYTHON_INSTALL_DIR": str(paths.UV_PYTHON_DIR),
        "npm_config_prefix": str(paths.NPM_PREFIX),
        "npm_config_update_notifier": "false",
        "npm_config_fund": "false",
    }


# ---------------------------------------------------------------------- apt
def apt_update() -> None:
    run(["apt-get", "update"])


def apt_install(packages: Iterable[str]) -> None:
    pkgs = list(packages)
    if not pkgs:
        return
    run(["apt-get", "install", "-y", "--no-install-recommends", "-o", "Dpkg::Options::=--force-confdef",
         "-o", "Dpkg::Options::=--force-confold", *pkgs])


def dpkg_installed(package: str) -> bool:
    res = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True)
    return res.returncode == 0 and "install ok installed" in res.stdout


def add_apt_repo(name: str, repo: dict) -> None:
    """Install a vendor's signing key (dearmored) and a deb822 source pinned to it."""
    keyring = paths.KEYRINGS_DIR / f"devos-{name}.gpg"
    key = fetch_bytes(repo["key_url"])
    if key.lstrip().startswith(b"-----BEGIN PGP"):
        res = subprocess.run(["gpg", "--dearmor"], input=key, capture_output=True, check=True)
        key = res.stdout
    keyring.parent.mkdir(parents=True, exist_ok=True)
    keyring.write_bytes(key)
    keyring.chmod(0o644)
    lines = [
        "Types: deb",
        f"URIs: {repo['url']}",
        f"Suites: {repo['suite']}",
    ]
    if repo.get("components"):
        lines.append(f"Components: {repo['components']}")
    if repo.get("arch"):
        lines.append(f"Architectures: {repo['arch'].replace(',', ' ')}")
    lines.append(f"Signed-By: {keyring}")
    list_name = repo.get("list_name", f"devos-{name}")
    # Remove legacy one-line lists the vendor package may have written for itself
    for legacy in (paths.SOURCES_DIR / f"{list_name}.list",):
        legacy.unlink(missing_ok=True)
    (paths.SOURCES_DIR / f"{list_name}.sources").write_text("\n".join(lines) + "\n")


def debconf_set(selections: Iterable[str]) -> None:
    text = "\n".join(selections) + "\n"
    run(["debconf-set-selections"], input_text=text)


# --------------------------------------------------------------- filesystem
def link_bins(src_dir: Path, names: Iterable[str]) -> None:
    for n in names:
        src = src_dir / n
        if not src.exists():
            raise ProvisionError(f"expected binary missing: {src}")
        dst = paths.BIN_DIR / n
        dst.unlink(missing_ok=True)
        dst.symlink_to(src)


def make_group_writable(path: Path) -> None:
    """Shared SDKs (Android, Flutter, Rust) must be updatable by developers."""
    ensure_group(paths.DEV_GROUP)
    run(["chgrp", "-R", paths.DEV_GROUP, str(path)])
    run(["chmod", "-R", "g+rwX", str(path)])
    run(["find", str(path), "-type", "d", "-exec", "chmod", "g+s", "{}", "+"])
    run(["setfacl", "-R", "-d", "-m", f"g:{paths.DEV_GROUP}:rwX", str(path)])


def write_desktop_entry(file_id: str, *, name: str, exec_: str, icon: str, comment: str = "",
                        categories: str = "Development;IDE;", wm_class: str | None = None,
                        extra: dict[str, str] | None = None) -> Path:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        f"Name={name}",
        f"Comment={comment}",
        f"Exec={exec_}",
        f"TryExec={exec_.split()[0]}",
        f"Icon={icon}",
        "Terminal=false",
        f"Categories={categories}",
    ]
    if wm_class:
        lines.append(f"StartupWMClass={wm_class}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}={v}")
    path = paths.APPS_DIR / file_id
    path.write_text("\n".join(lines) + "\n")
    return path


def ensure_group(name: str) -> None:
    if subprocess.run(["getent", "group", name], capture_output=True).returncode != 0:
        run(["groupadd", "--system", name])
