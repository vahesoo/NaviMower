"""Release contract for Navimower 0.4.6-beta3."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta3"


def test_beta3_scope_is_history_hotpath_hardening() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    prepared = (COMPONENT / "prepared_render_model.py").read_text(encoding="utf-8")
    assert 'snapshot["trail_point_count"] = self.history.active_point_count()' in coordinator
    assert "def active_point_count" in history
    assert "card_materialization_diagnostics" in history
    assert "await self.hass.async_add_executor_job(self._live_source)" in prepared
