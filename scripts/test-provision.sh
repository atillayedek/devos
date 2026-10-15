#!/usr/bin/env bash
# Real provisioning test in a clean Debian 13 container: downloads, verifies and installs
# the selected tools exactly as the installer would, then runs the health checks on them.
#   scripts/test-provision.sh [tool ids...]
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-yaml python3-keyring ca-certificates gnupg curl git acl xz-utils unzip \
  zstd sudo procps file libglib2.0-bin desktop-file-utils >/dev/null
rsync() { :; }
install -d /usr/share/devos /usr/lib/python3/dist-packages
cp -r /src/devos /usr/lib/python3/dist-packages/
cp /src/provisioning/tools.yaml /usr/share/devos/tools.yaml
install -m 0755 /src/bin/* /usr/bin/
install -m 0644 /src/live-build/config/includes.chroot/etc/profile.d/devos-env.sh /etc/profile.d/
sed -i 's/\r$//' /usr/bin/devos-* /etc/profile.d/devos-env.sh
useradd -m -s /bin/bash dev
TOOLS=("$@")
devos-provision --tools "${TOOLS[@]}" --target-user dev --accept-licenses "${TOOLS[@]}" --accepted-via test-container || true
echo "================ HEALTH (as user dev) ================"
devos-health --user dev --tool "${TOOLS[@]}" || true
