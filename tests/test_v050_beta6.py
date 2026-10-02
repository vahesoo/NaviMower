"""Release contract for the 0.5.0-beta6 canonical cleanup."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta6_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta6"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta6.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Canonical cleanup",
        "ZoneLedger",
        "contract v3",
        "0.4.0-beta6",
    ):
        assert marker in notes


def test_beta6_has_one_zone_and_canonical_authority_path() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    ledger = (COMPONENT / "zone_ledger_semantics.py").read_text(encoding="utf-8")
    canonical = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")

    assert "run_zone_ledger_authority(self, snapshot)" in coordinator
    assert "run_canonical_authority(self, snapshot)" in coordinator
    assert "build_zone_model(" not in coordinator
    assert "install_zone_ledger_semantics" not in runtime
    assert "install_canonical_authority_semantics" not in runtime
    assert "_zone_ledger_cutover" not in ledger
    assert "legacy_totals_bridge" not in canonical
    assert "legacy_resolved_fallback" not in canonical
    assert '"parity"' not in canonical


def test_beta6_map_contract_removes_current_state_aliases() -> None:
    api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")

    assert "MAP_RESOURCE_CONTRACT_VERSION: Final = 3" in const
    assert '"compatibility_aliases": False' in api
    assert 'for key in ("zone_states", "zone_states_revision", "totals")' in api
    assert '"canonical": _canonical_frontend_state(coordinator)' in api
