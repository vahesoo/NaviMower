from pathlib import Path
import json

ROOT = Path('.')
COMPONENT = ROOT / 'custom_components' / 'navimower'


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


ERROR_PAYLOAD = r'''"""Safe inspection helpers for Navimow's get-hint-error-compress payload."""
from __future__ import annotations

import base64
import binascii
import bz2
import hashlib
import json
import lzma
import re
import zlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_MAX_INPUT_CHARS = 1_000_000
_MAX_DECODED_BYTES = 2_000_000
_MAX_JSON_EXPORT_BYTES = 1_000_000
_MAX_TEXT_PREVIEW_CHARS = 2_048
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
_CODE_RE = re.compile(r"(?<!\\d)(\\d{4,6})(?!\\d)")
_PRINTABLE_RE = re.compile(rb"[\\x20-\\x7e]{4,}")
_WORDS = ("error", "fault", "warn", "hint", "lift", "stuck", "dock", "motor", "camera", "sensor", "mow", "blade", "wheel", "charge", "position")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact(text: str, redactions: Iterable[str]) -> str:
    out = text
    for item in sorted({str(v) for v in redactions if v and len(str(v)) >= 4}, key=len, reverse=True):
        out = out.replace(item, "<redacted>")
    return out


def _json(text: str) -> Any | None:
    stripped = text.strip()
    if stripped[:1] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return None


def _base64(text: str) -> bytes | None:
    compact = "".join(text.split())
    if len(compact) < 8 or not _BASE64_RE.fullmatch(compact) or len(compact) % 4 == 1:
        return None
    compact += "=" * ((4 - len(compact) % 4) % 4)
    try:
        if "-" in compact or "_" in compact:
            return base64.urlsafe_b64decode(compact.encode("ascii"))
        return base64.b64decode(compact.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None


def _hex(text: str) -> bytes | None:
    compact = "".join(text.split())
    if len(compact) < 8 or len(compact) % 2 or not _HEX_RE.fullmatch(compact):
        return None
    try:
        return bytes.fromhex(compact)
    except ValueError:
        return None


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\\x1f\\x8b"):
        return "gzip"
    if data.startswith(b"\\x28\\xb5\\x2f\\xfd"):
        return "zstd"
    if data.startswith(b"BZh"):
        return "bzip2"
    if data.startswith(b"\\xfd7zXZ\\x00"):
        return "xz"
    if len(data) >= 2 and (data[0] & 0x0F) == 8 and ((data[0] << 8) + data[1]) % 31 == 0:
        return "zlib"
    return None


def _zlib(data: bytes, wbits: int) -> bytes:
    decoder = zlib.decompressobj(wbits)
    out = decoder.decompress(data, _MAX_DECODED_BYTES + 1)
    if len(out) > _MAX_DECODED_BYTES or decoder.unconsumed_tail:
        raise ValueError("decoded output exceeds safety limit")
    out += decoder.flush()
    if len(out) > _MAX_DECODED_BYTES:
        raise ValueError("decoded output exceeds safety limit")
    return out


def _decompress(data: bytes, kind: str) -> bytes:
    if kind == "gzip":
        return _zlib(data, 16 + zlib.MAX_WBITS)
    if kind == "zlib":
        return _zlib(data, zlib.MAX_WBITS)
    if kind == "bzip2":
        out = bz2.BZ2Decompressor().decompress(data, max_length=_MAX_DECODED_BYTES + 1)
    elif kind == "xz":
        out = lzma.LZMADecompressor().decompress(data, max_length=_MAX_DECODED_BYTES + 1)
    elif kind == "zstd":
        try:
            import compression.zstd as zstd  # type: ignore[import-not-found]
            out = zstd.decompress(data)
        except ImportError:
            import zstandard  # type: ignore[import-not-found]
            out = zstandard.ZstdDecompressor().decompress(data, max_output_size=_MAX_DECODED_BYTES + 1)
    else:
        raise ValueError(kind)
    if len(out) > _MAX_DECODED_BYTES:
        raise ValueError("decoded output exceeds safety limit")
    return out


def _text(data: bytes) -> tuple[str | None, str | None]:
    codecs = ["utf-8"]
    if data.startswith((b"\\xff\\xfe", b"\\xfe\\xff")):
        codecs.insert(0, "utf-16")
    if data.count(b"\\x00") > max(2, len(data) // 8):
        codecs.extend(("utf-16-le", "utf-16-be"))
    for codec in codecs:
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        if not text or sum(ch.isprintable() or ch in "\\r\\n\\t" for ch in text) / len(text) >= 0.80:
            return text, codec
    return None, None


def _codes(text: str) -> list[str]:
    return list(dict.fromkeys(_CODE_RE.findall(text)))[:128]


def _interesting(text: str, redactions: Iterable[str]) -> list[str]:
    found = []
    for line in text.splitlines() or [text]:
        line = " ".join(line.strip().split())
        lower = line.lower()
        if line and (any(word in lower for word in _WORDS) or _CODE_RE.search(line)):
            line = _redact(line, redactions)
            found.append(line[:317] + "..." if len(line) > 320 else line)
        if len(found) >= 40:
            break
    return list(dict.fromkeys(found))


def inspect_hint_error_payload(value: Any, *, redactions: Iterable[str] = ()) -> dict[str, Any]:
    """Decode an error/hint payload conservatively and return bounded diagnostics."""
    if value is None:
        return {"present": False}
    if isinstance(value, Mapping):
        return {"present": True, "raw_type": "dict", "layers": [], "decoded": {"kind": "json", "decoded_data": dict(value)}}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"present": True, "raw_type": "list", "layers": [], "decoded": {"kind": "json", "decoded_data": list(value)}}

    source_text = value if isinstance(value, str) else None
    raw = value.encode("utf-8", errors="replace") if isinstance(value, str) else bytes(value) if isinstance(value, (bytes, bytearray)) else repr(value).encode()
    result = {"present": True, "raw_type": "string" if isinstance(value, str) else "bytes", "raw_length": len(value) if isinstance(value, str) else len(raw), "raw_sha256": _sha(raw), "layers": []}
    if isinstance(value, str) and len(value) > _MAX_INPUT_CHARS:
        result["decode_error"] = "input exceeds safety limit"
        return result
    if len(raw) > _MAX_DECODED_BYTES and not isinstance(value, str):
        result["decode_error"] = "input exceeds safety limit"
        return result

    if source_text is not None:
        direct = _json(source_text)
        if direct is not None:
            result["layers"] = [{"operation": "json_text", "input_length": len(source_text)}]
            result["decoded"] = {"kind": "json", "decoded_data": direct}
            return result
        decoded = _base64(source_text)
        op = "base64"
        if decoded is None:
            decoded = _hex(source_text)
            op = "hex"
        if decoded is not None:
            raw = decoded
            result["layers"].append({"operation": op, "input_length": len(source_text), "output_length": len(raw), "output_sha256": _sha(raw)})

    for _ in range(4):
        kind = _kind(raw)
        if kind is None:
            break
        try:
            decoded = _decompress(raw, kind)
        except Exception as err:  # noqa: BLE001
            result["layers"].append({"operation": kind, "input_length": len(raw), "decode_error": f"{type(err).__name__}: {err}"})
            break
        result["layers"].append({"operation": kind, "input_length": len(raw), "output_length": len(decoded), "output_sha256": _sha(decoded)})
        raw = decoded

    decoded = {"length": len(raw), "sha256": _sha(raw), "magic_hex": raw[:16].hex()}
    text, encoding = _text(raw)
    if text is not None:
        decoded["text_encoding"] = encoding
        decoded["text_length"] = len(text)
        codes = _codes(text)
        if codes:
            decoded["code_candidates"] = codes
        interesting = _interesting(text, redactions)
        if interesting:
            decoded["interesting_text"] = interesting
        parsed = _json(text)
        if parsed is not None:
            encoded = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode()
            decoded["kind"] = "json"
            decoded["json_serialized_length"] = len(encoded)
            if len(encoded) <= _MAX_JSON_EXPORT_BYTES:
                decoded["decoded_data"] = parsed
            else:
                decoded["decoded_data_omitted"] = "json exceeds export safety limit"
        else:
            decoded["kind"] = "text"
            decoded["text_preview"] = _redact(text[:_MAX_TEXT_PREVIEW_CHARS], redactions)
    else:
        decoded["kind"] = "binary"
        strings = []
        for match in _PRINTABLE_RE.finditer(raw):
            item = match.group().decode("ascii")
            if any(word in item.lower() for word in _WORDS) or _CODE_RE.search(item):
                strings.append(_redact(item[:320], redactions))
            if len(strings) >= 40:
                break
        if strings:
            decoded["interesting_strings"] = list(dict.fromkeys(strings))
            codes = _codes("\\n".join(strings))
            if codes:
                decoded["code_candidates"] = codes
    result["decoded"] = decoded
    return result
'''
(COMPONENT / 'error_payload.py').write_text(ERROR_PAYLOAD, encoding='utf-8')

API_INIT = '''"""Navimow private cloud API package (crypto + passport + client)."""
from __future__ import annotations

from typing import Any

from ..error_payload import inspect_hint_error_payload
from .client import NavimowAuthError, NavimowCloudClient as _NavimowCloudClient, NavimowError
from .passport import PassportAuthError, PassportError, Tokens


class NavimowCloudClient(_NavimowCloudClient):
    """Private-cloud client with safe inspection of compressed hint/error data."""

    def errors(self, sn: str, vehicle_type: int) -> dict[str, Any]:
        raw = super().errors(sn, vehicle_type)
        redactions = (sn, self.device_id, self.uid, self.tokens.access_token, self.tokens.refresh_token, self.tokens.uuid)
        return {
            "endpoint": "/vehicle/vehicle/get-hint-error-compress",
            "inspection": inspect_hint_error_payload(raw, redactions=redactions),
        }


__all__ = ["NavimowAuthError", "NavimowCloudClient", "NavimowError", "PassportAuthError", "PassportError", "Tokens"]
'''
(COMPONENT / 'api' / '__init__.py').write_text(API_INIT, encoding='utf-8')

manifest_path = COMPONENT / 'manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
if manifest.get('version') != '0.4.1-beta9':
    raise SystemExit(f"unexpected manifest version: {manifest.get('version')}")
manifest['version'] = '0.4.1-beta10'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

changelog = ROOT / 'CHANGELOG.md'
text = changelog.read_text(encoding='utf-8')
entry = '''## 0.4.1-beta10 - hint-error payload decoding diagnostics\n\n- Safely inspect `/vehicle/vehicle/get-hint-error-compress` instead of reducing the payload to length and SHA-256 only.\n- Detect plain JSON/text, Base64/URL-safe Base64 and hex wrapping, then gzip, zlib, bzip2, xz and Zstandard compression where a decoder is available.\n- Export bounded decode-layer metadata, hashes/magic bytes, code candidates and relevant text snippets; structured JSON still passes through existing diagnostics sanitization/redaction.\n- Enforce strict input/decompressed-size limits.\n- Keep beta9 Problem/Error behavior unchanged until field captures prove which values are active faults versus a static catalog.\n\n'''
if '## 0.4.1-beta10 ' not in text:
    changelog.write_text(text.replace('# Changelog\n\n', '# Changelog\n\n' + entry, 1), encoding='utf-8')

notes = '''title: Navimower 0.4.1-beta10\n\n## Hint-error payload decoding diagnostics\n\nThis beta investigates `/vehicle/vehicle/get-hint-error-compress` while keeping beta9 problem-state behavior unchanged.\n\n- Preserve and safely inspect the large hint/error response instead of reducing it to only a length/hash summary.\n- Detect plain JSON/text, Base64/URL-safe Base64 and hex wrapping, then gzip, zlib, bzip2, xz and Zstandard compression where a decoder is available.\n- Export decode-layer lengths/hashes, decoded magic bytes, code candidates and bounded error/fault-related text. Decoded JSON still passes through existing diagnostics sanitization/redaction.\n- Apply hard input and decompression limits.\n- **Problem** and **Error** entity semantics are intentionally unchanged; field testing comes first.\n\nCompare diagnostics captured during an active problem such as Lifted with a later normal/cleared state.\n'''
(ROOT / '.github' / 'release-notes' / '0.4.1-beta10.md').write_text(notes, encoding='utf-8')

TEST = r'''"""Regression contracts for Navimower 0.4.1-beta10."""
from __future__ import annotations

import base64
import gzip
import importlib.util
import json
from pathlib import Path
import zlib

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _probe():
    path = COMPONENT / "error_payload.py"
    spec = importlib.util.spec_from_file_location("navimower_beta10_error_payload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta10_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta10"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta10.md").read_text()
    assert "get-hint-error-compress" in notes and "Base64" in notes and "Zstandard" in notes
    assert "Problem" in notes and "Error" in notes


def test_hint_error_probe_decodes_base64_gzip_json() -> None:
    probe = _probe()
    payload = {"errors": [{"code": 6113, "message": "Failed to dock"}], "vehicle_sn": "SECRET-MOWER"}
    raw = base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
    result = probe.inspect_hint_error_payload(raw, redactions=("SECRET-MOWER",))
    assert [layer["operation"] for layer in result["layers"]] == ["base64", "gzip"]
    assert result["decoded"]["decoded_data"] == payload
    assert "6113" in result["decoded"]["code_candidates"]


def test_hint_error_probe_decodes_base64_zlib_json() -> None:
    probe = _probe()
    payload = {"code": 1105, "title": "Mowing motor stall protection"}
    raw = base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()
    result = probe.inspect_hint_error_payload(raw)
    assert [layer["operation"] for layer in result["layers"]] == ["base64", "zlib"]
    assert result["decoded"]["decoded_data"] == payload
    assert "1105" in result["decoded"]["code_candidates"]


def test_hint_error_probe_keeps_unknown_binary_bounded() -> None:
    probe = _probe()
    raw = base64.b64encode(b"\\x00\\x01\\x02fault=6108 mower stuck\\x00\\xff").decode()
    result = probe.inspect_hint_error_payload(raw)
    assert result["decoded"]["kind"] == "binary"
    assert "6108" in result["decoded"]["code_candidates"]


def test_api_wrapper_keeps_problem_logic_diagnostic_only() -> None:
    api = (COMPONENT / "api" / "__init__.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert "inspect_hint_error_payload" in api
    assert '"endpoint": "/vehicle/vehicle/get-hint-error-compress"' in api
    assert "authoritative problem signals are inline private errors, private 0302" in coordinator
    assert "hint catalog is diagnostic only" in coordinator
'''
(ROOT / 'tests' / 'test_v041_beta10.py').write_text(TEST, encoding='utf-8')

release_test = ROOT / 'tests' / 'test_v034_release.py'
release_text = release_test.read_text(encoding='utf-8')
for old, new in (
    ('assert manifest["version"] == "0.4.1-beta9"', 'assert manifest["version"] == "0.4.1-beta10"'),
    ('release-notes" / "0.4.1-beta9.md"', 'release-notes" / "0.4.1-beta10.md"'),
    ('assert notes.startswith("title: Navimower 0.4.1-beta9")', 'assert notes.startswith("title: Navimower 0.4.1-beta10")'),
    ('    assert "isLifted" in notes\n    assert "index2" in notes\n    assert "/downlink/#" in notes\n', '    assert "get-hint-error-compress" in notes\n    assert "Base64" in notes\n    assert "Problem" in notes\n'),
):
    if old not in release_text:
        raise SystemExit(f'missing release test anchor: {old!r}')
    release_text = release_text.replace(old, new, 1)
release_test.write_text(release_text, encoding='utf-8')

beta9_test = ROOT / 'tests' / 'test_v041_beta9.py'
beta9_text = beta9_test.read_text(encoding='utf-8')
old = '''def test_beta9_version_and_notes() -> None:\n    manifest = json.loads((COMPONENT / "manifest.json").read_text())\n    assert manifest["version"] == "0.4.1-beta9"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta9.md").read_text()\n'''
new = '''def test_beta9_version_and_notes() -> None:\n    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta9.md").read_text()\n'''
if old not in beta9_text:
    raise SystemExit('missing beta9 historical version anchor')
beta9_test.write_text(beta9_text.replace(old, new, 1), encoding='utf-8')

print('beta10 patch applied')
