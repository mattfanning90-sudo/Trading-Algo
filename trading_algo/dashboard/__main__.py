"""CLI entry: python -m trading_algo.dashboard --account full --synthetic"""
from __future__ import annotations

import argparse

from . import server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Live trading dashboard")
    ap.add_argument("--account", default="main")
    ap.add_argument("--synthetic", action="store_true",
                    help="mark positions against synthetic prices (offline)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)
    server.serve(args.account, args.synthetic, args.host, args.port)


if __name__ == "__main__":
    main()
