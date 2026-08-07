"""Regression contracts for Navimower 0.4.1-beta2."""
from __future__ import annotations
from datetime import date
import importlib.util, json, sys, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_v041_beta2_test"
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
def test_beta2_release_notes():
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta2.md").read_text(); assert "Zone progress and coverage ownership" in notes; assert "Mowing-footprint width" in notes; assert "316" in notes
def test_progress_owner_never_falls_back_to_physical_zone():
    source = (COMPONENT / "coordinator.py").read_text(); block = source.split("mqtt_zone_id = _valid_zone_id", 1)[1].split('snapshot["coverage"]', 1)[0]
    assert "active_progress_zone = mqtt_zone_id or cloud_zone_id" in block; assert "or physical_zone_id" not in block
    refresh = source.split("def _refresh_zone_model", 1)[1].split("def _session_completed", 1)[0]; assert 'snapshot.get("active_zone_progress_zone_id")' in refresh
def test_history_uses_explicit_progress_owner_and_does_not_split_session():
    source = (COMPONENT / "history.py").read_text(); update = source.split("def update_zone_history", 1)[1].split("def update_from_snapshot", 1)[0]
    assert "active_progress_zone_id" in update; assert 'active.get("visited_zone_ids")[-1]' not in update
    prepare = source.split("def prepare_cycle", 1)[1].split("def cycle_diagnostics", 1)[0]; assert 'snapshot.get("active_zone_progress_zone_id")' in prepare; assert 'active.setdefault("zone_cycle_boundaries", [])' in prepare; assert "_finish_active_locked" not in prepare; assert "_discard_active_locked" not in prepare
def test_unknown_zone_height_does_not_disable_whole_mower():
    source = (COMPONENT / "coordinator.py").read_text(); capability = source.split("raw_zone_heights =", 1)[1].split("settings =", 1)[0]
    assert "encoded_zone_height" not in capability; assert "any(value is not None for value in zone_height_values)" in capability
    detail = source.split("def _build_zone_details", 1)[1].split("def _apply_active_zone_progress", 1)[0]; assert "unknown_encoded" in detail; assert "global_height" in detail
def test_daily_trail_zone_boundary_replaces_only_that_zone_inside_one_session():
    package = types.ModuleType(PACKAGE); package.__path__ = [str(COMPONENT)]; sys.modules.setdefault(PACKAGE, package); _load(f"{PACKAGE}.const", COMPONENT / "const.py"); zone_state = _load(f"{PACKAGE}.zone_state", COMPONENT / "zone_state.py"); day = date(2026, 8, 7)
    def point(stamp, x, zone): return [stamp, x, 1.0, 0.0, "mowing", 4, -1, zone]
    payload = zone_state.build_daily_trails(sessions=[{"id":"one-task","started_at_ms":1000,"active":False,"completed":False,"segment_starts_ms":[1000],"zone_cycle_boundaries":[{"zone_id":13,"at_ms":5000}],"points":[point(1000,1.0,13),point(2000,2.0,13),point(3000,10.0,24),point(4000,11.0,24),point(5000,3.0,13),point(6000,4.0,13)]}], map_zones=[], local_date=day, to_local_date=lambda _stamp: day, revision=1)
    zones = {row["zone_id"]: row for row in payload["zones"]}; assert zones[13]["segments"] == [[[3.0,1.0],[4.0,1.0]]]; assert zones[24]["segments"] == [[[10.0,1.0],[11.0,1.0]]]
def test_session_svg_uses_reported_swath_width():
    package = types.ModuleType(PACKAGE + "_svg"); package.__path__ = [str(COMPONENT)]; sys.modules.setdefault(PACKAGE + "_svg", package); _load(f"{PACKAGE}_svg.const", COMPONENT / "const.py"); _load(f"{PACKAGE}_svg.zone_state", COMPONENT / "zone_state.py"); svg = _load(f"{PACKAGE}_svg.session_svg", COMPONENT / "session_svg.py")
    session = {"id":"x-series","active":False,"ended_at_ms":3000,"segment_starts_ms":[1000],"mowing_path_width_m":0.5,"points":[[1000,0.0,0.0,0.0,"mowing",4,5,1],[2000,2.0,0.0,0.0,"mowing",4,5,1],[3000,4.0,0.0,0.0,"mowing",4,5,1]]}
    artifact = svg.build_session_svg_archive(session); assert artifact is not None; assert artifact["version"] == 2; assert artifact["mowed_area"]["swath_width_m"] == 0.5
