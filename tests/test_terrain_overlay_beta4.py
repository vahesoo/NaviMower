"""Regression coverage for the persistent LiDAR terrain overlay runtime."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_terrain_overlay_archive_cache_and_runtime_contract() -> None:
    code = textwrap.dedent(
        r'''
        import importlib.util
        import io
        import json
        from pathlib import Path
        import sys
        import tempfile
        import types
        import zipfile

        root = Path.cwd()

        def module(name):
            value = types.ModuleType(name)
            sys.modules[name] = value
            return value

        module("custom_components")
        navimower = module("custom_components.navimower")
        navimower.__path__ = [str(root / "custom_components" / "navimower")]

        api = module("custom_components.navimower.api")
        class NavimowAuthError(Exception):
            pass
        api.NavimowAuthError = NavimowAuthError

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.terrain_overlay",
            root / "custom_components" / "navimower" / "terrain_overlay.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        def webp(payload):
            body = bytes(payload)
            size = len(body) + 4
            return b"RIFF" + size.to_bytes(4, "little") + b"WEBP" + body

        metadata = {
            "terrain_view_image_name": "merge2d_test.webp",
            "terrain_adapt_image_name": "adapt_test.webp",
            "terrain_adapt_max_height": "1.52841",
            "grid_map_size": "0.06",
            "width": 2275,
            "height": 2325,
            "minX": -44.967,
            "maxX": 30.899,
            "minY": -33.391,
            "maxY": 44.121,
            "minZ": -1.644,
            "maxZ": 2.962,
            "pixel_per_meter": 30.0,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("upload/merge2d_test.json", json.dumps(metadata))
            archive.writestr("upload/merge2d_test.webp", webp(b"terrain"))
            archive.writestr("upload/adapt_test.webp", webp(b"elevation"))
        archive_bytes = buffer.getvalue()

        manifest, images = target.decode_terrain_archive(
            archive_bytes,
            vendor_file_version="1789401490",
            map_version="1789383132",
            downloaded_utc="2026-09-14T18:00:00+00:00",
        )
        assert manifest["reference_frame"] == "mower_local_xy"
        assert manifest["extent"] == {
            "min_x": -44.967,
            "max_x": 30.899,
            "min_y": -33.391,
            "max_y": 44.121,
        }
        assert manifest["pixel_per_meter"] == 30.0
        assert manifest["grid_map_size_m"] == 0.06
        assert manifest["adapt_max_height_m"] == 1.52841
        assert set(images) == {"terrain", "elevation"}

        temp_root = Path(tempfile.mkdtemp())
        class Config:
            def path(self, *parts):
                return str(temp_root.joinpath(*parts))
        class Hass:
            config = Config()
        class Entry:
            entry_id = "entry-test"
            data = {"model": "i2 LiDAR"}
        class Client:
            version = "1789401490"
            def call(self, path, request):
                assert path == target.TERRAIN_ENDPOINT
                assert request["type"] in {2, "2"}
                return {"version": self.version, "url": "https://vendor.invalid/signed?secret=do-not-store"}
        class Coordinator:
            hass = Hass()
            entry = Entry()
            sn = "TEST-SN"
            client = Client()
            vehicle_type = 160000001
            data = {
                "model": "i2 LiDAR",
                "vehicle_type": vehicle_type,
                "map": {"map_version": "1789383132"},
            }

        manager = target.TerrainOverlayManager(Coordinator())
        manager._write_cache_blocking(manifest, images)
        assert ".storage/navimower_terrain/entry-test" in manager.cache_dir.as_posix()
        assert manager.manifest_path.is_file()
        assert "signed" not in manager.manifest_path.read_text()
        assert "secret" not in manager.manifest_path.read_text()

        restored = target.TerrainOverlayManager(Coordinator())
        restored._load_cache_blocking()
        frontend = restored.frontend_metadata()
        assert frontend["supported"] is True
        assert frontend["available"] is True
        assert frontend["version"] == "1789401490"
        assert frontend["terrain"]["api_path"].endswith("/entry-test/terrain")
        assert frontend["elevation"]["api_path"].endswith("/entry-test/elevation")
        assert restored.image_resource("terrain")[0].suffix == ".webp"

        # Same file version is metadata-only: the ~2 MB resource is not fetched again.
        def should_not_download(url):
            raise AssertionError("same terrain version must not redownload")
        target._download_signed_resource = should_not_download
        restored.refresh_blocking(Coordinator.data)
        assert restored.diagnostics()["last_error"] is None

        # A new vendor version atomically replaces the cached images.
        Coordinator.client.version = "1789401491"
        restored._last_attempt_mono = None
        target._download_signed_resource = lambda url: archive_bytes
        restored.refresh_blocking(Coordinator.data)
        assert restored.frontend_metadata()["supported"] is True
        assert restored.frontend_metadata()["version"] == "1789401491"
        assert restored.image_resource("terrain")[0].name == "terrain-1789401491.webp"
        assert not (restored.cache_dir / "terrain-1789401490.webp").exists()

        # Download errors must never echo the signed URL into diagnostics.
        class DownloadFailure(Exception):
            pass
        Coordinator.client.version = "1789401492"
        restored._last_attempt_mono = None
        def fail_download(url):
            raise DownloadFailure(url)
        target._download_signed_resource = fail_download
        restored.refresh_blocking(Coordinator.data)
        error = restored.diagnostics()["last_error"]
        assert error == "DownloadFailure"
        assert "secret" not in error
        assert restored.frontend_metadata()["version"] == "1789401491"

        # Capability is resource-driven, not model-driven. A future/unknown
        # model becomes supported after a valid type-2 terrain package is seen.
        class PlainEntry:
            entry_id = "entry-plain"
            data = {"model": "H2"}
        class PlainClient:
            calls = 0
            version = "1789401493"
            def call(self, path, request):
                self.calls += 1
                assert path == target.TERRAIN_ENDPOINT
                return {"version": self.version, "url": "https://vendor.invalid/terrain"}
        class PlainCoordinator:
            hass = Hass()
            entry = PlainEntry()
            sn = "PLAIN-SN"
            vehicle_type = 160000001
            client = PlainClient()
            data = {
                "model": "H2",
                "vehicle_type": vehicle_type,
                "map": {"map_version": "1789383132"},
            }
        plain = target.TerrainOverlayManager(PlainCoordinator())
        assert plain.supported is False
        assert plain.frontend_metadata()["supported"] is False
        target._download_signed_resource = lambda url: archive_bytes
        plain.refresh_blocking(PlainCoordinator.data)
        assert PlainCoordinator.client.calls >= 1
        assert plain.supported is True
        assert plain.frontend_metadata()["supported"] is True
        assert plain.diagnostics()["support_source"] == "vendor_type2_resource"
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_terrain_overlay_is_runtime_cache_not_probe_storage() -> None:
    terrain = (ROOT / "custom_components" / "navimower" / "terrain_overlay.py").read_text()
    map_api = (ROOT / "custom_components" / "navimower" / "map_api.py").read_text()
    init = (ROOT / "custom_components" / "navimower" / "__init__.py").read_text()

    assert "navimower_diagnostics" not in terrain
    assert '".storage", "navimower_terrain"' in terrain
    assert 'TERRAIN_CHECK_TTL_SECONDS = 15 * 60' in terrain
    assert 'TERRAIN_UNAVAILABLE_TTL_SECONDS = 6 * 60 * 60' in terrain
    assert '"/mowerbot/vehicle/common/get-iot-file"' in terrain
    assert '"/api/navimower/terrain/{entry_id}/{kind}"' in map_api
    assert '"terrain_overlay": terrain_overlay' in map_api
    assert "TerrainOverlayManager(coordinator)" in init
    assert "terrain_overlay.start()" in init
    assert "await terrain_overlay.async_stop()" in init
    assert "TerrainOverlayManager.async_remove_all" in init
