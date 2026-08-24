from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path.cwd()
COMP = ROOT / "custom_components" / "navimower"

# Identity
manifest_path = COMP / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["version"] = "0.4.3-beta37"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Integrate order mode into the main Schedule screen and remove the raw queue editor.
flow_path = COMP / "config_flow.py"
flow = flow_path.read_text(encoding="utf-8")
flow = flow.replace('                "navimower_schedule_order",\n', '')
flow = flow.replace(
    '        self._custom_area_candidate: list[list[float]] | None = None\n',
    '        self._custom_area_candidate: list[list[float]] | None = None\n'
    '        self._pending_schedule_order_mode: str | None = None\n',
)
pattern = re.compile(
    r'\n\n    async def async_step_navimower_schedule_order\(.*?\n    async def async_step_custom_areas',
    re.S,
)
replacement = r'''

    def _schedule_order_selector(self, mode: str):
        return SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": SCHEDULE_ORDER_AUTOMATIC, "label": "Automatic order"},
                    {"value": SCHEDULE_ORDER_CUSTOM, "label": "Custom order"},
                ],
                mode=SelectSelectorMode.LIST,
            )
        )

    def _seed_custom_queue(self, zone_ids: Any) -> list[str]:
        selected = {str(value) for value in (zone_ids or [])}
        coordinator = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        rows = []
        for row in data.get("zone_states") or []:
            if not isinstance(row, dict):
                continue
            zone_id = str(row.get("id") or "")
            completed = row.get("last_completed_at")
            if zone_id in selected and completed:
                rows.append((str(completed), zone_id))
        rows.sort(key=lambda item: (item[0], item[1]))
        return [zone_id for _, zone_id in rows]

    def _apply_schedule_order(self, result: ConfigFlowResult, mode: str) -> ConfigFlowResult:
        if result.get("type") == "form" and result.get("step_id") == "navimower_schedule":
            schema = dict(result["data_schema"].schema)
            schema[vol.Required(OPT_SCHEDULE_ORDER_MODE, default=mode)] = self._schedule_order_selector(mode)
            result["data_schema"] = vol.Schema(schema)
            return result
        if result.get("type") == "create_entry":
            data = dict(result.get("data") or {})
            data[OPT_SCHEDULE_ORDER_MODE] = mode
            if mode == SCHEDULE_ORDER_CUSTOM:
                selected = data.get("navimower_schedule_zone_ids", self._options().get("navimower_schedule_zone_ids", []))
                existing = list(self._options().get(OPT_SCHEDULE_CUSTOM_QUEUE, []) or [])
                selected_set = {str(value) for value in (selected or [])}
                queue = [str(value) for value in existing if str(value) in selected_set]
                if not queue:
                    queue = self._seed_custom_queue(selected)
                data[OPT_SCHEDULE_CUSTOM_QUEUE] = queue
            result["data"] = data
        return result

    async def async_step_navimower_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self._options()
        mode = str(options.get(OPT_SCHEDULE_ORDER_MODE, SCHEDULE_ORDER_AUTOMATIC))
        payload = None if user_input is None else dict(user_input)
        if payload is not None:
            mode = str(payload.pop(OPT_SCHEDULE_ORDER_MODE, mode) or SCHEDULE_ORDER_AUTOMATIC)
            if mode not in {SCHEDULE_ORDER_AUTOMATIC, SCHEDULE_ORDER_CUSTOM}:
                mode = SCHEDULE_ORDER_AUTOMATIC
            self._pending_schedule_order_mode = mode
        result = await super().async_step_navimower_schedule(payload)
        return self._apply_schedule_order(result, mode)

    async def async_step_navimower_schedule_window(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        result = await super().async_step_navimower_schedule_window(user_input)
        mode = self._pending_schedule_order_mode or str(
            self._options().get(OPT_SCHEDULE_ORDER_MODE, SCHEDULE_ORDER_AUTOMATIC)
        )
        return self._apply_schedule_order(result, mode)

    async def async_step_custom_areas'''
flow, count = pattern.subn(replacement, flow, count=1)
if count != 1:
    raise SystemExit(f"Could not replace beta36 schedule-order step: {count}")
flow_path.write_text(flow, encoding="utf-8")

# Home Assistant translations: field labels must live in both files.
for rel in ("strings.json", "translations/en.json"):
    path = COMP / rel
    doc = json.loads(path.read_text(encoding="utf-8"))
    step = doc["options"]["step"]["navimower_schedule"]
    step["data"]["navimower_schedule_order_mode"] = "Zone order"
    step.setdefault("data_description", {})["navimower_schedule_order_mode"] = (
        "Automatic order selects the least recently completed selected zone first. "
        "Custom order keeps a persistent queue that can later be rearranged in Navimower Map Card."
    )
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Historical version guards.
for path in (ROOT / "tests").glob("test_v043_beta*.py"):
    text = path.read_text(encoding="utf-8")
    if "0.4.3-beta36" in text and "0.4.3-beta37" not in text:
        if path.name == "test_v043_beta36.py":
            text = text.replace("assert '0.4.3-beta36' in Path('custom_components/navimower/manifest.json').read_text()", "assert any(v in Path('custom_components/navimower/manifest.json').read_text() for v in ('0.4.3-beta36', '0.4.3-beta37'))")
            text = text.replace("assert 'async_step_navimower_schedule_order' in s", "assert 'async_step_navimower_schedule' in s")
        else:
            text = text.replace('"0.4.3-beta36"}', '"0.4.3-beta36", "0.4.3-beta37"}')
            text = text.replace('"0.4.3-beta36",\n}', '"0.4.3-beta36", "0.4.3-beta37",\n}')
            text = text.replace("or '\"version\": \"0.4.3-beta36\"' in manifest)", "or '\"version\": \"0.4.3-beta36\"' in manifest or '\"version\": \"0.4.3-beta37\"' in manifest)")
        path.write_text(text, encoding="utf-8")

(ROOT / "tests" / "test_v043_beta37.py").write_text('''from pathlib import Path\nimport json\n\nROOT = Path(__file__).resolve().parents[1]\nCOMP = ROOT / "custom_components" / "navimower"\n\ndef test_beta37_identity_and_release_notes():\n    manifest = json.loads((COMP / "manifest.json").read_text())\n    assert manifest["version"] == "0.4.3-beta37"\n    notes = (ROOT / ".github/release-notes/0.4.3-beta37.md").read_text()\n    assert notes.startswith("title: Navimower 0.4.3-beta37")\n\ndef test_order_mode_is_on_main_schedule_form_without_raw_queue_field():\n    source = (COMP / "config_flow.py").read_text()\n    init = source[source.index("async def async_step_init"):source.index("def _schedule_order_selector")]\n    assert '"navimower_schedule_order"' not in init\n    assert "async_step_navimower_schedule" in source\n    assert "OPT_SCHEDULE_ORDER_MODE" in source\n    assert "vol.Optional(OPT_SCHEDULE_CUSTOM_QUEUE" not in source\n    assert "_seed_custom_queue" in source\n\ndef test_schedule_order_translation_contract():\n    for rel in ("strings.json", "translations/en.json"):\n        doc = json.loads((COMP / rel).read_text())\n        step = doc["options"]["step"]["navimower_schedule"]\n        assert step["data"]["navimower_schedule_order_mode"] == "Zone order"\n''', encoding="utf-8")

(ROOT / ".github" / "release-notes" / "0.4.3-beta37.md").write_text('''title: Navimower 0.4.3-beta37\n\n## Schedule order UX\n\n- Moves Automatic/Custom order selection into the main Navimower Schedule configuration screen.\n- Removes the raw custom queue ID text field from the user-facing options flow.\n- When Custom order is selected for the first time, seeds its queue from the same least-recently-completed ordering used by Automatic order.\n- Keeps an existing Custom queue persistent when switching modes; newly selected zones can be arranged later in Navimower Map Card.\n- Adds the missing Home Assistant strings/translations for the order selector.\n''', encoding="utf-8")
