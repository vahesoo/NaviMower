"""Regression checks for beta42 gate-transition diagnostics."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS = ROOT / "custom_components" / "navimower" / "diagnostics.py"
source = DIAGNOSTICS.read_text(encoding="utf-8")

for marker in (
    "def _mqtt_navigation_diagnostics",
    '"cached_location": cached',
    '"work_sub_action"',
    '"work_target_zone"',
    '"mow_boundary"',
    '"partition_ids"',
    '"gate_states"',
    '"gate_arrival_guards"',
    '"mqtt_navigation": sanitize(_mqtt_navigation_diagnostics(coordinator, data))',
    'mqtt_bridge.diagnostic_inventory()',
    'mqtt_bridge.diagnostic_discovery()',
):
    assert marker in source, marker

print("beta42 MQTT navigation diagnostic tests passed")
