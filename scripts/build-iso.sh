#!/usr/bin/env bash
# Builds DevOS-Full-<Edition>.iso. Runs inside a privileged debian:trixie container
# (see scripts/build.sh); /src is the repository, /build a scratch volume, /out the output.
set -euo pipefail
EDITION="${1:-Online}"
[[ "$EDITION" == Online || "$EDITION" == Offline ]] || { echo "edition must be Online or Offline"; exit 2; }
SRC=/src; WORK="/build/$EDITION"; OUT=/out

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq live-build librsvg2-bin python3-yaml python3-pytest python3-pefile rsync xorriso squashfs-tools \
  dosfstools mtools ca-certificates >/dev/null

echo "== running unit tests"
(cd "$SRC" && python3 -m pytest -q -p no:cacheprovider tests)

echo "== preparing live-build tree in $WORK"
mkdir -p "$WORK"
rsync -a --delete --exclude cache --exclude chroot --exclude binary --exclude '.build' \
  "$SRC/live-build/" "$WORK/" 
INC="$WORK/config/includes.chroot"

# DevOS Python package, commands and manifest
install -d "$INC/usr/lib/python3/dist-packages" "$INC/usr/bin" "$INC/usr/share/devos/branding" "$INC/etc/devos"
rsync -a --delete --exclude __pycache__ "$SRC/devos/" "$INC/usr/lib/python3/dist-packages/devos/"
install -m 0755 "$SRC"/bin/* "$INC/usr/bin/"
install -m 0644 "$SRC/provisioning/tools.yaml" "$INC/usr/share/devos/tools.yaml"
echo "$EDITION" > "$INC/etc/devos/edition"

# Branding rendered from SVG sources
ICONS="$INC/usr/share/icons/hicolor"
install -d "$ICONS/scalable/apps" "$ICONS/256x256/apps" "$INC/usr/share/pixmaps"
for svg in "$SRC"/branding/icons/*.svg; do
  n=$(basename "$svg" .svg)
  install -m 0644 "$svg" "$ICONS/scalable/apps/$n.svg"
  rsvg-convert -w 256 -h 256 "$svg" -o "$ICONS/256x256/apps/$n.png"
done
install -m 0644 "$SRC/branding/logo.svg" "$ICONS/scalable/apps/devos.svg"
rsvg-convert -w 256 -h 256 "$SRC/branding/logo.svg" -o "$INC/usr/share/pixmaps/devos.png"
WP="$INC/usr/share/wallpapers/DevOS"
install -d "$WP/contents/images"
rsvg-convert -w 3840 -h 2160 "$SRC/branding/wallpaper.svg" -o "$WP/contents/images/3840x2160.png"
rsvg-convert -w 1920 -h 1080 "$SRC/branding/wallpaper.svg" -o "$WP/contents/images/1920x1080.png"
cat > "$WP/metadata.json" <<'JSON'
{ "KPlugin": { "Id": "DevOS", "Name": "DevOS", "License": "CC-BY-SA-4.0",
  "Authors": [ { "Name": "DevOS Project" } ] } }
JSON
for m in online offline; do
  rsvg-convert -w 640 -h 400 "$SRC/branding/calamares/mode-$m.svg" -o "$INC/usr/share/devos/branding/mode-$m.png"
done
CB="$INC/usr/share/devos/overrides/etc/calamares/branding/devos"
install -d "$CB"
install -m 0644 "$SRC/branding/calamares/branding.desc" "$SRC/branding/calamares/show.qml" "$CB/"
rsvg-convert -w 256 -h 256 "$SRC/branding/logo.svg" -o "$CB/logo.png"
rsvg-convert -w 960 -h 540 "$SRC/branding/wallpaper.svg" -o "$CB/welcome.png"
# GRUB / syslinux boot menu splash
rm -rf "$WORK/config/bootloaders"
cp -r /usr/share/live/build/bootloaders "$WORK/config/bootloaders"
for d in grub-pc grub-efi; do
  [ -d "$WORK/config/bootloaders/$d" ] && rsvg-convert -w 1920 -h 1080 "$SRC/branding/wallpaper.svg" -o "$WORK/config/bootloaders/$d/splash.png"
done
for d in syslinux syslinux_common isolinux; do
  [ -d "$WORK/config/bootloaders/$d" ] && rsvg-convert -w 640 -h 480 "$SRC/branding/wallpaper.svg" -o "$WORK/config/bootloaders/$d/splash.png"
done

# normalise line endings / permissions (sources may come from a Windows checkout)
find "$WORK/config" "$WORK/auto" -type f \( -name '*.hook.chroot' -o -name '*.sh' -o -name '*.conf' \
  -o -name '*.desktop' -o -name '*.py' -o -name '*.yaml' -o -name '*.list.chroot' -o -path '*/auto/*' \
  -o -path '*/usr/bin/*' -o -path '*/usr/lib/devos/*' \) -exec sed -i 's/\r$//' {} +
chmod 0755 "$WORK"/auto/* "$WORK"/config/hooks/live/*.hook.chroot "$INC"/usr/lib/devos/* \
  "$INC"/usr/lib/calamares/modules/devosprovision/main.py

echo "== lb config ($EDITION)"
cd "$WORK"
DEVOS_EDITION="$EDITION" lb config
echo "== lb build ($EDITION) — this downloads and assembles several GB"
lb build

ISO=$(ls -1 "$WORK"/*.iso | head -1)
install -d "$OUT"
cp "$ISO" "$OUT/DevOS-Full-$EDITION.iso"
(cd "$OUT" && sha256sum "DevOS-Full-$EDITION.iso" > "DevOS-Full-$EDITION.iso.sha256")
ls -lh "$OUT"
