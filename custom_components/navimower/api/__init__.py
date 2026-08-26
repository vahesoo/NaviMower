"""Navimow private cloud API package (crypto + passport + regional client)."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..error_catalog import build_error_catalog, resolve_error_code
from ..error_payload import inspect_hint_error_payload
from . import client as _client
from . import passport as _passport
from .client import (
    NavimowAuthError,
    NavimowCloudClient as _NavimowCloudClient,
    NavimowError,
)
from .passport import PassportAuthError, PassportError, Tokens
from .regions import canonical_region, mower_hosts, normalize_mower_host

_LOGGER = logging.getLogger(__name__)

_RESOLVED_MOWER_HOSTS: dict[str, str] = {}


class NavimowCloudClient(_NavimowCloudClient):
    """Private-cloud client with regional routing and safe error inspection."""

    def __init__(self, *args: Any, host: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        initial_region = getattr(self, "_region", None)
        self._reported_region = str(initial_region or "")
        self._region = canonical_region(initial_region)
        self._mower_login_attempts: list[dict[str, Any]] = []
        self._shared_auth_list_attempts: list[dict[str, Any]] = []
        cached = _RESOLVED_MOWER_HOSTS.get(self.device_id)
        preferred = mower_hosts(self._region)
        selected = normalize_mower_host(host) or cached or (preferred[0] if preferred else None)
        if selected is None:
            raise ValueError("No Navimow private-cloud mower host is available")
        self._host = selected
        self._host_source = (
            "explicit" if normalize_mower_host(host) else "process_cache" if cached else "region_default"
        )

    @property
    def host(self) -> str:
        return self._host

    @property
    def host_source(self) -> str:
        return self._host_source

    @property
    def reported_region(self) -> str:
        return self._reported_region

    @property
    def mower_login_attempts(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._mower_login_attempts))

    @property
    def shared_auth_list_attempts(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._shared_auth_list_attempts))

    @property
    def mower_host_candidates(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self._host, *mower_hosts(self._region))))

    def set_host(self, host: str, *, source: str = "runtime") -> None:
        normalized = normalize_mower_host(host)
        if normalized is None:
            return
        self._host = normalized
        self._host_source = source
        _RESOLVED_MOWER_HOSTS[self.device_id] = normalized

    def _select_region_default(self) -> None:
        candidates = mower_hosts(self._region)
        if not candidates:
            return
        if self._host_source == "region_default":
            self.set_host(candidates[0], source="region_default")

    def authenticate(
        self,
        email: str,
        password: str,
        region: str | None = None,
    ) -> Tokens:
        self._tokens = _passport.login(email, password, region)
        reported = self._tokens.region or region
        self._reported_region = str(reported or "")
        self._region = canonical_region(reported)
        self._select_region_default()
        return self._tokens

    def refresh_session(self) -> Tokens:
        self._tokens = _passport.refresh(self._tokens, self._region)
        reported = self._tokens.region or self._reported_region or self._region
        self._reported_region = str(reported or "")
        self._region = canonical_region(reported)
        self._select_region_default()
        return self._tokens

    def session_state(self) -> dict[str, str]:
        state = super().session_state()
        return {
            **state,
            "region": canonical_region(state.get("region") or self._region),
            "host": self._host,
            "host_source": self._host_source,
        }

    def _post(self, path: str, envelope: dict) -> dict:
        data = json.dumps(envelope, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"https://{self._host}{path}",
            data=data,
            headers=_client._HEADERS,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            try:
                return json.loads(err.read())
            except Exception as inner:
                raise NavimowError(err.code, "HTTP error") from inner
        except urllib.error.URLError as err:
            raise NavimowError("network", str(err.reason)) from err

    @staticmethod
    def _login_response_row(host: str, variant: str, result: Any) -> dict[str, Any]:
        """Return a value-free structural summary of one mower-login response."""
        if isinstance(result, dict):
            code = result.get("code")
            desc = str(result.get("desc", ""))[:160]
            data = result.get("data")
            top_level_keys = sorted(str(key) for key in result.keys())[:32]
        else:
            code = None
            desc = str(result)[:160]
            data = None
            top_level_keys = []
        data_keys = sorted(str(key) for key in data.keys())[:32] if isinstance(data, dict) else []
        return {
            "host": host,
            "variant": variant,
            "code": str(code),
            "desc": desc,
            "data_type": type(data).__name__ if data is not None else "none",
            "data_keys": data_keys,
            "top_level_keys": top_level_keys,
        }

    def bootstrap_shared_auth_list(self) -> list[dict[str, Any]]:
        start_host = self._host
        start_source = self._host_source
        attempts: list[dict[str, Any]] = []
        self._shared_auth_list_attempts = []
        login_fields = {
            "uuid": self._tokens.uuid,
            "token": self._tokens.access_token,
            "refresh_token": self._tokens.refresh_token,
            "region": self._region,
        }
        for host in self.mower_host_candidates:
            self._host = host
            variants = (
                (
                    "login_style",
                    {**login_fields, **self._common_params(access_token="", uid="")},
                ),
                (
                    "login_style_plus_access_token",
                    {
                        **login_fields,
                        **self._common_params(access_token=self._tokens.access_token, uid=""),
                    },
                ),
            )
            for variant, body in variants:
                row: dict[str, Any] = {
                    "host": host,
                    "variant": variant,
                    "code": "unknown",
                    "desc": "",
                    "data_type": "none",
                    "item_count": 0,
                    "first_item_keys": [],
                }
                try:
                    result = self._raw("/vehicle/vehicle/auth-list", body)
                except NavimowError as err:
                    row["code"] = str(getattr(err, "code", "unknown"))
                    row["desc"] = str(getattr(err, "desc", "") or "")[:160]
                    attempts.append(row)
                    continue

                code = result.get("code") if isinstance(result, dict) else None
                desc = str(result.get("desc", "")) if isinstance(result, dict) else ""
                data = result.get("data") if isinstance(result, dict) else None
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict) and isinstance(data.get("list"), list):
                    items = data["list"]
                else:
                    items = []
                first = items[0] if items and isinstance(items[0], dict) else None
                row.update(
                    {
                        "code": str(code),
                        "desc": desc[:160],
                        "data_type": type(data).__name__ if data is not None else "none",
                        "item_count": len(items),
                        "first_item_keys": sorted(str(key) for key in first.keys())[:32] if first else [],
                    }
                )
                attempts.append(row)
                if code != _client.CODE_OK or not items:
                    continue
                uid = items[0].get("auth_uid") or items[0].get("authUid")
                if not uid:
                    continue
                self._uid = str(uid)
                self._shared_auth_list_attempts = attempts
                source = start_source if host == start_host else "shared_auth_list_probe"
                self.set_host(host, source=source)
                _LOGGER.info("Navimow shared account uid bootstrapped from auth-list on %s (%s)", host, variant)
                return items

        self._shared_auth_list_attempts = attempts
        self._host = start_host
        self._host_source = start_source
        return []

    def mower_login(self) -> str:
        """Probe mower-login hosts while retaining plain/signed response structure."""
        start_host = self._host
        start_source = self._host_source
        last_error: NavimowError | None = None
        attempts: list[dict[str, Any]] = []
        self._mower_login_attempts = []
        field4 = {
            "uuid": self._tokens.uuid,
            "token": self._tokens.access_token,
            "refresh_token": self._tokens.refresh_token,
            "region": self._region,
        }
        for host in self.mower_host_candidates:
            self._host = host
            try:
                plain_body = {**field4, **self._common_params(access_token="")}
                plain_result = self._raw("/user/user/login", plain_body)
                attempts.append(self._login_response_row(host, "plain", plain_result))
                uid = self._extract_uid(plain_result)
                if not uid:
                    signed_body = self._add_checkcode({**field4, **self._common_params(access_token="")})
                    signed_result = self._raw("/user/user/login", signed_body)
                    attempts.append(self._login_response_row(host, "signed", signed_result))
                    uid = self._extract_uid(signed_result)
                    final_result = signed_result
                else:
                    final_result = plain_result
                if not uid:
                    code = final_result.get("code") if isinstance(final_result, dict) else None
                    desc = str(final_result.get("desc", "")) if isinstance(final_result, dict) else str(final_result)
                    raise NavimowAuthError(code, f"mower login returned no uid: {desc}")
            except NavimowError as err:
                last_error = err
                if not attempts or attempts[-1].get("host") != host:
                    attempts.append(
                        {
                            "host": host,
                            "variant": "transport",
                            "code": str(getattr(err, "code", "unknown")),
                            "desc": str(getattr(err, "desc", "") or "")[:160],
                            "data_type": "none",
                            "data_keys": [],
                            "top_level_keys": [],
                        }
                    )
                continue

            self._uid = str(uid)
            self._mower_login_attempts = attempts
            source = start_source if host == start_host else "region_probe"
            self.set_host(host, source=source)
            return self._uid

        self._mower_login_attempts = attempts
        self._host = start_host
        self._host_source = start_source
        shared_items = self.bootstrap_shared_auth_list()
        if shared_items and self._uid:
            return self._uid
        if attempts:
            attempt_text = "; ".join(
                f"{row['host']}[{row['variant']}] (code={row['code']}, desc={row['desc'] or '-'}, "
                f"data_type={row['data_type']}, data_keys={','.join(row['data_keys']) or '-'}, "
                f"top_level_keys={','.join(row['top_level_keys']) or '-'})"
                for row in attempts
            )
            _LOGGER.warning(
                "Navimower private mower login variants failed: account_region=%s, reported_region=%s, attempts=%s",
                self._region,
                self._reported_region or "unknown",
                attempt_text,
            )
        if self._shared_auth_list_attempts:
            shared_attempt_text = "; ".join(
                f"{row['host']}[{row['variant']}] (code={row['code']}, desc={row['desc'] or '-'}, "
                f"data_type={row['data_type']}, item_count={row['item_count']}, "
                f"first_item_keys={','.join(row['first_item_keys']) or '-'})"
                for row in self._shared_auth_list_attempts
            )
            _LOGGER.warning("Navimower shared auth-list bootstrap did not yield uid: attempts=%s", shared_attempt_text)
        if last_error is not None:
            raise last_error
        raise NavimowError("no_host", "no private mower-cloud host responded")

    def errors(self, sn: str, vehicle_type: int) -> dict[str, Any]:
        raw = super().errors(sn, vehicle_type)
        redactions = (
            sn,
            self.device_id,
            self.uid,
            self.tokens.access_token,
            self.tokens.refresh_token,
            self.tokens.uuid,
        )
        inspection = inspect_hint_error_payload(raw, redactions=redactions)
        catalog = build_error_catalog(inspection)
        self._navimow_error_catalog = catalog
        return {
            "endpoint": "/vehicle/vehicle/get-hint-error-compress",
            "inspection": inspection,
            "catalog": deepcopy(catalog),
        }

    @property
    def error_catalog(self) -> dict[str, Any]:
        catalog = getattr(self, "_navimow_error_catalog", None)
        return deepcopy(catalog) if isinstance(catalog, dict) else {}

    def resolve_error_code(self, code: Any) -> list[dict[str, Any]]:
        return resolve_error_code(getattr(self, "_navimow_error_catalog", None), code)


__all__ = [
    "NavimowAuthError",
    "NavimowCloudClient",
    "NavimowError",
    "PassportAuthError",
    "PassportError",
    "Tokens",
]
