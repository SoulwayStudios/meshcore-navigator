"""Unit tests for watched words and node mention detection."""

import pytest
from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.core.mention_detector import MentionDetector


def test_watched_keyword_detection():
    config = AppConfig()
    config.notifications.watched_keywords = ["emergency", "weather", "repeater"]
    detector = MentionDetector(config=config)

    # 1. Message containing watched keyword
    msg1 = MessageEnvelope(id="m1", sender_name="Alice", channel="Public", text="This is an emergency alert!")
    is_mention, keywords = detector.evaluate_message(msg1)
    assert is_mention is True
    assert "emergency" in keywords

    # 2. Normal message without keywords
    msg2 = MessageEnvelope(id="m2", sender_name="Bob", channel="Public", text="Good morning everyone.")
    is_mention, keywords = detector.evaluate_message(msg2)
    assert is_mention is False
    assert len(keywords) == 0


def test_node_mention_detection():
    config = AppConfig()
    config.meshcore.node_alias = "MyHeltec"
    config.meshcore.node_id = "!4a2f8b1c"
    config.notifications.notify_on_node_mentions = True
    detector = MentionDetector(config=config)

    # Message mentioning @MyHeltec
    msg = MessageEnvelope(id="m3", sender_name="Alice", channel="Public", text="Hey @MyHeltec do you have battery status?")
    is_mention, keywords = detector.evaluate_message(msg)
    assert is_mention is True
    assert "@MyHeltec" in keywords
