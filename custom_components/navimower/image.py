"""Image platform exposing the latest Navimower map snapshot."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator_semantics import NavimowCoordinator
from .entity import NavimowEntity
from .map_snapshot import get_map_snapshot_manager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            NavimowMapSnapshotImage(coordinator),
            NavimowMapSnapshotDarkImage(coordinator),
        ]
    )


class NavimowMapSnapshotImage(NavimowEntity, ImageEntity):
    """Latest backend-rendered PNG map snapshot."""

    _attr_name = "Map snapshot"
    _attr_icon = "mdi:map"
    _attr_content_type = "image/png"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        NavimowEntity.__init__(self, coordinator, "map_snapshot")
        ImageEntity.__init__(self, coordinator.hass)
        self._manager = get_map_snapshot_manager(coordinator, "light")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._manager.add_listener(self._snapshot_updated))
        self._attr_image_last_updated = self._manager.last_rendered_at
        self._manager.consider_auto_refresh(self.data)

    @callback
    def _snapshot_updated(self) -> None:
        self._attr_image_last_updated = self._manager.last_rendered_at
        self.async_update_token()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._manager.consider_auto_refresh(self.data)
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        image = self._manager.image
        if image is None:
            image = await self._manager.async_refresh(
                reason="cold_request",
                force=True,
            )
        return image or None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        rendered = self._manager.last_rendered_at
        return {
            "last_rendered_at": rendered.isoformat() if rendered is not None else None,
            "render_age_seconds": (
                round(self._manager.render_age_seconds, 1)
                if self._manager.render_age_seconds is not None
                else None
            ),
            "render_reason": self._manager.render_reason,
            "render_duration_ms": self._manager.render_duration_ms,
            "failure_count": self._manager.failure_count,
            "last_error": self._manager.last_error,
            "refresh_interval_seconds": 60,
            "format": "png",
            "width": 1024,
            "height": 1024,
        }

    @property
    def available(self) -> bool:
        return self._manager.image is not None or super().available


class NavimowMapSnapshotDarkImage(NavimowMapSnapshotImage):
    """Dark-background snapshot variant for dashboards/notifications."""

    _attr_name = "Map snapshot dark"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        NavimowEntity.__init__(self, coordinator, "map_snapshot_dark")
        ImageEntity.__init__(self, coordinator.hass)
        self._manager = get_map_snapshot_manager(coordinator, "dark")
