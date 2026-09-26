"""Unit tests for MqttService, packet ingestion, deduplication, and EventBus bridging."""

from datetime import datetime, timezone
import json
import pytest
import time
from unittest.mock import MagicMock, patch

from meshcore_tray.config import AppConfig, MqttConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import PacketPathInfo, MessageEnvelope
from meshcore_tray.core.deduplicator import get_deduplicator
from meshcore_tray.core.mqtt_service import MqttService, parse_mqtt_broker_url
from meshcore_tray.storage import Storage


@pytest.fixture(autouse=True)
def reset_dedup():
    get_deduplicator().clear()
    yield
    get_deduplicator().clear()


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_mqtt.db"
    return Storage(str(db_file))


def test_mqtt_service_deduplication():
    """Verifies that MqttService drops duplicate packets within the deduplication window."""
    config = AppConfig()
    config.mqtt.enabled = True
    config.mqtt.dedup_window_secs = 2.0

    service = MqttService(config=config)

    packet_hex = "1501A1008899DEADBEEF"

    # First time -> not duplicate
    assert service._is_duplicate(packet_hex) is False

    # Second time immediately -> duplicate
    assert service._is_duplicate(packet_hex) is True

    # Different packet -> not duplicate
    assert service._is_duplicate("1501B2008899DEADBEEF") is False


def test_mqtt_service_process_json_payload(temp_storage):
    """Verifies that MqttService decodes a JSON-wrapped packet from CoreScope/meshcoretomqtt."""
    config = AppConfig()
    config.mqtt.enabled = True

    service = MqttService(config=config, storage=temp_storage)

    # Wire event listener
    received_paths = []
    bus.subscribe(EventType.PACKET_PATH_TRACED, lambda p: received_paths.append(p))

    raw_hex = "1501A1008899DEADBEEF"
    json_payload = json.dumps({
        "raw": raw_hex,
        "sender": "d79870ac",
        "snr": 11.2,
        "rssi": -80
    }).encode("utf-8")

    class FakeMsg:
        topic = "meshcore/packets"
        payload = json_payload

    service._on_message(None, None, FakeMsg())

    assert len(received_paths) == 1
    p = received_paths[0]
    assert p.sender_id == "d79870ac"
    assert p.route_type == "FLOOD"
    assert p.payload_type == "GRP_TXT"
    assert p.raw_hex == raw_hex
    assert p.hop_snrs == [11.2]


def test_mqtt_service_process_binary_payload(temp_storage):
    """Verifies that MqttService processes raw binary bytes payload from an MQTT broker."""
    config = AppConfig()
    config.mqtt.enabled = True

    service = MqttService(config=config, storage=temp_storage)

    received_paths = []
    bus.subscribe(EventType.PACKET_PATH_TRACED, lambda p: received_paths.append(p))

    # Binary raw bytes for flood packet
    bin_payload = bytes.fromhex("1501A1008899DEADBEEF")

    class FakeMsg:
        topic = "meshcore/uk/packets"
        payload = bin_payload

    service._on_message(None, None, FakeMsg())

    assert len(received_paths) >= 1
    p = received_paths[-1]
    assert p.raw_hex == "1501A1008899DEADBEEF"


def test_mqtt_service_gateway_forwarding():
    """Verifies that MqttService publishes local packets to MQTT if publish_enabled is active."""
    config = AppConfig()
    config.mqtt.broker_host = "localhost"
    config.mqtt.enabled = True
    config.mqtt.publish_enabled = True

    service = MqttService(config=config)
    service._connected = True
    service.client = MagicMock()

    mock_res = MagicMock()
    mock_res.rc = 0
    service.client.publish.return_value = mock_res

    local_path = PacketPathInfo(
        packet_id="local-12345",
        sender_id="local_node",
        sender_name="Heltec-V3",
        route_type="FLOOD",
        raw_hex="1501A1008899DEADBEEF"
    )

    service._on_local_packet_traced(local_path)

    assert service.client.publish.called
    args, kwargs = service.client.publish.call_args
    assert args[0] == "meshcore/packets"
    payload_dict = json.loads(args[1])
    assert payload_dict["raw"] == "1501A1008899DEADBEEF"


def test_mqtt_service_drops_packet_when_already_received_on_radio(temp_storage):
    """Verifies that MqttService drops duplicate packets if they match packets heard over physical radio."""
    config = AppConfig()
    config.mqtt.enabled = True

    service = MqttService(config=config, storage=temp_storage)

    received_paths = []
    bus.subscribe(EventType.PACKET_PATH_TRACED, lambda p: received_paths.append(p))

    raw_hex = "1501A1008899DEADBEEF"

    # Simulate physical LoRa radio receiving this packet first
    get_deduplicator().register_radio_packet(
        raw_hex=raw_hex,
        sender_id="radio_node",
        channel="public",
        text="Hello over physical airwaves"
    )

    # Now MQTT broker sends the same packet
    class FakeMsg:
        topic = "meshcore/packets"
        payload = json.dumps({"raw": raw_hex, "sender": "radio_node"}).encode("utf-8")

    service._on_message(None, None, FakeMsg())

    # Should be dropped by deduplicator!
    assert len(received_paths) == 0


def test_radio_message_supersedes_earlier_mqtt_message(temp_storage):
    """Verifies that physical radio messages supersede earlier MQTT network messages for the same text."""
    # 1. MQTT message arrives first over network
    mqtt_msg = MessageEnvelope(
        id="mqtt-12345",
        channel="Public",
        sender_id="node_a",
        sender_name="Alice",
        text="Hello Cumbria!",
        source_driver="mqtt"
    )
    temp_storage.save_message(mqtt_msg)

    msgs = temp_storage.get_messages("Public")
    assert len(msgs) == 1
    assert msgs[0].source_driver == "mqtt"
    assert msgs[0].id == "mqtt-12345"

    # 2. Local physical radio hears the message over RF LoRa slightly later
    radio_msg = MessageEnvelope(
        id="chan-public-99999",
        channel="Public",
        sender_id="node_a",
        sender_name="Alice",
        text="Hello Cumbria!",
        source_driver="meshcore_serial",
        metadata={"snr": 9.5, "rssi": -72}
    )
    temp_storage.save_message(radio_msg)

    # 3. Radio message must supersede the MQTT message without duplication
    msgs_after = temp_storage.get_messages("Public")
    assert len(msgs_after) == 1
    assert msgs_after[0].id == "chan-public-99999"
    assert msgs_after[0].source_driver == "meshcore_serial"
    assert msgs_after[0].metadata.get("snr") == 9.5

    # 4. If another MQTT duplicate arrives later, it must be dropped
    mqtt_dup = MessageEnvelope(
        id="mqtt-67890",
        channel="Public",
        sender_id="node_a",
        sender_name="Alice",
        text="Hello Cumbria!",
        source_driver="mqtt"
    )
    temp_storage.save_message(mqtt_dup)
    msgs_final = temp_storage.get_messages("Public")
    assert len(msgs_final) == 1
    assert msgs_final[0].source_driver == "meshcore_serial"


def test_radio_packet_path_supersedes_earlier_mqtt_path(temp_storage):
    """Verifies that physical radio packet paths supersede earlier MQTT network paths."""
    # 1. MQTT packet path arrives first
    mqtt_path = PacketPathInfo(
        packet_id="mqtt-987654",
        sender_id="node_b",
        sender_name="Bob",
        raw_hex="1501A1008899DEADBEEF",
        decoded_info={"text": "Test packet", "channel": "Public"},
        source="mqtt"
    )
    temp_storage.save_packet_path(mqtt_path)
    paths = temp_storage.get_recent_packet_paths()
    assert len(paths) == 1
    assert paths[0].source == "mqtt"

    # 2. Radio packet path arrives over physical LoRa
    radio_path = PacketPathInfo(
        packet_id="path-radio-111",
        sender_id="node_b",
        sender_name="Bob",
        raw_hex="1501A1008899DEADBEEF",
        hop_nodes=["Repeater1", "Repeater2"],
        hop_snrs=[7.2, 10.1],
        decoded_info={"text": "Test packet", "channel": "Public"},
        source="radio"
    )
    temp_storage.save_packet_path(radio_path)

    paths_after = temp_storage.get_recent_packet_paths()
    assert len(paths_after) == 1
    assert paths_after[0].source == "radio"
    assert paths_after[0].packet_id == "path-radio-111"
    assert paths_after[0].hop_nodes == ["Repeater1", "Repeater2"]


def test_parse_mqtt_broker_url():
    """Verifies parsing of URLs with schemes, ports, and transports."""
    # UKMesh WebSockets over TLS
    host, port, tls, trans, path = parse_mqtt_broker_url("wss://mqtt.ukmesh.com:443")
    assert host == "mqtt.ukmesh.com"
    assert port == 443
    assert tls is True
    assert trans == "websockets"
    assert path == "/mqtt"

    # UKMesh with custom path
    host, port, tls, trans, path = parse_mqtt_broker_url("wss://mqtt.ukmesh.com:443/ws-feed")
    assert host == "mqtt.ukmesh.com"
    assert port == 443
    assert tls is True
    assert trans == "websockets"
    assert path == "/ws-feed"

    # Standard MQTT TCP
    host, port, tls, trans, path = parse_mqtt_broker_url("mqtt://broker.emqx.io:1883")
    assert host == "broker.emqx.io"
    assert port == 1883
    assert tls is False
    assert trans == "tcp"

    # SSL / TLS TCP
    host, port, tls, trans, path = parse_mqtt_broker_url("ssl://mqtt.lincomatic.com:8883")
    assert host == "mqtt.lincomatic.com"
    assert port == 8883
    assert tls is True
    assert trans == "tcp"

    # Plain host and port
    host, port, tls, trans, path = parse_mqtt_broker_url("localhost:1883")
    assert host == "localhost"
    assert port == 1883
    assert tls is False
    assert trans == "tcp"


def test_ukmesh_publishing_denied():
    """Verifies that publishing to ukmesh.com is strictly denied (read-only enforcement)."""
    config = AppConfig()
    config.mqtt.enabled = True
    config.mqtt.broker_host = "mqtt.ukmesh.com"
    config.mqtt.publish_enabled = True  # User attempted to turn on publishing

    service = MqttService(config=config)
    service._connected = True
    service.client = MagicMock()

    # Attempt to publish a packet
    res = service.publish_packet("1501A1008899DEADBEEF")
    assert res is False
    # Broker client publish should NOT be called
    assert service.client.publish.call_count == 0


def test_ukmesh_json_payload_with_uppercase_snr_rssi_and_origin(temp_storage):
    """Verifies that UKMesh packet format (SNR, RSSI, origin) is correctly parsed and enriched."""
    config = AppConfig()
    config.mqtt.enabled = True

    service = MqttService(config=config, storage=temp_storage)

    received_paths = []
    bus.subscribe(EventType.PACKET_PATH_TRACED, lambda p: received_paths.append(p))

    raw_hex = "1501A1008899DEADBEEF"
    json_payload = json.dumps({
        "raw": raw_hex,
        "origin": "Dale",
        "origin_id": "observer-777",
        "SNR": 14.5,
        "RSSI": -68,
        "timestamp": 1742475000
    }).encode("utf-8")

    class FakeMsg:
        topic = "meshcore/uk/Dale/packets"
        payload = json_payload

    service._on_message(None, None, FakeMsg())

    assert len(received_paths) == 1
    p = received_paths[0]
    assert p.raw_hex == raw_hex
    assert p.hop_snrs == [14.5]
    assert p.decoded_info.get("observer_origin") == "Dale"
    assert p.decoded_info.get("rssi") == -68


def test_settings_widget_ukmesh_preset_selection():
    """Verifies that selecting UKMesh preset in SettingsWidget configures the exact parameters."""
    from PyQt6.QtWidgets import QApplication
    from meshcore_tray.ui.settings_widget import SettingsWidget

    app = QApplication.instance() or QApplication([])

    config = AppConfig()
    widget = SettingsWidget(config=config)

    # Find UKMesh preset index
    ukmesh_idx = -1
    for i in range(widget.combo_mqtt_preset.count()):
        if "UKMesh" in widget.combo_mqtt_preset.itemText(i):
            ukmesh_idx = i
            break

    assert ukmesh_idx > 0, "UKMesh preset not found in combo box"

    # Select preset
    widget.combo_mqtt_preset.setCurrentIndex(ukmesh_idx)

    # Verify fields
    assert widget.mqtt_host_input.text() == "mqtt.ukmesh.com"
    assert widget.mqtt_port_spin.value() == 443
    assert widget.mqtt_user_input.text() == "soulway"
    assert widget.mqtt_pass_input.text() == "ZM3d2A94ZBu5btbK"
    assert widget.chk_mqtt_tls.isChecked() is True
    assert widget.combo_mqtt_transport.currentData() == "websockets"
    assert widget.mqtt_ws_path_input.text() == "/mqtt"
    assert widget.mqtt_topics_input.text() == "public/+/+/packets, public/#"
    assert widget.chk_mqtt_publish.isChecked() is False

    # Apply settings and verify config
    widget._apply_settings(close_on_finish=False)
    assert config.mqtt.broker_host == "mqtt.ukmesh.com"
    assert config.mqtt.broker_port == 443
    assert config.mqtt.username == "soulway"
    assert config.mqtt.password == "ZM3d2A94ZBu5btbK"
    assert config.mqtt.use_tls is True
    assert config.mqtt.transport == "websockets"
    assert config.mqtt.ws_path == "/mqtt"
    assert config.mqtt.subscribe_topics == ["public/+/+/packets", "public/#"]
    assert config.mqtt.publish_enabled is False


def test_mqtt_advert_discovers_node_with_source_and_updates_map(temp_storage):
    """Verifies that an incoming ADVERT packet over MQTT sets source='mqtt' and notifies EventBus/map."""
    import struct

    config = AppConfig()
    config.mqtt.enabled = True
    service = MqttService(config=config, storage=temp_storage)

    discovered_nodes = []
    map_updated_events = []
    bus.subscribe(EventType.NODE_DISCOVERED, lambda n: discovered_nodes.append(n))
    bus.subscribe(EventType.MAP_NODES_UPDATED, lambda _: map_updated_events.append(True))

    # Build valid advert packet: 53.22034, -1.466099 (Somersall Repeater)
    hdr_b = bytes([0x11, 0x01, 0xAB])
    pubkey = b"\x07" * 32
    ts = struct.pack("<I", 1726000000)
    sig = b"\x02" * 64
    flag = bytes([0x92])  # has_location | has_name | repeater
    coords = struct.pack("<ii", int(53.22034 * 1000000), int(-1.466099 * 1000000))
    name = b"Somersall Repeater\x00"
    raw = (hdr_b + pubkey + ts + sig + flag + coords + name).hex()

    json_payload = json.dumps({
        "raw": raw,
        "origin": "UKMesh-EastMidlands",
        "origin_id": "EMA",
        "SNR": 9.5
    }).encode("utf-8")

    class FakeMsg:
        topic = "meshcore/EMA/07070707/packets"
        payload = json_payload

    service._on_message(None, None, FakeMsg())

    # Check contact in storage
    node_id = ("07" * 32)[:12]
    contact = temp_storage.get_contact(node_id)
    assert contact is not None
    assert contact.alias == "Somersall Repeater"
    assert contact.latitude == pytest.approx(53.22034, abs=1e-4)
    assert contact.longitude == pytest.approx(-1.466099, abs=1e-4)
    assert contact.is_repeater is True
    assert contact.source == "mqtt"

    # Check EventBus emissions
    assert len(discovered_nodes) == 1
    assert discovered_nodes[0].source == "mqtt"
    assert len(map_updated_events) >= 1


def test_mqtt_advert_preserves_radio_source(temp_storage):
    """Verifies that an existing contact heard directly on physical radio preserves source='radio'."""
    import struct
    from meshcore_tray.core.models import NodeContact

    # Pre-populate contact with source="radio"
    node_id = ("08" * 32)[:12]
    temp_storage.save_contact(NodeContact(
        node_id=node_id,
        alias="Local Repeater",
        public_key="08" * 32,
        latitude=53.0,
        longitude=-1.0,
        is_repeater=True,
        source="radio"
    ))

    config = AppConfig()
    config.mqtt.enabled = True
    service = MqttService(config=config, storage=temp_storage)

    # Ingest advert from MQTT for the same node
    hdr_b = bytes([0x11, 0x01, 0xAB])
    pubkey = b"\x08" * 32
    ts = struct.pack("<I", 1726000000)
    sig = b"\x02" * 64
    flag = bytes([0x92])
    coords = struct.pack("<ii", int(53.12345 * 1000000), int(-1.12345 * 1000000))
    name = b"Local Repeater Updated\x00"
    raw = (hdr_b + pubkey + ts + sig + flag + coords + name).hex()

    json_payload = json.dumps({"raw": raw}).encode("utf-8")

    class FakeMsg:
        topic = "meshcore/packets"
        payload = json_payload

    service._on_message(None, None, FakeMsg())

    contact = temp_storage.get_contact(node_id)
    assert contact is not None
    assert contact.alias == "Local Repeater Updated"
    assert contact.source == "radio", "Physical RF radio source must not be overwritten by MQTT"


def test_storage_ghana_coordinate_auto_repair(tmp_path):
    """Verifies that Storage._init_db auto-repairs legacy 10x-shifted coordinates."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    # Create legacy table and insert shifted coordinates (lat 5.3° in Accra, Ghana)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE contacts (
            node_id TEXT PRIMARY KEY,
            alias TEXT,
            public_key TEXT,
            latitude REAL,
            longitude REAL,
            is_repeater INTEGER DEFAULT 0,
            is_room_server INTEGER DEFAULT 0,
            last_seen TEXT,
            first_seen TEXT,
            source TEXT DEFAULT 'radio'
        )
    """)
    conn.execute(
        "INSERT INTO contacts (node_id, alias, latitude, longitude) VALUES (?, ?, ?, ?)",
        ("repeater123", "Somersall Repeater", 5.322034, -0.146609)
    )
    conn.commit()
    conn.close()

    # Opening storage should run migration and auto-repair 10x
    storage = Storage(str(db_path))
    contact = storage.get_contact("repeater123")
    assert contact is not None
    assert contact.latitude == pytest.approx(53.22034, abs=1e-4)
    assert contact.longitude == pytest.approx(-1.46609, abs=1e-4)


def test_nav_dock_and_map_mqtt_nodes_layer():
    """Verifies that NavDock and MeshMapWidget layer toggling for mqtt_nodes functions properly."""
    from PyQt6.QtWidgets import QApplication
    from meshcore_tray.ui.nav_dock import NavDockWidget

    app = QApplication.instance() or QApplication([])

    config = AppConfig()
    dock = NavDockWidget(config=config)
    assert hasattr(dock.map_layers, "btn_mqtt_nodes")
    assert hasattr(dock, "btn_mqtt_nodes")
    assert dock.map_layers.btn_mqtt_nodes.isChecked() is False

    toggled_events = []
    dock.layer_toggled.connect(lambda k, v: toggled_events.append((k, v)))

    # Toggle button via click
    dock.map_layers.btn_mqtt_nodes.click()
    assert ("mqtt_nodes", True) in toggled_events
    assert dock.map_layers.btn_mqtt_nodes.isChecked() is True

    # Programmatic set_layer_active True
    dock.set_layer_active("mqtt_nodes", True)
    assert dock.map_layers.btn_mqtt_nodes.isChecked() is True

    # Programmatic set_layer_active False
    dock.set_layer_active("mqtt_nodes", False)
    assert dock.map_layers.btn_mqtt_nodes.isChecked() is False


def test_map_leaflet_mqtt_nodes_template_and_styling():
    """Verifies Leaflet template includes mqtt-nodes-panel and dynamic styling without static outline."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE

    # 1. Overlay panel exists in HTML
    assert 'id="mqtt-nodes-panel"' in LEAFLET_HTML_TEMPLATE
    assert 'class="mqtt-nodes-panel map-overlay-panel"' in LEAFLET_HTML_TEMPLATE
    assert 'onclick="closeMqttNodes()"' in LEAFLET_HTML_TEMPLATE
    assert 'id="mqtt-discovered-count"' in LEAFLET_HTML_TEMPLATE

    # 2. Draggable registration
    assert "makeOverlayDraggable('mqtt-nodes-panel'" in LEAFLET_HTML_TEMPLATE

    # 3. Dynamic styling in applyNodeMarkerStyling
    assert "if (window._mqttNodesActive)" in LEAFLET_HTML_TEMPLATE
    assert "dot.style.setProperty('background', '#F97316', 'important');" in LEAFLET_HTML_TEMPLATE
    assert "dot.style.setProperty('border', '1.5px solid #FFFFFF', 'important');" in LEAFLET_HTML_TEMPLATE

    # 4. Fallback unstyling cleans background and restores opacity
    assert "dot.style.removeProperty('background');" in LEAFLET_HTML_TEMPLATE
    assert "calculateFreshnessOpacity(node.last_seen)" in LEAFLET_HTML_TEMPLATE

    # 5. node-dot-mqtt is NOT statically attached to all MQTT markers in setNodes
    assert "dotClass += ' node-dot-mqtt';" not in LEAFLET_HTML_TEMPLATE


def test_save_contacts_bulk_preserves_mqtt_source(temp_storage):
    """Verifies that radio hardware bulk sync does not overwrite existing MQTT contact source."""
    from meshcore_tray.core.models import NodeContact

    # Insert an MQTT discovered contact
    mqtt_node = NodeContact(
        node_id="ne11dunston2",
        alias="GB-GAT-NE11-Dunston-2",
        public_key="3e7cdf00e9bc00000000000000000000",
        latitude=54.94784,
        longitude=-1.65122,
        source="mqtt"
    )
    temp_storage.save_contact(mqtt_node)
    assert temp_storage.get_contact("ne11dunston2").source == "mqtt"

    # Radio hardware flash sync comes along with source='radio'
    flash_node = NodeContact(
        node_id="ne11dunston2",
        alias="GB-GAT-NE11-Dunston-2",
        public_key="3e7cdf00e9bc00000000000000000000",
        latitude=54.94784,
        longitude=-1.65122,
        source="radio"
    )
    temp_storage.save_contacts_bulk([flash_node])

    # Must preserve source='mqtt'
    saved = temp_storage.get_contact("ne11dunston2")
    assert saved is not None
    assert saved.source == "mqtt"


def test_storage_mqtt_source_auto_repair(tmp_path):
    """Verifies that Storage._init_db auto-repairs MQTT contacts incorrectly marked as radio by migration."""
    import sqlite3

    db_path = tmp_path / "test_repair.db"
    conn = sqlite3.connect(str(db_path))
    # Create legacy table structure with source='radio' default
    conn.execute("""
        CREATE TABLE contacts (
            node_id TEXT PRIMARY KEY,
            alias TEXT,
            public_key TEXT,
            latitude REAL,
            longitude REAL,
            is_repeater INTEGER DEFAULT 0,
            is_room_server INTEGER DEFAULT 0,
            last_seen TEXT,
            first_seen TEXT,
            source TEXT DEFAULT 'radio'
        )
    """)
    conn.execute("""
        CREATE TABLE packet_paths (
            packet_id TEXT PRIMARY KEY,
            sender_id TEXT,
            sender_name TEXT,
            source TEXT DEFAULT 'radio',
            timestamp TEXT
        )
    """)
    # Insert node incorrectly marked radio
    conn.execute(
        "INSERT INTO contacts (node_id, alias, source) VALUES (?, ?, ?)",
        ("65821b80039f", "GNOME-STKTN-RPT-V4", "radio")
    )
    # Insert MQTT packet path for this node
    conn.execute(
        "INSERT INTO packet_paths (packet_id, sender_id, sender_name, source) VALUES (?, ?, ?, ?)",
        ("mqtt-1726000001", "65821b80039f", "GNOME-STKTN-RPT-V4", "mqtt")
    )
    conn.commit()
    conn.close()

    # Initializing Storage should auto-repair source to 'mqtt'
    storage = Storage(str(db_path))
    c = storage.get_contact("65821b80039f")
    assert c is not None
    assert c.source == "mqtt"


def test_mqtt_service_req_packet_formatting(temp_storage):
    """Verifies that direct/control REQ packets format descriptive labels rather than bare hashes."""
    config = AppConfig()
    config.mqtt.enabled = True

    service = MqttService(config=config, storage=temp_storage)

    received_paths = []
    bus.subscribe(EventType.PACKET_PATH_TRACED, lambda p: received_paths.append(p))

    # Construct REQ packet:
    # Header byte 0: 0x01 (FLOOD route, REQ payload)
    # Path byte 1: 0x00 (0 hops)
    # Payload: dest_hash=0x12, src_hash=0x95, mac=0xABCD, encrypted_data=0x01020304
    raw_hex = "01001295ABCD01020304"

    json_payload = json.dumps({
        "raw": raw_hex,
        "gateway": "M7YCD-DL5-ROOM-OBS"
    }).encode("utf-8")

    class FakeMsg:
        topic = "meshcore/public/M7YCD-DL5-ROOM-OBS/packets"
        payload = json_payload

    service._on_message(None, None, FakeMsg())

    assert len(received_paths) == 1
    p = received_paths[0]
    assert p.sender_id == "95"
    assert "Node [95]" in p.sender_name
    assert "Node [12]" in p.sender_name
    assert "via @M7YCD-DL5-ROOM-OBS" in p.sender_name




