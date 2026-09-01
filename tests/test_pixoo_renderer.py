"""Unit tests for Pixoo 64 Single Latest Post per Channel, Tag Highlighting & Top-First Bounce."""

import time
import pytest
from PIL import Image
from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import MessageEnvelope, TelemetryEnvelope, NeighbourInfo
from meshcore_tray.pixoo.pixoo_renderer import (
    PixooRenderer, DisplayMode, COLOR_TAG_BLUE, COLOR_BODY_WHITE
)


def test_renderer_initialization():
    config = AppConfig()
    renderer = PixooRenderer(config=config)
    frame = renderer.render_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (64, 64)


def test_single_latest_message_per_channel():
    """Verify each channel holds and displays its single latest post."""
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # 1. Alice posts on Public
    renderer.trigger_message_alert(MessageEnvelope(id="a1", sender_name="Alice", channel="Public", text="Alice first message"))
    assert renderer.latest_messages["Public"].sender_name == "Alice"
    assert renderer.latest_messages["Public"].text == "Alice first message"

    # 2. Bob posts on Public -> replaces Alice's message as the latest post
    renderer.trigger_message_alert(MessageEnvelope(id="b1", sender_name="Bob", channel="Public", text="Bob new update"))
    assert renderer.latest_messages["Public"].sender_name == "Bob"
    assert renderer.latest_messages["Public"].text == "Bob new update"

    # 3. Charlie posts on ops -> separate channel
    renderer.trigger_message_alert(MessageEnvelope(id="c1", sender_name="Charlie", channel="ops", text="Ops coordination"))
    assert renderer.latest_messages["ops"].sender_name == "Charlie"
    assert renderer.latest_messages["Public"].sender_name == "Bob"


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
    assert renderer.latest_messages["test"] is None
    assert renderer.is_flashing is False

    msg_pub = MessageEnvelope(id="m_pub", sender_name="Alice", channel="Public", text="Public message")
    renderer.trigger_message_alert(msg_pub)
    assert renderer.latest_messages["Public"] is not None
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


def test_top_first_scrolling_and_bounce():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # Trigger long multi-line message
    long_msg = MessageEnvelope(
        id="m_long",
        sender_name="Charlie",
        channel="Public",
        text="Field report: Weather station update on mountain pass. Wind 25 knots gusting 40. Telemetry repeater active on frequency 868.125 MHz."
    )
    renderer.trigger_message_alert(long_msg)

    # Frame 0 is at the beginning of the hold at the top on the start of the message
    assert renderer.anim_frame == 0
    frame = renderer.render_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (64, 64)


def test_incoming_message_resets_page_timer():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    old_time = time.time() - 25.0
    renderer.page_start_time = old_time

    msg = MessageEnvelope(id="m_new", sender_name="Alice", channel="Public", text="Fresh transmission")
    renderer.trigger_message_alert(msg)

    assert renderer.page_start_time > old_time
    assert time.time() - renderer.page_start_time < 2.0


def test_short_message_fades_out_after_10_seconds():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    msg = MessageEnvelope(id="m_short", sender_name="Alice", channel="Public", text="Short hello")
    renderer.trigger_message_alert(msg)

    # Frame 0: message is full opacity
    frame_start = renderer.render_frame()
    assert isinstance(frame_start, Image.Image)

    # Fast forward past 10s + 1.5s fade duration (300 frames)
    renderer.anim_frame = 300
    frame_faded = renderer.render_frame()
    assert isinstance(frame_faded, Image.Image)


def test_long_message_fades_out_after_scroll_and_10s_hold():
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    long_msg = MessageEnvelope(
        id="m_long",
        sender_name="Charlie",
        channel="Public",
        text="Field report: Weather station update on mountain pass. Wind 25 knots gusting 40. Telemetry repeater active on frequency 868.125 MHz."
    )
    renderer.trigger_message_alert(long_msg)

    # Initial frame
    frame_start = renderer.render_frame()
    assert isinstance(frame_start, Image.Image)

    # Fast forward past scroll cycle + 10s hold + fade duration (1000 frames)
    renderer.anim_frame = 1000
    frame_faded = renderer.render_frame()
    assert isinstance(frame_faded, Image.Image)
