"""Lightweight background-job manager for the Adrenalift web server.

The desktop GUI runs long operations (memory scan, apply) on ``QThread``
workers and reports progress through Qt signals.  The web server has no Qt
event loop, so this module provides an equivalent: jobs run on plain
``threading.Thread`` objects and expose their progress, streaming log lines,
final result, and error through a thread-safe :class:`Job` object that the
HTTP layer polls.

A job target is any callable accepting two keyword callbacks::

    def target(progress, log):
        progress(50, "halfway")
        log("did a thing")
        return {"some": "result"}

``progress(pct, msg)`` and ``log(msg)`` are safe to call from the worker
thread; the manager serialises access with a lock.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class Job:
    """Thread-safe container tracking a single background operation."""

    def __init__(self, name: str):
        self.id: str = uuid.uuid4().hex
        self.name: str = name
        self.status: str = "pending"  # pending | running | done | error
        self.progress: float = 0.0
        self.message: str = ""
        self.log: List[str] = []
        self.result: Any = None
        self.error: Optional[str] = None
        self.created_at: float = time.time()
        self.finished_at: Optional[float] = None
        self._lock = threading.Lock()

    # -- worker-side callbacks -------------------------------------------
    def set_progress(self, pct: float, msg: str = "") -> None:
        with self._lock:
            try:
                self.progress = max(0.0, min(100.0, float(pct)))
            except (TypeError, ValueError):
                pass
            if msg:
                self.message = msg
                self.log.append(msg)

    def add_log(self, msg: str) -> None:
        if msg is None:
            return
        with self._lock:
            self.message = str(msg)
            self.log.append(str(msg))

    # -- reader-side snapshot --------------------------------------------
    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        """Return a JSON-serialisable view, including log lines after *since*."""
        with self._lock:
            since = max(0, int(since or 0))
            return {
                "id": self.id,
                "name": self.name,
                "status": self.status,
                "progress": round(self.progress, 1),
                "message": self.message,
                "log": list(self.log[since:]),
                "log_total": len(self.log),
                "result": self.result if self.status == "done" else None,
                "error": self.error,
            }


class JobManager:
    """Owns and runs :class:`Job` instances on background threads."""

    def __init__(self, max_keep: int = 50):
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._max_keep = max_keep
        self._lock = threading.Lock()

    def submit(self, name: str, target: Callable[..., Any]) -> Job:
        """Create a job and start *target(progress=..., log=...)* in a thread."""
        job = Job(name)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._evict_locked()

        def _run() -> None:
            job.status = "running"
            try:
                result = target(progress=job.set_progress, log=job.add_log)
                # Mirror the desktop convention where an apply function may
                # return ``(False, "reason")`` to signal a handled failure.
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and result[0] is False
                ):
                    job.error = str(result[1])
                    job.status = "error"
                else:
                    job.result = result
                    job.status = "done"
            except Exception as exc:  # noqa: BLE001 - surface any failure to UI
                job.error = str(exc) or exc.__class__.__name__
                job.status = "error"
                job.add_log(f"Error: {job.error}")
            finally:
                job.finished_at = time.time()
                if job.progress < 100 and job.status == "done":
                    job.set_progress(100, "")

        threading.Thread(target=_run, name=f"job-{name}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs once we exceed *max_keep*."""
        while len(self._order) > self._max_keep:
            old_id = self._order.pop(0)
            old = self._jobs.get(old_id)
            if old is not None and old.status in ("pending", "running"):
                # Never evict an in-flight job; keep it and stop trimming.
                self._order.insert(0, old_id)
                break
            self._jobs.pop(old_id, None)
