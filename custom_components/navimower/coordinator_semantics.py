"""Narrow semantic corrections layered on the main Navimower coordinator."""
from __future__ import annotations

from typing import Any

from .coordinator import NavimowCoordinator as _BaseNavimowCoordinator
from .coordinator import state_store


class NavimowCoordinator(_BaseNavimowCoordinator):
    """Coordinator with strict observed-mowing timestamp semantics.

    The vendor path-info-time ``startTime``/``endTime`` fields are coverage/cycle
    metadata. A map edit can rewrite ``startTime`` while resetting the edited
    zone to 0%, so those values must not become user-facing Last started / Last
    mowed timestamps. Real per-zone mowing timestamps are owned by the history
    manager and are written only from observed cutting poses.
    """

    def _build_zone_details(
        self,
        coverage: dict[str, Any] | None,
        global_height: int | None,
        cutting_height_supported: bool,
    ) -> list[dict[str, Any]]:
        details = super()._build_zone_details(
            coverage,
            global_height,
            cutting_height_supported,
        )
        for detail in details:
            detail.pop("last_started_at", None)
            detail.pop("last_mowed_at", None)
        return details


__all__ = ["NavimowCoordinator", "state_store"]
