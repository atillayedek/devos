#!/usr/bin/env bash
# Host entry point: builds the ISO inside a privileged Debian 13 container.
#   scripts/build.sh Online     -> out/DevOS-Full-Online.iso
#   scripts/build.sh Offline    -> out/DevOS-Full-Offline.iso
set -euo pipefail
EDITION="${1:-Online}"
cd "$(dirname "$0")/.."
SRC="$(pwd -W 2>/dev/null || pwd)"
mkdir -p out
MSYS_NO_PATHCONV=1 docker run --rm --privileged \
  -v "$SRC:/src:ro" -v "$SRC/out:/out" -v devos-build:/build \
  -e "GITHUB_TOKEN=${GITHUB_TOKEN:-}" \
  debian:trixie bash /src/scripts/build-iso.sh "$EDITION"
