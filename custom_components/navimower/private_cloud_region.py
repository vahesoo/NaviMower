"""Persist and diagnose private-cloud regional routing.

Regional host selection lives in :mod:`navimower.api`; this small runtime bridge
connects that per-client state to Home Assistant's config entry without making
the large coordinator aware of transport-specific routing tables.
"""
from __future__ import annotations

from typing import Any

from . import coordinator as _coordinator
from .api.regions import CONF_MOWER_HOST, canonical_region


def private_cloud_region_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Return value-free private-cloud routing information."""
    client = getattr(coordinator, "client", None)
    if client is None:
        return {}
    candidates = getattr(client, "mower_host_candidates", ())
    return {
        "account_region": canonical_region(getattr(client, "region", None)),
        "mower_host": getattr(client, "host", None),
        "mower_host_source": getattr(client, "host_source", None),
        "mower_host_candidates": list(candidates or ()),
        "scope": "private_app_cloud_only",
        "smart_home_mqtt_routing": "api_provided_mqttHost_or_mqttUrl",
    }


def install_private_cloud_region() -> None:
    """Restore/persist the resolved private mower host once per interpreter."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_private_cloud_region_installed", False):
        return

    original_init = cls.__init__
    original_persist = cls._persist_session  # noqa: SLF001

    def init(self: Any, hass: Any, entry: Any) -> None:
        original_init(self, hass, entry)
        stored_host = entry.data.get(CONF_MOWER_HOST)
        if stored_host and hasattr(self.client, "set_host"):
            self.client.set_host(str(stored_host), source="persisted")

    def persist_session(self: Any) -> None:
        # Let the coordinator persist tokens/region first, then add the one
        # transport-specific field it intentionally knows nothing about.
        original_persist(self)
        host = getattr(self.client, "host", None)
        if not host or self.entry.data.get(CONF_MOWER_HOST) == host:
            return
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_MOWER_HOST: str(host)},
        )

    cls.__init__ = init
    cls._persist_session = persist_session  # noqa: SLF001
    cls._private_cloud_region_installed = True
