"""Stable-release contracts for Navimower 0.4.5."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"
MQTT = ROOT / "custom_components" / "navimower" / "mqtt.py"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
NOTES = ROOT / ".github" / "release-notes" / "0.4.5.md"


def test_v045_stable_metadata() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5"
    notes = NOTES.read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.5\n")
    assert "Persistent current-cycle mowing state" in notes
    assert "Privacy-safe public diagnostics" in notes


def test_v045_stable_docs_pairing() -> None:
    readme = README.read_text(encoding="utf-8")
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "Curated privacy-safe Download diagnostics" in readme
    assert "Target zone" in readme and "Planned zones" in readme
    assert "## 0.4.5 - 2026-09-26" in changelog
    assert "Navimower Map Card `0.3.7`" in changelog


def test_v045_stable_masks_both_mqtt_identifiers() -> None:
    source = MQTT.read_text(encoding="utf-8")
    assert "self._masked_serial(self.coordinator.sn)" in source
    assert 'self._masked_serial(str(official_id)) if official_id else "unknown"' in source
    assert 'official_id or "unknown"' not in source
