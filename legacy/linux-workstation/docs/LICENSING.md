# What is shipped inside the ISO vs. downloaded from the vendor

DevOS never redistributes software whose license forbids it and never bypasses a
license. Every tool's decision is recorded in `provisioning/tools.yaml`
(`redistributable:`) and enforced by `devos-provision`:

* `--scope redistributable` (used to build **DevOS-Full-Offline.iso**) cannot install a
  tool marked `redistributable: false`.
* A proprietary tool is installed only after the user accepted its license — either by
  choosing **Full Online** in the installer (the license URLs are listed on that page) or
  in Developer Center's license dialog. Acceptance is recorded in
  `/etc/devos/accepted-licenses.json`.

| Tool | Shipped in Offline ISO | Why |
|---|---|---|
| Debian packages (GCC/Clang, Python, OpenJDK 21, PHP, PostgreSQL, MariaDB, Redis, Docker Engine (Moby), Podman, QEMU/KVM, Wine, Firefox ESR, Chromium, KDE…) | yes (both ISOs) | free software in Debian main/contrib/non-free-firmware |
| Node.js, Bun, Deno, uv, Go, Rust, .NET SDK, Temurin 17/25, Gradle, Kotlin, Flutter, VSCodium, DBeaver CE, Codex CLI, Gemini CLI, Ollama, llama.cpp, Aider, kubectl, Helm, kind, minikube, k9s, OpenTofu, AWS CLI, Azure CLI, Supabase CLI, Firebase CLI, cloudflared/Wrangler, Wine Staging, Bottles | yes (Offline ISO) | MIT / BSD / Apache-2.0 / MPL / GPL / LGPL |
| Visual Studio Code (Microsoft build) | no — Microsoft apt repo at install | Microsoft Software License |
| Google Chrome | no — Google apt repo at install | Chrome Terms of Service |
| JetBrains IDEs + Toolbox | no — download.jetbrains.com at install | JetBrains EULA; the user signs in with their own account, no license bypass |
| Android Studio, Android SDK/NDK/emulator/system images | no — dl.google.com at install | Android SDK License Agreement (must be accepted) |
| Claude Code | no — npm registry at install | Anthropic terms |
| Google Cloud CLI | no — Google apt repo at install | Google Cloud terms |
| VS Code / VSCodium AI extensions | no — installed per user at first login | marketplace terms |
| Visual Studio | never on Linux | Windows-only; DevOS downloads Microsoft's official bootstrapper into the Windows VM share on request |
| Windows | never | the user supplies their own ISO and license |

Every download is HTTPS-only and verified against the vendor's published digest
(SHA-256/SHA-512/SHA-1 from the vendor's checksum file, release API or repository
metadata). The AWS CLI is verified with GnuPG against the AWS CLI release key
(fingerprint `FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C`). Vendor apt
repositories are pinned to their own signing key with `Signed-By`.
