"""Tests for map custom right-click context menu and amateur radio utilities."""
import pytest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QMenu
from PyQt6.QtCore import QPoint

from meshcore_tray.ui.mesh_map_widget import (
    latlon_to_maidenhead,
    calculate_distance_and_bearing,
    WebBridge,
    MeshMapWidget,
)
from meshcore_tray.config import AppConfig


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test"])
    return app


def test_latlon_to_maidenhead():
    """Verify Maidenhead QTH grid locator calculation."""
    # Penrith / Cumbria UK
    grid = latlon_to_maidenhead(54.664, -2.754)
    assert grid.startswith("IO84")
    assert len(grid) == 6

    # London
    grid_london = latlon_to_maidenhead(51.5074, -0.1278)
    assert grid_london.startswith("IO91")

    # Extreme bounds check
    assert len(latlon_to_maidenhead(89.9, 179.9)) == 6
    assert len(latlon_to_maidenhead(-89.9, -179.9)) == 6
    assert latlon_to_maidenhead("invalid", "coords") == "Unknown"


def test_calculate_distance_and_bearing():
    """Verify distance and bearing calculation."""
    # Penrith to Carlisle (~28 km / ~17 miles north)
    penrith_lat, penrith_lon = 54.664, -2.754
    carlisle_lat, carlisle_lon = 54.895, -2.938

    dist_km, dist_mi, bearing = calculate_distance_and_bearing(
        penrith_lat, penrith_lon, carlisle_lat, carlisle_lon
    )
    assert 20 < dist_km < 35
    assert 12 < dist_mi < 22
    assert 320 < bearing < 350  # North-Northwest


def test_web_bridge_map_context_menu_signal():
    """Verify WebBridge emits map_context_menu_signal with lat, lon, x, y."""
    bridge = WebBridge()
    captured = []
    bridge.map_context_menu_signal.connect(lambda lat, lon, x, y: captured.append((lat, lon, x, y)))

    bridge.on_map_context_menu(54.664, -2.754, 150, 200)

    assert len(captured) == 1
    lat, lon, x, y = captured[0]
    assert pytest.approx(lat, 0.001) == 54.664
    assert pytest.approx(lon, 0.001) == -2.754
    assert x == 150
    assert y == 200


def test_mesh_map_context_menu_actions(qapp, tmp_path):
    """Verify MeshMapWidget context menu builds and triggers app actions."""
    cfg = AppConfig()
    cfg.meshcore.latitude = 54.664
    cfg.meshcore.longitude = -2.754
    cfg.meshcore.node_alias = "Test Station"

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(config=cfg)

    # Test ADS-B monitoring from context
    with patch.object(widget, "set_adsb_target") as mock_adsb_target:
        widget._context_monitor_adsb(54.7, -2.8, "IO84op")
        mock_adsb_target.assert_called_once()
        args = mock_adsb_target.call_args[0]
        assert "IO84op" in args[1]
        assert pytest.approx(args[2]) == 54.7
        assert pytest.approx(args[3]) == -2.8

    # Test Set Station Location from context
    widget._context_set_station_location(55.0, -3.0, "IO85aa")
    assert widget.config.meshcore.latitude == 55.0
    assert widget.config.meshcore.longitude == -3.0

    # Test Clear Traces & Pins
    with patch.object(widget, "clear_visualised_path") as mock_cvp, \
         patch.object(widget, "clear_repeater_neighbors") as mock_crn, \
         patch.object(widget, "clear_preview_packet_path") as mock_cpp:
        widget._context_clear_traces_and_pins()
        mock_cvp.assert_called_once()
        mock_crn.assert_called_once()
        mock_cpp.assert_called_once()

    # Test Menu Building and Action Inspection
    with patch.object(QMenu, "popup") as mock_popup:
        widget._show_map_context_menu(54.664, -2.754, 100, 100)
        mock_popup.assert_called_once()
