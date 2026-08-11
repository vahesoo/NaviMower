"""Compose stable semantic runtime extensions in one explicit order.

Release numbers never belong in this production wiring. The individual modules
are named after the behavior they own so a beta can become stable without a
second code-consolidation pass.
"""
from __future__ import annotations

from .capability_extensions import install_capability_extensions
from .navigation_fallback import install_navigation_fallback
from .notification_feed import install_notification_feed
from .state_semantics import install_state_semantics


def install_runtime_extensions() -> None:
    """Install semantic extensions in the historically proven order."""
    install_state_semantics()
    install_capability_extensions()
    install_navigation_fallback()
    install_notification_feed()
