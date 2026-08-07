"""Isolated regression test for the completed-session archive manager."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_completed_session_archive_manager() -> None:
    code = textwrap.dedent(
        r'''
        import asyncio
        import importlib.util
        from pathlib import Path
        import sys
        import types

        root = Path.cwd()
        def module(name):
            value = types.ModuleType(name)
            sys.modules[name] = value
            return value

        homeassistant = module("homeassistant")
        homeassistant.__path__ = []
        core = module("homeassistant.core")
        core.HomeAssistant = object
        helpers = module("homeassistant.helpers")
        helpers.__path__ = []
        storage = module("homeassistant.helpers.storage")

        class Store:
            values = {}
            def __init__(self, hass, version, key, **kwargs):
                self.key = key
            async def async_load(self):
                return self.values.get(self.key)
            async def async_save(self, value):
                self.values[self.key] = value
            async def async_remove(self):
                self.values.pop(self.key, None)
        storage.Store = Store

        module("custom_components")
        navimower = module("custom_components.navimower")
        navimower.__path__ = [str(root / "custom_components" / "navimower")]
        const = module("custom_components.navimower.const")
        const.DOMAIN = "navimower"
        svg = module("custom_components.navimower.session_svg")

        def fingerprint(session):
            return {
                "session_id": session["id"],
                "point_count": len(session["points"]),
                "ended_at_ms": session["ended_at_ms"],
                "segment_count": 1,
            }
        def build(session):
            return {"version": 1, "source": fingerprint(session), "mowed_area": {"path_d": "M0 0Z"}}
        def matches(render, session):
            return isinstance(render, dict) and render.get("source") == fingerprint(session)
        svg.build_session_svg_archive = build
        svg.render_matches_session = matches

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.session_archive",
            root / "custom_components" / "navimower" / "session_archive.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        class FakeHass:
            def async_create_task(self, coro, name=None):
                return asyncio.create_task(coro, name=name)
            async def async_add_executor_job(self, func, *args):
                return func(*args)

        class History:
            def __init__(self):
                self.session = {
                    "id": "s1", "active": False, "ended_at_ms": 100,
                    "points": [[1, 0, 0], [2, 1, 0]],
                }
                self.active_session_no = 3
            async def async_session_payload(self, session_id):
                return dict(self.session) if session_id == "s1" else None
            def session_summaries(self, include_points=False):
                return [{"id": "s1", "active": self.session["active"], "point_count": 2}]

        class Coordinator:
            def __init__(self):
                self.history = History()
                self.listeners = []
                self.data = {}
            def async_add_listener(self, callback):
                self.listeners.append(callback)
                return lambda: self.listeners.remove(callback)

        async def main():
            Store.values.clear()
            coordinator = Coordinator()
            manager = target.SessionArchiveManager(FakeHass(), "entry", coordinator)
            artifact = await manager.async_get("s1")
            assert artifact["mowed_area"]["path_d"] == "M0 0Z"
            assert "navimower_session_render_entry_s1" in Store.values
            assert await manager.async_get("s1") == artifact
            coordinator.history.session["active"] = True
            assert await manager.async_get("s1") is None

        asyncio.run(main())
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
