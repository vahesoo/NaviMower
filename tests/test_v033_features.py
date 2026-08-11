"""Dependency-free regressions for Navimower v0.3.3 and later."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_const():
    spec = importlib.util.spec_from_file_location(
        "navimower_test_const", COMPONENT / "const.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_model_support():
    spec = importlib.util.spec_from_file_location(
        "navimower_test_model_support", COMPONENT / "model_support.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_zone_order_falls_back_to_robot_order_for_h1() -> None:
    services = (COMPONENT / "services.py").read_text()
    mower = (COMPONENT / "lawn_mower.py").read_text()
    support = _load_model_support()

    assert support.is_h1_generation("H1500", 20000002) is True
    assert support.is_h1_generation("H800E", 0) is True
    assert support.is_h1_generation("H215", 400000459) is False
    assert support.supports_ordered_zone_mowing("H1500", 20000002) is False
    assert support.supports_ordered_zone_mowing("H215", 400000459) is True

    for source in (services, mower):
        assert "requested_ordered" in source
        assert "supports_ordered_zone_mowing(" in source
        assert "ordered = requested_ordered and" in source
    assert "requested_zones if requested_ordered else []" in services
    assert "sel if requested_ordered else []" in mower


def test_mow_command_trace_remains_internal_without_diagnostics_lookup() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text()
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    services = (COMPONENT / "services.py").read_text()
    mower = (COMPONENT / "lawn_mower.py").read_text()
    for marker in (
        "begin_mow_command_trace",
        "record_mow_command_result",
        "record_mow_command_error",
        "mow_command_diagnostics",
        "partition_ids_hex_sent",
        "partition_ids_big_endian_reference",
        "request_shape",
    ):
        assert marker in coordinator
    assert "coordinator.begin_mow_command_trace(" in services
    assert "self.coordinator.begin_mow_command_trace(" in mower
    assert "command_status" not in diagnostics
    assert "last_mow_command" not in diagnostics
    assert "extra vendor requests" in diagnostics


def test_command_number_extraction_handles_known_response_shapes() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_extract_command_number"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"Any": object}
    exec(compile(ast.fix_missing_locations(module), "coordinator.py", "exec"), namespace)
    extract = namespace["_extract_command_number"]
    assert extract({"cmd_num": "123"}) == "123"
    assert extract({"data": {"cmdNum": 456}}) == "456"
    assert extract("789") == "789"
    assert extract({"data": {"status": 1}}) is None


def test_card_route_simplifier_uses_30_cm_and_keeps_endpoints() -> None:
    const = _load_const()
    assert const.MAP_CARD_MIN_POINT_DISTANCE_M == 0.30

    source = (COMPONENT / "zone_state.py").read_text()
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "simplify_xy_points"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "as_float": lambda value: float(value),
        "MAP_CARD_MIN_POINT_DISTANCE_M": 0.30,
    }
    exec(compile(ast.fix_missing_locations(module), "zone_state.py", "exec"), namespace)
    simplify = namespace["simplify_xy_points"]
    points = [
        [0.0, 0.0],
        [0.10, 0.0],
        [0.29, 0.0],
        [0.30, 0.0],
        [0.45, 0.0],
        [0.61, 0.0],
        [0.62, 0.0],
    ]
    assert simplify(points) == [[0.0, 0.0], [0.30, 0.0], [0.61, 0.0], [0.62, 0.0]]
    assert simplify([[1.0, 1.0], [1.0, 1.0]]) == [[1.0, 1.0], [1.0, 1.0]]


def test_daily_trails_publish_simplified_segments() -> None:
    source = (COMPONENT / "zone_state.py").read_text()
    assert "simplify_xy_points(current_segment)" in source
    assert 'row["render_point_count"]' in source


def test_daily_trail_cycle_markers_survive_storage_and_prevent_merge() -> None:
    history = (COMPONENT / "history.py").read_text()
    assert '"cycle_reset_zone_ids": _unique_ints(session.get("cycle_reset_zone_ids"))' in history
    assert '"force_new_session_once": self._force_new_session_once' in history
    assert '"force_new_cycle_zone_ids": list(self._force_new_cycle_zone_ids)' in history
    assert 'if _unique_ints(continuation.get("cycle_reset_zone_ids")):' in history
    assert '"cycle_reset_zone_ids": cycle_reset_zone_ids' in history


def test_daily_trails_do_not_treat_every_dock_session_as_new_cycle() -> None:
    source = (COMPONENT / "zone_state.py").read_text()
    assert "boundary_before_next" in source
    assert "and not active" in source
    assert 'if "reset" in reason or session.get("completed") is True:' in source
    assert "zone_cycle_boundaries" in source
    assert "by_zone.pop(zone_id, None)" in source


def test_explicit_cycle_marker_blocks_short_gap_session_merge() -> None:
    source = (COMPONENT / "history.py").read_text()
    tree = ast.parse(source)
    names = {"_as_int", "_unique_ints", "_session_end_ms", "_sessions_can_merge"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {
        "Any": object,
        "_SESSION_CLOCK_SKEW_MS": 30_000,
        "_SESSION_MERGE_GAP_MS": 300_000,
    }
    exec(compile(ast.fix_missing_locations(module), "history.py", "exec"), namespace)
    can_merge = namespace["_sessions_can_merge"]
    previous = {
        "id": "old",
        "started_at_ms": 1_000,
        "ended_at_ms": 10_000,
        "legacy": False,
    }
    continuation = {
        "id": "new",
        "started_at_ms": 20_000,
        "legacy": False,
        "cycle_reset_zone_ids": [13],
    }
    assert can_merge(previous, continuation) is False
