# Packaging the dashboard as a native macOS app

The dashboard ships as a normal web app (`python -m trading_algo.dashboard`).
This folder turns it into a **native `.app`**: a thin launcher
(`trading_algo/dashboard/desktop.py`) runs the stdlib server on a private
loopback port and shows it in a native **WKWebView** window via
[`pywebview`](https://pywebview.flowrl.com/) — no browser, no internet, real
Dock icon and window.

> ⚠️ **Build on a Mac.** py2app produces a macOS-native bundle and cannot be
> cross-compiled from Linux/Windows. The Python launcher is cross-platform, but
> the `.app` itself must be built on macOS.

## Build (one command)

```bash
bash packaging/build_mac_app.sh
# → dist/Momentum Dashboard.app
```

That installs the runtime deps (`pywebview`, `pyobjc-framework-WebKit`) and the
build tool (`py2app`), then runs `python packaging/setup_app.py py2app`.

Launch it:

```bash
open "dist/Momentum Dashboard.app"          # live (account "full")
MOMENTUM_SYNTHETIC=1 open "dist/Momentum Dashboard.app"   # offline synthetic demo
MOMENTUM_ACCOUNT=micro open "dist/Momentum Dashboard.app" # a different account
```

Drag it into `/Applications` to install. First launch on recent macOS may need
right-click → Open (it's unsigned — see below).

## Configuration

The bundle reads two environment variables at launch:

| Variable | Default | Meaning |
|---|---|---|
| `MOMENTUM_ACCOUNT` | `full` | which `paper_state_{name}.json` to display |
| `MOMENTUM_SYNTHETIC` | `0` | `1`/`true` marks positions against synthetic prices (offline) |

The app shows whatever the paper engine has written. Keep the engine running
(`python -m trading_algo.engine --once/--loop`) to feed it live state.

## Quick test before bundling

```bash
pip install pywebview pyobjc-framework-WebKit
python -m trading_algo.dashboard.desktop --account full --synthetic
```

That opens the native window directly from source — useful to confirm the WebView
works before spending time on the full py2app build.

## Code signing / notarization (optional, for distribution)

Unsigned apps run fine locally (right-click → Open the first time). To share it:

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" "dist/Momentum Dashboard.app"
xcrun notarytool submit "dist/Momentum Dashboard.app" --keychain-profile AC --wait
xcrun stapler staple "dist/Momentum Dashboard.app"
```

## Adding an icon (optional)

Drop a `packaging/icon.icns` and uncomment the `iconfile` line in
`setup_app.py`. To make one from a 1024×1024 PNG:

```bash
mkdir icon.iconset
sips -z 512 512 logo.png --out icon.iconset/icon_512x512.png   # (repeat sizes)
iconutil -c icns icon.iconset -o packaging/icon.icns
```

## Alternatives

- **PyInstaller** (`pyinstaller --windowed --name "Momentum Dashboard"
  packaging/app_main.py`) also produces a `.app`; py2app tends to handle
  pyobjc/WebKit more cleanly for this use case.
- **Simplest possible app:** an Automator "Run Shell Script" app that runs
  `python -m trading_algo.dashboard --account full` and opens
  `http://127.0.0.1:8787` in your browser — no native window, but a
  double-clickable launcher in seconds.
