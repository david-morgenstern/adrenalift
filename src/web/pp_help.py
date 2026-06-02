"""Per-field descriptions and input hints for the PowerPlay-table editor.

The PowerPlay tab shows the *entire* decoded VBIOS PowerPlay table. That tree
mixes three very different kinds of field:

* **Structural / header fields** -- e.g. ``pmfw_pptable_start_offset`` (the byte
  offset where the firmware ``PPTable_t`` begins), ``pmfw_*_size``,
  ``table_revision``, ``golden_pp_id``, ``format_id``, ``reserve``. These
  describe the *layout* of the table itself. They are **not tunables**; editing
  them corrupts the table. We mark them read-only.
* **Firmware tuning fields** -- inside ``smc_pptable`` (SkuTable / BoardTable):
  clock tables, power/current/temperature limits, voltage and fan settings.
  Most are firmware-calibrated; only a few are meaningful to change.
* **Reserved / padding** -- spare bytes; never edit.

AMD does not publish a safe range for every field, so this module is honest
about what it can know:

* the **storage type** (authoritative, from the decoded struct) gives the hard
  numeric bounds (uint8 = 0-255, int16 = -32768..32767, float, ...);
* the **semantic unit / kind** (MHz, mV, degC, watts, amps, %, a 0/1 flag, an
  offset) is inferred from the field name -- best-effort, clearly hedged;
* a short **description** explains the field.

Nothing here touches hardware or Qt, so it is fully testable on any platform.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Storage type -> human label + hard numeric bounds
# ---------------------------------------------------------------------------
_TYPE_META: Dict[str, Tuple[str, Optional[int], Optional[int]]] = {
    "B": ("8-bit unsigned integer", 0, 255),
    "b": ("8-bit signed integer", -128, 127),
    "H": ("16-bit unsigned integer", 0, 65535),
    "h": ("16-bit signed integer", -32768, 32767),
    "I": ("32-bit unsigned integer", 0, 4294967295),
    "L": ("32-bit unsigned integer", 0, 4294967295),
    "i": ("32-bit signed integer", -2147483648, 2147483647),
    "l": ("32-bit signed integer", -2147483648, 2147483647),
    "f": ("32-bit float", None, None),
}


def type_label(type_code: str) -> str:
    return _TYPE_META.get(str(type_code), ("integer", None, None))[0]


def type_bounds(type_code: str) -> Tuple[Optional[int], Optional[int]]:
    meta = _TYPE_META.get(str(type_code), ("integer", None, None))
    return meta[1], meta[2]


# ---------------------------------------------------------------------------
# Curated descriptions for the structural / header fields (exact leaf names
# from struct_smu_14_0_2_powerplay_table). All read-only.
# ---------------------------------------------------------------------------
_STRUCTURAL = {
    "header": "PowerPlay table header (format, size, revision of the whole table).",
    "table_revision": "Revision number of this PowerPlay table layout.",
    "pptable_source": "Identifier of where this PP table came from.",
    "pmfw_pptable_start_offset": "Byte offset within the table where the firmware "
    "(PMFW/SMU) PPTable_t begins. A layout pointer, not a setting.",
    "pmfw_pptable_size": "Size in bytes of the firmware PPTable_t.",
    "pmfw_sku_table_start_offset": "Byte offset of the SkuTable inside the firmware table.",
    "pmfw_sku_table_size": "Size in bytes of the SkuTable.",
    "pmfw_board_table_start_offset": "Byte offset of the BoardTable inside the firmware table.",
    "pmfw_board_table_size": "Size in bytes of the BoardTable.",
    "pmfw_custom_sku_table_start_offset": "Byte offset of the CustomSkuTable.",
    "pmfw_custom_sku_table_size": "Size in bytes of the CustomSkuTable.",
    "golden_pp_id": "ID of the reference ('golden') PP table this one was derived from.",
    "golden_revision": "Revision of the reference ('golden') PP table.",
    "format_id": "PowerPlay table format identifier.",
    "version": "Struct version stamp.",
}

# Curated tunable-ish header fields (editable, with units).
_CURATED = {
    "platform_caps": ("Bitmask of platform capability flags.", "bitmask", False),
    "thermal_controller_type": ("Enum selecting the thermal controller hardware.", "enum", False),
    "small_power_limit1": ("Low power-limit hint.", "W", True),
    "small_power_limit2": ("Second low power-limit hint.", "W", True),
    "boost_power_limit": ("Boost power-limit hint.", "W", True),
    "software_shutdown_temp": ("Emergency shutdown temperature.", "degC", True),
}


# ---------------------------------------------------------------------------
# Token glossary for the deep firmware fields (checked in order; first match
# wins). Each entry: (regex, description, unit/kind, editable)
# ---------------------------------------------------------------------------
_UNIT_INPUT = {
    "MHz": "an integer frequency in MHz",
    "mV": "an integer voltage in millivolts (mV)",
    "degC": "an integer temperature in degrees Celsius",
    "W": "an integer power in watts",
    "A": "an integer current in amps",
    "%": "a percentage",
    "RPM": "fan speed in RPM",
    "PWM": "a fan PWM duty, 0-255",
    "flag": "a flag: 0 = off/disabled, 1 = on/enabled",
    "bitmask": "a bitmask (each bit is a separate on/off option)",
    "enum": "an enumerated code (specific integer values only)",
    "offset": "a signed offset (can be negative)",
    "count": "a whole-number count",
}

_TOKENS = [
    (r"padding|spare|reserve|reserved|crc|checksum", "Reserved / padding bytes.", "reserved", False),
    (r"foffset|f_offset", "Frequency offset applied to the clock.", "offset", True),
    (r"offset", "An offset value.", "offset", True),
    (r"(gfx|u|f|soc|dcf|disp|dpp|v|d|m)?clk|clock|freq|gfxclk|fclk|uclk|socclk|dclk|vclk|dispclk|dppclk|dcfclk|fmin|fmax",
     "A clock frequency.", "MHz", True),
    (r"loadline", "Load-line calibration resistance.", "enum", True),
    (r"vmax|vmin|voltage|volt|vdd|vddc|vddgfx|vddsoc|mvdd|svi|vid|vboot",
     "A voltage value.", "mV", True),
    (r"shutdown_temp|hotspot|tedge|tjmax|tj_|temperature|temp|tedge", "A temperature.", "degC", True),
    (r"fanpwm|minimumpwm|pwm", "Fan PWM duty.", "PWM", True),
    (r"acoustic|fanrpm|rpm|fantarget|zerorpm|fan", "A fan-control setting.", "RPM", True),
    (r"socketpower|boardpower|powerlimit|ppt|tbp|\bpower\b", "A power limit/target.", "W", True),
    (r"tdc|edc|currentlimit|\bcurrent\b", "A current limit.", "A", True),
    (r"percent|percentage|\bpct\b", "A percentage value.", "%", True),
    (r"enable|disable|_en$|ctrl|featurectrl|flag|\bmode\b", "An on/off / mode control.", "flag", True),
    (r"mask|bitmap|caps", "A bitmask of options.", "bitmask", False),
    (r"count|num[a-z]*|levels", "A count.", "count", False),
    (r"throttler|throttle", "A throttler limit or enable.", "enum", True),
    (r"id$|_id|revision|version|format|source|type$", "An identifier / type code.", "enum", False),
]


def _strip_index(name: str) -> str:
    return re.sub(r"\[\d+\]", "", name)


def describe(path: str, type_code: str = "H") -> Dict[str, object]:
    """Return {description, unit, editable, type_label, input_hint} for a field."""
    leaf = _strip_index(path.split(".")[-1])
    key = leaf.lower()
    plow = path.lower()
    tlabel = type_label(type_code)
    lo, hi = type_bounds(type_code)

    desc: str
    unit: Optional[str]
    editable: bool

    if leaf in _STRUCTURAL or key in _STRUCTURAL:
        desc = _STRUCTURAL.get(leaf) or _STRUCTURAL[key]
        unit, editable = "structural", False
    elif leaf in _CURATED or key in _CURATED:
        d, u, e = _CURATED.get(leaf) or _CURATED[key]
        desc, unit, editable = d, u, e
    else:
        desc, unit, editable = _heuristic(key, plow)

    return {
        "description": desc,
        "unit": unit,
        "editable": editable,
        "type_label": tlabel,
        "input_hint": _input_hint(unit, tlabel, lo, hi, editable, type_code),
    }


def _heuristic(key: str, plow: str) -> Tuple[str, str, bool]:
    # Group context refines a few cases.
    if "overdrivelimits" in plow:
        return ("An OverDrive allowed-range bound for this parameter (the min or "
                "max the firmware will accept).", _unit_from_key(key), True)
    if "freqtable" in plow:
        return ("A DPM clock-table frequency point.", "MHz", True)
    for pattern, desc, unit, editable in _TOKENS:
        if re.search(pattern, key):
            return desc, unit, editable
    return ("Firmware PowerPlay-table field (advanced; meaning not "
            "documented by AMD).", "raw", True)


def _unit_from_key(key: str) -> str:
    for pattern, _desc, unit, _e in _TOKENS:
        if re.search(pattern, key):
            return unit
    return "raw"


def _input_hint(unit, tlabel, lo, hi, editable, type_code) -> str:
    if not editable:
        if unit == "structural":
            return (f"Structural value ({tlabel}) — read-only. Changing it can "
                    "corrupt the table.")
        if unit == "reserved":
            return f"Reserved/padding ({tlabel}) — leave unchanged."
        return f"{tlabel} — read-only (identifier/bitmask)."
    bounds = ""
    if type_code != "f" and lo is not None:
        bounds = f" (stored as {tlabel}, {lo}–{hi})"
    elif type_code == "f":
        bounds = " (stored as a float)"
    unit_phrase = _UNIT_INPUT.get(unit, "a whole number")
    return f"Enter {unit_phrase}{bounds}."
