"""Backend-owned decision model for continuing an interrupted mowing task."""
from __future__ import annotations

from typing import Any

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    MAP_EDIT_STATES,
    STATE_PAUSED,
)

RESUME_STRATEGY_ORDERED_RUN = "ordered_run"
RESUME_STRATEGY_VENDOR = "vendor"
_RESUMABLE_VENDOR_PAUSED_STATES = {STATE_PAUSED, "0221"}


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _zone_ids(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        try:
            zone_id = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            continue
        if zone_id > 0 and zone_id not in result:
            result.append(zone_id)
    return result


def _base_result(
    *,
    available: bool,
    strategy: str | None,
    reason: str,
    activity: str | None,
    state_code: str | None,
    task_progress_pct: float | None,
    task_zone_ids: list[int],
    ordered_run: dict[str, Any] | None,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "available": available,
        "strategy": strategy,
        "reason": reason,
        "activity": activity,
        "state_code": state_code,
        "task_progress_pct": task_progress_pct,
        "task_zone_ids": task_zone_ids,
        "ordered_run_resumable": (
            ordered_run.get("resumable") if isinstance(ordered_run, dict) else None
        ),
        "ordered_remaining_zone_ids": (
            _zone_ids(ordered_run.get("remaining_zone_ids"))
            if isinstance(ordered_run, dict)
            else []
        ),
        "evidence": evidence,
    }


def guard_managed_schedule_resume(
    decision: dict[str, Any],
    schedule: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prevent a generic Resume action from bypassing the managed scheduler.

    When Navimower Schedule is enabled it is the owner of task continuation.
    The user can disable Schedule first when an explicit vendor Resume override
    is desired.
    """
    if not isinstance(schedule, dict) or schedule.get("enabled") is not True:
        return decision
    if decision.get("available") is not True:
        return decision

    guarded = dict(decision)
    guarded["available"] = False
    guarded["strategy"] = None
    guarded["reason"] = "managed_schedule_active"
    evidence = list(guarded.get("evidence") or [])
    if "managed_schedule_controls_resume" not in evidence:
        evidence.append("managed_schedule_controls_resume")
    guarded["evidence"] = evidence
    return guarded


def task_resume_decision(
    data: dict[str, Any] | None,
    *,
    active_session: dict[str, Any] | None = None,
    retained_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return whether and how an interrupted task can safely be continued.

    The decision is deliberately backend-owned. ordered_run means the
    integration can safely re-send only unfinished zones in their retained
    order with reset=false. vendor means the mower's own retained task
    should be resumed through the dedicated vendor Resume command.
    """
    snapshot = data or {}
    state_code = str(snapshot.get("state_code") or "").strip() or None
    activity = str(snapshot.get("activity") or "").strip().lower() or None
    docked = snapshot.get("docked") is True

    totals = snapshot.get("totals") or {}
    total_task_zone_ids = _zone_ids(totals.get("task_zone_ids"))
    active = active_session if isinstance(active_session, dict) else None
    retained = retained_session if isinstance(retained_session, dict) else None
    session = active or retained
    session_zone_ids = _zone_ids((session or {}).get("zone_ids"))
    task_zone_ids = session_zone_ids or total_task_zone_ids

    progress = _as_float(snapshot.get("mowing_progress"))
    if progress is None:
        progress = _as_float(snapshot.get("task_progress"))

    ordered_run = (
        snapshot.get("last_ordered_run")
        if isinstance(snapshot.get("last_ordered_run"), dict)
        else None
    )
    evidence: list[str] = []

    session_complete = False
    if session is not None:
        confirmed = set(_zone_ids(session.get("task_zone_completion_confirmed")))
        session_complete = bool(
            session.get("completed") is True
            or (
                bool(session_zone_ids)
                and set(session_zone_ids).issubset(confirmed)
            )
        )
        session_kind = "active_session" if active is not None else "retained_session"
        evidence.append(
            f"{session_kind}_{'complete' if session_complete else 'incomplete'}"
        )

    task_context = (
        bool(task_zone_ids)
        or bool(snapshot.get("active_cycle_id"))
        or isinstance(snapshot.get("last_ordered_run"), dict)
    )

    if state_code in MAP_EDIT_STATES:
        return _base_result(
            available=False,
            strategy=None,
            reason="map_edit",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=[*evidence, "map_edit_state"],
        )

    if activity == ACTIVITY_MOWING:
        return _base_result(
            available=False,
            strategy=None,
            reason="already_mowing",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=[*evidence, "activity_mowing"],
        )

    if session_complete:
        return _base_result(
            available=False,
            strategy=None,
            reason="task_complete",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=evidence,
        )

    if progress is not None and progress >= 100.0 and task_context:
        return _base_result(
            available=False,
            strategy=None,
            reason="task_complete",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=[*evidence, "task_progress_complete"],
        )

    ordered_resumable = bool(
        ordered_run
        and ordered_run.get("resumable") is True
        and not ordered_run.get("complete")
        and not ordered_run.get("superseded_at")
    )
    ordered_zone_ids = _zone_ids((ordered_run or {}).get("zone_ids"))
    ordered_matches_task = bool(
        task_zone_ids
        and ordered_zone_ids
        and set(task_zone_ids).issubset(set(ordered_zone_ids))
    )
    if ordered_resumable and ordered_matches_task:
        return _base_result(
            available=True,
            strategy=RESUME_STRATEGY_ORDERED_RUN,
            reason="ordered_run_remaining",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=[*evidence, "last_ordered_run_resumable"],
        )
    if ordered_resumable and not ordered_matches_task:
        evidence.append("ordered_run_unconfirmed_for_current_task")

    if state_code in _RESUMABLE_VENDOR_PAUSED_STATES or activity == ACTIVITY_PAUSED:
        evidence.append("paused_retained_task")
        return _base_result(
            available=True,
            strategy=RESUME_STRATEGY_VENDOR,
            reason="vendor_paused_task",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=evidence,
        )

    resumable_context = activity in {
        ACTIVITY_RETURNING,
        ACTIVITY_ERROR,
        ACTIVITY_DOCKED,
    } or docked

    if session is not None and not session_complete and resumable_context:
        return _base_result(
            available=True,
            strategy=RESUME_STRATEGY_VENDOR,
            reason="vendor_active_session",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=evidence,
        )

    if (
        progress is not None
        and 0.0 <= progress < 100.0
        and task_context
        and resumable_context
    ):
        evidence.append("task_progress_incomplete")
        return _base_result(
            available=True,
            strategy=RESUME_STRATEGY_VENDOR,
            reason="vendor_incomplete_task",
            activity=activity,
            state_code=state_code,
            task_progress_pct=progress,
            task_zone_ids=task_zone_ids,
            ordered_run=ordered_run,
            evidence=evidence,
        )

    return _base_result(
        available=False,
        strategy=None,
        reason="no_resumable_task_evidence",
        activity=activity,
        state_code=state_code,
        task_progress_pct=progress,
        task_zone_ids=task_zone_ids,
        ordered_run=ordered_run,
        evidence=evidence,
    )


__all__ = [
    "RESUME_STRATEGY_ORDERED_RUN",
    "RESUME_STRATEGY_VENDOR",
    "guard_managed_schedule_resume",
    "task_resume_decision",
]
