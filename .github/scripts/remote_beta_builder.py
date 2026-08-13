from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    return text.replace(old, new, 1)


manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta9":
    raise SystemExit(f"Expected 0.4.3-beta9 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta10"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


# Error sensor: private cloud is canonical; MQTT named Error is only an edge trigger.
state_path = COMPONENT / "state_semantics.py"
state = state_path.read_text(encoding="utf-8")
state = replace_once(
    state,
    "from copy import deepcopy\nfrom dataclasses import replace\nfrom typing import Any\n",
    "from copy import deepcopy\nfrom dataclasses import replace\nfrom datetime import UTC, datetime\nfrom typing import Any\n",
    "state diagnostics imports",
)
state = replace_once(
    state,
    '''        replace(description, attrs_fn=attrs)\n        if description.key == "error_text"\n''',
    '''        replace(\n            description,\n            value_fn=lambda data: data.get("error_text") or "No errors",\n            attrs_fn=attrs,\n        )\n        if description.key == "error_text"\n''',
    "clean error sensor state",
)
insert_marker = "\n\ndef install_state_semantics() -> None:\n"
if insert_marker not in state:
    raise SystemExit("state diagnostics insertion marker missing")
state = state.replace(
    insert_marker,
    '''\n\ndef error_transition_diagnostics(coordinator: Any) -> dict[str, Any]:\n    """Return MQTT-to-private error arbitration evidence without changing state."""\n    last_update = getattr(coordinator, "_mqtt_named_state_last_update", None)\n    age = coordinator._age_since(last_update) if last_update is not None else None  # noqa: SLF001\n    return {\n        "policy": "private_cloud_canonical_mqtt_transition_trigger",\n        "mqtt_named_state": getattr(coordinator, "_mqtt_named_state", None),\n        "mqtt_named_state_age": age,\n        "last_error_transition": deepcopy(\n            getattr(coordinator, "_error_transition_trace", None)\n        ),\n    }\n\n\ndef install_state_semantics() -> None:\n''',
    1,
)
start_marker = "    def ingest_mqtt_state(self: Any, state: dict[str, Any]) -> None:\n"
end_marker = "\n    cls._parse = parse\n"
start = state.find(start_marker)
end = state.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("ingest_mqtt_state replacement markers missing")
new_ingest = '''    def ingest_mqtt_state(self: Any, state: dict[str, Any]) -> None:\n        if not isinstance(state, dict):\n            return original_ingest_state(self, state)\n\n        state_name = str(state.get("state") or "").strip()\n        previous_named = self._fresh_mqtt_named_state()  # noqa: SLF001\n        transition = bool(state_name and state_name != previous_named)\n        error_transition = bool(\n            transition\n            and (state_name in {"Error", "Self-Checking"} or previous_named == "Error")\n        )\n\n        # A repeated MQTT Error is not a new source value and must not cause a\n        # poll storm. Only a named-state edge invalidates the canonical private\n        # status endpoints; the normal coordinator still performs the reads.\n        if error_transition:\n            _mark_endpoints_due(self, "index2", "auth_list")\n\n        result = original_ingest_state(self, state)\n\n        if error_transition:\n            if state_name == "Error":\n                reason = "MQTT state changed to Error"\n            elif previous_named == "Error":\n                reason = f"MQTT state changed away from Error to {state_name}"\n            else:\n                reason = f"MQTT error-related state changed to {state_name}"\n            self._error_transition_trace = {  # noqa: SLF001\n                "previous_mqtt_state": previous_named,\n                "new_mqtt_state": state_name,\n                "observed_utc": datetime.now(UTC).isoformat(),\n                "private_endpoints_marked_due": ["index2", "auth_list"],\n                "fast_refresh_requested": True,\n                "reason": reason,\n            }\n            self.request_fast_refresh(reason)\n        return result\n'''
state = state[:start] + new_ingest + state[end:]
state_path.write_text(state, encoding="utf-8")


# Retain the un-normalized Device feed in memory so diagnostics can show fields
# that the public sensor intentionally does not expose.
notification_path = COMPONENT / "notification_feed.py"
notification = notification_path.read_text(encoding="utf-8")
notification = replace_once(
    notification,
    '''    normalized = _normalize_response(response)\n    vendor_messages = normalized["list"][:VENDOR_NOTIFICATION_LIMIT]\n''',
    '''    coordinator._notification_raw_cache = deepcopy(response)  # noqa: SLF001\n    normalized = _normalize_response(response)\n    vendor_messages = normalized["list"][:VENDOR_NOTIFICATION_LIMIT]\n''',
    "raw notification cache",
)
notification_path.write_text(notification, encoding="utf-8")


error_discovery = r'''"""Focused read-only public H5 discovery for active error actions."""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .api.regions import canonical_region
from .diagnostics_sanitize import sanitize

MAX_HTML = 256 * 1024
MAX_ROOT_JS = 2 * 1024 * 1024
PREFIX_BYTES = 64 * 1024
MAX_PREFIX_REQUESTS = 180
MAX_FULL_MATCHES = 18
MAX_FULL_JS = 2 * 1024 * 1024
MAX_CONTEXTS = 80
CONTEXT_RADIUS = 1800
TIMEOUT = 5

SCRIPT_RE = re.compile(r"<script\\b[^>]*\\bsrc\\s*=\\s*[\"']([^\"']+)[\"']", re.I)
JS_RE = re.compile(r"[\"']([^\"'\\r\\n]{1,420}\\.js(?:\\?[^\"'\\r\\n]{0,120})?)[\"']", re.I)
ENDPOINT_RE = re.compile(
    r"[\"']((?:https?://[^\"'\\s]+)?/?(?:mowerbot|vehicle|setting|robot|api)/[^\"'\\r\\n]{1,320})[\"']",
    re.I,
)
HTTP_RE = re.compile(r"method\\s*:\\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\\w$]*\\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\\s*\\(\\s*[\"'](?P<method>[^\"']{1,160})[\"']",
    re.I,
)

BASE_TARGET_TERMS = (
    "Clear and resume",
    "Reboot Mower",
    "Got it",
    "clearAndResume",
    "clear_and_resume",
    "clearResume",
    "resumeAfterError",
    "clearError",
    "resetError",
    "clearFault",
    "resetFault",
    "rebootMower",
    "reboot_mower",
    "restartMower",
    "restart_mower",
    "/vehicle/set/send",
    "c:behavior",
    "cmdCode",
    "cmd_code",
    "sendEncryptionData",
    "callNative",
)
UI_LABELS = ("Clear and resume", "Reboot Mower", "Got it")
COMMAND_NEEDLES = (
    "clear",
    "resume",
    "reboot",
    "restart",
    "fault",
    "error",
    "cmdCode",
    "c:behavior",
    "/vehicle/set/send",
    "callNative",
    "sendEncryptionData",
)
PRIORITY_FILENAME_TOKENS = (
    "error",
    "fault",
    "alarm",
    "dialog",
    "popup",
    "home",
    "mower",
    "service-",
    "request-",
    "native-",
    "state",
)

# Current public-app roots observed during the 0.4.3 beta line. They are only
# fallback GET targets if the live HTML does not enumerate them; no identity is sent.
OBSERVED_PUBLIC_ROOT_SCRIPTS = (
    "https://cloud-acc.navimow.com/navimow/static/js/app-entry-afe8631d.js",
    "https://cloud-acc.navimow.com/navimow/static/js/app-entry-legacy-83eb5a47.js",
)
OBSERVED_PUBLIC_SUPPORT_SCRIPTS = (
    "https://cloud-acc.navimow.com/navimow/static/js/native-d66fe239.js",
    "https://cloud-acc.navimow.com/navimow/static/js/request-e9a0ef42.js",
)


def _host(client: Any) -> str:
    region = canonical_region(getattr(client, "region", "fra"))
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or ""))
    path = parsed.path
    while "/static/js/static/js/" in path:
        path = path.replace("/static/js/static/js/", "/static/js/")
    while "/assets/assets/" in path:
        path = path.replace("/assets/assets/", "/assets/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _resolve(base_url: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed_ref = urllib.parse.urlsplit(raw)
    if parsed_ref.scheme and parsed_ref.netloc:
        return _safe_url(raw)
    base = urllib.parse.urlsplit(base_url)
    clean = raw.lstrip("./")
    for marker in ("static/js/", "assets/"):
        if clean.startswith(marker):
            idx = base.path.find("/" + marker)
            if idx >= 0:
                prefix = base.path[: idx + 1]
                return _safe_url(
                    urllib.parse.urlunsplit((base.scheme, base.netloc, prefix + clean, "", ""))
                )
    return _safe_url(urllib.parse.urljoin(base_url, raw))


def _fetch(url: str, limit: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerErrorDiagnostics/0.4.3-beta10",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(limit + 1)
            status = int(getattr(response, "status", 200))
            content_type = str(response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as err:
        raw = err.read(limit + 1)
        status = int(err.code)
        content_type = str(err.headers.get("Content-Type", ""))
    except urllib.error.URLError as err:
        return {"ok": False, "url": _safe_url(url), "transport_error": sanitize(str(err.reason))}
    except Exception as err:  # noqa: BLE001 - optional diagnostics probe
        return {
            "ok": False,
            "url": _safe_url(url),
            "transport_error": sanitize(f"{type(err).__name__}: {err}"),
        }
    truncated = len(raw) > limit
    raw = raw[:limit]
    return {
        "ok": 200 <= status < 400,
        "url": _safe_url(url),
        "http_status": status,
        "content_type": content_type,
        "body_length_read": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "truncated": truncated,
        "_text": raw.decode("utf-8", errors="replace"),
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_text"}


def _allowed(url: str, hosts: set[str]) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc in hosts and parsed.path.lower().endswith(".js")


def _priority(url: str, source_context: str = "") -> int:
    text = (urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] + " " + source_context).lower()
    score = 0
    for token in PRIORITY_FILENAME_TOKENS:
        if token in text:
            score += 120 if token in {"error", "fault", "native-", "request-"} else 55
    if "cloud-acc.navimow.com" in url:
        score += 25
    return score


def _contexts(text: str, source: str, terms: list[str]) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for term in terms:
        needle = str(term or "").strip()
        if not needle:
            continue
        start = 0
        found = 0
        while found < 2 and len(rows) < MAX_CONTEXTS:
            index = lower.find(needle.lower(), start)
            if index < 0:
                break
            lo = max(0, index - CONTEXT_RADIUS)
            hi = min(len(text), index + len(needle) + CONTEXT_RADIUS)
            nearby = re.sub(r"\\s+", " ", text[lo:hi]).strip()
            endpoints = sorted(set(ENDPOINT_RE.findall(nearby)))[:20]
            methods = sorted(set(value.upper() for value in HTTP_RE.findall(nearby)))
            bridges = [
                {"callee": match.group("callee"), "method": match.group("method")}
                for match in BRIDGE_RE.finditer(nearby)
            ][:20]
            rows.append(
                {
                    "term": needle,
                    "source": _safe_url(source),
                    "offset": index,
                    "endpoint_paths": endpoints,
                    "http_methods": methods,
                    "bridge_calls": bridges,
                    "context": nearby,
                }
            )
            start = index + len(needle)
            found += 1
    return rows


def _translation_keys(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label in UI_LABELS:
        pattern = re.compile(
            r"[\"'](?P<key>[A-Za-z0-9_.-]{2,100})[\"']\\s*:\\s*[\"']"
            + re.escape(label)
            + r"[\"']",
            re.I,
        )
        for match in pattern.finditer(text):
            row = {"label": label, "key": match.group("key")}
            if row not in rows:
                rows.append(row)
    return rows


def _js_references(text: str, source: str, hosts: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, match in enumerate(JS_RE.finditer(text)):
        url = _resolve(source, match.group(1))
        if not _allowed(url, hosts) or url in seen:
            continue
        seen.add(url)
        lo = max(0, match.start() - 650)
        hi = min(len(text), match.end() + 650)
        context = re.sub(r"\\s+", " ", text[lo:hi]).strip()
        rows.append(
            {
                "url": url,
                "source": _safe_url(source),
                "order": order,
                "source_context": context,
                "priority": _priority(url, context),
            }
        )
    return rows


def probe_error_h5(client: Any, error_code: str = "", error_title: str = "") -> dict[str, Any]:
    """Inspect only public H5 assets for error-dialog command evidence."""
    host = _host(client)
    allowed_hosts = {urllib.parse.urlsplit(host).netloc, "cloud-acc.navimow.com"}
    entry_urls = (
        f"{host}/old/",
        f"{host}/maintenance/",
        "https://cloud-acc.navimow.com/navimow/",
    )
    dynamic_terms = [term for term in (str(error_code or ""), str(error_title or "")) if term]
    target_terms = list(dict.fromkeys([*BASE_TARGET_TERMS, *dynamic_terms]))

    pages: list[dict[str, Any]] = []
    root_urls: list[str] = []
    for url in entry_urls:
        row = _fetch(url, MAX_HTML)
        text = str(row.get("_text") or "")
        scripts: list[str] = []
        if text:
            for value in SCRIPT_RE.findall(text):
                resolved = _resolve(url, value)
                if _allowed(resolved, allowed_hosts) and resolved not in scripts:
                    scripts.append(resolved)
        pages.append({**_public(row), "script_urls": scripts})
        for script in scripts:
            if script not in root_urls:
                root_urls.append(script)

    for fallback in OBSERVED_PUBLIC_ROOT_SCRIPTS:
        if fallback not in root_urls:
            root_urls.append(fallback)

    root_rows: list[dict[str, Any]] = []
    candidate_map: dict[str, dict[str, Any]] = {}
    ui_contexts: list[dict[str, Any]] = []
    command_contexts: list[dict[str, Any]] = []
    translation_keys: list[dict[str, str]] = []
    matched_terms: set[str] = set()

    for url in root_urls[:10]:
        row = _fetch(url, MAX_ROOT_JS)
        text = str(row.get("_text") or "")
        hits = [term for term in target_terms if term.lower() in text.lower()]
        matched_terms.update(hits)
        root_rows.append({**_public(row), "matched_terms": hits[:40]})
        if not row.get("ok") or not text:
            continue
        for item in _contexts(text, url, hits):
            if item not in ui_contexts:
                ui_contexts.append(item)
        for item in _contexts(text, url, [term for term in COMMAND_NEEDLES if term.lower() in text.lower()]):
            if item not in command_contexts:
                command_contexts.append(item)
        for item in _translation_keys(text):
            if item not in translation_keys:
                translation_keys.append(item)
        for candidate in _js_references(text, url, allowed_hosts):
            existing = candidate_map.get(candidate["url"])
            if existing is None or candidate["priority"] > existing["priority"]:
                candidate_map[candidate["url"]] = candidate

    for url in OBSERVED_PUBLIC_SUPPORT_SCRIPTS:
        candidate_map.setdefault(
            url,
            {
                "url": url,
                "source": "observed_public_support_script",
                "order": -1,
                "source_context": "temporary observed beta fallback",
                "priority": _priority(url) + 200,
            },
        )

    queue = sorted(
        candidate_map.values(),
        key=lambda item: (-int(item.get("priority") or 0), int(item.get("order") or 0), str(item["url"])),
    )
    prefix_requests = 0
    prefix_successes = 0
    full_requests = 0
    full_successes = 0
    matched_assets: list[dict[str, Any]] = []
    scanned: set[str] = set()
    full_fetched: set[str] = set()
    index = 0

    while index < len(queue) and prefix_requests < MAX_PREFIX_REQUESTS:
        candidate = queue[index]
        index += 1
        url = str(candidate["url"])
        if url in scanned or not _allowed(url, allowed_hosts):
            continue
        scanned.add(url)
        prefix = _fetch(url, PREFIX_BYTES)
        prefix_requests += 1
        text = str(prefix.get("_text") or "")
        if prefix.get("ok"):
            prefix_successes += 1
        hits = [term for term in target_terms if term.lower() in text.lower()]
        keys = _translation_keys(text) if text else []
        known_key_hits = [
            item["key"]
            for item in translation_keys
            if item.get("key") and item["key"].lower() in text.lower()
        ] if text else []
        command_hits = [term for term in COMMAND_NEEDLES if term.lower() in text.lower()] if text else []
        matched_terms.update(hits)
        for item in keys:
            if item not in translation_keys:
                translation_keys.append(item)
        should_full = bool(
            hits
            or keys
            or known_key_hits
            or (command_hits and int(candidate.get("priority") or 0) >= 55)
            or int(candidate.get("priority") or 0) >= 180
        )
        asset_row = {
            "url": url,
            "source": candidate.get("source"),
            "priority": candidate.get("priority"),
            "prefix_http_status": prefix.get("http_status"),
            "prefix_length": prefix.get("body_length_read"),
            "prefix_sha256": prefix.get("body_sha256"),
            "matched_terms": hits[:40],
            "translation_keys": keys[:20],
            "known_translation_key_hits": known_key_hits[:20],
            "command_terms": command_hits[:30],
            "full_fetched": False,
        }

        if should_full and full_requests < MAX_FULL_MATCHES:
            full = _fetch(url, MAX_FULL_JS)
            full_requests += 1
            full_fetched.add(url)
            full_text = str(full.get("_text") or "")
            if full.get("ok"):
                full_successes += 1
            full_hits = [term for term in target_terms if term.lower() in full_text.lower()]
            full_keys = _translation_keys(full_text) if full_text else []
            for item in full_keys:
                if item not in translation_keys:
                    translation_keys.append(item)
            key_terms = [item["key"] for item in translation_keys if item.get("key")]
            context_terms = list(dict.fromkeys([*full_hits, *key_terms, *COMMAND_NEEDLES]))
            contexts = _contexts(full_text, url, [term for term in context_terms if term.lower() in full_text.lower()])
            for context in contexts:
                if context["term"] in COMMAND_NEEDLES or any(
                    marker.lower() in context["context"].lower()
                    for marker in ("cmdcode", "/vehicle/set/send", "callnative", "sendencryptiondata", "reboot", "clear")
                ):
                    if context not in command_contexts and len(command_contexts) < MAX_CONTEXTS:
                        command_contexts.append(context)
                elif context not in ui_contexts and len(ui_contexts) < MAX_CONTEXTS:
                    ui_contexts.append(context)
            for child in _js_references(full_text, url, allowed_hosts):
                if child["url"] not in candidate_map:
                    candidate_map[child["url"]] = child
                    queue.append(child)
            asset_row.update(
                {
                    "full_fetched": True,
                    "full_http_status": full.get("http_status"),
                    "full_length": full.get("body_length_read"),
                    "full_sha256": full.get("body_sha256"),
                    "full_matched_terms": full_hits[:50],
                    "full_translation_keys": full_keys[:20],
                }
            )
        if hits or keys or known_key_hits or command_hits or asset_row["full_fetched"]:
            matched_assets.append(asset_row)

    return {
        "ok": True,
        "beta_only": True,
        "focus": "active_error_clear_resume_reboot_contract_recovery",
        "read_only": True,
        "public_unauthenticated_h5_only": True,
        "mutation_calls_executed": False,
        "live_command_call_executed": False,
        "notification_detail_call_executed": False,
        "current_error_code_used_as_search_term": str(error_code or "") or None,
        "current_error_title_used_as_search_term": str(error_title or "") or None,
        "allowed_hosts": sorted(allowed_hosts),
        "limits": {
            "prefix_bytes": PREFIX_BYTES,
            "max_prefix_requests": MAX_PREFIX_REQUESTS,
            "max_full_matches": MAX_FULL_MATCHES,
            "max_full_js": MAX_FULL_JS,
        },
        "pages": pages,
        "root_scripts": root_rows,
        "candidate_count": len(candidate_map),
        "prefix_request_count": prefix_requests,
        "prefix_success_count": prefix_successes,
        "full_request_count": full_requests,
        "full_success_count": full_successes,
        "matched_terms": sorted(matched_terms),
        "translation_keys": translation_keys[:40],
        "matched_assets": matched_assets[:60],
        "ui_contexts": ui_contexts[:MAX_CONTEXTS],
        "command_contexts": command_contexts[:MAX_CONTEXTS],
        "note": (
            "Public GET-only discovery searches the current error code/title plus the exact "
            "Clear and resume and Reboot Mower UI labels, their translation keys, command "
            "wrappers and nearby request shapes. It never calls the private mower command "
            "endpoint or the notification detail/read endpoint."
        ),
    }
'''
(COMPONENT / "error_h5_discovery.py").write_text(error_discovery, encoding="utf-8")


# Diagnostics: stop running Maintenance/Reports discovery and devote the beta probe
# to the active error and its action contracts.
diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    "from .maintenance_h5_discovery import probe_maintenance_h5\n",
    "from .maintenance_h5_discovery import probe_maintenance_h5\nfrom .error_h5_discovery import probe_error_h5\nfrom .state_semantics import error_transition_diagnostics\n",
    "error diagnostics imports",
)
diagnostics = replace_once(
    diagnostics,
    '''    0.4.3-beta9 performs compact Mowing Reports transport recovery plus cross-file Parts maintenance alias/call-site tracing\n    within the bounded read-only public-H5 inspection; crawler budgets and output are reduced, and no mutation or report API request runs.\n''',
    '''    0.4.3-beta10 pauses Maintenance/Mowing Reports discovery and focuses Download diagnostics on the active error,\n    MQTT-to-private arbitration evidence, raw vendor notification fields and public-H5 recovery of Clear and resume / Reboot Mower contracts.\n''',
    "diagnostics beta focus",
)
old_probe = '''    try:\n        maintenance_h5_discovery = await hass.async_add_executor_job(\n            probe_maintenance_h5, coordinator.client\n        )\n    except Exception as err:  # noqa: BLE001 - optional beta diagnostics discovery\n        maintenance_h5_discovery = {\n            "ok": False, "read_only": True, "beta_only": True,\n            "mutation_calls_executed": False,\n            "error_type": type(err).__name__, "error": sanitize(str(err)),\n        }\n\n'''
new_probe = '''    maintenance_h5_discovery = {\n        "ok": True,\n        "read_only": True,\n        "beta_only": True,\n        "paused": True,\n        "reason": "0.4.3-beta10 diagnostics focus only on active error action recovery",\n        "mutation_calls_executed": False,\n    }\n    try:\n        error_command_discovery = await hass.async_add_executor_job(\n            probe_error_h5,\n            coordinator.client,\n            str(data.get("error_code") or ""),\n            str(data.get("error_title") or data.get("error_text") or ""),\n        )\n    except Exception as err:  # noqa: BLE001 - optional beta diagnostics discovery\n        error_command_discovery = {\n            "ok": False,\n            "read_only": True,\n            "beta_only": True,\n            "mutation_calls_executed": False,\n            "live_command_call_executed": False,\n            "notification_detail_call_executed": False,\n            "error_type": type(err).__name__,\n            "error": sanitize(str(err)),\n        }\n\n'''
diagnostics = replace_once(diagnostics, old_probe, new_probe, "focused diagnostics probe")
diagnostics = replace_once(
    diagnostics,
    '''        "problem_history": sanitize(deepcopy(problem_history)),\n        "latest_notification": sanitize(\n''',
    '''        "problem_history": sanitize(deepcopy(problem_history)),\n        "error_investigation": sanitize(\n            {\n                "policy": "private_cloud_canonical_mqtt_transition_trigger",\n                "transition": error_transition_diagnostics(coordinator),\n                "raw_index2_vehicle_state": (raw.get("index2") or {}).get("vehicle_state"),\n                "raw_auth_vehicle_state": (raw.get("auth_item") or {}).get("vehicle_state"),\n                "raw_index2_error_data": deepcopy((raw.get("index2") or {}).get("error_data") or []),\n                "vendor_notification_raw_cache": deepcopy(\n                    getattr(coordinator, "_notification_raw_cache", None)\n                ),\n                "vendor_notification_normalized_cache": deepcopy(\n                    getattr(coordinator, "_notification_cache", None)\n                ),\n                "command_discovery": deepcopy(error_command_discovery),\n            }\n        ),\n        "latest_notification": sanitize(\n''',
    "error investigation output",
)
diagnostics = replace_once(
    diagnostics,
    '''                "style": data.get("notification_style"),\n                "notification_code": data.get("notification_code"),\n''',
    '''                "style": data.get("notification_style"),\n                "variable": deepcopy(data.get("notification_variable")),\n                "notification_code": data.get("notification_code"),\n''',
    "notification variable diagnostics",
)
diagnostics = replace_once(
    diagnostics,
    '''            "Normal diagnostics use current coordinator state and caches; 0.4.3-beta9 keeps the H5 probe compact and focused on report transport plus handleH5MowerSet export/import call-site evidence.",\n            "The beta H5 inspection sends no account or mower identity and executes no maintenance mutation or mower command.",\n''',
    '''            "0.4.3-beta10 pauses Maintenance/Mowing Reports discovery and focuses the beta-only public H5 probe on Clear and resume / Reboot Mower evidence for the active error.",\n            "The error-action H5 inspection sends no account or mower identity and executes no mower command or notification-detail/read action.",\n''',
    "diagnostics notes",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")


test_source = r'''"""Regression contracts for Navimower 0.4.3-beta10 error diagnostics."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta10_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta10"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta10.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta10")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta10")


def test_beta10_error_sensor_is_cloud_canonical() -> None:
    source = (COMPONENT / "state_semantics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert 'value_fn=lambda data: data.get("error_text") or "No errors"' in source
    assert '"private_cloud_canonical_mqtt_transition_trigger"' in source
    assert 'transition = bool(state_name and state_name != previous_named)' in source
    assert 'state_name in {"Error", "Self-Checking"} or previous_named == "Error"' in source
    assert 'snapshot["error_text"] = "Error"' not in source
    assert 'snapshot["docked_source"] = "mqtt_error_state"' not in source


def test_beta10_retains_raw_vendor_notification_feed() -> None:
    source = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    assert 'coordinator._notification_raw_cache = deepcopy(response)' in source
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"vendor_notification_raw_cache"' in diagnostics
    assert '"vendor_notification_normalized_cache"' in diagnostics
    assert '"variable": deepcopy(data.get("notification_variable"))' in diagnostics


def test_beta10_diagnostics_focuses_only_error_action_discovery() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "from .error_h5_discovery import probe_error_h5" in diagnostics
    assert "probe_error_h5," in diagnostics
    assert '"paused": True' in diagnostics
    assert '"error_investigation"' in diagnostics
    assert '"command_discovery": deepcopy(error_command_discovery)' in diagnostics
    assert "probe_maintenance_h5, coordinator.client" not in diagnostics


def test_beta10_error_h5_probe_is_strictly_read_only() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    for phrase in (
        "Clear and resume",
        "Reboot Mower",
        "clearError",
        "rebootMower",
        "/vehicle/set/send",
        "c:behavior",
        "cmdCode",
        "MAX_PREFIX_REQUESTS = 180",
        "PREFIX_BYTES = 64 * 1024",
        'method="GET"',
        '"mutation_calls_executed": False',
        '"live_command_call_executed": False',
        '"notification_detail_call_executed": False',
    ):
        assert phrase in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source


def test_beta10_error_probe_keeps_bounded_evidence() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        '"translation_keys"',
        '"matched_assets"',
        '"ui_contexts"',
        '"command_contexts"',
        '"prefix_request_count"',
        '"full_request_count"',
    ):
        assert phrase in source
'''
(ROOT / "tests" / "test_v043_beta10.py").write_text(test_source, encoding="utf-8")


notes = '''title: Navimower 0.4.3-beta10\n\nFocused active-error arbitration and command-contract diagnostics.\n\n### Changed\n\n- Make the Error sensor private-cloud canonical: repeated MQTT `Error` messages no longer overwrite a detailed cloud fault with generic `Error`.\n- Use named MQTT Error transitions only to invalidate `index2`/`auth_list` and request one fast private refresh; repeated identical MQTT state does not trigger another error-driven poll.\n- Show `No errors` when the Error sensor has no active cloud fault.\n- Pause Maintenance and Mowing Reports H5 discovery in Download diagnostics for this beta.\n\n### Added\n\n- Preserve the un-normalized vendor Device notification feed in memory and expose a sanitized copy in diagnostics, including fields not retained by the public notification sensor.\n- Add a dedicated public-H5 error action probe for the exact `Clear and resume`, `Reboot Mower` and `Got it` UI labels, translation keys, current error code/title, nearby request endpoints, command payload shapes and native bridge calls.\n- Add MQTT error-transition evidence and current raw `index2.error_data` to the focused error-investigation diagnostics section.\n\n### Safety\n\n- Download diagnostics remains read-only. The error-action probe performs only bounded unauthenticated public HTTPS GETs.\n- No `Clear and resume`, reboot, Resume, notification-detail/read, or other mower command is executed.\n- The private current error code/title is used only as a local search term against already-downloaded public JavaScript and is never sent as mower identity.\n'''
(ROOT / ".github" / "release-notes" / "0.4.3-beta10.md").write_text(notes, encoding="utf-8")


changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
entry = '''## 0.4.3-beta10\n\nFocused active-error arbitration and command-contract diagnostics.\n\n### Changed\n\n- Keep the Error sensor canonical to private-cloud `index2.error_data`; MQTT named Error is now a transition trigger instead of a temporary display source.\n- Deduplicate repeated identical MQTT Error states so only state edges request an error-driven private refresh.\n- Display `No errors` when no active cloud fault exists.\n- Pause Maintenance/Mowing Reports H5 discovery while this beta concentrates on active error commands.\n\n### Added\n\n- Preserve sanitized raw vendor notification-feed evidence for Download diagnostics.\n- Add a bounded public-H5 probe for `Clear and resume`, `Reboot Mower`, their translation keys, request shapes, endpoints and native bridge contexts.\n- Add focused error-transition, raw `index2.error_data`, raw/normalized notification and command-discovery evidence to diagnostics.\n\n### Safety\n\n- Diagnostics executes no mower mutation and no notification-detail/read action; the H5 probe is public GET-only.\n\n'''
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("Unexpected changelog header")
changelog_path.write_text("# Changelog\n\n" + entry + changelog[len("# Changelog\n\n"):], encoding="utf-8")
