"""Regression coverage for 0.4.5-beta26 Prepared History."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta26_prepared_history_prewarm_manifest_and_resources() -> None:
    code = textwrap.dedent(
        r'''
        import asyncio
        import importlib.util
        import json
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
        svg.SESSION_SVG_CLASSIFIER_VERSION = 2

        def fingerprint(session):
            return {
                "session_id": session["id"],
                "point_count": len(session["points"]),
                "ended_at_ms": session.get("ended_at_ms"),
                "segment_count": 1,
            }

        def build(session):
            sid = session["id"]
            return {
                "version": 2,
                "coordinate_space": "map_xy_m",
                "source": fingerprint(session),
                "mowed_area": {"path_d": f"M0 0L{sid[-1]} 0Z"},
                "travel": {"path_d": ""},
                "route": {"path_d": f"M0 0L{sid[-1]} 0"},
            }

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
        target._ARCHIVE_SETTLE_SECONDS = 0

        class FakeHass:
            def async_create_task(self, coro, name=None):
                return asyncio.create_task(coro, name=name)
            async def async_add_executor_job(self, func, *args):
                return func(*args)

        class History:
            def __init__(self):
                self.active_session_no = 10
                self.sessions = {
                    "s1": {
                        "id": "s1", "active": False, "ended_at_ms": 100,
                        "points": [[1, 0, 0], [2, 1, 0]],
                        "point_count": 2,
                    },
                    "s2": {
                        "id": "s2", "active": False, "ended_at_ms": 200,
                        "points": [[3, 1, 0], [4, 2, 0]],
                        "point_count": 2,
                    },
                    "s3": {
                        "id": "s3", "active": True, "ended_at_ms": None,
                        "points": [[5, 2, 0], [6, 3, 0]],
                        "point_count": 2,
                    },
                }
            def sessions_index_payload(self):
                rows = []
                for sid in ("s3", "s2", "s1"):
                    session = self.sessions.get(sid)
                    if session is None:
                        continue
                    rows.append({
                        "id": sid,
                        "active": session["active"],
                        "ended_at_ms": session["ended_at_ms"],
                        "point_count": session["point_count"],
                        "zone_ids": [36],
                    })
                return {
                    "entry_id": "entry",
                    "retention_days": 30,
                    "active_session_id": "s3",
                    "sessions": rows,
                }
            async def async_session_payload(self, session_id):
                session = self.sessions.get(session_id)
                return dict(session) if session else None

        class Coordinator:
            def __init__(self):
                self.history = History()
                self.listeners = []
                self.data = {"mowing_path_width_m": 0.25}
            def async_add_listener(self, callback):
                self.listeners.append(callback)
                return lambda: self.listeners.remove(callback)

        async def main():
            Store.values.clear()
            coordinator = Coordinator()
            manager = target.SessionArchiveManager(FakeHass(), "entry", coordinator)

            # Seed one old archive to prove restart prewarm reuses persistent
            # storage while building only the missing retained session.
            Store.values["navimower_session_render_entry_s1"] = build(
                coordinator.history.sessions["s1"]
            )

            manager.start()
            for _ in range(50):
                if manager.prewarm_complete:
                    break
                await asyncio.sleep(0.01)

            diag = manager.diagnostics()
            assert diag["prewarm_started"] is True
            assert diag["prewarm_complete"] is True
            assert diag["retained_session_count"] == 3
            assert diag["eligible_session_count"] == 2
            assert diag["ready_session_count"] == 2
            assert diag["pending_session_count"] == 0
            assert diag["prewarm_cache_hit_count"] == 1
            assert diag["prewarm_build_count"] == 1
            assert diag["lazy_build_count"] == 0
            assert diag["failure_count"] == 0
            assert diag["resource_bytes_ready_total"] > 0
            assert diag["indexed_archive_store_count"] == 2
            assert diag["pruned_archive_store_count"] == 0
            assert diag["archive_index_failure_count"] == 0

            manifest = manager.manifest()
            assert manifest["scope"] == "prepared_history"
            assert manifest["ready_only"] is True
            assert manifest["retained_session_count"] == 3
            assert manifest["eligible_session_count"] == 2
            assert manifest["ready_session_count"] == 2
            assert manifest["pending_session_count"] == 0
            rows = {row["id"]: row for row in manifest["sessions"]}
            assert rows["s1"]["render_ready"] is True
            assert rows["s2"]["render_ready"] is True
            assert rows["s3"]["render_ready"] is False
            assert rows["s3"]["render"] is None

            first = rows["s1"]["render"]
            second = rows["s2"]["render"]
            assert first["resource_id"] != second["resource_id"]
            assert first["url"].endswith(first["resource_id"])
            assert first["scope"] == "prepared_history_render"

            resource = manager.resource(first["resource_id"])
            assert resource is not None
            payload = json.loads(resource["body"])
            assert payload["scope"] == "prepared_history_render"
            assert payload["session_id"] == "s1"
            assert payload["render"]["mowed_area"]["path_d"]
            manager.record_resource_response(byte_length=len(resource["body"]))
            manager.record_resource_response(not_modified=True)

            # Legacy beta14 endpoint remains available and must hit the same
            # persistent artifact without rebuilding it.
            before_builds = manager.build_count
            legacy = await manager.async_get("s1")
            assert legacy["source"]["session_id"] == "s1"
            assert manager.build_count == before_builds
            assert manager.legacy_render_reads == 1
            assert await manager.async_get("s3") is None

            after = manager.diagnostics()
            assert after["manifest_reads"] == 1
            assert after["resource_reads"] == 1
            assert after["resource_bytes_served_total"] == len(resource["body"])
            assert after["resource_not_modified_count"] == 1
            assert after["resource_first_read_age_s"] is not None
            assert after["resource_last_read_age_s"] is not None

            stable_a = target._encode_resource(
                "entry",
                "stable",
                {
                    "version": 2,
                    "generated_at": "2026-09-22T10:00:00+00:00",
                    "source": {"session_id": "stable"},
                    "mowed_area": {"path_d": "M0 0Z"},
                    "travel": {"path_d": ""},
                    "route": {"path_d": "M0 0"},
                },
            )
            stable_b = target._encode_resource(
                "entry",
                "stable",
                {
                    "version": 2,
                    "generated_at": "2026-09-22T11:00:00+00:00",
                    "source": {"session_id": "stable"},
                    "mowed_area": {"path_d": "M0 0Z"},
                    "travel": {"path_d": ""},
                    "route": {"path_d": "M0 0"},
                },
            )
            assert stable_a["resource_id"] == stable_b["resource_id"]
            assert b"generated_at" not in stable_a["body"]

            discovery = manager.discovery()
            assert discovery["ready_only"] is True
            assert discovery["capabilities"]["content_addressed_resources"] is True
            assert discovery["capabilities"]["retained_session_prewarm"] is True
            assert "{resource_id}" in discovery["resource_url_template"]
            assert "{session_id}" in discovery["legacy_session_render_url_template"]

            # Derived render Stores now follow History retention instead of
            # accumulating forever after their source session is pruned.
            coordinator.history.sessions.pop("s1")
            await manager._async_prune_archive_stores()
            assert "navimower_session_render_entry_s1" not in Store.values
            cleanup = manager.diagnostics()
            assert cleanup["pruned_archive_store_count"] == 1
            assert cleanup["indexed_archive_store_count"] == 1

            await manager.async_stop()

        asyncio.run(main())
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_beta26_history_api_and_cleanup_contract() -> None:
    api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    prepared = (COMPONENT / "prepared_render_model.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")

    assert "/api/navimower/history-manifest/{entry_id}" in api
    assert "/api/navimower/history-resource/{entry_id}/{resource_id}" in api
    assert '"prepared_history": prepared_history' in api
    assert '"history_manifest_path": f"/api/navimower/history-manifest/{entry_id}"' in api
    assert "Cache-Control" in api and "max-age=31536000, immutable" in api
    assert 'request.headers.get("If-None-Match")' in api
    assert "manager.record_resource_response(not_modified=True)" in api
    assert 'manager.record_resource_response(byte_length=len(resource["body"]))' in api
    assert "NavimowerSessionRenderView" in api, "beta14 legacy fallback must remain"

    assert '"history_ready_manifest": True' in prepared
    assert '"history_content_addressed_resources": True' in prepared
    assert '"history_manifest_url"' in prepared
    assert '"history_resource_url_template"' in prepared

    # This field was emitted by the old three-day frontend History model and
    # has no consumer in the current Map Card or integration.
    assert '"history_day_count"' not in coordinator

    # Keep the old daily-trails API until beta15 is field-tested; it is a
    # backward-compatibility path, not part of Prepared History.
    assert '"daily_trails"' in coordinator
