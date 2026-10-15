# DevOS architecture

## Base

* Debian 13 "trixie" amd64, `main contrib non-free non-free-firmware`, security + updates.
* KDE Plasma 6 (Wayland, X11 fallback), SDDM, PipeWire, NetworkManager, BlueZ, CUPS, Flatpak.
* Linux kernel + all Debian firmware packages (Intel/Realtek/MediaTek/Atheros/Broadcom Wi-Fi incl.
  Wi-Fi 6/6E/7 chips supported by the kernel), CPU microcode.
* Live ISO built with `live-build` (`iso-hybrid`, GRUB EFI + syslinux BIOS, zstd squashfs).
* Installer: Calamares 3.3 with Debian's job sequence, btrfs by default (`@`, `@home`, `@cache`,
  `@log`, `@snapshots`), optional LUKS, plus the DevOS `packagechooser@devosmode` page and the
  `devosprovision` Python job.

## Provisioning (installer step 5 → 23)

`provisioning/tools.yaml` lists every tool with its stage. The stage order implements the required
installation order:

| # | Stage | Done by |
|---|---|---|
| 1–4 | base OS, desktop, kernel/firmware, network drivers | squashfs (Calamares `unpackfs`) |
| 5 | development packages | Debian packages on the ISO (`stage: packages`, verified/repaired by devos-provision) |
| 6 | programming languages | Node, Bun, Deno, uv, Go, Rust, .NET, Temurin, Gradle, Kotlin |
| 7 | Android SDK + Studio | cmdline-tools → sdkmanager (latest stable platform, build-tools, NDK, CMake, emulator), system image + AVD for the user |
| 8 | Flutter | stable release + `precache --linux --web --android` |
| 9–11 | containers, virtualization, Wine/Bottles | Debian packages, WineHQ staging, Bottles Flatpak |
| 12 | browsers | Google Chrome (Firefox ESR, Chromium on ISO) |
| 13–14 | IDEs, JetBrains | VS Code, VSCodium, DBeaver; IDEA, PyCharm, WebStorm, PhpStorm, CLion, GoLand, Rider, DataGrip, RustRover, Toolbox |
| 15 | AI CLI tools | Claude Code, Codex, Gemini CLI, Ollama (no model), llama.cpp, Aider, editor AI extensions |
| 16 | cloud CLI | kubectl, Helm, kind, minikube, k9s, OpenTofu, AWS, Azure, gcloud, cloudflared/Wrangler, Supabase, Firebase |
| 17 | Git tooling | Git, LFS, gh, glab, SSH |
| 18 | environment variables | `/etc/profile.d/devos-env.sh`, `/etc/environment.d/60-devos.conf` |
| 19 | desktop entries | `update-desktop-database` |
| 20 | health checks | `devos-health` as the new user → `/var/lib/devos/health.json`, `flutter doctor -v` log |
| 21 | caches cleanup | apt/npm/uv/download caches |
| 22 | final snapshot | read-only btrfs snapshot `/.snapshots/devos-post-install` + package/tool version record |
| 23 | reboot | Calamares `finished` |

Versions are resolved at install time from the vendors' official endpoints (nodejs.org index,
go.dev JSON, JetBrains release API, Android repository XML, Flutter releases JSON, dotnet release
metadata, Adoptium API, GitHub releases …) — nothing is pinned to a stale version — and each
artifact is verified against the vendor's published checksum before installation. A failing tool
never aborts the OS installation; it is reported and repairable from Developer Center.

Shared SDK trees (`/opt/android-sdk`, `/opt/flutter`, `/opt/rust`) are owned by group `devos-dev`
with setgid + default ACLs so Android Studio's SDK Manager, `flutter upgrade` and `rustup update`
work for developers without root.

## Health model

`devos-health` runs every check in the manifest as the logged-in user:

* `cmd` — the binary runs and exits 0 (version captured), optional output regex;
* `desktop` — the menu entry exists and its `Exec`/`TryExec` resolves to an executable;
* specials — KVM (CPU flags + `/dev/kvm` access), Docker/Podman engine info, `flutter doctor`
  (Flutter, Android toolchain, Linux toolchain, Chrome must be ✓), Android SDK layout + license,
  JetBrains product-info + bundled JBR runtime, systemd units, Flatpak apps, network reachability.

Status per tool: *Installed / Working*, *Installed / Broken*, *Not installed*.
`Developer Environment: READY` only if every `required: true` tool is working; otherwise
`ACTION REQUIRED`. Missing proprietary tools additionally raise
*Developer Environment Setup incomplete* with a one-click, license-confirmed completion.

First login (`/etc/xdg/autostart/devos-firstboot.desktop`) creates `~/Developer`, installs editor AI
extensions for the user when online, runs the full health check and notifies with the real result.

## Security notes

* No secrets in the image. OAuth tokens stay in each provider CLI's own store; API keys entered in
  Accounts & AI go to the Secret Service keyring; Docker uses `docker-credential-secretservice`.
* Privileged actions go through polkit (`org.devos.provision` requires admin auth;
  `org.devos.service` is allowed for group `devos-dev` and limited to a fixed list of units).
* Agents launched by Vibe Coding keep their own permission prompts; DevOS never passes
  auto-approve flags (enforced by a unit test).
