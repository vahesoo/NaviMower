"""Permanent privacy/interoperability regression contracts for Navimower."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    text = (COMPONENT / name).read_text(encoding="utf-8")
    if name.endswith(".py"):
        ast.parse(text)
    return text


def test_production_tree_does_not_ship_retired_development_capture_surfaces() -> None:
    for name in (
        "private_api_probe.py",
        "raw_export.py",
        "raw_mqtt_semantics.py",
    ):
        assert not (COMPONENT / name).exists(), name

    init = _source("__init__.py")
    runtime = _source("runtime.py")
    services = _source("services.py")
    services_yaml = _source("services.yaml")

    for token in (
        "async_setup_private_api_probe",
        "SERVICE_PROBE_PRIVATE_API",
        "probe_private_api",
        "SERVICE_EXPORT_RAW_DATA",
        "async_export_raw_data",
        "export_raw_data",
        "install_raw_mqtt_semantics",
    ):
        assert token not in init
        assert token not in runtime
        assert token not in services
        assert token not in services_yaml


def test_retired_passive_discovery_is_not_user_configurable_or_subscribed() -> None:
    const = _source("const.py")
    flow = _source("config_flow.py")
    mqtt = _source("mqtt.py")

    assert "OPT_PASSIVE_DISCOVERY" not in const
    assert "DEFAULT_PASSIVE_DISCOVERY" not in const
    assert "OPT_PASSIVE_DISCOVERY" not in flow
    assert "passive_discovery" not in flow
    assert "mqtt_discovery_topic" not in mqtt
    assert "diagnostic_discovery" not in mqtt
    assert "payload_base64" not in mqtt
    assert "location_topic(device_id)" in mqtt

    # Current production diagnostics keep only a bounded schema inventory for
    # messages already delivered by the normal current-device MQTT connection.
    assert "_record_message_inventory" in mqtt
    assert "structure_summary(parsed)" in mqtt
    assert "safe_topic = safe_topic.replace(candidate, \"<device>\")" in mqtt


def test_home_assistant_download_is_the_only_shipped_support_export() -> None:
    diagnostics = _source("diagnostics.py")
    sanitizer = _source("diagnostics_sanitize.py")
    privacy_doc = (ROOT / "docs" / "DIAGNOSTICS_PRIVACY.md").read_text(encoding="utf-8")

    assert '"diagnostics_source": "home_assistant_download"' in diagnostics
    assert '"cached_only": True' in diagnostics
    assert "from .diagnostics_sanitize import REDACTION_VERSION, sanitize" in diagnostics
    assert "return sanitize(" in diagnostics
    assert "mqtt_discovery" not in diagnostics
    assert "raw-data export" not in diagnostics.lower()
    assert "unredacted development export" not in diagnostics.lower()

    assert "def sanitize" in sanitizer
    assert "REDACTION_VERSION" in sanitizer
    assert "Home Assistant **Download diagnostics** is the supported public troubleshooting path." in privacy_doc
    assert "does not expose raw-data export or arbitrary endpoint-probe actions" in privacy_doc


def test_interoperability_boundary_keeps_secrets_backend_owned() -> None:
    map_api = _source("map_api.py")
    underlay = _source("map_underlay.py")
    docs = (ROOT / "docs" / "MAP_GEOREFERENCE_AND_UNDERLAYS.md").read_text(
        encoding="utf-8"
    )

    # Frontend metadata exposes capability/status and authenticated HA paths,
    # while the Google key lookup remains backend-only.
    assert '"map_underlays": underlay["map_underlays"]' in map_api
    assert '"configured": configured' in underlay
    assert '"available": configured' in underlay
    assert '"tile_api_path_template"' in underlay
    assert "google_maps_api_key_for_entry" in underlay
    assert "The API key and Google session token remain on the Home Assistant backend" in docs
