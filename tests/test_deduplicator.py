import time
import pytest
from meshcore_tray.core.deduplicator import PacketDeduplicator, get_deduplicator


def test_deduplicator_hex_matching():
    dedup = PacketDeduplicator(default_window_secs=2.0)
    raw = "15024A8F0011223344"

    # Radio registers packet
    dedup.register_radio_packet(raw_hex=raw)

    # Check MQTT duplicate immediately -> should be True (duplicate)
    assert dedup.is_duplicate_or_radio(raw_hex=raw) is True
    assert dedup.is_duplicate_or_radio(raw_hex="  15024a8f0011223344  ") is True

    # Different packet should be False
    assert dedup.is_duplicate_or_radio(raw_hex="AABBCCDDEEFF") is False


def test_deduplicator_logical_key_matching():
    dedup = PacketDeduplicator(default_window_secs=2.0)

    # Radio registers packet with sender, channel, and message text
    dedup.register_radio_packet(
        sender_id="!4a2f8b1c",
        channel="cumbria",
        text="Hello over LoRa mesh!"
    )

    # MQTT arrives with identical message payload
    assert dedup.is_duplicate_or_radio(
        sender_id="4a2f8b1c",
        channel="#cumbria",
        text="Hello over LoRa mesh!"
    ) is True

    # Different text should not match
    assert dedup.is_duplicate_or_radio(
        sender_id="4a2f8b1c",
        channel="cumbria",
        text="Different text message"
    ) is False


def test_deduplicator_mqtt_ingest_and_window_expiry():
    dedup = PacketDeduplicator(default_window_secs=0.1)
    raw = "DEADBEEF123456"

    # 1st MQTT arrival -> accepted (register_mqtt_packet returns False for duplicate)
    assert dedup.register_mqtt_packet(raw_hex=raw) is False

    # 2nd MQTT arrival immediately -> duplicate (returns True)
    assert dedup.register_mqtt_packet(raw_hex=raw) is True

    # Wait for TTL to expire
    time.sleep(0.15)

    # After expiry, should not be marked as duplicate
    assert dedup.is_duplicate_or_radio(raw_hex=raw) is False


def test_get_deduplicator_singleton():
    d1 = get_deduplicator()
    d2 = get_deduplicator()
    assert d1 is d2
