"""Integration test verifying full flow: Mock Radio -> EventBus -> Storage -> Pixoo -> Gateway."""

import time
import pytest
from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.gateway import GatewayManager
from meshcore_tray.core.mention_detector import MentionDetector
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.pixoo.pixoo_service import PixooService
from meshcore_tray.storage import Storage


def test_full_packet_flow(tmp_path):
    config = AppConfig()
    config.notifications.watched_keywords = ["emergency", "repeater"]
    storage = Storage(db_path=tmp_path / "integration.db")

    detector = MentionDetector(config=config)
    bus.subscribe(EventType.MESSAGE_RECEIVED, detector.evaluate_message)

    driver = MockRadioDriver(config=config, storage=storage)
    pixoo = PixooService(config=config)
    gateway = GatewayManager(config=config, storage=storage, radio_driver=driver)

    # 1. Inject an emergency message
    msg = driver.inject_incoming_message(
        sender_name="Alice",
        channel="Public",
        text="⚠️ Mountain pass repeater emergency alert!",
        is_favorite=True
    )

    # Verify message was evaluated and flagged as mention/keyword alert
    assert msg.is_mention is True
    assert "emergency" in msg.matched_keywords or "repeater" in msg.matched_keywords

    # Verify message was persisted to SQLite
    stored_msgs = storage.get_messages(channel="Public")
    assert len(stored_msgs) == 1
    assert stored_msgs[0].sender_name == "Alice"
    assert stored_msgs[0].is_favorite is True

    # Verify Pixoo renderer triggered green strobe alert and grouped message
    assert pixoo.renderer.is_flashing is True
    assert len(pixoo.renderer.chat_groups) == 1
    assert pixoo.renderer.chat_groups[0].sender_name == "Alice"

    # 2. Test Gateway execution to send message
    res = gateway.execute_command(
        type("Cmd", (), {
            "command_type": "SEND_MSG",
            "payload": {"channel": "Public", "text": "Copy Alice, emergency team notified."}
        })()
    )
    assert res["status"] == "ok"
    assert len(storage.get_messages(channel="Public")) == 2

    # 3. Test Full-Text Search via storage (both messages contain 'emergency')
    search_results = storage.search_messages("emergency")
    assert len(search_results) == 2
    assert search_results[1].sender_name == "Alice"

    # Search for 'repeater' which is only in Alice's message
    rep_results = storage.search_messages("repeater")
    assert len(rep_results) == 1
    assert rep_results[0].sender_name == "Alice"
