"""py2app build config for the macOS dashboard app.

Build from the repo root ON A MAC:
    python packaging/setup_app.py py2app
-> dist/Momentum Dashboard.app

(See packaging/README.md and packaging/build_mac_app.sh for the full recipe.)
"""
from setuptools import setup

APP = ["packaging/app_main.py"]

OPTIONS = {
    "argv_emulation": False,
    # Copy the whole package (incl. dashboard/static/*) into the bundle.
    "packages": ["trading_algo"],
    # pywebview + its macOS WebKit backend; add more here if py2app misses any.
    "includes": ["webview", "objc"],
    "plist": {
        "CFBundleName": "Momentum Dashboard",
        "CFBundleDisplayName": "Momentum Dashboard",
        "CFBundleIdentifier": "com.tradingalgo.momentum-dashboard",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "NSHighResolutionCapable": True,
        # The dashboard talks to its own loopback server; allow it explicitly.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
    # "iconfile": "packaging/icon.icns",   # optional: drop in an .icns to brand it
}

setup(
    name="Momentum Dashboard",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
