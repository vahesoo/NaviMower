"""Release contract for Canonical authority cutover beta5."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta5_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.5.0-beta")
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta5.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Canonical authority cutover",
        "canonical_mower_state",
        "ZoneLedger",
        "contract.version = 2",
        "0.4.0-beta5",
        "cleanup beta",
    ):
        assert marker in notes


def test_beta5_runtime_uses_authority_not_shadow_hook() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    core = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

    assert "install_canonical_authority_semantics" in runtime
    assert "install_zone_ledger_semantics" in runtime
    assert "install_canonical_shadow_semantics" not in runtime
    assert "install_zone_ledger_shadow_semantics" not in runtime
    assert 'CANONICAL_MODE = "authoritative_v2"' in core
    assert '"public_owner": "canonical_mower_state"' in core
    assert '_canonical_diagnostics' in diagnostics
    assert '_canonical_shadow_diagnostics' not in diagnostics


def test_beta5_map_contract_v2_is_backend_owned() -> None:
    source = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    sensors = (COMPONENT / "sensor.py").read_text(encoding="utf-8")

    assert "MAP_RESOURCE_CONTRACT_VERSION: Final = 2" in const
    assert '"authority": "canonical_mower_state"' in source
    assert '"cycle_owner": "ZoneLedger"' in source
    assert '"canonical": _canonical_frontend_state(coordinator)' in source
    assert 'value_fn=lambda d: (d.get("totals") or {}).get("task_progress_pct")' in sensors
    assert 'value_fn=lambda d: (d.get("totals") or {}).get("map_coverage_pct")' in sensors
