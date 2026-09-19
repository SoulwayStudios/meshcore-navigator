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
from meshcore_tray.core.mqtt_service import MqttService
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

