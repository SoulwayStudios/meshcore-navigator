"""Unit tests for Pixoo 64 matrix renderer, chat stream grouping, and state machine."""

import time
import pytest
from PIL import Image
from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import MessageEnvelope, TelemetryEnvelope, NeighbourInfo
from meshcore_tray.pixoo.pixoo_renderer import PixooRenderer, DisplayMode


def test_renderer_initialization():
    config = AppConfig()
    renderer = PixooRenderer(config=config)
    frame = renderer.render_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (64, 64)


def test_chat_grouping_consecutive_messages():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # 1. Message from Alice
    msg1 = MessageEnvelope(id="m1", sender_name="Alice", channel="Public", text="First message from Alice")
    renderer.trigger_message_alert(msg1)

    assert len(renderer.chat_groups) == 1
    assert renderer.chat_groups[0].sender_name == "Alice"
    assert len(renderer.chat_groups[0].messages) == 1

    # 2. Second consecutive message from Alice on same channel
    msg2 = MessageEnvelope(id="m2", sender_name="Alice", channel="Public", text="Second message from Alice")
    renderer.trigger_message_alert(msg2)

    # Should be grouped under the same heading
    assert len(renderer.chat_groups) == 1
    assert len(renderer.chat_groups[0].messages) == 2

    # 3. Message from Bob
    msg3 = MessageEnvelope(id="m3", sender_name="Bob", channel="Public", text="Hello from Bob")
    renderer.trigger_message_alert(msg3)

    # Should create a new chat group below Alice's
    assert len(renderer.chat_groups) == 2
    assert renderer.chat_groups[1].sender_name == "Bob"


def test_alert_strobe_and_upward_scroller():
    config = AppConfig()
    config.pixoo_colors.alert_color = "#00FF44"
    renderer = PixooRenderer(config=config)

    # Add several messages to exceed 64px vertical height
    for i in range(5):
        msg = MessageEnvelope(
            id=f"m_{i}",
            sender_name=f"User{i}",
            channel="Public",
            text=f"Line {i} test message for vertical chat stream scrolling"
        )
        renderer.trigger_message_alert(msg)

    assert renderer.is_flashing is True

    # Render frame during strobe phase (< 1s)
    frame = renderer.render_frame()
    assert frame.size == (64, 64)


def test_telemetry_rendering():
    config = AppConfig()
    renderer = PixooRenderer(config=config)
    telem = TelemetryEnvelope(
        frequency_mhz=868.125,
        bandwidth_khz=250.0,
        spreading_factor=7,
        coding_rate="4/5",
        tx_power_dbm=22,
        noise_floor_dbm=-118.0
    )
    renderer.trigger_telemetry(telem)
    assert renderer.mode == DisplayMode.TELEMETRY

    frame = renderer.render_frame()
    assert frame.size == (64, 64)


def test_neighbours_rendering():
    config = AppConfig()
    renderer = PixooRenderer(config=config)
    neighbours = [
        NeighbourInfo(node_id="!8f3a", alias="Alice", snr_db=11.5, rssi_dbm=-75.0),
        NeighbourInfo(node_id="!9c21", alias="Bob", snr_db=3.5, rssi_dbm=-88.0),
        NeighbourInfo(node_id="!10a4", alias="Charlie", snr_db=-4.0, rssi_dbm=-105.0)
    ]
    renderer.trigger_neighbours(neighbours)
    assert renderer.mode == DisplayMode.NEIGHBOURS

    frame = renderer.render_frame()
    assert frame.size == (64, 64)


def test_channel_filtering_silences_pixoo():
    config = AppConfig()
    config.pixoo.channel_filters = {"#test": False, "Public": True}
    renderer = PixooRenderer(config=config)

    # Trigger message on filtered channel #test
    msg_test = MessageEnvelope(id="m_test", sender_name="Tester", channel="#test", text="Test message")
    renderer.trigger_message_alert(msg_test)
    assert len(renderer.chat_groups) == 0
    assert renderer.is_flashing is False

    # Trigger message on allowed channel Public
    msg_pub = MessageEnvelope(id="m_pub", sender_name="Alice", channel="Public", text="Public message")
    renderer.trigger_message_alert(msg_pub)
    assert len(renderer.chat_groups) == 1
    assert renderer.is_flashing is True


def test_quiet_hours_blackout():
    config = AppConfig()
    config.quiet_hours.enabled = True
    config.quiet_hours.start_time = "00:00"
    config.quiet_hours.end_time = "23:59"
    config.quiet_hours.action = "blackout"

    renderer = PixooRenderer(config=config)
    frame = renderer.render_frame()

    # All pixels should be pure black (0, 0, 0)
    for y in range(0, 64, 8):
        for x in range(0, 64, 8):
            assert frame.getpixel((x, y)) == (0, 0, 0)
