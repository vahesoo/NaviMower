"""Read-only public H5 discovery for Navimow Maintenance & Tools."""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .api.regions import canonical_region
from .diagnostics_sanitize import sanitize

ENTRY_PATHS = ("/old/", "/maintenance/", "/vehicle/maintenance/", "/setting/maintenance/")
TARGET_TERMS = (
    "componentMaintenance", "partsMaintenance", "partMaintenance",
    "get-component-maintenance", "resetBlade", "resetKnife", "changeBlade",
    "maintenanceMode", "enterMaintenance", "exitMaintenance",
    "cutHeight", "cuttingHeight", "ToolBox",
)
THEME_TERMS = ("maintenance", "blade", "knife", "toolbox", "cutheight", "cuttingheight")
MAX_HTML = 256 * 1024
MAX_JS = 2 * 1024 * 1024
MAX_ASSETS = 16
MAX_CONTEXTS = 48
RADIUS = 6000
TIMEOUT = 5

SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
JS_RE = re.compile(r"[\"']([^\"'\r\n]{1,420}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,280})[\"']", re.I)
HTTP_RE = re.compile(r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
SKIP_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
OBJECT_KEY_RE = re.compile(r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{0,90})[\"']?\s*:")
BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,160})[\"']", re.I
)

def _host(client: Any) -> str:
    region = canonical_region(getattr(client, "region", "fra"))
    return f"https://navimow-h5-{region}.willand.com"

def _safe_url(url: str) -> str:
    p = urllib.parse.urlsplit(str(url))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))

def _fetch(url: str, limit: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta2",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read(limit + 1)
            status = int(getattr(response, "status", 200))
            ctype = str(response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as err:
        raw = err.read(limit + 1)
        status = int(err.code)
        ctype = str(err.headers.get("Content-Type", ""))
    except urllib.error.URLError as err:
        return {"ok": False, "url": _safe_url(url), "transport_error": sanitize(str(err.reason))}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "url": _safe_url(url), "transport_error": sanitize(f"{type(err).__name__}: {err}")}
    truncated = len(raw) > limit
    raw = raw[:limit]
    return {
        "ok": 200 <= status < 400,
        "url": _safe_url(url),
        "http_status": status,
        "content_type": ctype,
        "body_length_read": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "truncated": truncated,
        "_text": raw.decode("utf-8", errors="replace"),
    }

def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "_text"}

def _structure(text: str) -> dict[str, Any]:
    keys = []
    for key in OBJECT_KEY_RE.findall(text):
        if key not in keys:
            keys.append(key)
        if len(keys) >= 80:
            break
    bridges = []
    for m in BRIDGE_RE.finditer(text):
        item = {"callee": m.group("callee"), "method": m.group("method")}
        if item not in bridges:
            bridges.append(item)
        if len(bridges) >= 32:
            break
    return {
        "mowerbot_paths": sorted(set(MOWERBOT_RE.findall(text)))[:48],
        "http_methods": sorted({v.upper() for v in HTTP_RE.findall(text)}),
        "skip_encryption": sorted({v.lower() for v in SKIP_RE.findall(text)}),
        "object_keys": keys,
        "bridge_calls": bridges,
    }

def _contexts(text: str, source: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows = []
    for term in TARGET_TERMS:
        start = 0
        for _ in range(4):
            idx = lower.find(term.lower(), start)
            if idx < 0 or len(rows) >= MAX_CONTEXTS:
                break
            lo, hi = max(0, idx - RADIUS), min(len(text), idx + len(term) + RADIUS)
            nearby = text[lo:hi]
            rows.append({
                "term": term,
                "source": _safe_url(source),
                **_structure(nearby),
                "context": re.sub(r"\s+", " ", nearby).strip(),
            })
            start = idx + len(term)
    return rows

def probe_maintenance_h5(client: Any) -> dict[str, Any]:
    host = _host(client)
    pages = []
    queue = []
    seen = set()
    for path in ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, MAX_HTML)
        text = str(result.get("_text") or "")
        row = _public(result)
        scripts = []
        if text:
            for value in SCRIPT_RE.findall(text):
                asset = _safe_url(urllib.parse.urljoin(url, value.strip()))
                if asset not in scripts:
                    scripts.append(asset)
            row["script_urls"] = scripts[:8]
            queue.extend(scripts[:8])
        pages.append(row)

    assets = []
    contexts = []
    index = 0
    while index < len(queue) and len(assets) < MAX_ASSETS:
        url = queue[index]
        index += 1
        if url in seen:
            continue
        seen.add(url)
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        if text:
            found = _contexts(text, url)
            contexts.extend(found)
            row["target_terms"] = sorted({item["term"] for item in found})
            row["request_paths"] = sorted({p for item in found for p in item["mowerbot_paths"]})
            lower = text.lower()
            for match in JS_RE.finditer(text):
                lo, hi = max(0, match.start() - 3500), min(len(text), match.end() + 3500)
                nearby = lower[lo:hi]
                if not any(term in nearby for term in THEME_TERMS):
                    continue
                dynamic = _safe_url(urllib.parse.urljoin(url, match.group(1).strip()))
                if dynamic not in seen and dynamic not in queue:
                    queue.append(dynamic)
        assets.append(row)

    unique_contexts = []
    markers = set()
    for row in contexts:
        marker = (row["term"], row["source"], row["context"])
        if marker in markers:
            continue
        markers.add(marker)
        unique_contexts.append(row)
        if len(unique_contexts) >= MAX_CONTEXTS:
            break

    requests = []
    request_markers = set()
    for row in unique_contexts:
        for path in row["mowerbot_paths"]:
            marker = (path, row["source"])
            if marker in request_markers:
                continue
            request_markers.add(marker)
            requests.append({
                "path": path,
                "source": row["source"],
                "matched_term": row["term"],
                "http_methods": row["http_methods"],
                "skip_encryption": row["skip_encryption"],
                "object_keys": row["object_keys"],
                "bridge_calls": row["bridge_calls"],
            })

    return {
        "read_only": True,
        "beta_only": True,
        "public_unauthenticated_h5_only": True,
        "normal_mower_polling_unchanged": True,
        "mutation_calls_executed": False,
        "source": "home_assistant_download",
        "host": host,
        "entry_paths": list(ENTRY_PATHS),
        "targets": list(TARGET_TERMS),
        "credential_safety": "No token, cookie, uid, device id, mower serial or encrypted p:101 business payload is sent to H5. Only public GET resources are read.",
        "investigation_goal": "Recover official Maintenance & Tools request structure for blade runtime reset and mower maintenance mode, including payload keys, HTTP methods, encryption flags and native bridge calls.",
        "pages": pages,
        "assets": assets,
        "contexts": unique_contexts,
        "request_candidates": requests,
        "note": "0.4.3-beta2 records bounded public H5 source context only. It does not reset maintenance counters, enter maintenance mode, change cutting height or execute any mower command.",
    }
