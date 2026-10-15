#!/usr/bin/env bash
# Host wrapper: scripts/test-iso.sh out/DevOS-Full-Online.iso
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd -W 2>/dev/null || pwd)"
ISO="$(basename "$1")"
MSYS_NO_PATHCONV=1 docker run --rm --privileged -v "$SRC:/src:ro" -v "$SRC/out:/out" \
  debian:trixie bash /src/scripts/test-iso-qemu.sh "/out/$ISO" /out/boot-test "${2:-2400}"
