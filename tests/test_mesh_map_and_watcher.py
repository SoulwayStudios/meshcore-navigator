"""Unit tests for Mesh Map & Packet Path Watcher functionality."""

import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import NodeContact, NeighbourInfo, PacketPathInfo, MessageEnvelope
from meshcore_tray.storage import Storage
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test"])
    return app


def test_storage_coordinates_and_packet_paths(tmp_path):
    """Verifies that latitude, longitude, and packet paths are stored and queried correctly."""
    db_path = tmp_path / "map_test.db"
    storage = Storage(db_path)

    # 1. Save contacts with coordinates
    yagi = NodeContact(
        node_id="d79870ac338f",
        alias="M7NCY West Yagi v4",
        is_repeater=True,
        latitude=54.65897,
        longitude=-3.4346,
        snr_db=13.8,
        rssi_dbm=-19.0
    )
    user_node = NodeContact(
        node_id="3705763e392d",
        alias="M7NCY Local",
        is_repeater=False,
        latitude=54.5500,
        longitude=-3.3000,
        snr_db=9.5,
        rssi_dbm=-85.0
    )
    no_coord_node = NodeContact(
        node_id="aabbccddeeff",
        alias="No GPS Node",
        latitude=None,
        longitude=None
    )
    storage.save_contact(yagi)
    storage.save_contact(user_node)
    storage.save_contact(no_coord_node)

    with_coords = storage.get_nodes_with_coordinates()
    assert len(with_coords) == 2
    aliases = [c.alias for c in with_coords]
    assert "M7NCY West Yagi v4" in aliases
    assert "M7NCY Local" in aliases
    assert "No GPS Node" not in aliases

    # 2. Save neighbours with coordinates
    neighbour = NeighbourInfo(
        node_id="d79870ac338f",
        alias="M7NCY West Yagi v4",
        snr_db=13.8,
        latitude=54.65897,
        longitude=-3.4346
    )
    storage.save_neighbour(neighbour)
    retrieved_n = storage.get_neighbours()
    assert len(retrieved_n) >= 1
    assert retrieved_n[0].latitude == 54.65897

    # 3. Save and retrieve PacketPathInfo
    path_info = PacketPathInfo(
        packet_id="pkt-test-01",
        sender_id="d79870ac338f",
        sender_name="M7NCY West Yagi v4",
        hop_nodes=["@M7NCY West Yagi v4", "@Local"],
        hop_snrs=[13.8, 9.5],
        route_type="FLOOD",
        coordinates=[[54.65897, -3.4346], [54.5500, -3.3000]]
    )
    storage.save_packet_path(path_info)
    paths = storage.get_recent_packet_paths(limit=10)
    assert len(paths) == 1
    assert paths[0].packet_id == "pkt-test-01"
    assert len(paths[0].coordinates) == 2
    assert paths[0].route_type == "FLOOD"


def test_mesh_map_widget_ui_and_controls(qapp, tmp_path):
    """Verifies that MeshMapWidget builds correctly, has functional filter buttons, and updates badges."""
    from unittest.mock import patch
    db_path = tmp_path / "map_widget_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    yagi = NodeContact(
        node_id="d79870ac338f",
        alias="M7NCY West Yagi v4",
        is_repeater=True,
        latitude=54.65897,
        longitude=-3.4346
    )
    storage.save_contact(yagi)
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    assert map_widget.stats_badge.text() == "1 Nodes • 1 Repeaters"

    # Test toggles
    map_widget.btn_repeaters.setChecked(True)
    map_widget._on_repeaters_toggle()
    assert map_widget.show_repeaters_only is True

    map_widget.btn_links.setChecked(False)
    map_widget._on_links_toggle()
    assert map_widget.show_rf_links is False

    # Test packet path event handling
    path_info = PacketPathInfo(
        packet_id="pkt-live-99",
        sender_id="d79870ac338f",
        sender_name="M7NCY West Yagi v4",
        hop_nodes=["@M7NCY West Yagi v4", "@Local"],
        hop_snrs=[12.5],
        route_type="ROUTED",
        coordinates=[[54.65897, -3.4346], [54.5500, -3.3000]]
    )
    map_widget._on_packet_path_traced(path_info)
    assert "ROUTED" in map_widget.watcher_status.text()
    assert "@M7NCY West Yagi v4" in map_widget.watcher_status.text()


def test_main_window_4_pane_layout_and_toggle(qapp, tmp_path):
    """Verifies that MainWindow correctly embeds the Map widget as Pane 2 in the 4-pane splitter and toggles."""
    from unittest.mock import patch
    db_path = tmp_path / "main_map_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage)

    # Verify 4-pane splitter
    assert win.main_splitter.count() == 4
    assert win.main_splitter.widget(0) == win.sidebar
    assert win.main_splitter.widget(1) == win.center_stack
    assert win.main_splitter.widget(2) == win.mesh_map
    assert win.main_splitter.widget(3) == win.pixoo_panel

    win.show()

    # Verify toggle button
    assert win.btn_toggle_map.isChecked() is True
    assert not win.mesh_map.isHidden()

    win.btn_toggle_map.setChecked(False)
    win._on_toggle_map()
    assert win.mesh_map.isHidden() is True

    win.btn_toggle_map.setChecked(True)
    win._on_toggle_map()
    assert win.mesh_map.isHidden() is False


def test_incoming_message_draws_green_route_on_map(qapp, tmp_path):
    """Verifies that incoming messages resolve route coordinates and dispatch green line path drawing."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.ui.mesh_map_widget import MeshMapWidget

    storage = Storage(tmp_path / "green_route_test.db")
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY Local"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # Save sender and repeater contacts with coordinates
    sender = NodeContact("!sender1", "Alice", latitude=53.48, longitude=-2.24)
    repeater = NodeContact("rep_mid", "Midland Repeater", latitude=54.10, longitude=-2.90, is_repeater=True)
    storage.save_contact(sender)
    storage.save_contact(repeater)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True
    map_widget.show_paths = True

    # Incoming message with intermediate repeater path
    msg = MessageEnvelope(
        id="m_in_1",
        sender_id="!sender1",
        sender_name="Alice",
        channel="Public",
        text="Hello from Manchester!",
        is_outgoing=False,
        metadata={"path": "rep_mid", "path_len": 1, "route_type": "FLOOD"}
    )

    map_widget._on_message_received(msg)

    # Verify runJavaScript was called with green route parameters
    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "drawPacketPath" in call_arg
    assert '"color": "green"' in call_arg
    assert '"is_incoming": true' in call_arg
    assert "53.48" in call_arg  # sender
    assert "54.1" in call_arg   # repeater
    assert "54.65897" in call_arg  # local destination


def test_visualise_message_path_with_known_and_unknown_repeaters(qapp, tmp_path):
    """Verifies that visualising a message path on the map loads the dotted line,
    resolves known repeaters with coordinates and highlights, marks missing ones as '?? Unknown',
    and supports clearing."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.core.event_bus import bus, EventType

    db_path = tmp_path / "vis_path_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346
    config.app_colors.map_visualised_path_color = "#FFA500"

    # Known sender and known repeater
    sender = NodeContact("node_bob", "Bob", latitude=53.48, longitude=-2.24)
    repeater1 = NodeContact("rep_01", "Helvellyn RPTR", latitude=54.52, longitude=-3.01, is_repeater=True)
    storage.save_contact(sender)
    storage.save_contact(repeater1)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Message path with 2 hops: rep_01 (known), unk_02 (unknown)
    msg = MessageEnvelope(
        id="m_vis_1",
        sender_id="node_bob",
        sender_name="Bob",
        channel="Public",
        text="Testing multi-hop visualisation",
        is_outgoing=False,
        metadata={"path": "rep_01unk_02", "path_len": 2, "route_type": "FLOOD"}
    )

    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "drawVisualisedMessagePath" in call_arg
    assert '"color": "#FFA500"' in call_arg
    assert "Helvellyn RPTR" in call_arg
    assert "?? Unknown" in call_arg
    assert "53.48" in call_arg  # Sender
    assert "54.52" in call_arg  # Known repeater
    assert "54.65897" in call_arg  # Local receiver
    assert "Path:" in map_widget.watcher_status.text()
    assert "2 repeaters (1 known, 1 unknown)" in map_widget.watcher_status.text()

    # Test Clear Path
    map_widget.clear_visualised_path()
    clear_call = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "clearVisualisedPath()" in clear_call
    assert "Listening for live RF packet paths" in map_widget.watcher_status.text()


def test_smart_hop_resolution_and_home_node_termination(qapp, tmp_path):
    """Verifies that 1-byte hop prefixes (like 9c, 62, d7) disambiguate to local Cumbria & NW repeaters
    (TinHub, MCC Allotment Rep, M7NCY West Yagi v4), and path sequence ends at Home Node M7NCY."""
    from unittest.mock import MagicMock
    import json

    db_path = tmp_path / "smart_hop_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # Populate candidates with collisions
    # 9c collision: Buckinghamshire vs Lancashire (TinHub)
    storage.save_contact(NodeContact("9cfea8e6b301", "M9YJR-RPT", latitude=51.79736, longitude=-0.78351, is_repeater=True))
    storage.save_contact(NodeContact("9c8dd7797c1e", "TinHub", latitude=53.796167, longitude=-2.295937, is_repeater=True))

    # 62: MCC Allotment
    storage.save_contact(NodeContact("627c826257c1", "🌐 MCC Allotment Rep", latitude=54.63682, longitude=-3.53887, is_repeater=True))

    # d7 collision: Southport vs M7NCY West Yagi v4
    storage.save_contact(NodeContact("d7396a18198b", "Southport Rptr", latitude=53.652142, longitude=-2.998592, is_repeater=True))
    storage.save_contact(NodeContact("d79870ac338f", "M7NCY West Yagi v4", latitude=54.65897, longitude=-3.4346, is_repeater=True))

    # Test storage disambiguation directly
    c_9c = storage.get_best_contact_for_hop("9c", ref_lat=54.65897, ref_lon=-3.4346, user_station_prefix="M7NCY")
    assert c_9c is not None and c_9c.alias == "TinHub"

    c_62 = storage.get_best_contact_for_hop("62", ref_lat=54.65897, ref_lon=-3.4346, user_station_prefix="M7NCY")
    assert c_62 is not None and "MCC Allotment" in c_62.alias

    c_d7 = storage.get_best_contact_for_hop("d7", ref_lat=54.65897, ref_lon=-3.4346, user_station_prefix="M7NCY")
    assert c_d7 is not None and c_d7.alias == "M7NCY West Yagi v4"

    from unittest.mock import patch
    # Test map path visualisation with 4 hops: 9c, fd, 62, d7
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    msg = MessageEnvelope(
        id="m_len_path",
        sender_id="!len_t096",
        sender_name="Len T096",
        channel="Public",
        text="If dry",
        is_outgoing=False,
        metadata={"path": "9cfd62d7", "path_len": 4, "route_type": "FLOOD"}
    )
    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]

    # Check that real repeater names appear, and unknown is marked ?? Unknown
    assert "TinHub" in call_arg
    assert "MCC Allotment" in call_arg
    assert "M7NCY West Yagi v4" in call_arg
    assert "?? Unknown (fd)" in call_arg

    # Parse coords from call_arg
    start_idx = call_arg.find("drawVisualisedMessagePath(") + len("drawVisualisedMessagePath(")
    end_idx = call_arg.rfind(");")
    args_str = call_arg[start_idx:end_idx]
    
    # Check that home station coordinates are the final destination
    assert "[54.65897, -3.4346]" in call_arg
    assert '"home_name": "M7NCY"' in call_arg


def test_closest_repeater_prioritisation_and_duplicate_highlighting(qapp, tmp_path):
    """Verifies that repeaters geographically closest to home location are prioritized when prefixes collide,
    that ambiguous repeaters are flagged with candidates and distances, and that popup closure cleans up path."""
    from unittest.mock import MagicMock, patch
    import json

    db_path = tmp_path / "closest_rep_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # 9c collision:
    # 1. Distant in Buckinghamshire (~364 km)
    storage.save_contact(NodeContact("9cfea8e6b301", "M9YJR-RPT", latitude=51.79736, longitude=-0.78351, is_repeater=True))
    # 2. Closer in Lancashire (~121 km)
    storage.save_contact(NodeContact("9c8dd7797c1e", "TinHub", latitude=53.796167, longitude=-2.295937, is_repeater=True))

    best, cands, amb = storage.resolve_hop_with_candidates("9c", ref_lat=54.65897, ref_lon=-3.4346)
    assert best is not None
    assert best.alias == "TinHub"
    assert amb is True
    assert len(cands) == 2
    assert cands[0]["alias"] == "TinHub"
    assert cands[0]["dist_km"] < cands[1]["dist_km"]

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    msg = MessageEnvelope(
        id="m_amb_msg",
        sender_id="!src_node",
        sender_name="Alice",
        channel="Public",
        text="Hello via duplicate repeater",
        is_outgoing=False,
        metadata={"path": "9c", "path_len": 1, "route_type": "FLOOD"}
    )
    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]

    assert '"is_ambiguous": true' in call_arg
    assert "TinHub" in call_arg
    assert "M9YJR-RPT" in call_arg
    assert '"color": "#FF00FF"' in call_arg
    assert '"heading_color": "#FF00FF"' in call_arg
    assert "1 with alternates" in map_widget.watcher_status.text()

    # Test closing visualised popup event from Leaflet bridge
    map_widget._on_visualised_path_closed()
    assert "Listening for live RF packet paths" in map_widget.watcher_status.text()


def test_hop_preferences_persistence_and_future_routing(qapp, tmp_path):
    """Verifies that manually adjusting a repeater saves the choice in hop_route_preferences,
    prioritizes it for all future routing requests, and marks it as a saved preference."""
    from unittest.mock import MagicMock, patch
    import json

    db_path = tmp_path / "pref_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # 1. Two repeaters with prefix 66:
    # Closer: cumbria (3.7 km)
    # Further: silverdale (67.4 km)
    cumbria = NodeContact("667a88112233", "cumbriaCQ.com LFT", latitude=54.632, longitude=-3.401, is_repeater=True)
    silverdale = NodeContact("66cc55443322", "Silverdale ST5", latitude=54.167, longitude=-2.825, is_repeater=True)
    storage.save_contact(cumbria)
    storage.save_contact(silverdale)

    # Initial resolution picks geographically closest: cumbria
    best, cands, amb = storage.resolve_hop_with_candidates("66", ref_lat=54.65897, ref_lon=-3.4346)
    assert best.alias == "cumbriaCQ.com LFT"
    assert cands[0]["alias"] == "cumbriaCQ.com LFT"
    assert cands[0]["is_saved_preference"] is False

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # User manually switches hop to silverdale: trigger _on_hop_candidate_selected
    msg = MessageEnvelope(
        id="m_switch_msg",
        sender_id="!src_node",
        sender_name="Alice",
        channel="Public",
        text="Testing manual switch",
        is_outgoing=False,
        metadata={"path": "66", "path_len": 1}
    )
    storage.save_message(msg)

    map_widget._on_hop_candidate_selected("66", "66cc55443322", msg.id)

    # Verify persistence in SQLite
    pref = storage.get_hop_preference("66")
    assert pref == "66cc55443322"
    all_prefs = storage.get_all_hop_preferences()
    assert len(all_prefs) == 1
    assert all_prefs[0]["hop_prefix"] == "66"
    assert all_prefs[0]["alias"] == "Silverdale ST5"

    # Verify that ALL FUTURE routing requests now resolve to Silverdale ST5
    future_best, future_cands, _ = storage.resolve_hop_with_candidates("66", ref_lat=54.65897, ref_lon=-3.4346)
    assert future_best.alias == "Silverdale ST5"
    assert future_best.node_id == "66cc55443322"
    assert future_cands[0]["alias"] == "Silverdale ST5"
    assert future_cands[0]["is_saved_preference"] is True

    # Also verify get_best_contact_for_hop used by drivers
    driver_contact = storage.get_best_contact_for_hop("66", ref_lat=54.65897, ref_lon=-3.4346)
    assert driver_contact.alias == "Silverdale ST5"

    # Verify status text update
    assert "Saved" in map_widget.watcher_status.text()
    assert "Silverdale ST5" in map_widget.watcher_status.text()

    # Test delete preference
    assert storage.delete_hop_preference("66") is True
    assert storage.get_hop_preference("66") is None
    # Reverts to closest (cumbria)
    reverted_best, _, _ = storage.resolve_hop_with_candidates("66", ref_lat=54.65897, ref_lon=-3.4346)
    assert reverted_best.alias == "cumbriaCQ.com LFT"


def test_visualised_path_segment_coloring_known_vs_unknown(qapp, tmp_path):
    """Verifies that line segments connecting through unknown repeaters are colored Red (#EF4444)
    while segments between known repeaters remain Magenta (#FF00FF)."""
    from unittest.mock import MagicMock, patch
    import json

    db_path = tmp_path / "seg_color_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # 1. Sender (known with GPS)
    sender = NodeContact("node_sender", "Alice", latitude=53.48, longitude=-2.24)
    # 2. Hop 1: Known repeater
    rep1 = NodeContact("rep_known_01", "Helvellyn RPTR", latitude=54.52, longitude=-3.01, is_repeater=True)
    # 3. Hop 2: UNKNOWN repeater (no contact in database)
    storage.save_contact(sender)
    storage.save_contact(rep1)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Path: Alice -> Helvellyn RPTR (known) -> unk_hop2 (unknown) -> Home
    msg = MessageEnvelope(
        id="m_mixed_path",
        sender_id="node_sender",
        sender_name="Alice",
        channel="Public",
        text="Testing colored segments",
        is_outgoing=False,
        metadata={"path": "rep_known_01unk_hop2", "path_len": 2, "route_type": "FLOOD"}
    )
    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]

    # Segment 1: Alice -> Helvellyn RPTR (both known, no unknown hops in between) -> #FF00FF (Magenta)
    # Segment 2: Helvellyn RPTR -> Home (traverses unk_hop2, which is unknown) -> #EF4444 (Red)
    assert '"color": "#FF00FF"' in call_arg
    assert '"color": "#EF4444"' in call_arg
    assert '"is_unknown": true' in call_arg
    assert '"is_unknown": false' in call_arg


def test_origin_marker_and_hitbox_features(qapp, tmp_path):
    """Verifies that visualised message path passes sender_coord for origin highlighting
    and that the web view HTML template contains the 28px broad hitbox and seamless animation offset."""
    from unittest.mock import MagicMock, patch

    db_path = tmp_path / "origin_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    sender = NodeContact("node_origin_1", "Alice", latitude=53.48, longitude=-2.24)
    storage.save_contact(sender)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    msg = MessageEnvelope(
        id="m_orig",
        sender_id="node_origin_1",
        sender_name="Alice",
        channel="Public",
        text="Origin check",
        is_outgoing=False,
        metadata={"path": "", "path_len": 0}
    )
    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]
    # Sender coordinates must be passed for origin marker
    assert '"sender_coord": [53.48, -2.24]' in call_arg

    # Check that HTML template has seamless animation and 28px hitbox logic
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE
    assert "stroke-dashoffset: -40" in LEAFLET_HTML_TEMPLATE
    assert "weight: 28" in LEAFLET_HTML_TEMPLATE
    assert "origin-path-highlight" in LEAFLET_HTML_TEMPLATE
    assert "ORIGIN" in LEAFLET_HTML_TEMPLATE


def test_visualised_path_black_dotted_line_for_repeater_with_no_gps(qapp, tmp_path):
    """Verifies that when a known repeater in route has no GPS, the line segment is colored
    black (#000000) and marks is_no_gps=true."""
    from unittest.mock import MagicMock, patch

    db_path = tmp_path / "no_gps_rep_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY"
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346

    # 1. Sender (with GPS)
    sender = NodeContact("node_sender", "Alice", latitude=53.48, longitude=-2.24)
    # 2. Repeater 1: Known repeater, but NO GPS coordinates (latitude=None, longitude=None)
    rep_no_gps = NodeContact("rep_nogps_01", "Winston RPTR", latitude=None, longitude=None, is_repeater=True)

    storage.save_contact(sender)
    storage.save_contact(rep_no_gps)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Path: Alice -> Winston RPTR (known, but no GPS) -> Home
    msg = MessageEnvelope(
        id="m_no_gps_path",
        sender_id="node_sender",
        sender_name="Alice",
        channel="Public",
        text="Testing no gps repeater",
        is_outgoing=False,
        metadata={"path": "rep_nogps_01", "path_len": 1, "route_type": "FLOOD"}
    )
    map_widget.visualise_message_path(msg)

    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]

    # Line bridging across Winston RPTR must be black #000000 and is_no_gps must be true
    assert '"color": "#000000"' in call_arg
    assert '"is_no_gps": true' in call_arg


def test_node_hover_last_heard_in_map_template():
    """Verifies formatLastHeard and Last heard display in node tooltips and popups."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE

    # 1. formatLastHeard JS function is embedded
    assert "function formatLastHeard(isoStr)" in LEAFLET_HTML_TEMPLATE
    assert "Just now" in LEAFLET_HTML_TEMPLATE
    assert "m ago" in LEAFLET_HTML_TEMPLATE
    assert "h ago" in LEAFLET_HTML_TEMPLATE
    assert "Yesterday" in LEAFLET_HTML_TEMPLATE

    # 2. Hover tooltip includes Last heard
    assert "Last heard: ' + lastHeardStr" in LEAFLET_HTML_TEMPLATE
    assert "Active now (Local node)" in LEAFLET_HTML_TEMPLATE

    # 3. Click popup includes Last heard stat
    assert '<div class="popup-stat">Last heard: \' + lastHeardStr + \'</div>' in LEAFLET_HTML_TEMPLATE


def test_path_modes_overlay_and_toggle(qapp, tmp_path):
    """Verifies 1-byte vs multibyte path mode overlay, storage persistence, and UI toggle."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE

    # 1. HTML template checks
    assert "path-mode-legend" in LEAFLET_HTML_TEMPLATE
    assert "1-Byte Path" in LEAFLET_HTML_TEMPLATE
    assert "Multibyte Path" in LEAFLET_HTML_TEMPLATE
    assert "function setPathModesVisible(visible)" in LEAFLET_HTML_TEMPLATE
    assert "function formatPathInfo(node)" in LEAFLET_HTML_TEMPLATE
    assert "#EF4444" in LEAFLET_HTML_TEMPLATE  # Red for 1-byte
    assert "#00FF7F" in LEAFLET_HTML_TEMPLATE  # Green for multibyte

    # 2. Storage checks for out_path_len and out_path_hash_mode
    storage = Storage(tmp_path / "path_modes_test.db")
    node_1b = NodeContact(
        node_id="node_1b_id",
        alias="OneByteNode",
        latitude=54.5,
        longitude=-3.1,
        out_path_len=2,
        out_path_hash_mode=0,
        out_path="1a2b"
    )
    node_mb = NodeContact(
        node_id="node_mb_id",
        alias="MultiByteNode",
        latitude=54.6,
        longitude=-3.2,
        out_path_len=3,
        out_path_hash_mode=1,
        out_path="1a2b3c4d5e6f"
    )
    storage.save_contact(node_1b)
    storage.save_contact(node_mb)

    saved_1b = storage.get_contact("node_1b_id")
    assert saved_1b is not None
    assert saved_1b.out_path_len == 2
    assert saved_1b.out_path_hash_mode == 0
    assert saved_1b.out_path == "1a2b"

    saved_mb = storage.get_contact("node_mb_id")
    assert saved_mb is not None
    assert saved_mb.out_path_len == 3
    assert saved_mb.out_path_hash_mode == 1

    coords_nodes = storage.get_nodes_with_coordinates()
    assert len(coords_nodes) == 2
    assert any(n.out_path_hash_mode == 0 for n in coords_nodes)
    assert any(n.out_path_hash_mode == 1 for n in coords_nodes)

    # 3. MeshMapWidget toggle button checks
    config = AppConfig()
    config.meshcore.map_show_path_modes = False

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)

    assert hasattr(map_widget, "btn_path_modes")
    assert map_widget.btn_path_modes.isCheckable()
    assert map_widget.btn_path_modes.isChecked() is False
    assert "Path Modes" in map_widget.btn_path_modes.text()

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Click to toggle ON
    map_widget.btn_path_modes.click()
    assert map_widget.btn_path_modes.isChecked() is True
    assert config.meshcore.map_show_path_modes is True
    assert map_widget.web_view.page().runJavaScript.called
    call_arg = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setPathModesVisible(true)" in call_arg

    # Click to toggle OFF
    map_widget.btn_path_modes.click()
    assert map_widget.btn_path_modes.isChecked() is False
    assert config.meshcore.map_show_path_modes is False
    call_arg2 = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setPathModesVisible(false)" in call_arg2


def test_tropo_forecast_map_overlay(qapp, tmp_path):
    """Verifies tropospheric ducting overlay pane layering, UI toggle, and WebBridge wiring."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.ui.mesh_map_widget import (
        LEAFLET_HTML_TEMPLATE,
        MeshMapWidget,
        get_leaflet_html,
    )
    from meshcore_tray.storage import Storage
    from meshcore_tray.config import AppConfig

    # 1. HTML template checks: custom panes and layering
    assert "map.createPane('tropoPane')" in LEAFLET_HTML_TEMPLATE
    assert "map.getPane('tropoPane').style.zIndex = 350" in LEAFLET_HTML_TEMPLATE
    assert "map.getPane('tropoPane').style.pointerEvents = 'none'" in LEAFLET_HTML_TEMPLATE

    assert "map.createPane('labelsPane')" in LEAFLET_HTML_TEMPLATE
    assert "map.getPane('labelsPane').style.zIndex = 380" in LEAFLET_HTML_TEMPLATE

    # 2. Legend and stepper checks
    assert "tropo-legend-panel" in LEAFLET_HTML_TEMPLATE
    assert "tropo-scale-bar" in LEAFLET_HTML_TEMPLATE
    assert "window.onTropoDataReady" in LEAFLET_HTML_TEMPLATE
    assert "clearTropoLayer" in LEAFLET_HTML_TEMPLATE

    # 3. Vector GeoJSON contours and inlined vendor scripts in get_leaflet_html()
    html = get_leaflet_html()
    assert "d3.contours" in LEAFLET_HTML_TEMPLATE
    assert "L.geoJSON" in LEAFLET_HTML_TEMPLATE
    assert "contours" in html

    # 4. Widget controls and interaction
    storage = Storage(tmp_path / "tropo_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)

    assert hasattr(map_widget, "btn_tropo")
    assert map_widget.btn_tropo.isCheckable()
    assert map_widget.btn_tropo.isChecked() is False
    assert "Tropo" in map_widget.btn_tropo.text()

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Patch tropo_service.fetch_forecast
    with patch.object(map_widget.tropo_service, "fetch_forecast") as mock_fetch:
        map_widget.btn_tropo.click()
        assert map_widget.btn_tropo.isChecked() is True
        mock_fetch.assert_called_with(offset_hours=0)
        assert "fetching" in map_widget.btn_tropo.text()

    # Simulate forecast ready
    fake_grid = {"w": 180, "h": 120, "lon_min": -20.0, "lat_max": 65.0, "res": 0.25, "b64_floats": "FAKEDATA"}
    map_widget._on_tropo_forecast_ready(fake_grid, "ww04-12.tif", "04 Sep 12:00 UTC")
    assert map_widget.btn_tropo.text() == "📡 Tropo"
    assert map_widget.web_view.page().runJavaScript.called
    js_call = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "window.onTropoDataReady" in js_call
    assert "ww04-12.tif" in js_call

    # Simulate stepping
    with patch.object(map_widget.tropo_service, "step_forecast") as mock_step:
        map_widget._on_bridge_tropo_stepped(3)
        mock_step.assert_called_with(3)

    # Click to toggle OFF
    map_widget.btn_tropo.click()
    assert map_widget.btn_tropo.isChecked() is False
    assert map_widget.btn_tropo.text() == "📡 Tropo"
    clear_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "clearTropoLayer()" in clear_js


def test_path_modes_overheard_resolution_and_styling(qapp, tmp_path):
    """Verifies that nodes without explicit routes resolve their 1-byte/multibyte mode from overheard RF paths."""
    import json
    from unittest.mock import MagicMock, patch
    from meshcore_tray.ui.mesh_map_widget import (
        LEAFLET_HTML_TEMPLATE,
        MeshMapWidget,
    )
    from meshcore_tray.storage import Storage
    from meshcore_tray.config import AppConfig
    from meshcore_tray.core.models import NodeContact

    storage = Storage(tmp_path / "overheard_test.db")
    config = AppConfig()

    # 1. Add 3 contacts (all default plen = -1, flood)
    c_1b = NodeContact(node_id="!1111", alias="Node1B", latitude=54.1, longitude=-3.1, out_path_len=-1)
    c_mb = NodeContact(node_id="!2222", alias="NodeMB", latitude=54.2, longitude=-3.2, out_path_len=-1)
    c_fl = NodeContact(node_id="!3333", alias="NodeFlood", latitude=54.3, longitude=-3.3, out_path_len=-1)
    storage.save_contact(c_1b)
    storage.save_contact(c_mb)
    storage.save_contact(c_fl)

    # 2. Mock get_overheard_path_modes on storage
    overheard_mock = {
        "1111": {"path_mode": 0, "path_len": 2, "source": "message"},
        "2222": {"path_mode": 2, "path_len": 3, "source": "packet_path"},
    }
    with patch.object(storage, "get_overheard_path_modes", return_value=overheard_mock):
        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            map_widget = MeshMapWidget(storage=storage, config=config)

        map_widget.web_view = MagicMock()
        map_widget._page_ready = True

        map_widget.refresh_map_data()

        assert map_widget.web_view.page().runJavaScript.called
        calls = [args[0][0] for args in map_widget.web_view.page().runJavaScript.call_args_list if "setNodes" in args[0][0]]
        assert len(calls) >= 1
        set_nodes_call = calls[-1]

        # Extract JSON array from setNodes(...)
        json_str = set_nodes_call[len("setNodes("):-2]
        nodes_list = json.loads(json_str)

        node_map = {n["node_id"]: n for n in nodes_list}
        assert "!1111" in node_map
        assert "!2222" in node_map
        assert "!3333" in node_map

        # Verify 1-byte overheard node
        assert node_map["!1111"]["out_path_hash_mode"] == 0
        assert node_map["!1111"]["out_path_len"] == 2
        assert node_map["!1111"]["out_path_src"] == "message"

        # Verify multibyte overheard node
        assert node_map["!2222"]["out_path_hash_mode"] == 2
        assert node_map["!2222"]["out_path_len"] == 3
        assert node_map["!2222"]["out_path_src"] == "packet_path"

        # Verify direct / flood node
        assert node_map["!3333"]["out_path_hash_mode"] == -1
        assert node_map["!3333"]["out_path_len"] == -1
        assert node_map["!3333"]["out_path_src"] == "configured"


def test_overheard_repeater_multibyte_deduction_and_upgrade(tmp_path):
    """Verifies that repeaters in multi-byte packet paths (like M7NCY West Yagi) are automatically detected as 3-byte or 2-byte and properly upgraded."""
    storage = Storage(tmp_path / "rep_test.db")

    # 1. Contact for M7NCY West Yagi (initial direct route or flood)
    yagi_contact = NodeContact(
        node_id="DD2DF7A117C6",
        alias="M7NCY West Yagi",
        public_key="dd2df7a117c69abe5b73080fe444872a2cc4a59d84ee83de573c87f9225f8055",
        is_repeater=True,
        latitude=54.6588,
        longitude=-3.4344,
        out_path_len=-1,
        out_path_hash_mode=-1
    )
    blake_contact = NodeContact(
        node_id="fddde8360086",
        alias="🌐 MCC Blake",
        public_key="fddde83600860000000000000000000000000000000000000000000000000000",
        is_repeater=True,
        latitude=54.55,
        longitude=-3.35,
        out_path_len=-1,
        out_path_hash_mode=-1
    )
    storage.save_contact(yagi_contact)
    storage.save_contact(blake_contact)

    # 2. Message 1: Initially heard with a 1-byte path (chunk size 2)
    msg_1b = MessageEnvelope(
        id="m_1b",
        timestamp="2026-09-04T12:00:00Z",
        source_driver="meshcore_serial",
        sender_id="!alan",
        sender_name="Alan-H",
        channel="Public",
        text="Hello 1-byte",
        metadata={
            "path": "42dd",  # 2 hops * 2 hex chars = 4 chars (1 byte / hop)
            "path_len": 2,
            "hop_nodes": ["@Methera", "@M7NCY West Yagi"],
            "repeaters": ["@Methera", "@M7NCY West Yagi"]
        }
    )
    storage.save_message(msg_1b)

    modes_1b = storage.get_overheard_path_modes()
    assert "m7ncy west yagi" in modes_1b
    assert modes_1b["m7ncy west yagi"]["path_mode"] == 0  # 1-byte

    # 3. Message 2: Overheard with a 3-byte path (chunk size 6: 42 chars / 7 hops = 6)
    msg_3b = MessageEnvelope(
        id="m_3b",
        timestamp="2026-09-04T12:05:00Z",
        source_driver="meshcore_serial",
        sender_id="!paul",
        sender_name="Paulmcl",
        channel="#cumbria",
        text="Hello 3-byte",
        metadata={
            "path": "93c28cd41ee2e426c039c4baeda7d5fddde8dd2df7",
            "path_len": 7,
            "hop_nodes": ["<Unknown Repeater 93c28c>", "@NUMC-MC", "@Alba-Éireann-01", "<Unknown Repeater 39c4ba>", "<Unknown Repeater eda7d5>", "@🌐 MCC Blake", "@M7NCY West Yagi"],
            "repeaters": ["<Unknown Repeater 93c28c>", "@NUMC-MC", "@Alba-Éireann-01", "<Unknown Repeater 39c4ba>", "<Unknown Repeater eda7d5>", "@🌐 MCC Blake", "@M7NCY West Yagi"]
        }
    )
    storage.save_message(msg_3b)

    modes_upgraded = storage.get_overheard_path_modes()

    # M7NCY West Yagi should be automatically upgraded to Mode 2 (3-byte)
    assert "m7ncy west yagi" in modes_upgraded
    assert modes_upgraded["m7ncy west yagi"]["path_mode"] == 2
    assert modes_upgraded["m7ncy west yagi"]["path_len"] == 7

    # Blake should also be detected as Mode 2
    assert "🌐 mcc blake" in modes_upgraded
    assert modes_upgraded["🌐 mcc blake"]["path_mode"] == 2

    # Sub-hashes dd2df7 and fddde8 should also be recorded with Mode 2
    assert "dd2df7" in modes_upgraded
    assert modes_upgraded["dd2df7"]["path_mode"] == 2
    assert "fddde8" in modes_upgraded
    assert modes_upgraded["fddde8"]["path_mode"] == 2

    # Unknown repeaters should be recorded
    assert "93c28c" in modes_upgraded
    assert modes_upgraded["93c28c"]["path_mode"] == 2


def test_map_coordinate_wrapping_and_corrupt_node_filtering(tmp_path, qapp):
    """Verifies that out-of-bounds coordinates are rejected by storage and map coordinates wrap properly."""
    db_path = tmp_path / "bounds_test.db"
    storage = Storage(db_path)

    # 1. Valid node
    valid_node = NodeContact(
        node_id="good01",
        alias="Good Repeater",
        is_repeater=True,
        latitude=54.5,
        longitude=-3.0
    )
    # 2. Corrupted node with latitude < -90 or longitude > 180
    bad_node = NodeContact(
        node_id="bad01",
        alias="Corrupted RF Packet Node",
        is_repeater=True,
        latitude=-330.347952,
        longitude=1177.20762
    )

    storage.save_contact(valid_node)
    storage.save_contact(bad_node)

    # Good node must be present
    nodes = storage.get_nodes_with_coordinates()
    node_ids = [n.node_id for n in nodes]
    assert "good01" in node_ids
    # Bad node must be rejected and NOT returned in nodes with coordinates
    assert "bad01" not in node_ids

    # 3. Test map view coordinate wrapping
    from unittest.mock import patch
    config = AppConfig()
    config.meshcore.map_center_lat = 51.0
    config.meshcore.map_center_lon = 0.0

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(storage=storage, config=config)
    # Simulate Leaflet moveend event passing unwrapped longitude 714.77051 (e.g. panned 2 worlds east)
    widget._on_bridge_map_moved(51.15179, 714.77051, 6)

    # Must be wrapped into [-180, 180]
    assert -180.0 <= config.meshcore.map_center_lon <= 180.0
    # 714.77051 - 720 = -5.22949
    assert abs(config.meshcore.map_center_lon - (-5.22949)) < 0.001
    assert config.meshcore.map_center_lat == 51.15179


def test_companion_orbitals_ui_and_widget(tmp_path, qapp):
    """Tests Companion Orbitals toggle button, count badge, and Leaflet JS emission."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.core.models import DockedCompanionInfo

    db_path = tmp_path / "orbitals_map_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.map_show_companion_orbitals = False

    # Dock a companion node to a repeater
    dock_info = DockedCompanionInfo(
        node_id="comp_bob",
        alias="Bob",
        repeater_id="rep_egremont",
        repeater_alias="@Egremont-1",
        channel="#test",
        snr=12.0,
        last_heard="2026-09-05T00:00:00Z"
    )
    storage.save_docked_companion(dock_info)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(storage=storage, config=config)
    widget.web_view = MagicMock()
    widget._page_ready = True

    # 1. Button exists and displays count
    assert hasattr(widget, "btn_orbitals")
    assert "Orbitals (1)" in widget.btn_orbitals.text()
    assert not widget.btn_orbitals.isChecked()

    # 2. Toggle button ON
    widget.btn_orbitals.setChecked(True)
    widget._on_orbitals_toggle()
    assert config.meshcore.map_show_companion_orbitals is True

    # Verify setCompanionOrbitalsVisible was executed via runJavaScript
    assert widget.web_view.page().runJavaScript.called
    js_calls = [call[0][0] for call in widget.web_view.page().runJavaScript.call_args_list]
    orbital_calls = [c for c in js_calls if "setCompanionOrbitalsVisible" in c]
    assert len(orbital_calls) > 0
    latest_call = orbital_calls[-1]
    assert "true" in latest_call
    assert "comp_bob" in latest_call or "Bob" in latest_call

    # 3. Toggle button OFF
    widget.btn_orbitals.setChecked(False)
    widget._on_orbitals_toggle()
    assert config.meshcore.map_show_companion_orbitals is False
    js_calls2 = [call[0][0] for call in widget.web_view.page().runJavaScript.call_args_list]
    latest_call2 = [c for c in js_calls2 if "setCompanionOrbitalsVisible" in c][-1]
    assert "false" in latest_call2


def test_companion_orbitals_html_template_and_color_config(tmp_path, qapp):
    """Verifies that the Leaflet HTML template contains updated threshold, transparency, and dynamic color."""
    from unittest.mock import MagicMock, patch
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE

    # Verify zoom threshold is 14 (zoomed in closer)
    assert "var ORBITAL_ZOOM_THRESHOLD = 14;" in LEAFLET_HTML_TEMPLATE
    # Verify hollow circle center is transparent (not black)
    assert "background: transparent !important;" in LEAFLET_HTML_TEMPLATE
    assert "rgba(18, 21, 26, 0.75)" not in LEAFLET_HTML_TEMPLATE
    # Verify satellite dots are enlarged to r=4.25 (25% larger than 3.4)
    assert 'r="4.25"' in LEAFLET_HTML_TEMPLATE
    # Verify buildNodePopupContent is defined to integrate docked nodes into repeater popup
    assert "function buildNodePopupContent(node)" in LEAFLET_HTML_TEMPLATE
    assert "🛰️ Docked Companions" in LEAFLET_HTML_TEMPLATE

    # Verify default gold color is #FFD335
    assert "--orbital-repeater-color: #FFD335;" in LEAFLET_HTML_TEMPLATE
    # Verify favorited repeater purple ring styling
    assert ".node-dot.orbital-ring-repeater.orbital-ring-repeater-fav" in LEAFLET_HTML_TEMPLATE
    assert "var(--favorite-color, #AA55FF)" in LEAFLET_HTML_TEMPLATE
    # Verify randomized starting angle and trailing arc path
    assert "startAngleOffset" in LEAFLET_HTML_TEMPLATE
    assert "trail_grad_" in LEAFLET_HTML_TEMPLATE
    assert "stroke-linecap=\"round\"" in LEAFLET_HTML_TEMPLATE

    # Verify AppConfig defaults and migration
    default_cfg = AppConfig()
    assert default_cfg.app_colors.map_orbital_repeater_color == "#FFD335"
    migrated_cfg = AppConfig.from_dict({"app_colors": {"map_orbital_repeater_color": "#FFD700"}})
    assert migrated_cfg.app_colors.map_orbital_repeater_color == "#FFD335"

    # Verify custom orbitalRepeater color is passed into JS setMapColors
    config = AppConfig()
    config.app_colors.map_orbital_repeater_color = "#39FF14"
    mock_storage = MagicMock()
    mock_storage.get_nodes_with_coordinates.return_value = []
    mock_storage.get_docked_companion_count.return_value = 0
    mock_storage.get_docked_companions.return_value = {}
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(storage=mock_storage, config=config)
    widget.web_view = MagicMock()
    widget._page_ready = True

    widget.apply_colors(config.app_colors)
    assert widget.web_view.page().runJavaScript.called
    js_calls = [call[0][0] for call in widget.web_view.page().runJavaScript.call_args_list]
    color_calls = [c for c in js_calls if "setMapColors" in c]
    assert len(color_calls) > 0
    assert '"orbitalRepeater": "#39FF14"' in color_calls[-1]


def test_multibyte_path_resolution_observer_and_messages(qapp, tmp_path):
    """Verifies that 2-byte and 3-byte path identifiers are correctly chunked and resolved for both Observer and Messages."""
    from datetime import datetime
    db_path = tmp_path / "multibyte_test.db"
    storage = Storage(db_path)
    config = AppConfig()
    config.meshcore.latitude = 54.65897
    config.meshcore.longitude = -3.4346
    config.meshcore.node_alias = "M7NCY Home"

    # Save test repeaters with known multi-byte prefixes
    rep1 = NodeContact(
        node_id="cb11aa22",
        alias="Repeater Alpha 2-Byte",
        latitude=54.6000,
        longitude=-3.4000,
        is_repeater=True
    )
    rep2 = NodeContact(
        node_id="3344bb55",
        alias="Repeater Bravo 2-Byte",
        latitude=54.5500,
        longitude=-3.3500,
        is_repeater=True
    )
    rep3 = NodeContact(
        node_id="cb1122ff",
        alias="Repeater Charlie 3-Byte",
        latitude=54.5000,
        longitude=-3.3000,
        is_repeater=True
    )
    sender = NodeContact(
        node_id="deadbeef01",
        alias="Mobile Sender",
        latitude=54.4000,
        longitude=-3.2000,
        is_repeater=False
    )
    storage.save_contact(rep1)
    storage.save_contact(rep2)
    storage.save_contact(rep3)
    storage.save_contact(sender)

    # 1. Test 2-byte chunking (4 hex characters per hop) for Driver Observer
    from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
    driver = MeshCoreDriver(config=config, storage=storage)
    
    rx_event = {
        "path": "cb113344",  # 2 hops of 2 bytes (4 chars each)
        "path_len": 2,
        "route_typename": "FLOOD",
        "snr": 8.5
    }
    driver._handle_rx_log_data(rx_event)
    assert len(driver._recent_rx_logs) > 0
    recent = driver._recent_rx_logs[-1]
    assert recent["hop_nodes"] == ["@Repeater Alpha 2-Byte", "@Repeater Bravo 2-Byte"]
    assert len(recent["hop_coords"]) == 2

    # 2. Test 3-byte chunking (6 hex characters per hop) for Driver Observer
    rx_event_3b = {
        "path": "cb1122",  # 1 hop of 3 bytes (6 chars)
        "path_len": 1,
        "route_typename": "FLOOD",
        "snr": 11.0
    }
    driver._handle_rx_log_data(rx_event_3b)
    recent_3b = driver._recent_rx_logs[-1]
    assert recent_3b["hop_nodes"] == ["@Repeater Charlie 3-Byte"]
    assert len(recent_3b["hop_coords"]) == 1

    # 3. Test Map Widget Observer path drawing appends local coordinate
    from unittest.mock import patch, MagicMock
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True
    map_widget.show_paths = True

    # Tracing 1 hop from Repeater Charlie to Home
    path_info = PacketPathInfo(
        packet_id="pkt-obs-1",
        sender_id="mesh",
        sender_name="RF Packet (FLOOD)",
        hop_nodes=["@Repeater Charlie 3-Byte"],
        hop_snrs=[11.0],
        route_type="FLOOD",
        coordinates=[[54.5000, -3.3000]]
    )
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        map_widget._on_packet_path_traced(path_info)
    assert map_widget.web_view.page().runJavaScript.called
    last_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "drawPacketPath" in last_js
    # Verify that the trajectory has 2 points: Repeater Charlie -> Local Node
    assert "[54.5, -3.3]" in last_js
    assert "[54.65897, -3.4346]" in last_js

    # 4. Test Map Widget Message Route drawing with 2-byte path
    msg_env = MessageEnvelope(
        id="msg-mb-1",
        timestamp=datetime.now().isoformat(),
        source_driver="meshcore_serial",
        sender_id="deadbeef01",
        sender_name="Mobile Sender",
        channel="Public",
        text="Hello over 2-byte hops!",
        metadata={
            "path": "cb113344",
            "path_len": 2,
            "route_type": "FLOOD"
        }
    )
    map_widget.web_view.page().runJavaScript.reset_mock()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        map_widget._on_message_received(msg_env)
    assert map_widget.web_view.page().runJavaScript.called
    msg_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "drawPacketPath" in msg_js
    assert '"color": "green"' in msg_js
    # Route must connect Sender -> Rep1 -> Rep2 -> Home
    assert "[54.4, -3.2]" in msg_js
    assert "[54.55, -3.35]" in msg_js
    assert "[54.65897, -3.4346]" in msg_js


def test_scopes_regional_overlay_and_controls(qapp, tmp_path):
    """Verifies that regional scopes storage methods, classification, and UI controls work correctly."""
    from unittest.mock import patch, MagicMock
    import json

    db_path = tmp_path / "scopes_test.db"
    storage = Storage(db_path)
    config = AppConfig()

    # 1. Verify get_scope_definitions returns the 7 requested scopes
    scope_defs = storage.get_scope_definitions()
    expected_scopes = ["gb-cum", "gb-nwk", "cax", "gb-nth", "sco", "iom", "ioi"]
    for s in expected_scopes:
        assert s in scope_defs, f"Scope {s} must be in scope definitions"
        assert "name" in scope_defs[s]
        assert "color" in scope_defs[s]

    # 2. Add sample repeaters to test classification
    cum_rep = NodeContact(
        node_id="cum01",
        alias="Cumbria Peak Repeater",
        is_repeater=True,
        latitude=54.55,
        longitude=-3.25
    )
    sco_rep = NodeContact(
        node_id="sco01",
        alias="SCO High Repeater",
        is_repeater=True,
        latitude=56.0,
        longitude=-4.0
    )
    ioi_rep = NodeContact(
        node_id="ioi01",
        alias="DUB Dublin Relay",
        is_repeater=True,
        latitude=53.34,
        longitude=-6.26
    )
    storage.save_contact(cum_rep)
    storage.save_contact(sco_rep)
    storage.save_contact(ioi_rep)

    # Test explicit assignment via save_contact_scope
    storage.save_contact_scope("cum01", scope_name="gb-cum", allowed_regions=["gb-cum", "cax"])
    updated_cum = storage.get_contact("cum01")
    assert updated_cum.scope_name == "gb-cum"
    assert updated_cum.allowed_regions == ["gb-cum", "cax"]

    # Test get_repeaters_by_scope
    by_scope = storage.get_repeaters_by_scope()
    assert "gb-cum" in by_scope
    assert "sco" in by_scope
    assert "ioi" in by_scope
    assert any(n["node_id"] == "cum01" for n in by_scope["gb-cum"]["nodes"])
    assert any(n["node_id"] == "sco01" for n in by_scope["sco"]["nodes"])
    assert any(n["node_id"] == "ioi01" for n in by_scope["ioi"]["nodes"])

    # 3. Test MeshMapWidget scopes button and JavaScript invocation
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=config)
    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    assert hasattr(map_widget, "btn_scopes")
    assert map_widget.btn_scopes.text() == "🌐 Scopes"

    # Toggle Scopes ON
    map_widget.btn_scopes.setChecked(True)
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        map_widget._on_scopes_toggle()

    assert map_widget.web_view.page().runJavaScript.called
    last_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setScopeOverlaysVisible(true," in last_js
    assert "gb-cum" in last_js
    assert "Cumbria Peak Repeater" in last_js

    # Toggle Scopes OFF
    map_widget.btn_scopes.setChecked(False)
    map_widget.web_view.page().runJavaScript.reset_mock()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
        map_widget._on_scopes_toggle()

    assert map_widget.web_view.page().runJavaScript.called
    off_js = map_widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setScopeOverlaysVisible(false, {});" in off_js

    # Verify settings update propagation
    config.meshcore.map_show_scopes = True
    map_widget._on_settings_updated(config)
    assert map_widget.btn_scopes.isChecked() is True

    # 4. Test Operator Scope Exclusion and Reassignment via Bridge
    # Exclude Cumbria Peak Repeater from scopes
    map_widget._on_node_scope_changed("cum01", "none")
    updated_scopes = storage.get_repeaters_by_scope()
    assert not any(n["node_id"] == "cum01" for n in updated_scopes["gb-cum"]["nodes"])

    # Re-assign to gb-nwk
    map_widget._on_node_scope_changed("cum01", "gb-nwk")
    updated_scopes2 = storage.get_repeaters_by_scope()
    assert any(n["node_id"] == "cum01" for n in updated_scopes2["gb-nwk"]["nodes"])


def test_blackdown_hills_omni_not_misclassified_as_ioi(tmp_path):
    """Verifies that English South Coast repeaters (like Blackdown Hills Omni) are not matched to Ireland."""
    db_path = tmp_path / "blackdown_test.db"
    storage = Storage(db_path)

    blackdown = NodeContact(
        node_id="6a9597921f38",
        alias="Blackdown Hills Omni",
        is_repeater=True,
        latitude=50.891361,
        longitude=-3.139039
    )
    storage.save_contact(blackdown)

    scopes = storage.get_repeaters_by_scope()
    # Must NOT be in Island of Ireland (#ioi)
    assert not any(n["node_id"] == "6a9597921f38" for n in scopes["ioi"]["nodes"])

    # Test explicit user exclusion
    storage.save_contact_scope("6a9597921f38", "none")
    assert storage.get_contact("6a9597921f38").scope_name == "none"
    scopes_after = storage.get_repeaters_by_scope()
    for s_key in scopes_after:
        assert not any(n["node_id"] == "6a9597921f38" for n in scopes_after[s_key]["nodes"])


def test_bodffordd_coordinates_protected_from_corrupt_flash_dump(tmp_path):
    """Verifies that known UK nodes (like Bodffordd Obs) are not moved to China/Siberia by corrupt flash dumps."""
    db_path = tmp_path / "bodffordd_test.db"
    storage = Storage(db_path)

    # 1. Genuine Bodffordd Obs in Anglesey, Wales
    genuine_node = NodeContact(
        node_id="6ec4553b4a55",
        alias="Bodffordd Obs",
        is_repeater=True,
        latitude=53.26558,
        longitude=-4.35804
    )
    storage.save_contact(genuine_node)

    # 2. Corrupt flash dump attempting to overwrite with coordinates in Siberia/China
    corrupt_dump = [
        NodeContact(
            node_id="6ec4553b4a55",
            alias="\x01\n>\x01\n>/4%,",  # Corrupted alias from OTA packet
            is_repeater=True,
            latitude=53.26558,
            longitude=109.888006
        )
    ]
    storage.save_contacts_bulk(corrupt_dump)

    # Coordinates must remain in Anglesey, Wales (-4.35804), NOT China (109.888)
    saved = storage.get_contact("6ec4553b4a55")
    assert saved.alias == "Bodffordd Obs"
    assert saved.longitude == -4.35804
    assert saved.latitude == 53.26558


def test_logging_system_and_map_watchdog(tmp_path, monkeypatch):
    """Verifies centralized logging, JS console capturing, screen change handling, and watchdog."""
    from meshcore_tray.logger import setup_app_logging, get_log_file_path
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE, LoggingWebEnginePage, MeshMapWidget
    import logging

    # 1. Verify log file creation
    monkeypatch.setattr("meshcore_tray.logger.get_app_dir", lambda: tmp_path)
    log_file = setup_app_logging(debug=True)
    assert log_file.exists()
    assert "meshcore_navigator.log" in str(log_file)

    test_logger = logging.getLogger("test_module")
    test_logger.info("Test message for file logging verification")
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Test message for file logging verification" in content

    # 2. Verify Leaflet template contains ResizeObserver and window error listeners
    assert "ResizeObserver" in LEAFLET_HTML_TEMPLATE
    assert "Leaflet Window Error" in LEAFLET_HTML_TEMPLATE
    assert "Leaflet Unhandled Rejection" in LEAFLET_HTML_TEMPLATE
    assert "Leaflet Base Tile Error" in LEAFLET_HTML_TEMPLATE

    # 3. Verify LoggingWebEnginePage captures JS console messages
    captured_logs = []
    class LogCatcher(logging.Handler):
        def emit(self, record):
            captured_logs.append(self.format(record))

    catcher = LogCatcher()
    logging.getLogger("meshcore_tray.mesh_map").addHandler(catcher)

    from PyQt6.QtWebEngineCore import QWebEnginePage
    LoggingWebEnginePage.javaScriptConsoleMessage(
        None,
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
        "Simulated WebGL Context Lost Error",
        42,
        "leaflet.js"
    )
    assert any("Simulated WebGL Context Lost Error" in msg for msg in captured_logs)
    assert any("[JS-ERROR]" in msg for msg in captured_logs)

    # 4. Verify watchdog and screen changed methods on MeshMapWidget
    cfg = AppConfig()
    widget = MeshMapWidget(config=cfg)
    assert hasattr(widget, "_start_renderer_watchdog")
    assert hasattr(widget, "_check_renderer_watchdog")
    assert hasattr(widget, "_on_window_screen_changed")
    assert hasattr(widget, "moveEvent")

    # Simulate pong
    widget._watchdog_unanswered = 2
    widget._on_watchdog_pong(2)
    assert widget._watchdog_unanswered == 0

    # Verify bridge activity reset and grace period
    import time
    widget._watchdog_unanswered = 4
    widget._reset_watchdog_activity()
    assert widget._watchdog_unanswered == 0

    widget._page_ready = True
    widget._watchdog_grace_until = time.time() + 100.0
    widget._watchdog_unanswered = 5
    widget._check_renderer_watchdog()
    assert widget._watchdog_unanswered == 0  # cleared during grace period

    # 5. Verify watchdog inflight probe bounding and token discrimination
    monkeypatch.setattr(widget, "isVisible", lambda: True)
    if widget.window():
        monkeypatch.setattr(widget.window(), "isVisible", lambda: True)
        monkeypatch.setattr(widget.window(), "isMinimized", lambda: False)

    widget._initial_loading_active = False
    widget._geometry_in_motion = False
    widget._watchdog_grace_until = 0.0
    widget._watchdog_probe_inflight = True
    widget._watchdog_probe_token = 42
    widget._watchdog_unanswered = 1
    widget._check_renderer_watchdog()
    assert widget._watchdog_unanswered == 2  # increments without issuing duplicate probe

    # Stale pong with wrong token is ignored
    widget._on_watchdog_pong(2, token=99)
    assert widget._watchdog_probe_inflight is True
    assert widget._watchdog_unanswered == 2

    # Matching pong clears unanswered count and inflight flag
    widget._on_watchdog_pong(2, token=42)
    assert widget._watchdog_probe_inflight is False
    assert widget._watchdog_unanswered == 0

    # 6. Verify recovery deadline monitoring when _page_ready is False
    widget._page_ready = False
    widget._recovery_in_progress = True
    widget._recovery_start_time = time.time() - 20.0  # past 15s deadline
    widget._recovery_attempts = 1
    recovery_forced = []
    widget._force_fresh_page_recovery = lambda: recovery_forced.append(True)
    widget._check_renderer_watchdog()
    assert len(recovery_forced) == 1
    assert widget._recovery_attempts == 2



