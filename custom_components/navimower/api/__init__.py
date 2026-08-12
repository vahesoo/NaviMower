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

# Fresh config flow -> config-entry setup happens in the same HA process. Keep
# the successfully probed host here until the normal coordinator persistence has
# written it to the config entry. This is only a hostname, never credentials.
_RESOLVED_MOWER_HOSTS: dict[str, str] = {}


class NavimowCloudClient(_NavimowCloudClient):
    """Private-cloud client with regional routing and safe error inspection."""

    def __init__(self, *args: Any, host: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._region = canonical_region(getattr(self, "_region", None))
        cached = _RESOLVED_MOWER_HOSTS.get(self.device_id)
        preferred = mower_hosts(self._region)
        selected = normalize_mower_host(host) or cached or (preferred[0] if preferred else None)
        if selected is None:  # defensive: mower_hosts currently always has fallbacks
            raise ValueError("No Navimow private-cloud mower host is available")
        self._host = selected
        self._host_source = (
            "explicit" if normalize_mower_host(host) else "process_cache" if cached else "region_default"
        )

    @property
    def host(self) -> str:
        """Current private mower-cloud hostname."""
        return self._host

    @property
    def host_source(self) -> str:
        """How the current private-cloud host was selected."""
        return self._host_source

    @property
    def mower_host_candidates(self) -> tuple[str, ...]:
        """Current host followed by bounded candidates for the account region."""
        return tuple(dict.fromkeys((self._host, *mower_hosts(self._region))))

    def set_host(self, host: str, *, source: str = "runtime") -> None:
        """Use a persisted/probed private-cloud host for this client instance."""
        normalized = normalize_mower_host(host)
        if normalized is None:
            return
        self._host = normalized
        self._host_source = source
        _RESOLVED_MOWER_HOSTS[self.device_id] = normalized

    def _select_region_default(self) -> None:
        """Move a fresh/default client to the first host for its resolved region."""
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
        """Resolve the private account region, then authenticate there."""
        self._tokens = _passport.login(email, password, region)
        self._region = canonical_region(self._tokens.region or region)
        self._select_region_default()
        return self._tokens

    def refresh_session(self) -> Tokens:
        """Refresh passport tokens against the account's owning region."""
        self._tokens = _passport.refresh(self._tokens, self._region)
        self._region = canonical_region(self._tokens.region or self._region)
        self._select_region_default()
        return self._tokens

    def session_state(self) -> dict[str, str]:
        """Persistable account/session state including the resolved mower host."""
        state = super().session_state()
        return {
            **state,
            "region": canonical_region(state.get("region") or self._region),
            "host": self._host,
            "host_source": self._host_source,
        }

    def _post(self, path: str, envelope: dict) -> dict:
        """POST one encrypted private-cloud request to this instance's host."""
        data = json.dumps(envelope, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"https://{self._host}{path}",
            data=data,
            headers=_client._HEADERS,  # reuse the proven mobile-app transport headers
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            try:
                return json.loads(err.read())
            except Exception as inner:  # pragma: no cover - defensive
                raise NavimowError(err.code, "HTTP error") from inner
        except urllib.error.URLError as err:
            raise NavimowError("network", str(err.reason)) from err

    def mower_login(self) -> str:
        """Register the app/device identity and retain the first usable mower host."""
        start_host = self._host
        start_source = self._host_source
        last_error: NavimowError | None = None
        for host in self.mower_host_candidates:
            self._host = host
            try:
                uid = super().mower_login()
            except NavimowError as err:
                last_error = err
                _LOGGER.debug(
                    "Navimow private mower host %s was not usable (code=%s)",
                    host,
                    getattr(err, "code", "unknown"),
                )
                continue
            source = start_source if host == start_host else "region_probe"
            self.set_host(host, source=source)
            return uid
        # Restore the original route so a caller can report stable diagnostics.
        self._host = start_host
        self._host_source = start_source
        if last_error is not None:
            raise last_error
        raise NavimowError("no_host", "no private mower-cloud host responded")

    def errors(self, sn: str, vehicle_type: int) -> dict[str, Any]:
        """Read and sanitize the private compressed hint/error catalog."""
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
        """Return the latest decoded vendor code lookup, if available."""
        catalog = getattr(self, "_navimow_error_catalog", None)
        return deepcopy(catalog) if isinstance(catalog, dict) else {}

    def resolve_error_code(self, code: Any) -> list[dict[str, Any]]:
        """Resolve one exact vendor code against the latest decoded catalog."""
        return resolve_error_code(getattr(self, "_navimow_error_catalog", None), code)


__all__ = [
    "NavimowAuthError",
    "NavimowCloudClient",
    "NavimowError",
    "PassportAuthError",
    "PassportError",
    "Tokens",
]
