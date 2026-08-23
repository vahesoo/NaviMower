"""Regression guards for 0.4.3-beta28 map-edit timestamp semantics."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

semantics = (ROOT / "custom_components/navimower/coordinator_semantics.py").read_text()
init_source = (ROOT / "custom_components/navimower/__init__.py").read_text()
history = (ROOT / "custom_components/navimower/history.py").read_text()
manifest = json.loads(
    (ROOT / "custom_components/navimower/manifest.json").read_text()
)

# The runtime coordinator must strip the ambiguous vendor coverage timestamps
# before zone history sees them. Raw vendor fields stay in the base coordinator
# for cycle/completion evidence.
assert 'detail.pop("last_started_at", None)' in semantics
assert 'detail.pop("last_mowed_at", None)' in semantics
assert "super()._build_zone_details(" in semantics
assert "from .coordinator_semantics import NavimowCoordinator, state_store" in init_source

# Actual Last started / Last mowed timestamps continue to be written from real
# cutting observations in the persistent history manager.
assert '"last_mowed_at": observed_iso' in history
assert 'record["last_started_at"] = observed_iso' in history
assert "if cutting:" in history

assert manifest["version"] == "0.4.3-beta28"
print("beta28 map-edit timestamp regression tests passed")
