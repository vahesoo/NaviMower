"""Release contract for Navimower 0.4.6-beta8."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta8_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta8"


def test_beta8_direct_prepared_live_contract() -> None:
    prepared = (COMPONENT / "prepared_render_model.py").read_text(encoding="utf-8")
    notes = (ROOT / ".github" / "release-notes" / "0.4.6-beta8.md").read_text(
        encoding="utf-8"
    )

    start = prepared.index("    def _live_source")
    end = prepared.index("    async def _build_live", start)
    live_source = prepared[start:end]
    assert "active_session_render_snapshot" in live_source
    assert "_map_payload_with_sessions" not in live_source
    assert '"trail_segments"' not in live_source

    build_start = prepared.index("def build_live_route_render_model")
    build_end = prepared.index("\n\nclass PreparedRenderModelManager", build_start)
    build = prepared[build_start:build_end]
    assert "split_session_route_segments(" in build
    assert notes.startswith("title: Navimower 0.4.6-beta8")
