"""Dependency-free checks for MQTT battery/progress parsing freshness flags."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCATION = ROOT / "custom_components" / "navimower" / "location.py"
MQTT = ROOT / "custom_components" / "navimower" / "mqtt.py"

spec = importlib.util.spec_from_file_location("navimower_location", LOCATION)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

extract = module.extract_mqtt_battery
parse = module.parse_location_payload
assert extract({"battery": 76}) == 76
assert extract({"battery": 101}) is None
assert extract({"capacityRemaining": [{"unit": "PERCENTAGE", "rawValue": "64"}]}) == 64

cache: dict[str, dict] = {}
first = parse(
    cache,
    "mower",
    [{"type": 2, "currentMowProgress": 8400, "subtotalArea": 903.39}],
)
assert first is not None
assert first["_progress_updated"] is True
assert first["_area_updated"] is True
assert first["mow_progress"] == 8400

# A later pose carries cached progress but must not refresh its source age.
second = parse(
    cache,
    "mower",
    [{"type": 1, "postureX": 1, "postureY": 2, "postureTheta": 0, "time": 3}],
)
assert second is not None
assert second["mow_progress"] == 8400
assert second["_pose_updated"] is True
assert second["_progress_updated"] is False
assert second["_area_updated"] is False

mqtt_source = MQTT.read_text(encoding="utf-8")
assert '"/realtimeDate/state"' in mqtt_source
assert "ingest_mqtt_state" in mqtt_source
print("mqtt telemetry tests passed")
