"""Regression contracts for Navimower 0.4.3-beta2 diagnostics hotfix."""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_release_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta2.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta2")
    assert "Download diagnostics" in notes
    assert "re.PatternError" in notes
    assert "literal backslashes" in notes


def test_beta2_all_discovery_regexes_compile() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    patterns: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr == "compile"
        ):
            continue
        assert node.args
        pattern = ast.literal_eval(node.args[0])
        assert isinstance(pattern, str)
        patterns.append(pattern)
        re.compile(pattern)
    assert len(patterns) >= 7


def test_beta2_patterns_target_real_javascript_syntax() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert r'<script\b' in source
    assert r'method\s*:\s*' in source
    assert r'skipEncryption\s*:\s*' in source
    assert r'\s*\(\s*' in source
    assert r're.sub(r"\s+"' in source
    assert r'<script\\b' not in source
    assert r'\\s*\\(' not in source
    assert "NavimowerDiagnostics/0.4.3-beta" in source


def test_beta2_diagnostics_keeps_read_only_maintenance_probe() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    discovery = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert "from .maintenance_h5_discovery import probe_maintenance_h5" in diagnostics
    assert "await hass.async_add_executor_job" in diagnostics
    assert '"maintenance_h5_discovery": maintenance_h5_discovery' in diagnostics
    assert '"mutation_calls_executed": False' in discovery
    assert "client.call(" not in discovery
