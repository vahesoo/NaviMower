"""Constants and small pure helpers for the Navimower integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "navimower"
MANUFACTURER: Final = "Segway Navimow"

# --- Config entry / connection keys ---------------------------------------
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"  # transient during config/reauth only
CONF_ACCESS_TOKEN: Final = "access_token"  # private-cloud Passport token
CONF_REFRESH_TOKEN: Final = "refresh_token"  # private-cloud Passport refresh token
CONF_PASSPORT_UUID: Final = "passport_uuid"  # private Passport account UUID
CONF_UID: Final = "uid"  # private mower-cloud uid
CONF_DEVICE_ID: Final = "device_id"  # stable private-cloud app/device id
CONF_REGION: Final = "region"
CONF_LANGUAGE: Final = "language"
CONF_VEHICLE_SN: Final = "vehicle_sn"
CONF_VEHICLE_TYPE: Final = "vehicle_type"
CONF_VEHICLE_NAME: Final = "vehicle_name"
CONF_MODEL: Final = "model"
CONF_OAUTH_DEVICE_ID: Final = "oauth_device_id"
CONF_AUTH_IMPLEMENTATION: Final = "auth_implementation"
CONF_OAUTH_TOKEN: Final = "token"
CONF_API_BASE_URL: Final = "api_base_url"

# Legacy v0.1.x key/domain. Used only to copy the existing OAuth token into the
# Navimower entry during config-entry migration; runtime no longer depends on it.
CONF_MQTT_SOURCE_ENTRY_ID: Final = "mqtt_source_entry_id"
LEGACY_OAUTH_SOURCE_DOMAIN: Final = "navimow"

# --- Options ---------------------------------------------------------------
OPT_ZONES: Final = "zones"  # legacy id:name fallback; preserved during migration
OPT_CHANNELS: Final = "channels"  # list[dict] of local X/Y rectangles
OPT_GATES: Final = "gates"  # list[dict] of zone-pair gates
OPT_TRAIL_RETENTION_DAYS: Final = "trail_retention_days"
OPT_INCLUDE_RETURN_TRAIL: Final = "include_return_trail"
OPT_DIAGNOSTICS_DETAIL: Final = "diagnostics_detail"

DEFAULT_TRAIL_RETENTION_DAYS: Final = 7
TRAIL_RETENTION_OPTIONS: Final[tuple[int, ...]] = (3, 7, 14, 30, 0)  # 0 = unlimited
GATE_CLOSE_DELAY_OPTIONS: Final[tuple[int, ...]] = (0, 10, 20, 30)
DEFAULT_INCLUDE_RETURN_TRAIL: Final = True
DEFAULT_DIAGNOSTICS_DETAIL: Final = "standard"

DEFAULT_LANGUAGE: Final = "en"

# --- Official Smart Home OAuth / MQTT -------------------------------------
OAUTH2_AUTHORIZE: Final = (
    "https://navimow-h5-fra.willand.com/smartHome/login?channel=homeassistant"
)
OAUTH2_TOKEN: Final = "https://navimow-fra.ninebot.com/openapi/oauth/getAccessToken"
CLIENT_ID: Final = "homeassistant"
CLIENT_SECRET: Final = "57056e15-722e-42be-bbaa-b0cbfb208a52"
API_BASE_URL: Final = "https://navimow-fra.ninebot.com"
MQTT_BROKER: Final = "mqtt.navimow.com"
MQTT_PORT: Final = 1883
MQTT_USERNAME: Final | None = None
MQTT_PASSWORD: Final | None = None
MQTT_POSE_STALE_SECONDS: Final = 20
# A live stream can be stale while the broker connection itself remains up.
# The bridge starts recovery a little after the entity freshness threshold so a
# single delayed packet does not cause needless reconnects.
MQTT_POSE_RECOVERY_STALE_SECONDS: Final = 25
MQTT_WATCHDOG_INTERVAL_SECONDS: Final = 5
MQTT_RESUBSCRIBE_GRACE_SECONDS: Final = 10
MQTT_DISCONNECT_TIMEOUT_SECONDS: Final = 15
# First rebuild is immediate; repeated failures back off while private-cloud
# fallback continues to publish state and position.
MQTT_RECOVERY_BACKOFF_SECONDS: Final[tuple[int, ...]] = (0, 15, 30, 60, 120, 300)
MQTT_HISTORY_SAVE_DELAY_SECONDS: Final = 30
# Legacy name retained while migrating old code paths.
MQTT_TRAIL_SAVE_DELAY_SECONDS: Final = MQTT_HISTORY_SAVE_DELAY_SECONDS
TUNNEL_DETECTION_RADIUS_M: Final = 1.0
ZONE_EDGE_TOLERANCE_M: Final = 0.35

# --- Private-cloud polling -------------------------------------------------
# v0.2.1 deliberately starts with a fairly aggressive profile. The private
# cloud is the fallback when the official MQTT location stream stalls, so a
# five-second active poll keeps route gaps below the map-card break threshold on
# a normally moving mower. These values can be relaxed after field testing.
DEFAULT_SCAN_INTERVAL: Final = 15
FAST_SCAN_INTERVAL: Final = 8
MOW_SCAN_INTERVAL: Final = 5
PRIVATE_FAST_REFRESH_MIN_SECONDS: Final = 2
PRIVATE_CORE_HEALTH_SECONDS: Final = 45

# Per-endpoint TTLs. A coordinator cycle may run every five seconds while
# mowing, but only endpoints whose TTL has elapsed are called.
PRIVATE_ENDPOINT_TTLS_ACTIVE: Final[dict[str, int]] = {
    "device_info": 86400,
    "index2": 5,
    "auth_list": 15,
    "location": 5,
    "path_info_time": 5,
    "set_list": 30,
    "today_plan": 30,
    "map_list": 60,
    "maintenance": 600,
}
PRIVATE_ENDPOINT_TTLS_IDLE: Final[dict[str, int]] = {
    "device_info": 86400,
    "index2": 15,
    "auth_list": 30,
    "location": 30,
    "path_info_time": 30,
    "set_list": 60,
    "today_plan": 60,
    "map_list": 120,
    "maintenance": 600,
}

# --- Map/history API -------------------------------------------------------
MAP_API_SCHEMA_VERSION: Final = 4
# Cached reduced map geometry has its own version. Bump this whenever the
# persisted geometry keys or semantics change without changing the public API.
MAP_GEOMETRY_SCHEMA_VERSION: Final = 3
SESSION_CACHE_LIMIT: Final = 64
# Short operator stops, integration reloads and Home Assistant restarts are one
# logical mowing session when cutting resumes within this window.
SESSION_MERGE_GAP_SECONDS: Final = 300
# Navimow may intentionally finish a practical cycle below 100% when small
# obstructed/inaccessible remnants remain. A vendor end timestamp at or above
# this threshold is treated as a completed cycle.
VENDOR_COMPLETION_PROGRESS_MIN: Final = 95
# A direct HA mowing command is the strongest navigation-intent source until
# the mower confirms the same immediate target or the safety TTL expires.
COMMAND_TARGET_TTL_SECONDS: Final = 1800
# Optimistic command activity prevents short transition/unknown states from
# being exposed as Docked while pause/start/dock is still being acknowledged.
COMMAND_ACTIVITY_TTL_SECONDS: Final = 30

# --- Coverage / rendered trail --------------------------------------------
SWATH_WIDTH_M: Final = 0.25
TRAIL_BREAK_M: Final = 5.0
# Legacy v0.1.x values retained only for importing old trail storage.
TRAIL_MAX_POINTS: Final = 10000
TRAIL_MIN_STEP_M: Final = 0.0

# --- vehicle_state (empirical private-cloud hex) ---------------------------
STATE_IDLE_DOCKED: Final = "0101"
STATE_IDLE_DOCKED_POST: Final = "0102"
STATE_MOWING: Final = "0210"
STATE_PAUSED: Final = "0211"
STATE_RETURNING: Final = "0220"

ACTIVITY_MOWING: Final = "mowing"
ACTIVITY_PAUSED: Final = "paused"
ACTIVITY_DOCKED: Final = "docked"
ACTIVITY_RETURNING: Final = "returning"
ACTIVITY_ERROR: Final = "error"

VEHICLE_STATE_TO_ACTIVITY: Final[dict[str, str]] = {
    STATE_IDLE_DOCKED: ACTIVITY_DOCKED,
    STATE_IDLE_DOCKED_POST: ACTIVITY_DOCKED,
    STATE_MOWING: ACTIVITY_MOWING,
    STATE_PAUSED: ACTIVITY_PAUSED,
    STATE_RETURNING: ACTIVITY_RETURNING,
}

VEHICLE_STATE_LABELS: Final[dict[str, str]] = {
    STATE_IDLE_DOCKED: "Docked",
    STATE_IDLE_DOCKED_POST: "Charging",
    STATE_MOWING: "Mowing",
    STATE_PAUSED: "Paused",
    STATE_RETURNING: "Returning to dock",
}

DOCKED_STATES: Final = {STATE_IDLE_DOCKED, STATE_IDLE_DOCKED_POST}
ACTIVE_STATES: Final = {STATE_MOWING, STATE_RETURNING}

# Observed official MQTT location payload vehicleState values.
MQTT_STATE_IDLE: Final = 1
MQTT_STATE_DOCKED: Final = 2
MQTT_STATE_CHARGING: Final = 3
MQTT_STATE_MOWING: Final = 4
MQTT_STATE_RETURNING: Final = 5
MQTT_STATE_MAPPING: Final = 6
MQTT_DOCKED_STATES: Final = {MQTT_STATE_DOCKED, MQTT_STATE_CHARGING}
MQTT_CUTTING_ACTIONS: Final = {5, 8}  # normal mowing / boundary mowing

# --- Mow options -----------------------------------------------------------
MOW_SETUP_CONTINUE_AUTO: Final = 0x11
MOW_SETUP_CONTINUE: Final = 0x12
MOW_SETUP_RESTART_AUTO: Final = 0x21
MOW_SETUP_RESTART: Final = 0x22


def mow_setup(*, reset: bool, ordered: bool) -> int:
    """Return ``partitionSetup`` for restart/continue and zone-order mode."""
    return (0x20 if reset else 0x10) | (0x02 if ordered else 0x01)


def encode_partition_ids(region_ids: list[int]) -> str:
    """Encode region ids as concatenated little-endian uint16 hex."""
    return "".join(f"{rid & 0xFF:02x}{(rid >> 8) & 0xFF:02x}" for rid in region_ids)


def decode_partition_id_list(be_hex: str) -> list[int]:
    """Decode index2.partitionIdList (big-endian uint16 hex) to region ids."""
    if not be_hex:
        return []
    try:
        raw = bytes.fromhex(be_hex.strip())
    except ValueError:
        return []
    return [
        value
        for i in range(0, len(raw) - 1, 2)
        if (value := int.from_bytes(raw[i : i + 2], "big"))
    ]
