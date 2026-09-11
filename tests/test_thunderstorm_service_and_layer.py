"""Tests for Live Thunderstorm Service, Radar Tile Sync, and Blitzortung Lightning Layer."""

import os
import pytest
from unittest.mock import patch, MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from meshcore_tray.config import AppConfig
from meshcore_tray.core.thunderstorm_service import ThunderstormService
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, get_leaflet_html
from meshcore_tray.ui.nav_dock import NavDockWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_thunderstorm_service_lifecycle(qapp):
    """Verifies that ThunderstormService starts and stops its polling worker properly."""
    service = ThunderstormService()
    assert not service.enabled
    assert service.latest_radar is None

    # Test radar metadata update signal
    emitted = []
    service.radar_updated.connect(lambda data: emitted.append(data))

    sample_radar = {
        "host": "https://tilecache.rainviewer.com",
        "radar": {
            "past": [{"time": 1700000000, "path": "/v2/radar/1700000000"}]
        }
    }
    service._on_radar_success(sample_radar)
    assert service.latest_radar == sample_radar
    assert len(emitted) == 1
    assert emitted[0]["host"] == "https://tilecache.rainviewer.com"

    # Enable and disable service
    with patch("meshcore_tray.core.thunderstorm_service.RainViewerFetchWorker") as mock_worker_cls:
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = False
        mock_worker_cls.return_value = mock_worker

        service.set_enabled(True)
        assert service.enabled is True
        mock_worker.start.assert_called_once()

        service.set_enabled(False)
        assert service.enabled is False


def test_leaflet_html_contains_thunderstorm_layer():
    """Verifies that Leaflet HTML contains the thunderstorm floating panel, radar layer, and Blitzortung WS."""
    html = get_leaflet_html()
    assert 'id="thunderstorm-panel"' in html
    assert 'id="strike-count-badge"' in html
    assert 'wss://ws7.blitzortung.org' in html
    assert 'thunderstormPane' in html
    assert 'lightningPane' in html
    assert 'setThunderstormVisible' in html
    assert 'updateThunderstormRadar' in html
    assert 'connectBlitzortung' in html
    assert 'disconnectBlitzortung' in html
    assert 'addLightningStrike' in html
    assert 'maxNativeZoom: 7' in html


def test_mesh_map_thunderstorm_toggle(qapp):
    """Verifies MeshMapWidget.set_thunderstorm updates internal state and watcher status."""
    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=None, config=config)

    assert not map_widget.show_thunderstorm

    # Toggle On
    map_widget.set_thunderstorm(True)
    assert map_widget.show_thunderstorm is True
    assert map_widget.thunderstorm_service.enabled is True
    assert "Thunderstorms" in map_widget.watcher_status.text()
    assert "RainViewer" in map_widget.watcher_status.text()

    # Toggle Off
    map_widget.set_thunderstorm(False)
    assert map_widget.show_thunderstorm is False
    assert map_widget.thunderstorm_service.enabled is False


def test_nav_dock_thunderstorm_button(qapp):
    """Verifies NavDockWidget has the thunderstorm layer button and emits layer_toggled."""
    dock = NavDockWidget()
    assert hasattr(dock, "btn_thunderstorm")
    assert dock.btn_thunderstorm.toolTip() == "Thunderstorm View"

    emitted_layers = []
    dock.layer_toggled.connect(lambda key, val: emitted_layers.append((key, val)))

    dock.btn_thunderstorm.setChecked(True)
    assert ("thunderstorm", True) in emitted_layers

    dock.btn_thunderstorm.setChecked(False)
    assert ("thunderstorm", False) in emitted_layers

    # Test programmatic set_layer_active
    dock.set_layer_active("thunderstorm", True)
    assert dock.btn_thunderstorm.isChecked() is True
    assert len(emitted_layers) == 2
