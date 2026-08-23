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

# Download diagnostics are now cache-only: no H5 error/maintenance/report probe
# and no Resume research payload may run merely because the user downloads JSON.
assert "probe_error_h5" not in diagnostics
assert "probe_maintenance_h5" not in diagnostics
assert "resume_command_diagnostics" not in diagnostics
assert "ERROR_DISCOVERY_TIMEOUT_SECONDS" not in diagnostics
assert '"diagnostics_source": "home_assistant_download_cached_only"' in diagnostics
assert 'raw_for_diagnostics.pop("maintenance", None)' in diagnostics

# Custom-area research needs the exact local polygon plus useful comparison
# metadata without changing the robot map itself.
assert '"off_limit_areas": _polygon_diagnostics(' in diagnostics
assert '"area_m2": round(area, 4)' in diagnostics
assert '"centroid": [round(centroid[0], 4), round(centroid[1], 4)]' in diagnostics
assert '"edit_session_active": bool(' in diagnostics
assert '"map_version": map_version' in diagnostics

assert manifest["version"] == "0.4.3-beta29"
print("beta29 map revision diagnostics tests passed")
