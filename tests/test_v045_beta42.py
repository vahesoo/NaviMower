"""Regression contracts for Navimower 0.4.5-beta42 Map API fix."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta42_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 42

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta42.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "beta41",
        "Map API",
        "async_get_device_by_identifier",
        "config_entry_id",
        "401903",
        "#377",
    ):
        assert phrase in notes


def _frontend_metadata_function():
    """Compile the real helper without importing Home Assistant in unit tests."""
    path = COMPONENT / "map_api.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_frontend_metadata"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)

    entity_registry = SimpleNamespace(
        async_get_entity_id=lambda domain, platform, unique_id: (
            f"{domain}.{unique_id}"
        )
    )

    class DeviceRegistry:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, str], str]] = []

        def async_get_device_by_identifier(
            self,
            identifier: tuple[str, str],
            config_entry_id: str,
        ):
            self.calls.append((identifier, config_entry_id))
            return SimpleNamespace(id="device-123")

    device_registry = DeviceRegistry()

    namespace: dict[str, Any] = {
        "Any": Any,
        "DOMAIN": "navimower",
        "er": SimpleNamespace(async_get=lambda hass: entity_registry),
        "dr": SimpleNamespace(async_get=lambda hass: device_registry),
        "map_underlay_metadata": lambda coordinator: {
            "location": None,
            "map_underlays": {},
        },
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_frontend_metadata"], device_registry


def test_beta42_frontend_metadata_uses_current_ha_device_registry_signature() -> None:
    frontend_metadata, device_registry = _frontend_metadata_function()
    coordinator = SimpleNamespace(
        hass=object(),
        sn="H2-TEST-SERIAL",
        entry=SimpleNamespace(entry_id="entry-42"),
        data={"task_resume": {"available": False}},
    )

    payload = frontend_metadata(coordinator)

    assert device_registry.calls == [
        (("navimower", "H2-TEST-SERIAL"), "entry-42")
    ]
    assert payload["entry_id"] == "entry-42"
    assert payload["device_id"] == "device-123"
    assert payload["map_api_path"] == "/api/navimower/map/entry-42"
    assert payload["entities"]["mower"] == "lawn_mower.H2-TEST-SERIAL_mower"


def test_beta42_deprecated_device_lookup_remains_removed() -> None:
    map_api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    assert "async_get_device(identifiers=" not in map_api
    assert "async_get_device_by_identifier(" in map_api
    assert "(DOMAIN, sn)," in map_api
    assert "entry_id," in map_api
