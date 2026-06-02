# Adrenalift

**Unlock the real boost clock potential of your AMD GPU on Windows.**

Adrenalift is a Windows utility that bypasses artificial clock limits imposed by the AMD Windows display driver. It locates the driver's cached PowerPlay (PP) table in memory and patches the boost clock ceiling so your GPU can reach the frequencies it is actually capable of.

> **RDNA4** is the primary target. RDNA3 support is present in the code but has not been tested.

---

## The Problem

On Linux, users have full control over GPU clocks and power through the kernel's `pp_od_clk_voltage` sysfs interface. The open-source `amdgpu` driver exposes OverDrive knobs directly — you can raise the boost clock, adjust the power limit, and the hardware will comply up to its physical limits.

On Windows, the story is very different. The AMD display driver (`amdkmdag.sys`) enforces a **clock gating policy at the driver level**: even when the silicon can sustain higher frequencies, the driver's internal limits prevent the GPU from ever reaching them. The overclocking sliders exposed by the official software are constrained to a narrow range defined by the driver's cached copy of the PowerPlay table, not by the hardware itself. In practice this means your GPU may be leaving significant performance on the table — held back purely by software.

## How Adrenalift Works

1. **Scan** — the tool scans physical memory for the driver's cached PowerPlay table, locating the exact byte offsets that define the boost clock ceiling.
2. **Patch** — it writes new values directly into the driver's in-memory cache, raising (or lowering) the maximum allowed boost clock.
3. **Apply** — the patched limits take effect immediately. No reboot is required, and the changes are non-persistent: a reboot restores stock values.

Because the patch lives only in RAM, it is inherently safe to revert — just restart the machine.

> Other features (SMU OverDrive table, D3DKMTEscape path, registry tweaks, SPPT cache editing) are work-in-progress and may not work reliably for all configurations. They are documented within the application's UI.

---

## Warning

> **USE AT YOUR OWN RISK.**
>
> This tool writes directly to physical memory and communicates with the GPU's System Management Unit. Incorrect use can cause **driver crashes, blue screens (BSOD), display corruption, or — in extreme cases — hardware damage** from running outside manufacturer-validated operating parameters.
>
> - Overclocking may void your GPU warranty.
> - Always start with small increments above stock and test for stability.
> - The authors accept **no responsibility** for any damage to hardware or data.
> - **Administrator privileges are required.** The application's manifest requests elevation automatically.

---

## Requirements

- **Windows 10+** (64-bit)
- **AMD RDNA4 GPU** (RDNA3 untested)
- **Administrator privileges**
- A VBIOS dump (`bios/vbios.rom`) — the app will prompt you to supply one if not found

---

## Quick Start (pre-built)

1. Download the latest `Adrenalift_x.x_xx.exe` from releases.
2. Place your VBIOS ROM in the `bios/` folder next to the executable (or let the app prompt you).
3. Run the executable — it will request admin elevation.
4. Use the **Simple** tab to raise the boost clock and apply.

---

## Web server (browser front-end)

Adrenalift can also run as a **local web server** instead of the desktop GUI.
This is handy for headless setups, remote access over your LAN, or simply if
you prefer a browser. It shares the same overclock engine as the desktop app
and adds an extended, beginner-friendly help and tooltip system so regular
users can navigate the controls more easily.

```powershell
# from a source checkout, after `pip install -r requirements.txt`
python -m src.web
# or use the helper launcher on Windows:
.\web.bat
```

Then open <http://127.0.0.1:8770> in your browser.

The web console mirrors the desktop app's full feature set, organised into tabs:

- **Performance** — the settings that actually move performance on RDNA4, in
  one place: the **power limit (W)** (the most reliable win — sent straight to
  the firmware, no scan needed), a **GFX clock offset**, an advanced
  **OverDrive PPT %**, and a one-click safe power-limit template. Also hosts the
  **Profiles** bar (save / load / apply / import / export).
- **Boost Clock** — Scan memory, then choose a boost clock and Apply (the same
  Simple-tab workflow as the desktop app). Tick **Enable OverDrive & metrics
  (deep scan)** to also locate the GPU DMA buffer, which unlocks the OverDrive
  editor, GFX-offset / OD-PPT controls, and the live Metrics tab for the session.
- **OverDrive** — the full firmware OverDrive table: per-field clock offsets,
  voltage maxima, PPT/TDC, fan curve and temperature limits (needs a deep scan).
- **PowerPlay** — a per-field editor for every decoded field of the driver's
  cached PowerPlay table; setting a field patches it in RAM (needs a scan).
- **SMU** — GFX clock min/max limits and a power-saving lock (disable the
  idle/clock-gating features that cause downclocking).
- **Escape** — apply OD via the WDDM **D3DKMTEscape** path, which needs no
  Administrator privileges.
- **Metrics** — live GPU sensor readout (clocks, power, temps, fan, …).
- **Status** — live SMU state, power limit, and DPM clock ranges.
- **System** — the **persistent** registry/ULPS tweaks (see the caveat below).
- **Help** — the full set of in-app guides (start with *Performance settings
  that matter* and *Ephemeral by design*); every control also has a `?` button
  with a short tip and a plain-language description.

### Ephemeral by design

Everything except the **System** tab is **volatile**: the boost-clock patch,
OverDrive, PowerPlay, SMU and Escape changes all live in RAM / firmware runtime
state, so **a reboot returns the GPU to stock**. **Profiles** are saved JSON
recipes you can export/import and re-apply, but they only ever change that
volatile state — there is no "apply on startup", so the ephemeral guarantee
holds.

> **The System (registry) tab is the one exception:** those tweaks write to the
> Windows registry and **survive a reboot**. Adrenalift takes a backup before
> the first apply, offers a **Restore** button, and auto-restores on a clean
> server shutdown — but a hard crash or power loss will not auto-revert, so use
> Restore. Registry tweaks are deliberately kept out of profiles.

Options:

```text
python -m src.web --host 0.0.0.0 --port 8770   # expose on the LAN
```

> The hardware actions (scan/apply/status/metrics) require **Windows** with the
> AMD driver, the bundled kernel drivers, and **Administrator** privileges —
> the same as the desktop app. On other platforms (or without elevation) the
> server still starts and the UI and help remain fully browsable; hardware
> actions return a clear "engine unavailable" message instead of failing
> silently.
>
> By default the server binds to `127.0.0.1` (localhost only). Only use
> `--host 0.0.0.0` on a trusted network: anyone who can reach the port can
> drive the overclock controls.

---

## Building from Source

### Prerequisites

- **Python 3.10+**
- **pip** (ships with Python)

### Steps

1. **Install Python dependencies:**

```bash
pip install -r requirements.txt
```

2. **Clone external dependencies:**

```bash
cd deps
git clone https://github.com/sibradzic/upp.git
```

After cloning, the directory layout should look like:

```
adrenalift/
├── deps/
│   └── upp/          ← cloned repo (git-ignored)
│       └── src/
│           └── upp/
├── src/
├── build.spec
└── ...
```

3. **Place driver binaries** in `drivers/`:
   - `inpoutx64.dll`
   - `WinRing0x64.dll`
   - `WinRing0x64.sys`
   - `WinRing0x64_patched.sys` (optional — removes the 1 MB physical memory restriction)

   See **[DRIVERS.md](DRIVERS.md)** for details on each driver, what the patched version changes, and how to independently verify the patch with `python tools/verify_patch.py`.

4. **Build:**

```powershell
.\build.ps1
```

Or manually:

```bash
python -m PyInstaller --noconfirm build.spec
```

The output `.exe` is written to `dist/`.

### Building the web console exe

To ship the **browser front-end** as a standalone executable (instead of the
desktop GUI), use the web build scripts. The resulting exe starts the local web
server and opens your browser automatically; it bundles the HTML/CSS/JS assets
and Flask instead of Qt.

```powershell
.\build_web.ps1
```

Or manually:

```bash
python -m PyInstaller --noconfirm build_web.spec
```

The output `dist\Adrenalift_Web_x.x_xx.exe` requests admin elevation, starts the
server on <http://127.0.0.1:8770>, and opens the browser. The same driver files
in `drivers/` and (optional) `bios/vbios.rom` requirements apply as for the
desktop build.

### UPP (Uplift Power Play)

UPP provides RDNA3/RDNA4 PowerPlay table decoding (`upp.decode`, `upp.atom_gen`).

**Repository:** https://github.com/sibradzic/upp.git

- **Runtime:** `src/io/vbios_parser.py` and `src/tools/sppt_cache.py` add `deps/upp/src` to `sys.path` so `from upp import decode` resolves locally.
- **Build (PyInstaller):** `build.spec` adds the same path to `pathex` and lists UPP sub-modules in `hiddenimports` so the bundled `.exe` includes everything.
- **Fallback:** If `deps/upp` is missing the app still runs, but VBIOS parsing will be unavailable. A warning is printed at build time and at runtime.

To update UPP:

```bash
cd deps/upp
git pull
```

---

## Project Structure

```
src/
├── app/                 # PySide6 GUI, settings, background workers
│   ├── main.py          # Entry point, main window, tab layout
│   ├── workers.py       # QThread workers (scan, apply, metrics, etc.)
│   ├── settings.py      # Persistent settings (settings.json)
│   └── ...
├── web/                 # Browser front-end (Flask), shares the engine
│   ├── server.py        # Flask app, REST API, entry point (python -m src.web)
│   ├── hardware_service.py  # Qt-free engine wrapper (scan/apply/status/metrics)
│   ├── jobs.py          # Background job manager (progress + log streaming)
│   ├── tooltips.py      # Extended beginner-friendly tips & help pages
│   ├── templates/       # index.html
│   └── static/          # app.js, style.css
├── engine/              # Core overclock logic
│   ├── overclock_engine.py   # Scan, patch, apply, verify, watchdog
│   ├── od_table.py           # OverDrive table structures & controller
│   ├── smu.py                # SMU mailbox protocol & message IDs
│   └── smu_metrics.py        # GPU metrics parsing
├── io/                  # Hardware & OS interfaces
│   ├── mmio.py               # WinRing0 / InpOut physical memory & MMIO
│   ├── d3dkmt_escape.py      # D3DKMTEscape (WDDM) path
│   ├── vbios_parser.py       # VBIOS ROM parsing (stock values)
│   └── ...
└── tools/               # CLI tools & reverse-engineering utilities
    ├── overclock_cli.py       # Command-line interface
    ├── reg_patch.py           # Registry tweaks (ULPS, clock gating keys)
    ├── sppt_cache.py          # PP_PhmSoftPowerPlayTable builder
    └── ...                    # Frida scripts, Ghidra helpers, probes
```

---

## License

This project is licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE) for details.
