"""Navimow private cloud API package (crypto + passport + client)."""
from __future__ import annotations

from typing import Any

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
        return {
            "endpoint": "/vehicle/vehicle/get-hint-error-compress",
            "inspection": inspect_hint_error_payload(raw, redactions=redactions),
        }


__all__ = [
    "NavimowAuthError",
    "NavimowCloudClient",
    "NavimowError",
    "PassportAuthError",
    "PassportError",
    "Tokens",
]
