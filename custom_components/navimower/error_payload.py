"""Safe inspection of Navimow get-hint-error-compress payloads."""
from __future__ import annotations

import base64
import binascii
import bz2
import ctypes
import ctypes.util
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
_MAX_TEXT_PREVIEW = 2_048
_ZSTD_CONTENTSIZE_UNKNOWN = (1 << 64) - 1
_ZSTD_CONTENTSIZE_ERROR = (1 << 64) - 2
_CODE_RE = re.compile(r"(?<!\d)(\d{4,6})(?!\d)")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")
_WORDS = (
    "error", "fault", "warn", "hint", "lift", "stuck", "dock", "motor",
    "camera", "sensor", "mow", "blade", "wheel", "charge", "position",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact(text: str, values: Iterable[str]) -> str:
    out = text
    for value in sorted(
        {str(item) for item in values if item and len(str(item)) >= 4},
        key=len,
        reverse=True,
    ):
        out = out.replace(value, "<redacted>")
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
    if len(compact) < 8 or len(compact) % 4 == 1 or not _BASE64_RE.fullmatch(compact):
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


def _compression(data: bytes) -> str | None:
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd"
    if data.startswith(b"BZh"):
        return "bzip2"
    if data.startswith(b"\xfd7zXZ\x00"):
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


def _zstd_stdlib(data: bytes) -> bytes:
    import compression.zstd as zstd  # type: ignore[import-not-found]

    return zstd.decompress(data)


def _zstd_third_party(data: bytes) -> bytes:
    import zstandard  # type: ignore[import-not-found]

    return zstandard.ZstdDecompressor().decompress(
        data, max_output_size=_MAX_DECODED_BYTES + 1
    )


def _load_libzstd() -> ctypes.CDLL:
    candidates = [
        ctypes.util.find_library("zstd"),
        "libzstd.so.1",
        "libzstd.so",
        "libzstd.dylib",
        "zstd.dll",
    ]
    errors: list[str] = []
    for candidate in dict.fromkeys(item for item in candidates if item):
        try:
            return ctypes.CDLL(candidate)
        except OSError as err:
            errors.append(f"{candidate}: {err}")
    detail = "; ".join(errors[-3:]) if errors else "library not found"
    raise ImportError(f"native libzstd unavailable: {detail}")


def _zstd_native(data: bytes) -> bytes:
    """Decompress one Zstandard frame through the system libzstd C API."""
    lib = _load_libzstd()
    size_t = ctypes.c_size_t
    source = ctypes.create_string_buffer(data)
    source_ptr = ctypes.cast(source, ctypes.c_void_p)

    lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, size_t]
    lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
    frame_size = int(lib.ZSTD_getFrameContentSize(source_ptr, len(data)))
    if frame_size == _ZSTD_CONTENTSIZE_ERROR:
        raise ValueError("invalid zstd frame")
    if frame_size == _ZSTD_CONTENTSIZE_UNKNOWN:
        if not hasattr(lib, "ZSTD_decompressBound"):
            raise ValueError("zstd frame size is unknown and decompress bound is unavailable")
        lib.ZSTD_decompressBound.argtypes = [ctypes.c_void_p, size_t]
        lib.ZSTD_decompressBound.restype = ctypes.c_ulonglong
        frame_size = int(lib.ZSTD_decompressBound(source_ptr, len(data)))
    if frame_size < 0 or frame_size > _MAX_DECODED_BYTES:
        raise ValueError("decoded output exceeds safety limit")

    capacity = max(frame_size, 1)
    destination = ctypes.create_string_buffer(capacity)
    lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, size_t, ctypes.c_void_p, size_t]
    lib.ZSTD_decompress.restype = size_t
    lib.ZSTD_isError.argtypes = [size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_getErrorName.argtypes = [size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p

    written = int(
        lib.ZSTD_decompress(
            ctypes.cast(destination, ctypes.c_void_p),
            capacity,
            source_ptr,
            len(data),
        )
    )
    if lib.ZSTD_isError(written):
        raw_name = lib.ZSTD_getErrorName(written)
        name = raw_name.decode("utf-8", errors="replace") if raw_name else "unknown"
        raise ValueError(f"libzstd decode failed: {name}")
    if written > _MAX_DECODED_BYTES:
        raise ValueError("decoded output exceeds safety limit")
    return destination.raw[:written]


def _zstd(data: bytes) -> tuple[bytes, str]:
    unavailable: list[str] = []
    for backend, decoder in (
        ("compression.zstd", _zstd_stdlib),
        ("zstandard", _zstd_third_party),
        ("libzstd", _zstd_native),
    ):
        try:
            return decoder(data), backend
        except (ImportError, ModuleNotFoundError) as err:
            unavailable.append(f"{backend}: {err}")
    detail = "; ".join(unavailable) if unavailable else "no backend available"
    raise RuntimeError(f"zstd decoder unavailable ({detail})")


def _decompress(data: bytes, kind: str) -> tuple[bytes, str | None]:
    if kind == "gzip":
        return _zlib(data, 16 + zlib.MAX_WBITS), None
    if kind == "zlib":
        return _zlib(data, zlib.MAX_WBITS), None
    if kind == "bzip2":
        out = bz2.BZ2Decompressor().decompress(data, max_length=_MAX_DECODED_BYTES + 1)
    elif kind == "xz":
        out = lzma.LZMADecompressor().decompress(data, max_length=_MAX_DECODED_BYTES + 1)
    elif kind == "zstd":
        out, backend = _zstd(data)
        if len(out) > _MAX_DECODED_BYTES:
            raise ValueError("decoded output exceeds safety limit")
        return out, backend
    else:
        raise ValueError(f"unsupported compression: {kind}")
    if len(out) > _MAX_DECODED_BYTES:
        raise ValueError("decoded output exceeds safety limit")
    return out, None


def _text(data: bytes) -> tuple[str | None, str | None]:
    codecs = ["utf-8"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        codecs.insert(0, "utf-16")
    if data.count(b"\x00") > max(2, len(data) // 8):
        codecs.extend(("utf-16-le", "utf-16-be"))
    for codec in codecs:
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        if not text:
            return text, codec
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        if printable / len(text) >= 0.80:
            return text, codec
    return None, None


def _codes(text: str) -> list[str]:
    return list(dict.fromkeys(_CODE_RE.findall(text)))[:128]


def _interesting(text: str, redactions: Iterable[str]) -> list[str]:
    found: list[str] = []
    for raw_line in text.splitlines() or [text]:
        line = " ".join(raw_line.strip().split())
        lowered = line.lower()
        if line and (any(word in lowered for word in _WORDS) or _CODE_RE.search(line)):
            line = _redact(line, redactions)
            if len(line) > 320:
                line = line[:317] + "..."
            if line not in found:
                found.append(line)
        if len(found) >= 40:
            break
    return found


def inspect_hint_error_payload(
    value: Any, *, redactions: Iterable[str] = ()
) -> dict[str, Any]:
    """Return bounded metadata and decoded content when the format is understood."""
    if value is None:
        return {"present": False}
    if isinstance(value, Mapping):
        return {
            "present": True,
            "raw_type": "dict",
            "layers": [],
            "decoded": {"kind": "json", "decoded_data": dict(value)},
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {
            "present": True,
            "raw_type": "list",
            "layers": [],
            "decoded": {"kind": "json", "decoded_data": list(value)},
        }

    if isinstance(value, str):
        source_text = value
        raw = value.encode("utf-8", errors="replace")
        raw_length = len(value)
        raw_type = "string"
        if len(value) > _MAX_INPUT_CHARS:
            return {
                "present": True,
                "raw_type": raw_type,
                "raw_length": raw_length,
                "raw_sha256": _sha(raw),
                "decode_error": "input exceeds safety limit",
            }
        direct_json = _json(value)
        if direct_json is not None:
            return {
                "present": True,
                "raw_type": raw_type,
                "raw_length": raw_length,
                "raw_sha256": _sha(raw),
                "layers": [{"operation": "json_text", "input_length": raw_length}],
                "decoded": {"kind": "json", "decoded_data": direct_json},
            }
    elif isinstance(value, (bytes, bytearray)):
        source_text = None
        raw = bytes(value)
        raw_length = len(raw)
        raw_type = "bytes"
        if len(raw) > _MAX_DECODED_BYTES:
            return {
                "present": True,
                "raw_type": raw_type,
                "raw_length": raw_length,
                "raw_sha256": _sha(raw),
                "decode_error": "input exceeds safety limit",
            }
    else:
        raw = repr(value).encode("utf-8", errors="replace")
        return {
            "present": True,
            "raw_type": type(value).__name__,
            "raw_length": len(raw),
            "raw_sha256": _sha(raw),
            "decode_error": "unsupported input type",
        }

    result: dict[str, Any] = {
        "present": True,
        "raw_type": raw_type,
        "raw_length": raw_length,
        "raw_sha256": _sha(raw),
        "layers": [],
    }
    if source_text is not None:
        wrapped = _base64(source_text)
        operation = "base64"
        if wrapped is None:
            wrapped = _hex(source_text)
            operation = "hex"
        if wrapped is not None:
            raw = wrapped
            result["layers"].append(
                {
                    "operation": operation,
                    "input_length": len(source_text),
                    "output_length": len(raw),
                    "output_sha256": _sha(raw),
                }
            )

    for _ in range(4):
        kind = _compression(raw)
        if kind is None:
            break
        try:
            decoded, backend = _decompress(raw, kind)
        except Exception as err:  # noqa: BLE001 - report the failed probe diagnostically.
            result["layers"].append(
                {
                    "operation": kind,
                    "input_length": len(raw),
                    "decode_error": f"{type(err).__name__}: {err}",
                }
            )
            break
        layer = {
            "operation": kind,
            "input_length": len(raw),
            "output_length": len(decoded),
            "output_sha256": _sha(decoded),
        }
        if backend:
            layer["backend"] = backend
        result["layers"].append(layer)
        raw = decoded

    decoded_meta: dict[str, Any] = {
        "length": len(raw),
        "sha256": _sha(raw),
        "magic_hex": raw[:16].hex(),
    }
    text, encoding = _text(raw)
    if text is not None:
        decoded_meta["text_encoding"] = encoding
        decoded_meta["text_length"] = len(text)
        codes = _codes(text)
        if codes:
            decoded_meta["code_candidates"] = codes
        snippets = _interesting(text, redactions)
        if snippets:
            decoded_meta["interesting_text"] = snippets
        parsed = _json(text)
        if parsed is not None:
            encoded = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode()
            decoded_meta["kind"] = "json"
            decoded_meta["json_serialized_length"] = len(encoded)
            if len(encoded) <= _MAX_JSON_EXPORT_BYTES:
                decoded_meta["decoded_data"] = parsed
            else:
                decoded_meta["decoded_data_omitted"] = "json exceeds export safety limit"
        else:
            decoded_meta["kind"] = "text"
            decoded_meta["text_preview"] = _redact(text[:_MAX_TEXT_PREVIEW], redactions)
    else:
        decoded_meta["kind"] = "binary"
        strings: list[str] = []
        for match in _PRINTABLE_RE.finditer(raw):
            item = match.group().decode("ascii")
            if any(word in item.lower() for word in _WORDS) or _CODE_RE.search(item):
                item = _redact(item[:320], redactions)
                if item not in strings:
                    strings.append(item)
            if len(strings) >= 40:
                break
        if strings:
            decoded_meta["interesting_strings"] = strings
            codes = _codes("\n".join(strings))
            if codes:
                decoded_meta["code_candidates"] = codes
    result["decoded"] = decoded_meta
    return result
