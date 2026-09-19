import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QMouseEvent

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import MessageEnvelope, PacketPathInfo
from meshcore_tray.storage import Storage
from meshcore_tray.ui.activity_timeline_widget import (
    ActivityTimelineCanvas,
    NetworkActivityTimelineWidget,
)
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, get_leaflet_html
from meshcore_tray.ui.nav_dock import MapLayerDockWidget


def test_storage_get_packet_timeline_timestamps(tmp_path):
    """Verifies that Storage retrieves epoch timestamps within cutoff window."""
    db_file = tmp_path / "test_timeline.db"
    storage = Storage(db_file)

    now = datetime.now()
    t_recent1 = (now - timedelta(minutes=10)).isoformat()
    t_recent2 = (now - timedelta(hours=2)).isoformat()
    t_old = (now - timedelta(hours=30)).isoformat()

    # Save packet paths
    storage.save_packet_path(
        PacketPathInfo(
            packet_id="pkt-recent",
            timestamp=t_recent1,
            route_type="FLOOD",
            hop_nodes=["repA", "repB"],
            sender_id="sender1",
            sender_name="Alice"
        )
    )
    storage.save_packet_path(
        PacketPathInfo(
            packet_id="pkt-old",
            timestamp=t_old,
            route_type="DIRECT",
            hop_nodes=[],
            sender_id="senderOld",
            sender_name="OldNode"
        )
    )

    # Save message envelope
    storage.save_message(
        MessageEnvelope(
            id="msg-1",
            timestamp=t_recent2,
            source_driver="meshcore_serial",
            sender_id="sender2",
            sender_name="Bob",
            channel="Public",
            text="Recent ping"
        )
    )

    # Query 24h timeline
    timestamps_24h = storage.get_packet_timeline_timestamps(hours=24)
    assert len(timestamps_24h) == 2
    assert all(isinstance(ts, float) for ts in timestamps_24h)
    assert timestamps_24h[0] <= timestamps_24h[1]

    # Query 1h timeline (should only contain t_recent1)
    timestamps_1h = storage.get_packet_timeline_timestamps(hours=1)
    assert len(timestamps_1h) == 1


def test_activity_timeline_widget_and_canvas(qapp, tmp_path):
    """Verifies that ActivityTimelineCanvas buckets packets and NetworkActivityTimelineWidget controls work."""
    db_file = tmp_path / "test_widget.db"
    storage = Storage(db_file)
    config = AppConfig()

    widget = NetworkActivityTimelineWidget(storage=storage, config=config)
    canvas = widget.canvas
    assert canvas.buckets == 100
    assert canvas.scope_hours == 24

    # Add timestamps into canvas
    now_ms = time.time() * 1000.0
    canvas.add_timestamp(now_ms - 5000)  # 5s ago
    canvas.add_timestamp(now_ms - 60000) # 1m ago
    assert len(canvas.timestamps) == 2
    assert canvas.max_count >= 1

    # Scope button switching
    widget.set_scope(1)
    assert canvas.scope_hours == 1
    assert widget.current_scope_hours == 1
    assert "AA55FF" in widget.btn_1h.styleSheet()
    assert widget.btn_24h.styleSheet() == ""

    widget.set_scope(6)
    assert canvas.scope_hours == 6
    assert "AA55FF" in widget.btn_6h.styleSheet()

    widget.set_scope(12)
    assert canvas.scope_hours == 12
    assert "AA55FF" in widget.btn_12h.styleSheet()

    widget.set_scope(24)
    assert canvas.scope_hours == 24
    assert "AA55FF" in widget.btn_24h.styleSheet()

    # Record packet through widget API
    count_before = len(canvas.timestamps)
    widget.record_packet()
    assert len(canvas.timestamps) == count_before + 1

    # Close signal
    close_emitted = []
    widget.close_requested.connect(lambda: close_emitted.append(True))
    widget.btn_close.click()
    assert len(close_emitted) == 1

    # Canvas scrubber interaction
    scrub_times = []
    canvas.time_scrubbed.connect(lambda t: scrub_times.append(t))
    canvas.resize(400, 30)
    canvas._handle_mouse(200.0)
    assert canvas.playhead_pct == 0.5
    assert len(scrub_times) == 1


def test_mesh_map_hud_legend_and_timeline_controls(qapp, tmp_path):
    """Verifies that MeshMapWidget controls HUD, legend, and bottom split timeline correctly."""
    db_file = tmp_path / "test_map.db"
    storage = Storage(db_file)
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # 1. Packet Feed HUD toggle
    hud_states = []
    map_widget.packet_hud_toggled.connect(lambda v: hud_states.append(v))
    map_widget.set_packet_hud_visible(True)
    assert map_widget.show_packet_hud is True
    assert hud_states[-1] is True
    assert map_widget.web_view.page().runJavaScript.called
    last_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setPacketHudVisible(true)" in last_js

    map_widget.set_packet_hud_visible(False)
    assert map_widget.show_packet_hud is False
    assert hud_states[-1] is False

    # Bridge HUD round-trip
    map_widget._on_bridge_packet_hud_toggled(True)
    assert map_widget.show_packet_hud is True
    assert hud_states[-1] is True

    # 2. Map Legend toggle
    legend_states = []
    map_widget.map_legend_toggled.connect(lambda v: legend_states.append(v))
    map_widget.set_map_legend_visible(True)
    assert map_widget.show_map_legend is True
    assert legend_states[-1] is True
    legend_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "toggleMapLegend(true)" in legend_js

    map_widget.set_map_legend_visible(False)
    assert map_widget.show_map_legend is False
    assert legend_states[-1] is False

    # Bridge Legend round-trip
    map_widget._on_bridge_map_legend_toggled(True)
    assert map_widget.show_map_legend is True
    assert legend_states[-1] is True

    # 3. Activity Timeline split-view toggle
    timeline_states = []
    map_widget.activity_timeline_toggled.connect(lambda v: timeline_states.append(v))
    map_widget.set_activity_timeline_visible(True)
    assert map_widget.show_activity_timeline is True
    assert timeline_states[-1] is True
    assert not map_widget.activity_timeline_dock.isHidden()

    map_widget.set_activity_timeline_visible(False)
    assert map_widget.show_activity_timeline is False
    assert timeline_states[-1] is False
    assert map_widget.activity_timeline_dock.isHidden()


def test_packet_path_and_message_hud_and_timeline_dispatch(qapp, tmp_path):
    """Verifies that live traces and received messages push into HUD ticker and timeline."""
    db_file = tmp_path / "test_dispatch.db"
    storage = Storage(db_file)
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    initial_timeline_count = len(map_widget.activity_timeline_dock.canvas.timestamps)

    # 1. Traced multi-hop packet
    path = PacketPathInfo(
        packet_id="pkt-trace-1",
        timestamp=datetime.now().isoformat(),
        route_type="FLOOD",
        hop_nodes=["Alpha", "Bravo"],
        coordinates=[[54.5, -3.3]],
        sender_id="senderA",
        sender_name="Alice"
    )
    map_widget._on_packet_path_traced(path)

    # Timeline count incremented
    assert len(map_widget.activity_timeline_dock.canvas.timestamps) == initial_timeline_count + 1

    # JavaScript addPacketToHud and drawPacketPath called
    calls = [c[0][0] for c in map_widget.web_view.page().runJavaScript.call_args_list]
    assert any("addPacketToHud" in c for c in calls)
    assert any("drawPacketPath" in c for c in calls)

    # 2. Incoming message envelope
    map_widget.web_view.page().runJavaScript.reset_mock()
    msg = MessageEnvelope(
        id="m-live-1",
        timestamp=datetime.now().isoformat(),
        source_driver="meshcore_serial",
        sender_id="senderB",
        sender_name="Bob",
        channel="Public",
        text="Hello from radio!",
        metadata={"route_type": "FLOOD"}
    )
    map_widget._on_message_received(msg)

    # Timeline count incremented again
    assert len(map_widget.activity_timeline_dock.canvas.timestamps) == initial_timeline_count + 2

    msg_calls = [c[0][0] for c in map_widget.web_view.page().runJavaScript.call_args_list]
    assert any("addPacketToHud" in c for c in msg_calls)


def test_nav_dock_layer_buttons_and_sync(qapp):
    """Verifies that MapLayerDockWidget includes packet_hud, activity_timeline, and map_legend."""
    dock = MapLayerDockWidget()

    assert hasattr(dock, "btn_packet_hud")
    assert hasattr(dock, "btn_activity_timeline")
    assert hasattr(dock, "btn_map_legend")

    toggled_layers = []
    dock.layer_toggled.connect(lambda k, v: toggled_layers.append((k, v)))

    # Test toggles
    dock.btn_packet_hud.setChecked(True)
    assert ("packet_hud", True) in toggled_layers

    dock.btn_activity_timeline.setChecked(True)
    assert ("activity_timeline", True) in toggled_layers

    dock.btn_map_legend.setChecked(True)
    assert ("map_legend", True) in toggled_layers

    # Test programmatic set_layer_active without signal emission
    toggled_layers.clear()
    dock.set_layer_active("packet_hud", False)
    assert not dock.btn_packet_hud.isChecked()
    assert len(toggled_layers) == 0

    dock.set_layer_active("activity_timeline", False)
    assert not dock.btn_activity_timeline.isChecked()
    assert len(toggled_layers) == 0

    dock.set_layer_active("map_legend", False)
    assert not dock.btn_map_legend.isChecked()
    assert len(toggled_layers) == 0


def test_leaflet_html_template_contains_corescope_elements():
    """Verifies that Leaflet HTML template has CoreScope HUD, Legend, and canvas animation structures."""
    html = get_leaflet_html()

    assert "live-packet-hud" in html
    assert "livePacketHud" in html
    assert "liveLegend" in html
    assert "PACKET TYPES" in html
    assert "NODE ROLES" in html
    assert "TYPE_COLORS" in html
    assert "setPacketHudVisible" in html
    assert "toggleMapLegend" in html
    assert "cycleHudCorner" in html
    assert "addPacketToHud" in html
    assert "renderCanvasAnimations" in html


def test_corescope_node_colors_and_fade_mask_override():
    """Verifies that original node colors are overridden with CoreScope canonical colors
    and that the top fade mask on the HUD feed has been eliminated."""
    config = AppConfig()
    colors = config.app_colors

    # CoreScope node colors
    assert colors.map_repeater_color.upper() == "#3B82F6"
    assert colors.map_repeater_hover_color.upper() == "#60A5FA"
    assert colors.map_companion_color.upper() == "#06B6D4"
    assert colors.map_companion_hover_color.upper() == "#22D3EE"
    assert colors.map_room_server_color.upper() == "#A855F7"
    assert colors.map_room_server_hover_color.upper() == "#C084FC"

    html = get_leaflet_html()

    # CSS :root variables
    assert "--repeater-color: #3B82F6;" in html
    assert "--repeater-hover-color: #60A5FA;" in html
    assert "--companion-color: #06B6D4;" in html
    assert "--companion-hover-color: #22D3EE;" in html
    assert "--room-server-color: #A855F7;" in html
    assert "--room-server-hover-color: #C084FC;" in html

    # Top fade mask must be completely removed so newest packets at top are crisp
    assert "linear-gradient(to bottom, transparent" not in html
    assert "mask-image: none;" in html
    assert "-webkit-mask-image: none;" in html


def test_corescope_type_colors_and_node_ping():
    """Verifies that TYPE_COLORS maps FLOOD/DIRECT to CoreScope legend colors
    and that pulseOriginNode supports dynamic packet type tinting."""
    html = get_leaflet_html()

    # Type color palette matching legend
    assert "'FLOOD': '#3B82F6'" in html
    assert "'DIRECT': '#F59E0B'" in html
    assert "'GRP_TXT': '#3B82F6'" in html
    assert "'TXT_MSG': '#F59E0B'" in html
    assert "'ADVERT': '#22C55E'" in html
    assert "'REQ': '#A855F7'" in html
    assert "'TRACE': '#EC4899'" in html

    # Dynamic pulse styling
    assert "function pulseOriginNode(coord, senderId, senderName, color)" in html
    assert "pingColor = color || '#3B82F6'" in html
    assert "border-color: ' + pingColor" in html


def test_settings_widget_mqtt_community_presets(qapp, tmp_path):
    """Verifies that SettingsDialog provides togglable open MQTT stream presets."""
    from meshcore_tray.ui.settings_widget import SettingsDialog

    db_file = tmp_path / "test_mqtt_presets.db"
    storage = Storage(db_file)
    config = AppConfig()

    dialog = SettingsDialog(config=config, storage=storage)
    assert hasattr(dialog, "combo_mqtt_preset")

    # 1. Lincomatic MeshCore Broker
    dialog.combo_mqtt_preset.setCurrentIndex(1)
    assert dialog.mqtt_host_input.text() == "mqtt.lincomatic.com"
    assert dialog.mqtt_port_spin.value() == 8883
    assert dialog.chk_mqtt_tls.isChecked() is True
    assert "meshcore/#" in dialog.mqtt_topics_input.text()

    # 2. 🇬🇧 IPNet UK MeshCore Observer
    dialog.combo_mqtt_preset.setCurrentIndex(2)
    assert dialog.mqtt_host_input.text() == "mqtt.ipnt.uk"
    assert dialog.mqtt_port_spin.value() == 1883
    assert "meshcore/uk/#" in dialog.mqtt_topics_input.text()

    # 3. 🇬🇧 NorthMesh UK MeshCore Network
    dialog.combo_mqtt_preset.setCurrentIndex(3)
    assert dialog.mqtt_host_input.text() == "mqtt.northmesh.co.uk"
    assert dialog.mqtt_port_spin.value() == 1883
    assert "meshcore/uk/#" in dialog.mqtt_topics_input.text()

    # 4. Local Bridge / meshcoretomqtt
    dialog.combo_mqtt_preset.setCurrentIndex(4)
    assert dialog.mqtt_host_input.text() == "localhost"
    assert dialog.mqtt_port_spin.value() == 1883
    assert dialog.chk_mqtt_tls.isChecked() is False

    # 5. EMQX Sandbox
    dialog.combo_mqtt_preset.setCurrentIndex(5)
    assert dialog.mqtt_host_input.text() == "broker.emqx.io"
    assert dialog.mqtt_port_spin.value() == 1883


def test_corescope_carto_dark_map_base_layer(qapp):
    """Verifies that CoreScope Dark (Carto pitch black landmass & dark grey ocean) is available as a 3-way selectable map."""
    html = get_leaflet_html()
    assert "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" in html
    assert "var corescopeBaseLayer = L.tileLayer" in html
    assert "type === 'corescope' || type === 'carto'" in html
    assert "function setCartoApiKey(key)" in html
    assert "function getCartoTileUrl(key)" in html

    cfg = AppConfig()
    cfg.map_base_layer = "canvas"
    cfg.carto_api_key = "test_carto_secret_123"
    widget = MeshMapWidget(config=cfg)

    # 1. Switch to CoreScope
    widget.set_base_map_layer("corescope")
    assert widget._current_base_layer == "corescope"
    assert cfg.map_base_layer == "corescope"
    assert "CoreScope Dark" in widget.watcher_status.text()

    # 2. Cycle to Topo
    widget._on_toggle_base_map_clicked()
    assert widget._current_base_layer == "topo"
    assert cfg.map_base_layer == "topo"

    # 3. Cycle to Canvas
    widget._on_toggle_base_map_clicked()
    assert widget._current_base_layer == "canvas"
    assert cfg.map_base_layer == "canvas"

    # 4. Cycle back to CoreScope
    widget._on_toggle_base_map_clicked()
    assert widget._current_base_layer == "corescope"
    assert cfg.map_base_layer == "corescope"

    # 5. Right-click context menu opens cleanly
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QMenu, QDialog
    with patch.object(QMenu, "exec", return_value=None):
        widget._show_base_map_menu(QPoint(10, 10))

    # 6. Prompt Carto API Key Dialog executes and applies key
    with patch.object(QDialog, "exec", return_value=None):
        widget.prompt_carto_api_key()

    # 7. SettingsWidget contains base map and carto key inputs
    from meshcore_tray.ui.settings_widget import SettingsWidget
    sw = SettingsWidget(config=cfg)
    assert hasattr(sw, "combo_map_base")
    assert hasattr(sw, "txt_carto_key")
    assert sw.txt_carto_key.text() == "test_carto_secret_123"

    # Changing settings and saving persists them
    sw.combo_map_base.setCurrentIndex(sw.combo_map_base.findData("corescope"))
    sw.txt_carto_key.setText("new_carto_key_999")
    sw._on_save_theme_clicked()
    assert cfg.map_base_layer == "corescope"
    assert cfg.carto_api_key == "new_carto_key_999"


def test_live_hud_single_entry_and_mqtt_replacement(qapp, tmp_path):
    """Verifies that:
    1. drawPacketPath does NOT call addPacketToHud (preventing duplicate insertion)
    2. HTML template contains MQTT removal logic when radio reception arrives
    3. Pulse and ping throttling prevents double map pings
    4. MeshMapWidget suppresses duplicate dispatches between _on_packet_path_traced and _on_message_received.
    """
    html = get_leaflet_html()

    # 1. drawPacketPath must NOT call addPacketToHud
    # Find drawPacketPath body
    dpp_idx = html.find("function drawPacketPath(coords, meta)")
    assert dpp_idx != -1
    dpp_body = html[dpp_idx:dpp_idx + 1200]
    assert "window.addPacketToHud" not in dpp_body

    # 2. HTML template contains deduplication and MQTT removal on RF arrival
    # 2. HTML template contains deduplication and MQTT removal on RF arrival within 3s window
    assert "existingItem.remove()" in html
    assert "now - cached.time < 3000" in html
    assert "data-event-key" in html
    assert "_recentPulses" in html
    assert "_recentNodePings" in html

    # 3. Room server diamonds rendered on top with roomServerPane and zIndexOffset 12000
    assert "map.createPane('roomServerPane')" in html
    assert "roomServerPane" in html
    assert "node-marker-wrap-room" in html
    assert "12000" in html

    # 4. Python level deduplication in MeshMapWidget
    db_file = tmp_path / "dedup_hud.db"
    storage = Storage(db_file)
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Same message received via PACKET_PATH_TRACED then MESSAGE_RECEIVED
    shared_id = "msg-unique-12345"
    path = PacketPathInfo(
        packet_id=f"path-{shared_id}",
        timestamp=datetime.now().isoformat(),
        route_type="FLOOD",
        payload_type="GRP_TXT",
        sender_id="n1",
        sender_name="Alice",
        decoded_info={"text": "Hello Cumbria", "channel": "cumbria"}
    )
    msg = MessageEnvelope(
        id=shared_id,
        timestamp=datetime.now().isoformat(),
        sender_id="n1",
        sender_name="Alice",
        channel="cumbria",
        text="Hello Cumbria",
        metadata={"route_type": "FLOOD"}
    )

    map_widget._on_packet_path_traced(path)
    hud_calls_1 = len(map_widget.web_view.page().runJavaScript.call_args_list)
    assert hud_calls_1 >= 1

    # Second dispatch for the SAME message must be dropped
    map_widget._on_message_received(msg)
    hud_calls_2 = len(map_widget.web_view.page().runJavaScript.call_args_list)
    assert hud_calls_2 == hud_calls_1, "Duplicate message should not generate additional JS calls"




