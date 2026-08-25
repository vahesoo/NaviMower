from pathlib import Path
import json

root=Path.cwd(); comp=root/'custom_components/navimower'
# manifest
p=comp/'manifest.json'; d=json.loads(p.read_text()); d['version']='0.4.3-beta38'; p.write_text(json.dumps(d,indent=2)+'\n')
# controller public setter + richer attributes
p=comp/'navimower_schedule.py'; s=p.read_text()
needle='    def _update_options(self, **updates: Any) -> None:\n'
insert='''    async def async_set_custom_queue(self, zone_ids: list[int]) -> None:\n        """Persist a user-defined custom mowing queue without starting mowing."""\n        queue = self._normalize_queue(zone_ids)\n        selected = set(self._selected_zone_ids)\n        if not queue:\n            raise ValueError("Custom mowing queue may not be empty")\n        unknown = [zone_id for zone_id in queue if zone_id not in selected]\n        if unknown:\n            raise ValueError(f"Queue contains zones outside the selected schedule allowlist: {unknown}")\n        eligible = {int(row["id"]) for row in self._eligible_zones() if row.get("id") is not None}\n        unproven = [zone_id for zone_id in queue if zone_id not in eligible]\n        if unproven:\n            raise ValueError(f"Queue contains zones without a confirmed completed mowing: {unproven}")\n        self._custom_queue = queue\n        self._order_mode = SCHEDULE_ORDER_CUSTOM\n        self._runtime["completed_queue_slots"] = []\n        self._runtime["active_queue_slot"] = None\n        self._update_options(**{OPT_SCHEDULE_CUSTOM_QUEUE: list(queue), OPT_SCHEDULE_ORDER_MODE: SCHEDULE_ORDER_CUSTOM})\n        await self._save()\n        if self._enabled:\n            self._queue_evaluation()\n\n'''
assert needle in s; s=s.replace(needle,insert+needle)
s=s.replace('                "mode",\n                "start",', '                "mode",\n                "order_mode",\n                "custom_queue",\n                "start",')
p.write_text(s)
# services.py
p=comp/'services.py'; s=p.read_text()
s=s.replace('SERVICE_RESUME = "resume"\n','SERVICE_RESUME = "resume"\nSERVICE_SET_SCHEDULE_QUEUE = "set_schedule_queue"\n')
s=s.replace('RESUME_SCHEMA = vol.Schema(', 'SET_SCHEDULE_QUEUE_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string, vol.Required("zones"): vol.All(cv.ensure_list, [vol.Coerce(int)])})\n\nRESUME_SCHEMA = vol.Schema(')
needle='    async def _resume(call: ServiceCall) -> None:\n'
insert='''    async def _set_schedule_queue(call: ServiceCall) -> None:\n        coordinator = _resolve_coordinator(call)\n        controller = getattr(coordinator, "navimower_schedule", None)\n        if controller is None:\n            raise ServiceValidationError("Navimower Schedule controller is not available")\n        try:\n            await controller.async_set_custom_queue([int(v) for v in call.data.get("zones") or []])\n        except ValueError as err:\n            raise ServiceValidationError(str(err)) from err\n        except Exception as err:\n            raise HomeAssistantError(f"Navimower set_schedule_queue failed: {err}") from err\n\n'''
assert needle in s; s=s.replace(needle,insert+needle)
needle='    if not hass.services.has_service(DOMAIN, SERVICE_RESUME):\n'
insert='''    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE_QUEUE):\n        hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE_QUEUE, _set_schedule_queue, schema=SET_SCHEDULE_QUEUE_SCHEMA)\n'''
s=s.replace(needle,insert+needle); p.write_text(s)
# yaml
p=comp/'services.yaml'; s=p.read_text(); s += '''\nset_schedule_queue:\n  name: Set Navimower custom schedule queue\n  description: Persist the ordered Custom queue without enabling or starting Navimower Schedule. Zones may repeat.\n  fields:\n    device_id:\n      name: Mower\n      required: false\n      selector:\n        device:\n          integration: navimower\n    zones:\n      name: Ordered zones\n      description: Selected, previously completed schedule zone IDs in mowing order. IDs may repeat.\n      required: true\n      selector:\n        object:\n'''; p.write_text(s)
# test + notes
(root/'tests/test_v043_beta38.py').write_text('''from pathlib import Path\nimport json\nROOT=Path(__file__).resolve().parents[1]; C=ROOT/'custom_components/navimower'\ndef test_identity(): assert json.loads((C/'manifest.json').read_text())['version']=='0.4.3-beta38'\ndef test_queue_api():\n s=(C/'services.py').read_text(); c=(C/'navimower_schedule.py').read_text(); y=(C/'services.yaml').read_text()\n assert 'SERVICE_SET_SCHEDULE_QUEUE' in s and 'async_set_custom_queue' in c and 'set_schedule_queue:' in y\n assert '"order_mode"' in c and '"custom_queue"' in c\n''')
(root/'.github/release-notes/0.4.3-beta38.md').write_text('''title: Navimower 0.4.3-beta38\n\n## Map Card custom queue API\n- Adds `navimower.set_schedule_queue` for persisting Custom order without enabling or starting the scheduler.\n- Validates every queue item against the selected, previously completed schedule zones; duplicate zones are supported.\n- Exposes order mode and custom queue in Schedule Status attributes for dashboard editors.\n''')
