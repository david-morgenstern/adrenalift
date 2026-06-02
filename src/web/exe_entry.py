"""Frozen entry point for the Adrenalift web-server executable.

This is the script PyInstaller bundles (see ``build_web.spec``).  It starts the
Flask server exactly like ``python -m src.web`` but additionally opens the
user's default browser at the served URL, so double-clicking the ``.exe`` lands
straight in the web console.

The console window is intentionally kept open: it shows the URL and the live
server log, and closing it (or Ctrl+C) stops the server.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

# Make ``src`` importable both when frozen and when run from a source checkout.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.web.server import create_app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="Adrenalift",
        description="Adrenalift web console (browser front-end).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1, localhost only). "
        "Use 0.0.0.0 to expose on your LAN -- only on a trusted network.",
    )
    parser.add_argument(
        "--port", type=int, default=8770, help="Port to listen on (default: 8770)."
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser window automatically.",
    )
    args = parser.parse_args(argv)

    app = create_app()

    # When binding to all interfaces, still open the browser on localhost.
    browse_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{browse_host}:{args.port}"

    print(f"Adrenalift web console running at {url}")
    print("Keep this window open. Press Ctrl+C to stop the server.")

    if not args.no_browser:
        # Delay slightly so the server is listening before the browser opens.
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
