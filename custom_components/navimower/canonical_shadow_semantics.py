"""Temporary beta3 hook that runs Canonical v2 beside the public runtime."""
from __future__ import annotations

from typing import Any

from . import coordinator as _coordinator
from .canonical_state import build_canonical_shadow, canonical_diagnostics
from .const import MQTT_POSE_STALE_SECONDS


def _run_shadow(owner: Any, snapshot: dict[str, Any]) -> None:
    mqtt_position = owner._fresh_mqtt_position() if hasattr(owner, "_fresh_mqtt_position") else None
    cloud_position = snapshot.get("cloud_position")
    cloud_age = owner._private_endpoint_age("location") if hasattr(owner, "_private_endpoint_age") else None
    ledger_state = getattr(owner, "_zone_ledger_shadow_state", None)
    ledger_diagnostics = getattr(owner, "_zone_ledger_shadow_diagnostics", None)
    store = getattr(owner, "vendor_trail_store", None)
    owned_zone_ids = set(store.owned_zone_ids()) if store is not None and hasattr(store, "owned_zone_ids") else set()
    store_revision = getattr(store, "revision", None) if store is not None else None

    state = build_canonical_shadow(
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
    owner._canonical_shadow_state = state
    owner._canonical_shadow_diagnostics = canonical_diagnostics(state)


def install_canonical_shadow_semantics() -> None:
    """Install one temporary observation point after the resolved zone model."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_canonical_shadow_semantics_installed", False):
        return

    original_refresh = cls._refresh_zone_model

    def refresh_zone_model(self: Any, snapshot: dict[str, Any]) -> None:
        original_refresh(self, snapshot)
        try:
            _run_shadow(self, snapshot)
        except Exception as err:
            self._canonical_shadow_state = None
            self._canonical_shadow_diagnostics = {
                "schema_version": 1,
                "mode": "shadow_beta3",
                "public_owner": "legacy_runtime",
                "error": f"{type(err).__name__}: {err}",
            }

    cls._refresh_zone_model = refresh_zone_model
    cls._canonical_shadow_semantics_installed = True
