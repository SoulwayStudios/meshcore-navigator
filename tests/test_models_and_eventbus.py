"""Unit tests for models and EventBus."""

import pytest
from meshcore_tray.core.models import (
    MessageEnvelope, TelemetryEnvelope, NeighbourInfo, CommandPacket
)
from meshcore_tray.core.event_bus import EventBus, EventType


def test_message_envelope_serialization():
    msg = MessageEnvelope(
        id="msg-001",
        sender_id="!8f3a",
        sender_name="Alice",
        channel="Public",
        text="Testing message envelope",
        is_favorite=True,
        metadata={"snr": 10.2, "rssi": -76.0}
    )
    d = msg.to_dict()
    assert d["id"] == "msg-001"
    assert d["sender_name"] == "Alice"
    assert d["is_favorite"] is True
    assert d["metadata"]["snr"] == 10.2

    reconstructed = MessageEnvelope.from_dict(d)
    assert reconstructed.id == msg.id
    assert reconstructed.sender_name == msg.sender_name
    assert reconstructed.metadata == msg.metadata


def test_telemetry_envelope_serialization():
    telem = TelemetryEnvelope(
        frequency_mhz=868.125,
        bandwidth_khz=250.0,
        spreading_factor=7,
        coding_rate="4/5",
        tx_power_dbm=22,
        noise_floor_dbm=-118.0
    )
    d = telem.to_dict()
    assert d["frequency_mhz"] == 868.125
    assert d["spreading_factor"] == 7

    reconstructed = TelemetryEnvelope.from_dict(d)
    assert reconstructed.frequency_mhz == telem.frequency_mhz


def test_event_bus_pub_sub():
    bus = EventBus()
    received_events = []

    def handler(data):
        received_events.append(data)

    bus.subscribe(EventType.MESSAGE_RECEIVED, handler)

    test_payload = {"test": "data_123"}
    bus.emit(EventType.MESSAGE_RECEIVED, test_payload)

    assert len(received_events) == 1
    assert received_events[0] == test_payload

    bus.unsubscribe(EventType.MESSAGE_RECEIVED, handler)
    bus.emit(EventType.MESSAGE_RECEIVED, {"second": "data"})
    assert len(received_events) == 1
