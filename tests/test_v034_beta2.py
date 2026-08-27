"""Dependency-free regressions for Navimower v0.3.4-beta2 behavior retained today."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_v034_beta2_test"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_account_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules.setdefault(PACKAGE, package)
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    return _load_module(f"{PACKAGE}.account", COMPONENT / "account.py")


def _config_flow_source() -> str:
    """Return production flow plus current semantic setup extensions."""
    source = (COMPONENT / "config_flow.py").read_text()
    base = COMPONENT / "config_flow_base.py"
    if base.exists():
        source += "\n" + base.read_text()
    semantics = COMPONENT / "setup_flow_semantics.py"
    if semantics.exists():
        source += "\n" + semantics.read_text()
    return source


class _Entry:
    def __init__(self, email: str, device_id: str | None) -> None:
        self.data = {"email": email, "device_id": device_id}


def test_same_account_uses_one_deterministic_private_identity() -> None:
    account = _load_account_module()
    entries = [
        _Entry("Shared@Example.com", "device-b"),
        _Entry(" shared@example.com ", "device-a"),
        _Entry("other@example.com", "device-0"),
    ]
    assert account.shared_private_device_id(entries, "SHARED@example.com") == "device-a"
    assert account.shared_private_device_id([], "new@example.com", "fresh") == "fresh"
    assert account.shared_private_device_id([], "new@example.com") is None


def test_config_flow_reuses_account_identity_and_skips_redundant_single_picker() -> None:
    source = _config_flow_source()
    assert "shared_private_device_id(" in source
    assert "self._async_current_entries()" in source
    assert "return await self.async_step_select_vehicle()" in source
    assert 'result.get("step_id") == "select_vehicle"' in source
    assert "len(self._vehicles) == 1" in source
    assert "return await self._prepare_vehicle(self._vehicles[0])" in source


def test_options_reload_without_deprecated_update_listener_pairing() -> None:
    flow = _config_flow_source()
    init = (COMPONENT / "__init__.py").read_text()
    assert "OptionsFlowWithReload" in flow
    assert "class NavimowOptionsFlow(OptionsFlowWithReload)" in flow
    assert "add_update_listener" not in init
    assert "_async_update_listener" not in init
    assert "shared_private_device_id(" in init
    assert "async_entries(DOMAIN)" in init


def test_idle_mower_does_not_report_false_pose_disconnect() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text()
    assert 'd.get("mqtt_stream_expected") is False' in source
    assert "false\n            # disconnection" not in source.lower()
