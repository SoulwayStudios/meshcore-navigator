"""Tests for Heard Floods View, Monospace Watcher Bar Styling, and Map Hover Trajectory Preview."""

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import PacketPathInfo
from meshcore_tray.storage import Storage
from meshcore_tray.ui.heard_floods_view import HeardFloodsWidget, FloodRowWidget
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.nav_dock import NavDockWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["meshcore-test"])
    return app


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_heard_floods.db"
    return Storage(str(db_file))


def test_flood_row_widget_display_and_hover_events(qapp):
    """Verifies that FloodRowWidget renders correctly and emits hovered, unhovered, and selected signals."""
    path = PacketPathInfo(
        packet_id="pkt-12345",
        sender_id="d79870ac",
        sender_name="M7NCY Repeater",
        route_type="FLOOD",
        hop_nodes=["Node-A", "Node-B", "Target"],
        hop_snrs=[12.5, 9.0],
        coordinates=[[54.5, -3.2], [54.6, -3.3], [54.7, -3.4]],
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    row = FloodRowWidget(path)

    # Check UI components and texts
    assert "M7NCY Repeater" in row.lbl_sender.text()
    assert "FLOOD" in row.lbl_badge.text()
    assert "Node-A ➔ Node-B ➔ Target" in row.lbl_hops.text()

    # Test Hover Enter Event
    hovered_paths = []
    unhovered_called = []
    selected_paths = []

    row.hovered.connect(lambda p: hovered_paths.append(p))
    row.unhovered.connect(lambda: unhovered_called.append(True))
    row.selected.connect(lambda p: selected_paths.append(p))

    # Simulate enterEvent
    enter_ev = QEnterEvent(QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPointF(5.0, 5.0))
    row.enterEvent(enter_ev)
    assert len(hovered_paths) == 1
    assert hovered_paths[0].packet_id == "pkt-12345"

    # Simulate leaveEvent
    leave_ev = QEvent(QEvent.Type.Leave)
    row.leaveEvent(leave_ev)
    assert len(unhovered_called) == 1

    # Simulate mousePressEvent
    mouse_ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5.0, 5.0), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    row.mousePressEvent(mouse_ev)
    assert len(selected_paths) == 1
    assert selected_paths[0].packet_id == "pkt-12345"


def test_heard_floods_widget_live_and_historical(qapp, temp_storage):
    """Verifies HeardFloodsWidget loads recent floods, receives live bus events, pause, and clear."""
    # Prepopulate storage with 2 paths
    p1 = PacketPathInfo(
        packet_id="pkt-hist-1",
        sender_id="sender1",
        sender_name="Node One",
        route_type="FLOOD",
        hop_nodes=["Hop1"],
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    p2 = PacketPathInfo(
        packet_id="pkt-hist-2",
        sender_id="sender2",
        sender_name="Node Two",
        route_type="ROUTED",
        hop_nodes=["Hop2", "Hop3"],
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    temp_storage.save_packet_path(p1)
    temp_storage.save_packet_path(p2)

    widget = HeardFloodsWidget(storage=temp_storage)
    assert len(widget._rows) == 2
    assert "2 Floods" in widget.count_badge.text()

    # Receive live packet path via EventBus
    p_live = PacketPathInfo(
        packet_id="pkt-live-3",
        sender_id="sender3",
        sender_name="Node Three",
        route_type="FLOOD",
        hop_nodes=["LiveHop"],
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    bus.emit(EventType.PACKET_PATH_TRACED, p_live)

    assert len(widget._rows) == 3
    assert "3 Floods" in widget.count_badge.text()
    assert widget._rows[0].path.packet_id == "pkt-live-3"

    # Test pause
    widget.btn_pause.click()
    assert widget.paused is True
    assert "PAUSED" in widget.title_lbl.text()

    p_ignored = PacketPathInfo(
        packet_id="pkt-live-4",
        sender_id="sender4",
        sender_name="Node Four",
        route_type="FLOOD",
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    bus.emit(EventType.PACKET_PATH_TRACED, p_ignored)
    assert len(widget._rows) == 3

    # Unpause
    widget.btn_pause.click()
    assert widget.paused is False

    # Test clear
    widget.clear_list()
    assert len(widget._rows) == 0
    assert "0 Floods" in widget.count_badge.text()

    # Test reload
    widget.reload()
    assert len(widget._rows) == 2


def test_main_window_nav_dock_floods_view_switch(qapp, temp_storage):
    """Verifies that clicking the Floods button in NavDock displays HeardFloodsWidget in center_stack."""
    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=temp_storage)

    # Initially Main Chat view (center_stack index 0)
    assert win.center_stack.currentIndex() == 0

    # Switch to Floods view via nav_dock
    win.nav_dock.switch_view("floods")
    assert win.center_stack.currentIndex() == 2
    assert isinstance(win.center_stack.currentWidget(), HeardFloodsWidget)

    # Click Back to Chat button in HeardFloodsWidget
    win.heard_floods_view.btn_chat.click()
    assert win.center_stack.currentIndex() == 0
    assert win.nav_dock.active_view == "main"
