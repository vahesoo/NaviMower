"""Segway/Ninebot passport authentication with regional account discovery.

The account directory is regional.  ``lookup_region`` asks the regional
passport servers which one owns the e-mail address using signed ``GET /v3/region``
before the password is offered to any server.

Synchronous (urllib) on purpose: Home Assistant runs this client in an executor.
Never logs tokens, passwords, e-mail addresses or full account identifiers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .regions import (
    ALL_PASSPORT_HOSTS,
    DEFAULT_REGION,
    canonical_region,
    passport_hosts,
)

_LOGGER = logging.getLogger(__name__)

# App identity used by the mobile-app passport protocol.
CLIENT_ID = "mowerbot_app_prod"
CLIENT_KEY = "830247f0-da96-5c21-8cf0-ca09299795f9"
APP_VERSION = "402000003"
OS_NAME = "Android"
OS_VERSION = "13"
OS_LANGUAGE = "en"
DEVICE = "ANDROID"

_RESULT_OK = "90000"
RESULT_TOKEN_EXPIRED = {"90015", "90016"}
RESULT_ACCOUNT_NOT_EXISTS = "00002"


class PassportError(Exception):
    """A passport call returned a non-success resultCode."""

    def __init__(self, code, desc: str) -> None:
        super().__init__(f"{code}: {desc}")
        self.code = str(code)
        self.desc = desc


class PassportAuthError(PassportError):
    """Bad credentials or expired session -- requires user re-auth."""


@dataclass
class Tokens:
    """Passport session tokens.

    ``region`` intentionally keeps the raw code returned by Passport. Host
    selection canonicalizes aliases separately (for example ``ore -> us``), but
    mower-cloud login must receive the vendor's raw code.
    """

    access_token: str
    refresh_token: str
    uuid: str = ""
    region: str = DEFAULT_REGION

    def redacted(self) -> dict:
        """A log-safe view (no token values)."""
        return {
            "access_token": bool(self.access_token),
            "refresh_token": bool(self.refresh_token),
            "uuid_present": bool(self.uuid),
            "region": self.region,
        }


def _sign(values: dict) -> str:
    return hashlib.sha256(
        "&".join(f"{key}={values[key]}" for key in sorted(values)).encode("utf-8")
    ).hexdigest()


def _signed_headers(path: str, req_params: dict) -> dict:
    timestamp = str(int(time.time() * 1000))
    sign_map = {
        "app_version": APP_VERSION,
        "clientKey": CLIENT_KEY,
        "os": OS_NAME,
        "os_language": OS_LANGUAGE,
        "os_version": OS_VERSION,
        "timestamp": timestamp,
        "url": path,
    }
    sign_map.update(req_params)
    return {
        "app_version": APP_VERSION,
        "clientId": CLIENT_ID,
        "os": OS_NAME,
        "os_language": OS_LANGUAGE,
        "os_version": OS_VERSION,
        "timestamp": timestamp,
        "sign": _sign(sign_map),
        "content-type": "application/json",
        "user-agent": "Segway_Mowerbot/4.02.0 (android)",
    }


def _request(
    host: str,
    path: str,
    params: dict,
    *,
    method: str,
    timeout: int = 20,
) -> dict:
    """Make one signed passport call against a specific regional host."""
    url = f"https://{host}{path}"
    body = None
    if method == "POST":
        body = json.dumps(params).encode()
    elif params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=_signed_headers(path, params),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        try:
            return json.loads(err.read())
        except Exception as inner:  # pragma: no cover - defensive
            raise PassportError(err.code, "HTTP error") from inner
    except urllib.error.URLError as err:
        raise PassportError("network", str(err.reason)) from err


def _extract_tokens(data: dict) -> Tokens:
    raw_region = data.get("region")
    return Tokens(
        access_token=str(data.get("access_token", "")),
        refresh_token=str(data.get("refresh_token", "")),
        uuid=str(data.get("uuid") or ""),
        region=str(raw_region) if raw_region else "",
    )


def lookup_region(email: str, hosts: tuple[str, ...] | None = None) -> str | None:
    """Return the raw region code that owns an account, without its password."""
    params = {"account": email, "device": DEVICE}
    last_transport_error: PassportError | None = None
    for host in hosts or ALL_PASSPORT_HOSTS:
        try:
            result = _request(host, "/v3/region", params, method="GET", timeout=15)
        except PassportError as err:
            last_transport_error = err
            _LOGGER.debug("Navimow region lookup host failed: %s", err.code)
            continue
        code = str(result.get("resultCode"))
        if code == _RESULT_OK:
            raw_region = str((result.get("data") or {}).get("region") or "")
            _LOGGER.debug(
                "Navimow private account region resolved: raw=%s routing=%s",
                raw_region or "unknown",
                canonical_region(raw_region),
            )
            return raw_region or None
        if code != RESULT_ACCOUNT_NOT_EXISTS:
            _LOGGER.debug("Navimow region lookup returned code %s", code)
    if last_transport_error is not None:
        # If even one regional directory could not be asked, absence is not
        # proven. Fail closed rather than sending the password to a guessed host.
        raise last_transport_error
    return None


def login(username: str, password: str, region: str | None = None) -> Tokens:
    """Log in on the account's owning region and keep its raw region code."""
    discovered = region is None
    if region is None:
        account_region = lookup_region(username)
        if account_region is None:
            raise PassportAuthError(
                RESULT_ACCOUNT_NOT_EXISTS,
                "account not found on any known regional passport service",
            )
    else:
        account_region = str(region)
    selected_region = canonical_region(account_region)
    params = {"username": username, "password": password, "device": DEVICE}
    last_error: PassportAuthError | None = None
    last_transport_error: PassportError | None = None

    for host in passport_hosts(selected_region):
        try:
            result = _request(host, "/v3/user/login", params, method="POST")
        except PassportError as err:
            last_transport_error = err
            continue
        code = str(result.get("resultCode"))
        if code == _RESULT_OK:
            tokens = _extract_tokens(result.get("data") or {})
            tokens.region = tokens.region or account_region
            _LOGGER.debug(
                "Navimow passport login ok: %s routing_region=%s",
                tokens.redacted(),
                canonical_region(tokens.region),
            )
            return tokens
        last_error = PassportAuthError(code, str(result.get("resultDesc", "")))
        # Wrong server is retryable. Wrong credentials or another business error
        # must not spray the password across unrelated regional servers.
        if code != RESULT_ACCOUNT_NOT_EXISTS:
            raise last_error

    # Future manual callers may pin a wrong region. Resolve once globally and
    # retry only if the directory proves the account belongs somewhere else.
    if not discovered:
        found = lookup_region(username)
        if found and canonical_region(found) != selected_region:
            return login(username, password, found)
    if last_error is not None:
        raise last_error
    if last_transport_error is not None:
        raise last_transport_error
    raise PassportError("network", "no regional passport host responded")


def refresh(tokens: Tokens, region: str | None = None) -> Tokens:
    """Refresh tokens while retaining Passport's raw account region code."""
    raw_region = tokens.region or str(region or DEFAULT_REGION)
    selected_region = canonical_region(region or raw_region or DEFAULT_REGION)
    params = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "device": DEVICE,
    }
    last_error: PassportError | None = None
    for host in passport_hosts(selected_region):
        try:
            result = _request(host, "/v3/user/refresh", params, method="POST")
        except PassportError as err:
            last_error = err
            continue
        code = str(result.get("resultCode"))
        if code != _RESULT_OK:
            raise PassportAuthError(code, str(result.get("resultDesc", "")))
        new = _extract_tokens(result.get("data") or {})
        if not new.uuid:
            new.uuid = tokens.uuid
        if not new.region:
            new.region = raw_region
        _LOGGER.debug(
            "Navimow passport refresh ok: %s routing_region=%s",
            new.redacted(),
            canonical_region(new.region),
        )
        return new
    if last_error is not None:
        raise last_error
    raise PassportError("network", "no regional passport host responded")
