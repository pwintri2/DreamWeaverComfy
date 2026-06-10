#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
rm -rf desktop-dist
mkdir -p desktop-dist/ui
cp index.html styles.css main.js state.js storage.js api.js package.json desktop-dist/
cp ui/*.js desktop-dist/ui/
