"""Regression contracts for Navimower 0.4.1-beta6 startup hotfix."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta6_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta6.md").read_text()
    assert "OPT_PASSIVE_DISCOVERY" in notes
    assert "startup" in notes.lower()


def test_beta6_startup_hotfix_remains_historical_only() -> None:
    """The beta6 fix stays documented; the retired option stays out of runtime."""
    source = (COMPONENT / "mqtt.py").read_text()
    tree = ast.parse(source)
    imported_from_const: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "const":
            imported_from_const.update(alias.name for alias in node.names)

    assert "DEFAULT_PASSIVE_DISCOVERY" not in imported_from_const
    assert "OPT_PASSIVE_DISCOVERY" not in imported_from_const
    assert "entry.options.get(OPT_PASSIVE_DISCOVERY" not in source


def test_passive_discovery_option_is_retired_from_current_constants() -> None:
    source = (COMPONENT / "const.py").read_text()
    assert 'OPT_PASSIVE_DISCOVERY: Final = "passive_discovery"' not in source
    assert "DEFAULT_PASSIVE_DISCOVERY" not in source
