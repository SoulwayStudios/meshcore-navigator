"""Unit and Integration Tests for VersionChecker, GitHub Releases, and UI Update Prompts."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
import requests

from meshcore_tray.core.version_checker import (
    VersionChecker,
    VersionCheckWorker,
    ReleaseInfo,
    clean_version_str,
    compare_versions,
)
from meshcore_tray.config import AppConfig
from meshcore_tray.ui.splash_overlay import SplashOverlay
from meshcore_tray.ui.settings_widget import SettingsWidget


# --- 1. Version Parsing & Comparison Tests ---

def test_clean_version_str():
    assert clean_version_str("v0.5.1") == "0.5.1"
    assert clean_version_str("V1.2.3") == "1.2.3"
    assert clean_version_str("  v0.4.0  ") == "0.4.0"
    assert clean_version_str("0.5.1") == "0.5.1"
    assert clean_version_str("") == "0.0.0"


def test_compare_versions_semver():
    # Newer releases
    assert compare_versions("0.5.2", "0.5.1") is True
    assert compare_versions("v0.6.0", "0.5.1") is True
    assert compare_versions("1.0.0", "0.5.1") is True
    assert compare_versions("0.5.1.1", "0.5.1") is True

    # Same version
    assert compare_versions("0.5.1", "0.5.1") is False
    assert compare_versions("v0.5.1", "0.5.1") is False

    # Older versions
    assert compare_versions("0.5.0", "0.5.1") is False
    assert compare_versions("0.4.9", "0.5.1") is False
    assert compare_versions("0.1.0", "0.5.1") is False

    # Fallback resilience on non-standard tags
    assert compare_versions("alpha-99", "0.5.1") is False
    assert compare_versions("v2-build", "1.0.0") is True


# --- 2. Worker Network Request Tests ---

def test_version_check_worker_newer_release():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tag_name": "v0.6.0",
        "name": "MeshCore Navigator v0.6.0 - Major Update",
        "html_url": "https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
        "body": "New feature release with improved mapping.",
        "published_at": "2026-09-13T12:00:00Z"
    }

    results = []
    worker = VersionCheckWorker(current_version="0.5.1")
    worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))

    with patch("requests.get", return_value=mock_resp):
        worker.run()

    assert len(results) == 1
    is_newer, info, err = results[0]
    assert is_newer is True
    assert isinstance(info, ReleaseInfo)
    assert info.version == "0.6.0"
    assert info.tag_name == "v0.6.0"
    assert info.is_newer is True
    assert err == ""


def test_version_check_worker_current_release():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tag_name": "v0.5.1",
        "name": "v0.5.1",
        "html_url": "https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.5.1",
    }

    results = []
    worker = VersionCheckWorker(current_version="0.5.1")
    worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))

    with patch("requests.get", return_value=mock_resp):
        worker.run()

    assert len(results) == 1
    is_newer, info, err = results[0]
    assert is_newer is False
    assert info is not None
    assert info.is_newer is False
    assert err == ""


def test_version_check_worker_http_errors():
    worker = VersionCheckWorker(current_version="0.5.1")

    # 404
    mock_404 = MagicMock(status_code=404)
    with patch("requests.get", return_value=mock_404):
        results = []
        worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))
        worker.run()
        assert results[0][0] is False
        assert "No releases" in results[0][2]

    # 403 Rate Limit
    mock_403 = MagicMock(status_code=403)
    with patch("requests.get", return_value=mock_403):
        results = []
        worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))
        worker.run()
        assert results[0][0] is False
        assert "rate limit" in results[0][2]


def test_version_check_worker_timeout_and_exceptions():
    worker = VersionCheckWorker(current_version="0.5.1")

    # Timeout
    with patch("requests.get", side_effect=requests.exceptions.Timeout()):
        results = []
        worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))
        worker.run()
        assert results[0][0] is False
        assert "timed out" in results[0][2]

    # Generic RequestException
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("DNS failure")):
        results = []
        worker.check_completed.connect(lambda is_newer, info, err: results.append((is_newer, info, err)))
        worker.run()
        assert results[0][0] is False
        assert "Network error" in results[0][2]


# --- 3. VersionChecker Caching Tests ---

def test_version_checker_caching():
    checker = VersionChecker()
    fake_info = ReleaseInfo(
        tag_name="v0.6.0",
        version="0.6.0",
        name="MeshCore Navigator v0.6.0",
        html_url="https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
        is_newer=True,
    )

    # Populate cache
    checker._last_checked_at = datetime.now(timezone.utc)
    checker._cached_result = (True, fake_info, "")

    received = []
    checker.check_finished.connect(lambda n, i, e: received.append((n, i, e)))

    # Call check_for_updates without force -> should immediately return cached result without worker
    with patch.object(VersionCheckWorker, "start") as mock_start:
        checker.check_for_updates(force=False)
        assert not mock_start.called
        assert len(received) == 1
        assert received[0][0] is True
        assert received[0][1].version == "0.6.0"

    # Expire cache and check that worker is started
    checker._allow_test_network = True
    checker._last_checked_at = datetime.now(timezone.utc) - timedelta(hours=2)
    with patch.object(VersionCheckWorker, "start") as mock_start:
        checker.check_for_updates(force=False)
        assert mock_start.called


# --- 4. SplashOverlay UI & Update Prompt Tests ---

def test_splash_overlay_update_prompt_display():
    config = AppConfig()
    config.check_updates_on_startup = False  # Don't auto-launch network worker in test

    overlay = SplashOverlay(config=config)
    overlay.show()
    assert overlay.update_card.isHidden() is True

    # Simulate an update found notification
    fake_info = ReleaseInfo(
        tag_name="v0.6.0",
        version="0.6.0",
        name="MeshCore Navigator v0.6.0",
        html_url="https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
        is_newer=True
    )
    overlay._on_version_check_finished(True, fake_info, "")

    assert overlay._update_available is True
    assert overlay.update_card.isHidden() is False
    assert "v0.6.0" in overlay.update_title.text()
    assert "Download v0.6.0" in overlay.update_btn.text()

    # Verify auto-dismissal is blocked when update is available
    overlay._min_timer_done = True
    overlay.on_map_ready()
    assert overlay.isVisible() is True  # Still showing because user has not dismissed

    # Test "Remind Later" dismisses overlay
    overlay.update_dismiss_btn.click()
    assert overlay._is_dismissing is True


def test_splash_overlay_download_button_opens_url():
    config = AppConfig()
    config.check_updates_on_startup = False

    overlay = SplashOverlay(config=config)
    fake_info = ReleaseInfo(
        tag_name="v0.6.0",
        version="0.6.0",
        name="v0.6.0",
        html_url="https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
        is_newer=True
    )
    overlay._on_version_check_finished(True, fake_info, "")

    with patch("PyQt6.QtGui.QDesktopServices.openUrl") as mock_open:
        overlay.update_btn.click()
        assert mock_open.called
        called_url = mock_open.call_args[0][0].toString()
        assert "github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0" in called_url
        assert overlay._is_dismissing is True


# --- 5. Settings Widget Startup Option & About Tab Tests ---

def test_settings_widget_update_controls(tmp_path):
    from meshcore_tray.storage import Storage

    db_path = tmp_path / "test_settings_updates.db"
    storage = Storage(db_path=db_path)
    config = AppConfig()
    config.check_updates_on_startup = True

    widget = SettingsWidget(config=config, storage=storage)

    # 1. Verify startup checkbox exists and reflects config
    assert hasattr(widget, "chk_check_updates")
    assert widget.chk_check_updates.isChecked() is True

    # 2. Verify About tab has updates card and manual check button
    assert hasattr(widget, "btn_check_updates")
    assert hasattr(widget, "lbl_update_status")

    # 3. Simulate clicking manual check
    with patch.object(VersionChecker, "check_for_updates") as mock_check:
        widget.btn_check_updates.click()
        assert mock_check.called
        assert widget.btn_check_updates.isEnabled() is False
        assert "Connecting to GitHub" in widget.lbl_update_status.text()

    # 4. Simulate response with update
    fake_info = ReleaseInfo(
        tag_name="v0.6.0",
        version="0.6.0",
        name="MeshCore Navigator v0.6.0",
        html_url="https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
        is_newer=True
    )
    widget._on_manual_version_check_finished(True, fake_info, "")
    assert widget.btn_check_updates.isEnabled() is True
    assert "Update Available: v0.6.0" in widget.lbl_update_status.text()

    # 5. Toggle checkbox and save settings
    widget.chk_check_updates.setChecked(False)
    widget._apply_settings(close_on_finish=False)
    assert config.check_updates_on_startup is False
