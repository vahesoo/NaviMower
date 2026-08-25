from pathlib import Path


def test_beta35_schedule_status_contract() -> None:
    root = Path(__file__).parents[1]
    helper = (root / "custom_components/navimower/schedule_status.py").read_text()
    sensor = (root / "custom_components/navimower/sensor.py").read_text()
    manifest = (root / "custom_components/navimower/manifest.json").read_text()

    assert ('"version": "0.4.3-beta35"' in manifest or '"version": "0.4.3-beta36"' in manifest or '"version": "0.4.3-beta37"' in manifest or '"version": "0.4.3-beta38"' in manifest)
    assert '"queue": queue' in helper
    assert '"completed_zones": completed' in helper
    assert '"active_zone": active' in helper
    assert '"next_zone": upcoming[0] if upcoming else None' in helper
    assert '"upcoming_zones": upcoming' in helper
    assert '"round_index": diagnostics.get("round_index") or 1' in helper
    assert 'class NavimowerScheduleStatusSensor' in sensor
    assert 'super().__init__(coordinator, "navimower_schedule_status")' in sensor
    assert 'NavimowerScheduleStatusSensor(coordinator)' in sensor
    assert 'from .schedule_status import schedule_status_snapshot' in sensor
