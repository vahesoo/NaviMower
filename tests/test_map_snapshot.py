"""Regression coverage for backend map snapshot rendering and wiring."""
from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
RENDER = COMPONENT / "map_snapshot_render.py"
MANAGER = COMPONENT / "map_snapshot.py"
IMAGE_PLATFORM = COMPONENT / "image.py"
SETUP = COMPONENT / "__init__.py"
SERVICES = COMPONENT / "services.py"
SERVICES_YAML = COMPONENT / "services.yaml"


def _render_module():
    spec = importlib.util.spec_from_file_location("navimower_map_snapshot_render", RENDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_renderer_produces_png_with_map_cycle_and_pose() -> None:
    renderer = _render_module()
    source = {
        "map": {
            "zones": [
                {
                    "id": 5,
                    "name": "Back yard",
                    "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]],
                }
            ],
            "off_limit_areas": [[[4, 3], [5, 3], [5, 4], [4, 4]]],
            "vf_off_areas": [[[7, 5], [8, 5], [8, 6], [7, 6]]],
            "station": {"x": 1.0, "y": 1.0},
        },
        "gate_areas": [
            {
                "name": "Gate",
                "polygon": [[9.0, 2.0], [10.0, 2.0], [10.0, 3.0], [9.0, 3.0]],
            }
        ],
        "vendor_segments": [[[1.0, 2.0], [4.0, 2.0], [7.0, 2.0]]],
        "fallback_session": {
            "points": [
                [1000, 1.0, 6.0, 0.0, "mowing", 4, 5, 5],
                [2000, 4.0, 6.0, 0.0, "mowing", 4, 5, 5],
                [3000, 7.0, 6.0, 0.0, "returning", 5, 0, 5],
            ],
            "segment_starts_ms": [1000],
        },
        "live_route_segments": [[[7.0, 2.0], [8.0, 2.5]]],
        "mowing_path_width_m": 0.25,
        "position": {"x": 8.0, "y": 2.5, "heading": 0.4},
        "show_zone_labels": True,
    }

    png = renderer.render_snapshot_png(source, size=640)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(BytesIO(png))
    assert image.size == (640, 640)
    assert image.mode == "RGB"
    colors = image.getcolors(maxcolors=640 * 640)
    assert colors is not None
    assert len(colors) > 8



def test_snapshot_uses_svg_derived_mower_artwork() -> None:
    renderer = _render_module()
    source = {
        "map": {
            "zones": [
                {
                    "id": 1,
                    "name": "Test",
                    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                }
            ],
            "station": {"x": 1.0, "y": 1.0},
        },
        "position": {"x": 5.0, "y": 5.0, "heading": 0.0},
        "mowing_path_width_m": 0.4,
        "show_zone_labels": False,
    }

    png = renderer.render_snapshot_png(source, size=640)
    image = Image.open(BytesIO(png)).convert("RGB")
    colors = image.getcolors(maxcolors=640 * 640)
    assert colors is not None
    palette = {color for _count, color in colors}

    # #E38A51 is the orange nose/body accent from the Map Card H2 SVG source.
    assert (227, 138, 81) in palette
    assert len(renderer.H2_SNAPSHOT_SVG_PATHS) >= 10
    assert len(renderer._mower_art_layers()) >= 10


def test_snapshot_selects_model_specific_mower_artwork() -> None:
    renderer = _render_module()

    assert renderer._mower_art_key({"model_family": "h2", "model": "H215"}) == "h2"
    assert renderer._mower_art_key({"model_family": "i1", "model": "i108"}) == "i_light"
    assert renderer._mower_art_key({"model_family": "i2_awd", "model": "i208 AWD"}) == "i_light"
    assert renderer._mower_art_key({"model_family": "i2_lidar", "model": "i215 LiDAR"}) == "i2_lidar"
    assert renderer._mower_art_key({"model_family": "x3", "model": "X390"}) == "x3"
    assert renderer._mower_art_key({"model_family": "x4", "model": "X450"}) == "x4"
    assert renderer._mower_art_key({"model_family": "h5", "model": "H510 Pro"}) == "h2"

    assert renderer._raster_mower_art("i_light").size == (86, 120)
    assert renderer._raster_mower_art("i2_lidar").size == (82, 120)
    assert renderer._raster_mower_art("x3").size == (96, 120)
    assert renderer._raster_mower_art("x4").size == (84, 120)


def test_snapshot_png_changes_with_mower_model_artwork() -> None:
    renderer = _render_module()
    base = {
        "map": {
            "zones": [
                {
                    "id": 1,
                    "name": "Test",
                    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                }
            ],
        },
        "position": {"x": 5.0, "y": 5.0, "heading": 0.0},
        "mowing_path_width_m": 0.4,
        "show_zone_labels": False,
    }

    h2 = renderer.render_snapshot_png({**base, "model_family": "h2", "model": "H215"}, size=640)
    x3 = renderer.render_snapshot_png({**base, "model_family": "x3", "model": "X390"}, size=640)
    i1 = renderer.render_snapshot_png({**base, "model_family": "i1", "model": "i108"}, size=640)

    assert h2 != x3
    assert h2 != i1
    assert x3 != i1

def test_cutting_segments_do_not_turn_return_route_into_mowed_area() -> None:
    renderer = _render_module()
    segments = renderer._session_cutting_segments(
        {
            "points": [
                [1000, 0.0, 0.0, 0.0, "mowing", 4, 5, 5],
                [2000, 1.0, 0.0, 0.0, "mowing", 4, 5, 5],
                [3000, 2.0, 0.0, 0.0, "returning", 5, 0, 5],
                [4000, 3.0, 0.0, 0.0, "returning", 5, 0, 5],
            ],
            "segment_starts_ms": [1000],
        }
    )
    assert segments == [[[0.0, 0.0], [1.0, 0.0]]]


def test_snapshot_wiring_keeps_cache_and_manual_refresh_semantics() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    image = IMAGE_PLATFORM.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")
    services = SERVICES.read_text(encoding="utf-8")
    services_yaml = SERVICES_YAML.read_text(encoding="utf-8")

    assert "ACTIVE_REFRESH_SECONDS = 60.0" in manager
    assert 'reason="active_interval"' in manager
    assert 'reason="state_change"' in manager
    assert 'reason="cold_request"' in image
    assert "if image is None:" in image
    assert "Platform.IMAGE" in setup
    assert 'SERVICE_REFRESH_MAP_SNAPSHOT = "refresh_map_snapshot"' in services
    assert 'reason="manual"' in services
    assert "require_fresh=True" in services
    assert "refresh_map_snapshot:" in services_yaml
    assert "return only after the cached image has been replaced" in services_yaml
    assert '"render_reason": self._manager.render_reason' in image
    assert '"render_age_seconds"' in image
    assert "current_physical_zone_id" in manager
    assert '"model_family": str(' in manager
    assert '"model": str(data.get("model") or "")' in manager
    render = RENDER.read_text(encoding="utf-8")
    assert "raw vendor postureTheta in radians" in render
    assert "H2_SNAPSHOT_SVG_PATHS" in render
    assert "_svg_path_polygons" in render
    assert "angle = math.pi / 2.0 - heading" in render
    assert "math.cos(angle)" in render



def test_dark_snapshot_has_distinct_palette_and_unicode_zone_labels() -> None:
    renderer = _render_module()
    name = "Mägi õue · Üsküdar · Øst · Łąka"
    source = {
        "map": {
            "zones": [
                {
                    "id": 1,
                    "name": name,
                    "polygon": [[0, 0], [12, 0], [12, 8], [0, 8]],
                }
            ],
            "station": {"x": 1.0, "y": 1.0},
        },
        "position": {"x": 6.0, "y": 4.0, "heading": 0.0},
        "mowing_path_width_m": 0.4,
        "show_zone_labels": True,
    }

    light = renderer.render_snapshot_png(source, size=640, theme="light")
    dark = renderer.render_snapshot_png(source, size=640, theme="dark")
    assert light != dark

    light_image = Image.open(BytesIO(light)).convert("RGB")
    dark_image = Image.open(BytesIO(dark)).convert("RGB")
    assert light_image.getpixel((0, 0)) == renderer._LIGHT_PALETTE["background"][:3]
    assert dark_image.getpixel((0, 0)) == renderer._DARK_PALETTE["background"][:3]

    font = renderer._snapshot_font(20)
    for value in ("Mägi", "õue", "Üsküdar", "Øst", "Łąka"):
        bbox = font.getbbox(value)
        assert bbox[2] > bbox[0]
        assert bbox[3] > bbox[1]


def test_dark_snapshot_entity_is_disabled_by_default_and_refresh_is_enabled_only() -> None:
    image = IMAGE_PLATFORM.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")
    services = SERVICES.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")

    assert "class NavimowMapSnapshotDarkImage" in image
    assert '_attr_name = "Map snapshot dark"' in image
    assert "_attr_entity_registry_enabled_default = False" in image
    assert 'get_map_snapshot_manager(coordinator, "dark")' in image

    assert "def active(self) -> bool:" in manager
    assert "if not self.active:" in manager
    assert 'attribute = (' in manager
    assert '"map_snapshot_dark_manager"' in manager
    assert "def active_map_snapshot_managers" in manager

    assert "active_map_snapshot_managers(coordinator)" in services
    assert "await asyncio.gather(" in services
    assert "No enabled Navimower map snapshot image entity is available." in services
    assert "map_snapshot_dark_manager" in setup



def test_snapshot_uses_packaged_noto_sans_for_international_labels() -> None:
    renderer = _render_module()
    path = renderer._packaged_snapshot_font_path()
    assert path is not None
    assert "noto" in path.lower()

    font = renderer._snapshot_font(24)
    family, _style = font.getname()
    assert "Noto Sans" in family

    replacement = bytes(font.getmask("\ufffd"))
    question = bytes(font.getmask("?"))
    for character in ("ä", "ö", "ü", "õ", "Ø", "ı", "ş", "Ł", "ą"):
        mask = bytes(font.getmask(character))
        assert mask
        assert mask != replacement
        assert mask != question

    source = {
        "map": {
            "zones": [
                {
                    "id": 1,
                    "name": "Mägi õue · Üsküdar · Øst · Łąka",
                    "polygon": [[0, 0], [12, 0], [12, 8], [0, 8]],
                }
            ]
        },
        "show_zone_labels": True,
    }
    png = renderer.render_snapshot_png(source, size=640, theme="dark")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
