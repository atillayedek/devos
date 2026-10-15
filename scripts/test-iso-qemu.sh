#!/usr/bin/env bash
# Boots the ISO in QEMU (inside debian:trixie) and verifies the live system reaches the desktop.
# Uses the ISO's own kernel/initrd with console=ttyS0 so boot progress is machine-checkable,
# then captures a screenshot of the running session.  Runs inside the test container.
set -euo pipefail
ISO="$1"; OUT="${2:-/out/boot-test}"; TIMEOUT="${3:-2400}"
mkdir -p "$OUT"; rm -f "$OUT"/*
apt-get update -qq && apt-get install -y -qq qemu-system-x86 xorriso netpbm socat >/dev/null
xorriso -osirrox on -indev "$ISO" -extract /live/vmlinuz "$OUT/vmlinuz" -extract /live/initrd.img "$OUT/initrd.img" >/dev/null 2>&1
ACCEL=tcg; [ -w /dev/kvm ] && ACCEL=kvm
echo "accelerator: $ACCEL"
qemu-system-x86_64 -machine q35,accel=$ACCEL -cpu max -smp 4 -m 6144 \
  -kernel "$OUT/vmlinuz" -initrd "$OUT/initrd.img" \
  -append "boot=live components username=devos hostname=devos locales=en_US.UTF-8 console=ttyS0,115200 systemd.show_status=1" \
  -drive file="$ISO",media=cdrom,readonly=on -vga virtio -display none \
  -nic user,model=virtio-net-pci -serial file:"$OUT/serial.log" \
  -monitor unix:"$OUT/mon.sock",server,nowait -daemonize -pidfile "$OUT/qemu.pid"
start=$(date +%s); reached=""
while (( $(date +%s) - start < TIMEOUT )); do
  if grep -qE "Reached target.*(Graphical Interface|graphical)" "$OUT/serial.log" 2>/dev/null; then reached=graphical; break; fi
  sleep 10
done
echo "boot result: ${reached:-TIMEOUT} after $(( $(date +%s) - start ))s"
[ -n "$reached" ] && sleep 240   # give SDDM autologin + Plasma time to draw the desktop
echo "screendump $OUT/screen.ppm" | socat - unix-connect:"$OUT/mon.sock" >/dev/null || true
sleep 3
[ -f "$OUT/screen.ppm" ] && pnmtopng "$OUT/screen.ppm" > "$OUT/screen.png" && rm "$OUT/screen.ppm"
grep -E "Reached target|Started .*(SDDM|NetworkManager|Simple Desktop)|Failed to start" "$OUT/serial.log" | tail -25 || true
kill "$(cat "$OUT/qemu.pid")" 2>/dev/null || true
[ -n "$reached" ]
