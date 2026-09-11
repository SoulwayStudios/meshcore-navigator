"""Unit tests for MeshCoreDriver message decoding, contact synchronization, and telemetry."""

import pytest
from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope, TelemetryEnvelope
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.storage import Storage


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_driver.db"
    return Storage(db_file)


@pytest.fixture
def driver(temp_storage):
    config = AppConfig()
    config.favorites = ["Alice", "!8f3a", "Public"]
    return MeshCoreDriver(config=config, storage=temp_storage)


def test_handle_channel_message(driver, temp_storage):
    received = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received.append(m))

    event_payload = {
        "type": "CHAN",
        "channel_idx": 0,
        "text": "G7VQV: Morning Eastleigh signal check",
        "SNR": 10.5,
        "RSSI": -78.0,
        "sender_timestamp": 1788417769
    }

    driver._handle_channel_msg(event_payload)

    assert len(received) == 1
    msg = received[0]
    assert msg.channel == "Public"
    assert msg.sender_name == "G7VQV"
    assert msg.text == "Morning Eastleigh signal check"
    assert msg.metadata["snr"] == 10.5
    assert msg.metadata["rssi"] == -78.0
    assert not msg.is_direct_message

    # Check persistence
    stored_msgs = temp_storage.get_messages(channel="Public")
    assert len(stored_msgs) >= 1
    assert stored_msgs[-1].text == "Morning Eastleigh signal check"


def test_handle_contact_message(driver, temp_storage):
    received = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received.append(m))

    event_payload = {
        "type": "PRIV",
        "pubkey_prefix": "3705763e392d",
        "text": "Direct test message",
        "SNR": 8.0,
        "RSSI": -82.0,
        "sender_timestamp": 1788418000
    }

    driver._handle_contact_msg(event_payload)

    assert len(received) == 1
    msg = received[0]
    assert msg.is_direct_message
    assert msg.sender_id == "3705763e392d"
    assert msg.text == "Direct test message"
    assert msg.metadata["snr"] == 8.0


def test_handle_battery_telemetry(driver, temp_storage):
    telemetry_updates = []
    bus.subscribe(EventType.TELEMETRY_UPDATED, lambda t: telemetry_updates.append(t))

    event_payload = {
        "level": 4150,  # mV
        "used_kb": 200,
        "total_kb": 1400
    }

    driver._handle_battery(event_payload)

    assert len(telemetry_updates) == 1
    t = telemetry_updates[0]
    assert t.voltage_volts == 4.15
    assert t.battery_pct > 80


def test_handle_contacts_sync(driver, temp_storage):
    neighbours_updates = []
    bus.subscribe(EventType.NEIGHBOURS_UPDATED, lambda n: neighbours_updates.append(n))

    contacts_payload = {
        "b60e77b8c29cfbc2d6ab075d9af535bb0fc4aacfe4006de51a7aea25746974ad": {
            "public_key": "b60e77b8c29cfbc2d6ab075d9af535bb0fc4aacfe4006de51a7aea25746974ad",
            "type": 2,
            "adv_name": "Repeater-Echo",
            "last_advert": 1788417700
        },
        "7bd00810a3b5316477d30d99835c3dff209fba6abb54124a2de85c3cc5e4b6cd": {
            "public_key": "7bd00810a3b5316477d30d99835c3dff209fba6abb54124a2de85c3cc5e4b6cd",
            "type": 1,
            "adv_name": "Mobile-Alpha",
            "last_advert": 1788417750
        }
    }

    driver._handle_contacts(contacts_payload)

    stored_contacts = temp_storage.get_contacts()
    assert len(stored_contacts) == 2

    # Check repeater flag
    contact_map = {c.alias: c for c in stored_contacts}
    assert contact_map["Repeater-Echo"].is_repeater is True
    assert contact_map["Mobile-Alpha"].is_repeater is False

    assert len(neighbours_updates) == 1
    assert len(neighbours_updates[0]) == 2


def test_join_channel(driver, temp_storage):
    # Test joining #cumbria
    res = driver.join_channel("#cumbria")
    assert res["status"] == "ok"
    assert res["name"] == "#cumbria"
    assert res["channel_id"] == 1

    stored_ch = temp_storage.get_channel_by_name("#cumbria")
    assert stored_ch is not None
    assert stored_ch.name == "#cumbria"
    assert stored_ch.channel_id == 1

    # Test joining #test
    res2 = driver.join_channel("#test")
    assert res2["status"] == "ok"
    assert res2["name"] == "#test"
    assert res2["channel_id"] == 2

    stored_test = temp_storage.get_channel_by_name("#test")
    assert stored_test is not None
    assert stored_test.channel_id == 2


def test_set_radio_params(driver, temp_storage):
    telemetry_updates = []
    bus.subscribe(EventType.TELEMETRY_UPDATED, lambda t: telemetry_updates.append(t))

    res = driver.set_radio_params(
        frequency_mhz=869.618,
        bandwidth_khz=62.5,
        spreading_factor=8,
        coding_rate="4/5",
        tx_power_dbm=22
    )

    assert res["status"] == "ok"
    assert res["frequency_mhz"] == 869.618
    assert res["bandwidth_khz"] == 62.5
    assert res["spreading_factor"] == 8
    assert res["coding_rate"] == "4/5"
    assert res["tx_power_dbm"] == 22

    # Check database persistence
    telem = temp_storage.get_latest_telemetry("local")
    assert telem is not None
    assert telem.frequency_mhz == 869.618
    assert telem.bandwidth_khz == 62.5
    assert telem.spreading_factor == 8
    assert telem.coding_rate == "4/5"
    assert telem.tx_power_dbm == 22


def test_set_node_name(driver):
    res = driver.set_node_name("G1CAE-Base")
    assert res["status"] == "ok"
    assert res["name"] == "G1CAE-Base"
    assert driver.config.meshcore.node_alias == "G1CAE-Base"


def test_channel_message_deduplication(driver, temp_storage):
    received = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received.append(m))

    event_payload = {
        "type": "CHAN",
        "channel_idx": 1,
        "chan_name": "#cumbria",
        "text": "M7NCY: Test from mobile node",
        "SNR": 9.2,
        "RSSI": -75.0,
        "sender_timestamp": 1788528000
    }

    # First arrival (e.g. from live OTA RX_LOG_DATA)
    driver._handle_channel_msg(event_payload)
    assert len(received) == 1
    assert received[0].channel == "#cumbria"
    assert received[0].text == "Test from mobile node"

    # Second duplicate arrival (e.g. from hardware drain_messages queue)
    driver._handle_channel_msg(event_payload)
    # Should still only have 1 message emitted due to deduplication
    assert len(received) == 1


def test_live_ota_channel_message_forwarding(driver, temp_storage):
    from meshcore_tray.core.models import ChannelInfo
    temp_storage.save_channel(ChannelInfo(channel_id=1, name="#cumbria", is_favorite=True))

    received = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received.append(m))

    rx_payload = {
        "route_type": 1,
        "route_typename": "FLOOD",
        "payload_type": 5,
        "chan_name": "#cumbria",
        "message": "OTA test message to cumbria",
        "sender_timestamp": 1788528500,
        "snr": 11.0,
        "rssi": -72.0,
        "path": "0102",
        "path_len": 2
    }

    driver._handle_rx_log_data(rx_payload)

    assert len(received) == 1
    msg = received[0]
    assert msg.channel == "#cumbria"
    assert msg.text == "OTA test message to cumbria"
    assert msg.metadata["snr"] == 11.0


def test_ensure_channel_synced(driver, temp_storage):
    from meshcore_tray.core.models import ChannelInfo
    temp_storage.save_channel(ChannelInfo(channel_id=1, name="#cumbria", is_favorite=True))

    # Should return False when not connected
    assert driver.ensure_channel_synced("#cumbria") is False
    # Public is always assumed synced
    assert driver.ensure_channel_synced("Public") is True


def test_channel_message_echo_suppression_and_repeat_increment(driver, temp_storage):
    from meshcore_tray.core.models import MessageEnvelope
    driver.config.meshcore.node_alias = "M7NCY_MC_MAPPER"

    out_msg = MessageEnvelope(
        id="out-123",
        source_driver="meshcore_serial",
        sender_id="!local",
        sender_name="M7NCY_MC_MAPPER",
        channel="#test",
        is_direct_message=False,
        text="testing a message",
        is_outgoing=True,
        repeats_heard=0
    )
    temp_storage.save_message(out_msg)
    driver._record_outgoing_message("out-123", "#test", "testing a message")

    received = []
    updated = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received.append(m))
    bus.subscribe(EventType.MESSAGE_UPDATED, lambda m: updated.append(m))

    # Simulate OTA repeated packet heard over RF
    echo_payload = {
        "type": "CHAN",
        "chan_name": "#test",
        "channel_idx": 1,
        "text": "M7NCY_MC_MAPPER: testing a message",
        "SNR": 12.5,
        "RSSI": -40.0,
        "sender_timestamp": 1788559817,
        "path": "dd2d",
        "path_len": 1,
        "hop_nodes": ["@M7NCY West Yagi"]
    }

    driver._handle_channel_msg(echo_payload)

    # 1. MESSAGE_RECEIVED must NOT be emitted (prevents duplicate chat bubble & Pixoo false alert)
    assert len(received) == 0

    # 2. MESSAGE_UPDATED MUST be emitted to update the 'repeats heard' badge
    assert len(updated) == 1
    assert updated[0].id == "out-123"
    assert updated[0].repeats_heard == 1

    # 3. Storage must still only have 1 message (the outgoing one), no duplicate incoming message
    all_msgs = temp_storage.get_messages(channel="#test")
    assert len(all_msgs) == 1
    assert all_msgs[0].id == "out-123"
    assert all_msgs[0].repeats_heard == 1


def test_storage_cleanup_echo_messages(temp_storage):
    from meshcore_tray.core.models import MessageEnvelope

    out_msg = MessageEnvelope(
        id="out-1",
        sender_name="M7NCY_MC_MAPPER",
        channel="#test",
        text="Hello world",
        is_outgoing=True
    )
    echo_msg = MessageEnvelope(
        id="chan-echo-1",
        sender_name="M7NCY_MC_MAPPER",
        channel="#test",
        text="Hello world",
        is_outgoing=False
    )
    other_msg = MessageEnvelope(
        id="chan-valid-2",
        sender_name="G7VQV",
        channel="#test",
        text="Morning Eastleigh",
        is_outgoing=False
    )

    temp_storage.save_message(out_msg)
    temp_storage.save_message(echo_msg)
    temp_storage.save_message(other_msg)

    assert len(temp_storage.get_messages(channel="#test")) == 3

    deleted = temp_storage.cleanup_echo_messages("M7NCY_MC_MAPPER")
    assert deleted == 1

    remaining = temp_storage.get_messages(channel="#test")
    assert len(remaining) == 2
    rem_ids = [m.id for m in remaining]
    assert "out-1" in rem_ids
    assert "chan-valid-2" in rem_ids
    assert "chan-echo-1" not in rem_ids


def test_map_marker_glow_styles():
    from meshcore_tray.ui.mesh_map_widget import get_leaflet_html
    html = get_leaflet_html()

    assert ".node-dot-repeater" in html
    assert "box-shadow: 0 0 6px var(--repeater-color);" in html
    assert ".node-dot-companion" in html
    assert "box-shadow: 0 0 6px var(--companion-color);" in html
    assert ".node-dot-favorite" in html
    assert "box-shadow: 0 0 6px var(--favorite-color);" in html
    assert ".node-dot-local" in html
    assert "box-shadow: 0 0 6px #38BDF8;" in html


