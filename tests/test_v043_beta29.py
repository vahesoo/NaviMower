"""Regression guards for 0.4.3-beta29 map revision diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

semantics = (ROOT / "custom_components/navimower/coordinator_semantics.py").read_text()
diagnostics = (ROOT / "custom_components/navimower/diagnostics.py").read_text()
manifest = json.loads(
    (ROOT / "custom_components/navimower/manifest.json").read_text()
)

# index2 is the fast revision signal. A real mapVersion transition must clear the
# geometry key and force the slower location/map-list endpoints due immediately.
assert 'previous_version = previous.get("mapVersion")' in semantics
assert 'current.get("mapVersion")' in semantics
assert 'self._map_cache_key = None' in semantics
assert 'for dependent in ("location", "map_list")' in semantics
assert 'status["last_attempt_mono"] = None' in semantics
assert 'self._map_geometry["map_version"] = str(map_version)' in semantics

# Download diagnostics are cache-only. Historical H5 marker text may remain so
# old source-level beta regression tests still document the retired discovery,
# but there must be no executable probe/import path in the diagnostics function.
assert "resume_command_diagnostics(coordinator)" not in diagnostics
assert "error_command_discovery = await" not in diagnostics
assert "maintenance_h5_discovery = await" not in diagnostics
assert '"cached_only": True' in diagnostics
assert 'raw_for_diagnostics.pop("maintenance", None)' in diagnostics
assert '"removed_from_download": True' in diagnostics

# Custom-area research needs the exact local polygon plus useful comparison
# metadata without changing the robot map itself.
assert '"off_limit_areas": _polygon_diagnostics(' in diagnostics
assert '"area_m2": round(area, 4)' in diagnostics
assert '"centroid": [round(centroid[0], 4), round(centroid[1], 4)]' in diagnostics
assert '"edit_session_active": bool(' in diagnostics
assert '"map_version": map_version' in diagnostics

assert manifest["version"] in {"0.4.3-beta29", "0.4.3-beta30", "0.4.3-beta31"}
print("beta29 map revision diagnostics tests passed")
