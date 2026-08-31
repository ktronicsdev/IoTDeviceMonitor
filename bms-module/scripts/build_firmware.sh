#!/usr/bin/env bash
# =============================================================================
# Build a flashable KT BMS Monitor .bin for one site.
#
# The installer in Sri Lanka does NOT need ESPHome - they flash this .bin from
# a browser at https://web.esphome.io. This script produces that file.
#
# Usage:
#   ./build_firmware.sh                                  # gayan-imh trial defaults
#   ./build_firmware.sh kt-bms-<site> C8:47:8C:AA:BB:CC   # any other site
#
# Output: bms-module/dist/<device>-<date>.bin  (gitignored)
#
# ⚠️  The .bin contains the GitHub token from firmware/secrets.yaml.
#     This repo is PUBLIC - never commit the .bin and never attach it to an
#     issue/PR/Actions artifact. Send it to the installer privately.
# =============================================================================
set -euo pipefail

DEVICE="${1:-kt-bms-gayan-imh}"
BMS_MAC="${2:-}"

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_YAML="${MODULE_DIR}/firmware/jk-bms-monitor.yaml"
SECRETS="${MODULE_DIR}/firmware/secrets.yaml"
DIST="${MODULE_DIR}/dist"

if [ ! -f "$SECRETS" ]; then
  echo "ERROR: ${SECRETS} not found."
  echo "       cp ${MODULE_DIR}/firmware/secrets.example.yaml ${SECRETS}   # then edit it"
  exit 1
fi

if grep -q "github_pat_XXXX" "$SECRETS"; then
  echo "ERROR: secrets.yaml still has the placeholder token - put a real"
  echo "       fine-grained PAT (Contents: Read and write on ktronicsdev/IoTDeviceMonitor) in it."
  exit 1
fi

ESPHOME="${ESPHOME:-esphome}"
if ! command -v "$ESPHOME" >/dev/null 2>&1; then
  if py -m esphome version >/dev/null 2>&1; then
    ESPHOME="py -m esphome"          # Windows: python launcher
  else
    echo "ERROR: esphome not installed.  pip install esphome"
    exit 1
  fi
fi

SUBS=(-s device_name "$DEVICE")
if [ -n "$BMS_MAC" ]; then
  SUBS+=(-s bms_mac "$BMS_MAC")
else
  echo "NOTE: no BMS MAC given - using the default in the YAML."
  echo "      The installer must scan the real MAC first (see INSTALL.md);"
  echo "      a wrong MAC means the ESP32 never connects to the battery."
fi

echo "==> Validating config for ${DEVICE}"
$ESPHOME "${SUBS[@]}" config "$FIRMWARE_YAML" >/dev/null

echo "==> Compiling (first run downloads the toolchain - can take several minutes)"
$ESPHOME "${SUBS[@]}" compile "$FIRMWARE_YAML"

# The web flasher needs the *factory* image (bootloader + partitions + app).
BIN="$(find "${MODULE_DIR}/firmware/.esphome/build/${DEVICE}" -name 'firmware.factory.bin' 2>/dev/null | head -1)"
if [ -z "$BIN" ]; then
  echo "ERROR: firmware.factory.bin not found - check the compile output above."
  exit 1
fi

mkdir -p "$DIST"
OUT="${DIST}/${DEVICE}-$(date -u +%Y%m%d).bin"
cp "$BIN" "$OUT"

echo
echo "✅ Built: ${OUT}"
echo "   Send this file to the installer privately (WhatsApp/Drive/email - NOT this repo)."
echo "   They flash it at https://web.esphome.io  ->  see bms-module/INSTALL.md"
