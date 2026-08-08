from pathlib import Path
import json

root = Path('.')
const = root / 'custom_components/navimower/const.py'
text = const.read_text()
text = text.replace('    "maintenance": 600,\n}', '    "maintenance": 600,\n    "errors": 30,\n}', 1)
text = text.replace('    "maintenance": 600,\n}', '    "maintenance": 600,\n    "errors": 60,\n}', 1)
text = text.replace('STATE_RETURNING: Final = "0220"\n', 'STATE_RETURNING: Final = "0220"\nSTATE_LIFTED: Final = "0302"\n')
text = text.replace('    STATE_RETURNING: ACTIVITY_RETURNING,\n}', '    STATE_RETURNING: ACTIVITY_RETURNING,\n    STATE_LIFTED: ACTIVITY_ERROR,\n}')
text = text.replace('    STATE_RETURNING: "Returning to dock",\n}', '    STATE_RETURNING: "Returning to dock",\n    STATE_LIFTED: "Lifted",\n}')
const.write_text(text)

coord = root / 'custom_components/navimower/coordinator.py'
text = coord.read_text()
text = text.replace('    STATE_MOWING,\n    STATE_RETURNING,', '    STATE_MOWING,\n    STATE_RETURNING,\n    STATE_LIFTED,')
text = text.replace('            "maintenance": lambda: self.client.maintenance(sn),\n', '            "maintenance": lambda: self.client.maintenance(sn),\n            "errors": lambda: self.client.errors(sn, vtype),\n')
text = text.replace('        maintenance = raw.get("maintenance") or {}\n', '        maintenance = raw.get("maintenance") or {}\n        hint_errors = raw.get("errors")\n')
text = text.replace('                "maintenance": maintenance,\n                "today_plan": today_plan,', '                "maintenance": maintenance,\n                "hint_error_compress": hint_errors,\n                "today_plan": today_plan,')
old = '''        battery = _as_int(state.get("battery"))
        if battery is None or not 0 <= battery <= 100:
            return
        previous_snapshot = dict(self.data or {})
        self._mqtt_battery = battery
        self._mqtt_battery_last_update = time.monotonic()
        self._mqtt_connected = True
        snapshot = dict(self.data or self._bootstrap_snapshot())
        self._stabilize_telemetry(snapshot, previous_snapshot)
        snapshot.update(self._connectivity_fields())
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)
'''
new = '''        battery = _as_int(state.get("battery"))
        state_name = str(state.get("state") or "").strip()
        if battery is None or not 0 <= battery <= 100:
            return
        previous_snapshot = dict(self.data or {})
        previous_state = str(previous_snapshot.get("state") or "")
        self._mqtt_battery = battery
        self._mqtt_battery_last_update = time.monotonic()
        self._mqtt_connected = True
        snapshot = dict(self.data or self._bootstrap_snapshot())
        if state_name == "isLifted":
            snapshot["state"] = "Lifted"
            snapshot["activity"] = ACTIVITY_ERROR
            snapshot["docked"] = False
            snapshot["docked_source"] = "mqtt_lifted_state"
        self._stabilize_telemetry(snapshot, previous_snapshot)
        snapshot.update(self._connectivity_fields())
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)
        if state_name == "isLifted" and previous_state != "Lifted":
            self.request_fast_refresh("MQTT state changed to isLifted")
'''
if old not in text:
    raise SystemExit('ingest_mqtt_state block not found')
text = text.replace(old, new)
coord.write_text(text)

manifest_path = root / 'custom_components/navimower/manifest.json'
manifest = json.loads(manifest_path.read_text())
manifest['version'] = '0.4.1-beta8'
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')

changelog = root / 'CHANGELOG.md'
old_changelog = changelog.read_text()
header = '''# Changelog

## 0.4.1-beta8 - lifted state and error catalog

- Map the observed private-cloud state code `0302` to **Lifted** and error activity.
- React immediately to official MQTT `state=isLifted`, publishing **Lifted** and requesting a fast private-cloud refresh.
- Poll and retain `/vehicle/vehicle/get-hint-error-compress` in the normal private-cloud cache (30 s active / 60 s idle) for ongoing error-catalog investigation.
- Keep the endpoint read-only and include its cached value in the snapshot raw diagnostics payload.

'''
if not old_changelog.startswith('# Changelog\n'):
    raise SystemExit('unexpected changelog header')
changelog.write_text(header + old_changelog[len('# Changelog\n\n'):])

notes = root / '.github/release-notes/0.4.1-beta8.md'
notes.write_text('''# Navimower 0.4.1-beta8

This beta adds the state/error observations confirmed during H215 lift-alarm testing.

- Private-cloud state `0302` is now exposed as **Lifted**.
- Official MQTT `state=isLifted` updates the mower state immediately instead of waiting for the next cloud poll.
- `/vehicle/vehicle/get-hint-error-compress` is now part of normal read-only polling and last-good caching, using a 30-second active and 60-second idle TTL.
- The cached compressed hint/error catalog is retained in raw diagnostic state for further reverse engineering.

No mower command behavior or Map Card code changes are included.
''')

test = root / 'tests/test_v041_beta8.py'
test.write_text('''"""Regression contracts for Navimower 0.4.1-beta8."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta8_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta8"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()
    assert "0302" in notes
    assert "get-hint-error-compress" in notes


def test_lifted_state_mapping_is_explicit() -> None:
    source = (COMPONENT / "const.py").read_text()
    assert 'STATE_LIFTED: Final = "0302"' in source
    assert 'STATE_LIFTED: ACTIVITY_ERROR' in source
    assert 'STATE_LIFTED: "Lifted"' in source


def test_hint_error_endpoint_is_polled_and_cached() -> None:
    const = (COMPONENT / "const.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert '"errors": 30' in const
    assert '"errors": 60' in const
    assert '"errors": lambda: self.client.errors(sn, vtype)' in coordinator
    assert '"hint_error_compress": hint_errors' in coordinator


def test_mqtt_lifted_state_is_published_immediately() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert 'state_name == "isLifted"' in source
    assert 'snapshot["state"] = "Lifted"' in source
    assert 'snapshot["activity"] = ACTIVITY_ERROR' in source
    assert 'request_fast_refresh("MQTT state changed to isLifted")' in source
''')
