#!/usr/bin/env bash
# Verifies that every package named in live-build/config/package-lists exists in
# Debian trixie (main contrib non-free non-free-firmware). Runs inside debian:trixie.
set -euo pipefail
LISTS_DIR="${1:-/src/live-build/config/package-lists}"
cat > /etc/apt/sources.list.d/debian.sources <<'SRC'
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://deb.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SRC
dpkg --add-architecture i386
apt-get update -qq >/dev/null
missing=0
while read -r pkg; do
  name="${pkg%%:*}"
  if ! apt-cache show "$pkg" >/dev/null 2>&1; then
    # virtual packages are acceptable when something provides them
    if apt-cache showpkg "$name" 2>/dev/null | awk '/^Reverse Provides:/{f=1;next} f&&NF{print;exit}' | grep -q .; then
      echo "VIRTUAL $pkg"
    else
      echo "MISSING $pkg"; missing=$((missing+1))
    fi
  fi
done < <(cat "$LISTS_DIR"/*.list.chroot | sed 's/#.*//' | tr -s ' \t' '\n' | grep -v '^$' | sort -u)
echo "missing=$missing"
echo "== simulating installation of all lists together (dependency resolution)"
pkgs=$(cat "$LISTS_DIR"/*.list.chroot | sed 's/#.*//' | tr -s ' 	' '
' | grep -v '^$' | sort -u)
if ! apt-get install -s -y $pkgs > /tmp/sim.log 2>&1; then
  grep -E "^E:|has no installation candidate|but it is not|Depends:|Conflicts" /tmp/sim.log | head -40
  missing=$((missing+1))
else
  echo "simulation OK: $(grep -c '^Inst ' /tmp/sim.log) packages would be installed"
fi
exit $(( missing > 0 ))
