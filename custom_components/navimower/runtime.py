"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .capability_profile import install_capability_profile
from .navigation_fallback import install_navigation_fallback
from .notification_feed import install_notification_feed
from .private_cloud_region import install_private_cloud_region
from .schedule_pause_semantics import install_schedule_pause_semantics
from .setup_flow_semantics import install_setup_flow_semantics
from .state_semantics import install_state_semantics
from .zone_entity_cleanup import install_zone_entity_cleanup


def install_runtime_extensions() -> None:
    """Install semantic extensions in the historically proven order."""
    install_state_semantics()
    install_private_cloud_region()
    install_capability_extensions()
    install_capability_profile()
    install_navigation_fallback()
    install_notification_feed()
    install_schedule_pause_semantics()
    install_setup_flow_semantics()
    install_zone_entity_cleanup()
