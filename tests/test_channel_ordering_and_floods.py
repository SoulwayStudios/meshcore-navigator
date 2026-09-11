"""Unit tests for persistent channel ordering, sidebar visibility, flood unread tracking, and zero-jitter hover."""

import sys
import json
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication, QWidget

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import ChannelInfo, PacketPathInfo
from meshcore_tray.storage import Storage
from meshcore_tray.ui.sidebar import Sidebar, ChannelListWidget
from meshcore_tray.ui.heard_floods_view import HeardFloodsWidget, FloodRowWidget
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_app_config_channel_and_group_ordering():
    """Verifies that channel_order and group_order can be set, serialized, and loaded."""
    cfg = AppConfig()
    cfg.set_group_order(["Regions", "Emergency", "Channels"])
    cfg.set_channel_order(["cumbria", "northwest", "public"])

    assert cfg.get_group_order() == ["Regions", "Emergency", "Channels"]
    assert cfg.get_channel_order() == ["cumbria", "northwest", "public"]

    # Test serialization
    data = cfg.to_dict()
    assert data["group_order"] == ["Regions", "Emergency", "Channels"]
    assert data["channel_order"] == ["cumbria", "northwest", "public"]

    # Test deserialization
    loaded = AppConfig.from_dict(data)
    assert loaded.get_group_order() == ["Regions", "Emergency", "Channels"]
    assert loaded.get_channel_order() == ["cumbria", "northwest", "public"]


def test_sidebar_channel_ordering_and_grouping(qapp, tmp_path):
    """Verifies that sidebar orders channels according to channel_order and respects group_order."""
    db_path = tmp_path / "order_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    storage.save_channel(ChannelInfo(0, "Public", False, True))
    storage.save_channel(ChannelInfo(1, "cumbria", False, True))
    storage.save_channel(ChannelInfo(2, "northwest", False, True))

    # Place cumbria and northwest in "Regions"
    config.set_channel_group("cumbria", "Regions")
    config.set_channel_group("northwest", "Regions")
    # Put northwest before cumbria
    config.set_channel_order(["northwest", "cumbria", "Public"])
    config.set_group_order(["Regions", "Channels"])

    sidebar = Sidebar(storage=storage, config=config, show_contacts=False)
    sidebar.reload()

    # Collect visible item text in order
    items = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]

    # Regions group header should appear first
    assert "REGIONS" in items[0]
    # Northwest should appear before Cumbria in the list
    nw_idx = -1
    cu_idx = -1
    for i, t in enumerate(items):
        if "northwest" in t.lower():
            nw_idx = i
        if "cumbria" in t.lower():
            cu_idx = i
    assert nw_idx != -1 and cu_idx != -1
    assert nw_idx < cu_idx

    # Simulate reorder drop: moving cumbria before northwest
    sidebar._on_channel_reordered("cumbria", "Regions", "northwest", insert_after=False)
    assert config.get_channel_order() == ["cumbria", "northwest", "Public"]


def test_sidebar_hidden_on_floods_and_only_visible_on_main(qapp, tmp_path):
    """Verifies that the channels list is visible on 'main' and hidden on 'floods', 'dms', and 'repeaters'."""
    db_path = tmp_path / "nav_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.first_run_completed = True

    # Use a dummy parent widget to host the nav test
    parent = QWidget()
    from unittest.mock import patch
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(storage=storage, config=config)
    win.show()
    qapp.processEvents()

    # Initially on main view: sidebar must be visible
    win._on_nav_view_changed("main")
    assert win.sidebar.isHidden() is False

    # Switch to floods: sidebar must be hidden!
    win._on_nav_view_changed("floods")
    assert win.sidebar.isHidden() is True

    # Switch to dms: sidebar must be hidden
    win._on_nav_view_changed("dms")
    assert win.sidebar.isHidden() is True

    # Switch back to main: sidebar must be restored
    win._on_nav_view_changed("main")
    assert win.sidebar.isHidden() is False

    # Open settings: sidebar must be hidden
    win._open_settings()
    assert win.sidebar.isHidden() is True

    # Close settings: returns to main, sidebar must be visible
    win._close_settings()
    assert win.sidebar.isHidden() is False

    win.close()
    parent.close()


def test_heard_floods_unread_and_hover_no_jitter(qapp, tmp_path):
    """Verifies flood read/unread state tracking and that hover doesn't reflow geometry."""
    db_path = tmp_path / "flood_test.db"
    storage = Storage(db_path)

    # Save two paths with different timestamps
    path_old = PacketPathInfo(
        packet_id="p1",
        sender_id="n1",
        sender_name="Node1",
        timestamp="2026-09-11T01:00:00Z",
        route_type="FLOOD",
        hop_nodes=["rep1"]
    )
    path_new = PacketPathInfo(
        packet_id="p2",
        sender_id="n2",
        sender_name="Node2",
        timestamp="2026-09-11T02:00:00Z",
        route_type="FLOOD",
        hop_nodes=["rep1", "rep2"]
    )
    storage.save_packet_path(path_old)
    storage.save_packet_path(path_new)

    # Set last read to old path timestamp
    storage.set_last_read_flood_timestamp("2026-09-11T01:00:00Z")
    assert storage.get_last_read_flood_timestamp() == "2026-09-11T01:00:00Z"

    widget = HeardFloodsWidget(storage=storage)
    assert len(widget._rows) >= 2

    # The newer row (index 0) must be unread
    assert widget._rows[0].is_unread is True
    # The older row (index 1) must be read
    assert widget._rows[1].is_unread is False

    # Verify hover style does NOT use 1.5px border (which causes layout reflow)
    row = widget._rows[0]
    qss = row.styleSheet()
    assert "1.5px" not in qss
    assert "border: 1px solid" in qss

    # Verify last read timestamp was updated to newest path timestamp
    assert storage.get_last_read_flood_timestamp() == "2026-09-11T02:00:00Z"

    widget.close()
