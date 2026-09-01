"""Unit tests for SQLite Storage, search, and activity ranking."""

import os
from pathlib import Path
import pytest
from meshcore_tray.core.models import MessageEnvelope, ChannelInfo, NodeContact, TelemetryEnvelope
from meshcore_tray.storage import Storage


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_meshcore.db"
    return Storage(db_path=db_file)


def test_save_and_retrieve_message(temp_storage):
    msg = MessageEnvelope(
        id="msg-test-1",
        sender_id="!8f3a",
        sender_name="Alice",
        channel="Public",
        text="Hello world from storage test",
        is_favorite=True
    )
    temp_storage.save_message(msg)

    messages = temp_storage.get_messages(channel="Public")
    assert len(messages) == 1
    assert messages[0].id == "msg-test-1"
    assert messages[0].sender_name == "Alice"
    assert messages[0].is_favorite is True


def test_full_text_search(temp_storage):
    msg1 = MessageEnvelope(id="m1", sender_name="Alice", channel="Public", text="Severe weather warning on peak")
    msg2 = MessageEnvelope(id="m2", sender_name="Bob", channel="#ops", text="Radio check all good")
    msg3 = MessageEnvelope(id="m3", sender_name="Charlie", channel="Public", text="Battery level low at station")

    temp_storage.save_message(msg1)
    temp_storage.save_message(msg2)
    temp_storage.save_message(msg3)

    # Search for 'weather'
    results = temp_storage.search_messages("weather")
    assert len(results) == 1
    assert results[0].id == "m1"

    # Search for 'radio'
    results = temp_storage.search_messages("radio")
    assert len(results) == 1
    assert results[0].id == "m2"

    # Search by sender name 'Alice'
    results = temp_storage.search_messages("Alice")
    assert len(results) == 1
    assert results[0].sender_name == "Alice"


def test_channel_activity_ranking_for_autocomplete(temp_storage):
    ch_pub = ChannelInfo(0, "Public", True, True, "2026-09-01T10:00:00Z")
    ch_ops = ChannelInfo(1, "public-ops", False, True, "2026-09-01T12:00:00Z")  # More recent
    ch_test = ChannelInfo(2, "public-test", False, False, "2026-09-01T08:00:00Z")

    temp_storage.save_channel(ch_pub)
    temp_storage.save_channel(ch_ops)
    temp_storage.save_channel(ch_test)

    ranked = temp_storage.get_channels_by_recent_activity("pu")
    assert len(ranked) == 3
    # public-ops was most recent so it should be first
    assert ranked[0].name == "public-ops"
    assert ranked[1].name == "Public"
    assert ranked[2].name == "public-test"


def test_seen_username_autocomplete_from_messages(temp_storage):
    # Simulate receiving messages from users never manually added as contacts
    msg1 = MessageEnvelope(id="m_u1", sender_name="David_LoRa", sender_id="!1a2b", channel="Public", text="Hello mesh")
    msg2 = MessageEnvelope(id="m_u2", sender_name="Emma_Node", sender_id="!3c4d", channel="Public", text="Signal test")
    temp_storage.save_message(msg1)
    temp_storage.save_message(msg2)

    # Autocomplete for '@' (all seen users)
    contacts = temp_storage.get_contacts_by_recent_activity("")
    aliases = [c.alias for c in contacts]
    assert "David_LoRa" in aliases
    assert "Emma_Node" in aliases

    # Autocomplete with prefix '@Dav'
    dav_contacts = temp_storage.get_contacts_by_recent_activity("Dav")
    assert len(dav_contacts) == 1
    assert dav_contacts[0].alias == "David_LoRa"
