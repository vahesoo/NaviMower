"""Constants and small pure helpers for the Navimower integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "navimower"
MANUFACTURER: Final = "Segway Navimow"
OAUTH_SOURCE_DOMAIN: Final = "navimow"

# --- Config entry / options keys -------------------------------------------
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"  # only used transiently during the config flow
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_UID: Final = "uid"
CONF_DEVICE_ID: Final = "device_id"
CONF_REGION: Final = "region"
CONF_LANGUAGE: Final = "language"
CONF_VEHICLE_SN: Final = "vehicle_sn"
CONF_VEHICLE_TYPE: Final = "vehicle_type"
CONF_VEHICLE_NAME: Final = "vehicle_name"
CONF_MODEL: Final = "model"
CONF_MQTT_SOURCE_ENTRY_ID: Final = "mqtt_source_entry_id"

# Options-flow keys
OPT_ZONES: Final = "zones"  # user-supplied "id:name,id:name" fallback list
OPT_CHANNELS: Final = "channels"  # JSON list of channel rectangles
OPT_GATES: Final = "gates"  # JSON list of bidirectional zone-pair gates

DEFAULT_LANGUAGE: Final = "en"

# --- Official OAuth / MQTT -------------------------------------------------
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
MQTT_TRAIL_SAVE_DELAY_SECONDS: Final = 30
TUNNEL_DETECTION_RADIUS_M: Final = 1.0
ZONE_EDGE_TOLERANCE_M: Final = 0.35

# --- Private-cloud polling -------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final = 30
FAST_SCAN_INTERVAL: Final = 12
# MQTT supplies the dense pose/trail. Private cloud is deliberately not polled
# every 3 seconds while mowing; this keeps the app API load conservative.
MOW_SCAN_INTERVAL: Final = 12
SLOW_REFRESH_EVERY: Final = 6

# --- Coverage / mowed-trail overlay ----------------------------------------
SWATH_WIDTH_M: Final = 0.25
TRAIL_MAX_POINTS: Final = 10000
TRAIL_MIN_STEP_M: Final = 0.12
TRAIL_BREAK_M: Final = 5.0

# --- vehicle_state (empirical hex from private index2/auth-list) ------------
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
    STATE_IDLE_DOCKED_POST: "Docked (finished)",
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

# --- Mow options ------------------------------------------------------------
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
