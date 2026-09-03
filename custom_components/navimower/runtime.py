"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .capability_profile import install_capability_profile
from .capability_semantics import install_capability_semantics
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
    # Geodetic metre conversion must be installed before any georeference
    # fitting/validation layer captures or uses the helpers.
    install_georeference_geodesy_semantics()
    install_georeference_semantics()
    install_georeference_static_anchor_semantics()
    install_georeference_x3_bias_semantics()
    # Some firmwares zero current local X/Y/heading while docked but retain the
    # previous GPS point. Reject that inconsistent pair before it can enter
    # learning or invalidate an explicit vendor map transform.
    install_georeference_pose_semantics()
    # Static vendor ties keep rotation/local geometry authoritative. A mature,
    # tightly validated cloud XY/GPS fit may refine translation only. X3 is
    # already excluded because its RTK-anchor/bias path owns translation.
    install_georeference_translation_refinement_semantics()
    # Wrap the vendor/local georeference chain so persisted spherical fits are
    # migrated and every fresh transform records the WGS84 ellipsoid model.
    install_georeference_geodesy_state_semantics()
    # European static orthophotos use the ETRS89/ETRF cartographic frame. Apply
    # the small EPSG:8366 translation only after the WGS84 ellipsoid pipeline is
    # complete. The cartographic layer keeps local X/Y, rotation and scale intact
    # and explicitly excludes X3's vendor RTK-anchor/bias path.
    install_georeference_cartographic_semantics()
    # Candidate diagnostics compare raw vendor/cloud GPS with the cartographic
    # active map, so normalize candidates into the same presentation frame before
    # reporting residual vectors.
    install_georeference_diagnostics_frame_semantics()
    install_georeference_diagnostics_semantics()
    # Underlay providers may use a different geographic registration from the
    # mower's active presentation frame. Export provider-ready frames only after
    # every georeference layer above has finished composing the active transform.
    install_georeference_frames_semantics()
    install_navigation_fallback()
    install_notification_feed()
    install_raw_mqtt_semantics()
    install_schedule_pause_semantics()
    install_schedule_ownership_semantics()
    install_schedule_round_semantics()
    install_setup_flow_semantics()
    install_zone_entity_cleanup()
