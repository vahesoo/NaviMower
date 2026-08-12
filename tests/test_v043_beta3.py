"""Regression contracts for Navimower 0.4.3-beta3 and later 0.4.3 betas."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.4.3")
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta3.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta3")
    assert "48 public JavaScript assets" in notes
    assert "49-byte response" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.4.3-beta3" in changelog


def test_beta3_discovery_has_bounded_lazy_chunk_crawler() -> None:
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
        pattern = ast.literal_eval(node.args[0])
        assert isinstance(pattern, str)
        re.compile(pattern)
        patterns.append(pattern)
    assert len(patterns) >= 7
    asset_match = re.search(r"MAX_ASSETS\s*=\s*(\d+)", source)
    candidate_match = re.search(r"MAX_JS_CANDIDATES\s*=\s*(\d+)", source)
    assert asset_match is not None and int(asset_match.group(1)) > 0
    assert candidate_match is not None and int(candidate_match.group(1)) > 0
    assert "bounded_lazy_chunk" in source
    assert '"unfetched_candidates": unfetched' in source
    assert "heapq.heappush" in source


def test_beta3_collects_small_json_and_global_request_structure() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "SMALL_JSON_MAX = 8192",
        'row["json_body"] = json_body',
        "ENDPOINT_RE = re.compile",
        '"endpoint_paths": endpoint_paths',
        "bridge_candidates",
        "knifeDurationSet",
        "chassisDurationSet",
        "knifeDefaultDuration",
        "chassisDefaultDuration",
        "usedTime",
        "setTime",
    ):
        assert phrase in source
    assert "ENDPOINT_RE.findall(text)" in source
    assert "_structure(text)" in source


def test_beta3_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"public_unauthenticated_h5_only": True' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta" in diagnostics
