"""Tests for ADS-B Tactical Proximity Audio Alerts.

Covers:
1. Alert audio synthesis and default tactical WAV generation.
2. Cross-platform path normalization (including Windows quoted paths and backslashes).
3. Fallback to default tactical tone when custom path is invalid or missing.
4. SettingsWidget audio controls, mode toggles, saving, and loading.
5. MainWindow _on_adsb_proximity_alert audio dispatch and tray notification icon.
6. MeshMapWidget test sound slot and HTML template elements.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.alert_audio import (
    AlertAudioManager,
    generate_tactical_alert_wav,
    get_alert_audio_manager,
    get_default_tactical_wav_path,
    normalize_sound_path,
    play_adsb_proximity_alert,
)


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_meshcore.db"
    return Storage(db_path=db_file)


def test_tactical_wav_generation(tmp_path):
    """Verifies that generate_tactical_alert_wav synthesizes a valid PCM WAV file."""
    wav_target = tmp_path / "test_alert.wav"
    result = generate_tactical_alert_wav(wav_target)
    assert result.exists()
    assert result.stat().st_size > 1000

    import wave
    with wave.open(str(result), "rb") as r:
        assert r.getnchannels() == 1
        assert r.getsampwidth() == 2
        assert r.getframerate() == 44100
        assert r.getnframes() > 1000


def test_normalize_sound_path(tmp_path):
    """Verifies that normalize_sound_path cleanly strips quotes, whitespace, and resolves paths."""
    assert normalize_sound_path("") is None
    assert normalize_sound_path(None) is None
    assert normalize_sound_path("   ") is None
    assert normalize_sound_path("/non/existent/path/sound.wav") is None

    # Create dummy audio file
    dummy_wav = tmp_path / "custom_siren.wav"
    dummy_wav.write_bytes(b"RIFFdummydata")

    # Clean path
    norm1 = normalize_sound_path(str(dummy_wav))
    assert norm1 == dummy_wav.resolve()

    # Quoted path (common on Windows Copy as Path)
    norm2 = normalize_sound_path(f'"{dummy_wav}"')
    assert norm2 == dummy_wav.resolve()

    # Single-quoted path with leading/trailing spaces
    norm3 = normalize_sound_path(f"  '{dummy_wav}'  ")
    assert norm3 == dummy_wav.resolve()


def test_get_default_tactical_wav_path():
    """Verifies that get_default_tactical_wav_path returns an existing cached file."""
    p = get_default_tactical_wav_path()
    assert p.exists()
    assert p.stat().st_size > 1000


def test_alert_audio_manager_play_and_fallbacks(tmp_path):
    """Verifies AlertAudioManager playback modes and graceful fallback behavior."""
    mgr = AlertAudioManager()
    mgr.play_sound = MagicMock(return_value=True)

    config = AppConfig()
    config.meshcore.adsb_alert_enabled = True
    config.meshcore.adsb_alert_sound_enabled = True
    config.meshcore.adsb_alert_sound_mode = "tactical"

    # 1. Tactical mode
    assert mgr.play_adsb_alert(config) is True
    mgr.play_sound.assert_called_once()
    called_path = mgr.play_sound.call_args[0][0]
    assert called_path.name == "tactical_alert.wav"

    # 2. Custom mode with valid sound file
    custom_wav = tmp_path / "ww2_siren.wav"
    custom_wav.write_bytes(b"RIFFdata")
    config.meshcore.adsb_alert_sound_mode = "custom"
    config.meshcore.adsb_alert_sound_file = str(custom_wav)

    mgr.play_sound.reset_mock()
    assert mgr.play_adsb_alert(config) is True
    mgr.play_sound.assert_called_once_with(custom_wav.resolve())

    # 3. Custom mode with invalid/missing sound file -> falls back to tactical tone!
    config.meshcore.adsb_alert_sound_file = str(tmp_path / "missing_file.wav")
    mgr.play_sound.reset_mock()
    assert mgr.play_adsb_alert(config) is True
    fallback_path = mgr.play_sound.call_args[0][0]
    assert fallback_path.name == "tactical_alert.wav"

    # 4. Sound disabled
    config.meshcore.adsb_alert_sound_enabled = False
    mgr.play_sound.reset_mock()
    assert mgr.play_adsb_alert(config) is False
    mgr.play_sound.assert_not_called()

    # 5. Alert entirely disabled
    config.meshcore.adsb_alert_sound_enabled = True
    config.meshcore.adsb_alert_enabled = False
    mgr.play_sound.reset_mock()
    assert mgr.play_adsb_alert(config) is False
    mgr.play_sound.assert_not_called()


def test_settings_widget_adsb_sound_controls(qapp, temp_storage):
    """Verifies SettingsWidget ADS-B audio controls, toggle states, saving, and reloading."""
    from meshcore_tray.ui.settings_widget import SettingsWidget

    config = AppConfig()
    config.meshcore.adsb_alert_sound_enabled = True
    config.meshcore.adsb_alert_sound_mode = "custom"
    config.meshcore.adsb_alert_sound_file = "/path/to/my_siren.wav"

    widget = SettingsWidget(config=config, storage=temp_storage)

    # 1. Verify UI widgets exist
    assert hasattr(widget, "chk_adsb_sound_en")
    assert hasattr(widget, "combo_adsb_sound_mode")
    assert hasattr(widget, "txt_adsb_sound_file")
    assert hasattr(widget, "btn_browse_sound")
    assert hasattr(widget, "btn_test_sound")
    assert hasattr(widget, "btn_reset_sound")

    # 2. Verify initial population from config
    assert widget.chk_adsb_sound_en.isChecked() is True
    assert widget.combo_adsb_sound_mode.currentIndex() == 1  # Custom
    assert widget.txt_adsb_sound_file.text() == "/path/to/my_siren.wav"
    assert widget.txt_adsb_sound_file.isEnabled() is True

    # 3. Mode combo switch to Tactical disables custom text input
    widget.combo_adsb_sound_mode.setCurrentIndex(0)
    assert widget.txt_adsb_sound_file.isEnabled() is False
    assert widget.btn_browse_sound.isEnabled() is False

    # 4. Reset button clears text and sets combo back to tactical
    widget.txt_adsb_sound_file.setText("some_temp_file.wav")
    widget._on_reset_adsb_sound()
    assert widget.combo_adsb_sound_mode.currentIndex() == 0
    assert widget.txt_adsb_sound_file.text() == ""

    # 5. Test saving through _apply_settings
    widget.chk_adsb_sound_en.setChecked(False)
    widget.combo_adsb_sound_mode.setCurrentIndex(1)
    widget.txt_adsb_sound_file.setText('  "C:\\Sounds\\alert.wav"  ')
    widget._apply_settings(close_on_finish=False)

    assert config.meshcore.adsb_alert_sound_enabled is False
    assert config.meshcore.adsb_alert_sound_mode == "custom"
    assert config.meshcore.adsb_alert_sound_file == "C:\\Sounds\\alert.wav"  # Stripped quotes and whitespace


def test_main_window_adsb_proximity_alert_audio_and_noicon(qapp, temp_storage):
    """Verifies that MainWindow triggers in-app audio alert and uses NoIcon to prevent motherboard beep."""
    from meshcore_tray.ui.main_window import MainWindow
    from PyQt6.QtWidgets import QSystemTrayIcon

    config = AppConfig()
    config.meshcore.adsb_alert_enabled = True
    config.meshcore.adsb_alert_sound_enabled = True

    with patch("meshcore_tray.ui.main_window.play_adsb_proximity_alert") as mock_play:
        win = MainWindow(config=config, storage=temp_storage)
        win._tray_icon = MagicMock()

        # Trigger proximity alert
        win._on_adsb_proximity_alert("4002A1", "RRR214", 5.2, "military")

        # 1. Verify in-app audio alert was triggered
        mock_play.assert_called_once_with(config)

        # 2. Verify tray notification used NoIcon (so Windows does not emit MB_ICONWARNING motherboard beep)
        win._tray_icon.showMessage.assert_called_once()
        args = win._tray_icon.showMessage.call_args[0]
        assert "Military Aircraft Proximity Alert" in args[0]
        assert "5.2 miles" in args[1]
        assert args[2] == QSystemTrayIcon.MessageIcon.NoIcon


def test_mesh_map_widget_adsb_sound_elements_and_test_slot():
    """Verifies Leaflet template includes sound status and test sound button, and slot triggers alert."""
    from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, LEAFLET_HTML_TEMPLATE

    # Verify template contains sound status and test button
    assert 'id="adsb-alert-sound-status"' in LEAFLET_HTML_TEMPLATE
    assert "on_test_adsb_alert_sound" in LEAFLET_HTML_TEMPLATE

    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(config=config)

    with patch("meshcore_tray.core.alert_audio.play_adsb_proximity_alert") as mock_play:
        widget._on_test_adsb_alert_sound()
        mock_play.assert_called_once_with(config)
        assert "Testing proximity alert audio output" in widget.watcher_status.text()


def test_alert_audio_backend_dispatch(tmp_path):
    """Verifies that play_sound dispatches via native OS audio tools on Linux/Windows and falls back to QMediaPlayer."""
    mgr = AlertAudioManager()
    test_wav = tmp_path / "test.wav"
    test_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    # On Linux: verify subprocess.Popen is called for pw-play / paplay / aplay
    with patch("sys.platform", "linux"), patch("shutil.which", return_value="/usr/bin/pw-play"), patch("subprocess.Popen") as mock_popen:
        assert mgr.play_sound(test_wav) is True
        mock_popen.assert_called_once()
        assert "pw-play" in mock_popen.call_args[0][0][0]

    # On Windows: verify winsound.PlaySound is called for WAV files
    mock_winsound = MagicMock()
    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winsound": mock_winsound}):
        assert mgr.play_sound(test_wav) is True
        mock_winsound.PlaySound.assert_called_once()

    # Universal QMediaPlayer fallback when system audio commands not found
    with patch("sys.platform", "linux"), patch("shutil.which", return_value=None):
        mock_player = MagicMock()
        mgr._media_player = mock_player
        assert mgr.play_sound(test_wav) is True
        mock_player.play.assert_called_once()
