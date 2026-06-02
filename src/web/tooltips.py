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
    "deep_scan": {
        "tip": "Also locate the GPU DMA buffer to unlock OverDrive + live metrics.",
        "description": (
            "A normal scan only finds the PowerPlay table, which is all the "
            "Boost Clock patch needs. Tick <b>deep scan</b> to additionally "
            "locate the driver's GPU DMA buffer. That unlocks the OverDrive "
            "controls (GFX clock offset, OD PPT%) and the live Metrics tab for "
            "the rest of the session. It is slower (it probes the GPU memory "
            "aperture, which can take 30 seconds or more) and is only needed "
            "once per session — the location is then remembered until you "
            "close the server."
        ),
    },
    # -- Performance tab --------------------------------------------------
    "performance": {
        "tip": "The handful of settings that actually move performance on RDNA4.",
        "description": (
            "This tab gathers the knobs that matter for real-world gains and "
            "leaves the experimental ones elsewhere. On RDNA4 the most reliable "
            "win is a small <b>power-limit</b> bump; the boost-clock ceiling and "
            "GFX offset help on some cards but the driver/firmware often clamps "
            "them back. Make one change at a time and test stability before "
            "stacking another."
        ),
    },
    "power_limit": {
        "tip": "GPU package-power (PPT) limit in watts — the knob that reliably sticks.",
        "description": (
            "Raises or lowers the total board power the GPU is allowed to draw, "
            "sent straight to the firmware (it does not need a deep scan). A "
            "higher limit lets the card hold its boost clock longer under load, "
            "which is usually the single most effective change on RDNA4. Stay "
            "close to your card's stock limit: many AIB models already ship a "
            "little higher than reference, so a modest nudge (for example a "
            "reference 330&nbsp;W card up to ~340&nbsp;W) is a safe, well-trodden "
            "increase. Large jumps raise heat and power draw — watch "
            "temperatures on the Metrics tab."
        ),
    },
    "power_template": {
        "tip": "Apply the recommended safe power-limit bump.",
        "description": (
            "Fills in and applies a conservative power limit that is known to "
            "be safe on most RDNA4 boards (a small step above a 330&nbsp;W "
            "reference limit, the level several higher-tier AIB cards already "
            "ship at). It is a starting point, not a maximum — verify "
            "stability and temperatures, then adjust to taste."
        ),
    },
    "gfx_offset": {
        "tip": "Shift the GFX clock up or down by a fixed MHz offset (OverDrive).",
        "description": (
            "Applies a frequency offset to the graphics clock through the "
            "OverDrive table. A positive offset asks for higher clocks at a "
            "given voltage; a negative one undervolts-by-frequency for cooler, "
            "quieter operation. This needs a <b>deep scan</b> first so the DMA "
            "buffer is available. RDNA4 firmware may ignore or clamp large "
            "offsets — start with a small value (e.g. +25–100&nbsp;MHz) "
            "and confirm it took effect on the Status/Metrics tabs."
        ),
    },
    "od_ppt": {
        "tip": "Power limit as a percentage over default (OverDrive PPT).",
        "description": (
            "The OverDrive-table way to express a power-limit change, as a "
            "percentage above (or below) the card's default rather than an "
            "absolute watt figure. Prefer the absolute <b>Power limit (W)</b> "
            "control unless you specifically want a percentage. Needs a deep "
            "scan first."
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
# A page entry uses either ``const`` (pulled from src.app.help_texts) or
# ``html`` (inline, defined here for web-only pages such as Performance).
PERFORMANCE_HELP_HTML = """
<h3>Settings that actually matter for performance</h3>
<p>Adrenalift exposes a lot of knobs, but on RDNA4 only a few make a
dependable difference. Here is what to reach for, in order.</p>

<h4>1. Power limit (PPT) &mdash; the reliable one</h4>
<p>The graphics driver and SMU firmware aggressively clamp clocks back to
their idea of "allowed", so raising a clock ceiling often does nothing visible.
What the firmware <i>does</i> honour is the <b>power limit</b>. Giving the card
a little more power budget lets it sustain its boost clock longer under load,
which is the most consistent real-world gain. Many partner cards (Red Devil,
Taichi, etc.) already ship above the reference limit, so nudging a reference
330&nbsp;W board to about <b>340&nbsp;W</b> is a safe, validated step that
other brands allow out of the box. Use the <b>Power limit (W)</b> control (or
the one-click safe template).</p>

<h4>2. Boost clock ceiling</h4>
<p>The original feature: patch the driver's cached PowerPlay table so the GPU
is <i>allowed</i> to reach a higher boost clock. On some cards this helps; on
many RDNA4 cards the firmware still clamps the result. Harmless to try &mdash;
scan, set a value slightly above stock, apply, and check whether clocks under
load actually rise.</p>

<h4>3. GFX clock offset (OverDrive)</h4>
<p>A fixed MHz shift applied through the OverDrive table. A small positive
offset can squeeze out a little more frequency; a small negative offset acts
as a frequency-based undervolt for lower temperatures and noise. Requires a
<b>deep scan</b> so the DMA buffer is available, and large offsets are often
ignored &mdash; keep it modest.</p>

<h4>A safe starting template</h4>
<ul>
  <li><b>Power limit:</b> stock + ~10&nbsp;W (e.g. 330&nbsp;&rarr;&nbsp;340&nbsp;W).</li>
  <li><b>Boost clock:</b> optional, +50\u2013100&nbsp;MHz over stock, only if it
      measurably helps.</li>
  <li><b>GFX offset:</b> 0 to start; try +25\u201350&nbsp;MHz once the above is
      stable.</li>
</ul>
<p>Change one thing at a time, run a real workload, and watch temperatures and
the throttling readout on the <b>Metrics</b> tab. Everything here lives only in
RAM &mdash; a reboot restores stock values.</p>
"""

_HELP_PAGES_META: List[Dict[str, str]] = [
    {
        "id": "performance",
        "title": "Performance settings that matter",
        "summary": "Start here \u2014 the few knobs that actually help on RDNA4.",
        "html": PERFORMANCE_HELP_HTML,
    },
    {
        "id": "how_it_works",
        "title": "How Adrenalift works",
        "summary": "What scanning and applying actually do.",
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
        const = meta.get("const")
        if const:
            out[const] = getattr(help_texts, const, "")
    return out


def help_pages() -> List[Dict[str, str]]:
    """Return navigable help pages with friendly summaries and HTML bodies."""
    html_by_const = _load_help_html()
    pages: List[Dict[str, str]] = []
    for meta in _HELP_PAGES_META:
        # Inline ``html`` (web-only pages) wins; otherwise pull from help_texts.
        body = meta.get("html") or html_by_const.get(meta.get("const", ""), "")
        pages.append(
            {
                "id": meta["id"],
                "title": meta["title"],
                "summary": meta["summary"],
                "html": body,
            }
        )
    return pages


def tooltips() -> Dict[str, Tooltip]:
    """Return the full tooltip / description map."""
    return TOOLTIPS
