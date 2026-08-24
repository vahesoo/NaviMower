from pathlib import Path

def test_beta36_version_and_constants():
    assert '0.4.3-beta36' in Path('custom_components/navimower/manifest.json').read_text()
    c=Path('custom_components/navimower/const.py').read_text()
    assert 'OPT_SCHEDULE_CUSTOM_QUEUE' in c and 'SCHEDULE_ORDER_CUSTOM' in c

def test_custom_queue_keeps_duplicates_and_slots():
    s=Path('custom_components/navimower/navimower_schedule.py').read_text()
    assert '_custom_queue_entries' in s
    assert 'completed_queue_slots' in s
    assert 'active_queue_slot' in s
    assert 'queue_slot=queue_slot' in s

def test_status_is_slot_aware():
    s=Path('custom_components/navimower/schedule_status.py').read_text()
    assert 'completed_slots' in s and '"slot": slot' in s

def test_options_expose_order_mode():
    s=Path('custom_components/navimower/config_flow.py').read_text()
    assert 'async_step_navimower_schedule_order' in s
    assert 'Automatic order' in s and 'Custom order' in s
