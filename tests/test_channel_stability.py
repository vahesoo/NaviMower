"""Source-level regression checks for stable Current channel semantics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"
SENSOR = ROOT / "custom_components" / "navimower" / "sensor.py"
source = COORDINATOR.read_text(encoding="utf-8")
sensor = SENSOR.read_text(encoding="utf-8")

assert '"Not in channel" if pose_valid else "Position unavailable"' not in source
assert 'tunnel_source = "last_known_stale_pose"' in source
assert 'tunnel_source = "confirmed_docked"' in source
assert '"current_channel_stale": tunnel_stale' in source
assert '"current_channel_pose_valid": pose_valid' in source
# Physical Gate-area membership still calls only the fresh MQTT pose helper.
channel_state = source.split("def channel_state", 1)[1].split("def ", 1)[0]
assert "position = self._fresh_mqtt_position()" in channel_state
assert "return None" in channel_state
assert '"stale": d.get("current_channel_stale")' in sensor
print("channel stability tests passed")
