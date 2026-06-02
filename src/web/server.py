"""Flask web server exposing Adrenalift as a browser application.

Routes
------
``GET  /``                  -> single-page UI (templates/index.html)
``GET  /api/state``         -> engine availability, VBIOS info, last scan
``GET  /api/help``          -> long-form help pages
``GET  /api/tooltips``      -> short tips + descriptions for UI controls
``POST /api/scan``          -> start a background memory scan (returns job id)
``POST /api/apply/simple``  -> start a background boost-clock apply (job id)
``POST /api/apply/power_limit`` -> set the SMU power (PPT) limit in watts (job id)
``POST /api/apply/gfx_offset``  -> set the OverDrive GFX clock offset (job id)
``POST /api/apply/od_ppt``      -> set the OverDrive PPT percentage (job id)
``GET  /api/job/<id>``      -> poll a background job (progress + log + result)
``GET  /api/status``        -> read SMU state + DPM ranges (synchronous)
``GET  /api/metrics``       -> read live GPU metrics (synchronous)

Hardware actions are gated through :mod:`src.web.hardware_service`, which
degrades gracefully when the Windows-only engine is unavailable.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the project root is importable when run as ``python -m src.web``.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, jsonify, render_template, request

from src.web import hardware_service as hw
from src.web.jobs import JobManager
from src.web.tooltips import help_pages, tooltips

try:
    from src.app.constants import APP_BUILD, APP_VERSION
except Exception:  # noqa: BLE001 - version is cosmetic
    APP_VERSION, APP_BUILD = "?", "?"


def _resource_dir() -> str:
    """Directory that holds ``templates/`` and ``static/``.

    When frozen by PyInstaller the package source is unpacked under
    ``sys._MEIPASS`` rather than living next to this module, so Flask's default
    ``__name__``-based root detection finds nothing.  Resolve it explicitly so
    the bundled web exe serves its pages correctly.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "src", "web")
    return os.path.dirname(os.path.abspath(__file__))


def create_app() -> Flask:
    base = _resource_dir()
    app = Flask(
        __name__,
        template_folder=os.path.join(base, "templates"),
        static_folder=os.path.join(base, "static"),
    )
    jobs = JobManager()

    # -- pages -----------------------------------------------------------
    @app.route("/")
    def index():
        return render_template(
            "index.html",
            app_version=APP_VERSION,
            app_build=APP_BUILD,
        )

    # -- metadata --------------------------------------------------------
    @app.route("/api/state")
    def api_state():
        return jsonify(
            {
                "version": APP_VERSION,
                "build": APP_BUILD,
                "engine": hw.engine_status(),
                "vbios": hw.vbios_summary(),
                "last_scan": hw.last_scan_summary(),
            }
        )

    @app.route("/api/help")
    def api_help():
        return jsonify({"pages": help_pages()})

    @app.route("/api/tooltips")
    def api_tooltips():
        return jsonify({"tooltips": tooltips()})

    @app.route("/api/metrics/layout")
    def api_metrics_layout():
        return jsonify({"sections": hw.metrics_display_sections()})

    # -- background jobs -------------------------------------------------
    @app.route("/api/scan", methods=["POST"])
    def api_scan():
        data = request.get_json(silent=True) or {}
        try:
            num_threads = int(data.get("workers", 0) or 0)
        except (TypeError, ValueError):
            num_threads = 0
        deep_scan = bool(data.get("deep"))

        def target(progress, log):
            return hw.run_scan(
                num_threads=num_threads,
                deep_scan=deep_scan,
                progress=progress,
                log=log,
            )

        job = jobs.submit("scan", target)
        return jsonify({"job_id": job.id})

    @app.route("/api/apply/simple", methods=["POST"])
    def api_apply_simple():
        data = request.get_json(silent=True) or {}
        clock = data.get("clock")
        if clock is None:
            return jsonify({"error": "Missing 'clock' (MHz)."}), 400
        try:
            clock = int(clock)
        except (TypeError, ValueError):
            return jsonify({"error": "'clock' must be a whole number of MHz."}), 400
        if not (200 <= clock <= 6000):
            return jsonify({"error": "'clock' must be between 200 and 6000 MHz."}), 400

        def target(progress, log):
            return hw.apply_boost_clock(clock, progress=progress, log=log)

        job = jobs.submit("apply_simple", target)
        return jsonify({"job_id": job.id})

    @app.route("/api/apply/power_limit", methods=["POST"])
    def api_apply_power_limit():
        data = request.get_json(silent=True) or {}
        watts = data.get("watts")
        if watts is None:
            return jsonify({"error": "Missing 'watts'."}), 400
        try:
            watts = int(watts)
        except (TypeError, ValueError):
            return jsonify({"error": "'watts' must be a whole number."}), 400
        if not (hw.POWER_LIMIT_MIN_W <= watts <= hw.POWER_LIMIT_MAX_W):
            return (
                jsonify(
                    {
                        "error": (
                            f"'watts' must be between {hw.POWER_LIMIT_MIN_W} "
                            f"and {hw.POWER_LIMIT_MAX_W}."
                        )
                    }
                ),
                400,
            )

        def target(progress, log):
            return hw.set_power_limit(watts, progress=progress, log=log)

        job = jobs.submit("power_limit", target)
        return jsonify({"job_id": job.id})

    @app.route("/api/apply/gfx_offset", methods=["POST"])
    def api_apply_gfx_offset():
        data = request.get_json(silent=True) or {}
        offset = data.get("offset")
        if offset is None:
            return jsonify({"error": "Missing 'offset' (MHz)."}), 400
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return jsonify({"error": "'offset' must be a whole number of MHz."}), 400
        if not (-1000 <= offset <= 1000):
            return jsonify({"error": "'offset' must be between -1000 and 1000 MHz."}), 400

        def target(progress, log):
            return hw.apply_gfx_offset(offset, progress=progress, log=log)

        job = jobs.submit("gfx_offset", target)
        return jsonify({"job_id": job.id})

    @app.route("/api/apply/od_ppt", methods=["POST"])
    def api_apply_od_ppt():
        data = request.get_json(silent=True) or {}
        pct = data.get("pct")
        if pct is None:
            return jsonify({"error": "Missing 'pct' (percentage)."}), 400
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            return jsonify({"error": "'pct' must be a whole number."}), 400
        if not (-30 <= pct <= 30):
            return jsonify({"error": "'pct' must be between -30 and 30."}), 400

        def target(progress, log):
            return hw.apply_od_ppt(pct, progress=progress, log=log)

        job = jobs.submit("od_ppt", target)
        return jsonify({"job_id": job.id})

    @app.route("/api/job/<job_id>")
    def api_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Unknown job id."}), 404
        try:
            since = int(request.args.get("since", 0))
        except (TypeError, ValueError):
            since = 0
        return jsonify(job.snapshot(since=since))

    # -- synchronous read-only -------------------------------------------
    @app.route("/api/status")
    def api_status():
        try:
            return jsonify(hw.read_status())
        except hw.HardwareUnavailable as exc:
            return jsonify({"ok": False, "error": hw.safe_message(exc)}), 503

    @app.route("/api/metrics")
    def api_metrics():
        try:
            return jsonify(hw.read_metrics())
        except hw.HardwareUnavailable as exc:
            return jsonify({"ok": False, "error": hw.safe_message(exc)}), 503

    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.web",
        description="Run the Adrenalift web server.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1, localhost only).",
    )
    parser.add_argument(
        "--port", type=int, default=8770, help="Port to listen on (default: 8770)."
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable Flask debug mode."
    )
    args = parser.parse_args(argv)

    app = create_app()
    url = f"http://{args.host}:{args.port}"
    print(f"Adrenalift web server running at {url}")
    print("Open that address in your browser. Press Ctrl+C to stop.")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
