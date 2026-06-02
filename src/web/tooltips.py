"""Beginner-friendly tooltips and descriptions for the Adrenalift web UI.

The desktop GUI scatters short ``setToolTip(...)`` strings across many tab
modules and keeps long-form explanations in :mod:`src.app.help_texts`.  The web
front-end needs that guidance in one structured, data-driven place so it can be
rendered consistently (hover tooltips + expandable "Learn more" descriptions)
for users who are *not* GPU-overclocking experts.

This module therefore does two things:

1. Defines :data:`TOOLTIPS` -- a mapping of UI element ids to a short hover
   ``tip`` and a longer plain-language ``description``.  These intentionally
   re-use and *extend* the wording from the desktop tooltips so behaviour stays
   familiar while reading more clearly for regular users.

2. Re-exports the long-form HTML cheat-sheets from
   :mod:`src.app.help_texts` as a list of navigable help pages
   (:func:`help_pages`), each with a friendly summary line.

Nothing here imports Qt, so it is safe to use from the web server (and is fully
testable on any platform).
"""

from __future__ import annotations

from typing import Dict, List, TypedDict


class Tooltip(TypedDict):
    tip: str
    description: str


# ---------------------------------------------------------------------------
# Short hover tip + longer "Learn more" description for each web UI control.
# Keys are referenced by templates/static JS via ``data-help="<key>"``.
# ---------------------------------------------------------------------------

TOOLTIPS: Dict[str, Tooltip] = {
    # -- Global / workflow ------------------------------------------------
    "workflow": {
        "tip": "The usual order is: Scan \u2192 set a Boost Clock \u2192 Apply.",
        "description": (
            "Adrenalift raises the boost-clock ceiling that the AMD driver "
            "normally enforces. You generally follow three steps: first "
            "<b>Scan</b> so the tool can locate the driver's clock table in "
            "memory, then choose a <b>Boost Clock</b> target, then press "
            "<b>Apply</b>. Changes live only in RAM, so a reboot always "
            "restores the stock values."
        ),
    },
    "scan": {
        "tip": "Search system memory for the driver's PowerPlay clock table.",
        "description": (
            "Scanning looks through physical memory for the AMD driver's "
            "cached <b>PowerPlay (PP) table</b> \u2014 the table that holds "
            "the clock limits the driver is willing to enforce. The tool needs "
            "to know exactly where that table lives before it can patch it, so "
            "run a scan once per session (or after a driver reset). A scan can "
            "take from a few seconds to a minute depending on how much RAM is "
            "installed and how many worker threads you use."
        ),
    },
    "scan_workers": {
        "tip": "How many threads scan memory in parallel.",
        "description": (
            "More workers finish the memory scan faster but put a heavier "
            "load on your system while the scan runs. If your machine becomes "
            "unresponsive during a scan, lower this number. A value around the "
            "number of CPU cores is usually a good balance."
        ),
    },
    # -- Boost clock ------------------------------------------------------
    "boost_clock": {
        "tip": "Target maximum boost frequency, in MHz.",
        "description": (
            "This is the new ceiling you want the GPU to be allowed to reach. "
            "Start only slightly above your card's stock boost clock and test "
            "for stability before going higher \u2014 raising it too far can "
            "cause crashes, display corruption, or a driver reset. The value is "
            "written into the driver's cached clock table; the GPU will boost "
            "up to it when power and temperature allow, not constantly."
        ),
    },
    "apply_simple": {
        "tip": "Write the chosen Boost Clock into the driver's table and activate it.",
        "description": (
            "Apply does three things in order: (1) it overwrites the "
            "GameClock and BoostClock fields in the driver's in-RAM table with "
            "your value, (2) it tells the GPU firmware not to sleep so the new "
            "limit takes effect immediately, and (3) it nudges the driver to "
            "re-read its table so your value is pushed to the hardware right "
            "away. Nothing is written to disk, so the change is reverted by a "
            "reboot. A successful Scan is required first."
        ),
    },
    "save_default_clock": {
        "tip": "Remember this Boost Clock value for the next launch.",
        "description": (
            "Stores the current Boost Clock so it is pre-filled the next time "
            "you open Adrenalift. It does not apply the value \u2014 you still "
            "press Apply to activate it."
        ),
    },
    # -- Status / metrics -------------------------------------------------
    "status": {
        "tip": "Read live GPU firmware (SMU) state: clocks, power limit, features.",
        "description": (
            "Queries the GPU's System Management Unit (SMU) for its current "
            "minimum/maximum clock limits per domain, the active power (PPT) "
            "limit, voltage, and which firmware feature flags are enabled. Use "
            "it to confirm an overclock took effect or to inspect the card's "
            "current operating envelope. This is read-only and changes nothing."
        ),
    },
    "metrics": {
        "tip": "Live sensor readout: clocks, power, temperatures, fan, activity.",
        "description": (
            "Shows the GPU's real-time telemetry pulled straight from the SMU "
            "metrics table \u2014 current clocks, board power, temperatures, "
            "fan speed, utilisation, and throttling reasons. Reading metrics "
            "requires the DMA buffer to be available; if it isn't, run a DRAM "
            "scan in the desktop app first. This view is read-only."
        ),
    },
    "metrics_auto": {
        "tip": "Automatically refresh the live metrics every couple of seconds.",
        "description": (
            "When enabled, the metrics table re-reads the GPU sensors on a "
            "timer so you can watch values change under load. Turn it off to "
            "freeze the current snapshot or to reduce background SMU traffic."
        ),
    },
    # -- VBIOS ------------------------------------------------------------
    "vbios": {
        "tip": "Stock clock and power values read from your card's VBIOS ROM.",
        "description": (
            "Adrenalift reads a dump of your card's VBIOS to learn the factory "
            "default clocks and limits. These act as a baseline and a sanity "
            "check while scanning. If no valid ROM is found the tool falls "
            "back to built-in defaults, which still work but are less precise."
        ),
    },
}


# ---------------------------------------------------------------------------
# Long-form help pages (re-using the existing desktop cheat-sheets).
# ---------------------------------------------------------------------------

# Friendly one-line summaries shown in the help navigation list.  Order here
# defines the order the pages appear in the UI.
_HELP_PAGES_META: List[Dict[str, str]] = [
    {
        "id": "how_it_works",
        "title": "How Adrenalift works",
        "summary": "Start here \u2014 what scanning and applying actually do.",
        "const": "SIMPLE_HOW_IT_WORKS_HTML",
    },
    {
        "id": "status",
        "title": "Reading GPU status",
        "summary": "What the SMU version, clocks and limits mean.",
        "const": "STATUS_CHEATSHEET",
    },
    {
        "id": "clocks",
        "title": "Clocks explained",
        "summary": "GFX, memory, fabric and SoC clock domains.",
        "const": "CLOCK_CHEATSHEET",
    },
    {
        "id": "metrics",
        "title": "Live metrics glossary",
        "summary": "Every sensor in the metrics readout, in plain terms.",
        "const": "METRICS_CHEATSHEET",
    },
    {
        "id": "controls",
        "title": "Advanced controls",
        "summary": "OverDrive offsets, power and temperature limits.",
        "const": "CONTROLS_CHEATSHEET",
    },
    {
        "id": "features",
        "title": "SMU feature flags",
        "summary": "What the firmware feature bits do.",
        "const": "FEATURES_CHEATSHEET",
    },
    {
        "id": "throttlers",
        "title": "Throttlers",
        "summary": "Why the GPU holds back, and the throttler mask.",
        "const": "THROTTLERS_CHEATSHEET",
    },
    {
        "id": "pp",
        "title": "PowerPlay table editor",
        "summary": "The full PP table field editor (power users).",
        "const": "PP_HELP_HTML",
    },
    {
        "id": "registry",
        "title": "Registry tweaks",
        "summary": "ULPS and clock-gating registry keys.",
        "const": "REG_CHEATSHEET_HTML",
    },
]


def _load_help_html() -> Dict[str, str]:
    """Return ``{const_name: html}`` from :mod:`src.app.help_texts`.

    Imported lazily and defensively: a missing constant simply yields an empty
    string so the help page degrades gracefully instead of breaking the UI.
    """
    try:
        from src.app import help_texts
    except Exception:  # pragma: no cover - only if help_texts import breaks
        return {}
    out: Dict[str, str] = {}
    for meta in _HELP_PAGES_META:
        out[meta["const"]] = getattr(help_texts, meta["const"], "")
    return out


def help_pages() -> List[Dict[str, str]]:
    """Return navigable help pages with friendly summaries and HTML bodies."""
    html_by_const = _load_help_html()
    pages: List[Dict[str, str]] = []
    for meta in _HELP_PAGES_META:
        pages.append(
            {
                "id": meta["id"],
                "title": meta["title"],
                "summary": meta["summary"],
                "html": html_by_const.get(meta["const"], ""),
            }
        )
    return pages


def tooltips() -> Dict[str, Tooltip]:
    """Return the full tooltip / description map."""
    return TOOLTIPS
