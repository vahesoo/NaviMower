"""Official Navimow Smart Home OAuth implementation."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation

from .const import (
    CLIENT_ID,
    CLIENT_SECRET,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
)

_LOGGER = logging.getLogger(__name__)
_REGISTERED_KEY = f"{DOMAIN}_oauth_registered"


class NavimowOAuth2Implementation(LocalOAuth2Implementation):
    """Local OAuth implementation owned by the standalone Navimower entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        super().__init__(
            hass=hass,
            domain=domain,
            client_id=client_id,
            client_secret=client_secret,
            authorize_url=OAUTH2_AUTHORIZE,
            token_url=OAUTH2_TOKEN,
        )

    @property
    def name(self) -> str:
        return "Navimow Smart Home"

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Append Navimow's required Home Assistant channel marker."""
        return {"channel": "homeassistant"}

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh a token or request reauthentication if no refresh token exists."""
        if not token.get("refresh_token"):
            raise ConfigEntryAuthFailed(
                "Navimower OAuth access token expired without a refresh token"
            )
        return await super()._async_refresh_token(token)


def async_register_oauth_implementation(hass: HomeAssistant) -> None:
    """Register Navimower's local OAuth implementation once per HA process."""
    if hass.data.get(_REGISTERED_KEY):
        return
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        NavimowOAuth2Implementation(
            hass,
            DOMAIN,
            CLIENT_ID,
            CLIENT_SECRET,
        ),
    )
    hass.data[_REGISTERED_KEY] = True

