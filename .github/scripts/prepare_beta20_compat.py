from pathlib import Path

ROOT = Path.cwd()
test_path = ROOT / "tests" / "test_telemetry_stability.py"
text = test_path.read_text(encoding="utf-8")
old = '''    item._mqtt_progress_last_update = None\n    item._mqtt_area_last_update = None\n'''
new = '''    item._mqtt_progress_last_update = None\n    item._mqtt_route_progress_last_update = None\n    item._mqtt_work_progress_last_update = None\n    item._mqtt_task_progress_last_update = None\n    item._mqtt_area_last_update = None\n'''
if text.count(old) != 1:
    raise SystemExit(f"telemetry Mini fixture: expected one marker, got {text.count(old)}")
text = text.replace(old, new, 1)

old = '''item._mqtt_progress_last_update = time.monotonic()\nitem._mqtt_area_last_update = time.monotonic()\n'''
new = '''item._mqtt_progress_last_update = time.monotonic()\nitem._mqtt_task_progress_last_update = item._mqtt_progress_last_update\nitem._mqtt_area_last_update = time.monotonic()\n'''
if text.count(old) != 1:
    raise SystemExit(f"fresh task progress fixture: expected one marker, got {text.count(old)}")
text = text.replace(old, new, 1)
test_path.write_text(text, encoding="utf-8")
