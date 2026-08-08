from pathlib import Path

root = Path('.')
release_test = root / 'tests/test_v034_release.py'
text = release_test.read_text(encoding='utf-8')
old = '''    assert manifest["version"] == "0.4.1-beta7"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta7.md").read_text()\n    assert notes.startswith("title: Navimower 0.4.1-beta7")\n    assert "mqtt callback" in notes.lower()\n    assert "structure_summary" in notes\n    assert "services.yaml" in notes\n'''
new = '''    assert manifest["version"] == "0.4.1-beta8"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()\n    assert notes.startswith("title: Navimower 0.4.1-beta8")\n    assert "Lifted" in notes\n    assert "isLifted" in notes\n    assert "get-hint-error-compress" in notes\n'''
if old not in text:
    raise SystemExit('current release-test beta7 anchor missing')
release_test.write_text(text.replace(old, new, 1), encoding='utf-8')

notes = root / '.github/release-notes/0.4.1-beta8.md'
notes.write_text('''title: Navimower 0.4.1-beta8

## Lifted state and error catalog

This beta adds the state/error observations confirmed during H215 lift-alarm testing.

- Private-cloud state `0302` is now exposed as **Lifted**.
- Official MQTT `state=isLifted` updates the mower state immediately instead of waiting for the next cloud poll.
- `/vehicle/vehicle/get-hint-error-compress` is now part of normal read-only polling and last-good caching, using a 30-second active and 60-second idle TTL.
- The cached compressed hint/error catalog is retained in raw diagnostic state for further reverse engineering.

No mower command behavior or Map Card code changes are included.
''', encoding='utf-8')
