from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "navimower"

def test_beta37_identity_and_release_notes():
    manifest = json.loads((COMP / "manifest.json").read_text())
    assert manifest["version"] == "0.4.3-beta37"
    notes = (ROOT / ".github/release-notes/0.4.3-beta37.md").read_text()
    assert notes.startswith("title: Navimower 0.4.3-beta37")

def test_order_mode_is_on_main_schedule_form_without_raw_queue_field():
    source = (COMP / "config_flow.py").read_text()
    init = source[source.index("async def async_step_init"):source.index("def _schedule_order_selector")]
    assert '"navimower_schedule_order"' not in init
    assert "async_step_navimower_schedule" in source
    assert "OPT_SCHEDULE_ORDER_MODE" in source
    assert "vol.Optional(OPT_SCHEDULE_CUSTOM_QUEUE" not in source
    assert "_seed_custom_queue" in source

def test_schedule_order_translation_contract():
    for rel in ("strings.json", "translations/en.json"):
        doc = json.loads((COMP / rel).read_text())
        step = doc["options"]["step"]["navimower_schedule"]
        assert step["data"]["navimower_schedule_order_mode"] == "Zone order"
