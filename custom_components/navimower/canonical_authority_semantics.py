"""Install Canonical Mower State as the public resolved-state owner."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator as _coordinator
from .canonical_state import build_canonical_state, canonical_diagnostics
from .const import MQTT_POSE_STALE_SECONDS


def _apply_public_state(snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    """Project canonical resolved values onto stable HA/public aliases."""
    navigation = state.get("navigation") if isinstance(state.get("navigation"), dict) else {}
    position = navigation.get("position") if isinstance(navigation.get("position"), dict) else {}
    value = position.get("value")
    if isinstance(value, dict):
        snapshot["position"] = deepcopy(value)
        snapshot["pose_source"] = position.get("source")
        snapshot["position_source_age"] = position.get("source_age_s")
        snapshot["position_stale"] = bool(position.get("stale"))

    for key, target in (
        ("physical_zone_id", "current_physical_zone_id"),
        ("physical_zone_source", "current_physical_zone_source"),
        ("target_zone_id", "target_zone_id"),
        ("target_zone_source", "target_zone_source"),
        ("planned_zone_ids", "planned_zone_ids"),
        ("planned_zones_source", "planned_zones_source"),
        ("channel_id", "current_channel_id"),
        ("channel_source", "current_channel_source"),
    ):
        if key in navigation:
            snapshot[target] = deepcopy(navigation.get(key))

    task = state.get("task") if isinstance(state.get("task"), dict) else {}
    totals = snapshot.get("totals")
    if not isinstance(totals, dict):
        totals = {}
        snapshot["totals"] = totals
    totals["task_progress_pct"] = task.get("progress_pct")
    totals["task_mowed_area_m2"] = task.get("mowed_area_m2")
    totals["task_area_m2"] = task.get("area_m2")
    totals["task_zone_ids"] = deepcopy(task.get("zone_ids") or [])
    totals["active_zone_id"] = task.get("active_zone_id")
    totals["task_progress_source"] = task.get("progress_source")
    totals["task_mowed_area_source"] = task.get("mowed_area_source")

    snapshot["mowing_progress"] = task.get("progress_pct")
    snapshot["mowing_progress_source"] = task.get("progress_source") or "canonical_task"
    snapshot["task_progress"] = task.get("progress_pct")
    snapshot["session_area"] = task.get("mowed_area_m2")
    snapshot["session_area_source"] = task.get("mowed_area_source") or "canonical_task"
    snapshot["task_mowed_area"] = task.get("mowed_area_m2")

    snapshot["canonical_owner"] = "canonical_mower_state"
    snapshot["canonical_schema_version"] = state.get("schema_version")
    snapshot["canonical_mode"] = state.get("mode")


def _run_authority(owner: Any, snapshot: dict[str, Any]) -> None:
    mqtt_position = owner._fresh_mqtt_position() if hasattr(owner, "_fresh_mqtt_position") else None
    cloud_position = snapshot.get("cloud_position")
    cloud_age = owner._private_endpoint_age("location") if hasattr(owner, "_private_endpoint_age") else None
    ledger_state = getattr(owner, "_zone_ledger_state", None)
    ledger_diagnostics = getattr(owner, "_zone_ledger_diagnostics", None)
    store = getattr(owner, "vendor_trail_store", None)
    owned_zone_ids = set(store.owned_zone_ids()) if store is not None and hasattr(store, "owned_zone_ids") else set()
    store_revision = getattr(store, "revision", None) if store is not None else None

    state = build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_diagnostics=ledger_diagnostics,
        vendor_owned_zone_ids=owned_zone_ids,
        vendor_store_revision=store_revision,
        mqtt_position=mqtt_position,
        cloud_position=cloud_position,
        cloud_position_age_s=cloud_age,
        mqtt_pose_max_age_s=float(MQTT_POSE_STALE_SECONDS),
    )
    _apply_public_state(snapshot, state)
    owner._canonical_state = state
    owner._canonical_diagnostics = canonical_diagnostics(state)


def install_canonical_authority_semantics() -> None:
    """Install the canonical projection after CycleEngine has resolved zones."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_canonical_authority_semantics_installed", False):
        return

    original_refresh = cls._refresh_zone_model

    def refresh_zone_model(self: Any, snapshot: dict[str, Any]) -> None:
        original_refresh(self, snapshot)
        try:
            _run_authority(self, snapshot)
        except Exception as err:  # noqa: BLE001
            self._canonical_diagnostics = {
                "schema_version": 1,
                "mode": "authoritative_v2",
                "public_owner": "canonical_mower_state",
                "error": f"{type(err).__name__}: {err}",
            }

    cls._refresh_zone_model = refresh_zone_model
    cls._canonical_authority_semantics_installed = True
