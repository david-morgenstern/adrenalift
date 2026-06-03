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
    """Raised when the overclock engine / GPU hardware cannot be used.

    Carries a stable ``code`` identifying the failure category.  The HTTP layer
    maps that code to a fixed, human-authored message via :func:`safe_message`
    so that **no exception-derived text is ever returned to the client** (the
    raw exception detail is only ever written to the server log).
    """

    def __init__(self, message: str, code: str = "generic"):
        super().__init__(message)
        self.code = code


# Fixed, human-authored messages keyed by failure code.  These are the only
# strings sent to the browser for hardware errors; they never contain stack
# traces or raw exception text.
ERROR_MESSAGES: Dict[str, str] = {
    "engine_unavailable": (
        "The overclock engine is unavailable on this system. Hardware actions "
        "require Windows with the AMD driver, the bundled kernel drivers, and "
        "Administrator privileges. See the server console for details."
    ),
    "init_failed": (
        "Could not initialise the GPU drivers. Make sure the AMD driver and "
        "bundled kernel drivers are installed and that the server is running "
        "as Administrator. See the server console for details."
    ),
    "no_scan": (
        "No scan result available. Run a Scan first so Adrenalift knows where "
        "the driver's clock table lives."
    ),
    "dma_unavailable": (
        "This action needs the GPU DMA buffer, which has not been located yet. "
        "Run a Scan with 'Enable OverDrive & metrics (deep scan)' ticked first, "
        "then try again."
    ),
    "metrics_failed": "Failed to read the SMU metrics table.",
    "power_limit_failed": (
        "The firmware rejected the power-limit change. Try a value closer to "
        "your card's stock limit. See the server console for details."
    ),
    "od_failed": (
        "The firmware rejected the OverDrive change. The value may be outside "
        "the range your card accepts. See the server console for details."
    ),
    "pp_failed": (
        "Could not patch the PowerPlay-table field in memory. Re-run a Scan and "
        "try again. See the server console for details."
    ),
    "escape_failed": (
        "The D3DKMTEscape OD8 write was rejected by the driver. See the server "
        "console for details."
    ),
    "generic": "The requested hardware action could not be completed.",
}


def safe_message(exc: "HardwareUnavailable") -> str:
    """Return a fixed, safe-to-display message for a hardware error.

    The returned value is a controlled literal selected by the exception's
    ``code``; it is never derived from the underlying exception text.
    """
    code = getattr(exc, "code", "generic")
    if code in ERROR_MESSAGES:
        return ERROR_MESSAGES[code]
    return ERROR_MESSAGES["generic"]


# Serialise all hardware access: the underlying drivers and SMU mailbox are a
# single shared resource and must not be entered concurrently.
_hw_lock = threading.Lock()

# Process-persistent hardware handle.  Created once on first use and reused by
# every operation (scan / apply / status / metrics) so WinRing0 + InpOut are
# *not* re-loaded and the driver service *not* re-installed on every call.
# That per-call churn -- a fresh WinRing0() each time, each copying the .sys and
# re-registering the WinRing0_1_2_0 service -- is what produced the WinError 32
# sharing-violation interlock.  Torn down once, in shutdown_hardware().
_engine_hw = None
_degraded_reason: Optional[str] = None


def _acquire_hw(engine, *, want_dma: bool = False, discover: bool = False,
                gui_log: LogFn = None):
    """Return the process-persistent hardware handle, creating it on first use.

    The caller MUST hold ``_hw_lock``.  Never tears the handle down.  Raises on
    initial-load failure (the handle is left uncached so the next call retries).

    Args:
        want_dma: also ensure the driver DMA buffer is mapped (needed for OD /
            metrics).  Uses a cached offset; runs a full BAR scan only when
            *discover* is True.
        discover: allow the (slow) DMA BAR scan.  Only the deep scan sets this;
            read paths leave it False so they fail fast instead of blocking.
    """
    global _engine_hw, _degraded_reason
    if _engine_hw is None:
        hw = engine.init_hardware(skip_dma_discovery=True, gui_log=gui_log)
        _engine_hw = hw
        # No WinRing0 => the engine fell back to InpOut32-only mode: the patched
        # physical-memory path is gone, so scan / boost-clock patching will be
        # unreliable.  Record it so the UI can warn instead of failing silently.
        if hw.get("wr0") is None:
            _degraded_reason = (
                "Limited mode: the WinRing0 physical-memory driver could not be "
                "loaded, so memory scan and boost-clock patching are unreliable. "
                "Close other monitoring / overclocking tools (HWiNFO, MSI "
                "Afterburner / RivaTuner, GPU-Z, ZenTimings) and relaunch "
                "Adrenalift. The power-limit control still works."
            )
        else:
            _degraded_reason = None
    if want_dma and _engine_hw.get("virt") is None:
        engine.ensure_dma_buffer(_engine_hw, gui_log=gui_log, discover=discover)
    return _engine_hw


def degraded_reason() -> Optional[str]:
    """Human-readable reason the engine is running limited, or None.

    Only meaningful once the hardware handle has been created at least once
    (i.e. after the first scan / apply / status call this session).
    """
    with _hw_lock:
        return _degraded_reason


def shutdown_hardware() -> None:
    """Release the persistent hardware handle (call once on server shutdown)."""
    global _engine_hw, _degraded_reason
    with _hw_lock:
        hw, _engine_hw = _engine_hw, None
        _degraded_reason = None
    if hw is None:
        return
    try:
        engine = _import_engine()
        engine.cleanup_hardware(hw)
    except Exception:  # noqa: BLE001
        pass

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
            ERROR_MESSAGES["engine_unavailable"], code="engine_unavailable"
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
    except HardwareUnavailable:
        # Use the fixed message, never the exception text.
        info["reason"] = ERROR_MESSAGES["engine_unavailable"]
    return info


# ---------------------------------------------------------------------------
# VBIOS summary (read-only, used for the info banner)
# ---------------------------------------------------------------------------

def vbios_summary() -> Dict[str, Any]:
    """Return a short description of the detected VBIOS values (or defaults)."""
    try:
        from src.app.constants import DEFAULT_VBIOS_PATH, _get_vbios_values
    except Exception as exc:  # noqa: BLE001
        _log.warning("VBIOS constants import failed: %s", exc)
        return {"available": False, "summary": "VBIOS information is unavailable."}

    try:
        vals = _get_vbios_values(DEFAULT_VBIOS_PATH)
    except Exception as exc:  # noqa: BLE001
        _log.warning("VBIOS parse failed: %s", exc)
        return {"available": False, "summary": "Could not parse the VBIOS ROM."}

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
    deep_scan: bool = False,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Scan physical memory for the driver's PowerPlay table.

    Returns a JSON-serialisable summary and caches it for a later Apply.
    Raises :class:`HardwareUnavailable` on any hardware/engine failure.

    When *deep_scan* is true the (slower) DMA-buffer discovery runs as well,
    which unlocks the OverDrive controls (GFX clock offset, OD PPT) and live
    metrics for the rest of the session.  The discovered offset is cached in
    memory so later OverDrive applies reuse it without re-scanning.
    """
    engine = _import_engine()

    with _hw_lock:
        vbios = _get_vbios_values_or_defaults()

        hw = None
        try:
            try:
                hw = _acquire_hw(
                    engine,
                    want_dma=deep_scan,
                    discover=deep_scan,
                    gui_log=(log if deep_scan else None),
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
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
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
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
        raise HardwareUnavailable(ERROR_MESSAGES["no_scan"], code="no_scan")

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
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
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
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# Power limit (SMU SetPptLimit -- works without the DMA buffer)
# ---------------------------------------------------------------------------

# Conservative absolute clamp for the power-limit control.  This is a guard
# rail, not a recommendation: stay close to your card's stock limit and only
# nudge it up a little (see the Performance-tab help).
POWER_LIMIT_MIN_W = 100
POWER_LIMIT_MAX_W = 400


def set_power_limit(
    watts: int,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Set the GPU package-power (PPT) limit in watts via the SMU mailbox.

    This is the single knob that reliably sticks on RDNA4: it sends
    ``SetPptLimit`` directly to the firmware and does **not** need the DMA
    buffer, so it works straight after launch without a deep scan.
    """
    engine = _import_engine()
    try:
        watts = int(watts)
    except (TypeError, ValueError) as exc:
        raise ValueError("Power limit must be a whole number of watts.") from exc
    if not (POWER_LIMIT_MIN_W <= watts <= POWER_LIMIT_MAX_W):
        raise ValueError(
            f"Power limit must be between {POWER_LIMIT_MIN_W} and "
            f"{POWER_LIMIT_MAX_W} W."
        )

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc

            smu = hw["smu"]
            progress(20, f"Reading current power limit…")
            try:
                before = smu.get_ppt_limit()
            except Exception:  # noqa: BLE001 - read-back is best-effort
                before = None

            progress(50, f"Setting power limit to {watts} W…")
            try:
                smu.set_ppt_limit(watts)
            except Exception as exc:  # noqa: BLE001
                _log.warning("set_ppt_limit failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["power_limit_failed"],
                    code="power_limit_failed",
                ) from exc

            try:
                after = smu.get_ppt_limit()
            except Exception:  # noqa: BLE001
                after = None

            progress(100, "Power limit applied.")
            msg = f"Power limit set to {watts} W"
            if after is not None:
                msg += f" (firmware now reports {after} W)"
            msg += "."
            log(msg)
            return {
                "ok": True,
                "requested_w": watts,
                "before_w": before,
                "after_w": after,
                "message": msg,
            }
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# OverDrive controls (GFX clock offset, OD PPT %) -- need the DMA buffer,
# i.e. a prior deep scan in this process so the offset is cached.
# ---------------------------------------------------------------------------

def _require_dma(engine):
    """Persistent handle with the DMA buffer mapped from a cached offset.

    Raises ``dma_unavailable`` if no deep scan has located the buffer this
    session (never blocks on a full BAR scan -- that's the deep scan's job).
    """
    hw = _acquire_hw(engine, want_dma=True, discover=False)
    if hw.get("virt") is None:
        raise HardwareUnavailable(
            ERROR_MESSAGES["dma_unavailable"], code="dma_unavailable"
        )
    return hw


def apply_gfx_offset(
    offset_mhz: int,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Apply a GFX clock frequency offset (MHz) through the OverDrive table.

    Requires a prior deep scan (so the DMA buffer offset is cached).
    """
    engine = _import_engine()
    try:
        offset_mhz = int(offset_mhz)
    except (TypeError, ValueError) as exc:
        raise ValueError("GFX offset must be a whole number of MHz.") from exc
    if not (-1000 <= offset_mhz <= 1000):
        raise ValueError("GFX offset must be between -1000 and +1000 MHz.")

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _require_dma(engine)
            except HardwareUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc

            progress(40, f"Applying GFX offset {offset_mhz:+d} MHz…")

            def _modify(od):
                od.FeatureCtrlMask |= (1 << engine.PP_OD_FEATURE_GFXCLK_BIT)
                od.GfxclkFoffset = offset_mhz

            ok, err = engine.apply_od_single_field(hw["smu"], hw["virt"], _modify)
            if not ok:
                _log.warning("apply GFX offset failed: %s", err)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["od_failed"], code="od_failed"
                )
            progress(100, "GFX offset applied.")
            msg = f"GFX clock offset set to {offset_mhz:+d} MHz."
            log(msg)
            return {"ok": True, "offset_mhz": offset_mhz, "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


def apply_od_ppt(
    pct: int,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Apply an OverDrive PPT percentage (power-limit % over default).

    Requires a prior deep scan (DMA buffer cached).  This is the OverDrive-table
    equivalent of the absolute :func:`set_power_limit` knob; prefer the absolute
    watt control unless you specifically want a percentage offset.
    """
    engine = _import_engine()
    try:
        pct = int(pct)
    except (TypeError, ValueError) as exc:
        raise ValueError("OD PPT must be a whole-number percentage.") from exc
    if not (-30 <= pct <= 30):
        raise ValueError("OD PPT must be between -30% and +30%.")

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _require_dma(engine)
            except HardwareUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc

            progress(40, f"Applying OD PPT {pct:+d}%…")

            def _modify(od):
                od.FeatureCtrlMask |= (1 << engine.PP_OD_FEATURE_PPT_BIT)
                od.Ppt = pct

            ok, err = engine.apply_od_single_field(hw["smu"], hw["virt"], _modify)
            if not ok:
                _log.warning("apply OD PPT failed: %s", err)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["od_failed"], code="od_failed"
                )
            progress(100, "OD PPT applied.")
            msg = f"OverDrive PPT set to {pct:+d}% over default."
            log(msg)
            return {"ok": True, "pct": pct, "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# OverDrive table -- full per-field editor (mirrors src/app/tab_od.py)
# ---------------------------------------------------------------------------

# Each scalar field: (key/attr, group, unit, feature-bit constant name, label).
# Array fields are expanded per-index at runtime.  Bit names are resolved against
# src.engine.od_table so we don't hard-code their numeric values here.
_OD_SCALAR_FIELDS = [
    ("GfxclkFoffset", "Frequency", "MHz", "PP_OD_FEATURE_GFXCLK_BIT", "GFX clock offset"),
    ("UclkFmin", "Frequency", "MHz", "PP_OD_FEATURE_UCLK_BIT", "Memory (UCLK) min"),
    ("UclkFmax", "Frequency", "MHz", "PP_OD_FEATURE_UCLK_BIT", "Memory (UCLK) max"),
    ("FclkFmin", "Frequency", "MHz", "PP_OD_FEATURE_FCLK_BIT", "Fabric (FCLK) min"),
    ("FclkFmax", "Frequency", "MHz", "PP_OD_FEATURE_FCLK_BIT", "Fabric (FCLK) max"),
    ("Ppt", "Power", "%", "PP_OD_FEATURE_PPT_BIT", "Power limit (PPT) %"),
    ("Tdc", "Power", "%", "PP_OD_FEATURE_TDC_BIT", "Current limit (TDC) %"),
    ("GfxEdc", "Power", "", "PP_OD_FEATURE_EDC_BIT", "GFX EDC"),
    ("GfxPccLimitControl", "Power", "", "PP_OD_FEATURE_EDC_BIT", "GFX PCC limit"),
    ("VddGfxVmax", "Voltage", "mV", "PP_OD_FEATURE_GFX_VMAX_BIT", "VddGfx Vmax"),
    ("VddSocVmax", "Voltage", "mV", "PP_OD_FEATURE_SOC_VMAX_BIT", "VddSoc Vmax"),
    ("GfxclkFmaxVmax", "Voltage", "MHz", "PP_OD_FEATURE_GFX_VMAX_BIT", "GFX clock Fmax@Vmax"),
    ("MaxOpTemp", "Limits", "C", "PP_OD_FEATURE_TEMPERATURE_BIT", "Max operating temp"),
    ("FanTargetTemperature", "Fan", "C", "PP_OD_FEATURE_FAN_CURVE_BIT", "Fan target temp"),
    ("FanMinimumPwm", "Fan", "", "PP_OD_FEATURE_FAN_CURVE_BIT", "Fan minimum PWM"),
    ("AcousticTargetRpmThreshold", "Fan", "RPM", "PP_OD_FEATURE_FAN_CURVE_BIT", "Acoustic target RPM"),
    ("AcousticLimitRpmThreshold", "Fan", "RPM", "PP_OD_FEATURE_FAN_CURVE_BIT", "Acoustic limit RPM"),
    ("FanMode", "Fan", "", "PP_OD_FEATURE_FAN_CURVE_BIT", "Fan mode (0=auto)"),
    ("FanZeroRpmEnable", "Fan", "", "PP_OD_FEATURE_ZERO_FAN_BIT", "Fan zero-RPM enable"),
    ("FanZeroRpmStopTemp", "Fan", "C", "PP_OD_FEATURE_ZERO_FAN_BIT", "Fan zero-RPM stop temp"),
]

# Array fields: (attr, group, unit, bit-name, label-prefix, count-constant-name).
_OD_ARRAY_FIELDS = [
    ("VoltageOffsetPerZoneBoundary", "Voltage", "mV", "PP_OD_FEATURE_GFX_VF_CURVE_BIT",
     "V/F zone", "PP_NUM_OD_VF_CURVE_POINTS"),
    ("FanLinearPwmPoints", "Fan", "", "PP_OD_FEATURE_FAN_CURVE_BIT",
     "Fan PWM point", "NUM_OD_FAN_MAX_POINTS"),
    ("FanLinearTempPoints", "Fan", "C", "PP_OD_FEATURE_FAN_CURVE_BIT",
     "Fan temp point", "NUM_OD_FAN_MAX_POINTS"),
]


def _od_specs(od_table):
    """Build the full OD field spec list, expanding arrays per index."""
    specs = []
    for attr, group, unit, bit, label in _OD_SCALAR_FIELDS:
        if hasattr(od_table.OverDriveTable_t, attr):
            specs.append({"key": attr, "attr": attr, "index": None, "group": group,
                          "unit": unit, "bit": bit, "label": label})
    for attr, group, unit, bit, prefix, count_name in _OD_ARRAY_FIELDS:
        count = getattr(od_table, count_name, 0)
        for i in range(count):
            specs.append({"key": f"{attr}_{i}", "attr": attr, "index": i, "group": group,
                          "unit": unit, "bit": bit, "label": f"{prefix} {i}"})
    return specs


def od_field_layout() -> List[Dict[str, Any]]:
    """Return the OD field spec (no hardware needed) for the UI to render."""
    try:
        from src.engine import od_table
    except Exception:  # noqa: BLE001
        return []
    return [
        {"key": s["key"], "label": s["label"], "unit": s["unit"], "group": s["group"]}
        for s in _od_specs(od_table)
    ]


def read_od_fields() -> Dict[str, Any]:
    """Read the live OverDrive table and return current values per field."""
    engine = _import_engine()
    from src.engine import od_table
    with _hw_lock:
        hw = None
        try:
            try:
                hw = _require_dma(engine)
            except HardwareUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc
            od = engine.read_od(hw["smu"], hw["virt"])
            if od is None:
                raise HardwareUnavailable(ERROR_MESSAGES["od_failed"], code="od_failed")
            values = {}
            for s in _od_specs(od_table):
                try:
                    if s["index"] is None:
                        values[s["key"]] = int(getattr(od, s["attr"]))
                    else:
                        values[s["key"]] = int(getattr(od, s["attr"])[s["index"]])
                except Exception:  # noqa: BLE001
                    values[s["key"]] = None
            return {"ok": True, "values": values}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


def apply_od_field(
    key: str,
    value: int,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Set a single OverDrive table field by key. Requires a deep scan."""
    engine = _import_engine()
    from src.engine import od_table
    spec = next((s for s in _od_specs(od_table) if s["key"] == key), None)
    if spec is None:
        raise ValueError("Unknown OD field.")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OD value must be a whole number.") from exc
    bit = getattr(od_table, spec["bit"])

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _require_dma(engine)
            except HardwareUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc

            progress(40, f"Setting {spec['label']} = {value}…")

            def _modify(od):
                od.FeatureCtrlMask |= (1 << bit)
                if spec["index"] is None:
                    setattr(od, spec["attr"], value)
                else:
                    getattr(od, spec["attr"])[spec["index"]] = value

            ok, err = engine.apply_od_single_field(hw["smu"], hw["virt"], _modify)
            if not ok:
                _log.warning("apply OD field %s failed: %s", key, err)
                raise HardwareUnavailable(ERROR_MESSAGES["od_failed"], code="od_failed")
            progress(100, "Applied.")
            msg = f"OverDrive: {spec['label']} set to {value}{(' ' + spec['unit']) if spec['unit'] else ''}."
            log(msg)
            return {"ok": True, "key": key, "value": value, "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# PowerPlay table -- full field editor (mirrors src/app/tab_pp.py)
# ---------------------------------------------------------------------------

def _flatten_pp_tree(node, prefix, baseclock_off, out):
    """Walk the decoded PP tree, collecting editable leaves (value+offset)."""
    if isinstance(node, dict):
        if "value" in node and "offset" in node:
            try:
                raw_off = int(node.get("offset", -1))
            except (TypeError, ValueError):
                raw_off = -1
            if raw_off >= 0:
                tcode = str(node.get("type", "H"))
                from src.web import pp_help
                info = pp_help.describe(prefix, tcode)
                out.append({
                    "path": prefix,
                    "offset": raw_off - baseclock_off,
                    "type": tcode,
                    "vbios_value": node.get("value"),
                    "description": info["description"],
                    "input_hint": info["input_hint"],
                    "editable": info["editable"],
                    "type_label": info["type_label"],
                })
            return
        for k, child in node.items():
            child_path = f"{prefix}.{k}" if prefix else str(k)
            _flatten_pp_tree(child, child_path, baseclock_off, out)
    elif isinstance(node, (list, tuple)):
        for i, child in enumerate(node):
            _flatten_pp_tree(child, f"{prefix}[{i}]", baseclock_off, out)


def pp_field_layout() -> Dict[str, Any]:
    """Decode the VBIOS PP table into a flat, editable field list."""
    try:
        from src.app.constants import DEFAULT_VBIOS_PATH
        from src.io.vbios_storage import read_vbios_decoded
        from src.io.vbios_parser import decode_pp_table_full
    except Exception as exc:  # noqa: BLE001
        _log.warning("PP decode imports failed: %s", exc)
        return {"available": False, "reason": "PP decoding is unavailable.", "fields": []}

    rom_bytes, _ = read_vbios_decoded(DEFAULT_VBIOS_PATH)
    if not rom_bytes:
        return {
            "available": False,
            "reason": "A VBIOS ROM (bios/vbios.rom) is required to edit PP-table fields.",
            "fields": [],
        }
    decoded = decode_pp_table_full(rom_bytes, rom_path=DEFAULT_VBIOS_PATH)
    if decoded is None or getattr(decoded, "data", None) is None:
        return {"available": False, "reason": "Could not decode the PP table.", "fields": []}

    vbios = _get_vbios_values_or_defaults_quiet()
    baseclock_off = int(getattr(vbios, "baseclock_pp_offset", 0) or 0) if vbios else 0
    fields: List[Dict[str, Any]] = []
    _flatten_pp_tree(decoded.data, "", baseclock_off, fields)
    return {"available": True, "fields": fields}


def _get_vbios_values_or_defaults_quiet():
    try:
        return _get_vbios_values_or_defaults()
    except Exception:  # noqa: BLE001
        return None


def apply_pp_field(
    offset: int,
    value,
    type_code: str = "H",
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Patch a single PP-table field across all scanned RAM copies.

    Requires a prior successful scan (the volatile RAM PP-table addresses).
    """
    engine = _import_engine()
    scan_result = _get_cached_result()
    if not (scan_result and getattr(scan_result, "valid_addrs", None)):
        raise HardwareUnavailable(ERROR_MESSAGES["no_scan"], code="no_scan")

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc
            progress(40, f"Patching PP field @0x{int(offset):X}…")
            res = engine.patch_pp_single_field(
                hw["inpout"], scan_result, int(offset), value, str(type_code)
            )
            if not res.get("ok"):
                raise HardwareUnavailable(ERROR_MESSAGES["pp_failed"], code="pp_failed")
            progress(100, "Patched.")
            msg = (
                f"PP field @0x{int(offset):X} set to {value} "
                f"({res.get('writes', 0)}/{res.get('addrs', 0)} copies)."
            )
            log(msg)
            return {"ok": True, "offset": int(offset), "value": value,
                    "writes": res.get("writes", 0), "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# SMU controls -- GFX clock limits, power-saving lock (all DMA-free)
# ---------------------------------------------------------------------------

def apply_freq_limits(
    gfx_min: int = 0,
    gfx_max: int = 0,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Set the GFX clock soft/hard min and/or max (MHz) via the SMU. DMA-free."""
    engine = _import_engine()
    gfx_min = int(gfx_min or 0)
    gfx_max = int(gfx_max or 0)
    if gfx_min and not (200 <= gfx_min <= 4000):
        raise ValueError("GFX min must be between 200 and 4000 MHz.")
    if gfx_max and not (200 <= gfx_max <= 4000):
        raise ValueError("GFX max must be between 200 and 4000 MHz.")
    if gfx_min and gfx_max and gfx_min > gfx_max:
        raise ValueError("GFX min cannot exceed GFX max.")

    with _hw_lock:
        hw = None
        try:
            try:
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc
            smu = hw["smu"]
            gfx = engine.PPCLK.GFXCLK & 0xFFFF
            done = []
            if gfx_max:
                p = (gfx << 16) | (gfx_max & 0xFFFF)
                smu.send_msg(engine.PPSMC.SetSoftMaxByFreq, p)
                smu.send_msg(engine.PPSMC.SetHardMaxByFreq, p)
                done.append(f"max={gfx_max} MHz")
            if gfx_min:
                p = (gfx << 16) | (gfx_min & 0xFFFF)
                smu.send_msg(engine.PPSMC.SetSoftMinByFreq, p)
                smu.send_msg(engine.PPSMC.SetHardMinByFreq, p)
                done.append(f"min={gfx_min} MHz")
            if not done:
                return {"ok": True, "message": "No GFX clock limits changed."}
            progress(100, "Applied.")
            msg = "GFX clock limits set: " + ", ".join(done) + "."
            log(msg)
            return {"ok": True, "gfx_min": gfx_min, "gfx_max": gfx_max, "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


def apply_power_saving_lock(
    lock: bool = True,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Disable (lock) or re-allow the GFX power-saving features that cause
    clock-gating/idle downclock (DS_GFXCLK, GFX_ULV, GFXOFF). DMA-free."""
    engine = _import_engine()
    with _hw_lock:
        hw = None
        try:
            try:
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc
            smu = hw["smu"]
            feat_mask = ((1 << engine.SMU_FEATURE.DS_GFXCLK) |
                         (1 << engine.SMU_FEATURE.GFX_ULV) |
                         (1 << engine.SMU_FEATURE.GFXOFF))
            if lock:
                smu.send_msg(engine.PPSMC.DisallowGfxOff)
                smu.send_msg(engine.PPSMC.DisableSmuFeaturesLow, feat_mask)
                msg = "Power-saving features locked (DS_GFXCLK / GFX_ULV / GFXOFF disabled)."
            else:
                smu.send_msg(engine.PPSMC.EnableSmuFeaturesLow, feat_mask)
                smu.send_msg(engine.PPSMC.AllowGfxOff)
                msg = "Power-saving features re-enabled (back to stock idle behaviour)."
            progress(100, "Applied.")
            log(msg)
            return {"ok": True, "locked": bool(lock), "message": msg}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


# ---------------------------------------------------------------------------
# D3DKMTEscape OD8 path (no admin) -- mirrors src/app/tab_escape.py
# ---------------------------------------------------------------------------

def apply_escape(
    clock_mhz: int = 0,
    power_w: int = 0,
    gfx_offset_mhz: int = 0,
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Apply OD settings through the D3DKMTEscape path (no admin required).

    Only the high-level knobs that map cleanly to OD8 entries are exposed
    (clock ceiling, power limit, GFX offset); fields left at 0 are unchanged.
    """
    engine = _import_engine()
    clock_mhz = int(clock_mhz or 0)
    power_w = int(power_w or 0)
    gfx_offset_mhz = int(gfx_offset_mhz or 0)
    if not (clock_mhz or power_w or gfx_offset_mhz):
        raise ValueError("Set at least one of clock, power, or GFX offset.")

    settings = engine.OverclockSettings(
        clock=clock_mhz or 0,
        power=power_w or 0,
        offset=gfx_offset_mhz or 0,
        od_ppt=0,
        od_tdc=0,
    )
    progress(40, "Sending OD8 settings via D3DKMTEscape…")
    result = engine.apply_od_via_escape(settings)
    if not result.get("ok"):
        _log.warning("escape apply failed: %s", result.get("error"))
        raise HardwareUnavailable(ERROR_MESSAGES["escape_failed"], code="escape_failed")
    progress(100, "Applied.")
    changed = result.get("changed_indices", [])
    msg = f"Escape OD8 applied ({len(changed)} value(s) changed)."
    log(msg)
    return {
        "ok": True,
        "changed_indices": list(changed),
        "verified": _jsonable(result.get("verified", {}) or {}),
        "message": msg,
    }


# ---------------------------------------------------------------------------
# Profile apply -- orchestrates the volatile sections in dependency order
# ---------------------------------------------------------------------------

def apply_profile(
    settings: Dict[str, Any],
    *,
    progress: ProgressFn = _noop_progress,
    log: LogFn = _noop_log,
) -> Dict[str, Any]:
    """Apply a saved profile's volatile sections in a safe order.

    Reuses the public single-action functions (each manages its own hardware
    handle + lock), so this never holds the hardware lock itself.  Registry
    settings are never part of a profile, so nothing here is persistent.
    """
    settings = settings or {}
    needs_dma = any(k in settings for k in ("gfx_offset_mhz", "od_ppt_pct", "od_fields"))
    needs_scan = needs_dma or any(k in settings for k in ("boost_clock_mhz", "pp_fields"))

    results: List[Dict[str, Any]] = []

    def _step(name, fn):
        try:
            r = fn()
            results.append({"step": name, "ok": True, "message": r.get("message", "done")})
            log(f"[{name}] {r.get('message', 'done')}")
        except Exception as exc:  # noqa: BLE001 - per-step, keep going
            detail = safe_message(exc) if isinstance(exc, HardwareUnavailable) else str(exc)
            results.append({"step": name, "ok": False, "message": detail})
            log(f"[{name}] FAILED: {detail}")

    # 1) Scan if needed and not already cached.
    if needs_scan and _get_cached_result() is None:
        progress(5, "Scanning memory for the profile…")
        _step("scan", lambda: run_scan(deep_scan=needs_dma, progress=progress, log=log))

    # 2) Apply sections (order: power -> clocks/pp -> OD -> features).
    if "power_limit_w" in settings:
        progress(30, "Power limit…")
        _step("power_limit", lambda: set_power_limit(settings["power_limit_w"], progress=progress, log=log))
    if "boost_clock_mhz" in settings:
        progress(45, "Boost clock…")
        _step("boost_clock", lambda: apply_boost_clock(settings["boost_clock_mhz"], progress=progress, log=log))
    for off, spec in (settings.get("pp_fields") or {}).items():
        _step(f"pp@{off}", lambda off=off, spec=spec: apply_pp_field(
            int(off), spec.get("value"), spec.get("type", "H"), progress=progress, log=log))
    if "gfx_offset_mhz" in settings:
        progress(65, "GFX offset…")
        _step("gfx_offset", lambda: apply_gfx_offset(settings["gfx_offset_mhz"], progress=progress, log=log))
    if "od_ppt_pct" in settings:
        progress(75, "OD PPT…")
        _step("od_ppt", lambda: apply_od_ppt(settings["od_ppt_pct"], progress=progress, log=log))
    for key, val in (settings.get("od_fields") or {}).items():
        _step(f"od.{key}", lambda key=key, val=val: apply_od_field(key, val, progress=progress, log=log))
    fl = settings.get("freq_limits") or {}
    if fl.get("gfx_min") or fl.get("gfx_max"):
        progress(85, "GFX clock limits…")
        _step("freq_limits", lambda: apply_freq_limits(
            fl.get("gfx_min", 0), fl.get("gfx_max", 0), progress=progress, log=log))

    ok = all(r["ok"] for r in results) if results else False
    applied = sum(1 for r in results if r["ok"])
    progress(100, "Profile applied.")
    return {
        "ok": ok,
        "steps": results,
        "message": f"Profile applied: {applied}/{len(results)} step(s) succeeded.",
    }


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
                hw = _acquire_hw(engine)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
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
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
            pass


def read_metrics() -> Dict[str, Any]:
    """Return the flattened live SMU metrics dict (read-only)."""
    engine = _import_engine()
    with _hw_lock:
        hw = None
        try:
            try:
                hw = _acquire_hw(engine, want_dma=True, discover=False)
            except Exception as exc:  # noqa: BLE001
                _log.warning("init_hardware failed: %s", exc)
                raise HardwareUnavailable(
                    ERROR_MESSAGES["init_failed"], code="init_failed"
                ) from exc

            if hw.get("virt") is None:
                raise HardwareUnavailable(
                    ERROR_MESSAGES["dma_unavailable"], code="dma_unavailable"
                )
            _m, values = engine.read_smu_metrics_full(hw["smu"], hw["virt"])
            if not values:
                raise HardwareUnavailable(
                    ERROR_MESSAGES["metrics_failed"], code="metrics_failed"
                )
            return {"ok": True, "metrics": _jsonable(values)}
        finally:
            # Persistent handle: not torn down per call (see _acquire_hw /
            # shutdown_hardware). The lock release below ends the critical
            # section.
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
