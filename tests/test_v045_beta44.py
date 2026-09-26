"""Regression contracts for Navimower 0.4.5-beta44 Activity causes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta44_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 44

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta44.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "What happened",
        "Activity",
        "navimower_activity",
        "Current zone",
        "Target zone",
        "Planned zones",
        "Navimower Schedule",
        "Context",
    ):
        assert phrase in notes


def test_beta44_activity_context_lifecycle_runtime() -> None:
    code = textwrap.dedent(
        r"""
        import importlib.util
        from pathlib import Path
        import sys
        import types

        root = Path.cwd()
        sys.modules["custom_components"] = types.ModuleType("custom_components")
        pkg = types.ModuleType("custom_components.navimower")
        pkg.__path__ = [str(root / "custom_components" / "navimower")]
        sys.modules["custom_components.navimower"] = pkg

        const = types.ModuleType("custom_components.navimower.const")
        const.DOMAIN = "navimower"
        sys.modules["custom_components.navimower.const"] = const

        ha = types.ModuleType("homeassistant")
        sys.modules["homeassistant"] = ha
        core = types.ModuleType("homeassistant.core")

        class Context:
            _counter = 0
            def __init__(self, *, parent_id=None, user_id=None):
                type(self)._counter += 1
                self.id = f"ctx-{type(self)._counter}"
                self.parent_id = parent_id
                self.user_id = user_id

        def callback(func):
            return func

        core.Context = Context
        core.HomeAssistant = object
        core.callback = callback
        sys.modules["homeassistant.core"] = core

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.activity_context",
            root / "custom_components" / "navimower" / "activity_context.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class Bus:
            def __init__(self):
                self.events = []
            def async_fire(self, event_type, data, context=None):
                self.events.append((event_type, dict(data), context))

        class Hass:
            def __init__(self):
                self.bus = Bus()

        class Coordinator:
            def __init__(self):
                self.data = {
                    "name": "Tont",
                    "activity": "mowing",
                    "current_zone": "Street2",
                    "current_physical_zone": "Street2",
                    "target_zone": "Street2",
                    "planned_zones": "Street2, Yard2",
                }
                self._listener = None
                self._last_mow_command_trace = None
                self._last_resume_command = None
            def async_add_listener(self, cb):
                self._listener = cb
                return lambda: None

        hass = Hass()
        coordinator = Coordinator()
        manager = module.NavimowerActivityContextManager(hass, "entry-1", coordinator)
        manager.start()

        parent = Context(user_id="user-1")
        manager.note_command(
            source="navimower_schedule_window_closed",
            message="Navimower Schedule sent the mower to the dock.",
            context=parent,
        )
        coordinator.data = {
            **coordinator.data,
            "activity": "returning",
            "current_zone": "Yard2",
            "target_zone": "No active target",
            "planned_zones": "No planned zones",
        }
        coordinator._listener()

        mower_context = manager.context_for("mower")
        zone_context = manager.context_for("current_zone")
        target_context = manager.context_for("target_zone")
        planned_context = manager.context_for("planned_zones")
        assert mower_context is not None
        assert zone_context is not None
        assert target_context is not None
        assert planned_context is not None
        assert mower_context.parent_id == parent.id
        assert mower_context.user_id == "user-1"

        messages = [data["message"] for _event, data, _context in hass.bus.events]
        assert "Navimower Schedule sent the mower to the dock." in messages
        assert any(
            "Vendor task zone selection changed from Street2 to Yard2" in value
            for value in messages
        )
        assert any("Immediate mowing target was cleared" in value for value in messages)
        assert any("planned zones were cleared" in value for value in messages)
        assert all(
            event == module.EVENT_NAVIMOWER_ACTIVITY
            for event, _data, _context in hass.bus.events
        )

        # A later update without a cause must clear the one-update context map.
        coordinator._listener()
        assert manager.context_for("mower") is None
        assert manager.context_for("current_zone") is None
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_beta44_entities_apply_and_clear_activity_contexts() -> None:
    entity = (COMPONENT / "entity.py").read_text(encoding="utf-8")
    assert 'self._navimower_key = str(key)' in entity
    assert 'manager.context_for(self._navimower_key)' in entity
    assert 'self.async_set_context(context if context is not None else Context())' in entity
    assert "self.async_write_ha_state()" in entity


def test_beta44_logbook_platform_describes_context_event() -> None:
    source = (COMPONENT / "logbook.py").read_text(encoding="utf-8")
    assert "def async_describe_events(" in source
    assert "EVENT_NAVIMOWER_ACTIVITY" in source
    assert "LOGBOOK_ENTRY_NAME" in source
    assert "LOGBOOK_ENTRY_MESSAGE" in source
    assert "async_describe_event(DOMAIN, EVENT_NAVIMOWER_ACTIVITY" in source


def test_beta44_command_paths_record_causes_before_optimistic_state() -> None:
    mower = (COMPONENT / "lawn_mower.py").read_text(encoding="utf-8")
    schedule = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")

    assert mower.index('self._note_activity_command(\n            "lawn_mower.dock"') < mower.index(
        "self.coordinator.set_pending_activity(ACTIVITY_RETURNING)"
    )
    assert schedule.index('self._note_activity_command(\n            source,\n            "Navimower Schedule sent the mower to the dock."') < schedule.index(
        "self.coordinator.set_pending_activity(ACTIVITY_RETURNING)"
    )
    assert 'context=call.context' in services
    assert '"Navimower Mow action requested mowing' in services
