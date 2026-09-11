"""Unit tests for ChatWidget read state tracking and unread message divider."""

import html
import sys
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.storage import Storage
from meshcore_tray.ui.chat_widget import ChatWidget, UnreadDivider, MessageBubble


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_chat_widget_unread_divider_placement(qapp, tmp_path):
    storage = Storage(tmp_path / "chat_unread_test.db")

    # Populate 3 messages
    msg1 = MessageEnvelope(id="m1", timestamp="2026-09-03T10:00:00Z", sender_name="UserA", channel="Public", text="First message")
    msg2 = MessageEnvelope(id="m2", timestamp="2026-09-03T10:05:00Z", sender_name="UserB", channel="Public", text="Second message (read boundary)")
    msg3 = MessageEnvelope(id="m3", timestamp="2026-09-03T10:10:00Z", sender_name="UserC", channel="Public", text="Third message (new unread)")
    msg4 = MessageEnvelope(id="m4", timestamp="2026-09-03T10:15:00Z", sender_name="UserD", channel="Public", text="Fourth message (new unread)")

    storage.save_message(msg1)
    storage.save_message(msg2)
    storage.save_message(msg3)
    storage.save_message(msg4)

    # Set last read state to msg2
    storage.mark_as_read("chan:Public", "m2", "2026-09-03T10:05:00Z")

    chat = ChatWidget(storage=storage)
    chat.set_target("Public")

    # Check child widgets in container layout
    widgets = []
    for i in range(chat.container_layout.count()):
        item = chat.container_layout.itemAt(i)
        if item and item.widget():
            widgets.append(item.widget())

    # Verify UnreadDivider is placed right before msg3
    divider_idx = -1
    for idx, w in enumerate(widgets):
        if isinstance(w, UnreadDivider):
            divider_idx = idx
            break

    assert divider_idx != -1, "UnreadDivider should be present in the message list"
    assert isinstance(widgets[divider_idx + 1], MessageBubble)
    assert widgets[divider_idx + 1].msg.id == "m3"

    # Commit mark as read and verify update to m4
    chat.mark_current_as_read()
    latest_read = storage.get_last_read("chan:Public")
    assert latest_read["last_read_msg_id"] == "m4"


def test_channel_single_hashtag_formatting(qapp, tmp_path):
    storage = Storage(tmp_path / "hashtag_test.db")
    chat = ChatWidget(storage=storage)

    # Test setting "#cumbria" -> should display "#cumbria" not "##cumbria"
    chat.set_target("#cumbria")
    assert chat.title_label.text() == "#cumbria"

    # Test setting "cumbria" without hash -> should display "#cumbria"
    chat.set_target("cumbria")
    assert chat.title_label.text() == "#cumbria"


def test_node_info_dialog_and_bubble_menu(qapp, tmp_path):
    storage = Storage(tmp_path / "node_info_test.db")
    msg = MessageEnvelope(
        id="m_info_1",
        sender_name="Heltec_Base",
        sender_id="!7e4b",
        channel="#cumbria",
        text="Beacon test from high peak",
        metadata={"snr": 11.2, "rssi": -72.0, "raw": "RAW_HEX_PACKET"}
    )
    storage.save_message(msg)

    from meshcore_tray.ui.chat_widget import NodeInfoDialog
    dlg = NodeInfoDialog(msg, storage=storage)
    assert dlg.windowTitle() == "Node Info — @Heltec_Base"

    # Test bubble context menu creation & visualise path signal
    bubble = MessageBubble(msg, storage=storage)
    assert bubble.msg.sender_name == "Heltec_Base"
    assert hasattr(bubble, "visualise_path_requested")

    received_msgs = []
    bubble.visualise_path_requested.connect(received_msgs.append)
    bubble.visualise_path_requested.emit(msg)
    assert len(received_msgs) == 1
    assert received_msgs[0].id == "m_info_1"


def test_blocked_user_filtered_from_chat(qapp, tmp_path):
    from meshcore_tray.config import AppConfig
    storage = Storage(tmp_path / "block_test.db")
    config = AppConfig()
    config.blocked_users = ["SpamBot", "!9999"]

    msg_ok = MessageEnvelope(id="m_ok", sender_name="LegitUser", sender_id="!1111", channel="Public", text="Legit msg")
    msg_spam = MessageEnvelope(id="m_spam", sender_name="SpamBot", sender_id="!9999", channel="Public", text="Spam link")

    storage.save_message(msg_ok)
    storage.save_message(msg_spam)

    chat = ChatWidget(storage=storage, config=config)
    chat.set_target("Public")

    bubbles = [chat.container_layout.itemAt(i).widget() for i in range(chat.container_layout.count()) if isinstance(chat.container_layout.itemAt(i).widget(), MessageBubble)]
    assert len(bubbles) == 1
    assert bubbles[0].msg.sender_name == "LegitUser"


def test_outgoing_message_shows_repeats_heard_and_updates(qapp, tmp_path):
    """Verifies that outgoing messages show '🔁 X repeats heard' and update dynamically."""
    storage = Storage(tmp_path / "repeats_test.db")
    out_msg = MessageEnvelope(
        id="out_1",
        sender_name="LocalNode",
        sender_id="!local",
        channel="Public",
        text="Testing repeater coverage",
        is_outgoing=True,
        repeats_heard=0
    )
    storage.save_message(out_msg)

    chat = ChatWidget(storage=storage)
    chat.set_target("Public")

    assert "out_1" in chat._bubbles
    bubble = chat._bubbles["out_1"]
    assert hasattr(bubble, "repeats_badge")
    assert "0 repeats heard" in bubble.repeats_badge.text()

    # Simulate 1 repeat heard
    out_msg.repeats_heard = 1
    storage.update_message_repeats("out_1", 1)
    chat.update_message(out_msg)
    assert "1 repeat heard" in bubble.repeats_badge.text()

    # Simulate multiple repeats heard
    out_msg.repeats_heard = 3
    storage.update_message_repeats("out_1", 3)
    chat.update_message(out_msg)
    assert "3 repeats heard" in bubble.repeats_badge.text()


def test_echo_message_filtered_from_chat(qapp, tmp_path):
    """Verifies that incoming echoes of our own node transmissions are filtered from chat display."""
    from meshcore_tray.config import AppConfig
    storage = Storage(tmp_path / "echo_test.db")
    config = AppConfig()
    config.meshcore.node_alias = "M7NCY_MC_MAPPER"

    out_msg = MessageEnvelope(
        id="out_sent_1",
        sender_name="M7NCY_MC_MAPPER",
        sender_id="!989afa62",
        channel="#test",
        text="testing a message",
        is_outgoing=True,
        repeats_heard=1
    )
    echo_msg = MessageEnvelope(
        id="echo_recv_1",
        sender_name="M7NCY_MC_MAPPER",
        sender_id="!M7NCY_MC_MAPPER",
        channel="#test",
        text="testing a message",
        is_outgoing=False
    )

    storage.save_message(out_msg)
    storage.save_message(echo_msg)

    chat = ChatWidget(storage=storage, config=config)
    chat.set_target("#test")

    bubbles = [
        chat.container_layout.itemAt(i).widget()
        for i in range(chat.container_layout.count())
        if isinstance(chat.container_layout.itemAt(i).widget(), MessageBubble)
    ]
    # Only the outgoing message bubble should be shown; echo is filtered
    assert len(bubbles) == 1
    assert bubbles[0].msg.id == "out_sent_1"
    assert bubbles[0].msg.is_outgoing is True

    # Try dynamically appending another incoming echo
    dynamic_echo = MessageEnvelope(
        id="echo_dyn_2",
        sender_name="M7NCY_MC_MAPPER",
        sender_id="!M7NCY_MC_MAPPER",
        channel="#test",
        text="another test message",
        is_outgoing=False
    )
    chat.add_message(dynamic_echo)

    bubbles_after = [
        chat.container_layout.itemAt(i).widget()
        for i in range(chat.container_layout.count())
        if isinstance(chat.container_layout.itemAt(i).widget(), MessageBubble)
    ]
    assert len(bubbles_after) == 1


def test_insert_break_opportunities():
    """Verifies that long strings, repeater routes, and unbroken tokens receive break opportunities."""
    from meshcore_tray.ui.chat_widget import insert_break_opportunities

    # Standard sentence should not have ZWSP injected between regular words
    normal = "Hello from Workington Cumbria"
    assert insert_break_opportunities(normal) == normal

    # Arrow-delimited mesh paths must receive break opportunities
    arrow_path = "fefebc→fc2a45→55e0a0→eda7d5"
    wrapped_arrow = insert_break_opportunities(arrow_path)
    assert "\u200b" in wrapped_arrow
    assert wrapped_arrow.replace("\u200b", "") == arrow_path

    # Greater-than delimited path chains
    gt_path = "80>17>95>90>98>e2"
    wrapped_gt = insert_break_opportunities(gt_path)
    assert "\u200b" in wrapped_gt
    assert wrapped_gt.replace("\u200b", "") == gt_path

    # Long continuous unbroken token without symbols (e.g. 64-character public key)
    pubkey = "f24cf620a27c4d4ba39a9bef865fbd1e10dddcedbef9db242e50fb45dde9457d"
    wrapped_pub = insert_break_opportunities(pubkey)
    assert "\u200b" in wrapped_pub
    assert len(wrapped_pub.split("\u200b")) >= 4
    assert wrapped_pub.replace("\u200b", "") == pubkey

    # Multiline text preservation
    multi = "Line 1\nPath: 80>17>95\nLine 3"
    wrapped_multi = insert_break_opportunities(multi)
    lines = wrapped_multi.split("\n")
    assert len(lines) == 3
    assert lines[0] == "Line 1"
    assert "\u200b" in lines[1]
    assert lines[2] == "Line 3"


def test_message_body_label_wrapping_and_sizing(qapp):
    """Verifies MessageBodyLabel wraps long unbroken strings and maintains a bounded minimum width."""
    from meshcore_tray.ui.chat_widget import MessageBodyLabel

    raw_text = "Received Dublin @[ANK 💳]: fefebc→fc2a45→55e0a0→eda7d5→0e9ef5→b2bb22→4a4fec (7 hops)"
    lbl = MessageBodyLabel(raw_text)

    # Minimum size hint width must not blow out to the full text width (~500px+)
    min_hint = lbl.minimumSizeHint()
    assert min_hint.width() <= 60

    # Test that height increases as available width shrinks (proper word wrapping)
    h_wide = lbl.heightForWidth(600)
    h_narrow = lbl.heightForWidth(300)
    assert h_narrow > h_wide, f"Narrow height ({h_narrow}) should be greater than wide height ({h_wide})"


def test_chat_bubble_long_string_wrapping_in_chat_widget(qapp, tmp_path):
    """Verifies that ChatWidget container does not blow out its width when containing very long unbroken path strings."""
    storage = Storage(tmp_path / "wrapping_chat_test.db")

    # Message with 95-character unbroken repeater chain
    long_path_msg = MessageEnvelope(
        id="long_path_1",
        channel="#test",
        sender_name="🤖",
        sender_id="bot1",
        text="@[BenFLX]\nHello from WORKINGTON Cumbria\n!test\nHops: 30\nPath: 80>17>95>90>98>e2>5c>4b>be>7c>ad>9b>74>91>82>ab>f2>06>3a>75>72>e4>5b>ec>71>6b>a9>19>3f>ca\nTim",
        timestamp="2026-09-05T20:00:00Z"
    )
    # Message with unicode arrows and no spaces
    arrow_msg = MessageEnvelope(
        id="arrow_msg_2",
        channel="#test",
        sender_name="bc57",
        sender_id="bc57_id",
        text="Received Dublin @[ANK 💳]: fefebc→fc2a45→55e0a0→eda7d5→0e9ef5→b2bb22→4a4fec (7 hops)",
        timestamp="2026-09-05T20:01:00Z"
    )
    storage.save_message(long_path_msg)
    storage.save_message(arrow_msg)

    chat = ChatWidget(storage=storage)
    chat.set_target("#test")
    chat.resize(400, 600)
    chat.show()
    qapp.processEvents()

    # Container minimum width must be bounded (less than 400px viewport)
    container_min_w = chat.container.minimumSizeHint().width()
    assert container_min_w < 400, f"Container min width {container_min_w} should not exceed viewport width 400"

    # MessageBubble minimum size hint must also be bounded
    bubble = chat._bubbles["arrow_msg_2"]
    assert bubble.minimumSizeHint().width() <= 80

    # Body label inside bubble must be multi-line wrapped at 350px width
    body_lbl = bubble.body_lbl
    assert body_lbl.heightForWidth(350) > body_lbl.heightForWidth(600)


def test_message_body_label_copy_sanitizes_zwsp(qapp):
    """Verifies that copying text from MessageBodyLabel sanitizes zero-width spaces."""
    from meshcore_tray.ui.chat_widget import MessageBodyLabel
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import Qt

    raw_text = "fefebc→fc2a45→55e0a0"
    lbl = MessageBodyLabel(raw_text)
    assert "\u200b" in lbl.text()
    assert lbl.raw_text() == raw_text

    # Mock selection
    lbl.selectedText = lambda: lbl.text()
    # Trigger Ctrl+C key event
    key_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    lbl.keyPressEvent(key_event)

    clip = qapp.clipboard()
    assert "\u200b" not in clip.text()
    assert clip.text() == raw_text


def test_message_bubble_copy_menu_sanitizes_zwsp(qapp):
    """Verifies that MessageBubble copy action copies clean text without zero-width spaces."""
    msg = MessageEnvelope(
        id="copy_test_1",
        channel="#test",
        sender_name="bc57",
        text="Received Dublin @[ANK 💳]: fefebc→fc2a45→55e0a0",
        timestamp="2026-09-05T20:00:00Z"
    )
    bubble = MessageBubble(msg)
    clip = qapp.clipboard()

    # Simulate copy message action
    text_to_copy = html.unescape(bubble.msg.text)
    clip.setText(text_to_copy.replace("\u200b", ""))

    assert "\u200b" not in clip.text()
    assert clip.text() == "Received Dublin @[ANK 💳]: fefebc→fc2a45→55e0a0"


