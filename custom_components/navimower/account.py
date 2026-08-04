"""Pure helpers for account-scoped private-cloud identity.

Navimow's private app cloud associates an account session with a stable app
``device_id``. Multiple Home Assistant config entries using the same account
must therefore present the same identity; otherwise logging in one mower entry
can invalidate another entry from that account.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .const import CONF_DEVICE_ID, CONF_EMAIL


def normalize_private_account(value: Any) -> str:
    """Return the case-insensitive private-cloud account key."""
    return str(value or "").strip().casefold()


def private_account_entries(entries: Iterable[Any], email: Any) -> list[Any]:
    """Return config entries that use the same private-cloud account."""
    wanted = normalize_private_account(email)
    if not wanted:
        return []
    return [
        entry
        for entry in entries
        if normalize_private_account((getattr(entry, "data", {}) or {}).get(CONF_EMAIL))
        == wanted
    ]


def shared_private_device_id(
    entries: Iterable[Any],
    email: Any,
    fallback: Any = None,
) -> str | None:
    """Return one deterministic device identity for every entry of an account.

    ``min`` lets entries created before multi-mower support converge on the same
    value regardless of setup order. A fresh fallback is used only when the
    account has no persisted identity yet.
    """
    identities = {
        str(value).strip()
        for entry in private_account_entries(entries, email)
        if (value := (getattr(entry, "data", {}) or {}).get(CONF_DEVICE_ID))
        and str(value).strip()
    }
    if identities:
        return min(identities)
    fallback_text = str(fallback or "").strip()
    return fallback_text or None
