"""Release regressions for Navimower 0.5.0-beta19."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta19_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta19"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta19.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Issue #437",
        "startup/departure position trust gate",
        "within 2 m of the station",
        "used only as validation evidence",
        "health.position_trust",
    ):
        assert marker in notes


def test_position_trust_gate_is_canonical_pre_resolver_input() -> None:
    authority = (COMPONENT / "canonical_authority_semantics.py").read_text(
        encoding="utf-8"
    )
    canonical = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    trust = (COMPONENT / "position_trust.py").read_text(encoding="utf-8")

    assert "prepare_position_candidates(" in authority
    assert "record_position_result(" in authority
    assert "position_trust_diagnostics(" in authority
    assert 'health["position_trust"] = trust_diagnostics' in authority
    assert "position_override: dict[str, Any] | None = None" in canonical
    assert "DOCK_STATION_TOLERANCE_M = 2.0" in trust
    assert '_unavailable_override("startup_waiting")' in trust
    assert '"source": "trusted_previous"' in trust
    ast.parse(authority)
    ast.parse(canonical)
    ast.parse(trust)


def test_station_is_validator_not_public_position_source() -> None:
    canonical = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    trust = (COMPONENT / "position_trust.py").read_text(encoding="utf-8")
    assert '"map_station"' not in canonical
    assert '"docked_station_authority"' not in canonical
    assert "map_station_position_override_active" not in canonical
    assert "_station_position(" in trust
    assert "station_distance" in trust
    assert '"source": "map_station"' not in trust


def test_position_trust_diagnostics_do_not_export_coordinates() -> None:
    source = (COMPONENT / "position_trust.py").read_text(encoding="utf-8")
    diag = source.split("def position_trust_diagnostics", 1)[1]
    assert '"coordinates_included": False' in diag
    assert '"station_distance_m"' in diag
    assert '"last_position"' not in diag
