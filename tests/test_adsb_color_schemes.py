"""Unit tests for ADS-B color schemes, settings integration, and aircraft photo lookups."""
import json
import pytest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig, AppColors
from meshcore_tray.core.adsb_service import ADSBService, AircraftPhotoWorker
from meshcore_tray.ui.mesh_map_widget import WebBridge, MeshMapWidget
from meshcore_tray.ui.settings_widget import SettingsWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test"])
    return app


def test_app_colors_adsb_defaults():
    """Verify default ADS-B color scheme settings."""
    colors = AppColors()
    assert colors.adsb_color_mode == "altitude"
    # Altitude defaults
    assert colors.adsb_alt_ground == "#FF00FF"
    assert colors.adsb_alt_low == "#FF0000"
    assert colors.adsb_alt_mid == "#0000FF"
    assert colors.adsb_alt_high == "#FFFFFF"
    # Aircraft type defaults
    assert colors.adsb_type_airliner == "#FFFFFF"
    assert colors.adsb_type_light == "#0000FF"
    assert colors.adsb_type_military == "#00FF00"
    assert colors.adsb_type_helicopter == "#FFFF00"
    assert colors.adsb_type_glider == "#FF00FF"
    # Distance ramp defaults
    assert colors.adsb_dist_close == "#FF0000"
    assert colors.adsb_dist_mid_close == "#FFA500"
    assert colors.adsb_dist_mid_far == "#FFFF00"
    assert colors.adsb_dist_far == "#00FF00"


def test_app_config_adsb_serialization(tmp_path):
    """Verify AppConfig saves and loads ADS-B color configurations."""
    cfg_file = tmp_path / "test_config.json"
    cfg = AppConfig()
    cfg.app_colors.adsb_color_mode = "type"
    cfg.app_colors.adsb_alt_ground = "#112233"
    cfg.app_colors.adsb_type_helicopter = "#445566"
    cfg.app_colors.adsb_dist_far = "#778899"
    cfg.save(cfg_file)

    loaded = AppConfig.load(cfg_file)
    assert loaded.app_colors.adsb_color_mode == "type"
    assert loaded.app_colors.adsb_alt_ground == "#112233"
    assert loaded.app_colors.adsb_type_helicopter == "#445566"
    assert loaded.app_colors.adsb_dist_far == "#778899"


def test_adsb_service_photo_worker():
    """Verify AircraftPhotoWorker parses Planespotters.net responses."""
    worker = AircraftPhotoWorker("406542")
    mock_resp_data = {
        "photos": [
            {
                "thumbnail_large": {"src": "https://t.plnspttrs.net/test_large.jpg"},
                "photographer": "Test Pilot",
                "link": "https://www.planespotters.net/photo/123",
                "aircraft_type": "Airbus A320",
                "airline": {"name": "Test Airways"}
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        received = []
        worker.photo_ready.connect(lambda h, p: received.append((h, p)))
        worker.run()

        assert len(received) == 1
        hex_code, photo_info = received[0]
        assert hex_code == "406542"
        assert photo_info["thumbnail"] == "https://t.plnspttrs.net/test_large.jpg"
        assert photo_info["photographer"] == "Test Pilot"
        assert photo_info["airline"] == "Test Airways"


def test_adsb_service_photo_caching():
    """Verify ADSBService caches aircraft photos and doesn't re-query cached hexes."""
    svc = ADSBService()
    svc._photo_cache["406542"] = {
        "thumbnail": "https://t.plnspttrs.net/cached.jpg",
        "photographer": "Cached Photographer",
        "link": "https://planespotters.net",
        "aircraft_type": "Boeing 737",
        "airline": "Cached Air"
    }

    received = []
    svc.photo_received.connect(lambda h, p: received.append((h, p)))
    svc.request_aircraft_photo("406542")

    assert len(received) == 1
    assert received[0][0] == "406542"
    assert received[0][1]["photographer"] == "Cached Photographer"


def test_web_bridge_adsb_signals():
    """Verify WebBridge emits adsb_color_mode_changed_signal and request_aircraft_photo_signal."""
    bridge = WebBridge()
    modes = []
    photos = []
    bridge.adsb_color_mode_changed_signal.connect(lambda m: modes.append(m))
    bridge.request_aircraft_photo_signal.connect(lambda h: photos.append(h))

    bridge.on_adsb_color_mode_changed("distance")
    assert modes == ["distance"]

    bridge.request_aircraft_photo("a1b2c3")
    assert photos == ["a1b2c3"]


def test_mesh_map_widget_adsb_color_mode(qapp, tmp_path):
    """Verify MeshMapWidget updates config when color mode changes."""
    cfg = AppConfig()
    cfg.app_colors.adsb_color_mode = "altitude"

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(config=cfg)

    widget._on_bridge_adsb_color_mode_changed("type")
    assert widget.config.app_colors.adsb_color_mode == "type"

    with patch.object(widget.adsb_service, "request_aircraft_photo") as mock_req:
        widget._on_bridge_request_aircraft_photo("406123")
        mock_req.assert_called_once_with("406123")


def test_settings_widget_adsb_color_pickers(qapp, tmp_path):
    """Verify SettingsWidget contains ADS-B color controls and syncs changes."""
    cfg = AppConfig()
    widget = SettingsWidget(config=cfg)

    # Check color pickers exist
    assert hasattr(widget, "combo_adsb_mode")
    assert hasattr(widget, "btn_col_adsb_alt_ground")
    assert hasattr(widget, "btn_col_adsb_type_airliner")
    assert hasattr(widget, "btn_col_adsb_dist_close")

    # Change mode combo
    widget.combo_adsb_mode.setCurrentIndex(1)  # "Aircraft Type"
    assert widget.config.app_colors.adsb_color_mode == "type"

    # Trigger color change
    widget._on_app_color_changed("adsb_alt_low", "#123456", widget.btn_col_adsb_alt_low)
    assert widget.config.app_colors.adsb_alt_low == "#123456"


def test_map_widget_html_contains_aircraft_click_enhancements():
    """Verify HTML template contains enlarged hitboxes, elevated panes, and cluster cycling."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE
    # Elevated marker pane above nodes at 600 and overlays at 400
    assert "adsbMarkersPane" in LEAFLET_HTML_TEMPLATE
    assert "adsbRadarPane" in LEAFLET_HTML_TEMPLATE
    assert "adsbTrailsPane" in LEAFLET_HTML_TEMPLATE
    # Hitbox extension in CSS
    assert ".adsb-plane-marker::before" in LEAFLET_HTML_TEMPLATE
    # Overlapping cluster resolution
    assert "getOverlappingAircraft" in LEAFLET_HTML_TEMPLATE
    assert "adsb-cluster-bar" in LEAFLET_HTML_TEMPLATE
    assert "selectAdsbAircraft" in LEAFLET_HTML_TEMPLATE
    assert "openAircraftTooltip" in LEAFLET_HTML_TEMPLATE

