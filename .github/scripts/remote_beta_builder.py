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


# Release identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta11":
    raise SystemExit(f"Expected 0.4.3-beta11 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta12"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


# Bound active-error public-H5 diagnostics by both request counts and wall time.
error_path = COMPONENT / "error_h5_discovery.py"
error = error_path.read_text(encoding="utf-8")
error = replace_once(
    error,
    "import hashlib\nimport re\nfrom typing import Any\n",
    "import hashlib\nimport re\nimport time\nfrom typing import Any\n",
    "error discovery time import",
)
error = replace_once(
    error,
    '''MAX_HTML = 256 * 1024
MAX_ROOT_JS = 2 * 1024 * 1024
PREFIX_BYTES = 64 * 1024
MAX_PREFIX_REQUESTS = 180
MAX_FULL_MATCHES = 18
MAX_FULL_JS = 2 * 1024 * 1024
MAX_CONTEXTS = 80
CONTEXT_RADIUS = 1800
TIMEOUT = 5
''',
    '''MAX_HTML = 256 * 1024
MAX_ROOT_JS = 1024 * 1024
PREFIX_BYTES = 64 * 1024
MAX_ROOT_REQUESTS = 4
MAX_PREFIX_REQUESTS = 32
MAX_FULL_MATCHES = 6
MAX_FULL_JS = 2 * 1024 * 1024
MAX_CONTEXTS = 80
CONTEXT_RADIUS = 1800
MAX_PROBE_SECONDS = 24.0
TIMEOUT = 2.5
MIN_REQUEST_TIMEOUT = 0.2
''',
    "error discovery bounded limits",
)
error = replace_once(
    error,
    '"User-Agent": "Mozilla/5.0 NavimowerErrorDiagnostics/0.4.3-beta11",',
    '"User-Agent": "Mozilla/5.0 NavimowerErrorDiagnostics/0.4.3-beta12",',
    "error discovery user agent",
)
error = replace_once(
    error,
    "def _fetch(url: str, limit: int) -> dict[str, Any]:",
    "def _fetch(url: str, limit: int, timeout: float = TIMEOUT) -> dict[str, Any]:",
    "fetch timeout argument",
)
error = replace_once(
    error,
    "with urllib.request.urlopen(request, timeout=TIMEOUT) as response:",
    "with urllib.request.urlopen(request, timeout=max(MIN_REQUEST_TIMEOUT, float(timeout))) as response:",
    "fetch bounded socket timeout",
)
error = replace_once(
    error,
    '''\n\ndef _public(row: dict[str, Any]) -> dict[str, Any]:
''',
    '''\n\ndef _deadline_fetch(url: str, limit: int, deadline: float) -> dict[str, Any]:
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
''',
    "deadline fetch helper",
)
error = replace_once(
    error,
    '''\n\ndef _full_fetch_priority(
''',
    '''\n\ndef _candidate_queue_key(item: dict[str, Any]) -> tuple[Any, ...]:
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
''',
    "candidate queue priority helper",
)
error = replace_once(
    error,
    '''def probe_error_h5(client: Any, error_code: str = "", error_title: str = "") -> dict[str, Any]:
    """Inspect only public H5 assets for error-dialog command evidence."""
    host = _host(client)
''',
    '''def probe_error_h5(client: Any, error_code: str = "", error_title: str = "") -> dict[str, Any]:
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
''',
    "probe deadline state",
)
error = replace_once(
    error,
    '''    entry_urls = (
        f"{host}/old/",
        f"{host}/maintenance/",
        "https://cloud-acc.navimow.com/navimow/",
    )
''',
    '''    entry_urls = (
        f"{host}/old/",
        "https://cloud-acc.navimow.com/navimow/",
    )
''',
    "error-only entry roots",
)
error = replace_once(
    error,
    '''    for url in entry_urls:
        row = _fetch(url, MAX_HTML)
''',
    '''    for url in entry_urls:
        if budget_exhausted:
            break
        row = fetch_bounded(url, MAX_HTML)
''',
    "bounded entry fetch",
)
error = replace_once(
    error,
    '''    for url in root_urls[:10]:
        row = _fetch(url, MAX_ROOT_JS)
''',
    '''    for url in root_urls[:MAX_ROOT_REQUESTS]:
        if budget_exhausted:
            break
        row = fetch_bounded(url, MAX_ROOT_JS)
''',
    "bounded root fetch",
)
error = replace_once(
    error,
    '''    queue = sorted(
        candidate_map.values(),
        key=lambda item: (-int(item.get("priority") or 0), int(item.get("order") or 0), str(item["url"])),
    )
''',
    '''    for basename in OBSERVED_ERROR_COMMAND_ASSETS:
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
''',
    "prioritized observed error asset",
)
error = replace_once(
    error,
    "while index < len(queue) and prefix_requests < MAX_PREFIX_REQUESTS:",
    "while index < len(queue) and prefix_requests < MAX_PREFIX_REQUESTS and not budget_exhausted:",
    "prefix wall-clock guard",
)
error = replace_once(
    error,
    "        prefix = _fetch(url, PREFIX_BYTES)\n",
    "        prefix = fetch_bounded(url, PREFIX_BYTES)\n",
    "bounded prefix fetch",
)
error = replace_once(
    error,
    '''                candidate_map[child["url"]] = child
                queue.append(child)

    full_plan = sorted(
''',
    '''                candidate_map[child["url"]] = child
                queue.append(child)
            queue[index:] = sorted(queue[index:], key=_candidate_queue_key)

    full_plan = sorted(
''',
    "reprioritize discovered children",
)
error = replace_once(
    error,
    '''    for rank, planned in enumerate(full_plan[:MAX_FULL_MATCHES], start=1):
        url = str(planned.get("url") or "")
''',
    '''    for rank, planned in enumerate(full_plan[:MAX_FULL_MATCHES], start=1):
        if budget_exhausted:
            break
        url = str(planned.get("url") or "")
''',
    "full fetch wall-clock guard",
)
error = replace_once(
    error,
    "        full = _fetch(url, MAX_FULL_JS)\n",
    "        full = fetch_bounded(url, MAX_FULL_JS)\n",
    "bounded full fetch",
)
error = replace_once(
    error,
    '''        "limits": {
            "prefix_bytes": PREFIX_BYTES,
            "max_prefix_requests": MAX_PREFIX_REQUESTS,
            "max_full_matches": MAX_FULL_MATCHES,
            "max_full_js": MAX_FULL_JS,
        },
''',
    '''        "limits": {
            "prefix_bytes": PREFIX_BYTES,
            "max_root_requests": MAX_ROOT_REQUESTS,
            "max_prefix_requests": MAX_PREFIX_REQUESTS,
            "max_full_matches": MAX_FULL_MATCHES,
            "max_full_js": MAX_FULL_JS,
            "max_probe_seconds": MAX_PROBE_SECONDS,
            "per_request_timeout_seconds": TIMEOUT,
        },
''',
    "diagnostics bounded limits evidence",
)
error = replace_once(
    error,
    '''        "selection": {
            "mode": "two_pass_prefix_score_then_full",
''',
    '''        "selection": {
            "mode": "two_pass_prefix_score_then_full",
            "bounded_by_wall_clock": True,
''',
    "selection bounded marker",
)
error = replace_once(
    error,
    '''        "pages": pages,
''',
    '''        "execution": {
            "wall_clock_budget_seconds": MAX_PROBE_SECONDS,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "budget_exhausted": budget_exhausted,
            "stop_reason": stop_reason,
        },
        "pages": pages,
''',
    "execution budget diagnostics",
)
error = replace_once(
    error,
    '''            "Public GET-only discovery now scores all bounded prefix evidence before using "
            "full-fetch slots, then follows the beta9-proven handleH5MowerSet wrapper through "
            "ES-module aliases to bounded call arguments. It never calls the private mower "
            "command endpoint or the notification detail/read endpoint."
''',
    '''            "Public GET-only discovery prioritizes proven error-command assets and keeps "
            "two-pass prefix/full-fetch recovery inside a strict wall-clock budget. Partial "
            "evidence is returned when the budget is exhausted. It never calls the private "
            "mower command endpoint or the notification detail/read endpoint."
''',
    "bounded discovery note",
)
error_path.write_text(error, encoding="utf-8")


# Home Assistant fail-safe: even an unexpected public-H5 stall must not block the download.
diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    "from __future__ import annotations\n\nfrom copy import deepcopy\n",
    "from __future__ import annotations\n\nimport asyncio\nfrom copy import deepcopy\n",
    "diagnostics asyncio import",
)
diagnostics = replace_once(
    diagnostics,
    "from .resume import resume_command_diagnostics\n\n\n",
    "from .resume import resume_command_diagnostics\n\n\nERROR_DISCOVERY_TIMEOUT_SECONDS = 30.0\n\n\n",
    "diagnostics outer timeout constant",
)
diagnostics = replace_once(
    diagnostics,
    '''    0.4.3-beta11 keeps Maintenance/Mowing Reports discovery paused and focuses
    Download diagnostics on active error command recovery, including two-pass
    public-H5 evidence and any notification-detail response already produced by
    an explicit user Mark notification as read action.
''',
    '''    0.4.3-beta12 keeps Maintenance/Mowing Reports discovery paused and makes
    active-error command recovery diagnostics-safe: public-H5 inspection has a
    strict wall-clock budget plus an outer Home Assistant timeout, while partial
    evidence and explicit notification-detail traces remain downloadable.
''',
    "diagnostics beta12 docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"reason": "0.4.3-beta11 diagnostics focus only on active error action recovery",',
    '"reason": "0.4.3-beta12 diagnostics focus only on bounded active error action recovery",',
    "paused maintenance reason",
)
diagnostics = replace_once(
    diagnostics,
    '''    try:
        error_command_discovery = await hass.async_add_executor_job(
            probe_error_h5,
            coordinator.client,
            str(data.get("error_code") or ""),
            str(data.get("error_title") or data.get("error_text") or ""),
        )
    except Exception as err:  # noqa: BLE001 - optional beta diagnostics discovery
        error_command_discovery = {
            "ok": False,
            "read_only": True,
            "beta_only": True,
            "mutation_calls_executed": False,
            "live_command_call_executed": False,
            "notification_detail_call_executed": False,
            "error_type": type(err).__name__,
            "error": sanitize(str(err)),
        }
''',
    '''    try:
        async with asyncio.timeout(ERROR_DISCOVERY_TIMEOUT_SECONDS):
            error_command_discovery = await hass.async_add_executor_job(
                probe_error_h5,
                coordinator.client,
                str(data.get("error_code") or ""),
                str(data.get("error_title") or data.get("error_text") or ""),
            )
    except TimeoutError:
        error_command_discovery = {
            "ok": False,
            "read_only": True,
            "beta_only": True,
            "timed_out": True,
            "timeout_seconds": ERROR_DISCOVERY_TIMEOUT_SECONDS,
            "mutation_calls_executed": False,
            "live_command_call_executed": False,
            "notification_detail_call_executed": False,
            "error_type": "TimeoutError",
            "error": "public H5 error discovery exceeded the diagnostics timeout",
        }
    except Exception as err:  # noqa: BLE001 - optional beta diagnostics discovery
        error_command_discovery = {
            "ok": False,
            "read_only": True,
            "beta_only": True,
            "mutation_calls_executed": False,
            "live_command_call_executed": False,
            "notification_detail_call_executed": False,
            "error_type": type(err).__name__,
            "error": sanitize(str(err)),
        }
''',
    "diagnostics outer timeout wrapper",
)
diagnostics = diagnostics.replace(
    "0.4.3-beta11 keeps Maintenance/Mowing Reports discovery paused and uses two-pass public H5 selection for Clear and resume / Reboot Mower evidence.",
    "0.4.3-beta12 keeps Maintenance/Mowing Reports discovery paused and bounds Clear and resume / Reboot Mower public-H5 recovery so Download diagnostics always returns partial evidence instead of waiting on the crawler.",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")


# Keep entity attributes Recorder-safe while retaining full internal/diagnostic history.
notification_path = COMPONENT / "notification_feed.py"
notification = notification_path.read_text(encoding="utf-8")
notification = replace_once(
    notification,
    "_NOTIFICATION_ATTR_HISTORY_LIMIT = MERGED_NOTIFICATION_LIMIT\n",
    "# Entity attributes are intentionally smaller than the internal merged history so Recorder stays below its 16 KiB state-attribute limit.\n_NOTIFICATION_ATTR_HISTORY_LIMIT = 5\n",
    "notification attribute history limit",
)
notification_path.write_text(notification, encoding="utf-8")


# Keep beta11 historical tests cumulative now that beta12 is current.
beta11_path = ROOT / "tests" / "test_v043_beta11.py"
beta11 = beta11_path.read_text(encoding="utf-8")
beta11 = replace_once(beta11, "import json\n", "", "beta11 json import")
beta11 = replace_once(
    beta11,
    '''def test_beta11_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta11"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta11.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta11")
''',
    '''def test_beta11_release_artifacts_remain_in_history() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta11.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta11")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.4.3-beta11" in changelog
''',
    "beta11 cumulative release identity",
)
beta11_path.write_text(beta11, encoding="utf-8")


beta12_test = '''"""Regression contracts for Navimower 0.4.3-beta12 bounded diagnostics."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta12_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta12"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta12.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta12")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\\n\\n## 0.4.3-beta12")


def test_beta12_error_discovery_is_wall_clock_bounded() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    for phrase in (
        "MAX_ROOT_REQUESTS = 4",
        "MAX_PREFIX_REQUESTS = 32",
        "MAX_FULL_MATCHES = 6",
        "MAX_PROBE_SECONDS = 24.0",
        "TIMEOUT = 2.5",
        "def _deadline_fetch",
        "wall_clock_budget_exhausted",
        "and not budget_exhausted",
        '"bounded_by_wall_clock": True',
        '"budget_exhausted": budget_exhausted',
        '"elapsed_seconds"',
    ):
        assert phrase in source


def test_beta12_prioritizes_proven_error_command_asset() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert "def _candidate_queue_key" in source
    assert '"source": "observed_error_command_asset"' in source
    assert '"priority": _priority(observed_url) + 5000' in source
    assert "queue = sorted(candidate_map.values(), key=_candidate_queue_key)" in source


def test_beta12_diagnostics_has_outer_timeout_fail_safe() -> None:
    source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "ERROR_DISCOVERY_TIMEOUT_SECONDS = 30.0" in source
    assert "async with asyncio.timeout(ERROR_DISCOVERY_TIMEOUT_SECONDS):" in source
    assert '"timed_out": True' in source
    assert "public H5 error discovery exceeded the diagnostics timeout" in source


def test_beta12_notification_entity_history_is_recorder_safe() -> None:
    source = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source
    assert "Recorder stays below its 16 KiB state-attribute limit" in source
    assert "MERGED_NOTIFICATION_LIMIT = LOCAL_NOTIFICATION_LIMIT + VENDOR_NOTIFICATION_LIMIT" in (COMPONENT / "notification_center.py").read_text(encoding="utf-8")


def test_beta12_discovery_remains_non_mutating() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_command_call_executed": False' in source
    assert '"notification_detail_call_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
'''
(ROOT / "tests" / "test_v043_beta12.py").write_text(beta12_test, encoding="utf-8")


release_notes = '''title: Navimower 0.4.3-beta12

Navimower 0.4.3-beta12 fixes the beta11 Download diagnostics stall while preserving focused read-only recovery of the active error's Clear and resume / Reboot Mower command contracts.

### Fixed

- Bound the public-H5 error-action probe to a 24-second wall-clock budget with a 2.5-second per-request ceiling.
- Reduce generic discovery from up to 180 prefix requests and 18 full JavaScript fetches to at most 32 prefixes and 6 full fetches, plus four root-script requests.
- Add a 30-second Home Assistant diagnostics timeout as a final fail-safe so the download returns even if an unexpected H5/network operation stalls.
- Return partial discovery evidence with explicit elapsed/budget-exhausted diagnostics instead of withholding the entire diagnostics file.
- Prioritize the previously observed `index-594ad42d.js` error-command asset and native/request support chunks before generic lazy assets.
- Limit the `Latest notification` sensor's `recent` attribute to five entries while keeping the full bounded vendor/local history internally and in Download diagnostics, preventing Recorder's 16 KiB attribute warning.

### Preserved

- Error remains private-cloud canonical; MQTT Error transitions are triggers for cloud refresh rather than display values.
- Explicit user notification-detail traces remain available to diagnostics.
- Maintenance and Mowing Reports discovery remains paused while the beta line focuses on active error recovery commands.

### Safety

- Error H5 discovery remains public, unauthenticated and GET-only.
- Download diagnostics sends no Clear and resume, Reboot Mower, Resume, notification-detail/read or other mower command.
'''
(ROOT / ".github" / "release-notes" / "0.4.3-beta12.md").write_text(release_notes, encoding="utf-8")


changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
changelog = replace_once(
    changelog,
    "# Changelog\n\n## 0.4.3-beta10\n",
    '''# Changelog

## 0.4.3-beta12

Bounded active-error diagnostics and Recorder-safe notification attributes.

### Fixed

- Bound Clear and resume / Reboot Mower public-H5 discovery by wall clock, per-request timeout and smaller request budgets so Download diagnostics returns reliably.
- Prioritize proven error-command assets before generic lazy chunks and retain partial evidence when the discovery budget expires.
- Add an outer Home Assistant diagnostics timeout as a final fail-safe.
- Limit the Latest notification entity's recent attribute to five entries to stay below Recorder's 16 KiB state-attribute limit while retaining full internal/diagnostic history.

### Safety

- Error-action discovery remains public HTTPS GET-only and executes no mower or notification-detail command.

## 0.4.3-beta11

Two-pass active-error command discovery and explicit notification-detail trace retention.

### Changed

- Score bounded public-H5 prefix evidence before spending full-fetch slots so strong Clear and resume / Reboot Mower candidates cannot be starved by earlier generic assets.
- Reuse the proven handleH5MowerSet wrapper/export/import tracing to capture bounded command-call argument evidence.
- Preserve the response from an explicit user Mark notification as read action for later diagnostics without making a hidden detail request.

### Safety

- Discovery remained public, unauthenticated and GET-only and did not guess or execute unproven mower commands.

## 0.4.3-beta10
''',
    "prepend beta12 and restore beta11 changelog history",
)
changelog_path.write_text(changelog, encoding="utf-8")
