"""Apply the reviewed v0.3.4-beta3 patch once on the GitHub branch."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "custom_components/navimower/history.py",
    '''                        if progress is not None:
                            previous_progress = (
                                _as_int(task_progress.get(str(active_zone_id))) or 0
                            )
                            task_progress[str(active_zone_id)] = max(
                                previous_progress, progress
                            )
''',
    '''                        if progress is not None:
                            previous_progress = _as_int(
                                task_progress.get(str(active_zone_id))
                            )
                            vendor_progress = _as_int(
                                active_detail.get("vendor_percentage")
                                if active_detail.get("vendor_percentage") is not None
                                else active_detail.get("percentage")
                            )
                            stale_completed_value = bool(
                                previous_progress is not None
                                and previous_progress >= VENDOR_COMPLETION_PROGRESS_MIN
                                and progress < VENDOR_COMPLETION_PROGRESS_MIN
                                and vendor_progress is not None
                                and vendor_progress < VENDOR_COMPLETION_PROGRESS_MIN
                            )
                            if stale_completed_value:
                                # A restored/transition spike of 100% must not pin
                                # an actively mowed zone when both the fresh work
                                # counter and vendor coverage confirm it is still
                                # incomplete. This heals beta2-era session state.
                                task_progress[str(active_zone_id)] = progress
                                if active.get("completion_reason") == "vendor_progress":
                                    active["completed"] = None
                                    active["completion_reason"] = None
                                    active["final_progress"] = {}
                            else:
                                task_progress[str(active_zone_id)] = max(
                                    previous_progress or 0, progress
                                )
''',
)

replace_once(
    "custom_components/navimower/history.py",
    '''                if (
                    percentages
                    and len(percentages) == len(active.get("zone_ids") or [])
                    and all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in percentages)
                ):
                    active["completed"] = True
                    active["completion_reason"] = (
                        active.get("completion_reason") or "vendor_progress"
                    )
''',
    '''                if (
                    percentages
                    and len(percentages) == len(active.get("zone_ids") or [])
                    and all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in percentages)
                ):
                    active["completed"] = True
                    active["completion_reason"] = (
                        active.get("completion_reason") or "vendor_progress"
                    )
                elif active.get("completion_reason") == "vendor_progress":
                    # Recompute optimistic completion after a stale 100% value
                    # has been corrected by fresh active-zone telemetry.
                    active["completed"] = None
                    active["completion_reason"] = None
                    active["final_progress"] = {}
''',
)

replace_once(
    "custom_components/navimower/zone_state.py",
    '''        display_pct = clamp_pct(
            detail.get("progress")
            if detail.get("progress") is not None
            else detail.get("percentage")
            if detail.get("percentage") is not None
            else cloud.get("pct")
            if cloud.get("pct") is not None
            else persisted.get("percentage")
        )
        current_task_pct = clamp_pct(task_progress.get(str(zone_id)))
        task_override = (
            cycle_id is not None
            and zone_id in visited_zone_ids
            and current_task_pct is not None
        )
        if task_override:
            display_pct = current_task_pct
''',
    '''        display_pct = clamp_pct(
            detail.get("progress")
            if detail.get("progress") is not None
            else detail.get("percentage")
            if detail.get("percentage") is not None
            else cloud.get("pct")
            if cloud.get("pct") is not None
            else persisted.get("percentage")
        )
        live_display_pct = display_pct
        current_task_pct = clamp_pct(task_progress.get(str(zone_id)))
        task_override = (
            cycle_id is not None
            and zone_id in visited_zone_ids
            and current_task_pct is not None
        )
        recovered_stale_completion = bool(
            zone_id == active_zone_id
            and task_override
            and current_task_pct is not None
            and current_task_pct >= COMPLETION_THRESHOLD
            and live_display_pct is not None
            and live_display_pct < COMPLETION_THRESHOLD
            and vendor_pct is not None
            and vendor_pct < COMPLETION_THRESHOLD
        )
        if recovered_stale_completion:
            # Defence in depth: even before the corrected session checkpoint is
            # written, never let a restored/transition 100% override two fresh
            # incomplete counters for the currently active zone.
            current_task_pct = live_display_pct
            display_pct = live_display_pct
            task_override = False
        elif task_override:
            display_pct = current_task_pct
''',
)

replace_once(
    "custom_components/navimower/zone_state.py",
    '''        progress_source = detail.get("progress_source") or persisted.get(
            "progress_source"
        )
''',
    '''        progress_source = detail.get("progress_source") or persisted.get(
            "progress_source"
        )
        if recovered_stale_completion:
            progress_source = "active_live_recovery"
''',
)

replace_once(
    "custom_components/navimower/zone_state.py",
    '''                "progress_source": (
                    progress_source
                    or ("task_cycle" if task_override else "coverage")
                ),
                "cutting_height_mm": detail.get("cutting_height_mm"),
''',
    '''                "progress_source": (
                    progress_source
                    or ("task_cycle" if task_override else "coverage")
                ),
                "progress_recovered": recovered_stale_completion,
                "cutting_height_mm": detail.get("cutting_height_mm"),
''',
)

replace_once(
    "custom_components/navimower/sensor.py",
    '''        map_data = self.data.get("map") or {}
        active = self.coordinator.history.active_session
        return {
''',
    '''        map_data = self.data.get("map") or {}
        # The session index contains metadata only. Avoid deep-copying active and
        # cached full sessions (including thousands of route points) on every
        # Home Assistant state write.
        session_index = self.coordinator.history.sessions_index_payload()
        return {
''',
)

replace_once(
    "custom_components/navimower/sensor.py",
    '''            "active_session_id": (active or {}).get("id"),
            "retained_session_count": len(
                self.coordinator.history.session_summaries(include_points=False)
            ),
''',
    '''            "active_session_id": session_index.get("active_session_id"),
            "retained_session_count": len(session_index.get("sessions") or []),
''',
)

for test_path in (
    "tests/test_v030_architecture.py",
    "tests/test_v031_features.py",
):
    replace_once(test_path, '"0.3.4-beta2"', '"0.3.4-beta3"')

replace_once(
    "custom_components/navimower/manifest.json",
    '"version": "0.3.4-beta2"',
    '"version": "0.3.4-beta3"',
)

replace_once(
    "CHANGELOG.md",
    "# Changelog\n\n",
    '''# Changelog

## 0.3.4-beta3 — active-zone progress and map-data performance

- Recover an active zone from a stale/restored 100% session value when both the
  fresh MQTT work counter and vendor coverage confirm that the zone is still
  below the practical completion threshold.
- Heal the persisted active-session progress and clear an optimistic
  `vendor_progress` completion flag, preventing an incomplete route from being
  finalized as completed after a restart or transient 100% work counter.
- Add a second defensive check in the central zone model so stale completion can
  never override fresh active-zone telemetry while the history checkpoint heals.
- Build Map data attributes from the lightweight session index instead of deep
  copying cached sessions and thousands of route points during every state write.
- Add regressions based on the H215 Street 100%→29% and X390 Maja tagune
  100%→32% diagnostic cases.

''',
)

(ROOT / ".github/release-notes/0.3.4-beta3.md").write_text(
    '''title: Navimower 0.3.4-beta3 — progress recovery and Map data performance

## Active-zone progress recovery

- Fix active zones becoming pinned at 100% after a Home Assistant restart or a transient vendor work-counter spike.
- When the stored session says 95–100% but both fresh active-zone work progress and vendor coverage are below 95%, replace the stale stored value with the fresh live value.
- Clear a false optimistic `vendor_progress` completion flag so an unfinished route is not finalized as a completed cycle.
- Keep the normal monotonic anti-regression filter for all other cases, including edge/subtask counters where vendor coverage remains complete.
- Add a defence-in-depth check in the zone model, so the corrected value reaches zone sensors and Navimower Map Card immediately, before the next history checkpoint.

The fix is based on field diagnostics from both mower families:

- H215 Street: stored 100%, fresh MQTT work progress 29%, vendor coverage 12%.
- X390 Maja tagune: stored 100%, fresh MQTT work progress 32%, vendor coverage 30%.

## Map data performance

- Stop deep-copying the active session and every cached route when Home Assistant writes the `Map data` sensor state.
- Read `active_session_id` and retained session count from the lightweight metadata-only session index.
- Preserve all existing Map Card API paths, revisions and visible sensor attributes.
- Prevent the Home Assistant `Updating state ... took more than 0.4 seconds` warning caused by large retained route caches.

## Upgrade

Install the prerelease through HACS and restart Home Assistant. Any currently pinned active-zone percentage should correct itself on the next fresh MQTT/private-cloud update; deleting history or recreating config entries is not required.
''',
    encoding="utf-8",
)

(ROOT / "tests/test_v034_beta3.py").write_text(
    '''"""Regressions for Navimower v0.3.4-beta3."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_v034_beta3_test"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _zone_state_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules.setdefault(PACKAGE, package)
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    return _load_module(f"{PACKAGE}.zone_state", COMPONENT / "zone_state.py")


def _model(*, vendor: float, live: float):
    module = _zone_state_module()
    return module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Completed", "area": 1000.0},
            {"id": 24, "name": "Active", "area": 100.0},
        ],
        zone_details=[
            {
                "id": 13,
                "name": "Completed",
                "area_m2": 1000.0,
                "percentage": 100,
                "vendor_percentage": 100,
            },
            {
                "id": 24,
                "name": "Active",
                "area_m2": 100.0,
                "percentage": vendor,
                "vendor_percentage": vendor,
                "progress": live,
                "progress_source": "mqtt_map_work_position",
            },
        ],
        coverage={
            "zones": [
                {"id": 13, "area": 1000.0, "finished": 1000.0, "pct": 100},
                {"id": 24, "area": 100.0, "finished": vendor, "pct": vendor},
            ]
        },
        zone_history={},
        active_session={
            "id": "active-cycle",
            "zone_ids": [13, 24],
            "visited_zone_ids": [13, 24],
            "task_zone_progress": {"13": 100, "24": 100},
        },
        active_zone_id=24,
    )


def test_stale_completed_active_zone_uses_fresh_live_progress() -> None:
    zones, totals = _model(vendor=12, live=29)
    active = next(row for row in zones if row["id"] == 24)
    assert active["coverage_pct"] == 29.0
    assert active["task_progress_pct"] == 29.0
    assert active["mowed_area_m2"] == 29.0
    assert active["progress_source"] == "active_live_recovery"
    assert active["progress_recovered"] is True
    assert totals["completed_zone_count"] == 1
    assert totals["map_coverage_pct"] == 93.5


def test_completed_vendor_coverage_keeps_monotonic_task_value() -> None:
    zones, _totals = _model(vendor=100, live=29)
    active = next(row for row in zones if row["id"] == 24)
    assert active["coverage_pct"] == 100.0
    assert active["task_progress_pct"] == 100.0
    assert active["progress_recovered"] is False


def test_history_heals_stale_completion_and_false_completed_flag() -> None:
    source = (COMPONENT / "history.py").read_text()
    assert "stale_completed_value = bool(" in source
    assert "vendor_progress < VENDOR_COMPLETION_PROGRESS_MIN" in source
    assert 'task_progress[str(active_zone_id)] = progress' in source
    assert 'active.get("completion_reason") == "vendor_progress"' in source
    assert 'active["completed"] = None' in source


def test_map_data_uses_metadata_only_session_index() -> None:
    source = (COMPONENT / "sensor.py").read_text()
    block = source.split("class NavimowerMapDataSensor", 1)[1]
    assert "sessions_index_payload()" in block
    assert "history.active_session" not in block
    assert "session_summaries(" not in block
    assert 'session_index.get("active_session_id")' in block
''',
    encoding="utf-8",
)
