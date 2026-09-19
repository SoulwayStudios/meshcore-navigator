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


def test_packet_feed_filters_and_search(qapp, temp_storage):
    """Verifies that filter pills and search input dynamically filter rows."""
    from meshcore_tray.ui.heard_floods_view import PacketFeedWidget
    p1 = PacketPathInfo(
        packet_id="p1", sender_id="node_a", sender_name="Alpha Node",
        route_type="FLOOD", payload_type="ADVERT", hop_nodes=["r1"]
    )
    p2 = PacketPathInfo(
        packet_id="p2", sender_id="node_b", sender_name="Bravo Node",
        route_type="DIRECT", payload_type="GRP_TXT", hop_nodes=[]
    )
    temp_storage.save_packet_path(p1)
    temp_storage.save_packet_path(p2)

    widget = PacketFeedWidget(storage=temp_storage)
    assert len(widget._rows) == 2

    advert_row = next(r for r in widget._rows if r.path.payload_type == "ADVERT")
    grp_row = next(r for r in widget._rows if r.path.payload_type == "GRP_TXT")

    # Filter by ADVERT
    widget._on_filter_clicked("ADVERT")
    assert not advert_row.isHidden()
    assert grp_row.isHidden()
    assert "1 Packets" in widget.count_badge.text()

    # Filter by GRP_TXT
    widget._on_filter_clicked("GRP_TXT")
    assert advert_row.isHidden()
    assert not grp_row.isHidden()

    # Search filter
    widget._on_filter_clicked("ALL")
    widget.search_input.setText("alpha")
    assert not advert_row.isHidden()
    assert grp_row.isHidden()


    widget.search_input.setText("nonexistent")
    assert widget._rows[0].isHidden()
    assert widget._rows[1].isHidden()
    assert "0 Packets" in widget.count_badge.text()



def test_byte_inspector_drawer(qapp):
    """Verifies that selecting a row populates ByteInspectorDrawer with field breakdown and hex dump."""
    from meshcore_tray.ui.heard_floods_view import ByteInspectorDrawer, HeardFloodsWidget
    drawer = ByteInspectorDrawer()

    raw_hex = "1501A1008899DEADBEEF"
    p = PacketPathInfo(
        packet_id="inspect-1",
        sender_id="n1",
        sender_name="Origin Node",
        route_type="FLOOD",
        payload_type="GRP_TXT",
        raw_hex=raw_hex,
        hop_nodes=["HopA"],
        hop_snrs=[10.5]
    )
    drawer.inspect_path(p)

    assert drawer.table.rowCount() >= 4
    assert "Header (1B)" in drawer.table.item(0, 1).text()
    assert "0000" in drawer.hex_text.toPlainText()
    assert "15 01 A1 00" in drawer.hex_text.toPlainText()

    # Test trace requested signal
    traced_paths = []
    drawer.trace_requested.connect(lambda pt: traced_paths.append(pt))
    drawer.btn_trace.click()
    assert len(traced_paths) == 1
    assert traced_paths[0].packet_id == "inspect-1"


def test_byop_decode_dialog(qapp):
    """Verifies that DecodePacketDialog correctly decodes raw hex packets."""
    from meshcore_tray.ui.heard_floods_view import DecodePacketDialog
    dlg = DecodePacketDialog()
    dlg.input_hex.setPlainText("1501A1008899DEADBEEF")
    dlg.btn_decode.click()

    assert "FLOOD" in dlg.status_lbl.text()
    assert dlg.table.rowCount() >= 4


def test_all_filter_headers_display_data(qapp, temp_storage):
    """Verifies that Floods, Adverts, Chat, Traces, and ACKs headers display data correctly when clicked."""
    p_flood = PacketPathInfo(packet_id="p-fl", sender_id="n1", sender_name="FloodNode", route_type="FLOOD", payload_type="FLOOD")
    p_adv = PacketPathInfo(packet_id="p-adv", sender_id="n2", sender_name="AdvertNode", route_type="FLOOD", payload_type="ADVERT")
    p_chat = PacketPathInfo(packet_id="p-chat", sender_id="n3", sender_name="ChatNode", route_type="FLOOD", payload_type="GRP_TXT")
    p_trace = PacketPathInfo(packet_id="p-trace", sender_id="n4", sender_name="TraceRoute", route_type="TRACEROUTE", payload_type="TRACE")
    p_ack = PacketPathInfo(packet_id="p-ack", sender_id="n5", sender_name="Packet ACK", route_type="DIRECT", payload_type="ACK")

    temp_storage.save_packet_path(p_flood)
    temp_storage.save_packet_path(p_adv)
    temp_storage.save_packet_path(p_chat)
    temp_storage.save_packet_path(p_trace)
    temp_storage.save_packet_path(p_ack)

    widget = HeardFloodsWidget(storage=temp_storage)
    assert len(widget._rows) == 5

    # Test ALL
    widget._on_filter_clicked("ALL")
    assert sum(1 for r in widget._rows if not r.isHidden()) == 5

    # Test FLOOD
    widget._on_filter_clicked("FLOOD")
    # p_flood, p_adv, p_chat are all FLOOD route_type
    assert any(not r.isHidden() and r.path.packet_id == "p-fl" for r in widget._rows)

    # Test ADVERT
    widget._on_filter_clicked("ADVERT")
    visible_adv = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_adv) == 1
    assert visible_adv[0].path.packet_id == "p-adv"

    # Test GRP_TXT (Chat)
    widget._on_filter_clicked("GRP_TXT")
    visible_chat = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_chat) == 1
    assert visible_chat[0].path.packet_id == "p-chat"

    # Test TRACE
    widget._on_filter_clicked("TRACE")
    visible_trace = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_trace) == 1
    assert visible_trace[0].path.packet_id == "p-trace"

    # Test ACK
    widget._on_filter_clicked("ACK")
    visible_ack = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_ack) == 1
    assert visible_ack[0].path.packet_id == "p-ack"


def test_historical_payload_inference(qapp, temp_storage):
    """Verifies that legacy/historical packets without payload_type are auto-inferred on load."""
    p_legacy_trace = PacketPathInfo(
        packet_id="leg-trace", sender_id="trace", sender_name="TraceRoute",
        route_type="TRACEROUTE", payload_type="FLOOD"
    )
    p_legacy_adv = PacketPathInfo(
        packet_id="leg-adv", sender_id="adv1", sender_name="@MyNode (Advert)",
        route_type="FLOOD", payload_type="FLOOD"
    )
    p_legacy_chat = PacketPathInfo(
        packet_id="leg-chat", sender_id="chat1", sender_name="@Alice [#General]",
        route_type="FLOOD", payload_type="FLOOD"
    )
    temp_storage.save_packet_path(p_legacy_trace)
    temp_storage.save_packet_path(p_legacy_adv)
    temp_storage.save_packet_path(p_legacy_chat)

    widget = HeardFloodsWidget(storage=temp_storage)

    # Filter by TRACE should find the legacy trace
    widget._on_filter_clicked("TRACE")
    assert any(not r.isHidden() and r.path.packet_id == "leg-trace" for r in widget._rows)

    # Filter by ADVERT should find the legacy advert
    widget._on_filter_clicked("ADVERT")
    assert any(not r.isHidden() and r.path.packet_id == "leg-adv" for r in widget._rows)

    # Filter by Chat should find the legacy chat
    widget._on_filter_clicked("GRP_TXT")
    assert any(not r.isHidden() and r.path.packet_id == "leg-chat" for r in widget._rows)


def test_corescope_style_and_badge_colors(qapp):
    """Verifies that badge and card colors match CoreScope standards (#3B82F6, #F59E0B)."""
    p_flood = PacketPathInfo(packet_id="f1", sender_id="s1", sender_name="N1", route_type="FLOOD")
    p_direct = PacketPathInfo(packet_id="d1", sender_id="s2", sender_name="N2", route_type="DIRECT")

    row_flood = FloodRowWidget(p_flood, is_unread=True)
    row_direct = FloodRowWidget(p_direct, is_unread=False)

    # Flood badge should be CoreScope Blue (#3B82F6)
    assert "#3B82F6" in row_flood.lbl_badge.styleSheet()
    # Direct badge should be CoreScope Amber (#F59E0B)
    assert "#F59E0B" in row_direct.lbl_badge.styleSheet()

    # Unread badge should have #3B82F6 border
    assert "#3B82F6" in row_flood.lbl_unread.styleSheet()


def test_source_badge_and_filtering(qapp, temp_storage):
    """Verifies that RF and MQTT packets display distinct source pills and can be filtered."""
    p_rf = PacketPathInfo(
        packet_id="rf-1", sender_id="rf_node", sender_name="Radio Sender",
        route_type="FLOOD", payload_type="GRP_TXT", source="radio"
    )
    p_mqtt = PacketPathInfo(
        packet_id="mqtt-1", sender_id="mqtt_node", sender_name="MQTT Broker",
        route_type="FLOOD", payload_type="GRP_TXT", source="mqtt"
    )

    row_rf = FloodRowWidget(p_rf)
    row_mqtt = FloodRowWidget(p_mqtt)

    assert hasattr(row_rf, "lbl_source")
    assert hasattr(row_mqtt, "lbl_source")
    assert "📻 RF" in row_rf.lbl_source.text()
    assert "🌐 MQTT" in row_mqtt.lbl_source.text()
    assert "#10B981" in row_rf.lbl_source.styleSheet()
    assert "#F59E0B" in row_mqtt.lbl_source.styleSheet()

    temp_storage.save_packet_path(p_rf)
    temp_storage.save_packet_path(p_mqtt)

    widget = HeardFloodsWidget(storage=temp_storage)
    assert len(widget._rows) == 2

    # Filter by RADIO
    widget._on_filter_clicked("RADIO")
    visible_rf = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_rf) == 1
    assert visible_rf[0].path.packet_id == "rf-1"

    # Filter by MQTT
    widget._on_filter_clicked("MQTT")
    visible_mqtt = [r for r in widget._rows if not r.isHidden()]
    assert len(visible_mqtt) == 1
    assert visible_mqtt[0].path.packet_id == "mqtt-1"


def test_heard_floods_rf_supersedes_mqtt_for_same_packet(qapp, temp_storage):
    """Verifies that when a physical RF radio packet arrives, it supersedes an existing MQTT row."""
    widget = HeardFloodsWidget(storage=temp_storage)

    # 1. MQTT arrives first
    p_mqtt = PacketPathInfo(
        packet_id="mqtt-test-1", sender_id="node_x", sender_name="[#cumbria] Node X",
        route_type="FLOOD", payload_type="GRP_TXT", source="mqtt",
        decoded_info={"channel": "cumbria", "text": "Testing LoRa mesh"}
    )
    widget._on_live_path_traced(p_mqtt)
    assert len(widget._rows) == 1
    assert widget._rows[0].path.source == "mqtt"
    assert "🌐 MQTT" in widget._rows[0].lbl_source.text()

    # 2. Radio hears it over physical LoRa RF
    p_rf = PacketPathInfo(
        packet_id="path-radio-test-1", sender_id="node_x", sender_name="[#cumbria] Node X",
        route_type="FLOOD", payload_type="GRP_TXT", source="radio",
        hop_nodes=["Repeater-1"],
        decoded_info={"channel": "cumbria", "text": "Testing LoRa mesh"}
    )
    widget._on_live_path_traced(p_rf)

    # 3. RF must replace MQTT: total rows should still be 1, but source is now RF
    assert len(widget._rows) == 1
    assert widget._rows[0].path.source == "radio"
    assert "📻 RF" in widget._rows[0].lbl_source.text()
    assert widget._rows[0].path.packet_id == "path-radio-test-1"

    # 4. If a duplicate MQTT packet arrives again, it must be suppressed
    p_mqtt_dup = PacketPathInfo(
        packet_id="mqtt-test-2", sender_id="node_x", sender_name="[#cumbria] Node X",
        route_type="FLOOD", payload_type="GRP_TXT", source="mqtt",
        decoded_info={"channel": "cumbria", "text": "Testing LoRa mesh"}
    )
    widget._on_live_path_traced(p_mqtt_dup)
    assert len(widget._rows) == 1
    assert widget._rows[0].path.source == "radio"




