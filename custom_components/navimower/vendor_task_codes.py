"""Read-only official-app task/start-type codebook for HA Download diagnostics.

Sources: official app 4.02.0 and 4.3.5 MowerWorkInfo, MowerTask and
MowerTaskResponse, audited under navimower-dev-docs/research/full-app-audit.

m_task_status is the MowerTask day state. c_plan_status is a DIFFERENT
current-period status, not another task/day state.

mowStartType marks the *type of mowing task*. It does not identify the sender
of the most recent Continue / Resume command, and must never drive controls.
"""
from __future__ import annotations

import re
import time
from typing import Any

TASK_DAY_STATES: dict[int, str] = {
    0: "TASK_INVALID",
    1: "TASK_WAITING",
    2: "TASK_WORKING",
    3: "TASK_COMPLETE",
    4: "TASK_NO_TASK",
    5: "TASK_CANCELED",
    6: "TASK_NO_PLAN",
    7: "TASK_MOW_BY_USER",
    8: "TASK_MOW_PLAN_LOOP_WAITING",
    9: "TASK_MOW_MANUAL_LOOP_WAITING",
}

MOW_START_TYPES: dict[int, str] = {
    -1: "MOW_BY_UNKNOWN",
    0: "MOW_BY_AUTO",
    1: "MOW_BY_USER",
}

# MowerTaskResponse.getUndrawnPileReason() parses hexadecimal TEXT, not
# decimal text. This is a separate domain from m_task_status and vehicleState.
TASK_LOOP_WAIT_REASONS: dict[int, str] = {
    1: "RAIN",
    2: "LOW_POWER",
    3: "DARK",
    4: "POOR_POSITION",
    5: "AFTER_RAIN",
    6: "STORM",
    7: "HIGH_TEMP",
    8: "SNOW",
    9: "AFTER_FROST",
    10: "AFTER_SNOW",
}

_DECIMAL = re.compile(r"[+-]?[0-9]+\Z", flags=re.ASCII)
_HEX = re.compile(r"(?:0[xX])?[0-9A-Fa-f]+\Z", flags=re.ASCII)


def _decimal_code(value: Any) -> int | None:
    """Strictly decode a vendor integer; do not guess hexadecimal task states."""
    if type(value) is int:
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _DECIMAL.fullmatch(raw) or len(raw) > 16:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def _hex_wait_reason(value: Any) -> int | None:
    """The vendor Java MowerTaskResponse parser uses base 16 for this field."""
    if type(value) is int:
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _HEX.fullmatch(raw) or len(raw) > 16:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def _age_seconds(owner: Any, endpoint: str | None = None, *,
                 field_timestamp: str | None = None) -> float | None:
    if field_timestamp:
        stamp = getattr(owner, field_timestamp, None)
    else:
        statuses = getattr(owner, "_endpoint_status", None)
        entry = statuses.get(endpoint) if isinstance(statuses, dict) else None
        stamp = entry.get("last_success_mono") if isinstance(entry, dict) else None
    if type(stamp) not in {int, float}:
        return None
    return round(max(0.0, time.monotonic() - stamp), 2)


def _observation(
    raw_value: Any, symbols: dict[int, str], *,
    source: str, age_seconds: float | None, hexadecimal: bool = False,
) -> dict[str, Any]:
    code = _hex_wait_reason(raw_value) if hexadecimal else _decimal_code(raw_value)
    return {
        "value": code,
        "symbol": symbols.get(code),
        "source": source if code is not None else None,
        "source_age_s": age_seconds if code is not None else None,
        "recognized": code in symbols,
    }


def vendor_task_codes_diagnostics(owner: Any, data: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize *cached* fields only; no cloud calls and no command side effects.

    Report MQTT packed, MQTT explicit and private-cloud packed start-type data
    separately. They can be cached from different moments and must not be
    fused into proof of who initiated the latest mowing/Resume transition.
    """
    snapshot = data if isinstance(data, dict) else {}
    raw = snapshot.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    today_plan = raw.get("today_plan")
    today_plan = today_plan if isinstance(today_plan, dict) else {}
    raw_location = raw.get("location")
    raw_location = raw_location if isinstance(raw_location, dict) else {}
    raw_index2 = raw.get("index2")
    raw_index2 = raw_index2 if isinstance(raw_index2, dict) else {}
    mqtt = getattr(owner, "_mqtt_location", None)
    mqtt = mqtt if isinstance(mqtt, dict) else {}

    packed_private_source = None
    packed_private_endpoint = None
    for endpoint, value in (("location", raw_location), ("index2", raw_index2)):
        if value.get("map_work_position") or value.get("mapWorkPosition"):
            packed_private_source = f"private_cloud.{endpoint}.map_work_position"
            packed_private_endpoint = endpoint
            break

    task_age = _age_seconds(owner, "today_plan")
    return {
        "schema_version": 1,
        "evidence": "official_app_4.02.0_and_4.3.5_decompiled_constants",
        "mower_task": {
            "day_state": _observation(
                today_plan.get("m_task_status"), TASK_DAY_STATES,
                source="private_cloud.today_plan.m_task_status",
                age_seconds=task_age,
            ),
            "current_plan_status": {
                "value": _decimal_code(today_plan.get("c_plan_status")),
                "source": "private_cloud.today_plan.c_plan_status"
                if _decimal_code(today_plan.get("c_plan_status")) is not None else None,
                "source_age_s": task_age
                if _decimal_code(today_plan.get("c_plan_status")) is not None else None,
                "note": "Separate current-period status; NOT a MowerTask day-state code.",
            },
            "loop_wait_reason": _observation(
                today_plan.get("undrawnPileReason"), TASK_LOOP_WAIT_REASONS,
                source="private_cloud.today_plan.undrawnPileReason",
                age_seconds=task_age, hexadecimal=True,
            ),
        },
        "mower_work_info": {
            "mqtt_explicit_start_type": _observation(
                mqtt.get("mow_start_type"), MOW_START_TYPES,
                source="official_mqtt.mowStartType",
                age_seconds=_age_seconds(
                    owner, field_timestamp="_mqtt_start_type_last_update",
                ),
            ),
            "mqtt_packed_start_type": _observation(
                mqtt.get("work_mode"), MOW_START_TYPES,
                source="official_mqtt.mapWorkPosition.word3",
                age_seconds=_age_seconds(
                    owner, field_timestamp="_mqtt_work_start_type_last_update",
                ),
            ),
            "private_packed_start_type": _observation(
                snapshot.get("work_mode"), MOW_START_TYPES,
                source=packed_private_source or "private_cloud.map_work_position.word3",
                age_seconds=_age_seconds(owner, packed_private_endpoint)
                if packed_private_endpoint else None,
            ),
            "note": (
                "MowStartType describes the original mowing task type, not the "
                "identity of the sender of the most recent Resume. The packed "
                "third word is historically named work_mode in NaviMower; "
                "it is NOT the mower's configured Work mode select."
            ),
        },
    }
