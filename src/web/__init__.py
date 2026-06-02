"""Adrenalift web server package.

Provides a browser-based front-end as an alternative to the PySide6 desktop
GUI.  The desktop app and the web server share the same overclock engine
(``src.engine.overclock_engine``); this package only adds an HTTP layer plus a
job manager and an extended, beginner-friendly help / tooltip system.

Run it with::

    python -m src.web            # http://127.0.0.1:8770

The engine performs Windows-only physical-memory and SMU operations, so the
hardware actions only work on a Windows host with the required drivers and
Administrator privileges.  On other platforms the server still starts and the
UI (including all help and tooltips) remains fully navigable; hardware actions
report a friendly "engine unavailable" message instead of crashing.
"""

from __future__ import annotations

__all__ = ["create_app", "main"]


def create_app(*args, **kwargs):
    # Imported lazily so that merely importing the package does not require
    # Flask to be installed (e.g. when only the help/tooltip data is needed).
    from src.web.server import create_app as _create_app

    return _create_app(*args, **kwargs)


def main(*args, **kwargs):
    from src.web.server import main as _main

    return _main(*args, **kwargs)
