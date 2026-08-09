"""Navimow private cloud API package (crypto + passport + client)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..error_catalog import build_error_catalog, resolve_error_code
from ..error_payload import inspect_hint_error_payload
from .client import (
    NavimowAuthError,
    NavimowCloudClient as _NavimowCloudClient,
    NavimowError,
)
from .passport import PassportAuthError, PassportError, Tokens


class NavimowCloudClient(_NavimowCloudClient):
    """Private-cloud client with safe hint/error payload inspection."""

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
