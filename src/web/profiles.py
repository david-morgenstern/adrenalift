"""File-based tuning-profile store for the Adrenalift web console.

A *profile* is a named bundle of **volatile** tuning settings (power limit,
boost clock, OverDrive fields, PowerPlay-table field patches, etc.) that the
user can save, export, import, and re-apply.  Saving a profile writes a JSON
file to disk -- that is its whole purpose: it is a re-applyable *recipe*.

Crucially, profiles never contain the persistent registry tweaks (those live in
:mod:`src.web.registry_service` and are quarantined), so applying a profile only
ever changes volatile GPU/RAM/SMU state.  A reboot always reverts that state;
the saved profile just lets the user re-apply it afterwards.  There is
deliberately no "apply on startup" mechanism, which would break that guarantee.

Profiles are stored as ``<settings_dir>/profiles/<slug>.json`` next to the
executable (frozen) or in the project root (dev), reusing the same directory
resolution as :mod:`src.app.settings`.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# Sections a profile may carry.  All are volatile (reboot reverts them); the
# registry is intentionally absent.
_ALLOWED_KEYS = {
    "power_limit_w",
    "boost_clock_mhz",
    "gfx_offset_mhz",
    "od_ppt_pct",
    "od_fields",      # {od_field_key: number}
    "pp_fields",      # {offset_str: {"value": number, "type": "H"|"B"|"I"|"f"}}
    "freq_limits",    # {"gfx_min": int, "gfx_max": int}
    "smu_features",   # {"enable": [bit,...], "disable": [bit,...]}
}


def _profiles_dir() -> str:
    try:
        from src.app.settings import _SETTINGS_DIR
        base = _SETTINGS_DIR
    except Exception:  # noqa: BLE001 - fall back to CWD if settings import fails
        base = os.getcwd()
    return os.path.join(base, "profiles")


def _slug(name: str) -> str:
    """Turn a profile name into a safe filename stem (no path traversal)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    slug = slug.strip("._") or "profile"
    return slug[:64]


def _path_for(name: str) -> str:
    return os.path.join(_profiles_dir(), _slug(name) + ".json")


def sanitize_settings(raw: Any) -> Dict[str, Any]:
    """Keep only known, well-typed sections from a settings dict."""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key in _ALLOWED_KEYS:
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out


def validate(profile: Any) -> Optional[str]:
    """Return an error string if *profile* is not a usable profile, else None."""
    if not isinstance(profile, dict):
        return "Profile must be a JSON object."
    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        return "Profile needs a non-empty 'name'."
    settings = profile.get("settings")
    if not isinstance(settings, dict):
        return "Profile needs a 'settings' object."
    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_profiles() -> List[Dict[str, Any]]:
    """Return a summary list of saved profiles (name + metadata, no settings)."""
    d = _profiles_dir()
    if not os.path.isdir(d):
        return []
    out: List[Dict[str, Any]] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append(
                {
                    "name": data.get("name", fn[:-5]),
                    "slug": fn[:-5],
                    "created_at": data.get("created_at"),
                    "sections": sorted((data.get("settings") or {}).keys()),
                }
            )
        except Exception:  # noqa: BLE001 - skip unreadable files
            continue
    return out


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    path = _path_for(name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def save_profile(name: str, settings: Any) -> Dict[str, Any]:
    """Create or overwrite a profile, keeping only known volatile sections."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Profile name is required.")
    profile = {
        "schema": SCHEMA_VERSION,
        "name": name,
        "created_at": int(time.time()),
        "settings": sanitize_settings(settings),
    }
    os.makedirs(_profiles_dir(), exist_ok=True)
    with open(_path_for(name), "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    return profile


def delete_profile(name: str) -> bool:
    path = _path_for(name)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def import_profile(payload: Any) -> Dict[str, Any]:
    """Validate and store an imported profile JSON, returning the stored copy."""
    err = validate(payload)
    if err:
        raise ValueError(err)
    return save_profile(payload["name"], payload.get("settings"))
