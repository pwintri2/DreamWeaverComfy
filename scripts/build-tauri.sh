#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
npm install
npm run tauri:build

echo "Build output:"
find src-tauri/target/release/bundle -maxdepth 3 -type f | sort
