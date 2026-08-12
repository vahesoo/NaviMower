"""Regional routing for the Navimow mobile-app private cloud.

The private account directory is regional.  Keep that routing separate from the
Smart Home OAuth/MQTT branch: official MQTT continues to use the broker returned
by the Smart Home API and is not selected from this table.
"""
from __future__ import annotations

import urllib.parse
from typing import Final

CONF_MOWER_HOST: Final = "mower_host"
DEFAULT_REGION: Final = "fra"

# Backend aliases observed from account responses / existing deployments.
_REGION_ALIASES: Final = {
    "eu": "fra",
    "sea": "sg",
    "ore": "us",
}

# Passport account-directory hosts.  The owning region is resolved with the
# signed GET /v3/region request before a password is sent anywhere.
PASSPORT_HOSTS: Final = {
    "fra": (
        "api-passport-fra.willand.com",
        "api-passport-fra.ninebot.com",
    ),
    "sg": (
        "api-passport-sg.willand.com",
        "api-passport-sg.ninebot.com",
    ),
    "us": (
        "api-passport-us.ninebot.com",
        "api-passport-ore.ninebot.com",
        "api-passport-ore.willand.com",
    ),
    "bj": (
        "api-passport-bj.willand.com",
        "api-passport-bj.ninebot.com",
    ),
}

# Private mower-cloud hosts.  A working US account reported region ``ore`` but
# its mower data was served by Frankfurt; keep that observed route first.
MOWER_HOSTS: Final = {
    "fra": (
        "navimow-fra.ninebot.com",
        "navimow-fra.willand.com",
    ),
    "sg": ("navimow-sg.willand.com",),
    "us": (
        "navimow-fra.ninebot.com",
        "navimow-ore.willand.com",
    ),
    "bj": (
        "navimow-bj.ninebot.com",
        "navimow-bj.willand.com",
    ),
}

REGIONS: Final = tuple(PASSPORT_HOSTS)

ALL_PASSPORT_HOSTS: Final = tuple(
    dict.fromkeys(
        [hosts[0] for hosts in PASSPORT_HOSTS.values() if hosts]
        + [host for hosts in PASSPORT_HOSTS.values() for host in hosts[1:]]
    )
)

_ALL_MOWER_HOSTS: Final = tuple(
    dict.fromkeys(host for hosts in MOWER_HOSTS.values() for host in hosts)
)


def canonical_region(region: str | None) -> str:
    """Normalize a backend region code while preserving unknown future codes."""
    code = str(region or "").strip().lower()
    if not code:
        return DEFAULT_REGION
    return _REGION_ALIASES.get(code, code)


def passport_hosts(region: str | None) -> tuple[str, ...]:
    """Return passport hosts for a region, or all known hosts when unknown."""
    return PASSPORT_HOSTS.get(canonical_region(region)) or ALL_PASSPORT_HOSTS


def mower_hosts(region: str | None) -> tuple[str, ...]:
    """Return regional mower hosts followed by bounded known fallbacks."""
    preferred = MOWER_HOSTS.get(canonical_region(region)) or ()
    fallback = tuple(host for host in _ALL_MOWER_HOSTS if host not in preferred)
    return preferred + fallback


def normalize_mower_host(host: str | None) -> str | None:
    """Return only a hostname from a stored hostname/base URL."""
    text = str(host or "").strip()
    if not text:
        return None
    parsed = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
    return parsed.hostname.lower() if parsed.hostname else None
