import pytest
import os
import tempfile
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication(["meshcore-test"])
    return app_instance


def test_map_mixer_title_and_companion_node_heading(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()
        config.meshcore.node_alias = "M7NCY-Portable"

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            win = MainWindow(config=config, storage=storage)

        # 1. Window title check
        assert win.windowTitle() == "MESHCORE NAVIGATOR"

        # 2. Top bar logo label check
        assert hasattr(win, "companion_node_lbl")
        assert "NAVIGATOR" in win.windowTitle()
        
        # Verify companion node label
        assert win.companion_node_lbl is not None
        assert "M7NCY-Portable" in win.companion_node_lbl.text()
        assert "📡" in win.companion_node_lbl.text()
        assert "Companion Node: M7NCY-Portable" in win.companion_node_lbl.toolTip()

        # Update alias and test dynamic update
        config.meshcore.node_alias = "M7NCY-BaseStation"
        win._update_companion_node_display()
        assert "M7NCY-BaseStation" in win.companion_node_lbl.text()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_last_active_channel_persistence_and_startup_restoration(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()

        # Pre-seed last active channel in storage
        storage.set_app_state("last_active_channel", "#cumbria")
        assert storage.get_app_state("last_active_channel") == "#cumbria"

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            win = MainWindow(config=config, storage=storage)

        # Verify it started with #cumbria
        assert win.current_channel == "#cumbria"
        assert win.sidebar.active_channel == "#cumbria"
        assert win.chat_widget.current_channel == "#cumbria"

        # Switch to another channel
        win._on_channel_selected("#scotland")
        assert win.current_channel == "#scotland"
        assert storage.get_app_state("last_active_channel") == "#scotland"
        assert config.last_active_channel == "#scotland"

        # Launch another window with the same storage/config to simulate app restart
        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            win2 = MainWindow(config=config, storage=storage)

        assert win2.current_channel == "#scotland"
        assert win2.sidebar.active_channel == "#scotland"
        assert win2.chat_widget.current_channel == "#scotland"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
