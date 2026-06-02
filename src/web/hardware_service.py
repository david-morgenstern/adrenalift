"""Platform-agnostic hardware service for the Adrenalift web server.

This module is the bridge between the HTTP layer and
:mod:`src.engine.overclock_engine`.  It reproduces the behaviour of the desktop
``ScanThread`` / ``ApplyWorker`` (open hardware, do the work, always clean up)
but with plain progress/log callbacks instead of Qt signals, and it caches the
most recent scan result so an Apply can reuse it -- exactly as the desktop
``MainOverclockWidget`` does.

The overclock engine performs Windows-only operations (physical-memory mapping,
SMU mailbox).  Importing it therefore fails on other platforms.  Every public
function here imports the engine lazily and raises :class:`HardwareUnavailable`
with a clear, user-facing message when it cannot be used, so the web server and
its UI stay usable everywhere while real hardware actions are gated to a
properly configured Windows host.
"""

from __future__ import annotations

import platform
import threading
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

import logging

_log = logging.getLogger("adrenalift.web")

LogFn = Callable[[str], None]
ProgressFn = Callable[[float, str], None]


class HardwareUnavailable(RuntimeError):
    """Raised when the overclock engine / GPU hardware cannot be used."""


# Serialise all hardware access: the underlying drivers and SMU mailbox are a
# single shared resource and must not be entered concurrently.
_hw_lock = threading.Lock()

# Cached result of the most recent successful scan (mirrors the desktop
# ``MainOverclockWidget.scan_result``).  Apply needs the valid addresses.
_last_scan: Optional[Dict[str, Any]] = None
_last_scan_lock = threading.Lock()


def _noop_log(_msg: str) -> None:
    pass


def _noop_progress(_pct: float, _msg: str = "") -> None:
    pass


# ---------------------------------------------------------------------------
# Engine availability
# ---------------------------------------------------------------------------

def _import_engine():
    """Import and return the overclock engine module, or raise a clear error."""
    try:
        from src.engine import overclock_engine as engine
    except Exception as exc:  # noqa: BLE001 - convert to a friendly message
        # Log the raw detail server-side only; never return it to the client
        # (avoids leaking internal stack-trace information over HTTP).
        _log.warning("Overclock engine import failed: %s", exc)
        raise HardwareUnavailable(
            "The overclock engine is unavailable on this system "
            f"({platform.system()}). Hardware actions require Windows with the "
            "AMD driver, the bundled kernel drivers, and Administrator "
            "privileges. See the server console for details."
        ) from exc
    return engine


def engine_status() -> Dict[str, Any]:
    """Return a non-throwing description of engine availability for the UI."""
    info: Dict[str, Any] = {
        "platform": platform.system(),
        "available": False,
        "reason": "",
    }
    try:
        _import_engine()
        info["available"] = True
    except HardwareUnavailable as exc:
        info["reason"] = str(exc)
    return info


# ---------------------------------------------------------------------------
# VBIOS summary (read-only, used for the info banner)
# ---------------------------------------------------------------------------

def vbios_summary() -> Dict[str, Any]:
    """Return a short description of the detected VBIOS values (or defaults)."""
    try:
        from src.app.constants import DEFAULT_VBIOS_PATH, _get_vbios_values
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "summary": f"VBIOS info unavailable: {exc}"}

    try:
        vals = _get_vbios_values(DEFAULT_VBIOS_PATH)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "summary": f"VBIOS parse failed: {exc}"}

    if vals is None:
        return {
            "available": False,
            "summary": "No VBIOS ROM found (bios/vbios.rom). Built-in defaults "
            "will be used.",
        }
    try:
        summary = vals.summary()
    except Exception:  # noqa: BLE001
        summary = "VBIOS detected."
    return {"available": True, "summary": summary}


def _get_vbios_values_or_defaults():
    from src.app.constants import DEFAULT_VBIOS_PATH, _get_vbios_values

    vbios = _get_vbios_values(DEFAULT_VBIOS_PATH)
    if vbios is None:
        engine = _import_engine()
        vbios = engine.parse_vbios_or_defaults(DEFAULT_VBIOS_PATH)
    return vbios


# ---------------------------------------------------------------------------
# Scan (mirrors src/app/workers.py ScanThread.run)
# ---------------------------------------------------------------------------

def run_scan(
    *,
    num_threads: int = 0,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Scan physical memory for the driver's PowerPlay table.

    Returns a JSON-serialisable summary and caches it for a later Apply.
    Raises :class:`HardwareUnavailable` on any hardware/engine failure.
    """
    engine = _import_engine()

    with _hw_lock:
        vbios = _get_vbios_values_or_defaults()

        hw = None
        try:
            try:
                hw = engine.init_hardware(skip_dma_discovery=True)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    "Could not initialise the GPU drivers. Make sure the "
                    "AMD driver and bundled kernel drivers are installed and "
                    "that the server is running as Administrator. See the "
                    "server console for details."
                ) from exc

            inpout = hw["inpout"]
            dma_ok = hw.get("virt") is not None

            settings = engine.OverclockSettings(
                game_clock=vbios.gameclock_ac,
                boost_clock=vbios.boostclock_ac,
                clock=vbios.gameclock_ac,
            )
            scan_opts = engine.ScanOptions()
            scan_opts.num_threads = num_threads

            if not dma_ok:
                progress(
                    2,
                    "DMA buffer not found \u2014 PP-table patching still works; "
                    "run a DRAM scan in the desktop app to enable OD/metrics.",
                )

            result = engine.scan_for_pptable(
                inpout,
                settings,
                scan_opts=scan_opts,
                progress_callback=progress,
                vbios_values=vbios,
            )

            if hw and result and dma_ok:
                try:
                    od = engine.read_od(hw["smu"], hw["virt"])
                    if od is not None:
                        result.od_table = od
                except Exception:  # noqa: BLE001 - OD readback is best-effort
                    pass

            summary = _scan_result_to_dict(result, dma_ok=dma_ok)
            _store_scan(result, summary)
            log(summary["message"])
            return summary
        finally:
            if hw:
                try:
                    engine.cleanup_hardware(hw)
                except Exception:  # noqa: BLE001
                    pass


def _scan_result_to_dict(result, *, dma_ok: bool) -> Dict[str, Any]:
    valid = list(getattr(result, "valid_addrs", []) or [])
    rejected = list(getattr(result, "rejected_addrs", []) or [])
    error = getattr(result, "error", None)
    has_od = getattr(result, "od_table", None) is not None

    if error and not valid:
        message = f"Scan failed: {error}"
        ready = has_od
    elif valid:
        message = f"Found {len(valid)} PowerPlay table(s) in memory."
        ready = True
    else:
        message = "Scan complete: no PowerPlay table found."
        ready = has_od

    return {
        "ok": bool(valid) or has_od,
        "ready_to_apply": bool(ready),
        "valid_count": len(valid),
        "valid_addrs": [f"0x{a:012X}" for a in valid],
        "rejected_count": len(rejected),
        "has_od_table": has_od,
        "dma_available": bool(dma_ok),
        "error": error,
        "message": message,
    }


def _store_scan(result, summary: Dict[str, Any]) -> None:
    global _last_scan
    with _last_scan_lock:
        _last_scan = {"result": result, "summary": summary}


def last_scan_summary() -> Optional[Dict[str, Any]]:
    with _last_scan_lock:
        return dict(_last_scan["summary"]) if _last_scan else None


def _get_cached_result():
    with _last_scan_lock:
        return _last_scan["result"] if _last_scan else None


# ---------------------------------------------------------------------------
# Apply boost clock (mirrors MainOverclockWidget._on_apply_simple)
# ---------------------------------------------------------------------------

def apply_boost_clock(
    clock_mhz: int,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Patch the driver's PP table with *clock_mhz* and activate it.

    Requires a prior successful :func:`run_scan` that found valid addresses.
    """
    engine = _import_engine()

    scan_result = _get_cached_result()
    valid = getattr(scan_result, "valid_addrs", None) if scan_result else None
    if not valid:
        raise HardwareUnavailable(
            "No scan result available. Run a Scan first so Adrenalift knows "
            "where the driver's clock table lives."
        )

    try:
        clock_mhz = int(clock_mhz)
    except (TypeError, ValueError) as exc:
        raise ValueError("Boost clock must be a whole number of MHz.") from exc

    with _hw_lock:
        vbios = _get_vbios_values_or_defaults()
        settings = engine.OverclockSettings(
            clock=clock_mhz, offset=0, od_ppt=0, od_tdc=0
        )

        hw = None
        try:
            try:
                hw = engine.init_hardware(skip_dma_discovery=True)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    "Could not initialise the GPU drivers. Make sure the "
                    "AMD driver and bundled kernel drivers are installed and "
                    "that the server is running as Administrator. See the "
                    "server console for details."
                ) from exc

            inpout, smu = hw["inpout"], hw["smu"]
            # ``progress`` (the job's set_progress) already records each message
            # in the job log, so don't also call ``log`` here or lines double up.
            results = engine.apply_clocks_only(
                inpout,
                smu,
                scan_result,
                settings,
                vbios_values=vbios,
                progress_callback=progress,
            )
            patched = results.get("patched_count", 0)
            skipped = results.get("skipped_count", 0)
            msg = (
                f"Applied {clock_mhz} MHz boost clock: {patched} patched, "
                f"{skipped} skipped."
            )
            log(msg)
            if hw.get("virt") is None:
                log(
                    "Note: DMA buffer not available \u2014 OD/metrics read-back "
                    "skipped (this does not affect the clock patch)."
                )
            return {
                "ok": True,
                "clock_mhz": clock_mhz,
                "patched_count": patched,
                "skipped_count": skipped,
                "message": msg,
            }
        finally:
            if hw:
                try:
                    engine.cleanup_hardware(hw)
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Read-only status / metrics
# ---------------------------------------------------------------------------

def read_status() -> Dict[str, Any]:
    """Return SMU state + DPM ranges for display (read-only)."""
    engine = _import_engine()
    with _hw_lock:
        hw = None
        try:
            try:
                hw = engine.init_hardware(skip_dma_discovery=True)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    "Could not initialise the GPU drivers. Make sure the "
                    "AMD driver and bundled kernel drivers are installed and "
                    "that the server is running as Administrator. See the "
                    "server console for details."
                ) from exc

            smu = hw["smu"]
            state = engine.query_smu_state(smu)
            dpm = engine.get_dpm_ranges(smu)
            return {
                "ok": True,
                "smu_version": state.get("smu_version"),
                "smu_drv_if": state.get("smu_drv_if"),
                "ppt_limit": state.get("smu_ppt"),
                "voltage": state.get("smu_voltage"),
                "dpm_ranges": dpm,
                "dma_available": hw.get("virt") is not None,
            }
        finally:
            if hw:
                try:
                    engine.cleanup_hardware(hw)
                except Exception:  # noqa: BLE001
                    pass


def read_metrics() -> Dict[str, Any]:
    """Return the flattened live SMU metrics dict (read-only)."""
    engine = _import_engine()
    with _hw_lock:
        hw = None
        try:
            try:
                hw = engine.init_hardware(skip_dma_discovery=True)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    "Could not initialise the GPU drivers. Make sure the "
                    "AMD driver and bundled kernel drivers are installed and "
                    "that the server is running as Administrator. See the "
                    "server console for details."
                ) from exc

            if hw.get("virt") is None:
                raise HardwareUnavailable(
                    "Live metrics need the GPU DMA buffer, which has not been "
                    "located yet. Run a DRAM scan in the desktop app to enable "
                    "metrics, then try again."
                )
            _m, values = engine.read_smu_metrics_full(hw["smu"], hw["virt"])
            if not values:
                raise HardwareUnavailable("Failed to read SMU metrics table.")
            return {"ok": True, "metrics": _jsonable(values)}
        finally:
            if hw:
                try:
                    engine.cleanup_hardware(hw)
                except Exception:  # noqa: BLE001
                    pass


def _jsonable(values: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce metric values to JSON-serialisable scalars."""
    out: Dict[str, Any] = {}
    for key, val in values.items():
        if isinstance(val, (int, float, str, bool)) or val is None:
            out[key] = val
        elif isinstance(val, (list, tuple)):
            out[key] = [v if isinstance(v, (int, float, str, bool)) else str(v) for v in val]
        else:
            out[key] = str(val)
    return out


def metrics_display_sections() -> List[Dict[str, Any]]:
    """Return the grouped metric layout used by the desktop app for display."""
    try:
        from src.app.constants import _METRICS_DISPLAY_SECTIONS
    except Exception:  # noqa: BLE001
        return []
    return [{"title": title, "keys": list(keys)} for title, keys in _METRICS_DISPLAY_SECTIONS]
