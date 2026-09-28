"""Release contract for Navimower 0.4.6-beta5."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta5_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta5"


def test_beta5_prepared_history_diagnostics_contract() -> None:
    archive = (COMPONENT / "session_archive.py").read_text(encoding="utf-8")
    svg = (COMPONENT / "session_svg.py").read_text(encoding="utf-8")
    notes = (ROOT / ".github" / "release-notes" / "0.4.6-beta5.md").read_text(
        encoding="utf-8"
    )
    for key in (
        "last_build_source_point_count",
        "last_build_render_point_count",
        "max_session_point_count",
        "retained_point_count_total",
        "last_cache_miss_reason",
        "last_build_stage_ms",
        "prewarm_total_ms",
    ):
        assert key in archive
    assert "def build_session_svg_archive_profiled" in svg
    assert '"swath_raster_ms"' in svg
    assert '"boundary_trace_ms"' in svg
    assert '"svg_path_encode_ms"' in svg
    assert notes.startswith("title: Navimower 0.4.6-beta5")
