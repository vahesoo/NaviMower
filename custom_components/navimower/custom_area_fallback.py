"""Pure Custom Area presence fallback semantics.

MQTT remains the preferred low-latency pose source. When it is unavailable,
fresh private-cloud X/Y may keep a Custom Area usable. Entering a cloud-backed
area is accepted immediately; leaving a previously active area requires two
strictly newer cloud reports so an automation cannot close a physical gate on a
single delayed sample.
"""
from __future__ import annotations

from typing import Any


def resolve_custom_area_presence(
    previous: dict[str, Any] | None,
    *,
    inside: bool | None,
    source: str,
    usable: bool,
    report_key: Any = None,
) -> tuple[bool | None, dict[str, Any]]:
    """Resolve one Custom Area state and return its updated confirmation memory."""
    prior = previous if isinstance(previous, dict) else {}
    report = str(report_key or "")

    if source == "mqtt":
        if inside is None:
            return None, prior
        return bool(inside), {
            "value": bool(inside),
            "report": None,
            "outside_count": 0,
            "source": "mqtt",
        }

    if source != "private_cloud" or not usable or inside is None:
        return None, prior

    if inside:
        return True, {
            "value": True,
            "report": report,
            "outside_count": 0,
            "source": "private_cloud",
        }

    # Cloud says outside. If the area was not previously active there is no
    # unsafe ON -> OFF transition to protect, so OFF can be published directly.
    if prior.get("value") is not True:
        return False, {
            "value": False,
            "report": report,
            "outside_count": 0,
            "source": "private_cloud",
        }

    count = int(prior.get("outside_count") or 0)
    previous_report = str(prior.get("report") or "")
    if report and report != previous_report:
        count += 1
    else:
        count = max(1, count)

    if count >= 2:
        return False, {
            "value": False,
            "report": report,
            "outside_count": count,
            "source": "private_cloud",
        }

    return True, {
        "value": True,
        "report": report,
        "outside_count": count,
        "source": "private_cloud",
    }
