"""Lawn mower platform for Navimower."""
from __future__ import annotations

import logging

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
    MAP_EDIT_STATES,
    STATE_PAUSED,
    encode_partition_ids,
    mow_setup,
)
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity
from .model_support import supports_ordered_zone_mowing
from .resume import async_resume_task

_LOGGER = logging.getLogger(__name__)

_ACTIVITY_MAP = {
    ACTIVITY_DOCKED: LawnMowerActivity.DOCKED,
    ACTIVITY_MOWING: LawnMowerActivity.MOWING,
    ACTIVITY_PAUSED: LawnMowerActivity.PAUSED,
    ACTIVITY_RETURNING: LawnMowerActivity.RETURNING,
    ACTIVITY_ERROR: LawnMowerActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NavimowLawnMower(coordinator)])


class NavimowLawnMower(NavimowEntity, LawnMowerEntity):
    """The mower as a HA lawn_mower entity."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, "mower")
        self._last_valid_activity = (
            LawnMowerActivity.DOCKED
            if (coordinator.data or {}).get("docked") is True
            else LawnMowerActivity.PAUSED
        )

    @property
    def activity(self) -> LawnMowerActivity:
        if str(self.data.get("state_code") or "") in MAP_EDIT_STATES:
            self._last_valid_activity = LawnMowerActivity.PAUSED
            return LawnMowerActivity.PAUSED
        mapped = _ACTIVITY_MAP.get(self.data.get("activity"))
        if mapped is not None:
            self._last_valid_activity = mapped
            return mapped
        if self.data.get("docked") is True:
            self._last_valid_activity = LawnMowerActivity.DOCKED
            return LawnMowerActivity.DOCKED
        return self._last_valid_activity

    async def async_start_mowing(self) -> None:
        client = self.coordinator.client
        sn = self._sn
        if self.data.get("state_code") == STATE_PAUSED:
            await async_resume_task(
                self.coordinator,
                source="lawn_mower.start_mowing_paused",
            )
            return

        zones = self.data.get("zones") or []
        if not zones:
            raise HomeAssistantError(
                "No mowing zones are known for this mower. Configure them in the "
                "integration Options (id:name,...) so a start command can be sent."
            )
        available_ids = [z["id"] for z in zones]
        sel = [
            zone_id
            for zone_id in (self.coordinator.selected_zone_ids or [])
            if zone_id in available_ids
        ]
        region_ids = sel or available_ids
        partition_ids = encode_partition_ids(region_ids)
        requested_ordered = bool(sel)
        ordered = requested_ordered and supports_ordered_zone_mowing(
            self.data.get("model") or self.coordinator.entry.data.get("model"),
            self.coordinator.vehicle_type,
        )
        partition_setup = mow_setup(reset=True, ordered=ordered)
        self.coordinator.begin_mow_command_trace(
            source="lawn_mower.start_mowing",
            requested_zone_ids=sel,
            resolved_zone_ids=region_ids,
            reset=True,
            ordered=ordered,
            partition_ids_hex=partition_ids,
            partition_setup=partition_setup,
        )
        self.coordinator.set_pending_activity(ACTIVITY_MOWING)
        self.coordinator.set_command_target(
            sel if requested_ordered else [], source="lawn_mower.start_mowing"
        )
        try:
            result = await self.coordinator.async_send(
                client.mow_zones,
                sn,
                partition_ids,
                partition_setup,
            )
            self.coordinator.record_mow_command_result(result)
            self.coordinator.start_new_mowing_cycle(
                region_ids, source="lawn_mower.start_mowing_reset"
            )
        except Exception as err:
            self.coordinator.record_mow_command_error(err)
            self.coordinator.clear_pending_activity()
            if requested_ordered:
                self.coordinator.clear_command_target()
            raise

    async def async_pause(self) -> None:
        self.coordinator.set_pending_activity(ACTIVITY_PAUSED)
        try:
            await self.coordinator.async_send(self.coordinator.client.pause, self._sn)
        except Exception:
            self.coordinator.clear_pending_activity()
            raise

    async def async_dock(self) -> None:
        self.coordinator.clear_command_target()
        self.coordinator.set_pending_activity(ACTIVITY_RETURNING)
        center = getattr(self.coordinator, "notification_center", None)
        if center is not None:
            center.note_dock_command("lawn_mower.dock")
        try:
            await self.coordinator.async_send(self.coordinator.client.dock, self._sn)
        except Exception:
            if center is not None:
                center.clear_dock_command()
            self.coordinator.clear_pending_activity()
            raise

    @property
    def extra_state_attributes(self) -> dict:
        data = self.data
        state_code = str(data.get("state_code") or "")
        return {
            "state_code": data.get("state_code"),
            "state": data.get("state"),
            "model": data.get("model") or self.coordinator.entry.data.get("model"),
            "vehicle_type": self.coordinator.vehicle_type,
            "map_editing": state_code in MAP_EDIT_STATES,
            "current_zone": data.get("current_zone"),
            "current_physical_zone": data.get("current_physical_zone"),
            "target_zone": data.get("target_zone"),
            "target_zone_source": data.get("target_zone_source"),
            "command_target_active": data.get("command_target_active"),
            "current_channel": data.get("current_channel"),
            "pose_source": data.get("pose_source"),
            "mqtt_pose_age": data.get("mqtt_pose_age"),
            "map_api_path": f"/api/navimower/map/{self.coordinator.entry.entry_id}",
        }
