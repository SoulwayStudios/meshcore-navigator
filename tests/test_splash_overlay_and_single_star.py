"""Unit tests for SplashOverlay first-run/subsequent-run behavior and single-star favorite rendering."""

import sys
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import ChannelInfo
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.splash_overlay import SplashOverlay


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_splash_overlay_first_run_ok_dismiss(qapp, tmp_path):
    """Verifies that first run displays the OK button and clicking it sets first_run_completed."""
    cfg_file = tmp_path / "config.json"
    config = AppConfig()
    config.first_run_completed = False
    config.save(cfg_file)

    overlay = SplashOverlay(config=config)
    assert overlay.first_run is True
    assert hasattr(overlay, "ok_btn")
    assert overlay.ok_btn.isVisibleTo(overlay)

    # Click OK button
    overlay.ok_btn.click()
    assert config.first_run_completed is True
    overlay.deleteLater()


def test_splash_overlay_subsequent_run_auto_dismiss(qapp, tmp_path):
    """Verifies that subsequent run operates as a loading mask without OK button and dismisses on map ready."""
    config = AppConfig()
    config.first_run_completed = True

    overlay = SplashOverlay(config=config)
    assert overlay.first_run is False
    assert not hasattr(overlay, "ok_btn")

    # Simulate min timer done and map ready
    overlay._min_timer_done = True
    overlay.on_map_ready()
    assert overlay._map_is_ready is True
    assert overlay._is_dismissing is True
    overlay.deleteLater()


def test_channel_single_star_rendering(qapp, tmp_path):
    """Verifies that favorite channels display exactly one star (★), fixing the double-star bug."""
    config = AppConfig()
    config.favorite_channels = ["cumbria"]
    storage = Storage(tmp_path / "single_star.db")
    storage.save_channel(ChannelInfo(1, "cumbria", is_favorite=True, is_pixoo_enabled=True))
    storage.save_channel(ChannelInfo(2, "northwest", is_favorite=False, is_pixoo_enabled=True))

    sidebar = Sidebar(storage=storage, config=config, show_contacts=False)
    sidebar.reload()

    # Find cumbria item
    items = [sidebar.channel_list.item(i) for i in range(sidebar.channel_list.count())]
    cumbria_item = next((item for item in items if "cumbria" in item.text().lower()), None)
    assert cumbria_item is not None

    # Must contain exactly ONE star in the text
    assert cumbria_item.text().count("★") == 1
    assert cumbria_item.text().startswith("★ ")

    # Must NOT have an icon set (which caused the second star)
    assert cumbria_item.icon().isNull() is True
