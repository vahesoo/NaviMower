"""Regression guards for 0.4.3-beta29 map revision diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))

assert '"off_limit_areas": _polygon_diagnostics(' in diagnostics
assert '"area_m2": round(area, 4)' in diagnostics
assert '"centroid": [round(centroid[0], 4), round(centroid[1], 4)]' in diagnostics
assert '"edit_session_active": bool(' in diagnostics
assert '"map_version": map_version' in diagnostics

assert manifest["version"] in {
    "0.4.3-beta29", "0.4.3-beta30", "0.4.3-beta31", "0.4.3-beta32",
    "0.4.3-beta33", "0.4.3-beta34", "0.4.3-beta35", "0.4.3-beta36",
}
print("beta29 map revision diagnostics tests passed")
