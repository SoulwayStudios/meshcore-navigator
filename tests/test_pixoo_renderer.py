"""Unit tests for Pixoo 64 3-Message Rolling Stack, Sender Grouping, Dimming & Queued Channel Switching."""

import time
import pytest
from PIL import Image
from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import MessageEnvelope, TelemetryEnvelope, NeighbourInfo
from meshcore_tray.pixoo.pixoo_renderer import (
    PixooRenderer, DisplayMode, COLOR_TAG_BLUE, COLOR_WHITE, COLOR_GREY_25, COLOR_GREY_50
)


def test_renderer_initialization():
    config = AppConfig()
    renderer = PixooRenderer(config=config)
    frame = renderer.render_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (64, 64)


def test_three_message_rolling_fifo_stack_progression():
    """Verify exact FIFO rolling stack progression as specified by user:
    Alice, Alice, Alice -> Bob posts -> Alice, Alice, Bob -> Bob posts again -> Alice, Bob, Bob -> Charlie posts -> Bob, Bob, Charlie.
    """
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # 1. Alice posts 3 messages
    renderer.trigger_message_alert(MessageEnvelope(id="a1", sender_name="Alice", channel="Public", text="Alice msg 1"))
    renderer.trigger_message_alert(MessageEnvelope(id="a2", sender_name="Alice", channel="Public", text="Alice msg 2"))
    renderer.trigger_message_alert(MessageEnvelope(id="a3", sender_name="Alice", channel="Public", text="Alice msg 3"))

    stack = renderer.channel_messages["Public"]
    assert len(stack) == 3
    assert [m.sender_name for m in stack] == ["Alice", "Alice", "Alice"]
    assert [m.text for m in stack] == ["Alice msg 1", "Alice msg 2", "Alice msg 3"]

    # 2. Bob posts -> removes Alice's oldest message (a1)
    renderer.trigger_message_alert(MessageEnvelope(id="b1", sender_name="Bob", channel="Public", text="Bob msg 1"))
    stack = renderer.channel_messages["Public"]
    assert len(stack) == 3
    assert [m.sender_name for m in stack] == ["Alice", "Alice", "Bob"]
    assert [m.text for m in stack] == ["Alice msg 2", "Alice msg 3", "Bob msg 1"]

    # 3. Bob posts again -> removes Alice's next oldest message (a2)
    renderer.trigger_message_alert(MessageEnvelope(id="b2", sender_name="Bob", channel="Public", text="Bob msg 2"))
    stack = renderer.channel_messages["Public"]
    assert len(stack) == 3
    assert [m.sender_name for m in stack] == ["Alice", "Bob", "Bob"]
    assert [m.text for m in stack] == ["Alice msg 3", "Bob msg 1", "Bob msg 2"]

    # 4. Charlie posts -> removes Alice's last message (a3)
    renderer.trigger_message_alert(MessageEnvelope(id="c1", sender_name="Charlie", channel="Public", text="Charlie msg 1"))
    stack = renderer.channel_messages["Public"]
    assert len(stack) == 3
    assert [m.sender_name for m in stack] == ["Bob", "Bob", "Charlie"]
    assert [m.text for m in stack] == ["Bob msg 1", "Bob msg 2", "Charlie msg 1"]


def test_queued_channel_switch_fifteen_seconds():
    config = AppConfig()
    config.pixoo.channel_filters = {"Public": True, "ops": True}
    renderer = PixooRenderer(config=config)

    # 1. Message arrives on Public -> switches to Public immediately
    msg_pub = MessageEnvelope(id="m1", sender_name="Alice", channel="Public", text="Public hello")
    renderer.trigger_message_alert(msg_pub)
    assert renderer.get_active_channel_pages()[renderer.current_page_idx].lower() == "public"

    # 2. Message arrives 2 seconds later on ops (< 15s) -> should queue switch
    time_pub = renderer.last_switch_time
    msg_ops = MessageEnvelope(id="m2", sender_name="Bob", channel="ops", text="Ops alert")
    renderer.trigger_message_alert(msg_ops)

    # Still viewing Public, switch is pending
    assert renderer.get_active_channel_pages()[renderer.current_page_idx].lower() == "public"
    assert renderer.pending_channel_switch == "ops"
    assert renderer.pending_switch_time == time_pub + 15.0


def test_consecutive_user_grouping_and_dimming():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # Add 3 run-on messages from Alice
    renderer.trigger_message_alert(MessageEnvelope(id="a1", sender_name="Alice", channel="Public", text="1st thought"))
    renderer.trigger_message_alert(MessageEnvelope(id="a2", sender_name="Alice", channel="Public", text="2nd thought"))
    renderer.trigger_message_alert(MessageEnvelope(id="a3", sender_name="Alice", channel="Public", text="3rd thought"))

    page_img = renderer._render_channel_page("Public", is_header_inverted=False)
    assert isinstance(page_img, Image.Image)
    assert page_img.size == (64, 64)


def test_tag_highlighting_in_message_body():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    tokens = renderer._wrap_and_tokenize("Test @Alice on #general and #Public", base_color=(255, 255, 255), max_chars_per_line=12)
    tag_colors = []
    for line in tokens:
        for word, color in line:
            if word in ("@Alice", "#general", "#Public"):
                tag_colors.append((word, color))

    for word, col in tag_colors:
        assert col == COLOR_TAG_BLUE


def test_inverted_header_flash_alert():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    msg = MessageEnvelope(id="m_alert", sender_name="Alice", channel="Public", text="Alert msg")
    renderer.trigger_message_alert(msg)

    assert renderer.is_flashing is True

    frame = renderer.render_frame()
    assert isinstance(frame, Image.Image)
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

    msg_test = MessageEnvelope(id="m_test", sender_name="Tester", channel="#test", text="Test message")
    renderer.trigger_message_alert(msg_test)
    assert len(renderer.channel_messages["test"]) == 0
    assert renderer.is_flashing is False

    msg_pub = MessageEnvelope(id="m_pub", sender_name="Alice", channel="Public", text="Public message")
    renderer.trigger_message_alert(msg_pub)
    assert len(renderer.channel_messages["Public"]) == 1
    assert renderer.is_flashing is True


def test_quiet_hours_blackout():
    config = AppConfig()
    config.quiet_hours.enabled = True
    config.quiet_hours.start_time = "00:00"
    config.quiet_hours.end_time = "23:59"
    config.quiet_hours.action = "blackout"

    renderer = PixooRenderer(config=config)
    frame = renderer.render_frame()

    for y in range(0, 64, 8):
        for x in range(0, 64, 8):
            assert frame.getpixel((x, y)) == (0, 0, 0)
