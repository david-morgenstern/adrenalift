"""Entry point so the web server can be launched with ``python -m src.web``."""

from src.web.server import main

if __name__ == "__main__":
    raise SystemExit(main())
