from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# Release identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta15":
    raise SystemExit(f"Expected beta15 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta16"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


# Shared production option keys belong with the rest of config-entry constants.
const_path = COMPONENT / "const.py"
replace_once(
    const_path,
    '''OPT_PASSIVE_DISCOVERY: Final = "passive_discovery"\n\nDEFAULT_TRAIL_RETENTION_DAYS: Final = 7\n''',
    '''OPT_PASSIVE_DISCOVERY: Final = "passive_discovery"\nOPT_SCHEDULE_ENABLED: Final = "navimower_schedule_enabled"\nOPT_SCHEDULE_START: Final = "navimower_schedule_start"\nOPT_SCHEDULE_END: Final = "navimower_schedule_end"\nOPT_SCHEDULE_MODE: Final = "navimower_schedule_mode"\nOPT_SCHEDULE_ZONE_IDS: Final = "navimower_schedule_zone_ids"\n\nSCHEDULE_MODE_WINDOW: Final = "window"\nSCHEDULE_MODE_CONTINUOUS: Final = "continuous"\nDEFAULT_SCHEDULE_MODE: Final = SCHEDULE_MODE_WINDOW\nDEFAULT_SCHEDULE_START: Final = "10:00"\nDEFAULT_SCHEDULE_END: Final = "20:00"\n\nDEFAULT_TRAIL_RETENTION_DAYS: Final = 7\n''',
    "schedule option constants",
)


# Pure scheduling helpers: accept TimeSelector HH:MM:SS values, filter explicitly
# selected/proven zones, and never rank an uncompleted zone as the oldest.
schedule_logic_path = COMPONENT / "schedule_logic.py"
write(schedule_logic_path, r'''
"""Pure decision helpers for Navimower-managed mowing windows."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable


def parse_hhmm(value: Any, default: str) -> time:
    """Return a local wall-clock time from ``HH:MM[:SS]`` or a time object."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or default).strip()
    try:
        parts = text.split(":")
        if len(parts) < 2:
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (TypeError, ValueError):
        hour_s, minute_s = default.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    return time(hour=hour, minute=minute)


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def window_state(now: datetime, start: time, end: time) -> tuple[bool, str | None]:
    """Return whether ``now`` is in the daily window and its stable start-date token."""
    local_time = now.timetz().replace(tzinfo=None)
    if start == end:
        return False, None
    if start < end:
        if start <= local_time < end:
            return True, now.date().isoformat()
        return False, None
    if local_time >= start:
        return True, now.date().isoformat()
    if local_time < end:
        return True, (now.date() - timedelta(days=1)).isoformat()
    return False, None


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def later_iso(first: Any, second: Any) -> str | None:
    """Return the later valid ISO timestamp."""
    a, b = parse_iso(first), parse_iso(second)
    if a is None and b is None:
        return None
    if a is None:
        return str(second)
    if b is None:
        return str(first)
    return str(second) if b > a else str(first)


def completion_advanced(current: Any, baseline: Any, dispatched_at: Any) -> bool:
    """Accept completion only when it is newer than both baseline and this dispatch."""
    current_dt = parse_iso(current)
    dispatch_dt = parse_iso(dispatched_at)
    if current_dt is None or dispatch_dt is None or current_dt <= dispatch_dt:
        return False
    baseline_dt = parse_iso(baseline)
    return baseline_dt is None or current_dt > baseline_dt


def filter_schedule_zones(
    zones: Iterable[dict[str, Any]],
    selected_zone_ids: Iterable[int],
    *,
    scheduler_completed_at: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return only user-selected zones with a confirmed successful completion."""
    selected: set[int] = set()
    for value in selected_zone_ids or []:
        try:
            selected.add(int(value))
        except (TypeError, ValueError):
            continue
    confirmed = scheduler_completed_at or {}
    result: list[dict[str, Any]] = []
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        try:
            zone_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if zone_id not in selected:
            continue
        effective = later_iso(row.get("last_completed_at"), confirmed.get(str(zone_id)))
        if parse_iso(effective) is None:
            continue
        result.append(row)
    return result


def select_oldest_zone(
    zones: Iterable[dict[str, Any]],
    *,
    completed_in_window: set[int] | None = None,
    just_completed_zone_id: int | None = None,
    scheduler_completed_at: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Choose the eligible zone whose confirmed completion is oldest."""
    completed = completed_in_window or set()
    confirmed = scheduler_completed_at or {}
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        try:
            zone_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if zone_id <= 0 or zone_id in completed or zone_id == just_completed_zone_id:
            continue
        effective = later_iso(row.get("last_completed_at"), confirmed.get(str(zone_id)))
        parsed = parse_iso(effective)
        if parsed is None:
            continue
        candidates.append(((parsed, zone_id), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
''')


# Managed scheduler runtime: selected-zone allowlist, proven-completion guard and
# continuous 24-hour mode while retaining the existing one-zone-at-a-time model.
schedule_path = COMPONENT / "navimower_schedule.py"
replace_once(
    schedule_path,
    '''    ACTIVITY_RETURNING,\n    DOMAIN,\n    encode_partition_ids,\n    mow_setup,\n)\n''',
    '''    ACTIVITY_RETURNING,\n    DEFAULT_SCHEDULE_END,\n    DEFAULT_SCHEDULE_MODE,\n    DEFAULT_SCHEDULE_START,\n    DOMAIN,\n    OPT_SCHEDULE_ENABLED,\n    OPT_SCHEDULE_END,\n    OPT_SCHEDULE_MODE,\n    OPT_SCHEDULE_START,\n    OPT_SCHEDULE_ZONE_IDS,\n    SCHEDULE_MODE_CONTINUOUS,\n    SCHEDULE_MODE_WINDOW,\n    encode_partition_ids,\n    mow_setup,\n)\n''',
    "schedule shared constants import",
)
replace_once(
    schedule_path,
    '''    completion_advanced,\n    format_hhmm,\n    later_iso,\n''',
    '''    completion_advanced,\n    filter_schedule_zones,\n    format_hhmm,\n    later_iso,\n''',
    "schedule zone filter import",
)
replace_once(
    schedule_path,
    '''OPT_SCHEDULE_ENABLED = "navimower_schedule_enabled"\nOPT_SCHEDULE_START = "navimower_schedule_start"\nOPT_SCHEDULE_END = "navimower_schedule_end"\nDEFAULT_SCHEDULE_START = "10:00"\nDEFAULT_SCHEDULE_END = "20:00"\n\n''',
    '',
    "remove local schedule option constants",
)
replace_once(
    schedule_path,
    '''        self._enabled = bool(entry.options.get(OPT_SCHEDULE_ENABLED, False))\n        self._start = parse_hhmm(entry.options.get(OPT_SCHEDULE_START), DEFAULT_SCHEDULE_START)\n        self._end = parse_hhmm(entry.options.get(OPT_SCHEDULE_END), DEFAULT_SCHEDULE_END)\n        self._runtime: dict[str, Any] = self._empty_runtime()\n''',
    '''        self._enabled = bool(entry.options.get(OPT_SCHEDULE_ENABLED, False))\n        mode = str(entry.options.get(OPT_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE))\n        self._mode = mode if mode in {SCHEDULE_MODE_WINDOW, SCHEDULE_MODE_CONTINUOUS} else DEFAULT_SCHEDULE_MODE\n        self._start = parse_hhmm(entry.options.get(OPT_SCHEDULE_START), DEFAULT_SCHEDULE_START)\n        self._end = parse_hhmm(entry.options.get(OPT_SCHEDULE_END), DEFAULT_SCHEDULE_END)\n        self._selection_configured = OPT_SCHEDULE_ZONE_IDS in entry.options\n        self._selected_zone_ids = self._normalize_zone_ids(entry.options.get(OPT_SCHEDULE_ZONE_IDS, []))\n        self._legacy_selection_migration_allowed = self._enabled and not self._selection_configured\n        self._runtime: dict[str, Any] = self._empty_runtime()\n''',
    "schedule runtime options",
)
replace_once(
    schedule_path,
    '''    @staticmethod\n    def _empty_runtime() -> dict[str, Any]:\n''',
    '''    @staticmethod\n    def _normalize_zone_ids(values: Any) -> set[int]:\n        if not isinstance(values, (list, tuple, set)):\n            values = [] if values in (None, "") else [values]\n        result: set[int] = set()\n        for value in values:\n            try:\n                zone_id = int(value)\n            except (TypeError, ValueError):\n                continue\n            if zone_id > 0:\n                result.add(zone_id)\n        return result\n\n    @staticmethod\n    def _empty_runtime() -> dict[str, Any]:\n''',
    "schedule zone id normalizer",
)
replace_once(
    schedule_path,
    '''            "window_token": None,\n            "completed_zone_ids_in_window": [],\n''',
    '''            "window_token": None,\n            "round_index": 1,\n            "round_started_at": None,\n            "completed_zone_ids_in_window": [],\n''',
    "schedule round runtime",
)
replace_once(
    schedule_path,
    '''    @property\n    def start_time(self) -> time:\n        return self._start\n\n    @property\n    def end_time(self) -> time:\n        return self._end\n''',
    '''    @property\n    def mode(self) -> str:\n        return self._mode\n\n    @property\n    def selected_zone_ids(self) -> tuple[int, ...]:\n        return tuple(sorted(self._selected_zone_ids))\n\n    @property\n    def start_time(self) -> time:\n        return self._start\n\n    @property\n    def end_time(self) -> time:\n        return self._end\n''',
    "schedule option properties",
)
replace_once(
    schedule_path,
    '''        if isinstance(saved, dict):\n            restored = self._empty_runtime()\n            restored.update({key: deepcopy(value) for key, value in saved.items() if key in restored})\n            self._runtime = restored\n        self._unsub = self.coordinator.async_add_listener(self._handle_update)\n''',
    '''        if isinstance(saved, dict):\n            restored = self._empty_runtime()\n            restored.update({key: deepcopy(value) for key, value in saved.items() if key in restored})\n            self._runtime = restored\n        self._maybe_migrate_legacy_zone_selection()\n        self._unsub = self.coordinator.async_add_listener(self._handle_update)\n''',
    "legacy scheduler zone migration start",
)
replace_once(
    schedule_path,
    '''    def diagnostics(self) -> dict[str, Any]:\n        now = dt_util.now()\n        open_now, token = window_state(now, self._start, self._end)\n        return {\n            "enabled": self._enabled,\n            "start": format_hhmm(self._start),\n            "end": format_hhmm(self._end),\n            "window_open": open_now,\n            "window_token_now": token,\n            **deepcopy(self._runtime),\n        }\n''',
    '''    def diagnostics(self) -> dict[str, Any]:\n        now = dt_util.now()\n        open_now, token = self._window_state(now)\n        eligible = self._eligible_zones()\n        eligible_ids = sorted(int(row["id"]) for row in eligible if row.get("id") is not None)\n        return {\n            "enabled": self._enabled,\n            "mode": self._mode,\n            "start": format_hhmm(self._start),\n            "end": format_hhmm(self._end),\n            "selected_zone_ids": sorted(self._selected_zone_ids),\n            "eligible_zone_ids": eligible_ids,\n            "zone_selection_configured": self._selection_configured,\n            "window_open": open_now,\n            "window_token_now": token,\n            **deepcopy(self._runtime),\n        }\n''',
    "schedule diagnostics options",
)
replace_once(
    schedule_path,
    '''                "start",\n                "end",\n                "window_open",\n''',
    '''                "mode",\n                "start",\n                "end",\n                "selected_zone_ids",\n                "eligible_zone_ids",\n                "window_open",\n''',
    "schedule entity option attributes",
)
replace_once(
    schedule_path,
    '''        if enabled:\n            native = (self.coordinator.data or {}).get("settings", {}).get("schedule_enabled")\n            if native is None:\n                raise RuntimeError("Native mowing schedule state is not available yet")\n            if native is True:\n                await self._async_set_native_schedule(False)\n''',
    '''        if enabled:\n            if not self._eligible_zones():\n                raise RuntimeError(\n                    "Configure at least one successfully completed automatic mowing zone first"\n                )\n            native = (self.coordinator.data or {}).get("settings", {}).get("schedule_enabled")\n            if native is None:\n                raise RuntimeError("Native mowing schedule state is not available yet")\n            if native is True:\n                await self._async_set_native_schedule(False)\n''',
    "schedule enable selection guard",
)
replace_once(
    schedule_path,
    '''        self._enabled = enabled\n        self._update_options(**{OPT_SCHEDULE_ENABLED: enabled})\n''',
    '''        self._enabled = enabled\n        self._legacy_selection_migration_allowed = False\n        self._update_options(**{OPT_SCHEDULE_ENABLED: enabled})\n''',
    "disable legacy migration after user switch",
)
replace_once(
    schedule_path,
    '''    def _zones(self) -> list[dict[str, Any]]:\n        return [row for row in (self.coordinator.data or {}).get("zone_states") or [] if isinstance(row, dict)]\n\n    def _zone(self, zone_id: int | None) -> dict[str, Any] | None:\n''',
    '''    def _zones(self) -> list[dict[str, Any]]:\n        return [row for row in (self.coordinator.data or {}).get("zone_states") or [] if isinstance(row, dict)]\n\n    def _maybe_migrate_legacy_zone_selection(self) -> None:\n        """Preserve an already-enabled beta scheduler without auto-enrolling future zones."""\n        if not self._legacy_selection_migration_allowed or self._selection_configured:\n            return\n        zones = self._zones()\n        if not zones:\n            return\n        proven: set[int] = set()\n        for row in zones:\n            try:\n                zone_id = int(row.get("id"))\n            except (TypeError, ValueError):\n                continue\n            if zone_id > 0 and parse_iso(row.get("last_completed_at")) is not None:\n                proven.add(zone_id)\n        self._selected_zone_ids = proven\n        self._selection_configured = True\n        self._legacy_selection_migration_allowed = False\n        self._update_options(\n            **{\n                OPT_SCHEDULE_ZONE_IDS: [str(value) for value in sorted(proven)],\n                OPT_SCHEDULE_MODE: self._mode,\n            }\n        )\n        _LOGGER.info(\n            "Migrated the enabled beta Navimower schedule to an explicit allowlist of %s proven zones",\n            len(proven),\n        )\n\n    def _eligible_zones(self) -> list[dict[str, Any]]:\n        return filter_schedule_zones(\n            self._zones(),\n            self._selected_zone_ids,\n            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},\n        )\n\n    def _window_state(self, now: datetime) -> tuple[bool, str | None]:\n        if self._mode == SCHEDULE_MODE_CONTINUOUS:\n            return True, "continuous"\n        return window_state(now, self._start, self._end)\n\n    def _zone(self, zone_id: int | None) -> dict[str, Any] | None:\n''',
    "schedule selection helpers",
)
replace_once(
    schedule_path,
    '''        data = self.coordinator.data or {}\n        settings = data.get("settings") or {}\n''',
    '''        data = self.coordinator.data or {}\n        self._maybe_migrate_legacy_zone_selection()\n        settings = data.get("settings") or {}\n''',
    "legacy schedule migration evaluation",
)
replace_once(
    schedule_path,
    '''        now = dt_util.now()\n        in_window, token = window_state(now, self._start, self._end)\n        if in_window and token and token != self._runtime.get("window_token"):\n            self._runtime["window_token"] = token\n            self._runtime["completed_zone_ids_in_window"] = []\n            self._runtime["just_completed_zone_id"] = None\n            self._runtime["suspended_reason"] = None\n            self._runtime["retry_not_before"] = None\n            await self._save()\n''',
    '''        now = dt_util.now()\n        in_window, token = self._window_state(now)\n        if in_window and token and token != self._runtime.get("window_token"):\n            self._runtime["window_token"] = token\n            self._runtime["round_index"] = 1\n            self._runtime["round_started_at"] = _utc_now()\n            self._runtime["completed_zone_ids_in_window"] = []\n            self._runtime["just_completed_zone_id"] = None\n            self._runtime["suspended_reason"] = None\n            self._runtime["retry_not_before"] = None\n            await self._save()\n''',
    "continuous window state",
)
replace_once(
    schedule_path,
    '''        completed = {int(value) for value in self._runtime.get("completed_zone_ids_in_window") or []}\n        candidate = select_oldest_zone(\n            self._zones(),\n            completed_in_window=completed,\n            just_completed_zone_id=self._runtime.get("just_completed_zone_id"),\n            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},\n        )\n        if candidate is None:\n            return\n''',
    '''        completed = {int(value) for value in self._runtime.get("completed_zone_ids_in_window") or []}\n        eligible = self._eligible_zones()\n        candidate = select_oldest_zone(\n            eligible,\n            completed_in_window=completed,\n            just_completed_zone_id=self._runtime.get("just_completed_zone_id"),\n            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},\n        )\n        if candidate is None and self._mode == SCHEDULE_MODE_CONTINUOUS and eligible:\n            eligible_ids = {int(row["id"]) for row in eligible if row.get("id") is not None}\n            if eligible_ids and eligible_ids.issubset(completed):\n                self._runtime["completed_zone_ids_in_window"] = []\n                self._runtime["just_completed_zone_id"] = None\n                self._runtime["round_index"] = int(self._runtime.get("round_index") or 1) + 1\n                self._runtime["round_started_at"] = _utc_now()\n                await self._save()\n                candidate = select_oldest_zone(\n                    eligible,\n                    completed_in_window=set(),\n                    just_completed_zone_id=None,\n                    scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},\n                )\n        if candidate is None:\n            return\n''',
    "schedule eligible zone selection and continuous rounds",
)


# Options flow: a dedicated Navimower Schedule page under the integration gear.
config_flow_path = COMPONENT / "config_flow.py"
replace_once(
    config_flow_path,
    'from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType\n',
    '''from homeassistant.helpers.selector import (\n    SelectSelector,\n    SelectSelectorConfig,\n    SelectSelectorMode,\n    TextSelector,\n    TextSelectorConfig,\n    TextSelectorType,\n    TimeSelector,\n)\n''',
    "schedule selector imports",
)
replace_once(
    config_flow_path,
    '''    DEFAULT_INCLUDE_RETURN_TRAIL,\n    DEFAULT_LANGUAGE,\n    DEFAULT_TRAIL_RETENTION_DAYS,\n''',
    '''    DEFAULT_INCLUDE_RETURN_TRAIL,\n    DEFAULT_LANGUAGE,\n    DEFAULT_SCHEDULE_END,\n    DEFAULT_SCHEDULE_MODE,\n    DEFAULT_SCHEDULE_START,\n    DEFAULT_TRAIL_RETENTION_DAYS,\n''',
    "schedule default imports",
)
replace_once(
    config_flow_path,
    '''    OPT_INCLUDE_RETURN_TRAIL,\n    OPT_TRAIL_RETENTION_DAYS,\n    OPT_ZONES,\n''',
    '''    OPT_INCLUDE_RETURN_TRAIL,\n    OPT_SCHEDULE_END,\n    OPT_SCHEDULE_MODE,\n    OPT_SCHEDULE_START,\n    OPT_SCHEDULE_ZONE_IDS,\n    OPT_TRAIL_RETENTION_DAYS,\n    OPT_ZONES,\n    SCHEDULE_MODE_CONTINUOUS,\n    SCHEDULE_MODE_WINDOW,\n''',
    "schedule option imports",
)
replace_once(
    config_flow_path,
    'from .oauth import async_register_oauth_implementation\n',
    'from .oauth import async_register_oauth_implementation\nfrom .schedule_logic import format_hhmm, parse_hhmm\n',
    "schedule time helpers import",
)
replace_once(
    config_flow_path,
    '    """Manage general history, user-friendly gates and local channels."""\n',
    '    """Manage history, the managed scheduler, gates and local channels."""\n',
    "options flow docstring",
)
replace_once(
    config_flow_path,
    '''    def _gates(self) -> list[NavimowerGate]:\n''',
    '''    def _schedule_zone_rows(self) -> list[dict[str, Any]]:\n        coordinator = self._coordinator()\n        data = getattr(coordinator, "data", None) or {}\n        rows = data.get("zone_states") or []\n        return [row for row in rows if isinstance(row, dict) and row.get("id") is not None]\n\n    def _schedule_zone_choices(self) -> dict[str, str]:\n        choices: dict[str, str] = {}\n        for row in self._schedule_zone_rows():\n            if not row.get("last_completed_at"):\n                continue\n            try:\n                zone_id = str(int(row["id"]))\n            except (TypeError, ValueError):\n                continue\n            choices[zone_id] = str(row.get("name") or f"Zone {zone_id}")\n        for value in self._options().get(OPT_SCHEDULE_ZONE_IDS, []) or []:\n            text = str(value)\n            if text.isdigit():\n                choices.setdefault(text, f"Zone {text} (saved)")\n        return choices\n\n    def _schedule_unavailable_text(self) -> str:\n        rows: list[str] = []\n        for row in self._schedule_zone_rows():\n            if row.get("last_completed_at"):\n                continue\n            name = str(row.get("name") or f"Zone {row.get('id')}")\n            rows.append(f"- {name} — Never completed")\n        return "\\n".join(rows) if rows else "None"\n\n    def _gates(self) -> list[NavimowerGate]:\n''',
    "schedule options zone helpers",
)
replace_once(
    config_flow_path,
    '            menu_options=["general", "gates", "channels"],\n',
    '            menu_options=["general", "navimower_schedule", "gates", "channels"],\n',
    "options menu schedule entry",
)
insert_marker = '    async def async_step_gates(\n'
schedule_step = '''    async def async_step_navimower_schedule(\n        self, user_input: dict[str, Any] | None = None\n    ) -> ConfigFlowResult:\n        options = self._options()\n        choices = self._schedule_zone_choices()\n        errors: dict[str, str] = {}\n        if user_input is not None:\n            selected = [\n                str(value)\n                for value in user_input.get(OPT_SCHEDULE_ZONE_IDS, []) or []\n                if str(value) in choices\n            ]\n            mode = str(user_input.get(OPT_SCHEDULE_MODE) or DEFAULT_SCHEDULE_MODE)\n            start = format_hhmm(\n                parse_hhmm(user_input.get(OPT_SCHEDULE_START), DEFAULT_SCHEDULE_START)\n            )\n            end = format_hhmm(\n                parse_hhmm(user_input.get(OPT_SCHEDULE_END), DEFAULT_SCHEDULE_END)\n            )\n            if mode == SCHEDULE_MODE_WINDOW and start == end:\n                errors["base"] = "schedule_same_time"\n            else:\n                return self._save(\n                    **{\n                        OPT_SCHEDULE_ZONE_IDS: selected,\n                        OPT_SCHEDULE_MODE: mode,\n                        OPT_SCHEDULE_START: start,\n                        OPT_SCHEDULE_END: end,\n                    }\n                )\n\n        selected_default = [\n            str(value)\n            for value in options.get(OPT_SCHEDULE_ZONE_IDS, []) or []\n            if str(value) in choices\n        ]\n        mode_default = str(options.get(OPT_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE))\n        if mode_default not in {SCHEDULE_MODE_WINDOW, SCHEDULE_MODE_CONTINUOUS}:\n            mode_default = DEFAULT_SCHEDULE_MODE\n        return self.async_show_form(\n            step_id="navimower_schedule",\n            data_schema=vol.Schema(\n                {\n                    vol.Required(\n                        OPT_SCHEDULE_ZONE_IDS,\n                        default=selected_default,\n                    ): SelectSelector(\n                        SelectSelectorConfig(\n                            options=[\n                                {"value": value, "label": label}\n                                for value, label in choices.items()\n                            ],\n                            multiple=True,\n                            mode=SelectSelectorMode.LIST,\n                        )\n                    ),\n                    vol.Required(\n                        OPT_SCHEDULE_MODE,\n                        default=mode_default,\n                    ): SelectSelector(\n                        SelectSelectorConfig(\n                            options=[\n                                {"value": SCHEDULE_MODE_WINDOW, "label": "Time window"},\n                                {"value": SCHEDULE_MODE_CONTINUOUS, "label": "24 hours"},\n                            ],\n                            mode=SelectSelectorMode.LIST,\n                        )\n                    ),\n                    vol.Required(\n                        OPT_SCHEDULE_START,\n                        default=str(options.get(OPT_SCHEDULE_START, DEFAULT_SCHEDULE_START)),\n                    ): TimeSelector(),\n                    vol.Required(\n                        OPT_SCHEDULE_END,\n                        default=str(options.get(OPT_SCHEDULE_END, DEFAULT_SCHEDULE_END)),\n                    ): TimeSelector(),\n                }\n            ),\n            errors=errors,\n            description_placeholders={\n                "unavailable_zones": self._schedule_unavailable_text(),\n            },\n        )\n\n'''
replace_once(
    config_flow_path,
    insert_marker,
    schedule_step + insert_marker,
    "schedule options form",
)


# User-facing copy in both the source strings and bundled English translation.
for relative in ("strings.json", "translations/en.json"):
    path = COMPONENT / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    options = payload["options"]
    init = options["step"]["init"]
    init["description"] = (
        "Configure retained trail history, Navimower Schedule, zone-pair gates and "
        "optional local X/Y gate areas."
    )
    menu = init["menu_options"]
    rebuilt_menu: dict[str, str] = {}
    for key, value in menu.items():
        rebuilt_menu[key] = value
        if key == "general":
            rebuilt_menu["navimower_schedule"] = "Navimower Schedule"
    init["menu_options"] = rebuilt_menu
    options["step"]["navimower_schedule"] = {
        "title": "Navimower Schedule",
        "description": (
            "Choose which proven zones the integration may mow automatically and when it "
            "may work. Only zones with at least one confirmed successful completion can "
            "be selected. A new or difficult zone must first be fully completed manually "
            "once; completing it does not select it automatically. Start and end are used "
            "only in Time window mode.\\n\\nNot yet available for automatic mowing:\\n"
            "{unavailable_zones}"
        ),
        "data": {
            "navimower_schedule_zone_ids": "Automatic mowing zones",
            "navimower_schedule_mode": "Allowed mowing time",
            "navimower_schedule_start": "Start",
            "navimower_schedule_end": "End",
        },
        "data_description": {
            "navimower_schedule_zone_ids": (
                "The scheduler only dispatches zones selected here. New zones are never "
                "added automatically."
            ),
            "navimower_schedule_mode": (
                "Time window stops managed mowing outside the configured hours. 24 hours "
                "keeps the managed scheduler available continuously and starts a new round "
                "after every selected zone has completed."
            ),
            "navimower_schedule_start": "Used only in Time window mode.",
            "navimower_schedule_end": "Used only in Time window mode.",
        },
    }
    options["error"]["schedule_same_time"] = (
        "Start and end must be different when Allowed mowing time is Time window."
    )
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# Historical beta identity remains historical; beta16 owns the current manifest assertion.
beta15_test = ROOT / "tests" / "test_v043_beta15.py"
replace_once(
    beta15_test,
    '''def test_beta15_identity():\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta15"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta15.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta15")\n''',
    '''def test_beta15_identity():\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta15.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta15")\n''',
    "beta15 historical identity test",
)

write(ROOT / "tests" / "test_v043_beta16.py", r'''
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _schedule_logic():
    spec = importlib.util.spec_from_file_location(
        "navimower_schedule_logic_beta16", COMPONENT / "schedule_logic.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta16_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta16"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta16.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta16")


def test_uncompleted_zone_is_not_eligible_or_ranked_first():
    logic = _schedule_logic()
    zones = [
        {"id": 1, "name": "Never completed", "last_completed_at": None},
        {"id": 2, "name": "Proven", "last_completed_at": "2026-08-17T10:00:00+00:00"},
    ]
    eligible = logic.filter_schedule_zones(zones, [1, 2])
    assert [row["id"] for row in eligible] == [2]
    assert logic.select_oldest_zone(zones)["id"] == 2


def test_schedule_time_parser_accepts_time_selector_seconds():
    logic = _schedule_logic()
    assert logic.format_hhmm(logic.parse_hhmm("09:30:00", "10:00")) == "09:30"


def test_options_flow_exposes_multi_zone_and_24_hour_configuration():
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert 'menu_options=["general", "navimower_schedule", "gates", "channels"]' in source
    assert "multiple=True" in source
    assert '"24 hours"' in source
    assert '"unavailable_zones": self._schedule_unavailable_text()' in source
    step = strings["options"]["step"]["navimower_schedule"]
    assert step["data"]["navimower_schedule_zone_ids"] == "Automatic mowing zones"
    assert "fully completed manually once" in step["description"]


def test_scheduler_filters_allowlist_and_has_continuous_rounds():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "def _eligible_zones" in source
    assert "filter_schedule_zones(" in source
    assert 'return True, "continuous"' in source
    assert "eligible_ids.issubset(completed)" in source
    assert 'self._runtime["round_index"]' in source
    assert "Configure at least one successfully completed automatic mowing zone first" in source


def test_existing_enabled_beta_scheduler_gets_one_time_explicit_allowlist_migration():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "_legacy_selection_migration_allowed" in source
    assert "def _maybe_migrate_legacy_zone_selection" in source
    assert "OPT_SCHEDULE_ZONE_IDS: [str(value) for value in sorted(proven)]" in source
    assert "future zones" in source
''')


notes = '''
title: Navimower 0.4.3-beta16

Managed-scheduler configuration beta.

### Added
- Add a dedicated **Navimower Schedule** page under the integration Configure/gear options flow.
- Add a multi-select **Automatic mowing zones** allowlist using stable zone IDs.
- List mapped zones that are not yet eligible and explain that each must be fully completed manually once before it can be selected.
- Add **Time window** and **24 hours** managed-schedule modes. Time window continues to use the existing Start/End controls; 24 hours never closes the managed window and starts a new oldest-zone round after all selected zones complete.

### Safety
- A zone with no confirmed `last_completed_at` is never an automatic scheduler candidate.
- New zones are never automatically added to the allowlist after configuration.
- Turning Navimower Schedule on is refused until at least one selected zone has a confirmed successful completion; the native schedule is left untouched on that failed enable attempt.
- Already-enabled beta13-beta15 schedulers receive a one-time allowlist containing only currently proven zones, preserving existing field setups without auto-enrolling future zones.

### Unchanged
- The Navimower Schedule switch remains the runtime on/off control and native/managed schedules remain mutually exclusive.
- Existing Start/End time entities remain available for dashboards and automations.
- Maintenance and Mowing Reports discovery remains paused.
'''
write(ROOT / ".github" / "release-notes" / "0.4.3-beta16.md", notes)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n"
if not changelog.startswith(marker):
    raise SystemExit("changelog heading missing")
entry = '''

## 0.4.3-beta16

Configure the integration-owned scheduler from the Navimower gear/options flow.

### Added

- Add multi-zone Automatic mowing zones selection using stable zone IDs.
- Show never-completed zones as unavailable until one confirmed manual completion exists.
- Add Time window and 24 hours modes; continuous mode rolls into a new oldest-zone round after all selected zones finish.

### Safety

- Never schedule a zone without a confirmed successful completion.
- Never auto-enroll newly created zones.
- Refuse enabling the managed scheduler when no proven selected zone exists.
- Migrate already-enabled beta schedulers once to an explicit allowlist of currently proven zones.
'''
changelog_path.write_text(
    marker + dedent(entry) + changelog[len(marker):],
    encoding="utf-8",
)


# Builder-level smoke checks before the full repository suite.
config_source = config_flow_path.read_text(encoding="utf-8")
schedule_source = schedule_path.read_text(encoding="utf-8")
logic_source = schedule_logic_path.read_text(encoding="utf-8")
assert 'menu_options=["general", "navimower_schedule", "gates", "channels"]' in config_source
assert "multiple=True" in config_source
assert '"24 hours"' in config_source
assert "def filter_schedule_zones" in logic_source
assert "if parsed is None:\n            continue" in logic_source
assert 'return True, "continuous"' in schedule_source
assert "eligible_ids.issubset(completed)" in schedule_source
assert "Configure at least one successfully completed automatic mowing zone first" in schedule_source
