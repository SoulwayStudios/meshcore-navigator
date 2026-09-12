"""Tests for clean system tray shutdown and database parking."""

import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.tray import SystemTray


@pytest.fixture
def app():
    instance = QApplication.instance()
    if not instance:
        instance = QApplication([])
    yield instance


def test_mainwindow_cleanup_is_idempotent(app, tmp_path):
    cfg = AppConfig()
    win = MainWindow(config=cfg, storage=None, radio_driver=None)
    mock_map = MagicMock()
    win.mesh_map = mock_map

    assert not win._is_cleaned_up
    win.cleanup()
    assert win._is_cleaned_up
    assert mock_map.cleanup.call_count == 1

    # Second call should be a no-op
    win.cleanup()
    assert mock_map.cleanup.call_count == 1
    win.close()


def test_mainwindow_close_event_hides_to_tray_when_tray_active(app, tmp_path):
    cfg = AppConfig()
    win = MainWindow(config=cfg, storage=None)
    mock_tray = MagicMock()
    mock_tray.isVisible.return_value = True
    win._tray_icon = mock_tray

    mock_event = MagicMock()
    win.closeEvent(mock_event)

    assert mock_event.ignore.called
    assert not mock_event.accept.called
    assert not win._shutdown_completed


@pytest.mark.asyncio
async def test_mainwindow_async_clean_exit(app, tmp_path):
    cfg = AppConfig()
    storage = MagicMock()
    storage.get_channel_unread_count.return_value = 0
    storage.get_messages.return_value = []
    radio = MagicMock()
    radio.stop = AsyncMock()
    pixoo = MagicMock()
    pixoo.stop = AsyncMock()
    gw = MagicMock()

    win = MainWindow(
        config=cfg,
        storage=storage,
        radio_driver=radio,
        pixoo_service=pixoo,
        gateway=gw,
    )

    await win._async_clean_exit()

    assert radio.stop.await_count == 1
    assert pixoo.stop.await_count == 1
    assert gw.stop_http_bridge.call_count == 1
    assert win._is_cleaned_up
    assert win._shutdown_completed
    storage.backup_database.assert_called_once_with(reason="app_quit")


def test_tray_quit_triggers_initiate_clean_exit(app):
    cfg = AppConfig()
    mock_win = MagicMock()
    tray = SystemTray(main_window=mock_win, config=cfg)

    tray._on_quit_requested()
    assert mock_win.initiate_clean_exit.called
