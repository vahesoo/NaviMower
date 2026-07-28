"""OAuth2 implementation for Navimow integration."""
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN

_LOGGER = logging.getLogger(__name__)


class NavimowOAuth2Implementation(LocalOAuth2Implementation):
    """OAuth2 implementation for Navimow."""

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Initialize Navimow OAuth2 implementation."""
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
        """Return the name of the implementation."""
        return "Navimow"

    async def async_generate_authorize_url(self, *args, **kwargs) -> str:
        """Append channel=homeassistant without changing OAuth2 behavior."""
        url = await super().async_generate_authorize_url(*args, **kwargs)
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("channel", "homeassistant")
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def _async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """Navimow-specific token refresh.

        Navimow 的 OAuth token 有效期约为 1-2 天。到期后 HA 会尝试用
        grant_type=refresh_token 换新 token。若服务端不支持此 grant type
        或 refresh_token 本身已过期，会抛出异常。

        此处明确区分两种失败：
        - 确定性认证失败（401/403、no refresh_token）→ ConfigEntryAuthFailed
        - 瞬态失败（网络超时、DNS 等）→ 原样抛出，由上层决定是否重试
        """
        if "refresh_token" not in token:
            # Navimow 初始 token 不含 refresh_token，直接告知用户需重新认证
            raise ConfigEntryAuthFailed(
                "Navimow access token has expired and no refresh token is available. "
                "Please re-authenticate."
            )
        try:
            return await super()._async_refresh_token(token)
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            err_str = str(err).lower()
            # 服务端明确拒绝（401/403/invalid/expired）→ 需要重新认证
            if any(k in err_str for k in ("401", "403", "invalid", "expired", "unauthorized", "forbidden")):
                _LOGGER.warning(
                    "Navimow refresh token rejected by server (%s). Re-authentication required.",
                    err,
                )
                raise ConfigEntryAuthFailed(
                    f"Navimow refresh token has expired. Please re-authenticate: {err}"
                ) from err
            # 其他错误（网络等）原样抛出，不立即触发重新认证流程
            _LOGGER.warning("Navimow token refresh failed (possibly transient): %s", err)
            raise
