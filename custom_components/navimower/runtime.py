"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .capability_profile import install_capability_profile
from .capability_semantics import install_capability_semantics
from .georeference_diagnostics_semantics import install_georeference_diagnostics_semantics
from .georeference_semantics import install_georeference_semantics
from .georeference_static_anchor_semantics import install_georeference_static_anchor_semantics
from .navigation_fallback import install_navigation_fallback
from .notification_feed import install_notification_feed
from .private_cloud_region import install_private_cloud_region
from .raw_mqtt_semantics import install_raw_mqtt_semantics
from .schedule_ownership_semantics import install_schedule_ownership_semantics
from .schedule_pause_semantics import install_schedule_pause_semantics
from .schedule_round_semantics import install_schedule_round_semantics
from .setup_flow_semantics import install_setup_flow_semantics
from .state_semantics import install_state_semantics
from .zone_entity_cleanup import install_zone_entity_cleanup


def install_runtime_extensions() -> None:
    """Install semantic extensions in the historically proven order."""
    install_state_semantics()
    install_private_cloud_region()
    install_capability_extensions()
    install_capability_profile()
    install_capability_semantics()
    install_georeference_semantics()
    install_georeference_static_anchor_semantics()
    install_georeference_diagnostics_semantics()
    install_navigation_fallback()
    install_notification_feed()
    install_raw_mqtt_semantics()
    install_schedule_pause_semantics()
    install_schedule_ownership_semantics()
    install_schedule_round_semantics()
    install_setup_flow_semantics()
    install_zone_entity_cleanup()
