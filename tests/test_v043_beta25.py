import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta25_release_notes_remain_available():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta25.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta25")


def test_error_discovery_is_still_read_only_but_more_targeted():
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "PREFIX_BYTES = 768 * 1024" in source
    assert "MAX_PREFIX_REQUESTS = 14" in source
    assert "MAX_FULL_MATCHES = 8" in source
    assert "ACTION_CONTEXT_RADIUS = 12000" in source
    assert "GENERIC_NATIVE_CALL_RE" in source
    assert "COMMAND_FIELD_RE" in source
    assert "STRING_LITERAL_RE" in source
    assert "def _action_neighborhoods(" in source
    assert '"action_neighborhoods": action_neighborhoods' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_command_call_executed": False' in source
    assert '"public_unauthenticated_h5_only": True' in source
    assert 'method="GET"' in source


def test_action_neighborhood_targets_real_error_ui_and_dynamic_error_terms():
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert 'UI_LABELS = ("Clear and resume", "Reboot Mower", "Got it")' in source
    assert "action_anchors = list(dict.fromkeys([*UI_LABELS, *dynamic_terms]))" in source
    assert '"string_literals": literals' in source
    assert '"native_calls": native_calls' in source
    assert '"command_fields": command_fields' in source
    assert '"js_references"' in source
