from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).parents[2]


def patch_client() -> None:
    path = ROOT / "custom_components/navimower/api/client.py"
    source = path.read_text(encoding="utf-8")

    if "from ..const import encode_partition_ids\n" not in source:
        needle = "from ..discovery import structure_summary\n"
        if needle not in source:
            raise RuntimeError("client import anchor not found")
        source = source.replace(
            needle,
            "from ..const import encode_partition_ids\n" + needle,
            1,
        )

    pattern = re.compile(
        r"    @staticmethod\n"
        r"    def _partition_plan_hex\(day: int, enabled: bool, periods: list\[dict\]\) -> str:\n"
        r".*?"
        r"\n    def set_day_schedule\(",
        re.S,
    )
    replacement = '''    @staticmethod
    def _partition_plan_hex(day: int, enabled: bool, periods: list[dict]) -> str:
        """Encode one Navimow weekday plan for the ``s:mower`` device command.

        The app-captured layout is::

            01 <day> <open> <n_periods> [<start> <end> <n_zones> <zone_id>...]...

        Header, time-slot and zone-count fields are one byte. Zone ids are
        little-endian uint16 values, matching the partition-id encoding used by
        mowing commands. An empty zone list means all zones.
        """
        out = [
            "%02X" % (value & 0xFF)
            for value in (1, int(day), 1 if enabled else 0, len(periods))
        ]
        for period in periods:
            ids = [int(zone_id) for zone_id in (period.get("partition_ids") or [])]
            out.append(
                "%02X%02X%02X"
                % (
                    int(period["start_time"]) & 0xFF,
                    int(period["end_time"]) & 0xFF,
                    len(ids),
                )
            )
            out.append(encode_partition_ids(ids).upper())
        return "".join(out)

    def set_day_schedule('''
    source, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"schedule encoder replacement count was {count}")
    path.write_text(source, encoding="utf-8")


def write_tests() -> None:
    path = ROOT / "tests/test_v042_beta5_schedule_encoding.py"
    path.write_text('''"""Regression coverage for Navimow schedule partitionPlan encoding."""
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
''', encoding="utf-8")


def write_release_notes() -> None:
    path = ROOT / ".github/release-notes/0.4.2-beta5.md"
    if path.exists():
        raise RuntimeError("beta5 release notes already exist")
    path.write_text('''title: Navimower 0.4.2-beta5

Navimower 0.4.2-beta5 fixes zone-restricted mowing schedule writes so the robot receives the same 16-bit partition-id layout used by the official Navimow app and by Navimower mowing commands.

### Fixed schedule zone encoding

The per-day `partitionPlan` device payload keeps its one-byte header, 15-minute time slots and zone-count fields, but each selected zone id is now encoded as a **little-endian uint16** value through Navimower's existing `encode_partition_ids()` helper.

The previous inherited encoder wrote each selected zone id as one byte. That shifted every following byte in the device payload. Depending on the schedule, the mower could therefore drop selected zones, misread a later period, or create a phantom `00:15-00:15` period that then synchronized back to the Navimow app.

Multi-period schedules with no selected zones were not affected by this particular bug; the corrected layout matters whenever a period explicitly targets one or more zones.

### Regression coverage

Beta5 adds byte-level schedule tests for a disabled day, one and multiple all-zones periods, one and multiple selected zones, and multiple zone-restricted periods. The tests also require the schedule encoder to use the shared partition-id helper so schedule and immediate mowing commands cannot silently diverge again.

### Recovery for an existing phantom period

If a mower already contains a bogus `00:15-00:15` schedule entry created by the old payload, update to beta5 and save that weekday again from Home Assistant. `navimower.set_schedule` rewrites the complete day, so the corrected payload should replace the malformed onboard day plan.

### Upstream validation

This fix follows the app-captured correction published in `ilguala/navimow_pro` v0.2.9 for issue #5. Their official-app captures confirmed that multi-period framing was already correct and that zone ids in this schedule payload are little-endian 16-bit values. Navimower applies that confirmed wire format using its existing shared encoder rather than maintaining a separate inferred zone-id representation.

The separate report where the global Mowing schedule switch can appear Off while the app says On is not claimed fixed by beta5; its upstream root cause remains separate from the partitionPlan byte-layout bug.

0.4.2-beta5 is cumulative from stable 0.4.1 through beta4; earlier 0.4.2 betas do not need to be installed first.
''', encoding="utf-8")


def patch_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    if "## 0.4.2-beta5\n" in text:
        return
    section = '''## 0.4.2-beta5

Fifth beta in the cumulative 0.4.2 development line.

### Fixed

- Fixed zone-restricted schedule writes to encode every selected zone id as little-endian uint16 instead of one byte in the robot `partitionPlan` payload.
- Prevented selected zones from shifting later schedule bytes, which could make the mower drop zones, misread later periods or create a phantom `00:15-00:15` period that synchronized back to the Navimow app.
- Kept multi-period framing unchanged; app captures confirmed that multi-period all-zones schedules were already encoded correctly.

### Validation

- Added byte-level regression tests for disabled days, one/multiple all-zones periods, one/multiple selected zones and multiple zone-restricted periods.
- Schedule encoding now reuses the same `encode_partition_ids()` little-endian uint16 helper as immediate zone mowing.

### Upstream confirmation

- Synced the schedule zone-id wire format with the official-app-captured fix published by `ilguala/navimow_pro` v0.2.9 for issue #5. The separate schedule-master-switch state report remains outside this fix.

'''
    marker = "# Changelog\n\n"
    if marker not in text:
        raise RuntimeError("CHANGELOG header not found")
    path.write_text(text.replace(marker, marker + section, 1), encoding="utf-8")


def main() -> None:
    patch_client()
    write_tests()
    write_release_notes()
    patch_changelog()


if __name__ == "__main__":
    main()
