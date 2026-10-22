"""Bounded app-active reporting to help investigate sparse H1 location updates."""
from __future__ import annotations

from typing import Any

INTERVAL_SECONDS = 300.0


def report_if_due(owner: Any, now: float) -> None:
    """Run inside the existing serialized cloud poll, never in a second loop."""
    if getattr(owner, "_shutdown_complete", False):
        return
    status = getattr(owner, "_app_presence_status", None)
    if status is None:
        status = {"attempts": 0, "successes": 0, "last_attempt_mono": None, "last_success_mono": None}
        owner._app_presence_status = status
    previous = status["last_attempt_mono"]
    if previous is not None and now - previous < INTERVAL_SECONDS:
        return
    status["last_attempt_mono"] = now
    status["attempts"] += 1
    try:
        owner.client.app_active(owner.sn)
    except Exception:  # An optional presence failure must not fail normal polling.
        status["last_result"] = "failed"
    else:
        status["last_result"] = "success"
        status["successes"] += 1
        status["last_success_mono"] = now


def diagnostics(owner: Any, now: float) -> dict[str, Any]:
    """Expose outcome and ages without identity, coordinates or response bodies."""
    status = getattr(owner, "_app_presence_status", {})
    return {
        "interval_seconds": INTERVAL_SECONDS,
        "attempts": status.get("attempts", 0),
        "successes": status.get("successes", 0),
        "last_result": status.get("last_result", "not_sent"),
        "last_attempt_age_s": max(0.0, now - status["last_attempt_mono"]) if status.get("last_attempt_mono") is not None else None,
        "last_success_age_s": max(0.0, now - status["last_success_mono"]) if status.get("last_success_mono") is not None else None,
    }
