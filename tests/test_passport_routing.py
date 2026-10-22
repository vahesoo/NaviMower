"""Exercise regional Passport login/refresh without HA or live credentials."""
from __future__ import annotations

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "custom_components/navimower/api"
US_HOST = "api-passport-ore.willand.com"
EU_HOST = "api-passport-fra.willand.com"
ACCOUNT = "routing-test@example.invalid"
PASSWORD = "synthetic-password"


@pytest.fixture
def passport(monkeypatch):
    """Load the real client in an isolated package, bypassing HA startup."""
    package_name = "_passport_routing_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(API_DIR)]
    monkeypatch.setitem(sys.modules, package_name, package)
    for name in ("regions", "passport"):
        full_name = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(full_name, API_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full_name, module)
        spec.loader.exec_module(module)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Passport routing tests must not access the network")

    monkeypatch.setattr(module, "_request", unexpected_request)
    return module


def success(**data):
    return {"resultCode": "90000", "data": data}


def test_discovered_us_account_avoids_legacy_version_error(passport, monkeypatch):
    calls = []

    def request(host, path, params, *, method, **kwargs):
        calls.append((host, path, method))
        if path == "/v3/region":
            assert method == "GET"
            assert params == {"account": ACCOUNT, "device": "ANDROID"}
            if host == US_HOST:
                return success(region="ore")
            return {"resultCode": "00002"}
        assert path == "/v3/user/login"
        assert method == "POST"
        assert params == {"username": ACCOUNT, "password": PASSWORD, "device": "ANDROID"}
        if host != US_HOST:
            return {"resultCode": "90060", "resultDesc": "version too low"}
        return success(access_token="synthetic-access", refresh_token="synthetic-refresh")

    monkeypatch.setattr(passport, "_request", request)
    tokens = passport.login(ACCOUNT, PASSWORD)
    assert tokens.region == "ore"
    assert tokens.access_token == "synthetic-access"
    assert calls[-1] == (US_HOST, "/v3/user/login", "POST")
    assert [call for call in calls if call[2] == "POST"] == [calls[-1]]
    assert not any("ninebot.com" in call[0] for call in calls)


@pytest.mark.parametrize("region,host", [("us", US_HOST), ("ore", US_HOST), ("fra", EU_HOST)])
def test_pinned_login_prefers_owning_host(passport, monkeypatch, region, host):
    calls = []

    def request(actual_host, path, params, *, method, **kwargs):
        calls.append((actual_host, path, method))
        return success(access_token="synthetic-access", refresh_token="synthetic-refresh", region=region)

    monkeypatch.setattr(passport, "_request", request)
    assert passport.login(ACCOUNT, PASSWORD, region).region == region
    assert calls == [(host, "/v3/user/login", "POST")]


@pytest.mark.parametrize("region,host", [("us", US_HOST), ("ore", US_HOST), ("fra", EU_HOST)])
def test_restored_tokens_refresh_on_owning_host(passport, monkeypatch, region, host):
    original = passport.Tokens("old-access", "old-refresh", "synthetic-uuid", region)
    restored = passport.Tokens(**json.loads(json.dumps(asdict(original))))
    calls = []

    def request(actual_host, path, params, *, method, **kwargs):
        calls.append((actual_host, path, method))
        assert params == {
            "access_token": "old-access", "refresh_token": "old-refresh", "device": "ANDROID"
        }
        # Refresh responses need not repeat the account UUID or raw region.
        return success(access_token="new-access", refresh_token="new-refresh")

    monkeypatch.setattr(passport, "_request", request)
    refreshed = passport.refresh(restored)
    assert calls == [(host, "/v3/user/refresh", "POST")]
    assert refreshed == passport.Tokens("new-access", "new-refresh", "synthetic-uuid", region)


@pytest.mark.parametrize("operation", ["login", "refresh"])
@pytest.mark.parametrize("code", ["90060", "90016"])
def test_business_errors_are_terminal(passport, monkeypatch, operation, code):
    calls = []

    def request(host, path, params, *, method, **kwargs):
        calls.append(host)
        return {"resultCode": code, "resultDesc": "synthetic rejection"}

    monkeypatch.setattr(passport, "_request", request)
    with pytest.raises(passport.PassportAuthError) as error:
        if operation == "login":
            passport.login(ACCOUNT, PASSWORD, "ore")
        else:
            passport.refresh(passport.Tokens("old-access", "old-refresh", region="ore"))
    assert error.value.code == code
    assert calls == [US_HOST]


@pytest.mark.parametrize("operation", ["login", "refresh"])
def test_transport_failure_keeps_regional_fallback(passport, monkeypatch, operation):
    calls = []

    def request(host, path, params, *, method, **kwargs):
        calls.append(host)
        if len(calls) == 1:
            raise passport.PassportError("network", "synthetic outage")
        return success(access_token="new-access", refresh_token="new-refresh", region="ore")

    monkeypatch.setattr(passport, "_request", request)
    if operation == "login":
        result = passport.login(ACCOUNT, PASSWORD, "ore")
    else:
        result = passport.refresh(passport.Tokens("old-access", "old-refresh", region="ore"))
    assert result.region == "ore"
    assert calls == [US_HOST, "api-passport-us.ninebot.com"]


@pytest.mark.parametrize("unreachable_directory", [False, True])
def test_unresolved_account_never_sends_password(passport, monkeypatch, unreachable_directory):
    calls = []

    def request(host, path, params, *, method, **kwargs):
        calls.append(host)
        assert method == "GET"
        assert path == "/v3/region"
        assert "password" not in params
        if unreachable_directory and host == US_HOST:
            raise passport.PassportError("network", "synthetic outage")
        return {"resultCode": "00002"}

    monkeypatch.setattr(passport, "_request", request)
    with pytest.raises(passport.PassportError) as error:
        passport.login(ACCOUNT, PASSWORD)
    assert error.value.code == ("network" if unreachable_directory else "00002")
    assert len(calls) == len(set(calls))
    assert US_HOST in calls
