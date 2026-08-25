"""Regression guards for the map revision diagnostics introduced in 0.4.3-beta29."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

assert '"off_limit_areas": _polygon_diagnostics(' in diagnostics
assert '"area_m2": round(area, 4)' in diagnostics
assert '"centroid": [round(centroid[0], 4), round(centroid[1], 4)]' in diagnostics
assert '"edit_session_active": bool(' in diagnostics
assert '"map_version": map_version' in diagnostics

print("map revision diagnostics regression checks passed")
