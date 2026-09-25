"""Regression contracts for Navimower 0.4.5-beta41 auth/startup fixes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta41_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 41

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta41.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "401903",
        "refresh",
        "re-login",
        "last-good",
        "mower_sdk",
        "event loop",
        "async_get_device_by_identifier",
    ):
        assert phrase in notes


def test_beta41_recognizes_401903_as_private_auth_error() -> None:
    client = (COMPONENT / "api" / "client.py").read_text(encoding="utf-8")
    assert "401903,  # token expired" in client
    assert "def _is_auth_error_code(code: Any) -> bool:" in client
    assert "if retry_auth and auth and _is_auth_error_code(code):" in client
    assert "if _is_auth_error_code(code):" in client


def test_beta41_401903_refreshes_relogs_and_retries() -> None:
    code = textwrap.dedent(
        r"""
        import importlib.util
        from pathlib import Path
        import sys
        import types

        root = Path.cwd()

        def module(name):
            value = types.ModuleType(name)
            sys.modules[name] = value
            return value

        module("custom_components")
        navimower = module("custom_components.navimower")
        navimower.__path__ = [str(root / "custom_components" / "navimower")]
        api = module("custom_components.navimower.api")
        api.__path__ = [str(root / "custom_components" / "navimower" / "api")]

        const = module("custom_components.navimower.const")
        const.encode_partition_ids = lambda values: ""

        discovery = module("custom_components.navimower.discovery")
        discovery.structure_summary = lambda value: {
            "parsed_types": set(),
            "top_level_keys": set(),
            "key_paths": set(),
            "observed_type_values": set(),
        }

        crypto = module("custom_components.navimower.api.crypto")
        crypto.pack = lambda value: value
        crypto.decode_response = lambda value: value

        passport = module("custom_components.navimower.api.passport")

        class PassportError(Exception):
            def __init__(self, code, desc):
                super().__init__(f"{code}: {desc}")
                self.code = code
                self.desc = desc

        class Tokens:
            def __init__(
                self,
                access_token,
                refresh_token,
                uuid="",
                region="fra",
            ):
                self.access_token = access_token
                self.refresh_token = refresh_token
                self.uuid = uuid
                self.region = region

        passport.PassportError = PassportError
        passport.Tokens = Tokens
        passport.OS_VERSION = "13"
        passport.APP_VERSION = "test"

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.api.client",
            root / "custom_components" / "navimower" / "api" / "client.py",
        )
        client_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = client_module
        spec.loader.exec_module(client_module)

        def build(responses):
            client = client_module.NavimowCloudClient(
                "device",
                tokens=Tokens("old-access", "refresh", "uuid", "fra"),
                uid="uid",
            )
            response_iter = iter(responses)
            client._raw = lambda path, body: next(response_iter)
            calls = []
            client.refresh_session = (
                lambda: calls.append("refresh") or client.tokens
            )
            client.mower_login = lambda: calls.append("login") or "uid"
            return client, calls

        client, calls = build(
            [
                {"code": "401903", "desc": "token expired"},
                {"code": 1, "data": {"ok": True}},
            ]
        )
        assert client.call("/test", {}) == {"ok": True}
        assert calls == ["refresh", "login"]

        client, calls = build(
            [
                {"code": 401903, "desc": "token expired"},
                {"code": 401903, "desc": "token expired"},
            ]
        )
        try:
            client.call("/test", {})
        except client_module.NavimowAuthError as err:
            assert err.code == 401903
        else:
            raise AssertionError("401903 retry failure must become NavimowAuthError")
        assert calls == ["refresh", "login"]

        client = client_module.NavimowCloudClient(
            "device",
            tokens=Tokens("old-access", "refresh", "uuid", "fra"),
            uid="uid",
        )
        client._raw = lambda path, body: {
            "code": 401903,
            "desc": "token expired",
        }

        def failed_refresh():
            raise PassportError("90016", "refresh expired")

        client.refresh_session = failed_refresh
        try:
            client.call("/test", {})
        except client_module.NavimowAuthError as err:
            assert "passport refresh failed" in str(err)
        else:
            raise AssertionError("failed refresh must require private reauthentication")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_beta41_mower_sdk_imports_run_off_event_loop() -> None:
    mqtt = (COMPONENT / "mqtt.py").read_text(encoding="utf-8")
    config_flow = (COMPONENT / "config_flow_base.py").read_text(encoding="utf-8")

    assert "def _load_mower_sdk_types() -> tuple[Any, Any]:" in mqtt
    assert "from mower_sdk.api import MowerAPI" in mqtt
    assert "from mower_sdk.sdk import NavimowSDK" in mqtt
    assert (
        "MowerAPI, NavimowSDK = await self.hass.async_add_executor_job(\n"
        "            _load_mower_sdk_types\n"
        "        )"
    ) in mqtt

    start = mqtt[
        mqtt.index("    async def _async_start_locked")
        : mqtt.index("    async def async_quiesce")
    ]
    assert "from mower_sdk" not in start

    assert "def _load_mower_api_type() -> Any:" in config_flow
    assert (
        "MowerAPI = await self.hass.async_add_executor_job(_load_mower_api_type)"
        in config_flow
    )


def test_beta41_private_logs_identify_mower_and_registry_api_is_current() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    map_api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")

    assert "def _masked_serial(value: Any) -> str:" in coordinator
    assert "private endpoint %s for mower %s failed repeatedly" in coordinator
    assert "_masked_serial(self.sn)" in coordinator

    assert (
        "device_registry.async_get_device_by_identifier("
        in map_api
    )
    assert "(DOMAIN, sn)" in map_api
    assert "entry_id," in map_api
    assert "device_registry.async_get_device(identifiers=" not in map_api
