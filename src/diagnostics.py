"""
Adrenalift -- Environment Diagnostics
=====================================

Best-effort, side-effect-free environment snapshot intended to be written
once per session to the persistent log file (overclock_log.txt) so that
"nothing applied" bug reports come with enough context to be actionable.

Captured (best effort -- any individual section may be missing):

  * Adrenalift / Python / OS / arch / admin status
  * WinRing0 driver status: patched-vs-original, _has_phys_patch
  * InpOut32 availability
  * AMD display adapter(s): MatchingDeviceId, DriverDesc, DriverVersion
    (raw AMD 4-part driver version is also annotated with a best-guess
     Adrenalin marketing-year heuristic and a "this is recent, the
     cached PowerPlay table may be ignored" note when appropriate)
  * `amdkmdag.sys` file version (the actual kernel driver binary)
  * Cached PP-table address and DMA buffer offset from settings.json

Nothing in this module raises; every failure is swallowed and logged
as "<section>: unavailable (<reason>)" so a single missing piece of
data never blocks the rest of the snapshot.

The snapshot is idempotent within a process -- repeated calls after the
first are no-ops, so it is safe to call from every `init_hardware()`.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import threading
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# One-shot guard
# ---------------------------------------------------------------------------

_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_DONE = False


def reset_snapshot_guard() -> None:
    """Allow `log_environment_snapshot` to run again (test helper)."""
    global _SNAPSHOT_DONE
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_DONE = False


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # pragma: no cover - defensive
        return default if default is not None else f"<error: {e}>"


def _is_admin() -> Optional[bool]:
    if os.name != "nt":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def _file_version_string(path: str) -> Optional[str]:
    """Return the FileVersion of a Windows PE file (e.g. amdkmdag.sys).

    Uses the Version.dll API.  Returns None if anything fails.
    """
    if os.name != "nt" or not path or not os.path.exists(path):
        return None
    try:
        version = ctypes.windll.version  # type: ignore[attr-defined]
    except Exception:
        return None

    GetFileVersionInfoSizeW = version.GetFileVersionInfoSizeW
    GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    GetFileVersionInfoSizeW.restype = ctypes.c_ulong

    GetFileVersionInfoW = version.GetFileVersionInfoW
    GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong,
                                    ctypes.c_ulong, ctypes.c_void_p]
    GetFileVersionInfoW.restype = ctypes.c_int

    VerQueryValueW = version.VerQueryValueW
    VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                               ctypes.POINTER(ctypes.c_void_p),
                               ctypes.POINTER(ctypes.c_uint)]
    VerQueryValueW.restype = ctypes.c_int

    handle = ctypes.c_ulong(0)
    size = GetFileVersionInfoSizeW(path, ctypes.byref(handle))
    if size == 0:
        return None
    buf = ctypes.create_string_buffer(size)
    if not GetFileVersionInfoW(path, 0, size, buf):
        return None

    # Fixed file-info struct (\\) gives us the canonical four-part version
    block_ptr = ctypes.c_void_p()
    block_len = ctypes.c_uint()
    if not VerQueryValueW(buf, "\\",
                          ctypes.byref(block_ptr), ctypes.byref(block_len)):
        return None
    if not block_ptr.value or block_len.value < 52:
        return None

    # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS,
    # dwFileVersionLS at offsets 0/4/8/12 (uint32 each).
    ms = ctypes.c_uint.from_address(block_ptr.value + 8).value
    ls = ctypes.c_uint.from_address(block_ptr.value + 12).value
    return f"{(ms >> 16) & 0xFFFF}.{ms & 0xFFFF}.{(ls >> 16) & 0xFFFF}.{ls & 0xFFFF}"


def _amdkmdag_path() -> Optional[str]:
    """Best-effort path to the loaded amdkmdag.sys binary."""
    if os.name != "nt":
        return None
    candidates = [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                     "System32", "DriverStore", "FileRepository"),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                     "System32", "drivers", "amdkmdag.sys"),
    ]
    # Direct file (older driver layouts)
    direct = candidates[1]
    if os.path.exists(direct):
        return direct
    # DriverStore: scan u*.inf_amd64_* subdirs (newest mtime wins)
    store = candidates[0]
    if not os.path.isdir(store):
        return None
    best = None
    best_mtime = -1.0
    try:
        for entry in os.listdir(store):
            sub = os.path.join(store, entry)
            cand = os.path.join(sub, "amdkmdag.sys")
            if os.path.isfile(cand):
                try:
                    m = os.path.getmtime(cand)
                except OSError:
                    continue
                if m > best_mtime:
                    best_mtime = m
                    best = cand
    except OSError:
        return None
    return best


# AMD's driver version is "<edition>.<minor>.<build>.<revision>".  The 3rd
# segment ("build") is the most useful field for narrowing down Adrenalin
# releases.  These thresholds are deliberately coarse: we just want to flag
# "this is a recent driver, expect cached-PowerPlay-table patches to be
# unreliable" without pretending to know exact release dates.
def _annotate_amd_driver_version(ver: str) -> str:
    if not ver:
        return ""
    parts = ver.split(".")
    if len(parts) < 3:
        return ""
    try:
        build = int(parts[2])
    except ValueError:
        return ""
    # 24.x line moved more clock-policy into SMU firmware; cached PP table
    # patches are often silently ignored from this point on.
    if build >= 24000:
        return (" [recent driver: cached PowerPlay-table patches may be "
                "ignored by the SMU firmware; PPT-only knob is most reliable]")
    if build >= 22000:
        return " [mid-era driver: PP-table patch usually works]"
    return " [older driver]"


# ---------------------------------------------------------------------------
# Section collectors -- each returns a list[str] of log lines
# ---------------------------------------------------------------------------

def _section_basic() -> list[str]:
    lines = ["[diag] === Adrenalift environment snapshot ==="]
    try:
        from src.app.constants import APP_VERSION  # type: ignore
    except Exception:
        APP_VERSION = "?"
    lines.append(f"[diag] adrenalift_version : {APP_VERSION}")
    lines.append(f"[diag] python            : "
                 f"{sys.version.split()[0]} ({platform.architecture()[0]})")
    lines.append(f"[diag] platform          : {platform.platform()}")
    lines.append(f"[diag] machine           : {platform.machine()}")
    admin = _is_admin()
    lines.append(f"[diag] is_admin          : "
                 f"{'yes' if admin else 'no' if admin is False else 'unknown'}")
    lines.append(f"[diag] frozen            : {bool(getattr(sys, 'frozen', False))}")
    lines.append(f"[diag] executable        : {sys.executable}")
    return lines


def _section_winring0(inpout=None) -> list[str]:
    lines = ["[diag] --- WinRing0 / InpOut ---"]
    # Look for the driver files on disk first (independent of whether the
    # engine has loaded one yet).
    found = []
    for root_attr in ("_MEIPASS",):  # PyInstaller bundle dir
        d = getattr(sys, root_attr, None)
        if d:
            found.append(os.path.join(d, "drivers"))
    found.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "drivers"))
    if getattr(sys, "frozen", False):
        found.append(os.path.join(os.path.dirname(sys.executable), "drivers"))

    seen = set()
    patched_path = None
    original_path = None
    for d in found:
        d = os.path.normpath(d)
        if d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        p = os.path.join(d, "WinRing0x64_patched.sys")
        o = os.path.join(d, "WinRing0x64.sys")
        if patched_path is None and os.path.isfile(p):
            patched_path = p
        if original_path is None and os.path.isfile(o):
            original_path = o

    lines.append(f"[diag] winring0_patched_sys : "
                 f"{patched_path or 'NOT FOUND (physical memory reads will be capped at 1 MB)'}")
    lines.append(f"[diag] winring0_sys         : {original_path or 'NOT FOUND'}")

    # If a live InpOut32 was passed in, report which driver variant
    # actually loaded.
    if inpout is not None:
        try:
            loaded_kind = ("patched" if getattr(inpout, "_has_phys_patch", False)
                           else "original")
            lines.append(f"[diag] winring0_loaded      : {loaded_kind}")
            if loaded_kind == "original":
                lines.append("[diag]   WARNING: physical memory reads are "
                             "capped at 1 MB without the patched driver; "
                             "the PowerPlay-table scan will almost certainly "
                             "fail to locate the cached table.")
        except Exception:
            pass
    return lines


def _section_amd_adapters() -> list[str]:
    lines = ["[diag] --- AMD display adapter(s) ---"]
    try:
        from src.io.pptable_sources import enumerate_display_adapters
    except Exception as e:
        lines.append(f"[diag] adapter_enum unavailable: {e}")
        return lines

    adapters = enumerate_display_adapters() or ()
    amd_count = 0
    for a in adapters:
        provider = (a.get("ProviderName") or "")
        desc = (a.get("DriverDesc") or "")
        if "amd" not in provider.lower() and "amd" not in desc.lower() and \
           "radeon" not in desc.lower():
            continue
        amd_count += 1
        ver = a.get("DriverVersion", "?")
        annot = _annotate_amd_driver_version(ver) if isinstance(ver, str) else ""
        lines.append(f"[diag] adapter[{amd_count - 1}].desc          : {desc}")
        lines.append(f"[diag] adapter[{amd_count - 1}].driver_version: {ver}{annot}")
        mdi = a.get("MatchingDeviceId")
        if mdi:
            lines.append(f"[diag] adapter[{amd_count - 1}].device_id     : {mdi}")
    if amd_count == 0:
        lines.append("[diag] no AMD display adapters found in registry")

    # amdkmdag.sys file version (the actual loaded kernel binary)
    kmd_path = _amdkmdag_path()
    if kmd_path:
        kmd_ver = _file_version_string(kmd_path) or "<unreadable>"
        annot = _annotate_amd_driver_version(kmd_ver) if isinstance(kmd_ver, str) else ""
        lines.append(f"[diag] amdkmdag.sys path : {kmd_path}")
        lines.append(f"[diag] amdkmdag.sys ver  : {kmd_ver}{annot}")
    else:
        lines.append("[diag] amdkmdag.sys      : not found")
    return lines


def _section_caches() -> list[str]:
    lines = ["[diag] --- cached scan results ---"]
    try:
        from src.app.settings import settings as _settings  # type: ignore
    except Exception as e:
        lines.append(f"[diag] settings unavailable: {e}")
        return lines

    dma_off = _safe(lambda: _settings.get("dma_cache.offset"))
    dma_method = _safe(lambda: _settings.get("dma_cache.method"))
    pp_addr = _safe(lambda: _settings.get("pptable_cache.address"))
    pp_gpid = _safe(lambda: _settings.get("pptable_cache.golden_pp_id"))

    def _hexish(v):
        if isinstance(v, int):
            return f"0x{v:X}"
        return repr(v)

    lines.append(f"[diag] dma_cache.offset       : "
                 f"{_hexish(dma_off) if dma_off is not None else '(none)'} "
                 f"(method={dma_method!r})")
    lines.append(f"[diag] pptable_cache.address  : "
                 f"{_hexish(pp_addr) if pp_addr is not None else '(none)'} "
                 f"(golden_pp_id={pp_gpid!r})")
    return lines


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def log_environment_snapshot(log: Optional[Callable[[str], None]] = None,
                             *, force: bool = False, inpout=None) -> None:
    """Write a one-shot environment snapshot to the persistent log.

    Args:
        log:    Callable that accepts a single str (a log line).  If None,
                falls back to the project file-logger (`overclock` logger).
        force:  If True, snapshot is emitted even if it has been emitted
                already in this process.
        inpout: Optional live `InpOut32` instance; when provided we can
                report whether the patched WinRing0 driver actually loaded.
    """
    global _SNAPSHOT_DONE
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT_DONE and not force:
            return
        _SNAPSHOT_DONE = True

    if log is None:
        import logging
        _logger = logging.getLogger("overclock")
        def log(m):
            _logger.info(m)

    sections = [
        _section_basic,
        lambda: _section_winring0(inpout=inpout),
        _section_amd_adapters,
        _section_caches,
    ]
    for sec in sections:
        try:
            for line in sec():
                try:
                    log(line)
                except Exception:
                    pass
        except Exception as e:  # pragma: no cover - per-section safety net
            try:
                name = getattr(sec, "__name__", "lambda")
                log(f"[diag] section {name} failed: {e}")
            except Exception:
                pass
    try:
        log("[diag] === end snapshot ===")
    except Exception:
        pass
