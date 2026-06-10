#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
if ! command -v flatpak-builder >/dev/null 2>&1; then
  echo "flatpak-builder is niet geinstalleerd. Installeer het via je pakketbeheerder en run dit script opnieuw." >&2
  exit 1
fi

flatpak-builder --force-clean --user --install build-dir ai.wintrip.Dreamweaver.yml
echo "Start daarna met: flatpak run ai.wintrip.Dreamweaver"
