"""Beta16 runtime corrections derived from live H215 state/error captures.

This module deliberately keeps the beta15 diagnostic capture intact while
promoting only field observations that are now repeated/proven enough for
public entity semantics:

* private state 0103 is Idle (not docked),
* official MQTT numeric vehicleState=3 is a coarse stopped state and must not
  imply docked/charging by itself,
* private state 0301 is the generic numeric-fault state,
* index2.error_data is the authoritative source for active numeric fault code,
  title and content,
* official MQTT named state Error is an early trigger for an immediate index2
  refresh; Self-Checking / leaving Error triggers a clear-confirmation refresh.

Lifted remains a separate safety state (0302) unless a real numeric error is
reported in error_data. No 180D/6007 mapping is invented here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from . import const as _const
from . import coordinator as _coordinator
from .beta17_runtime import install_beta17_runtime
from .beta18_runtime import install_beta18_runtime

_STATE_IDLE = "0103"
_STATE_FAULT = "0301"
_STATE_LIFTED = "0302"
_MQTT_STOPPED = 3


def _first_error(raw: dict[str, Any]) -> dict[str, Any] | None:
    index2 = raw.get("index2") or {}
    errors = (
        index2.get("error_data")
        or index2.get("errorData")
        or index2.get("error_list")
        or []
    )
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return None
    first = errors[0]
    code = first.get("error_code") or first.get("errorCode") or first.get("code")
    title = first.get("title") or first.get("desc") or first.get("message")
    content = (
        first.get("content")
        or first.get("detail")
        or first.get("description")
    )
    return {
        "code": str(code).strip() if code is not None else None,
        "title": str(title).strip() if title is not None else None,
        "content": str(content).strip() if content is not None else None,
    }


def _raw_for_existing_parser(raw: dict[str, Any], error: dict[str, Any] | None) -> dict[str, Any]:
    """Let the existing parser consume the live vendor title without data loss."""
    if not error:
        return raw
    index2 = raw.get("index2")
    if not isinstance(index2, dict):
        return raw
    errors = index2.get("error_data")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return raw
    first = dict(errors[0])
    # beta15's parser understands message/desc/code. Prefer the vendor title,
    # falling back to content/code, while retaining every original field.
    if not (first.get("desc") or first.get("message") or first.get("code")):
        first["message"] = error.get("title") or error.get("content") or error.get("code")
    copied_errors = list(errors)
    copied_errors[0] = first
    copied_index2 = dict(index2)
    copied_index2["error_data"] = copied_errors
    copied_raw = dict(raw)
    copied_raw["index2"] = copied_index2
    return copied_raw


def _copy_problem_details(snapshot: dict[str, Any], problem: dict[str, Any] | None) -> None:
    if not isinstance(problem, dict):
        return
    for key in ("error_code", "error_title", "error_content", "error_kind"):
        if snapshot.get(key) is None and problem.get(key) is not None:
            snapshot[key] = problem.get(key)


def _mark_endpoints_due(coordinator: Any, *keys: str) -> None:
    """Bypass only the endpoint TTL; the normal coordinator performs the read."""
    for key in keys:
        status = coordinator._endpoint_status.get(key)  # noqa: SLF001 - beta shim
        if isinstance(status, dict):
            status["last_attempt_mono"] = None


def _install_error_sensor_attributes() -> None:
    """Expose live code/title/content on the existing Error sensor."""
    from . import sensor as sensor_platform

    def attrs(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": data.get("error_code"),
            "title": data.get("error_title"),
            "content": data.get("error_content"),
            "kind": data.get("error_kind"),
            "source": data.get("problem_source"),
            "state_code": data.get("state_code"),
        }

    sensor_platform.SENSORS = tuple(
        replace(description, attrs_fn=attrs)
        if description.key == "error_text"
        else description
        for description in sensor_platform.SENSORS
    )


def install_beta16_runtime() -> None:
    """Install beta16 state/error semantics once per interpreter."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_beta16_runtime_installed", False):
        install_beta17_runtime()
        install_beta18_runtime()
        return

    # The coordinator imported these mutable objects by reference, so mutating
    # them fixes both the constants module and already-imported coordinator code.
    _const.VEHICLE_STATE_TO_ACTIVITY[_STATE_IDLE] = _const.ACTIVITY_PAUSED
    _const.VEHICLE_STATE_LABELS[_STATE_IDLE] = "Idle"
    _const.VEHICLE_STATE_TO_ACTIVITY[_STATE_FAULT] = _const.ACTIVITY_ERROR
    _const.VEHICLE_STATE_LABELS[_STATE_FAULT] = "Error"

    # Observed H215 MQTT numeric state 3 accompanies both isIdel/0103 and
    # isPaused/0211. It is therefore neutral stopped context, not charging.
    _const.MQTT_DOCKED_STATES.discard(_MQTT_STOPPED)

    original_parse = cls._parse
    original_apply_problem = cls._apply_problem_latch
    original_ingest_state = cls.ingest_mqtt_state

    def parse(self: Any, raw: dict[str, Any]) -> dict[str, Any]:
        previous_problem = deepcopy(self._last_problem)  # noqa: SLF001
        live_error = _first_error(raw)
        snapshot = original_parse(self, _raw_for_existing_parser(raw, live_error))
        state_code = str(snapshot.get("state_code") or "")

        if live_error:
            title = live_error.get("title") or live_error.get("content") or live_error.get("code") or "Error"
            snapshot["error_text"] = title
            snapshot["error_code"] = live_error.get("code")
            snapshot["error_title"] = live_error.get("title") or title
            snapshot["error_content"] = live_error.get("content")
            snapshot["error_kind"] = "fault"
        elif state_code == _STATE_LIFTED or self._fresh_mqtt_named_state() == "isLifted":  # noqa: SLF001
            snapshot["error_code"] = None
            snapshot["error_title"] = "Lifted"
            snapshot["error_content"] = None
            snapshot["error_kind"] = "safety"
            snapshot["error_text"] = "Lifted"
        elif state_code == _STATE_FAULT and snapshot.get("error"):
            # Preserve details across a short endpoint race where 0301 remains
            # visible but error_data momentarily disappears.
            prior = previous_problem if isinstance(previous_problem, dict) else {}
            snapshot["error_code"] = prior.get("error_code")
            snapshot["error_title"] = prior.get("error_title") or snapshot.get("error_text") or "Error"
            snapshot["error_content"] = prior.get("error_content")
            snapshot["error_kind"] = "fault"
        else:
            snapshot["error_code"] = None
            snapshot["error_title"] = None
            snapshot["error_content"] = None
            snapshot["error_kind"] = None

        if self._problem_latched and isinstance(self._last_problem, dict):  # noqa: SLF001
            self._last_problem.update(  # noqa: SLF001
                {
                    "error_code": snapshot.get("error_code"),
                    "error_title": snapshot.get("error_title"),
                    "error_content": snapshot.get("error_content"),
                    "error_kind": snapshot.get("error_kind"),
                    "error_text": snapshot.get("error_text"),
                }
            )
            snapshot["last_problem"] = deepcopy(self._last_problem)  # noqa: SLF001
        return snapshot

    def apply_problem_latch(self: Any, snapshot: dict[str, Any]) -> None:
        original_apply_problem(self, snapshot)
        if self._problem_latched:  # noqa: SLF001
            _copy_problem_details(snapshot, self._last_problem)  # noqa: SLF001

    def ingest_mqtt_state(self: Any, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return original_ingest_state(self, state)
        state_name = str(state.get("state") or "").strip()
        previous_named = self._fresh_mqtt_named_state()  # noqa: SLF001
        important = bool(
            state_name in {"Error", "Self-Checking", "isLifted"}
            or previous_named in {"Error", "isLifted"}
        )
        if important:
            _mark_endpoints_due(self, "index2", "auth_list")

        result = original_ingest_state(self, state)

        if state_name == "Error":
            self._set_problem_latch(  # noqa: SLF001
                True,
                source="mqtt_state",
                state="Problem",
                state_code=(
                    _STATE_FAULT
                    if str((self.data or {}).get("state_code") or "") == _STATE_FAULT
                    else ""
                ),
                error_text="Error",
            )
            if isinstance(self._last_problem, dict):  # noqa: SLF001
                self._last_problem.update(  # noqa: SLF001
                    {
                        "error_code": None,
                        "error_title": "Error",
                        "error_content": None,
                        "error_kind": "fault",
                    }
                )
            snapshot = dict(self.data or self._bootstrap_snapshot())  # noqa: SLF001
            snapshot["state"] = "Error"
            snapshot["activity"] = _const.ACTIVITY_ERROR
            snapshot["error"] = True
            snapshot["error_text"] = "Error"
            snapshot["error_code"] = None
            snapshot["error_title"] = "Error"
            snapshot["error_content"] = None
            snapshot["error_kind"] = "fault"
            snapshot["docked"] = False
            snapshot["docked_source"] = "mqtt_error_state"
            snapshot["last_problem"] = deepcopy(self._last_problem)  # noqa: SLF001
            self.async_set_updated_data(snapshot)
            self.request_fast_refresh("MQTT state changed to Error")
        elif state_name == "Self-Checking":
            self.request_fast_refresh("MQTT entered Self-Checking")
        elif previous_named == "Error" and state_name and state_name != "Error":
            self.request_fast_refresh("MQTT state changed away from Error")
        elif previous_named == "isLifted" and state_name and state_name != "isLifted":
            # beta15/original code already asks for a refresh; this call is
            # throttled, but the forced endpoint due flag remains in place.
            self.request_fast_refresh("MQTT state changed away from isLifted")
        return result

    cls._parse = parse
    cls._apply_problem_latch = apply_problem_latch
    cls.ingest_mqtt_state = ingest_mqtt_state
    cls._beta16_runtime_installed = True
    _install_error_sensor_attributes()
    install_beta17_runtime()
    install_beta18_runtime()
