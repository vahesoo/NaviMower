"""Focused read-only public H5 discovery for active error actions."""
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
