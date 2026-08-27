"""Initial setup semantics for private-cloud mower selection.

The legacy multi-mower beta deliberately showed the mower picker even when only
one unconfigured mower remained. Keep the picker for a real multi-mower choice,
but skip that redundant screen when the result is unambiguous.
"""
from __future__ import annotations

from typing import Any

from .config_flow_base import NavimowConfigFlow


_INSTALLED = False
_ORIGINAL_ASYNC_STEP_USER = NavimowConfigFlow.async_step_user


async def _async_step_user(
    self: NavimowConfigFlow,
    user_input: dict[str, Any] | None = None,
):
    result = await _ORIGINAL_ASYNC_STEP_USER(self, user_input)
    if (
        user_input is not None
        and result.get("type") == "form"
        and result.get("step_id") == "select_vehicle"
        and len(self._vehicles) == 1
    ):
        return await self._prepare_vehicle(self._vehicles[0])
    return result


def install_setup_flow_semantics() -> None:
    """Skip the mower picker only when one unconfigured mower remains."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowConfigFlow.async_step_user = _async_step_user
    _INSTALLED = True
