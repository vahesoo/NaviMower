"""Read-only Navimow notification/event endpoint discovery for diagnostics.

Beta20 narrows the search around the message/push namespaces that returned a
non-404 response in beta19. The probe still runs only when Home Assistant
Diagnostics is explicitly downloaded. It never calls settings, control or map
write endpoints.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .api.client import NavimowAuthError, NavimowError
from .diagnostics_export import inventory, sanitize

# Beta19 showed that these message/push namespaces returned HTTP/business 502
# rather than "url Not Exist". Keep the next pass focused there, while adding a
# small set of message-center naming variants rather than continuing to spray the
# vehicle namespace with known-dead 404 candidates.
_EVENT_PATHS: tuple[str, ...] = (
    "/message/message/list",
    "/message/message/get-list",
    "/message/notice/list",
    "/message/notice/get-list",
    "/message/notification/list",
    "/message/notification/get-list",
    "/message/push/list",
    "/push/message/list",
    "/push/notification/list",
    "/message/index/list",
    "/message/index/message-list",
    "/message/user/list",
    "/message/user-message/list",
    "/message/device/list",
    "/message/device-message/list",
    "/message/center/list",
    "/message/center/message-list",
    "/message/get-message-list",
    "/push/index/list",
    "/push/message/get-list",
)


def _profiles(
    sn: str,
    vehicle_type: Any,
    language: str,
    common_index: Any = None,
    share_type: Any = None,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Return deliberately different account/device parameter profiles.

    The phone app's Notification view is account-level, so beta20 explicitly
    tries bodies without mower identity as well as device-scoped variants. The
    expanded profile adds common message-center pagination/category aliases.
    """
    account_minimal = {"language": language}
    account_paged = {
        "language": language,
        "page": 1,
        "pageNum": 1,
        "pageNo": 1,
        "pageSize": 50,
        "current": 1,
        "size": 50,
        "limit": 50,
        "offset": 0,
        "cursor": "",
        "lastId": "",
        "type": 0,
        "msgType": 0,
        "noticeType": 0,
        "notificationType": 0,
        "eventType": 0,
        "messageType": 0,
        "messageCategory": 0,
        "category": 0,
        "bizType": 0,
        "businessType": 0,
        "sourceType": 0,
        "deviceType": 0,
        "productType": 0,
        "tabType": 0,
        "status": 0,
        "read": 0,
        "isRead": 0,
        "readStatus": 0,
        "startTime": 0,
        "beginTime": 0,
        "endTime": 0,
        "timestamp": 0,
    }
    device_minimal = {
        "vehicle_sn": sn,
        "vehicle_type": vehicle_type,
        "language": language,
    }
    device_extended = {
        **account_paged,
        "vehicle_sn": sn,
        "vehicle_type": vehicle_type,
        "deviceId": sn,
    }
    if common_index is not None:
        device_extended["commonUserVehicleIndex"] = common_index
    if share_type is not None:
        device_extended["vehicleShareType"] = share_type
    return (
        ("account_minimal", account_minimal),
        ("account_paged", account_paged),
        ("device_minimal", device_minimal),
        ("device_extended", device_extended),
    )


def _account_vehicle_hints(client: Any, sn: str) -> tuple[Any, Any]:
    """Best-effort read of per-vehicle account hints already exposed by auth-list."""
    try:
        rows = client.auth_list()
    except Exception:  # noqa: BLE001 - discovery must not fail diagnostics
        return None, None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if str(row.get("vehicle_sn") or "") != str(sn):
            continue
        return row.get("common_user_vehicle_index"), row.get("vehicle_share_type")
    return None, None


def probe_event_endpoints(client: Any, sn: str, vehicle_type: Any) -> dict[str, Any]:
    """Probe likely notification endpoints and return compact sanitized metadata."""
    language = str(getattr(client, "_language", "en") or "en")  # noqa: SLF001
    common_index, share_type = _account_vehicle_hints(client, sn)
    attempts: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    not_found_count = 0
    not_found_paths: set[str] = set()

    for path in _EVENT_PATHS:
        for profile_name, params in _profiles(
            sn, vehicle_type, language, common_index, share_type
        ):
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
                # Known-dead routes add little value and made beta19 diagnostics
                # noisy. Count them, but keep only non-404 failures in detail.
                if str(err.code) == "404" and str(err.desc).lower() == "url not exist":
                    not_found_count += 1
                    not_found_paths.add(path)
                    continue
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
                if isinstance(clean, list):
                    row["sample"] = clean[:2]
                elif isinstance(clean, dict):
                    row["sample"] = {key: clean[key] for key in list(clean)[:12]}
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

            # One successful response is enough for an endpoint; avoid more
            # profile calls and keep the diagnostics probe bounded.
            if row.get("ok"):
                break

    return {
        "read_only": True,
        "normal_polling_unchanged": True,
        "candidate_path_count": len(_EVENT_PATHS),
        "parameter_profiles": [
            "account_minimal",
            "account_paged",
            "device_minimal",
            "device_extended",
        ],
        "attempt_count": len(attempts) + not_found_count,
        "retained_attempt_count": len(attempts),
        "not_found_count": not_found_count,
        "not_found_path_count": len(not_found_paths),
        "matches": matches,
        "attempts": attempts,
        "note": (
            "Beta20 focuses on message/push routes and account-level payloads. "
            "Known 404 url-not-exist attempts are summarized by count only; "
            "successful or otherwise interesting responses retain sanitized "
            "structure/sample or business-code details."
        ),
    }
