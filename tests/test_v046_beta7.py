"""Release contract for Navimower 0.4.6-beta7."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta7_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta7"


def test_beta7_scope_contract() -> None:
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    prepared = (COMPONENT / "prepared_render_model.py").read_text(encoding="utf-8")
    store = (COMPONENT / "vendor_trail_store.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator_semantics.py").read_text(encoding="utf-8")
    notes = (ROOT / ".github" / "release-notes" / "0.4.6-beta7.md").read_text(
        encoding="utf-8"
    )

    assert "def active_session_render_snapshot" in history
    assert '"render_snapshots"' in history
    live_start = prepared.index("    def _live_source")
    live_end = prepared.index("    async def _build_live", live_start)
    live = prepared[live_start:live_end]
    assert "active_session_render_snapshot" in live
    assert 'getattr(history, "active_session", None)' not in live
    assert '"active_session": deepcopy(active_session)' not in live

    assert "def vendor_fetch_zone_ids" in store
    assert "last_vendor_fetch_blocked_zone_count" in store
    assert "self.vendor_trail_store.vendor_fetch_zone_ids(" in coordinator
    poll_start = coordinator.index("    def _refresh_vendor_trail_debug")
    poll_end = coordinator.index("    def _fetch_blocking", poll_start)
    poll = coordinator[poll_start:poll_end]
    assert "partitionList" in poll
    assert "zone_ids = self.vendor_trail_store.vendor_fetch_zone_ids(" in poll
    assert notes.startswith("title: Navimower 0.4.6-beta7")
