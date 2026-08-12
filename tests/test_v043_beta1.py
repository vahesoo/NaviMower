"""Regression contracts for Navimower 0.4.3-beta1 and later 0.4.3 betas."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/navimower"


def test_v043_beta1_contract() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.4.3")
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    discovery = (COMPONENT / "maintenance_h5_discovery.py").read_text()
    ast.parse(diagnostics)
    ast.parse(discovery)
    assert "probe_maintenance_h5" in diagnostics
    assert '"maintenance_h5_discovery": maintenance_h5_discovery' in diagnostics
    assert '"raw_component_maintenance"' in diagnostics
    for term in (
        "resetBlade",
        "resetKnife",
        "maintenanceMode",
        "enterMaintenance",
        "exitMaintenance",
        "cutHeight",
    ):
        assert term in discovery
    assert 'method="GET"' in discovery
    assert '"mutation_calls_executed": False' in discovery
    assert "client.call(" not in discovery
    notes = (ROOT / ".github/release-notes/0.4.3-beta1.md").read_text()
    assert "final beta" in notes
