"""Pytest collection rules for version-scoped release contract suites."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"


def _version() -> str:
    return str(json.loads(MANIFEST.read_text()).get("version") or "")


def pytest_ignore_collect(collection_path, config) -> bool:  # noqa: ARG001
    """Keep historical prerelease contracts from becoming stable contracts.

    The beta test files document the exact temporary surface of the 0.4.1 beta
    investigation, including diagnostics/probe features intentionally removed
    before stable. Stable 0.4.1 has its own release contract suite instead.
    """
    version = _version()
    name = collection_path.name
    if name.startswith("test_v041_beta") and not version.startswith("0.4.1-beta"):
        return True
    if name == "test_v041_release.py" and version != "0.4.1":
        return True
    return False
