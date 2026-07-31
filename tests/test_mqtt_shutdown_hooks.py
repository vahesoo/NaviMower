"""Static checks that bridge hooks are detached before SDK disconnect."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "custom_components" / "navimower" / "mqtt.py").read_text(encoding="utf-8")
assert "self._hook_state" in source
assert "def _detach_hooks" in source
quiesce = source.split("async def async_quiesce", 1)[1].split("async def async_resume", 1)[0]
disconnect = source.split("async def _async_disconnect_sdk", 1)[1].split("@staticmethod", 1)[0]
assert "self._detach_hooks(self.sdk)" in quiesce
assert "self._detach_hooks(sdk)" in disconnect
assert 'getattr(mqtt, name, None) is hook' in source
print("mqtt shutdown hook tests passed")
