"""Unit tests for Dual-Dock Architecture, Glowing Layer Icons, Reforming Map Overlay, and Titlebar Version."""

import os
import time
from unittest.mock import patch
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QResizeEvent, QWindowStateChangeEvent

from meshcore_tray import __version__, __app_name__
from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage, NodeContact
from meshcore_tray.ui.nav_dock import NavDockWidget, MapLayerDockWidget, LayerButton, CycleFilterButton
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, ReformingMapOverlay
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication(["meshcore-test"])
    return app_instance


def test_map_layer_dock_buttons_and_signals(app):
    """Verifies that MapLayerDockWidget contains all 11 layer buttons with white/glowing-green states."""
    cfg = AppConfig()
    dock = MapLayerDockWidget(config=cfg)
    assert dock.width() == 48

    # Check 11 buttons
    buttons = [
        dock.btn_cycle_filter,
        dock.btn_rf_links,
        dock.btn_byte_paths,
        dock.btn_heatmap,
        dock.btn_orbitals,
        dock.btn_tropo,
        dock.btn_thunderstorm,
        dock.btn_adsb,
        dock.btn_scopes,
        dock.btn_rf_los,
        dock.btn_space_weather
    ]
    assert len(buttons) == 11
    for btn in buttons:
        assert btn.width() == 44
        assert btn.height() == 44

    # Watcher / Observer traffic button must default to ON
    assert dock.btn_rf_links.isChecked() is True

    # All other auxiliary layer toggle buttons must start unchecked (OFF) by default
    for btn in [
        dock.btn_byte_paths,
        dock.btn_heatmap,
        dock.btn_orbitals,
        dock.btn_tropo,
        dock.btn_thunderstorm,
        dock.btn_adsb,
        dock.btn_scopes,
        dock.btn_rf_los,
        dock.btn_space_weather
    ]:
        assert btn.isChecked() is False

    # Test toggling emits layer_toggled
    emitted = []
    dock.layer_toggled.connect(lambda k, state: emitted.append((k, state)))

    dock.btn_heatmap.click()
    assert ("activity_heatmap", True) in emitted

    dock.btn_adsb.click()
    assert ("adsb", True) in emitted

    # Test set_layer_active without re-emitting
    emitted.clear()
    dock.set_layer_active("tropo", True)
    assert dock.btn_tropo.isChecked() is True
    assert len(emitted) == 0

    dock.set_layer_active("tropo", False)
    assert dock.btn_tropo.isChecked() is False
    assert len(emitted) == 0


def test_layer_button_white_and_glowing_green_icons(app):
    """Verifies that LayerButton provides white vector icons when inactive and glowing green when active."""
    btn = LayerButton("rf_links", "Observer Traffic")
    assert btn._icon_inactive is not None
    assert btn._icon_active is not None
    assert not btn._icon_inactive.isNull()
    assert not btn._icon_active.isNull()

    # Inactive state:
    assert btn.isChecked() is False
    assert "#24262B" in btn.styleSheet()

    # Active state:
    btn.setChecked(True)
    assert "#10B981" in btn.styleSheet()
    assert "#34D399" in btn.styleSheet()


def test_nav_dock_favorite_contacts(app):
    """Verifies that NavDockWidget displays favorite avatar pills at the bottom and emits contact_selected."""
    cfg = AppConfig()
    dock = NavDockWidget(config=cfg)

    # Initially empty favorites
    assert dock.fav_divider.isVisible() is False
    assert dock.fav_layout.count() == 0

    contacts = [
        NodeContact(node_id="!1234abcd", alias="Alice Base", is_favorite=True),
        NodeContact(node_id="!5678ef01", alias="Bob Repeater [REP]", is_favorite=True),
    ]

    selected = []
    dock.contact_selected.connect(lambda nid: selected.append(nid))

    dock.show()
    dock.update_favorite_contacts(contacts)
    assert dock.fav_divider.isVisible() is True
    assert dock.fav_layout.count() == 2

    pill_1 = dock.fav_layout.itemAt(0).widget()
    assert pill_1.property("initials") == "AB"
    assert not pill_1.icon().isNull()
    assert "Alice Base" in pill_1.toolTip()
    assert "!1234abcd" in pill_1.toolTip()
    assert "User / Client Node" in pill_1.toolTip()

    pill_2 = dock.fav_layout.itemAt(1).widget()
    assert pill_2.property("initials") == "BR"
    assert not pill_2.icon().isNull()
    assert "Bob Repeater [REP]" in pill_2.toolTip()
    assert "!5678ef01" in pill_2.toolTip()
    assert "Repeater Node" in pill_2.toolTip()

    pill_1.click()
    assert selected == ["!1234abcd"]

    pill_2.click()
    assert selected == ["!1234abcd", "!5678ef01"]


def test_reforming_map_overlay_hud(app, tmp_path):
    """Verifies that ReformingMapOverlay shows and hides with the designated status message."""
    storage = Storage(tmp_path / "overlay_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        assert hasattr(mesh_map, "reforming_overlay")
        assert isinstance(mesh_map.reforming_overlay, ReformingMapOverlay)

        # Show reforming HUD
        mesh_map.reforming_overlay.show_reforming("Reforming Map View...")
        assert mesh_map.reforming_overlay.isVisible() is True
        assert mesh_map.reforming_overlay.desc_lbl.text() == "Reforming Map View..."

        # Hide reforming HUD
        mesh_map.reforming_overlay.hide_reforming()
        assert mesh_map.reforming_overlay.isVisible() is False


def test_watchdog_grace_period_on_resize(app, tmp_path):
    """Verifies that resizeEvent and changeEvent extend watchdog grace period to prevent false reloads."""
    storage = Storage(tmp_path / "watchdog_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        app.processEvents()
        mesh_map.resize(800, 500)
        app.processEvents()

        assert mesh_map._watchdog_grace_until > time.time()
        assert mesh_map._watchdog_unanswered == 0


def test_main_window_version_titlebar_and_dock_wiring(app, tmp_path):
    """Verifies that MainWindow title displays application name and version, and layer dock is attached."""
    storage = Storage(tmp_path / "mw_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage)

        # 1. Window title includes version
        assert win.windowTitle() == f"{__app_name__} v{__version__}"

        # 2. Main splitter has 4 panes with mesh_map at index 2
        assert win.main_splitter.count() == 4
        assert win.main_splitter.widget(2) == win.mesh_map

        # 3. Layer dock is attached inside mesh_map
        assert hasattr(win, "map_layer_dock")
        assert win.map_layer_dock == win.nav_dock.map_layers
        assert win.mesh_map.map_content_layout.itemAt(0).widget() == win.map_layer_dock
