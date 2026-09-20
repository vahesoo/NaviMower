"""Startup regressions for beta17 map-artifact prewarming."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    finish = source.index(end, begin)
    return source[begin:finish]


def test_artifact_prewarm_is_not_started_during_persistent_restore_or_first_refresh():
    source = (ROOT / "custom_components/navimower/coordinator_semantics.py").read_text()
    load = _block(
        source,
        "    async def async_load_persistent_state(self) -> None:",
        "    def start_map_artifact_prewarm(self) -> None:",
    )
    accept = _block(
        source,
        "    def _accept_vendor_observations(self, snapshot: dict[str, Any]) -> None:",
        "    def _build_zone_details(",
    )
    assert "map_artifacts.request_refresh" not in load
    assert "artifacts and self._map_artifact_prewarm_enabled" in accept


def test_artifact_prewarm_starts_only_after_all_awaited_entry_setup_steps():
    source = (ROOT / "custom_components/navimower/__init__.py").read_text()
    setup = _block(
        source,
        "async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:",
        "async def async_unload_entry(",
    )
    forward = setup.index("await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)")
    prewarm = setup.index("coordinator.start_map_artifact_prewarm()")
    returned = setup.index("return True", prewarm)
    assert forward < prewarm < returned


def test_deferred_prewarm_uses_next_event_loop_turn():
    source = (ROOT / "custom_components/navimower/coordinator_semantics.py").read_text()
    start = _block(
        source,
        "    def start_map_artifact_prewarm(self) -> None:",
        "    async def async_shutdown(self) -> None:",
    )
    assert "self.hass.loop.call_soon(self.map_artifacts.request_refresh)" in start
