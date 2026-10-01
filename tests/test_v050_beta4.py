"""Release contract for forward-only vendor trail gap guard beta4."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_version_and_release_scope() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta4"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta4.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Forward-only vendor trail gap guard",
        "newly appended",
        "5 m",
        "not rescanned",
        "0.4.0-beta4",
        "shadow-only",
    ):
        assert marker in notes


def test_beta4_gap_guard_is_forward_only() -> None:
    store = (COMPONENT / "vendor_trail_store.py").read_text(encoding="utf-8")
    vendor = (COMPONENT / "vendor_trail.py").read_text(encoding="utf-8")
    assert "FUTURE_VENDOR_GAP_SPLIT_M = 5.0" in store
    assert "gap_guard_scanned_point_count" in store
    assert "future_gap_break_indices" in store
    assert "scan_from = len(previous_points)" in store
    assert "future_gap_break_indices" in vendor
    assert 'CANONICAL_MODE = "shadow_beta3"' in (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
