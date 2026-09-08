"""Regression contracts for Navimower 0.4.3-beta1 and later releases."""
import ast
from pathlib import Path

from diagnostics_contract import assert_cached_diagnostics_only

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/navimower"


def test_v043_beta1_contract() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    discovery = (COMPONENT / "maintenance_h5_discovery.py").read_text()
    ast.parse(diagnostics)
    ast.parse(discovery)
    # Historical research remains in-tree and read-only, not in the Download.
    assert_cached_diagnostics_only(diagnostics)
    assert 'raw_for_diagnostics.pop("maintenance", None)' in diagnostics
    for term in (
        "resetBlade", "resetKnife", "maintenanceMode", "enterMaintenance",
        "exitMaintenance", "cutHeight",
    ):
        assert term in discovery
    assert 'method="GET"' in discovery
    assert '"mutation_calls_executed": False' in discovery
    assert "client.call(" not in discovery
    notes = (ROOT / ".github/release-notes/0.4.3-beta1.md").read_text()
    assert "final beta" in notes
