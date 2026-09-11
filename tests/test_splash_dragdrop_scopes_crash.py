"""Tests for Splash Screen, Channel Drag-and-Drop, Scope Discoverability, and Crash Reporter."""

import sys
import urllib.parse
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtCore import Qt, QPoint, QEvent
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication, QWidget

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import ChannelInfo, MessageEnvelope
from meshcore_tray.storage import Storage
from meshcore_tray.ui.sidebar import Sidebar, ChannelListWidget
from meshcore_tray.ui.splash_overlay import SplashOverlay
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.crash_dialog import (
    CrashReportDialog, format_crash_markdown, build_system_info, open_bug_report_in_browser
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_splash_overlay_styling_and_safe_resize(qapp):
    """Verifies that the splash screen description is not clipped and resizing does not raise RuntimeError."""
    config = AppConfig()
    config.first_run_completed = True
    parent = QWidget()
    parent.resize(1000, 600)

    overlay = SplashOverlay(config=config, parent=parent)
    overlay.show()

    # Verify splash overlay was created and has comfortable width
    assert overlay.card.width() >= 560

    # Dismiss overlay
    overlay.dismiss(immediate=True)
    qapp.processEvents()

    # Trigger resizeEvent on parent - must NOT raise RuntimeError
    parent.resize(1200, 700)
    qapp.processEvents()

    parent.close()


def test_channel_drag_and_drop_between_groups(qapp, tmp_path):
    """Verifies that channels can be dragged and dropped into new groups without duplicating."""
    db_path = tmp_path / "drag_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    storage.save_channel(ChannelInfo(0, "Public", False, True))
    storage.save_channel(ChannelInfo(1, "cumbria", False, True))
    storage.save_channel(ChannelInfo(2, "northwest", False, True))

    sidebar = Sidebar(storage=storage, config=config)

    # Verify initial state: all in "Channels"
    assert config.get_channel_group("northwest") == "Channels"

    # Simulate drag-drop of 'northwest' onto a new group 'Regions'
    sidebar.channel_list.channel_group_dropped.emit("northwest", "Regions")
    qapp.processEvents()

    # Verify config updated
    assert config.get_channel_group("northwest") == "Regions"

    # Verify sidebar reloaded without duplicating northwest
    items = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    assert "▼  CHANNELS" in items
    assert "▼  REGIONS" in items
    # Check count of northwest items in the list
    nw_items = [t for t in items if "northwest" in t.lower()]
    assert len(nw_items) == 1, f"Expected exactly 1 northwest channel, found: {nw_items}"


def test_channel_deduplication_in_storage(tmp_path):
    """Verifies database deduplication when channels differ only by leading # or case."""
    db_path = tmp_path / "dedupe_test.db"
    storage = Storage(db_path)

    # Save duplicate variants
    storage.save_channel(ChannelInfo(1, "northwest", False, True))
    storage.save_channel(ChannelInfo(2, "#northwest", True, True))

    chans = storage.get_channels()
    clean_names = [c.name.strip().lstrip("#").lower() for c in chans]
    assert clean_names.count("northwest") == 1

    # Run verify_and_sanitize_database and check
    rep = storage.verify_and_sanitize_database()
    assert rep["integrity_ok"] is True

    chans_after = storage.get_channels()
    clean_after = [c.name.strip().lstrip("#").lower() for c in chans_after]
    assert clean_after.count("northwest") == 1


def test_scope_discoverability_from_messages_and_hashtags(tmp_path):
    """Verifies automatic discovery of regional RF scopes from incoming messages and channels."""
    db_path = tmp_path / "scope_disc_test.db"
    storage = Storage(db_path)

    # 1. Discover scope from channel name #gb-mid
    msg1 = MessageEnvelope(
        id="msg-1",
        timestamp="2026-09-11T03:00:00Z",
        source_driver="meshcore",
        sender_id="!node1",
        sender_name="M7MID",
        channel="#gb-mid",
        text="Midlands net check-in",
    )
    storage.save_message(msg1)

    # 2. Discover scope from message hashtag #scope:wales
    msg2 = MessageEnvelope(
        id="msg-2",
        timestamp="2026-09-11T03:01:00Z",
        source_driver="meshcore",
        sender_id="!node2",
        sender_name="GW4TEST",
        channel="Public",
        text="Snowdonia repeater active #scope:wales and #gb-wales",
    )
    storage.save_message(msg2)

    # Check discovered scopes
    disc = storage.get_discovered_scopes()
    assert "gb-mid" in disc
    assert "wales" in disc or "gb-wales" in disc

    # Check merged scope definitions
    all_scopes = storage.get_scope_definitions()
    assert "gb-cum" in all_scopes  # Baseline
    assert "gb-mid" in all_scopes  # Discovered
    assert all_scopes["gb-mid"]["color"].startswith("#")


def test_crash_reporter_formatting_and_dialog(qapp):
    """Verifies that CrashReportDialog formats markdown properly and generates valid GitHub Issue URLs."""
    try:
        raise ValueError("Simulated Radio Buffer Overflow")
    except ValueError as e:
        exc_type, exc_val, exc_tb = sys.exc_info()

    # Format markdown
    md = format_crash_markdown(exc_type, exc_val, exc_tb)
    assert "Crash Report: ValueError" in md
    assert "Simulated Radio Buffer Overflow" in md
    assert "MESHCORE NAVIGATOR" in md

    # Create dialog
    dlg = CrashReportDialog(exc_type, exc_val, exc_tb)
    assert dlg.tb_edit.toPlainText() != ""
    assert dlg.btn_copy is not None
    assert dlg.btn_github is not None

    # Test copy button
    dlg._copy_report()
    assert QApplication.clipboard().text() == dlg.markdown_report

    # Test open_bug_report_in_browser
    with patch("PyQt6.QtGui.QDesktopServices.openUrl") as mock_open:
        open_bug_report_in_browser(title="Test Bug", body="Test Body")
        assert mock_open.called
        called_url = mock_open.call_args[0][0].toString()
        assert "github.com/SoulwayStudios/meshcore-navigator/issues/new" in called_url
        assert "Test+Bug" in called_url or "Test%20Bug" in called_url

    dlg.close()
