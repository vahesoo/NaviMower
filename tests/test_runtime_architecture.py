"""Architecture guard for production runtime naming and composition."""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SEMANTIC_RUNTIME_FILES = {
    "runtime.py",
    "state_semantics.py",
    "private_cloud_region.py",
    "capability_extensions.py",
    "capability_profile.py",
    "navigation_fallback.py",
    "notification_feed.py",
    "schedule_pause_semantics.py",
    "setup_flow_semantics.py",
    "zone_entity_cleanup.py",
}


def test_production_runtime_has_no_release_number_layers() -> None:
    python_files = list(COMPONENT.rglob("*.py"))
    forbidden_files = [
        path.name
        for path in python_files
        if re.fullmatch(r"(?:beta|v)\d+.*runtime\.py", path.name, re.IGNORECASE)
    ]
    assert forbidden_files == []

    offenders: list[str] = []
    for path in python_files:
        text = path.read_text(encoding="utf-8")
        if "install_beta" in text or re.search(r"_beta\d+_", text):
            offenders.append(str(path.relative_to(ROOT)))
        if re.search(r"from \.beta\d+", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_semantic_runtime_has_one_composition_point() -> None:
    present = {path.name for path in COMPONENT.glob("*.py")}
    assert SEMANTIC_RUNTIME_FILES <= present

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    expected = [
        "install_state_semantics()",
        "install_private_cloud_region()",
        "install_capability_extensions()",
        "install_capability_profile()",
        "install_navigation_fallback()",
        "install_notification_feed()",
        "install_schedule_pause_semantics()",
        "install_setup_flow_semantics()",
        "install_zone_entity_cleanup()",
    ]
    positions = [runtime.index(call) for call in expected]
    assert positions == sorted(positions)

    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    assert "from .runtime import install_runtime_extensions" in services
    assert services.count("install_runtime_extensions()") == 1
