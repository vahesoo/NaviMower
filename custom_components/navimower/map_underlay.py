"""Map-underlay capabilities and Google Map Tiles backend helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import time
from typing import Any

from .account import normalize_private_account, private_account_entries
from .const import CONF_EMAIL, DOMAIN, OPT_GOOGLE_MAPS_API_KEY

GOOGLE_CREATE_SESSION_URL = "https://tile.googleapis.com/v1/createSession"
GOOGLE_2D_TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"
GOOGLE_VIEWPORT_URL = "https://tile.googleapis.com/tile/v1/viewport"
_GOOGLE_SESSION_REFRESH_MARGIN_SECONDS = 300
_GOOGLE_REQUEST_TIMEOUT_SECONDS = 20
_MANAGER_DATA_KEY = f"{DOMAIN}_map_underlay_manager"


class GoogleMapTilesError(RuntimeError):
    """Raised when the Google Map Tiles API cannot serve a request."""

    def __init__(self, kind: str, *, status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.status = status


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _api_key_marker(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def shared_google_maps_api_key(entries: Any, email: Any) -> str | None:
    """Return the account-scoped Google Map Tiles API key, if configured."""
    values = {
        str(value).strip()
        for entry in private_account_entries(entries, email)
        if (
            value := (getattr(entry, "options", {}) or {}).get(
                OPT_GOOGLE_MAPS_API_KEY
            )
        )
        and str(value).strip()
    }
    if not values:
        return None
    # Options flow mirrors a changed key across account peers. ``min`` keeps
    # legacy/conflicting storage deterministic until the next save heals it.
    return min(values)


def google_maps_api_key_for_entry(hass: Any, entry: Any) -> str | None:
    """Resolve one shared key for every mower entry using the same account."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return shared_google_maps_api_key(entries, (entry.data or {}).get(CONF_EMAIL))


def is_estonia_location(latitude: Any, longitude: Any) -> bool:
    """Return whether WGS84 coordinates fall inside a conservative EE bbox."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return 57.3 <= lat <= 60.0 and 21.5 <= lon <= 28.3


def coordinator_country_code(coordinator: Any) -> str | None:
    """Return a country capability without exposing exact mower coordinates."""
    data = getattr(coordinator, "data", None) or {}
    georeference = data.get("georeference")
    if not isinstance(georeference, dict):
        map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
        georeference = map_data.get("georeference")
    if not isinstance(georeference, dict):
        return None
    reference = georeference.get("reference")
    if not isinstance(reference, dict):
        return None
    if is_estonia_location(reference.get("latitude"), reference.get("longitude")):
        return "EE"
    return None


def google_session_locale(hass: Any, coordinator: Any | None = None) -> tuple[str, str]:
    """Return language/region values accepted by createSession."""
    config = getattr(hass, "config", None)
    language = str(getattr(config, "language", "") or "en").strip() or "en"
    region = str(getattr(config, "country", "") or "").strip().upper()
    if len(region) != 2:
        region = coordinator_country_code(coordinator) if coordinator is not None else None
    if not region:
        region = "US"
    return language, region


@dataclass(slots=True)
class _GoogleSessionState:
    key_marker: str | None = None
    session_token: str | None = None
    expiry_epoch: float | None = None
    status: str = "not_configured"
    last_success_utc: str | None = None
    last_error: str | None = None

    def active(self) -> bool:
        return bool(
            self.session_token
            and self.expiry_epoch is not None
            and self.expiry_epoch > time.time() + _GOOGLE_SESSION_REFRESH_MARGIN_SECONDS
        )


class GoogleMapTilesManager:
    """Keep short-lived Google session state without storing image tiles."""

    def __init__(self) -> None:
        self._states: dict[str, _GoogleSessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def account_key(entry: Any) -> str:
        email = normalize_private_account((entry.data or {}).get(CONF_EMAIL))
        return email or str(getattr(entry, "entry_id", "") or "unknown")

    def _state(self, account_key: str) -> _GoogleSessionState:
        return self._states.setdefault(account_key, _GoogleSessionState())

    def invalidate_account(self, account_key: str) -> None:
        """Forget only the Google session token; user configuration remains."""
        self._states.pop(account_key, None)
        self._locks.pop(account_key, None)

    def _record_error(
        self,
        account_key: str,
        kind: str,
        *,
        status: int | None = None,
    ) -> None:
        state = self._state(account_key)
        if status in {401, 403}:
            state.status = "authentication_error"
        else:
            state.status = kind
        state.last_error = f"http_{status}" if status is not None else kind

    async def _create_session(
        self,
        http_session: Any,
        account_key: str,
        api_key: str,
        *,
        language: str,
        region: str,
    ) -> _GoogleSessionState:
        state = self._state(account_key)
        marker = _api_key_marker(api_key)
        if state.key_marker != marker:
            state.key_marker = marker
            state.session_token = None
            state.expiry_epoch = None
            state.status = "configured"
            state.last_error = None

        payload = {
            "mapType": "satellite",
            "language": language,
            "region": region,
        }
        try:
            async with http_session.post(
                GOOGLE_CREATE_SESSION_URL,
                params={"key": api_key},
                json=payload,
                timeout=_GOOGLE_REQUEST_TIMEOUT_SECONDS,
            ) as response:
                if response.status != 200:
                    await response.read()
                    self._record_error(
                        account_key,
                        "session_error",
                        status=response.status,
                    )
                    raise GoogleMapTilesError(
                        "authentication_error"
                        if response.status in {401, 403}
                        else "session_error",
                        status=response.status,
                    )
                body = await response.json(content_type=None)
        except GoogleMapTilesError:
            raise
        except Exception as err:  # noqa: BLE001
            self._record_error(account_key, "connection_error")
            raise GoogleMapTilesError("connection_error") from err

        token = str((body or {}).get("session") or "").strip()
        try:
            expiry = float((body or {}).get("expiry"))
        except (TypeError, ValueError):
            expiry = 0.0
        if not token or expiry <= time.time():
            self._record_error(account_key, "invalid_session_response")
            raise GoogleMapTilesError("invalid_session_response")

        state.key_marker = marker
        state.session_token = token
        state.expiry_epoch = expiry
        state.status = "ok"
        state.last_success_utc = _utc_now()
        state.last_error = None
        return state

    async def async_ensure_session(
        self,
        http_session: Any,
        account_key: str,
        api_key: str,
        *,
        language: str,
        region: str,
    ) -> _GoogleSessionState:
        """Return a valid satellite session, refreshing it only when needed."""
        marker = _api_key_marker(api_key)
        state = self._state(account_key)
        if state.key_marker == marker and state.active():
            return state

        lock = self._locks.setdefault(account_key, asyncio.Lock())
        async with lock:
            state = self._state(account_key)
            if state.key_marker == marker and state.active():
                return state
            return await self._create_session(
                http_session,
                account_key,
                api_key,
                language=language,
                region=region,
            )

    async def async_validate_key(
        self,
        http_session: Any,
        account_key: str,
        api_key: str,
        *,
        language: str,
        region: str,
    ) -> None:
        """Validate a newly entered API key by creating a satellite session."""
        self.invalidate_account(account_key)
        await self.async_ensure_session(
            http_session,
            account_key,
            api_key,
            language=language,
            region=region,
        )

    async def async_tile(
        self,
        http_session: Any,
        account_key: str,
        api_key: str,
        *,
        z: int,
        x: int,
        y: int,
        language: str,
        region: str,
    ) -> tuple[bytes, dict[str, str]]:
        """Proxy one satellite tile without retaining or prefetching imagery."""
        state = await self.async_ensure_session(
            http_session,
            account_key,
            api_key,
            language=language,
            region=region,
        )
        assert state.session_token is not None
        url = GOOGLE_2D_TILE_URL.format(z=z, x=x, y=y)
        try:
            async with http_session.get(
                url,
                params={"session": state.session_token, "key": api_key},
                timeout=_GOOGLE_REQUEST_TIMEOUT_SECONDS,
            ) as response:
                body = await response.read()
                if response.status != 200:
                    self._record_error(
                        account_key,
                        "tile_error",
                        status=response.status,
                    )
                    raise GoogleMapTilesError(
                        "authentication_error"
                        if response.status in {401, 403}
                        else "tile_error",
                        status=response.status,
                    )
                headers = {
                    name: response.headers[name]
                    for name in (
                        "Content-Type",
                        "Cache-Control",
                        "ETag",
                        "Last-Modified",
                        "Expires",
                    )
                    if name in response.headers
                }
        except GoogleMapTilesError:
            raise
        except Exception as err:  # noqa: BLE001
            self._record_error(account_key, "connection_error")
            raise GoogleMapTilesError("connection_error") from err

        state.status = "ok"
        state.last_success_utc = _utc_now()
        state.last_error = None
        return body, headers

    async def async_viewport(
        self,
        http_session: Any,
        account_key: str,
        api_key: str,
        *,
        zoom: int,
        north: float,
        south: float,
        east: float,
        west: float,
        language: str,
        region: str,
    ) -> dict[str, Any]:
        """Proxy viewport metadata required for max zoom and attribution."""
        state = await self.async_ensure_session(
            http_session,
            account_key,
            api_key,
            language=language,
            region=region,
        )
        assert state.session_token is not None
        params = {
            "session": state.session_token,
            "key": api_key,
            "zoom": str(zoom),
            "north": str(north),
            "south": str(south),
            "east": str(east),
            "west": str(west),
        }
        try:
            async with http_session.get(
                GOOGLE_VIEWPORT_URL,
                params=params,
                timeout=_GOOGLE_REQUEST_TIMEOUT_SECONDS,
            ) as response:
                if response.status != 200:
                    await response.read()
                    self._record_error(
                        account_key,
                        "viewport_error",
                        status=response.status,
                    )
                    raise GoogleMapTilesError(
                        "authentication_error"
                        if response.status in {401, 403}
                        else "viewport_error",
                        status=response.status,
                    )
                body = await response.json(content_type=None)
        except GoogleMapTilesError:
            raise
        except Exception as err:  # noqa: BLE001
            self._record_error(account_key, "connection_error")
            raise GoogleMapTilesError("connection_error") from err

        state.status = "ok"
        state.last_success_utc = _utc_now()
        state.last_error = None
        return dict(body or {})

    def diagnostics(self, account_key: str, *, configured: bool) -> dict[str, Any]:
        """Return status only; never expose API keys or Google session tokens."""
        state = self._states.get(account_key)
        if not configured:
            return {
                "api": "not_configured",
                "configured": False,
                "status": "not_configured",
                "session_active": False,
                "session_expires_utc": None,
                "last_success": None,
                "last_error": None,
            }

        expiry_utc = None
        if state is not None and state.expiry_epoch is not None:
            try:
                expiry_utc = datetime.fromtimestamp(state.expiry_epoch, UTC).isoformat()
            except (ValueError, OSError, OverflowError):
                expiry_utc = None
        return {
            "api": "configured",
            "configured": True,
            "status": state.status if state is not None else "configured",
            "session_active": state.active() if state is not None else False,
            "session_expires_utc": expiry_utc,
            "last_success": state.last_success_utc if state is not None else None,
            "last_error": state.last_error if state is not None else None,
        }


def get_map_underlay_manager(hass: Any) -> GoogleMapTilesManager:
    """Return one process-wide manager shared by all Navimower entries."""
    manager = hass.data.get(_MANAGER_DATA_KEY)
    if isinstance(manager, GoogleMapTilesManager):
        return manager
    manager = GoogleMapTilesManager()
    hass.data[_MANAGER_DATA_KEY] = manager
    return manager


def map_underlay_metadata(coordinator: Any) -> dict[str, Any]:
    """Return backend-owned availability for the Map Card visual editor/runtime."""
    country_code = coordinator_country_code(coordinator)
    entry = coordinator.entry
    hass = coordinator.hass
    api_key = google_maps_api_key_for_entry(hass, entry)
    configured = bool(api_key)
    manager = get_map_underlay_manager(hass)
    google_status = manager.diagnostics(
        manager.account_key(entry),
        configured=configured,
    )
    entry_id = str(entry.entry_id)
    return {
        "location": {
            "country_code": country_code,
        },
        "map_underlays": {
            "estonia_orthophoto": {
                "available": country_code == "EE",
            },
            "estonia_hybrid": {
                "available": country_code == "EE",
            },
            "google_satellite": {
                "configured": configured,
                "available": configured
                and google_status.get("status") != "authentication_error",
                "status": google_status.get("status"),
                "tile_api_path_template": (
                    f"/api/navimower/underlay/google/{entry_id}/{{z}}/{{x}}/{{y}}"
                ),
                "viewport_api_path": (
                    f"/api/navimower/underlay/google/{entry_id}/viewport"
                ),
            },
        },
    }


def map_underlay_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Return privacy-safe underlay diagnostics for issue reports."""
    metadata = map_underlay_metadata(coordinator)
    entry = coordinator.entry
    hass = coordinator.hass
    configured = bool(google_maps_api_key_for_entry(hass, entry))
    manager = get_map_underlay_manager(hass)
    return {
        "location": metadata["location"],
        "estonia_orthophoto": metadata["map_underlays"]["estonia_orthophoto"],
        "estonia_hybrid": metadata["map_underlays"]["estonia_hybrid"],
        "google_satellite": manager.diagnostics(
            manager.account_key(entry),
            configured=configured,
        ),
    }
