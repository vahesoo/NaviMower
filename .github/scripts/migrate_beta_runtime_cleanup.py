from __future__ import annotations

from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "navimower"


def read(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(f"required file missing: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    ast.parse(text)


def migrate_state_semantics() -> None:
    source = read(COMPONENT / "beta16_runtime.py")
    source = replace_once(
        source,
        "from .beta17_runtime import install_beta17_runtime\nfrom .beta18_runtime import install_beta18_runtime\n",
        "",
        "state cross-runtime imports",
    )
    source = replace_once(
        source,
        "    if getattr(cls, \"_beta16_runtime_installed\", False):\n        install_beta17_runtime()\n        install_beta18_runtime()\n        return\n",
        "    if getattr(cls, \"_state_semantics_installed\", False):\n        return\n",
        "state idempotency block",
    )
    source = replace_once(
        source,
        "    cls._beta16_runtime_installed = True\n    _install_error_sensor_attributes()\n    install_beta17_runtime()\n    install_beta18_runtime()",
        "    cls._state_semantics_installed = True\n    _install_error_sensor_attributes()",
        "state installer tail",
    )
    source = source.replace("def install_beta16_runtime() -> None:", "def install_state_semantics() -> None:")
    source = source.replace("Install beta16 state/error semantics once per interpreter.", "Install proven state/error semantics once per interpreter.")
    source = source.replace("Beta16 runtime corrections derived from live H215 state/error captures.", "State and problem semantics derived from live mower captures.")
    source = source.replace("beta15 diagnostic capture", "earlier diagnostic capture")
    source = source.replace("beta15's parser", "the existing parser")
    source = source.replace("beta15/original code", "the existing coordinator code")
    source = source.replace("# noqa: SLF001 - beta shim", "# noqa: SLF001 - internal coordinator state")
    if "install_beta" in source or "_beta16_" in source or "beta17_runtime" in source or "beta18_runtime" in source:
        raise RuntimeError("state semantics still contains beta runtime wiring")
    write(COMPONENT / "state_semantics.py", source)


def migrate_capabilities() -> None:
    source = read(COMPONENT / "beta17_runtime.py")
    source = source.replace("Beta17 capability extensions from the first captured Navimow i2 AWD.", "Capability and route-history extensions for modern Navimow models.")
    source = source.replace("Beta17 deliberately exposes", "This module deliberately exposes")
    source = source.replace("def install_beta17_runtime() -> None:", "def install_capability_extensions() -> None:")
    source = source.replace("Install beta17 i2 capability and route-history corrections once.", "Install model capability and route-history extensions once.")
    source = source.replace("_beta17_runtime_installed", "_capability_extensions_installed")
    source = source.replace("_beta17_compacted_session_ids", "_compacted_session_ids")
    if "install_beta" in source or re.search(r"_beta\d+_", source):
        raise RuntimeError("capability extensions still contain beta runtime symbols")
    write(COMPONENT / "capability_extensions.py", source)


def migrate_navigation() -> None:
    source = read(COMPONENT / "beta18_runtime.py")
    source = source.replace("Beta18 navigation fallback corrections.", "Freshness-aware navigation fallback and gate safety.")
    source = source.replace("def install_beta18_runtime() -> None:", "def install_navigation_fallback() -> None:")
    source = source.replace("Install freshness-aware navigation fallback once per interpreter.", "Install freshness-aware navigation fallback once per interpreter.")
    source = source.replace("_beta18_runtime_installed", "_navigation_fallback_installed")
    source = source.replace("_beta18_cloud_gate_confirmations", "_cloud_gate_confirmations")
    source = source.replace("_beta18_gate_area_states", "_cloud_gate_area_states")
    if "install_beta" in source or re.search(r"_beta\d+_", source):
        raise RuntimeError("navigation fallback still contains beta runtime symbols")
    write(COMPONENT / "navigation_fallback.py", source)


def migrate_notification_feed() -> None:
    source = read(COMPONENT / "beta26_runtime.py")
    source = source.replace("0.4.2-beta4 retains at most", "The integration retains at most")
    source = source.replace("Historical beta26 route", "Legacy notification-history route")
    source = source.replace("Canonical beta30 naming.", "Canonical notification-code naming.")
    source = source.replace("Backward-compatible alias retained for automations built on beta29.", "Backward-compatible alias retained for existing automations.")
    source = source.replace("def install_beta26_runtime() -> None:", "def install_notification_feed() -> None:")
    source = source.replace("_beta26_runtime_installed", "_notification_feed_installed")
    source = source.replace("_beta26_notification_", "_notification_")
    if "install_beta" in source or re.search(r"_beta\d+_", source):
        raise RuntimeError("notification feed still contains beta runtime symbols")
    write(COMPONENT / "notification_feed.py", source)


def create_runtime_composition() -> None:
    write(
        COMPONENT / "runtime.py",
        '''"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .navigation_fallback import install_navigation_fallback
from .notification_feed import install_notification_feed
from .state_semantics import install_state_semantics


def install_runtime_extensions() -> None:
    """Install semantic extensions in the historically proven order."""
    install_state_semantics()
    install_capability_extensions()
    install_navigation_fallback()
    install_notification_feed()
''',
    )


def update_production_references() -> None:
    services_path = COMPONENT / "services.py"
    services = read(services_path)
    services = replace_once(
        services,
        "from .beta16_runtime import install_beta16_runtime\nfrom .beta26_runtime import install_beta26_runtime\n",
        "from .runtime import install_runtime_extensions\n",
        "service runtime imports",
    )
    services = replace_once(
        services,
        "install_beta16_runtime()\ninstall_beta26_runtime()\n",
        "install_runtime_extensions()\n",
        "service runtime calls",
    )
    write(services_path, services)

    actions_path = COMPONENT / "notification_actions.py"
    actions = read(actions_path).replace(
        "coordinator._beta26_notification_last_attempt_mono = None",
        "coordinator._notification_last_attempt_mono = None",
    )
    write(actions_path, actions)

    center_path = COMPONENT / "notification_center.py"
    center = read(center_path).replace(
        "from .beta26_runtime import refresh_notification_snapshot",
        "from .notification_feed import refresh_notification_snapshot",
    )
    write(center_path, center)


def create_architecture_guard() -> None:
    test = '''"""Architecture guard for production runtime naming and composition."""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SEMANTIC_RUNTIME_FILES = {
    "runtime.py",
    "state_semantics.py",
    "capability_extensions.py",
    "navigation_fallback.py",
    "notification_feed.py",
}


def test_production_runtime_has_no_release_number_layers() -> None:
    python_files = list(COMPONENT.rglob("*.py"))
    forbidden_files = [
        path.name
        for path in python_files
        if re.fullmatch(r"(?:beta|v)\\d+.*runtime\\.py", path.name, re.IGNORECASE)
    ]
    assert forbidden_files == []

    offenders: list[str] = []
    for path in python_files:
        text = path.read_text(encoding="utf-8")
        if "install_beta" in text or re.search(r"_beta\\d+_", text):
            offenders.append(str(path.relative_to(ROOT)))
        if re.search(r"from \\.beta\\d+", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_semantic_runtime_has_one_composition_point() -> None:
    present = {path.name for path in COMPONENT.glob("*.py")}
    assert SEMANTIC_RUNTIME_FILES <= present

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    expected = [
        "install_state_semantics()",
        "install_capability_extensions()",
        "install_navigation_fallback()",
        "install_notification_feed()",
    ]
    positions = [runtime.index(call) for call in expected]
    assert positions == sorted(positions)

    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    assert "from .runtime import install_runtime_extensions" in services
    assert services.count("install_runtime_extensions()") == 1
'''
    write(ROOT / "tests" / "test_runtime_architecture.py", test)


def create_beta6_contract() -> None:
    test = '''"""Regression contract for Navimower 0.4.2-beta6 runtime cleanup."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta6_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.2-beta6"
    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta6.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.2-beta6")
    assert "semantic runtime" in notes.lower()
    assert "beta-numbered runtime" in notes.lower()


def test_beta6_semantic_runtime_modules_parse_and_old_layers_are_gone() -> None:
    for name in (
        "runtime.py",
        "state_semantics.py",
        "capability_extensions.py",
        "navigation_fallback.py",
        "notification_feed.py",
    ):
        ast.parse((COMPONENT / name).read_text(encoding="utf-8"))

    for old in ("beta16_runtime.py", "beta17_runtime.py", "beta18_runtime.py", "beta26_runtime.py"):
        assert not (COMPONENT / old).exists()


def test_beta6_preserves_proven_runtime_contracts_under_semantic_names() -> None:
    state = (COMPONENT / "state_semantics.py").read_text(encoding="utf-8")
    assert '_STATE_IDLE = "0103"' in state
    assert '_STATE_FAULT = "0301"' in state
    assert 'MQTT_DOCKED_STATES.discard(_MQTT_STOPPED)' in state
    assert 'request_fast_refresh("MQTT state changed to Error")' in state

    capabilities = (COMPONENT / "capability_extensions.py").read_text(encoding="utf-8")
    assert '"i208 AWD"' in capabilities
    assert "append_or_coalesce" in capabilities
    assert "compact_route_points" in capabilities

    navigation = (COMPONENT / "navigation_fallback.py").read_text(encoding="utf-8")
    assert "choose_position(" in navigation
    assert "outside_count >= 2" in navigation
    assert 'position_source == "private_cloud"' in navigation

    feed = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    assert "vehicleMessageListField" in feed
    assert "VENDOR_NOTIFICATION_LIMIT" in feed
    assert "merge_notification_lists" in feed
    assert "refresh_notification_snapshot" in feed


def test_beta6_notification_read_invalidation_uses_semantic_cache_state() -> None:
    actions = (COMPONENT / "notification_actions.py").read_text(encoding="utf-8")
    assert "_notification_last_attempt_mono = None" in actions
    assert "_beta26_notification" not in actions
'''
    write(ROOT / "tests" / "test_v042_beta6.py", test)


def create_architecture_doc() -> None:
    path = ROOT / "docs" / "ARCHITECTURE.md"
    path.write_text(
        '''# Navimower production architecture

## Runtime naming rule

Release and beta numbers belong in `manifest.json`, changelog entries, release notes, Git tags and historical tests. They do **not** belong in production Python module names or runtime installer symbols.

Production behavior must be grouped by responsibility. The current semantic extension boundary is:

- `state_semantics.py` — proven state/error interpretation and error-sensor enrichment;
- `capability_extensions.py` — model capabilities, dynamic limits and route-history compaction;
- `navigation_fallback.py` — freshness-aware MQTT/private-cloud navigation fallback and gate safety;
- `notification_feed.py` — vendor notification transport/cache plus merged notification snapshot decoration;
- `runtime.py` — the single ordered composition point for those extensions.

`services.py` may call only the central `install_runtime_extensions()` composition point. Semantic modules must not chain-install one another.

## Rule for future betas

Do not add `betaNN_runtime.py`, `vNN_runtime.py`, `install_betaNN_*`, or `_betaNN_*` production symbols as a cache/workaround or release mechanism. A beta is cumulative: changes go directly into the current semantic production modules and the latest beta is already the stable-release candidate.

If a future protocol experiment genuinely needs temporary isolation, give it a responsibility-based name such as `experimental_<feature>.py`. The same change must document why isolation is needed and explicitly update the architecture guard in `tests/test_runtime_architecture.py`. Removing the guard or adding a blanket beta-number exception is not an acceptable workaround.

Historical beta behavior remains recoverable from Git tags and versioned tests/release notes; production source does not need to carry old release layers.
''',
        encoding="utf-8",
    )


def create_release_docs() -> None:
    notes = ROOT / ".github" / "release-notes" / "0.4.2-beta6.md"
    if notes.exists():
        raise RuntimeError("0.4.2-beta6 release notes already exist")
    notes.write_text(
        '''title: Navimower 0.4.2-beta6

Navimower 0.4.2-beta6 is a behavior-preserving production-source cleanup. It removes release-numbered runtime layers from `custom_components/navimower` and replaces them with responsibility-based semantic runtime modules plus one explicit composition point.

### Semantic runtime structure

The old `beta16_runtime.py`, `beta17_runtime.py`, `beta18_runtime.py` and `beta26_runtime.py` files are removed from production source. Their already-proven behavior remains cumulative under:

- `state_semantics.py` for state/error interpretation;
- `capability_extensions.py` for modern model capabilities and route-history compaction;
- `navigation_fallback.py` for freshness-aware position fallback and gate safety;
- `notification_feed.py` for vendor/local notification transport and snapshot decoration;
- `runtime.py` as the only ordered runtime composition point.

The install order is intentionally unchanged from the previous runtime chain, so this beta is an architectural cleanup rather than a mower-protocol behavior change. Internal version-stamped cache/latch attribute names were also replaced with responsibility-based names.

### Guard against repeating the problem

A permanent architecture regression test now rejects beta/version-numbered runtime Python files, `install_beta...` production hooks and `_betaNN_...` production state. `docs/ARCHITECTURE.md` records the same rule and the explicit process for a rare, justified experimental-module exception.

Future betas continue to be cumulative. New behavior goes directly into semantic production modules; Git tags and release notes retain history. Reaching stable therefore requires a version/release pass, not a second source-code consolidation.

### Compatibility

This cleanup preserves the proven H215 state/error corrections, i2 AWD capability extensions, route-point compaction, private-cloud navigation fallback/gate safety, vendor notification feed, local notification merge/read handling, Resume support and the beta5 schedule uint16 zone-id fix.

0.4.2-beta6 is cumulative from stable 0.4.1 through beta5; earlier 0.4.2 betas do not need to be installed first.
''',
        encoding="utf-8",
    )

    changelog_path = ROOT / "CHANGELOG.md"
    changelog = read(changelog_path)
    if "## 0.4.2-beta6\n" in changelog:
        raise RuntimeError("beta6 changelog already exists")
    section = '''## 0.4.2-beta6

Sixth beta in the cumulative 0.4.2 development line.

### Changed

- Replaced production `beta16_runtime.py`, `beta17_runtime.py`, `beta18_runtime.py` and `beta26_runtime.py` layers with responsibility-based semantic modules.
- Added one explicit `runtime.py` composition point while preserving the historically proven install order and runtime behavior.
- Renamed version-stamped internal notification/navigation/history runtime state to responsibility-based names.

### Guardrails

- Added a permanent architecture test that rejects beta/version-numbered production runtime files and beta-numbered runtime installer/state symbols.
- Added `docs/ARCHITECTURE.md` documenting the cumulative-beta rule and the explicit exception process for genuinely isolated experiments.

'''
    changelog = replace_once(changelog, "# Changelog\n\n", "# Changelog\n\n" + section, "changelog header")
    changelog_path.write_text(changelog, encoding="utf-8")


def remove_old_runtime_files() -> None:
    for name in ("beta16_runtime.py", "beta17_runtime.py", "beta18_runtime.py", "beta26_runtime.py"):
        (COMPONENT / name).unlink()


def validate_component() -> None:
    forbidden_names = []
    forbidden_symbols = []
    for path in COMPONENT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        if re.fullmatch(r"(?:beta|v)\d+.*runtime\.py", path.name, re.IGNORECASE):
            forbidden_names.append(path.name)
        if "install_beta" in text or re.search(r"_beta\d+_", text):
            forbidden_symbols.append(str(path.relative_to(ROOT)))
        if re.search(r"from \.beta\d+", text):
            forbidden_symbols.append(str(path.relative_to(ROOT)))
    if forbidden_names or forbidden_symbols:
        raise RuntimeError(
            f"architecture guard failed: files={forbidden_names}, symbols={forbidden_symbols}"
        )

    runtime = read(COMPONENT / "runtime.py")
    calls = [
        "install_state_semantics()",
        "install_capability_extensions()",
        "install_navigation_fallback()",
        "install_notification_feed()",
    ]
    positions = [runtime.index(call) for call in calls]
    if positions != sorted(positions):
        raise RuntimeError("semantic runtime install order changed")


def main() -> None:
    migrate_state_semantics()
    migrate_capabilities()
    migrate_navigation()
    migrate_notification_feed()
    create_runtime_composition()
    update_production_references()
    create_architecture_guard()
    create_beta6_contract()
    create_architecture_doc()
    create_release_docs()
    remove_old_runtime_files()
    validate_component()


if __name__ == "__main__":
    main()
