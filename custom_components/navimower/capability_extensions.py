"""Capability and route-history extensions for modern Navimow models.

The i208 AWD diagnostic proves a second modern capability family whose settings
are mostly field-identical to existing H215 experiments but were hidden by
model-name gates. This module deliberately exposes the observed user-facing fields
for field testing, keeps both observed light controls available on i2 AWD, uses
vendor battery limits when supplied, and adds a global cutting-height number
when the mower reports a real height plus a supported-height list.

Names that are documented in the i2 manual use Navimow terminology. Settings
whose only evidence is the vendor field name remain intentionally literal and
experimental until live before/after captures confirm their exact app labels.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from math import gcd
from typing import Any

from homeassistant.components.number import NumberMode
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfLength, UnitOfTime

from . import coordinator as _coordinator
from . import history as _history
from .route_dedupe import append_or_coalesce, compact_route_points

_I2_AWD_MODELS = ("i205 AWD", "i206 AWD", "i208 AWD", "i210 AWD")
_EXTENDED_TERRAIN_MODELS = ("H215", *_I2_AWD_MODELS)


def _merge_models(existing: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *additions)))


def _raw(data: dict[str, Any], key: str) -> Any:
    raw = data.get("raw") or {}
    set_list = raw.get("set_list") or {}
    return set_list.get(key) if isinstance(set_list, dict) else None


def _device_info(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("raw") or {}
    value = raw.get("device_info") or {}
    return value if isinstance(value, dict) else {}


def _battery_config(data: dict[str, Any]) -> dict[str, Any]:
    device = _device_info(data)
    nonstandard = device.get("nonstandardVehicleConfig") or {}
    config = nonstandard.get("batteryConfig") or {}
    return config if isinstance(config, dict) else {}


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _height_options(data: dict[str, Any]) -> list[int]:
    values = _device_info(data).get("mowingHeightList") or []
    out: list[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        if 10 <= parsed <= 150 and parsed not in out:
            out.append(parsed)
    return sorted(out)


def _height_step(values: list[int]) -> int:
    differences = [right - left for left, right in zip(values, values[1:]) if right > left]
    if not differences:
        return 1
    step = differences[0]
    for value in differences[1:]:
        step = gcd(step, value)
    return max(1, step)


def _install_switch_capabilities() -> None:
    from . import switch as platform

    rewritten = []
    for description in platform.SWITCHES:
        if description.key in {"terrain_adapt", "edge_sense"}:
            description = replace(
                description,
                models=_merge_models(description.models, _I2_AWD_MODELS),
            )
        elif description.key == "obstacle_avoidance":
            description = replace(
                description,
                name="Channel Obstacle Avoidance",
                translation_key=None,
            )
        elif description.key == "animal_protection":
            description = replace(
                description,
                name="Animal friendly",
                translation_key=None,
            )
        elif description.key == "traction_control":
            description = replace(
                description,
                name="Traction Control System (TCS)",
                translation_key=None,
            )
        elif description.key == "efls":
            description = replace(
                description,
                name="Camera-assisted positioning",
                translation_key=None,
            )
        rewritten.append(description)

    existing = {description.key for description in rewritten}
    extras = (
        platform.NavimowSwitchDescription(
            key="eco_mode",
            name="Eco mode",
            icon="mdi:leaf-circle-outline",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="powerSaveShutdownSwitch",
            write_key="powerSaveShutdownSwitch",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="narrow_zone_adapt",
            name="Narrow zone adapt",
            icon="mdi:arrow-collapse-horizontal",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="narrowZoneAdaptSwitch",
            write_key="narrowZoneAdaptSwitch",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="advanced_slope_mode",
            name="Advanced slope mode",
            icon="mdi:slope-uphill",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="advancedSlopeMode",
            write_key="advancedSlopeMode",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="grass_pattern_enhancement",
            name="Grass pattern enhancement",
            icon="mdi:grass",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="grassPatternEnhancement",
            write_key="grassPatternEnhancement",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="progress_retention",
            name="Progress retention",
            icon="mdi:progress-clock",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="progressRetentionSwitch",
            write_key="progressRetentionSwitch",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="headlight",
            name="Headlight",
            icon="mdi:car-light-high",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="headlightSwitch",
            write_key="headlightSwitch",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
        platform.NavimowSwitchDescription(
            key="night_animal_protection",
            name="Night animal protection",
            icon="mdi:weather-night-partly-cloudy",
            entity_category=EntityCategory.CONFIG,
            value_fn=lambda s: None,
            raw_read_key="nightAnimalProtection",
            write_key="nightAnimalProtection",
            iot=True,
            numeric=True,
            models=_I2_AWD_MODELS,
        ),
    )
    platform.SWITCHES = tuple(rewritten) + tuple(
        description for description in extras if description.key not in existing
    )


def _install_select_capabilities() -> None:
    from . import select as platform

    rewritten = []
    for description in platform.SETTING_SELECTS:
        if description.key == "night_light_level":
            description = replace(
                description,
                name="Light brightness",
                models=_merge_models(description.models, _I2_AWD_MODELS),
            )
        elif description.key == "light_brightness":
            description = replace(
                description,
                name="Light brightness (alternate)",
                models=_merge_models(description.models, _I2_AWD_MODELS),
            )
        elif description.key == "edge_sense_mode":
            description = replace(
                description,
                models=_merge_models(description.models, _I2_AWD_MODELS),
            )
        rewritten.append(description)

    existing = {description.key for description in rewritten}
    positioning = platform.NavimowSelectDescription(
        key="positioning_mode",
        name="Positioning mode",
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="rtkDataSource",
        write_key="rtkDataSource",
        # i2 AWD is antenna-free Network RTK by default; the captured mower
        # reports rtkDataSource=1, leaving 0 as the Local RTK alternative.
        value_map={"Network RTK": 1, "Local RTK": 0},
        robot_numeric=True,
        models=_I2_AWD_MODELS,
    )
    platform.SETTING_SELECTS = tuple(rewritten) + (
        () if positioning.key in existing else (positioning,)
    )


def _install_number_capabilities() -> None:
    from . import number as platform

    rewritten = []
    for description in platform.NUMBERS:
        if description.key == "return_battery_level":
            description = replace(
                description,
                native_min_value=5,
                native_max_value=20,
                native_step=5,
            )
        elif description.key == "charging_limit":
            description = replace(
                description,
                native_min_value=70,
                native_max_value=100,
                native_step=5,
            )
        rewritten.append(description)

    existing = {description.key for description in rewritten}
    extras = (
        platform.NavimowNumberDescription(
            key="cutting_height",
            name="Global cutting height",
            icon="mdi:arrow-expand-vertical",
            entity_category=EntityCategory.CONFIG,
            native_unit_of_measurement=UnitOfLength.MILLIMETERS,
            native_min_value=20,
            native_max_value=70,
            native_step=5,
            mode=NumberMode.SLIDER,
            value_fn=lambda s: None,
            raw_read_key="height",
            write_key="height",
            robot_hex=True,
        ),
        platform.NavimowNumberDescription(
            key="progress_retention_duration",
            name="Progress retention duration",
            icon="mdi:progress-clock",
            entity_category=EntityCategory.CONFIG,
            native_unit_of_measurement=UnitOfTime.HOURS,
            native_min_value=1,
            native_max_value=168,
            native_step=1,
            mode=NumberMode.SLIDER,
            value_fn=lambda s: None,
            raw_read_key="progressRetentionDuration",
            write_key="progressRetentionDuration",
            robot_hex=True,
        ),
        platform.NavimowNumberDescription(
            key="mowing_cycle_interval",
            name="Mowing cycle interval",
            icon="mdi:calendar-sync",
            entity_category=EntityCategory.CONFIG,
            native_unit_of_measurement=UnitOfTime.DAYS,
            native_min_value=1,
            native_max_value=7,
            native_step=1,
            mode=NumberMode.SLIDER,
            value_fn=lambda s: None,
            raw_read_key="cycleMowingTimeSetting",
            write_key="cycleMowingTimeSetting",
            robot_hex=True,
        ),
    )
    platform.NUMBERS = tuple(rewritten) + tuple(
        description for description in extras if description.key not in existing
    )

    original_wire_value = platform._wire_value

    def wire_value(description: Any, data: dict[str, Any]) -> int | None:
        if description.key == "cutting_height" and not _height_options(data):
            return None
        return original_wire_value(description, data)

    platform._wire_value = wire_value

    number_cls = platform.NavimowNumber
    original_init = number_cls.__init__

    def init(self: Any, coordinator: Any, description: Any) -> None:
        original_init(self, coordinator, description)
        data = coordinator.data or {}
        if description.key in {"return_battery_level", "charging_limit"}:
            config = _battery_config(data)
            if description.key == "return_battery_level":
                self._attr_native_min_value = _as_float(
                    config.get("returnBatteryLevelMin"), description.native_min_value
                )
                self._attr_native_max_value = _as_float(
                    config.get("returnBatteryLevelMax"), description.native_max_value
                )
            else:
                self._attr_native_min_value = _as_float(
                    config.get("chargingLimitMin"), description.native_min_value
                )
                self._attr_native_max_value = _as_float(
                    config.get("chargingLimitMax"), description.native_max_value
                )
            self._attr_native_step = float(description.native_step or 1)
        elif description.key == "cutting_height":
            heights = _height_options(data)
            if heights:
                self._attr_native_min_value = float(min(heights))
                self._attr_native_max_value = float(max(heights))
                self._attr_native_step = float(_height_step(heights))

    number_cls.__init__ = init


def _install_history_dedupe() -> None:
    cls = _history.NavimowerHistory

    def append_point_locked(
        session: dict[str, Any],
        *,
        position: dict[str, Any],
        pose_time: Any,
        heading: Any,
        activity: str,
        mqtt_vehicle_state: int | None,
        mqtt_action: int | None,
        physical_zone_id: int | None,
    ) -> None:
        x = _history._as_float(position.get("x"))  # noqa: SLF001
        y = _history._as_float(position.get("y"))  # noqa: SLF001
        if x is None or y is None:
            return
        sample = [
            _history._timestamp_ms(pose_time),  # noqa: SLF001
            x,
            y,
            _history._as_float(  # noqa: SLF001
                heading if heading is not None else position.get("heading")
            ),
            str(activity or "unknown"),
            mqtt_vehicle_state,
            mqtt_action,
            physical_zone_id,
        ]
        append_or_coalesce(session.setdefault("points", []), sample)

    cls._append_point_locked = staticmethod(append_point_locked)

    original_merge = _history._merge_session_records  # noqa: SLF001

    def merge_session_records(
        previous: dict[str, Any], continuation: dict[str, Any]
    ) -> dict[str, Any]:
        merged = original_merge(previous, continuation)
        merged["points"] = compact_route_points(merged.get("points"))
        return merged

    _history._merge_session_records = merge_session_records  # noqa: SLF001

    original_load_session = cls._async_load_session_file

    async def load_session_file(self: Any, session_id: str) -> dict[str, Any] | None:
        session = await original_load_session(self, session_id)
        if session is None:
            return None
        before = session.get("points") or []
        after = compact_route_points(before)
        if len(after) != len(before):
            session["points"] = after
            changed = getattr(self, "_compacted_session_ids", set())
            changed.add(session_id)
            self._compacted_session_ids = changed
            await self._session_store_for(session_id).async_save(deepcopy(session))
        return session

    cls._async_load_session_file = load_session_file

    original_load = cls.async_load

    async def async_load(self: Any) -> None:
        await original_load(self)
        changed = set(getattr(self, "_compacted_session_ids", set()))
        if not changed:
            return
        with self._lock:
            for session_id in changed:
                session = self._cache.get(session_id)
                if session is not None:
                    self._update_active_metadata_locked(session)
            self._trail_revision = max(
                self._sequence,
                sum(
                    _history._as_int(item.get("point_count")) or 0  # noqa: SLF001
                    for item in self._sessions
                ),
            )
        await self._index_store.async_save(self._index_data())
        self._compacted_session_ids = set()

    cls.async_load = async_load


def install_capability_extensions() -> None:
    """Install model capability and route-history extensions once."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_capability_extensions_installed", False):
        return
    _install_switch_capabilities()
    _install_select_capabilities()
    _install_number_capabilities()
    _install_history_dedupe()
    cls._capability_extensions_installed = True
