"""Regression coverage for Navimow schedule partitionPlan encoding."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
CLIENT = ROOT / "custom_components/navimower/api/client.py"


def _encoder():
    source = CLIENT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "NavimowCloudClient"
    )
    fn = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_partition_plan_hex"
    )
    fn.decorator_list = []
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "encode_partition_ids": lambda values: "".join(
            f"{value & 0xFF:02x}{(value >> 8) & 0xFF:02x}"
            for value in values
        )
    }
    exec(compile(module, str(CLIENT), "exec"), namespace)
    return namespace["_partition_plan_hex"], source


def test_schedule_encoder_uses_shared_uint16_partition_helper() -> None:
    _, source = _encoder()
    assert "from ..const import encode_partition_ids" in source
    assert "encode_partition_ids(ids).upper()" in source


def test_schedule_encoder_off_day() -> None:
    encode, _ = _encoder()
    assert encode(2, False, []) == "01020000"


def test_schedule_encoder_single_period_all_zones() -> None:
    encode, _ = _encoder()
    periods = [{"start_time": 41, "end_time": 58, "partition_ids": []}]
    assert encode(2, True, periods) == "01020101293A00"


def test_schedule_encoder_multiple_periods_all_zones() -> None:
    encode, _ = _encoder()
    periods = [
        {"start_time": 41, "end_time": 58, "partition_ids": []},
        {"start_time": 60, "end_time": 64, "partition_ids": []},
    ]
    assert encode(2, True, periods) == "01020102293A003C4000"


def test_schedule_encoder_single_uint16_zone() -> None:
    encode, _ = _encoder()
    periods = [{"start_time": 41, "end_time": 58, "partition_ids": [257]}]
    assert encode(2, True, periods) == "01020101293A010101"


def test_schedule_encoder_multiple_uint16_zones() -> None:
    encode, _ = _encoder()
    periods = [{"start_time": 41, "end_time": 58, "partition_ids": [1, 258]}]
    assert encode(2, True, periods) == "01020101293A0201000201"


def test_schedule_encoder_multiple_periods_with_zones() -> None:
    encode, _ = _encoder()
    periods = [
        {"start_time": 1, "end_time": 2, "partition_ids": [257]},
        {"start_time": 3, "end_time": 4, "partition_ids": [258, 513]},
    ]
    assert encode(5, True, periods) == "01050102010201010103040202010102"
