"""Release contract for Navimower 0.4.6-beta6."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta6_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta6"


def test_beta6_fresh_install_recovery_contract() -> None:
    store = (COMPONENT / "vendor_trail_store.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator_semantics.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    notes = (ROOT / ".github" / "release-notes" / "0.4.6-beta6.md").read_text(
        encoding="utf-8"
    )

    for symbol in (
        "prepare_observation",
        "begin_vendor_observation_batch",
        "finish_vendor_observation_batch",
        "recovery_diagnostics",
        "preinstall_skipped_point_count",
        "recovery_checkpoint_at_ms",
    ):
        assert symbol in store
    assert "store.prepare_observation(" in coordinator
    assert '"vendor_trail_recovery"' in diagnostics
    assert notes.startswith("title: Navimower 0.4.6-beta6")
