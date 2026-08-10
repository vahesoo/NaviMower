"""Read-only Navimow notification/event endpoint discovery for diagnostics.

The phone app exposes a Device notification timeline, but the runtime endpoint
has not yet been identified. This module deliberately probes a bounded set of
likely read endpoints only when Home Assistant diagnostics are requested.
Nothing here runs in the normal coordinator poll and no setting/control/map
write endpoint is used.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .api.client import NavimowAuthError, NavimowError
from .diagnostics_export import inventory, sanitize

# Keep this list explicit and auditable. All candidates are GET-like/read naming
# guesses around the app's Message / Notification / Event vocabulary.
_EVENT_PATHS: tuple[str, ...] = (
    "/message/message/list",
    "/message/message/get-list",
    "/message/notice/list",
    "/message/notice/get-list",
    "/message/notification/list",
    "/message/notification/get-list",
    "/message/push/list",
    "/user/message/list",
    "/user/message/get-list",
    "/user/notice/list",
    "/user/notification/list",
    "/user/push/list",
    "/vehicle/vehicle/event-list",
    "/vehicle/vehicle/get-event-list",
    "/vehicle/vehicle/message-list",
    "/vehicle/vehicle/get-message-list",
    "/vehicle/vehicle/notice-list",
    "/vehicle/vehicle/get-notice-list",
    "/vehicle/vehicle/notification-list",
    "/vehicle/vehicle/get-notification-list",
    "/vehicle/event/list",
    "/vehicle/message/list",
    "/push/message/list",
    "/push/notification/list",
)


def _profiles(sn: str, vehicle_type: Any, language: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Return two deliberately different parameter profiles.

    The broad profile contains common pagination/type aliases seen in mobile
    APIs. Unknown keys are expected to be ignored by tolerant endpoints. The
    minimal profile lets us distinguish an endpoint that rejects those aliases.
    """
    minimal = {
        "vehicle_sn": sn,
        "vehicle_type": vehicle_type,
        "language": language,
    }
    broad = {
        **minimal,
        "page": 1,
        "pageNum": 1,
        "pageNo": 1,
        "pageSize": 50,
        "current": 1,
        "size": 50,
        "limit": 50,
        "offset": 0,
        "type": 0,
        "eventType": 0,
        "messageType": 0,
        "readStatus": 0,
    }
    return (("minimal", minimal), ("broad", broad))


def probe_event_endpoints(client: Any, sn: str, vehicle_type: Any) -> dict[str, Any]:
    """Probe likely notification endpoints and return only sanitized metadata."""
    language = str(getattr(client, "_language", "en") or "en")  # noqa: SLF001
    attempts: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    for path in _EVENT_PATHS:
        for profile_name, params in _profiles(sn, vehicle_type, language):
            row: dict[str, Any] = {
                "path": path,
                "profile": profile_name,
                "parameter_keys": sorted(params),
            }
            try:
                data = client.call(path, deepcopy(params))
            except NavimowAuthError as err:
                row.update(
                    {
                        "ok": False,
                        "error_type": type(err).__name__,
                        "business_code": sanitize(err.code),
                        "description": sanitize(err.desc),
                    }
                )
            except NavimowError as err:
                row.update(
                    {
                        "ok": False,
                        "error_type": type(err).__name__,
                        "business_code": sanitize(err.code),
                        "description": sanitize(err.desc),
                    }
                )
            except Exception as err:  # noqa: BLE001 - diagnostics discovery
                row.update(
                    {
                        "ok": False,
                        "error_type": type(err).__name__,
                        "description": sanitize(str(err)),
                    }
                )
            else:
                clean = sanitize(data)
                row.update(
                    {
                        "ok": True,
                        "response_type": type(data).__name__,
                        "inventory": inventory(clean),
                    }
                )
                # Preserve a tiny sanitized sample for semantic recognition. The
                # normal sanitizer still redacts account/device/GPS identifiers.
                if isinstance(clean, list):
                    row["sample"] = clean[:2]
                elif isinstance(clean, dict):
                    row["sample"] = {
                        key: clean[key]
                        for key in list(clean)[:12]
                    }
                elif clean is not None:
                    row["sample"] = clean
                matches.append(
                    {
                        "path": path,
                        "profile": profile_name,
                        "response_type": row["response_type"],
                        "key_count": row["inventory"].get("key_count"),
                        "keyword_paths": row["inventory"].get("keyword_paths"),
                    }
                )
            attempts.append(row)

            # A successful minimal response is enough for this endpoint; avoid a
            # duplicate broad call and keep diagnostics reasonably bounded.
            if row.get("ok") and profile_name == "minimal":
                break

    return {
        "read_only": True,
        "normal_polling_unchanged": True,
        "candidate_path_count": len(_EVENT_PATHS),
        "parameter_profiles": ["minimal", "broad"],
        "attempt_count": len(attempts),
        "matches": matches,
        "attempts": attempts,
        "note": (
            "These are bounded discovery guesses executed only while diagnostics "
            "are generated. Successful response structure/sample fields are "
            "sanitized; failures retain business code/description to identify "
            "missing parameter or unknown-route responses."
        ),
    }
