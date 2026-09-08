"""Shared assertions for the current support-report contract.

Keep historical runtime regressions, but do not force obsolete diagnostic
crawlers or inert source-marker strings back into production code.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def load_redactor():
    spec = importlib.util.spec_from_file_location(
        "navimower_contract_redactor", COMPONENT / "diagnostics_sanitize.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_cached_diagnostics_only(source: str) -> None:
    """Check imports/calls/returned objects, not comments pretending to be code."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "h5_discovery" not in (node.module or "")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"probe_error_h5", "probe_maintenance_h5", "async_export_raw_data"}
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"async_add_executor_job", "urlopen", "async_send"}
        if isinstance(node, ast.Dict):
            emitted_keys = {
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            assert not emitted_keys.intersection({
                "maintenance_h5_discovery", "error_h5_discovery", "command_discovery"
            })
    handler = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_get_config_entry_diagnostics"
    )
    returns = [node for node in ast.walk(handler) if isinstance(node, ast.Return)]
    assert len(returns) == 2, "loaded and unloaded reports must both be protected"
    for node in returns:
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.func, ast.Name) and node.value.func.id == "sanitize"
        assert any(keyword.arg == "exclude_keys" for keyword in node.value.keywords)
    assert '"cached_only": True' in source
    assert "_RETIRED_H5_DISCOVERY_HISTORY" not in source
