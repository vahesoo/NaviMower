"""Release contract for vendor-current-state alignment beta3."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta3"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta3.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Vendor-current-state alignment",
        "100% or 69% to 0%",
        "Current trail",
        "cutting",
        "0.4.0-beta3",
        "shadow-only",
    ):
        assert marker in notes


def test_beta3_current_map_uses_vendor_state_but_canonical_stays_shadow() -> None:
    vendor = (COMPONENT / "vendor_progress_semantics.py").read_text(encoding="utf-8")
    ledger = (COMPONENT / "zone_ledger.py").read_text(encoding="utf-8")
    render = (COMPONENT / "vendor_trail_render_semantics.py").read_text(encoding="utf-8")
    core = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")

    assert '"vendor_current_state"' in vendor
    assert '"vendor_coverage_monotonic_hold"' not in vendor
    assert '"vendor_current_coverage"' in ledger
    assert "vendor_zero_suppressed_zone_ids" in render
    assert "_point_is_cutting" in render
    assert 'CANONICAL_MODE = "shadow_beta3"' in core
    assert '"public_owner": "legacy_runtime"' in core
