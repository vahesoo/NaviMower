"""Pytest collection rules for version-scoped release contract suites."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"


def _version() -> str:
    return str(json.loads(MANIFEST.read_text()).get("version") or "")


def pytest_ignore_collect(collection_path, config) -> bool:  # noqa: ARG001
    """Keep historical prerelease contracts from becoming later contracts."""
    version = _version()
    name = collection_path.name
    if name.startswith("test_v041_beta") and not version.startswith("0.4.1-beta"):
        return True
    if name == "test_v041_release.py" and version != "0.4.1":
        return True

    # Each 0.4.2 beta suite records that beta's temporary contract. This keeps
    # beta1's H5 discovery assertions from leaking into beta2 after the scanner
    # is intentionally removed, and gives stable 0.4.2 a clean release suite.
    if name.startswith("test_v042_beta") and name.endswith(".py"):
        beta_name = name.removeprefix("test_v042_").removesuffix(".py")
        if version != f"0.4.2-{beta_name}":
            return True

    # The 0.4.4 georeference investigation intentionally changed temporary
    # contracts between betas (for example beta17 disabled the cartographic
    # layer and beta19 restored it). Run only the contract matching the active
    # 0.4.4 prerelease; permanent behavior belongs in non-versioned tests.
    if name.startswith("test_v044_beta") and name.endswith(".py"):
        beta_name = name.removeprefix("test_v044_").removesuffix(".py")
        if version != f"0.4.4-{beta_name}":
            return True
    return False
