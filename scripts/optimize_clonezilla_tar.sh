#!/usr/bin/env bash
set -euo pipefail

# Optimize a Clonezilla saveparts archive that contains a single FAT32 partition image.
# This script is designed for usbtesterimg-style images (sdc1.vfat-ptcl-img.zst.aa).

usage() {
  cat <<USAGE
Usage: $0 --input-tar <archive.tar.gz> --output-tar <optimized.tar.gz> [--workdir <dir>]

Requires: partclone.vfat, zstd, mkfs.vfat, mcopy/mmd/mdel (mtools).
USAGE
}

INPUT_TAR=""
OUTPUT_TAR=""
WORKDIR="${PWD}/work_optimize_clonezilla"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-tar) INPUT_TAR="$2"; shift 2 ;;
    --output-tar) OUTPUT_TAR="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$INPUT_TAR" && -n "$OUTPUT_TAR" ]] || { usage; exit 1; }
for bin in partclone.vfat zstd mkfs.vfat mcopy mmd mdel; do
  command -v "$bin" >/dev/null || { echo "Missing dependency: $bin" >&2; exit 2; }
done

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

EXTRACT_DIR="$WORKDIR/extracted"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$INPUT_TAR" -C "$EXTRACT_DIR"

ROOT_DIR=$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n1)
[[ -n "$ROOT_DIR" ]] || { echo "Archive layout unsupported" >&2; exit 3; }
PTCL="$ROOT_DIR/sdc1.vfat-ptcl-img.zst.aa"
[[ -f "$PTCL" ]] || { echo "Expected $PTCL not found" >&2; exit 3; }

RAW="$WORKDIR/sdc1.raw"
zstd -d -c "$PTCL" > "$WORKDIR/sdc1.partclone"
partclone.vfat -r -s "$WORKDIR/sdc1.partclone" -O "$RAW"

MTOOLSRC="$WORKDIR/mtoolsrc"
cat > "$MTOOLSRC" <<MEOF
drive z: file=\"$RAW\" partition=0
MEOF
export MTOOLSRC

# Boot optimizations for portable Puppy test USB without removing core packages:
# 1) Remove stale persistent net rules.
mdel z::/etc/udev/rules.d/70-persistent-net.rules 2>/dev/null || true

# 2) Ensure non-blocking startup order for tester apps.
cat > "$WORKDIR/00-laptoptester-startup" <<'SH'
#!/bin/sh
APPDIR="/root/laptoptester"
[ -x /usr/bin/python3 ] || exit 0
if [ -f "$APPDIR/wifi_manager.py" ]; then
  /usr/bin/python3 "$APPDIR/wifi_manager.py" >/tmp/wifi_manager.log 2>&1 &
fi
if [ -f "$APPDIR/laptop_tester.py" ]; then
  exec /usr/bin/python3 "$APPDIR/laptop_tester.py" >/tmp/laptop_tester.log 2>&1
fi
SH
chmod +x "$WORKDIR/00-laptoptester-startup"
mmd -D s z::/root/Startup 2>/dev/null || true
mcopy -o "$WORKDIR/00-laptoptester-startup" z::/root/Startup/00-laptoptester-startup

# Rebuild partclone + zstd payload.
NEW_PTCL="$ROOT_DIR/sdc1.vfat-ptcl-img.zst.aa"
partclone.vfat -c -s "$RAW" -O "$WORKDIR/sdc1.partclone.new"
zstd -19 -T0 -f "$WORKDIR/sdc1.partclone.new" -o "$NEW_PTCL"

# Repack into single tar.gz for Clonezilla.
tar -czf "$OUTPUT_TAR" -C "$EXTRACT_DIR" "$(basename "$ROOT_DIR")"

echo "Optimized archive written to: $OUTPUT_TAR"
