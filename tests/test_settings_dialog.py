"""Unit tests for SettingsDialog radio presets and configuration."""

import sys
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.ui.settings_widget import SettingsDialog
from meshcore_tray.drivers.mock_driver import MockRadioDriver


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_settings_dialog_radio_presets(qapp, tmp_path):
    config = AppConfig()
    storage = Storage(tmp_path / "settings_test.db")
    driver = MockRadioDriver(config=config, storage=storage)

    dlg = SettingsDialog(config=config, storage=storage, radio_driver=driver)

    # 1. Switch to UK Narrow preset
    uk_idx = dlg.preset_combo.findData("uk_narrow")
    dlg.preset_combo.setCurrentIndex(uk_idx)
    dlg._on_preset_changed()

    assert dlg.freq_spin.value() == 869.618
    assert dlg.bw_combo.currentData() == 62.5
    assert dlg.sf_combo.currentData() == 8
    assert dlg.cr_combo.currentData() == "4/5"
    assert dlg.tx_spin.value() == 22

    # 2. Switch to EU 868 Standard preset
    eu_idx = dlg.preset_combo.findData("eu_868")
    dlg.preset_combo.setCurrentIndex(eu_idx)
    dlg._on_preset_changed()

    assert dlg.freq_spin.value() == 868.125
    assert dlg.bw_combo.currentData() == 250.0
    assert dlg.sf_combo.currentData() == 7
    assert dlg.cr_combo.currentData() == "4/5"

    # 3. Apply to driver
    dlg._apply_radio_to_hardware()

    assert config.meshcore.frequency_mhz == 868.125
    assert config.meshcore.bandwidth_khz == 250.0
    assert config.meshcore.spreading_factor == 7


def test_settings_dialog_sidebar_navigation(qapp, tmp_path):
    """Verifies that the settings dialog uses a left sidebar navigation with 7 categories."""
    config = AppConfig()
    storage = Storage(tmp_path / "nav_test.db")
    dlg = SettingsDialog(config=config, storage=storage)

    assert hasattr(dlg, "nav_list")
    assert hasattr(dlg, "stack")
    assert dlg.nav_list.count() == 8
    assert dlg.stack.count() == 8

    # Switch to "🎨 App UI Colors" (index 3)
    dlg.nav_list.setCurrentRow(3)
    assert dlg.stack.currentIndex() == 3

    # Switch to "🔀 Channels & Filters" (index 2)
    dlg.nav_list.setCurrentRow(2)
    assert dlg.stack.currentIndex() == 2

    # Switch to "ℹ️ About & Support" (index 7)
    dlg.nav_list.setCurrentRow(7)
    assert dlg.stack.currentIndex() == 7


def test_settings_pixoo_channel_filter_persistence(qapp, tmp_path):
    """Verifies that unchecking and checking Pixoo channel filter persists to both config and SQLite."""
    from meshcore_tray.core.models import ChannelInfo

    cfg_file = tmp_path / "config.json"
    config = AppConfig()
    config.save(cfg_file)

    storage = Storage(tmp_path / "filter_test.db")
    # Add two channels
    storage.save_channel(ChannelInfo(0, "Public", is_favorite=False, is_pixoo_enabled=True))
    storage.save_channel(ChannelInfo(1, "cumbria", is_favorite=True, is_pixoo_enabled=True))

    dlg = SettingsDialog(config=config, storage=storage)

    # In channel table, row 1 is #cumbria. Uncheck Pixoo display!
    pixoo_chk_cumbria = dlg.channel_table.cellWidget(1, 1)
    assert pixoo_chk_cumbria.isChecked() is True
    pixoo_chk_cumbria.setChecked(False)

    # Save
    dlg._save_and_close()

    # Verify config has cumbria = False
    assert config.pixoo.channel_filters.get("cumbria") is False

    # Verify SQLite was updated
    cumbria_ch = storage.get_channel("cumbria")
    assert cumbria_ch is not None
    assert cumbria_ch.is_pixoo_enabled is False

    # Reopen dialog and verify it loads as unchecked
    dlg2 = SettingsDialog(config=config, storage=storage)
    pixoo_chk_reopened = dlg2.channel_table.cellWidget(1, 1)
    assert pixoo_chk_reopened.isChecked() is False


def test_settings_app_colors_customization(qapp, tmp_path):
    """Verifies that customizing colors in the App UI Colors settings section updates AppColors."""
    config = AppConfig()
    storage = Storage(tmp_path / "colors_test.db")

    dlg = SettingsDialog(config=config, storage=storage)

    # Modify colors and dot size
    dlg.slider_dot_size.setValue(45)  # 4.5px
    dlg.btn_col_fav_chan.current_hex = "#FF0055"
    dlg.btn_col_fav_user.current_hex = "#FFCC00"
    dlg.btn_col_send.current_hex = "#2563EB"
    dlg.btn_col_radio_conn.current_hex = "#10B981"
    dlg.btn_col_sync_status.current_hex = "#06B6D4"
    dlg.btn_col_snr.current_hex = "#EAB308"
    dlg.btn_col_map_rep.current_hex = "#9333EA"
    dlg.btn_col_map_comp.current_hex = "#06B6D4"
    dlg.btn_col_watch_start.current_hex = "#F97316"
    dlg.btn_col_watch_end.current_hex = "#EF4444"
    dlg.btn_col_msg_start.current_hex = "#22C55E"
    dlg.btn_col_msg_end.current_hex = "#15803D"
    dlg.btn_col_watcher_status.current_hex = "#A855F7"
    dlg.btn_col_new_msg_bar.current_hex = "#3B82F6"
    dlg.btn_col_send_txt.current_hex = "#112233"
    dlg.btn_col_map_rep_hover.current_hex = "#F97316"
    dlg.btn_col_map_comp_hover.current_hex = "#10B981"
    dlg.btn_col_visualised_path.current_hex = "#FF8800"
    dlg.btn_col_visualised_heading.current_hex = "#FF00AA"
    dlg.chk_freshness.setChecked(True)

    dlg._save_and_close()

    assert config.app_colors.map_dot_size == 4.5
    assert config.app_colors.favorite_channel_color == "#FF0055"
    assert config.app_colors.favorite_user_color == "#FFCC00"
    assert config.app_colors.send_button_color == "#2563EB"
    assert config.app_colors.send_button_text_color == "#112233"
    assert config.app_colors.radio_connected_color == "#10B981"
    assert config.app_colors.sync_status_color == "#06B6D4"
    assert config.app_colors.message_snr_color == "#EAB308"
    assert config.app_colors.map_repeater_color == "#9333EA"
    assert config.app_colors.map_repeater_hover_color == "#F97316"
    assert config.app_colors.map_companion_color == "#06B6D4"
    assert config.app_colors.map_companion_hover_color == "#10B981"
    assert config.app_colors.map_visualised_path_color == "#FF8800"
    assert config.app_colors.map_visualised_heading_color == "#FF00AA"
    assert config.app_colors.map_watcher_line_start == "#F97316"
    assert config.app_colors.map_watcher_line_end == "#EF4444"
    assert config.app_colors.map_message_line_start == "#22C55E"
    assert config.app_colors.map_message_line_end == "#15803D"
    assert config.app_colors.map_watcher_status_color == "#A855F7"
    assert config.app_colors.new_messages_bar_color == "#3B82F6"
    assert config.meshcore.node_freshness_fading is True


def test_settings_apply_button_live_preview(qapp, tmp_path):
    """Verifies that clicking Apply saves config, emits event, and keeps dialog open."""
    from meshcore_tray.core.event_bus import bus, EventType

    cfg_file = tmp_path / "apply_config.json"
    config = AppConfig()
    config.save(cfg_file)
    storage = Storage(tmp_path / "apply_test.db")

    events_received = []
    bus.subscribe(EventType.SETTINGS_UPDATED, lambda cfg: events_received.append(cfg))

    dlg = SettingsDialog(config=config, storage=storage)

    # Change send button color
    dlg.btn_col_send.current_hex = "#9333EA"

    # Click Apply
    dlg.btn_apply.click()

    # Verify event was emitted
    assert len(events_received) == 1
    assert events_received[0].app_colors.send_button_color == "#9333EA"

    # Verify dialog is still active (not closed/accepted)
    assert dlg.isVisible() or dlg.result() == 0  # not accepted
    assert "✓ Saved to config.json & applied live!" in dlg.apply_status_lbl.text()


def test_main_window_freshness_toggle(qapp, tmp_path):
    """Verifies that the top-bar freshness toggle updates config, state, and event bus."""
    from meshcore_tray.ui.main_window import MainWindow

    cfg = AppConfig()
    cfg.meshcore.node_freshness_fading = False
    storage = Storage(tmp_path / "fresh_test.db")
    from unittest.mock import patch
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=cfg, storage=storage)
    assert win.btn_freshness_toggle.isChecked() is False
    assert "OFF" in win.btn_freshness_toggle.text()

    # Click toggle button
    win.btn_freshness_toggle.click()
    assert win.config.meshcore.node_freshness_fading is True
    assert "ON" in win.btn_freshness_toggle.text()
    assert win.btn_freshness_toggle.isChecked() is True


def test_settings_channel_favorite_persistence(qapp, tmp_path):
    """Verifies that checking and unchecking Favorite in SettingsDialog persists to both config and storage."""
    config = AppConfig()
    storage = Storage(tmp_path / "fav_settings_test.db")
    from meshcore_tray.core.models import ChannelInfo
    storage.save_channel(ChannelInfo(0, "Public", is_favorite=False, is_pixoo_enabled=True))
    storage.save_channel(ChannelInfo(1, "cumbria", is_favorite=True, is_pixoo_enabled=True))
    storage.save_channel(ChannelInfo(2, "test", is_favorite=False, is_pixoo_enabled=True))
    config.favorite_channels = ["cumbria"]

    dlg = SettingsDialog(config=config, storage=storage)

    # Row 1 is cumbria (currently fav). Let's uncheck it!
    fav_chk_cumbria = dlg.channel_table.cellWidget(1, 2)
    assert fav_chk_cumbria.isChecked() is True
    fav_chk_cumbria.setChecked(False)

    # Row 2 is test (currently not fav). Let's check it!
    fav_chk_test = dlg.channel_table.cellWidget(2, 2)
    assert fav_chk_test.isChecked() is False
    fav_chk_test.setChecked(True)

    dlg._save_and_close()

    # Verify config
    assert config.is_channel_favorite("cumbria") is False
    assert config.is_channel_favorite("test") is True

    # Verify storage
    assert storage.get_channel("cumbria").is_favorite is False
    assert storage.get_channel("test").is_favorite is True

    # Reopen dialog and verify table loads correctly
    dlg2 = SettingsDialog(config=config, storage=storage)
    assert dlg2.channel_table.cellWidget(1, 2).isChecked() is False
    assert dlg2.channel_table.cellWidget(2, 2).isChecked() is True


def test_settings_theme_auto_save_and_default_theme(qapp, tmp_path, monkeypatch):
    """Verifies that changing color pickers auto-saves live, Save Theme persists, and Set Current Theme as Default works."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("meshcore_tray.config.CONFIG_FILE", cfg_file)
    config = AppConfig()
    config.save(cfg_file)

    storage = Storage(tmp_path / "theme_test.db")
    dlg = SettingsDialog(config=config, storage=storage)

    # 1. Test auto-save on color change
    dlg.btn_col_fav_chan.color_changed.emit("#123456")
    assert config.app_colors.favorite_channel_color == "#123456"

    # Reload from disk and verify persisted
    loaded = AppConfig.load(cfg_file)
    assert loaded.app_colors.favorite_channel_color == "#123456"

    # 2. Test Set Current Theme as Default button
    dlg.btn_col_send.current_hex = "#ABCDEF"
    dlg.btn_set_default_theme.click()
    assert config.default_app_colors["favorite_channel_color"] == "#123456"
    assert config.default_app_colors["send_button_color"] == "#ABCDEF"

    loaded2 = AppConfig.load(cfg_file)
    assert loaded2.default_app_colors["send_button_color"] == "#ABCDEF"

    # 3. Modify colors and reset to default
    dlg.btn_col_send.current_hex = "#999999"
    dlg.btn_reset_default_theme.click()
    assert config.app_colors.send_button_color == "#ABCDEF"
    assert dlg.btn_col_send.current_hex == "#ABCDEF"


def test_floating_age_fade_toggle(qapp, tmp_path):
    """Verifies that the floating age fade button over the map toggles node freshness fading."""
    from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
    from unittest.mock import patch

    config = AppConfig()
    storage = Storage(tmp_path / "map_test.db")
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
        assert hasattr(map_widget, "btn_age_fade")
        assert map_widget.btn_age_fade.isChecked() is True

        map_widget.btn_age_fade.setChecked(False)
        map_widget._on_floating_age_fade_clicked()
        assert map_widget.freshness_fading is False
        assert config.meshcore.node_freshness_fading is False

        map_widget.set_freshness_fading(True)
        assert map_widget.btn_age_fade.isChecked() is True


def test_embedded_settings_view_in_main_window(qapp, tmp_path, monkeypatch):
    """Verifies that Settings is an embedded view at main_stack index 3, replacing channels, chat and map."""
    from unittest.mock import patch
    from meshcore_tray.ui.main_window import MainWindow

    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("meshcore_tray.config.CONFIG_FILE", cfg_file)
    config = AppConfig()
    config.save(cfg_file)
    storage = Storage(tmp_path / "embedded_test.db")

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage, radio_driver=None)

    # Initial state: main_stack is index 0 (main_splitter with channels, chat, map)
    assert win.main_stack.currentIndex() == 0
    assert win.main_stack.count() == 4
    assert win.main_stack.widget(3) == win.settings_view

    # Open Settings via nav_dock icon or _open_settings
    win._open_settings()
    assert win.main_stack.currentIndex() == 3

    # Close Settings via close_requested / Back button
    win.settings_view.close_requested.emit()
    assert win.main_stack.currentIndex() == 0

    # Navigating to another view exits settings as well
    win._open_settings()
    assert win.main_stack.currentIndex() == 3
    win.nav_dock.switch_view("dms")
    assert win.main_stack.currentIndex() == 1

    win.cleanup()


