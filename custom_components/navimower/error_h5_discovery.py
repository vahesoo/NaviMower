"""Focused read-only public H5 discovery for active error actions."""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .api.regions import canonical_region
from .diagnostics_sanitize import sanitize
from .maintenance_h5_discovery import (
    MOWER_SET_ARROW_WRAPPER_RE,
    MOWER_SET_WRAPPER_RE,
    _exported_aliases,
    _import_aliases_for_source,
    _named_callsite_contexts,
    _wrapper_definitions,
)

MAX_HTML = 256 * 1024
MAX_ROOT_JS = 1024 * 1024
PREFIX_BYTES = 768 * 1024
MAX_ROOT_REQUESTS = 4
MAX_PREFIX_REQUESTS = 14
MAX_FULL_MATCHES = 8
MAX_FULL_JS = 2 * 1024 * 1024
MAX_CONTEXTS = 80
CONTEXT_RADIUS = 1800
ACTION_CONTEXT_RADIUS = 12000
MAX_ACTION_NEIGHBORHOODS = 24
MAX_ACTION_LITERALS = 120
MAX_PROBE_SECONDS = 24.0
TIMEOUT = 2.5
MIN_REQUEST_TIMEOUT = 0.2

SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
JS_RE = re.compile(r"[\"']([^\"'\r\n]{1,420}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
ENDPOINT_RE = re.compile(
    r"[\"']((?:https?://[^\"'\s]+)?/?(?:mowerbot|vehicle|setting|robot|api)/[^\"'\r\n]{1,320})[\"']",
    re.I,
)
HTTP_RE = re.compile(r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,160})[\"']",
    re.I,
)
GENERIC_NATIVE_CALL_RE = re.compile(
    r"(?P<callee>(?:(?:window|globalThis|webkit|Android|android)[A-Za-z0-9_$.[\]]*\.)?"
    r"(?:sendEncryptionData|callNative|sendMessageToNative|postMessage|handleH5MowerSet|invoke))"
    r"\s*\((?P<args>.{0,1400}?)\)",
    re.I | re.S,
)
COMMAND_FIELD_RE = re.compile(
    r"(?P<key>cmdCode|cmd_code|command|action|behavior|method|event|code|type)"
    r"\s*:\s*(?P<value>[^,}\]]{1,220})",
    re.I,
)
STRING_LITERAL_RE = re.compile(
    r"(?P<quote>[\"'])(?P<value>(?:\\.|(?!\1).){2,180})(?P=quote)",
    re.S,
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
    "handleH5MowerSet",
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
    "handleH5MowerSet",
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

# Temporary beta-only fallback for the multi-signal lazy chunk observed in the
# beta10 diagnostics sample. Semantic scoring remains authoritative and this
# hint is removed once the Clear and resume contract is integrated.
OBSERVED_ERROR_COMMAND_ASSETS = ("index-594ad42d.js",)

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


def _fetch(url: str, limit: int, timeout: float = TIMEOUT) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerErrorDiagnostics/0.4.3-beta12",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(MIN_REQUEST_TIMEOUT, float(timeout))) as response:
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


def _deadline_fetch(url: str, limit: int, deadline: float) -> dict[str, Any]:
    """Fetch without starting work after the diagnostics wall-clock budget."""
    remaining = deadline - time.monotonic()
    if remaining <= MIN_REQUEST_TIMEOUT:
        return {
            "ok": False,
            "url": _safe_url(url),
            "budget_exhausted": True,
            "transport_error": "wall_clock_budget_exhausted",
        }
    return _fetch(url, limit, timeout=min(TIMEOUT, remaining))


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


def _candidate_queue_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Put proven error-command and native/request assets ahead of generic chunks."""
    url = str(item.get("url") or "")
    basename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    observed_rank = 0 if basename in OBSERVED_ERROR_COMMAND_ASSETS else 1
    support_rank = 0 if url in OBSERVED_PUBLIC_SUPPORT_SCRIPTS else 1
    return (
        observed_rank,
        support_rank,
        -int(item.get("priority") or 0),
        int(item.get("order") or 0),
        url,
    )


def _full_fetch_priority(
    candidate: dict[str, Any],
    hits: list[str],
    keys: list[dict[str, str]],
    known_key_hits: list[str],
    command_hits: list[str],
) -> tuple[int, list[str]]:
    """Rank full-fetch candidates only after all prefix evidence is known."""
    score = int(candidate.get("priority") or 0)
    reasons: list[str] = []
    lower_hits = {value.lower() for value in hits}
    lower_commands = {value.lower() for value in command_hits}
    basename = urllib.parse.urlsplit(str(candidate.get("url") or "")).path.rsplit("/", 1)[-1].lower()

    if basename in OBSERVED_ERROR_COMMAND_ASSETS:
        score += 1600
        reasons.append("observed_prior_multi_signal_asset")
    if keys or known_key_hits:
        score += 900 + 120 * (len(keys) + len(known_key_hits))
        reasons.append("translation_key_evidence")
    if any(label.lower() in lower_hits for label in UI_LABELS):
        score += 1400
        reasons.append("exact_error_action_label")
    if "handleh5mowerset" in lower_commands or "handleh5mowerset" in lower_hits:
        score += 1300
        reasons.append("mower_set_native_bridge")
    if any(value in lower_commands for value in ("cmdcode", "c:behavior", "/vehicle/set/send")):
        score += 1200
        reasons.append("private_command_shape")
    if "clear" in lower_commands and "resume" in lower_commands:
        score += 1500
        reasons.append("clear_plus_resume_prefix")
    if "clear" in lower_commands and ({"restart", "reboot"} & lower_commands):
        score += 800
        reasons.append("clear_plus_restart_prefix")
    if {"fault", "error"} & lower_commands and {"resume", "restart", "reboot"} & lower_commands:
        score += 500
        reasons.append("fault_recovery_terms")

    specific_hits = {
        "clearandresume",
        "clear_and_resume",
        "clearresume",
        "resumeaftererror",
        "clearerror",
        "reseterror",
        "clearfault",
        "resetfault",
        "rebootmower",
        "reboot_mower",
        "restartmower",
        "restart_mower",
    }
    if specific_hits & lower_hits:
        score += 1200
        reasons.append("specific_action_symbol")

    score += min(500, len(hits) * 70)
    score += min(350, len(command_hits) * 45)
    if not reasons and (hits or command_hits or int(candidate.get("priority") or 0) >= 180):
        reasons.append("generic_prefix_evidence")
    return score, list(dict.fromkeys(reasons))


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
            nearby = re.sub(r"\s+", " ", text[lo:hi]).strip()
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


def _action_neighborhoods(
    text: str, source: str, anchors: list[str]
) -> list[dict[str, Any]]:
    """Capture wide, bounded evidence around real error UI/action anchors."""
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for anchor in anchors:
        needle = str(anchor or "").strip()
        if not needle:
            continue
        start = 0
        found = 0
        while found < 2 and len(rows) < MAX_ACTION_NEIGHBORHOODS:
            index = lower.find(needle.lower(), start)
            if index < 0:
                break
            lo = max(0, index - ACTION_CONTEXT_RADIUS)
            hi = min(len(text), index + len(needle) + ACTION_CONTEXT_RADIUS)
            nearby = text[lo:hi]
            literals: list[str] = []
            for match in STRING_LITERAL_RE.finditer(nearby):
                value = re.sub(r"\s+", " ", match.group("value")).strip()
                if (
                    value
                    and value not in literals
                    and len(value) <= 180
                    and not value.startswith(("data:", "http://", "https://"))
                ):
                    literals.append(value)
                if len(literals) >= MAX_ACTION_LITERALS:
                    break
            native_calls = [
                {
                    "callee": match.group("callee"),
                    "args": re.sub(r"\s+", " ", match.group("args")).strip()[:1400],
                }
                for match in GENERIC_NATIVE_CALL_RE.finditer(nearby)
            ][:40]
            command_fields = [
                {
                    "key": match.group("key"),
                    "value": re.sub(r"\s+", " ", match.group("value")).strip()[:220],
                }
                for match in COMMAND_FIELD_RE.finditer(nearby)
            ][:60]
            rows.append(
                {
                    "anchor": needle,
                    "source": _safe_url(source),
                    "offset": index,
                    "window_start": lo,
                    "window_end": hi,
                    "string_literals": literals,
                    "endpoint_paths": sorted(set(ENDPOINT_RE.findall(nearby)))[:30],
                    "http_methods": sorted(set(value.upper() for value in HTTP_RE.findall(nearby))),
                    "native_calls": native_calls,
                    "command_fields": command_fields,
                    "js_references": [row["url"] for row in _js_references(nearby, source, {urllib.parse.urlsplit(source).netloc})[:30]],
                }
            )
            start = index + len(needle)
            found += 1
    return rows


def _translation_keys(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label in UI_LABELS:
        pattern = re.compile(
            r"[\"'](?P<key>[A-Za-z0-9_.-]{2,100})[\"']\s*:\s*[\"']"
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
        context = re.sub(r"\s+", " ", text[lo:hi]).strip()
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


def _mower_set_findings(text: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    definitions = _wrapper_definitions(
        text,
        source,
        (("function", MOWER_SET_WRAPPER_RE), ("arrow", MOWER_SET_ARROW_WRAPPER_RE)),
        "error_mower_set_wrapper_definition",
    )
    callsites = _named_callsite_contexts(
        text,
        source,
        definitions,
        "error_mower_set_direct_callsite",
    )
    exports: list[dict[str, Any]] = []
    for definition in definitions:
        local_name = str(definition.get("name") or "")
        for exported_name in _exported_aliases(text, local_name):
            row = {
                "source": _safe_url(source),
                "local_name": local_name,
                "exported_name": exported_name,
            }
            if row not in exports:
                exports.append(row)
    return definitions, exports, callsites


def _error_terms_nearby(context: str) -> list[str]:
    lower = str(context or "").lower()
    return [term for term in (*UI_LABELS, *COMMAND_NEEDLES) if term.lower() in lower]


def _append_unique(rows: list[dict[str, Any]], additions: list[dict[str, Any]], limit: int) -> None:
    for row in additions:
        if row not in rows:
            rows.append(row)
        if len(rows) >= limit:
            break


def probe_error_h5(client: Any, error_code: str = "", error_title: str = "") -> dict[str, Any]:
    """Inspect public H5 error-action assets within a strict diagnostics deadline."""
    started = time.monotonic()
    deadline = started + MAX_PROBE_SECONDS
    budget_exhausted = False
    stop_reason: str | None = None

    def fetch_bounded(url: str, limit: int) -> dict[str, Any]:
        nonlocal budget_exhausted, stop_reason
        row = _deadline_fetch(url, limit, deadline)
        if row.get("budget_exhausted") or time.monotonic() >= deadline:
            budget_exhausted = True
            stop_reason = stop_reason or "wall_clock_budget"
        return row

    host = _host(client)
    allowed_hosts = {urllib.parse.urlsplit(host).netloc, "cloud-acc.navimow.com"}
    entry_urls = (
        f"{host}/old/",
        "https://cloud-acc.navimow.com/navimow/",
    )
    dynamic_terms = [term for term in (str(error_code or ""), str(error_title or "")) if term]
    target_terms = list(dict.fromkeys([*BASE_TARGET_TERMS, *dynamic_terms]))

    pages: list[dict[str, Any]] = []
    root_urls: list[str] = []
    for url in entry_urls:
        if budget_exhausted:
            break
        row = fetch_bounded(url, MAX_HTML)
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
    fetched_texts: dict[str, str] = {}
    mower_set_wrapper_definitions: list[dict[str, Any]] = []
    mower_set_export_aliases: list[dict[str, Any]] = []
    mower_set_import_aliases: list[dict[str, Any]] = []
    mower_set_callsite_contexts: list[dict[str, Any]] = []
    action_neighborhoods: list[dict[str, Any]] = []
    action_anchors = list(dict.fromkeys([*UI_LABELS, *dynamic_terms]))

    for url in root_urls[:MAX_ROOT_REQUESTS]:
        if budget_exhausted:
            break
        row = fetch_bounded(url, MAX_ROOT_JS)
        text = str(row.get("_text") or "")
        hits = [term for term in target_terms if term.lower() in text.lower()]
        matched_terms.update(hits)
        root_rows.append({**_public(row), "matched_terms": hits[:40]})
        if not row.get("ok") or not text:
            continue
        fetched_texts[_safe_url(url)] = text
        for item in _contexts(text, url, hits):
            if item not in ui_contexts:
                ui_contexts.append(item)
        for item in _contexts(text, url, [term for term in COMMAND_NEEDLES if term.lower() in text.lower()]):
            if item not in command_contexts:
                command_contexts.append(item)
        for item in _translation_keys(text):
            if item not in translation_keys:
                translation_keys.append(item)
        _append_unique(
            action_neighborhoods,
            _action_neighborhoods(text, url, action_anchors),
            MAX_ACTION_NEIGHBORHOODS,
        )
        definitions, exports, callsites = _mower_set_findings(text, url)
        _append_unique(mower_set_wrapper_definitions, definitions, 32)
        _append_unique(mower_set_export_aliases, exports, 32)
        _append_unique(mower_set_callsite_contexts, callsites, 64)
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

    for basename in OBSERVED_ERROR_COMMAND_ASSETS:
        observed_url = f"{host}/old/assets/{basename}"
        candidate_map.setdefault(
            observed_url,
            {
                "url": observed_url,
                "source": "observed_error_command_asset",
                "order": -2,
                "source_context": "temporary proven error-command asset fallback",
                "priority": _priority(observed_url) + 5000,
            },
        )

    queue = sorted(candidate_map.values(), key=_candidate_queue_key)
    prefix_requests = 0
    prefix_successes = 0
    full_requests = 0
    full_successes = 0
    scanned: set[str] = set()
    prefix_evidence: list[dict[str, Any]] = []
    index = 0

    # Pass 1: collect bounded prefix evidence from the whole candidate queue. Do
    # not spend any full-fetch slots yet; beta10 could exhaust the 18-slot quota
    # before later, stronger multi-signal candidates were reached.
    while index < len(queue) and prefix_requests < MAX_PREFIX_REQUESTS and not budget_exhausted:
        candidate = queue[index]
        index += 1
        url = str(candidate["url"])
        if url in scanned or not _allowed(url, allowed_hosts):
            continue
        scanned.add(url)
        prefix = fetch_bounded(url, PREFIX_BYTES)
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
        full_score, full_reasons = _full_fetch_priority(
            candidate, hits, keys, known_key_hits, command_hits
        )
        should_full = bool(
            hits
            or keys
            or known_key_hits
            or command_hits
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
            "full_fetch_score": full_score,
            "full_fetch_reasons": full_reasons,
            "full_fetch_eligible": should_full,
            "full_fetched": False,
        }
        prefix_evidence.append(asset_row)

        if text:
            for child in _js_references(text, url, allowed_hosts):
                if child["url"] in candidate_map:
                    continue
                candidate_map[child["url"]] = child
                queue.append(child)
            queue[index:] = sorted(queue[index:], key=_candidate_queue_key)

    full_plan = sorted(
        [row for row in prefix_evidence if row.get("full_fetch_eligible")],
        key=lambda row: (
            -int(row.get("full_fetch_score") or 0),
            -int(row.get("priority") or 0),
            str(row.get("url") or ""),
        ),
    )
    asset_by_url = {str(row.get("url") or ""): row for row in prefix_evidence}

    # Pass 2: spend the full-fetch budget only on the strongest prefix evidence.
    for rank, planned in enumerate(full_plan[:MAX_FULL_MATCHES], start=1):
        if budget_exhausted:
            break
        url = str(planned.get("url") or "")
        if not url:
            continue
        full = fetch_bounded(url, MAX_FULL_JS)
        full_requests += 1
        full_text = str(full.get("_text") or "")
        if full.get("ok"):
            full_successes += 1
            fetched_texts[_safe_url(url)] = full_text
        full_hits = [term for term in target_terms if term.lower() in full_text.lower()]
        full_keys = _translation_keys(full_text) if full_text else []
        for item in full_keys:
            if item not in translation_keys:
                translation_keys.append(item)
        key_terms = [item["key"] for item in translation_keys if item.get("key")]
        context_terms = list(dict.fromkeys([*full_hits, *key_terms, *COMMAND_NEEDLES]))
        contexts = _contexts(
            full_text,
            url,
            [term for term in context_terms if term.lower() in full_text.lower()],
        )
        for context in contexts:
            if context["term"] in COMMAND_NEEDLES or any(
                marker.lower() in context["context"].lower()
                for marker in (
                    "cmdcode",
                    "/vehicle/set/send",
                    "handleh5mowerset",
                    "callnative",
                    "sendencryptiondata",
                    "reboot",
                    "clear",
                )
            ):
                if context not in command_contexts and len(command_contexts) < MAX_CONTEXTS:
                    command_contexts.append(context)
            elif context not in ui_contexts and len(ui_contexts) < MAX_CONTEXTS:
                ui_contexts.append(context)

        _append_unique(
            action_neighborhoods,
            _action_neighborhoods(full_text, url, action_anchors),
            MAX_ACTION_NEIGHBORHOODS,
        )
        definitions, exports, callsites = _mower_set_findings(full_text, url)
        _append_unique(mower_set_wrapper_definitions, definitions, 32)
        _append_unique(mower_set_export_aliases, exports, 32)
        _append_unique(mower_set_callsite_contexts, callsites, 64)

        asset_row = asset_by_url[url]
        asset_row.update(
            {
                "full_fetch_rank": rank,
                "full_fetched": True,
                "full_http_status": full.get("http_status"),
                "full_length": full.get("body_length_read"),
                "full_sha256": full.get("body_sha256"),
                "full_matched_terms": full_hits[:50],
                "full_translation_keys": full_keys[:20],
            }
        )

    # Recover the beta9-proven handleH5MowerSet wrapper across ES-module
    # export/import aliases and capture the actual imported call arguments.
    for export_row in list(mower_set_export_aliases):
        source_url = str(export_row.get("source") or "")
        exported_name = str(export_row.get("exported_name") or "")
        if not source_url or not exported_name:
            continue
        for consumer_url, consumer_text in fetched_texts.items():
            if consumer_url == source_url:
                continue
            imports = _import_aliases_for_source(
                consumer_text,
                consumer_url,
                source_url,
                [exported_name],
            )
            for import_row in imports:
                enriched_import = {"source": consumer_url, **import_row}
                _append_unique(mower_set_import_aliases, [enriched_import], 48)
                local_name = str(import_row.get("local_name") or "")
                if not local_name:
                    continue
                synthetic_definition = {
                    "name": local_name,
                    "endpoint": None,
                    "definition_offset": -10_000,
                }
                imported_calls = _named_callsite_contexts(
                    consumer_text,
                    consumer_url,
                    [synthetic_definition],
                    "error_mower_set_imported_callsite",
                )
                for callsite in imported_calls:
                    callsite["exported_name"] = exported_name
                    callsite["imported_from"] = source_url
                    callsite["error_terms_nearby"] = _error_terms_nearby(
                        str(callsite.get("context") or "")
                    )
                _append_unique(mower_set_callsite_contexts, imported_calls, 64)

    matched_assets = [
        row
        for row in prefix_evidence
        if row.get("matched_terms")
        or row.get("translation_keys")
        or row.get("known_translation_key_hits")
        or row.get("command_terms")
        or row.get("full_fetched")
    ]

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
            "max_root_requests": MAX_ROOT_REQUESTS,
            "max_prefix_requests": MAX_PREFIX_REQUESTS,
            "max_full_matches": MAX_FULL_MATCHES,
            "max_full_js": MAX_FULL_JS,
            "max_probe_seconds": MAX_PROBE_SECONDS,
            "per_request_timeout_seconds": TIMEOUT,
        },
        "selection": {
            "mode": "two_pass_prefix_score_then_full",
            "bounded_by_wall_clock": True,
            "full_fetch_candidate_count": len(full_plan),
            "full_fetch_plan": [
                {
                    "url": row.get("url"),
                    "score": row.get("full_fetch_score"),
                    "reasons": row.get("full_fetch_reasons"),
                    "command_terms": row.get("command_terms"),
                    "matched_terms": row.get("matched_terms"),
                }
                for row in full_plan[: min(30, len(full_plan))]
            ],
        },
        "execution": {
            "wall_clock_budget_seconds": MAX_PROBE_SECONDS,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "budget_exhausted": budget_exhausted,
            "stop_reason": stop_reason,
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
        "matched_assets": matched_assets[:80],
        "ui_contexts": ui_contexts[:MAX_CONTEXTS],
        "command_contexts": command_contexts[:MAX_CONTEXTS],
        "mower_set_wrapper_definitions": mower_set_wrapper_definitions[:32],
        "mower_set_export_aliases": mower_set_export_aliases[:32],
        "mower_set_import_aliases": mower_set_import_aliases[:48],
        "mower_set_callsite_contexts": mower_set_callsite_contexts[:64],
        "action_neighborhoods": action_neighborhoods[:MAX_ACTION_NEIGHBORHOODS],
        "note": (
            "Public GET-only discovery prioritizes proven error-command assets, scans wider "
            "bundle prefixes, and captures bounded action-neighborhood literals/native-call evidence "
            "inside a strict wall-clock budget. Partial "
            "evidence is returned when the budget is exhausted. It never calls the private "
            "mower command endpoint or the notification detail/read endpoint."
        ),
    }
