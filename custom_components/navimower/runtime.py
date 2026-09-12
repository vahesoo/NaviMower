"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .capability_profile import install_capability_profile
from .capability_semantics import install_capability_semantics
from .completion_semantics import install_completion_semantics
from .georeference_cartographic_semantics import install_georeference_cartographic_semantics
from .georeference_diagnostics_frame_semantics import (
    install_georeference_diagnostics_frame_semantics,
)
from .georeference_diagnostics_semantics import install_georeference_diagnostics_semantics
from .georeference_frames_semantics import install_georeference_frames_semantics
from .georeference_geodesy_semantics import (
    install_georeference_geodesy_semantics,
    install_georeference_geodesy_state_semantics,
)
from .georeference_pose_semantics import install_georeference_pose_semantics
from .georeference_semantics import install_georeference_semantics
from .georeference_static_anchor_semantics import install_georeference_static_anchor_semantics
from .georeference_translation_refinement_semantics import (
    install_georeference_translation_refinement_semantics,
)
from .georeference_x3_bias_semantics import install_georeference_x3_bias_semantics
from .history_performance import install_history_performance
from .map_api_performance import install_map_api_performance
from .mowing_pause_status import install_mowing_pause_status
from .navigation_fallback import install_navigation_fallback
from .navigation_intent import install_navigation_intent
from .notification_feed import install_notification_feed
from .private_cloud_region import install_private_cloud_region
from .raw_mqtt_semantics import install_raw_mqtt_semantics
from .schedule_ownership_semantics import install_schedule_ownership_semantics
from .schedule_pause_semantics import install_schedule_pause_semantics
from .schedule_queue_semantics import install_schedule_queue_semantics
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
    install_georeference_geodesy_semantics()
    install_georeference_semantics()
    install_georeference_static_anchor_semantics()
    install_georeference_x3_bias_semantics()
    install_georeference_pose_semantics()
    install_georeference_translation_refinement_semantics()
    install_georeference_geodesy_state_semantics()
    install_georeference_cartographic_semantics()
    install_georeference_diagnostics_frame_semantics()
    install_georeference_diagnostics_semantics()
    install_georeference_frames_semantics()
    install_navigation_fallback()
    install_navigation_intent()
    install_history_performance()
    install_completion_semantics()
    install_map_api_performance()
    install_notification_feed()
    install_mowing_pause_status()
    install_raw_mqtt_semantics()
    install_schedule_pause_semantics()
    install_schedule_ownership_semantics()
    install_schedule_round_semantics()
    install_schedule_queue_semantics()
    install_setup_flow_semantics()
    install_zone_entity_cleanup()
