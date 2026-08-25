from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / 'custom_components/navimower'


def test_queue_api():
    s = (C / 'services.py').read_text()
    c = (C / 'navimower_schedule.py').read_text()
    y = (C / 'services.yaml').read_text()
    assert 'SERVICE_SET_SCHEDULE_QUEUE' in s
    assert 'async_set_custom_queue' in c
    assert 'set_schedule_queue:' in y
    assert '"order_mode"' in c and '"custom_queue"' in c
