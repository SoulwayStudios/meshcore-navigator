"""Unit tests for Staggered Map Asset Loading, Geometry Pause Shield, and Layer Defaults."""

import os
import time
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtGui import QResizeEvent

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage, NodeContact
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, ReformingMapOverlay


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication(["meshcore-test"])
    return app_instance


def test_staggered_stages_sequence(app, tmp_path):
    """Verifies that the map progresses through the 4 staggered stages and safely releases the cover."""
    storage = Storage(tmp_path / "stagger_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        mesh_map.web_view = MagicMock()
        mock_page = MagicMock()
        mesh_map.web_view.page.return_value = mock_page

        ready_emitted = []
        mesh_map.map_ready.connect(lambda: ready_emitted.append(True))

        # 1. Startup: Stage 1 active, cover visible
        assert mesh_map._initial_loading_active is True
        assert mesh_map._stagger_stage == 1
        assert mesh_map.reforming_overlay.isVisible() is True
        assert "Stage 1/4" in mesh_map.reforming_overlay.desc_lbl.text()
        assert mesh_map.reforming_overlay.title_lbl.text() == "INITIALIZING MESH MAP"
        assert mesh_map.reforming_overlay.progress_bar.value() == 25
        assert mesh_map.reforming_overlay.percent_lbl.text() == "25%"

        # 2. Stage 1 -> Stage 2 on map loaded
        mesh_map._on_map_loaded(True)
        assert mesh_map._page_ready is True
        assert mesh_map._stagger_stage == 2
        assert "Stage 2/4" in mesh_map.reforming_overlay.desc_lbl.text()
        assert mesh_map.reforming_overlay.progress_bar.value() == 50
        assert mesh_map.reforming_overlay.percent_lbl.text() == "50%"
        assert mesh_map._stagger_timer.isActive() is True

        # 3. Stage 2 -> Stage 3 on timer timeout (Geometry settled -> Load nodes)
        mesh_map._on_debounced_map_resize()
        mesh_map._stagger_timer.stop()
        mesh_map._on_stagger_timer_timeout()
        assert mesh_map._stagger_stage == 3
        assert "Stage 3/4" in mesh_map.reforming_overlay.desc_lbl.text()
        assert mesh_map.reforming_overlay.progress_bar.value() == 75
        assert mesh_map.reforming_overlay.percent_lbl.text() == "75%"
        assert mesh_map._stagger_timer.isActive() is True

        # 4. Stage 3 -> Stage 4 on timer timeout (Nodes rendered -> Radio sync)
        mesh_map._stagger_timer.stop()
        mesh_map._on_stagger_timer_timeout()
        assert mesh_map._stagger_stage == 4
        assert "Stage 4/4" in mesh_map.reforming_overlay.desc_lbl.text()
        assert mesh_map.reforming_overlay.progress_bar.value() == 100
        assert mesh_map.reforming_overlay.percent_lbl.text() == "100%"
        assert mesh_map._stagger_timer.isActive() is True

        # 5. Stage 4 -> Finish (Release overlay and emit map_ready)
        mesh_map._stagger_timer.stop()
        mesh_map._on_stagger_timer_timeout()
        assert mesh_map._initial_loading_active is False
        assert mesh_map._stagger_stage == 0
        assert mesh_map.reforming_overlay.isVisible() is False
        assert len(ready_emitted) == 1


def test_geometry_pause_shield_during_resize(app, tmp_path):
    """Verifies that geometry motion pauses updates without triggering failsafes or reloads."""
    storage = Storage(tmp_path / "pause_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        mesh_map.web_view = MagicMock()
        mock_page = MagicMock()
        mesh_map.web_view.page.return_value = mock_page

        # Complete startup so we test normal runtime pause
        mesh_map._initial_loading_active = False
        mesh_map._page_ready = True

        # Trigger motion pause
        mesh_map.pause_geometry_motion()
        assert mesh_map._geometry_in_motion is True
        assert mesh_map._watchdog_grace_until > time.time() + 30.0
        assert mesh_map._watchdog_unanswered == 0

        # Attempt to refresh map data while in motion: must be buffered in memory!
        mesh_map.refresh_map_data(debounce=False)
        assert mesh_map._pending_refresh is True

        # Watchdog check during motion must NOT count timeouts or panic
        mesh_map._check_renderer_watchdog()
        assert mesh_map._watchdog_unanswered == 0

        # Debounced settle
        mesh_map._on_debounced_map_resize()
        assert mesh_map._geometry_in_motion is False
        assert mesh_map._pending_refresh is False


def test_staggered_startup_pauses_when_resized(app, tmp_path):
    """Verifies that resizing during startup halts stage advancement until stillness."""
    storage = Storage(tmp_path / "stagger_resize_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        mesh_map.web_view = MagicMock()
        mesh_map._on_map_loaded(True)
        assert mesh_map._stagger_stage == 2

        # User resizes window while in Stage 2
        mesh_map.pause_geometry_motion()
        assert mesh_map._geometry_in_motion is True

        # Timer fires while geometry is still in motion
        mesh_map._on_stagger_timer_timeout()
        # Must stay in Stage 2 and wait!
        assert mesh_map._stagger_stage == 2
        assert mesh_map._stagger_timer.isActive() is True

        # Window finishes moving
        mesh_map._on_debounced_map_resize()
        assert mesh_map._geometry_in_motion is False

        # Timer fires after stillness -> advances to Stage 3
        mesh_map._on_stagger_timer_timeout()
        assert mesh_map._stagger_stage == 3


def test_auxiliary_layers_default_off_on_start(app, tmp_path):
    """Verifies that all 10 auxiliary layers are OFF by default when MeshMapWidget is created."""
    storage = Storage(tmp_path / "layers_test.db")
    # Even if config had them previously saved as True
    config = AppConfig()
    config.meshcore.map_show_path_modes = True
    config.meshcore.map_show_companion_orbitals = True
    config.meshcore.map_show_adsb = True
    config.meshcore.map_show_thunderstorm = True
    config.meshcore.map_show_space_weather = True

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        assert mesh_map.show_rf_links is True  # Watcher / Observer mode defaulted ON
        assert mesh_map.show_paths is False
        assert mesh_map.show_companion_orbitals is False
        assert mesh_map.show_adsb is False
        assert mesh_map.show_thunderstorm is False
        assert mesh_map.show_space_weather is False
        assert mesh_map.activity_heatmap_active is False


def test_stage_2_advances_automatically_without_user_input(app, tmp_path):
    """Verifies that Stage 2 does not hang if no user input is provided, auto-clearing motion idle."""
    storage = Storage(tmp_path / "stage2_autoadvance.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        mesh_map.show()
        mesh_map.web_view = MagicMock()
        mock_page = MagicMock()
        mesh_map.web_view.page.return_value = mock_page

        # Map finishes loading
        mesh_map._on_map_loaded(True)
        assert mesh_map._stagger_stage == 2
        assert mesh_map._geometry_in_motion is False

        # Even if initial resize happened >350ms ago
        mesh_map._geometry_in_motion = True
        mesh_map._last_geometry_motion_time = time.time() - 0.50

        # Stage 2 timeout fires without user resizing or restoring the window
        mesh_map._stagger_timer.stop()
        mesh_map._on_stagger_timer_timeout()

        # Must have automatically cleared motion and advanced to Stage 3!
        assert mesh_map._geometry_in_motion is False
        assert mesh_map._stagger_stage == 3
        assert "Stage 3/4" in mesh_map.reforming_overlay.desc_lbl.text()


def test_reforming_overlay_stage_pills_and_indeterminate_mode(app):
    """Verifies that stage pills update their styling and indeterminate resize works smoothly."""
    overlay = ReformingMapOverlay()
    assert len(overlay.stage_pills) == 4
    assert overlay.card.width() == 440
    assert "36px" in overlay.spinner_lbl.styleSheet()
    assert overlay.title_lbl.text() == "INITIALIZING MESH MAP"

    # Test Stage 1
    overlay.show_reforming("Stage 1/4: Engine", stage=1, percent=25)
    assert overlay.progress_bar.value() == 25
    assert overlay.percent_lbl.text() == "25%"
    # Pill 1 should be active (bright green)
    assert "#10B981" in overlay.stage_pills[0].styleSheet()

    # Test Stage 3
    overlay.show_reforming("Stage 3/4: Nodes", stage=3, percent=75)
    assert overlay.progress_bar.value() == 75
    # Pill 1 & 2 should be completed
    assert "#064E3B" in overlay.stage_pills[0].styleSheet()
    assert "#064E3B" in overlay.stage_pills[1].styleSheet()
    # Pill 3 active
    assert "#10B981" in overlay.stage_pills[2].styleSheet()
    # Pill 4 pending
    assert "#1E293B" in overlay.stage_pills[3].styleSheet()

    # Test Indeterminate (general resize reforming)
    overlay.show_reforming("Reforming Map View...")
    assert overlay.progress_bar.minimum() == 0
    assert overlay.progress_bar.maximum() == 0
    assert overlay.percent_lbl.isVisible() is False
    overlay.hide_reforming()
    assert overlay.isVisible() is False
