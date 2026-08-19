from pathlib import Path
from textwrap import dedent

source_path = Path(".github/scripts/remote_beta_builder.py")
source = source_path.read_text(encoding="utf-8")
replacements = {
    '    "    def update_from_snapshot(\\n",\n': '    "    def update_from_snapshot(",\n',
    '    path.write_text(text[:start_at] + dedent(new).lstrip("\\n") + text[end_at:], encoding="utf-8")\n': '    path.write_text(text[:start_at] + new.lstrip("\\n") + text[end_at:], encoding="utf-8")\n',
}
for old, new in replacements.items():
    if old not in source:
        raise SystemExit(f"beta21 wrapper marker not found: {old!r}")
    source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"))

# Replace beta20 arbitration runtime tests with beta21 vendor-coverage authority
# regressions. The integration must never depend on a live 100 from
# mapWorkPosition/task progress to write Last completed.
test_path = Path("tests/test_history_merge.py")
text = test_path.read_text(encoding="utf-8")
start_marker = "def practical_completion_threshold_test() -> None:\n"
end_marker = "async def explicit_partial_reset_test() -> None:\n"
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("beta21 history runtime test markers not found")
block = dedent(r'''
def _coverage_snapshot(zone_rows, *, target, physical, activity="mowing", age=0):
    return {
        "coverage": {"zones": zone_rows},
        "zone_details": [
            {
                "id": row["id"],
                "name": row.get("name", f"Zone {row['id']}"),
                "percentage": row.get("pct"),
                "vendor_percentage": row.get("pct"),
                "vendor_start_time": row.get("start_time"),
                "vendor_end_time": row.get("end_time"),
            }
            for row in zone_rows
        ],
        "coverage_source_age": age,
        "active_zone_progress_zone_id": target,
        "current_physical_zone_id": physical,
        "activity": activity,
    }


def vendor_coverage_completion_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-coverage", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0}, pose_time=2_300_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    low = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 90,
          "start_time": 2_300_000_000, "end_time": 2_300_000_010}],
        target=13, physical=13,
    )
    manager.prepare_cycle(low, pose_time=2_300_000_010)
    manager.update_from_snapshot(low)
    assert "last_completed_at" not in manager.zone_history()["13"]

    done = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 100,
          "start_time": 2_300_000_000, "end_time": 2_300_000_020}],
        target=13, physical=13,
    )
    manager.prepare_cycle(done, pose_time=2_300_000_020)
    manager.update_from_snapshot(done)
    active = manager.active_session
    assert active is not None
    assert active["completed"] is True
    assert active["completion_reason"] == "vendor_coverage"
    zone = manager.zone_history()["13"]
    assert zone["last_completed_progress"] == 100
    assert zone["last_completed_source"] == "private_zone_coverage"
    assert zone["last_completed_confirmation"] == "coverage_100_after_incomplete"


vendor_coverage_completion_test()


def private_live_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-live-private", "TEST")
    manager.process_pose(
        position={"x": 1.0, "y": 1.0}, pose_time=2_410_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24], physical_zone_id=24,
    )
    partial = _coverage_snapshot(
        [{"id": 24, "name": "Yard", "pct": 25,
          "start_time": 2_410_000_000, "end_time": 2_410_000_010}],
        target=24, physical=24,
    )
    manager.prepare_cycle(partial, pose_time=2_410_000_010)
    manager.update_from_snapshot(partial)
    manager.update_zone_history(
        partial["coverage"], partial["zone_details"],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="private_map_work_position",
        active_zone_progress_source_age=0,
        task_progress=100, task_progress_source="mqtt_task_percentage",
        task_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, coverage_source_age=0,
        activity="mowing", observed_at=2_410_000_020,
    )
    assert "last_completed_at" not in manager.zone_history()["24"]


private_live_100_never_completes_test()


def stale_coverage_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-stale-coverage", "TEST")
    manager.process_pose(
        position={"x": 2.0, "y": 2.0}, pose_time=2_420_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    stale = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 100,
          "start_time": 2_200_000_000, "end_time": 2_200_000_100}],
        target=13, physical=13,
    )
    manager.prepare_cycle(stale, pose_time=2_420_000_010)
    manager.update_from_snapshot(stale)
    assert "last_completed_at" not in manager.zone_history()["13"]
    diag = manager.cycle_diagnostics()["active_completion"]
    assert diag["candidates"]["13"]["reason"] == "coverage_100_without_current_cycle_evidence"


stale_coverage_100_never_completes_test()


def multi_zone_target_transition_completion_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-multi-zone", "TEST")
    manager.process_pose(
        position={"x": 3.0, "y": 3.0}, pose_time=2_430_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24, 13], physical_zone_id=24,
    )
    first = _coverage_snapshot(
        [
            {"id": 24, "name": "Yard", "pct": 97,
             "start_time": 2_430_000_000, "end_time": 2_430_000_010},
            {"id": 13, "name": "Street", "pct": 0,
             "start_time": 2_430_000_000, "end_time": 0},
        ],
        target=24, physical=24,
    )
    manager.prepare_cycle(first, pose_time=2_430_000_010)
    manager.update_from_snapshot(first)
    assert "last_completed_at" not in manager.zone_history()["24"]

    second = _coverage_snapshot(
        [
            {"id": 24, "name": "Yard", "pct": 100,
             "start_time": 2_430_000_000, "end_time": 2_430_000_020},
            {"id": 13, "name": "Street", "pct": 20,
             "start_time": 2_430_000_015, "end_time": 2_430_000_020},
        ],
        target=13, physical=13,
    )
    manager.prepare_cycle(second, pose_time=2_430_000_020)
    manager.update_from_snapshot(second)
    zone = manager.zone_history()["24"]
    assert zone["last_completed_progress"] == 100
    assert zone["last_completed_source"] == "private_zone_coverage"
    diag = manager.cycle_diagnostics()["active_completion"]
    assert 24 in diag["confirmed"]
    assert 13 in diag["seen_incomplete"]


multi_zone_target_transition_completion_test()


def end_time_below_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-end-time", "TEST")
    manager.process_pose(
        position={"x": 4.0, "y": 4.0}, pose_time=2_440_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    partial = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 97,
          "start_time": 2_440_000_000, "end_time": 2_440_000_020}],
        target=13, physical=13, activity="returning",
    )
    manager.prepare_cycle(partial, pose_time=2_440_000_021)
    manager.update_from_snapshot(partial)
    assert "last_completed_at" not in manager.zone_history()["13"]


end_time_below_100_never_completes_test()
print("beta21 coverage completion tests passed")


''')
test_path.write_text(text[:start] + block + text[end:], encoding="utf-8")
