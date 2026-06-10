#!/usr/bin/env bash
# Build the macOS dashboard app. Run this ON A MAC (py2app cannot cross-compile).
#
#   bash packaging/build_mac_app.sh
#
# Produces: dist/Momentum Dashboard.app
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

if [[ "$(uname)" != "Darwin" ]]; then
  echo "⚠  This builds a macOS .app and must run on macOS (uname=$(uname))." >&2
  exit 1
fi

echo "→ Installing build + runtime dependencies…"
python3 -m pip install --upgrade pip wheel
python3 -m pip install -r requirements.txt
python3 -m pip install "pywebview>=5.0" pyobjc-framework-WebKit "py2app>=0.28"

echo "→ Cleaning previous build…"
rm -rf build "dist/Momentum Dashboard.app"

echo "→ Building .app with py2app…"
python3 packaging/setup_app.py py2app

echo
echo "✅ Built: dist/Momentum Dashboard.app"
echo "   Launch:   open 'dist/Momentum Dashboard.app'"
echo "   Install:  drag it into /Applications"
echo "   Synthetic demo:  MOMENTUM_SYNTHETIC=1 open 'dist/Momentum Dashboard.app'"
open dist 2>/dev/null || true
