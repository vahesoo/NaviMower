"""Stable-release contract for Navimower 0.4.2."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_v042_stable_release_notes_exist() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.2.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.2")
    for phrase in (
        "Download diagnostics",
        "regional private-cloud",
        "capability profile",
        "mark_notification_read",
        "navimower.resume",
        "partitionPlan",
    ):
        assert phrase.lower() in notes.lower()


def test_v042_support_diagnostics_remain_information_rich_and_sanitized() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    for section in (
        "entry",
        "mower",
        "connectivity",
        "private_cloud_region",
        "capabilities",
        "positioning",
        "telemetry",
        "settings",
        "map",
        "history",
        "problem_history",
        "latest_notification",
        "notification_center",
        "last_resume_command",
        "private_polling",
        "mqtt_health",
        "raw",
    ):
        assert f'"{section}"' in diagnostics

    assert "sanitize(deepcopy(raw))" in diagnostics
    assert "private_cloud_region_diagnostics(coordinator)" in diagnostics
    assert "build_capability_profile(data)" in diagnostics

    if "error_h5_discovery" in diagnostics:
        error_discovery = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
        assert "probe_error_h5" in diagnostics
        assert 'method="GET"' in error_discovery
        assert '"mutation_calls_executed": False' in error_discovery
        assert '"live_command_call_executed": False' in error_discovery
        assert '"notification_detail_call_executed": False' in error_discovery
        assert "client.call(" not in error_discovery
        assert "Authorization" not in error_discovery
        assert "Cookie" not in error_discovery
    elif "maintenance_h5_discovery" in diagnostics:
        maintenance_discovery = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
        assert 'method="GET"' in maintenance_discovery
        assert '"mutation_calls_executed": False' in maintenance_discovery
    else:
        assert "makes no extra vendor" in diagnostics

    sanitizer = (COMPONENT / "diagnostics_sanitize.py").read_text(encoding="utf-8")
    for secret in (
        "access_token",
        "refresh_token",
        "password",
        "vehicle_sn",
        "device_id",
        "latitude",
        "longitude",
        "ssid",
        "mac",
    ):
        assert f'"{secret}"' in sanitizer


def test_v042_production_architecture_stays_semantic() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    assert "install_capability_profile" in runtime
    assert "install_private_cloud_region" in runtime
    assert "install_beta" not in runtime
