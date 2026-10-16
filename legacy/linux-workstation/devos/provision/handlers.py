"""Installers for tools that are not plain Debian packages.

Every handler resolves the *current* stable release from the vendor's official
endpoint at provisioning time (no stale pinned versions) and verifies the
vendor-published checksum before installing.

A handler is a function `install(tool, ctx)`; a matching `resolve(tool, ctx)`
returns the artifact(s) it would download so `devos-provision --resolve-only`
can validate every source without installing anything.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import paths
from .util import (ProvisionError, add_apt_repo, apt_install, checksum_from_sums, download, extract,
                   fetch_json, fetch_text, github_find_asset, github_latest_tag, github_release_asset_digest, link_bins,
                   make_group_writable, run, write_desktop_entry)

log = logging.getLogger("devos.provision")


@dataclass
class Context:
    target_user: str | None = None
    accept_licenses: bool = False
    offline_bundle: bool = False
    accepted: list[str] = field(default_factory=list)


@dataclass
class Artifact:
    url: str
    sha256: str | None = None
    sha1: str | None = None
    sha512: str | None = None
    version: str = ""
    name: str | None = None
    unverified: bool = False

    def fetch(self) -> Path:
        return download(self.url, sha256=self.sha256, sha1=self.sha1, sha512=self.sha512,
                        name=self.name, allow_unverified=self.unverified)


RESOLVERS: dict[str, Callable] = {}
INSTALLERS: dict[str, Callable] = {}


def resolver(name):
    def deco(fn):
        RESOLVERS[name] = fn
        return fn
    return deco


def installer(name):
    def deco(fn):
        INSTALLERS[name] = fn
        return fn
    return deco


def _opt_install(art: Artifact, dest: Path, strip: int = 1) -> Path:
    archive = art.fetch()
    extract(archive, dest, strip_components=strip)
    (dest / ".devos-version").write_text(art.version + "\n")
    return dest


# =================================================================== Node.js
@resolver("node")
def resolve_node(tool, ctx):
    releases = fetch_json("https://nodejs.org/dist/index.json")
    lts = next(r for r in releases if r.get("lts"))
    ver = lts["version"]
    fname = f"node-{ver}-linux-x64.tar.xz"
    sums = fetch_text(f"https://nodejs.org/dist/{ver}/SHASUMS256.txt")
    return [Artifact(f"https://nodejs.org/dist/{ver}/{fname}", sha256=checksum_from_sums(sums, fname), version=ver)]


@installer("node")
def install_node(tool, ctx):
    art = resolve_node(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/node"))
    link_bins(dest / "bin", ["node", "npm", "npx"])
    # User-level `npm i -g` goes to ~/.local (on PATH) instead of the root-owned prefix
    (dest / "etc").mkdir(exist_ok=True)
    (dest / "etc" / "npmrc").write_text("prefix=${HOME}/.local\nupdate-notifier=false\nfund=false\n")
    paths.NPM_PREFIX.mkdir(parents=True, exist_ok=True)


# ======================================================================= Bun
@resolver("bun")
def resolve_bun(tool, ctx):
    tag = github_latest_tag("oven-sh/bun")
    base = f"https://github.com/oven-sh/bun/releases/download/{tag}"
    sums = fetch_text(f"{base}/SHASUMS256.txt")
    return [Artifact(f"{base}/bun-linux-x64.zip", sha256=checksum_from_sums(sums, "bun-linux-x64.zip"),
                     version=tag, name=f"bun-{tag.replace('/', '-')}-linux-x64.zip")]


@installer("bun")
def install_bun(tool, ctx):
    art = resolve_bun(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/bun"))
    (dest / "bunx").unlink(missing_ok=True)
    (dest / "bunx").symlink_to(dest / "bun")
    link_bins(dest, ["bun", "bunx"])


# ====================================================================== Deno
@resolver("deno")
def resolve_deno(tool, ctx):
    tag = github_latest_tag("denoland/deno")
    base = f"https://github.com/denoland/deno/releases/download/{tag}"
    fname = "deno-x86_64-unknown-linux-gnu.zip"
    sha = checksum_from_sums(fetch_text(f"{base}/{fname}.sha256sum"), fname)
    return [Artifact(f"{base}/{fname}", sha256=sha, version=tag, name=f"deno-{tag}-linux.zip")]


@installer("deno")
def install_deno(tool, ctx):
    art = resolve_deno(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/deno"), strip=0)
    link_bins(dest, ["deno"])


# ======================================================================== uv
@resolver("uv")
def resolve_uv(tool, ctx):
    tag = github_latest_tag("astral-sh/uv")
    base = f"https://github.com/astral-sh/uv/releases/download/{tag}"
    fname = "uv-x86_64-unknown-linux-gnu.tar.gz"
    sha = checksum_from_sums(fetch_text(f"{base}/{fname}.sha256"), fname)
    return [Artifact(f"{base}/{fname}", sha256=sha, version=tag, name=f"uv-{tag}-linux.tar.gz")]


@installer("uv")
def install_uv(tool, ctx):
    art = resolve_uv(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/uv"))
    link_bins(dest, ["uv", "uvx"])


# ======================================================================== Go
@resolver("go")
def resolve_go(tool, ctx):
    releases = fetch_json("https://go.dev/dl/?mode=json")
    rel = next(r for r in releases if r["stable"])
    f = next(f for f in rel["files"] if f["os"] == "linux" and f["arch"] == "amd64" and f["kind"] == "archive")
    return [Artifact(f"https://go.dev/dl/{f['filename']}", sha256=f["sha256"], version=rel["version"])]


@installer("go")
def install_go(tool, ctx):
    art = resolve_go(tool, ctx)[0]
    _opt_install(art, Path("/usr/local/go"))
    gobin = {"GOBIN": "/usr/local/bin", "GOPATH": "/var/cache/devos/gopath", "GOCACHE": "/var/cache/devos/gocache"}
    run(["/usr/local/go/bin/go", "install", "golang.org/x/tools/gopls@latest"], env=gobin)
    run(["/usr/local/go/bin/go", "install", "github.com/go-delve/delve/cmd/dlv@latest"], env=gobin)
    link_bins(Path("/usr/local/go/bin"), ["go", "gofmt"])


# ====================================================================== Rust
@resolver("rust")
def resolve_rust(tool, ctx):
    base = "https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu"
    sha = checksum_from_sums(fetch_text(f"{base}/rustup-init.sha256"), "rustup-init")
    return [Artifact(f"{base}/rustup-init", sha256=sha, version="stable")]


@installer("rust")
def install_rust(tool, ctx):
    art = resolve_rust(tool, ctx)[0]
    init = art.fetch()
    init.chmod(0o755)
    env = {"RUSTUP_HOME": "/opt/rust/rustup", "CARGO_HOME": "/opt/rust/cargo"}
    run([str(init), "-y", "--no-modify-path", "--profile", "default", "--default-toolchain", "stable",
         "-c", "rust-analyzer", "-c", "rust-src"], env=env)
    make_group_writable(Path("/opt/rust"))


# ===================================================================== .NET
@resolver("dotnet")
def resolve_dotnet(tool, ctx):
    index = fetch_json("https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json")
    arts = []
    for rel in index["releases-index"]:
        if rel["support-phase"] != "active":
            continue
        meta = fetch_json(rel["releases.json"])
        latest = meta["releases"][0]
        sdk = latest["sdk"]
        f = next(f for f in sdk["files"] if f["rid"] == "linux-x64" and f["name"].endswith(".tar.gz"))
        arts.append(Artifact(f["url"], sha512=f["hash"], version=sdk["version"],
                             name=f"dotnet-sdk-{sdk['version']}-linux-x64.tar.gz"))
    if not arts:
        raise ProvisionError("no active .NET release found")
    return arts


@installer("dotnet")
def install_dotnet(tool, ctx):
    dest = Path("/opt/dotnet")
    dest.mkdir(parents=True, exist_ok=True)
    versions = []
    # Several SDK archives share one root; extract each over the same tree.
    for art in resolve_dotnet(tool, ctx):
        archive = art.fetch()
        run(["tar", "-xzf", str(archive), "-C", str(dest)])
        versions.append(art.version)
    (dest / ".devos-version").write_text(" ".join(versions) + "\n")
    link_bins(dest, ["dotnet"])


@resolver("netcoredbg")
def resolve_netcoredbg(tool, ctx):
    tag = github_latest_tag("Samsung/netcoredbg")
    fname = "netcoredbg-linux-amd64.tar.gz"
    sha = github_release_asset_digest("Samsung/netcoredbg", tag, fname)
    return [Artifact(f"https://github.com/Samsung/netcoredbg/releases/download/{tag}/{fname}",
                     sha256=sha, version=tag, name=f"netcoredbg-{tag}.tar.gz")]


@installer("netcoredbg")
def install_netcoredbg(tool, ctx):
    art = resolve_netcoredbg(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/netcoredbg"))
    link_bins(dest, ["netcoredbg"])


# ================================================================ Java extra
@resolver("temurin")
def resolve_temurin(tool, ctx):
    arts = []
    for major in tool.install.get("versions", [17, 25]):
        data = fetch_json(f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
                          "?architecture=x64&image_type=jdk&os=linux&vendor=eclipse")
        pkg = data[0]["binary"]["package"]
        arts.append(Artifact(pkg["link"], sha256=pkg["checksum"], version=str(major), name=pkg["name"]))
    return arts


@installer("temurin")
def install_temurin(tool, ctx):
    for art in resolve_temurin(tool, ctx):
        _opt_install(art, Path(f"/usr/lib/jvm/temurin-{art.version}"))


@resolver("gradle")
def resolve_gradle(tool, ctx):
    cur = fetch_json("https://services.gradle.org/versions/current")
    sha = fetch_text(cur["checksumUrl"]).strip()
    return [Artifact(cur["downloadUrl"], sha256=sha, version=cur["version"])]


@installer("gradle")
def install_gradle(tool, ctx):
    art = resolve_gradle(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/gradle"))
    link_bins(dest / "bin", ["gradle"])


@resolver("kotlin")
def resolve_kotlin(tool, ctx):
    tag = github_latest_tag("JetBrains/kotlin")
    ver = tag.lstrip("v")
    fname = f"kotlin-compiler-{ver}.zip"
    base = f"https://github.com/JetBrains/kotlin/releases/download/{tag}"
    sha = checksum_from_sums(fetch_text(f"{base}/{fname}.sha256"), fname)
    return [Artifact(f"{base}/{fname}", sha256=sha, version=ver)]


@installer("kotlin")
def install_kotlin(tool, ctx):
    art = resolve_kotlin(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/kotlinc"))
    link_bins(dest / "bin", ["kotlinc", "kotlin", "kotlinc-jvm"])


# =================================================================== Flutter
@resolver("flutter")
def resolve_flutter(tool, ctx):
    data = fetch_json("https://storage.googleapis.com/flutter_infra_release/releases/releases_linux.json")
    stable_hash = data["current_release"]["stable"]
    rel = next(r for r in data["releases"] if r["hash"] == stable_hash)
    return [Artifact(f"{data['base_url']}/{rel['archive']}", sha256=rel["sha256"], version=rel["version"])]


@installer("flutter")
def install_flutter(tool, ctx):
    art = resolve_flutter(tool, ctx)[0]
    dest = _opt_install(art, paths.FLUTTER_HOME)
    run(["git", "config", "--system", "--add", "safe.directory", str(dest)])
    env = {"FLUTTER_SUPPRESS_ANALYTICS": "true", "PUB_CACHE": "/var/cache/devos/pub-cache"}
    run([str(dest / "bin/flutter"), "--disable-analytics"], env=env, check=False)
    run([str(dest / "bin/flutter"), "precache", "--linux", "--web", "--android"], env=env)
    make_group_writable(dest)


# =================================================================== Android
REPO_XML = "https://dl.google.com/android/repository/repository2-3.xml"
SDK_BASE = "https://dl.google.com/android/repository/"


@resolver("android_sdk")
def resolve_android_sdk(tool, ctx):
    root = ET.fromstring(fetch_text(REPO_XML))
    for pkg in root.iter("remotePackage"):
        if pkg.get("path") != "cmdline-tools;latest":
            continue
        for archive in pkg.iter("archive"):
            if archive.findtext("host-os") == "linux":
                comp = archive.find("complete")
                url = comp.findtext("url")
                rev = pkg.find("revision")
                ver = ".".join(x.text for x in rev if x.text)
                return [Artifact(SDK_BASE + url, sha1=comp.findtext("checksum"), version=ver)]
    raise ProvisionError("cmdline-tools;latest not found in Android repository")


def _sdkmanager(*args: str, input_text: str | None = None, capture: bool = False):
    cmd = [str(paths.ANDROID_HOME / "cmdline-tools/latest/bin/sdkmanager"), f"--sdk_root={paths.ANDROID_HOME}", *args]
    return run(cmd, input_text=input_text, capture=capture)


def _require_android_license(ctx: Context) -> None:
    if not ctx.accept_licenses or "android-sdk" not in ctx.accepted:
        raise ProvisionError("The Android SDK License Agreement has not been accepted by the user "
                             "(accept it in the installer or in Developer Center)")


def _latest_stable(listing: str, prefix: str, pattern: str) -> str | None:
    best, best_key = None, None
    for line in listing.splitlines():
        name = line.split("|")[0].strip()
        if not name.startswith(prefix):
            continue
        m = re.fullmatch(pattern, name[len(prefix):])
        if not m:
            continue
        key = tuple(int(x) for x in m.groups())
        if best_key is None or key > best_key:
            best, best_key = name, key
    return best


@installer("android_sdk")
def install_android_sdk(tool, ctx):
    _require_android_license(ctx)
    art = resolve_android_sdk(tool, ctx)[0]
    paths.ANDROID_HOME.mkdir(parents=True, exist_ok=True)
    _opt_install(art, paths.ANDROID_HOME / "cmdline-tools" / "latest")
    # The user accepted the SDK license in the installer / Developer Center; record it in the SDK.
    _sdkmanager("--licenses", input_text="y\n" * 50)
    listing = _sdkmanager("--list", capture=True).stdout
    platform = _latest_stable(listing, "platforms;android-", r"(\d+)")
    build_tools = _latest_stable(listing, "build-tools;", r"(\d+)\.(\d+)\.(\d+)")
    ndk = _latest_stable(listing, "ndk;", r"(\d+)\.(\d+)\.(\d+)")
    cmake = _latest_stable(listing, "cmake;", r"(\d+)\.(\d+)\.(\d+)")
    wanted = ["platform-tools", "emulator", *(p for p in (platform, build_tools, ndk, cmake) if p)]
    log.info("Android SDK packages: %s", ", ".join(wanted))
    _sdkmanager("--install", *wanted, input_text="y\n" * 20)
    make_group_writable(paths.ANDROID_HOME)
    run(["ln", "-sfn", str(paths.ANDROID_HOME / "cmdline-tools/latest/bin/sdkmanager"), "/usr/local/bin/sdkmanager"])
    run(["ln", "-sfn", str(paths.ANDROID_HOME / "cmdline-tools/latest/bin/avdmanager"), "/usr/local/bin/avdmanager"])
    for b in ("adb", "fastboot"):
        run(["ln", "-sfn", str(paths.ANDROID_HOME / "platform-tools" / b), f"/usr/local/bin/{b}"])
    run(["ln", "-sfn", str(paths.ANDROID_HOME / "emulator/emulator"), "/usr/local/bin/emulator"])


@installer("android_system_image")
def install_android_system_image(tool, ctx):
    _require_android_license(ctx)
    listing = _sdkmanager("--list", capture=True).stdout
    image = None
    for api in sorted({int(m) for m in re.findall(r"system-images;android-(\d+);google_apis;x86_64", listing)},
                      reverse=True):
        image = f"system-images;android-{api};google_apis;x86_64"
        break
    if not image:
        raise ProvisionError("no google_apis x86_64 system image available")
    _sdkmanager("--install", image, input_text="y\n" * 10)
    make_group_writable(paths.ANDROID_HOME)
    if ctx.target_user:
        api = image.split(";")[1].split("-")[1]
        avdmanager = str(paths.ANDROID_HOME / "cmdline-tools/latest/bin/avdmanager")
        devices = run([avdmanager, "list", "device", "-c"], capture=True, user=ctx.target_user).stdout.split()
        device = next((d for d in ("pixel_8", "pixel_7", "pixel_6", "pixel") if d in devices), devices[0] if devices else None)
        cmd = [avdmanager, "create", "avd", "--force", "-n", f"DevOS_API_{api}", "-k", image]
        if device:
            cmd += ["-d", device]
        run(cmd, user=ctx.target_user, input_text="no\n")


def _android_studio_release() -> dict:
    data = fetch_json("https://jb.gg/android-studio-releases-list.json")
    items = data["content"]["item"]
    return next(i for i in items if i.get("channel") == "Release")


@resolver("android_studio")
def resolve_android_studio(tool, ctx):
    item = _android_studio_release()
    dl = next(d for d in item["download"] if d["link"].endswith("-linux.tar.gz"))
    return [Artifact(dl["link"], sha256=dl["checksum"], version=item["version"])]


@installer("android_studio")
def install_android_studio(tool, ctx):
    art = resolve_android_studio(tool, ctx)[0]
    dest = _opt_install(art, paths.JETBRAINS_DIR / "android-studio")
    run(["ln", "-sfn", str(dest / "bin/studio.sh"), "/usr/local/bin/android-studio"])
    write_desktop_entry("devos-android-studio.desktop", name="Android Studio",
                        exec_=f"{dest}/bin/studio.sh %f", icon=str(dest / "bin/studio.svg"),
                        comment="Official IDE for Android development", wm_class="jetbrains-studio")


# ================================================================= JetBrains
JB_ICONS = {"TBA": "toolbox", "IIU": "idea", "IIC": "idea", "PCP": "pycharm", "PCC": "pycharm",
            "WS": "webstorm", "PS": "phpstorm", "CL": "clion", "GO": "goland", "RD": "rider",
            "DG": "datagrip", "RR": "rustrover"}
JB_NAMES = {"TBA": "JetBrains Toolbox", "IIU": "IntelliJ IDEA", "IIC": "IntelliJ IDEA Community",
            "PCP": "PyCharm", "PCC": "PyCharm Community", "WS": "WebStorm", "PS": "PhpStorm", "CL": "CLion",
            "GO": "GoLand", "RD": "Rider", "DG": "DataGrip", "RR": "RustRover"}


def _jb_release(code: str) -> dict | None:
    data = fetch_json(f"https://data.services.jetbrains.com/products/releases?code={code}&latest=true&type=release")
    rel = (data.get(code) or [None])[0]
    if not rel or "linux" not in rel.get("downloads", {}):
        return None
    return rel


@resolver("jetbrains")
def resolve_jetbrains(tool, ctx):
    for code in (tool.install["product"], tool.install.get("fallback_product")):
        if not code:
            continue
        rel = _jb_release(code)
        if rel:
            dl = rel["downloads"]["linux"]
            sha = checksum_from_sums(fetch_text(dl["checksumLink"]), dl["link"].split("/")[-1])
            return [Artifact(dl["link"], sha256=sha, version=f"{code}-{rel['version']}")]
    raise ProvisionError(f"no Linux release for {tool.install['product']}")


@installer("jetbrains")
def install_jetbrains(tool, ctx):
    art = resolve_jetbrains(tool, ctx)[0]
    code = art.version.split("-")[0]
    dirname = tool.install["dir"]
    dest = _opt_install(art, paths.JETBRAINS_DIR / dirname)
    if code == "TBA":
        launcher = dest / "bin" / "jetbrains-toolbox"
        if not launcher.exists():  # Toolbox 1.x layout
            (dest / "bin").mkdir(exist_ok=True)
            launcher.symlink_to(dest / "jetbrains-toolbox")
        icon = "jetbrains-toolbox"
        for candidate in (dest / "bin" / "toolbox-tray-color.png", dest / "toolbox.svg"):
            if candidate.exists():
                icon = str(candidate)
        write_desktop_entry("devos-jetbrains-toolbox.desktop", name="JetBrains Toolbox", exec_=str(launcher),
                            icon=icon, comment="Manage JetBrains IDEs and sign in to your JetBrains account",
                            categories="Development;")
        return
    info = json.loads((dest / "product-info.json").read_text())
    launch = info["launch"][0]
    exe = dest / launch["launcherPath"]
    icon = dest / info.get("svgIconPath", f"bin/{JB_ICONS[code]}.svg")
    run(["ln", "-sfn", str(exe), f"/usr/local/bin/{dirname}"])
    write_desktop_entry(f"devos-{dirname}.desktop", name=info.get("name", JB_NAMES[code]), exec_=f"{exe} %f",
                        icon=str(icon), comment=f"JetBrains {JB_NAMES[code]}",
                        wm_class=launch.get("startupWmClass"))


# =================================================================== Ollama
@resolver("ollama")
def resolve_ollama(tool, ctx):
    tag = github_latest_tag("ollama/ollama")
    base = f"https://github.com/ollama/ollama/releases/download/{tag}"
    sums = fetch_text(f"{base}/sha256sum.txt")
    for fname in ("ollama-linux-amd64.tar.zst", "ollama-linux-amd64.tgz"):
        try:
            return [Artifact(f"{base}/{fname}", sha256=checksum_from_sums(sums, fname), version=tag,
                             name=f"ollama-{tag}-{fname.split('ollama-')[1]}")]
        except ProvisionError:
            continue
    raise ProvisionError("no Linux amd64 Ollama archive in latest release")


@installer("ollama")
def install_ollama(tool, ctx):
    art = resolve_ollama(tool, ctx)[0]
    archive = art.fetch()
    flag = "--zstd" if archive.name.endswith(".zst") else "-z"
    shutil.rmtree("/usr/local/lib/ollama", ignore_errors=True)
    run(["tar", flag, "-xf", str(archive), "-C", "/usr/local"])
    if subprocess.run(["getent", "passwd", "ollama"], capture_output=True).returncode != 0:
        run(["useradd", "-r", "-s", "/bin/false", "-U", "-m", "-d", "/usr/share/ollama", "ollama"])
    Path("/etc/systemd/system/ollama.service").write_text(
        "[Unit]\nDescription=Ollama local model server\nAfter=network-online.target\n\n"
        "[Service]\nExecStart=/usr/local/bin/ollama serve\nUser=ollama\nGroup=ollama\n"
        "Restart=always\nRestartSec=3\nEnvironment=OLLAMA_HOST=127.0.0.1:11434\n\n"
        "[Install]\nWantedBy=multi-user.target\n")
    run(["systemctl", "enable", "ollama.service"], check=False)


@resolver("llama_cpp")
def resolve_llama_cpp(tool, ctx):
    # llama.cpp publishes its CPU builds as rolling "bNNNN" releases
    tag, name, url, sha = github_find_asset("ggml-org/llama.cpp", r"llama-b\d+-bin-ubuntu-x64\.(tar\.gz|zip)",
                                            include_prereleases=True)
    return [Artifact(url, sha256=sha, version=tag, name=name)]


@installer("llama_cpp")
def install_llama_cpp(tool, ctx):
    art = resolve_llama_cpp(tool, ctx)[0]
    archive = art.fetch()
    dest = Path("/opt/llama.cpp")
    extract(archive, dest, strip_components=0)
    bindir = next((p.parent for p in dest.rglob("llama-cli") if p.is_file()), None)
    if not bindir:
        raise ProvisionError("llama-cli not found in llama.cpp archive")
    (dest / ".devos-version").write_text(art.version + "\n")
    Path("/etc/ld.so.conf.d/devos-llama-cpp.conf").write_text(f"{bindir}\n")
    run(["ldconfig"])
    link_bins(bindir, [p.name for p in bindir.iterdir() if p.name.startswith("llama-") and os.access(p, os.X_OK)])


# =================================================================== DevOps
@resolver("kubectl")
def resolve_kubectl(tool, ctx):
    ver = fetch_text("https://dl.k8s.io/release/stable.txt").strip()
    url = f"https://dl.k8s.io/release/{ver}/bin/linux/amd64/kubectl"
    return [Artifact(url, sha256=fetch_text(url + ".sha256").strip(), version=ver, name=f"kubectl-{ver}")]


def _install_single_binary(art: Artifact, name: str) -> None:
    path = art.fetch()
    dest = paths.BIN_DIR / name
    shutil.copyfile(path, dest)
    dest.chmod(0o755)


@installer("kubectl")
def install_kubectl(tool, ctx):
    _install_single_binary(resolve_kubectl(tool, ctx)[0], "kubectl")


@resolver("helm")
def resolve_helm(tool, ctx):
    tag = github_latest_tag("helm/helm")
    fname = f"helm-{tag}-linux-amd64.tar.gz"
    sha = checksum_from_sums(fetch_text(f"https://get.helm.sh/{fname}.sha256sum"), fname)
    return [Artifact(f"https://get.helm.sh/{fname}", sha256=sha, version=tag)]


@installer("helm")
def install_helm(tool, ctx):
    art = resolve_helm(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/helm"))
    link_bins(dest, ["helm"])


@resolver("kind")
def resolve_kind(tool, ctx):
    tag = github_latest_tag("kubernetes-sigs/kind")
    base = f"https://github.com/kubernetes-sigs/kind/releases/download/{tag}"
    sha = checksum_from_sums(fetch_text(f"{base}/kind-linux-amd64.sha256sum"), "kind-linux-amd64")
    return [Artifact(f"{base}/kind-linux-amd64", sha256=sha, version=tag, name=f"kind-{tag}")]


@installer("kind")
def install_kind(tool, ctx):
    _install_single_binary(resolve_kind(tool, ctx)[0], "kind")


@resolver("minikube")
def resolve_minikube(tool, ctx):
    url = "https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64"
    return [Artifact(url, sha256=fetch_text(url + ".sha256").strip(), version="latest", name="minikube-linux-amd64")]


@installer("minikube")
def install_minikube(tool, ctx):
    _install_single_binary(resolve_minikube(tool, ctx)[0], "minikube")


@resolver("k9s")
def resolve_k9s(tool, ctx):
    tag = github_latest_tag("derailed/k9s")
    base = f"https://github.com/derailed/k9s/releases/download/{tag}"
    fname = "k9s_Linux_amd64.tar.gz"
    sha = checksum_from_sums(fetch_text(f"{base}/checksums.sha256"), fname)
    return [Artifact(f"{base}/{fname}", sha256=sha, version=tag, name=f"k9s-{tag}.tar.gz")]


@installer("k9s")
def install_k9s(tool, ctx):
    art = resolve_k9s(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/k9s"), strip=0)
    link_bins(dest, ["k9s"])


@resolver("opentofu")
def resolve_opentofu(tool, ctx):
    tag = github_latest_tag("opentofu/opentofu")
    ver = tag.lstrip("v")
    base = f"https://github.com/opentofu/opentofu/releases/download/{tag}"
    fname = f"tofu_{ver}_linux_amd64.tar.gz"
    sha = checksum_from_sums(fetch_text(f"{base}/tofu_{ver}_SHA256SUMS"), fname)
    return [Artifact(f"{base}/{fname}", sha256=sha, version=ver)]


@installer("opentofu")
def install_opentofu(tool, ctx):
    art = resolve_opentofu(tool, ctx)[0]
    dest = _opt_install(art, Path("/opt/opentofu"), strip=0)
    link_bins(dest, ["tofu"])


# ==================================================================== Cloud
@resolver("aws_cli")
def resolve_aws_cli(tool, ctx):
    # AWS publishes a PGP signature (.sig) rather than a hash; verify it with gpg on install.
    return [Artifact("https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip", version="v2", unverified=True)]


AWS_CLI_KEY_FPR = "FB5DB77FD5C118B80511ADA8A6310ACC4672475C"


@installer("aws_cli")
def install_aws_cli(tool, ctx):
    art = resolve_aws_cli(tool, ctx)[0]
    archive = art.fetch()
    sig = download(art.url + ".sig", name="awscli-exe-linux-x86_64.zip.sig", allow_unverified=True)
    gnupg = Path("/var/cache/devos/gnupg-aws")
    gnupg.mkdir(parents=True, exist_ok=True)
    gnupg.chmod(0o700)
    keyfile = Path(__file__).resolve().parent / "keys" / "aws-cli.asc"
    run(["gpg", "--homedir", str(gnupg), "--import", str(keyfile)])
    out = run(["gpg", "--homedir", str(gnupg), "--status-fd", "1", "--verify", str(sig), str(archive)],
              capture=True).stdout
    if f"VALIDSIG {AWS_CLI_KEY_FPR}" not in out:
        raise ProvisionError("AWS CLI signature is not from the AWS CLI release key")
    work = Path("/var/cache/devos/aws-cli")
    extract(archive, work, strip_components=1)
    run([str(work / "install"), "-i", "/opt/aws-cli", "-b", "/usr/local/bin", "--update"])


@resolver("supabase")
def resolve_supabase(tool, ctx):
    tag, name, url, sha = github_find_asset("supabase/cli", r"supabase_[\d.]+_linux_amd64\.deb")
    return [Artifact(url, sha256=sha, version=tag.lstrip("v"), name=name)]


@installer("supabase")
def install_supabase(tool, ctx):
    deb = resolve_supabase(tool, ctx)[0].fetch()
    apt_install([str(deb)])


# ====================================================== Editor AI extensions
VSCODE_EXTENSIONS = [
    "GitHub.copilot", "GitHub.copilot-chat", "anthropic.claude-code", "openai.chatgpt",
    "Google.geminicodeassist", "ms-python.python", "ms-python.debugpy", "ms-vscode.cpptools",
    "ms-dotnettools.csharp", "rust-lang.rust-analyzer", "golang.go", "Dart-Code.flutter",
    "vscjava.vscode-java-pack", "dbaeumer.vscode-eslint", "esbenp.prettier-vscode",
    "ms-azuretools.vscode-docker", "eamodio.gitlens",
]
VSCODIUM_EXTENSIONS = [
    "Continue.continue", "anthropic.claude-code", "Google.geminicodeassist", "ms-python.python",
    "rust-lang.rust-analyzer", "golang.go", "Dart-Code.flutter", "redhat.java", "llvm-vs-code-extensions.vscode-clangd",
    "dbaeumer.vscode-eslint", "esbenp.prettier-vscode", "muhammad-sammy.csharp",
]


@installer("editor_extensions")
def install_editor_extensions(tool, ctx):
    """Extensions live in the user's profile, so they are installed for the target user."""
    if not ctx.target_user:
        log.info("editor extensions are per-user; they will be installed at first login")
        return
    install_editor_extensions_for(ctx.target_user)


def install_editor_extensions_for(user: str | None) -> list[str]:
    failed = []
    for binary, extensions in (("code", VSCODE_EXTENSIONS), ("codium", VSCODIUM_EXTENSIONS)):
        if not shutil.which(binary):
            continue
        for ext in extensions:
            res = run([binary, "--install-extension", ext, "--force"], user=user, check=False, capture=True)
            if res.returncode != 0:
                failed.append(f"{binary}:{ext}")
    if failed:
        log.warning("extensions not available: %s", ", ".join(failed))
    return failed
