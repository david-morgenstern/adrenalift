"""Quarantined registry-tweak service for the Adrenalift web console.

Unlike every other Adrenalift action, the AMD driver registry tweaks
(:mod:`src.tools.reg_patch`) are **persistent across reboots** -- they write
DWORD values under the GPU's Display-Class registry key. That breaks the web
console's "a restart reverts everything" guarantee, so they are deliberately
kept out of profiles and isolated behind this service.

To make them behave ephemerally *when possible*, this service:

* takes a backup of the original values before the first apply (handled by
  :class:`src.tools.reg_patch.RegistryPatch`, which writes ``.reg_backup.json``),
* exposes a one-click :func:`restore`, and
* registers an ``atexit`` + SIGINT/SIGTERM handler that auto-restores on a
  clean server shutdown.

A hard crash or power loss cannot trigger the shutdown hook, so the UI must be
explicit: registry tweaks survive a reboot until restored, and the backup file
persists precisely so :func:`restore` can undo them later.

Like :mod:`src.web.hardware_service`, everything here degrades gracefully off
Windows: the registry is simply reported unavailable.
"""

from __future__ import annotations

import atexit
import logging
import platform
import signal
import threading
from typing import Any, Dict, List, Optional

_log = logging.getLogger("adrenalift.web.registry")

_lock = threading.Lock()
_auto_restore_armed = False
_applied_once = False


def _import_reg():
    """Import the registry-patch module or raise a clear, safe error."""
    try:
        from src.tools import reg_patch
    except Exception as exc:  # noqa: BLE001
        _log.warning("reg_patch import failed: %s", exc)
        raise RegistryUnavailable(
            "Registry tweaks are unavailable on this system. They require "
            "Windows with an AMD GPU and Administrator privileges."
        ) from exc
    return reg_patch


class RegistryUnavailable(RuntimeError):
    """Raised when the registry tweaks cannot be used (non-Windows / no adapter)."""


def available() -> bool:
    return platform.system() == "Windows"


def status() -> Dict[str, Any]:
    """Non-throwing description of the current registry state for the UI."""
    info: Dict[str, Any] = {
        "available": False,
        "platform": platform.system(),
        "applied": _applied_once,
        "reason": "",
        "adapter": None,
        "values": None,
    }
    if not available():
        info["reason"] = (
            "Registry tweaks require Windows with an AMD GPU. They are the only "
            "settings that survive a reboot, so they live here, separate from "
            "the ephemeral profiles."
        )
        return info
    try:
        reg = _import_reg()
        rp = reg.RegistryPatch()
        current = rp.read_current()
        info["available"] = True
        info["adapter"] = rp.info.get("DriverDesc", "AMD GPU")
        info["values"] = current
    except RegistryUnavailable as exc:
        info["reason"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        _log.warning("registry status failed: %s", exc)
        info["reason"] = (
            "Could not read the AMD GPU registry key. Make sure the server is "
            "running as Administrator."
        )
    return info


def recommended_values() -> Dict[str, int]:
    """Return the recommended anti-clock-gating value map (safe off-Windows)."""
    try:
        reg = _import_reg()
        return dict(reg.RECOMMENDED_VALUES)
    except Exception:  # noqa: BLE001
        return {}


def _arm_auto_restore() -> None:
    """Register atexit + signal handlers to restore on clean shutdown (once)."""
    global _auto_restore_armed
    if _auto_restore_armed:
        return
    _auto_restore_armed = True

    def _restore_quietly(*_args) -> None:
        try:
            restore()
            _log.info("Registry tweaks auto-restored on shutdown.")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Auto-restore on shutdown failed: %s", exc)

    atexit.register(_restore_quietly)
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            prev = signal.getsignal(sig)

            def _handler(signum, frame, _prev=prev):
                _restore_quietly()
                # Chain to the previous handler so Ctrl+C still stops the server.
                if callable(_prev):
                    _prev(signum, frame)
                else:
                    raise KeyboardInterrupt

            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # signal.signal only works on the main thread; ignore otherwise.
            pass


def apply(values: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Apply registry tweaks (recommended set, or a custom value map).

    A backup of the originals is written before the first change, and a
    shutdown auto-restore hook is armed.
    """
    global _applied_once
    if not available():
        raise RegistryUnavailable(
            "Registry tweaks require Windows with an AMD GPU."
        )
    reg = _import_reg()
    with _lock:
        rp = reg.RegistryPatch()
        # values=None -> RegistryPatch applies its PATCH_VALUES targets.
        changes = rp.apply(values=values)
        _applied_once = True
        _arm_auto_restore()
    changed = [
        {"name": n, "display": reg.REG_NAME_TO_DISPLAY.get(n, n), "old": o, "new": v}
        for (n, o, v) in changes
    ]
    return {
        "ok": True,
        "changed_count": len(changed),
        "changed": changed,
        "message": (
            f"Applied {len(changed)} registry change(s). These persist across "
            "reboots; use Restore (or a clean server shutdown) to revert."
        ),
    }


def restore() -> Dict[str, Any]:
    """Restore registry values from the backup written at first apply."""
    if not available():
        raise RegistryUnavailable(
            "Registry tweaks require Windows with an AMD GPU."
        )
    reg = _import_reg()
    with _lock:
        rp = reg.RegistryPatch()
        restored = rp.restore()
    items = [
        {"name": n, "display": reg.REG_NAME_TO_DISPLAY.get(n, n), "from": c, "to": r}
        for (n, c, r) in restored
    ]
    return {
        "ok": True,
        "restored_count": len(items),
        "restored": items,
        "message": f"Restored {len(items)} registry value(s) from backup.",
    }
