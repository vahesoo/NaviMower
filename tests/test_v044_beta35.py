"""Dependency-free regressions for Navimower 0.4.4-beta35."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_mow_rejects_unknown_explicit_zone_ids_before_encoding() -> None:
    source = (COMPONENT / "services.py").read_text()

    requested = 'requested_zones = [int(z) for z in call.data.get("zones") or []]'
    validation = 'if known_zone_ids and zone_id not in known_zone_ids'
    error = '"Use the internal zone IDs reported by the Navimower map."'
    encoding = "partition_ids = encode_partition_ids(zones)"

    assert requested in source
    assert validation in source
    assert '"Unknown zone id(s): "' in source
    assert error in source
    assert source.index(validation) < source.index(encoding)


def test_mow_validation_preserves_h1_order_fallback() -> None:
    source = (COMPONENT / "services.py").read_text()

    assert "requested_ordered = bool(zones)" in source
    assert "supports_ordered_zone_mowing(" in source
    assert "ordered = requested_ordered and" in source
    assert "mow_setup(reset=call.data[\"reset\"], ordered=ordered)" in source


def test_beta35_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.4-beta35"

    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta35.md"
    assert notes.is_file()
    text = notes.read_text()
    assert text.startswith("title: Navimower 0.4.4-beta35\n")
    assert "Unknown zone" in text or "zone ID" in text
