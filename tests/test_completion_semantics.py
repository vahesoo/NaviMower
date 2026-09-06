"""Dependency-free regressions for monotonic Last completed semantics."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SEMANTICS = COMPONENT / "completion_semantics.py"
RUNTIME = COMPONENT / "runtime.py"


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class _HistoryStub:
    _as_int = staticmethod(_as_int)
    _iso_ms = staticmethod(_iso_ms)


def _load_helpers() -> dict[str, Any]:
    source = SEMANTICS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "_verified_completion",
            "_completion_regression",
            "_preserve_newest_completion",
            "_near_reset_boundary",
            "_should_repair_legacy_completion",
        }
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "deepcopy": deepcopy,
        "_history": _HistoryStub,
        "_COMPLETION_FIELDS": (
            "last_completed_at",
            "last_completed_progress",
            "last_completed_source",
            "last_completed_confirmation",
            "last_completed_cycle_id",
        ),
        "_VERIFIED_COMPLETION_SOURCE": "private_zone_coverage",
        "_VERIFIED_CONFIRMATION_PREFIX": "coverage_100_",
    }
    exec(compile(module, SEMANTICS.name, "exec"), namespace)
    return namespace


def _record(stamp: str, *, cycle: str = "cycle-new") -> dict[str, Any]:
    return {
        "id": 36,
        "name": "Yard2",
        "last_completed_at": stamp,
        "last_completed_progress": 100,
        "last_completed_source": "private_zone_coverage",
        "last_completed_confirmation": "coverage_100_after_incomplete",
        "last_completed_cycle_id": cycle,
    }


def test_last_completed_never_moves_backwards() -> None:
    helpers = _load_helpers()
    preserve = helpers["_preserve_newest_completion"]

    previous = _record("2026-09-05T12:30:00+00:00", cycle="cycle-2")
    stale = _record("2026-09-03T15:20:00+00:00", cycle="cycle-3")
    protected, rejection = preserve(previous, stale)

    assert protected["last_completed_at"] == previous["last_completed_at"]
    assert protected["last_completed_cycle_id"] == "cycle-2"
    assert protected["last_completed_source"] == "private_zone_coverage"
    assert rejection is not None
    assert rejection["reason"] == "older_than_persisted_completion"
    assert rejection["candidate_at"] == stale["last_completed_at"]
    assert rejection["persisted_at"] == previous["last_completed_at"]


def test_newer_and_equal_completion_are_allowed() -> None:
    helpers = _load_helpers()
    preserve = helpers["_preserve_newest_completion"]

    previous = _record("2026-09-05T12:30:00+00:00", cycle="cycle-2")
    equal = _record("2026-09-05T12:30:00+00:00", cycle="cycle-3")
    newer = _record("2026-09-06T07:10:00+00:00", cycle="cycle-4")

    equal_result, equal_rejection = preserve(previous, equal)
    newer_result, newer_rejection = preserve(previous, newer)

    assert equal_result["last_completed_cycle_id"] == "cycle-3"
    assert equal_rejection is None
    assert newer_result["last_completed_at"] == newer["last_completed_at"]
    assert newer_result["last_completed_cycle_id"] == "cycle-4"
    assert newer_rejection is None


def test_verified_coverage_completion_survives_nearby_reset_boundary() -> None:
    helpers = _load_helpers()
    verified = helpers["_verified_completion"]
    should_repair = helpers["_should_repair_legacy_completion"]

    row = _record("2026-09-06T07:10:00+00:00")
    stamp = _iso_ms(row["last_completed_at"])
    assert stamp is not None
    assert verified(row) is True
    assert (
        should_repair(
            row,
            zone_id=36,
            reset_boundaries={36: [stamp + 15_000]},
        )
        is False
    )


def test_legacy_unverified_completion_is_still_repaired() -> None:
    helpers = _load_helpers()
    should_repair = helpers["_should_repair_legacy_completion"]

    stamp_text = "2026-09-06T07:10:00+00:00"
    stamp = _iso_ms(stamp_text)
    assert stamp is not None

    missing_progress = {
        "id": 36,
        "last_completed_at": stamp_text,
    }
    assert (
        should_repair(
            missing_progress,
            zone_id=36,
            reset_boundaries={},
        )
        is True
    )

    beta_reset_stamp = {
        "id": 36,
        "last_completed_at": stamp_text,
        "last_completed_progress": 100,
        "last_completed_source": "legacy_vendor_timestamp",
    }
    assert (
        should_repair(
            beta_reset_stamp,
            zone_id=36,
            reset_boundaries={36: [stamp - 30_000]},
        )
        is True
    )


def test_runtime_installs_completion_semantics_after_history_layer() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "from .completion_semantics import install_completion_semantics" in runtime
    history_index = runtime.index("install_history_performance()")
    completion_index = runtime.index("install_completion_semantics()")
    map_index = runtime.index("install_map_api_performance()")
    assert history_index < completion_index < map_index

    semantics = SEMANTICS.read_text(encoding="utf-8")
    assert "older_than_persisted_completion" in semantics
    assert "legacy_unverified_completion" in semantics
    assert "private_zone_coverage" in semantics
    assert "coverage_100_" in semantics
