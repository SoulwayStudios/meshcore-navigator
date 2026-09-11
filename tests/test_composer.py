"""Unit tests for PowerComposer widget and command parsing."""

import sys
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from meshcore_tray.ui.composer import PowerComposer
from meshcore_tray.storage import Storage


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def composer(qapp, tmp_path):
    storage = Storage(db_path=tmp_path / "composer_test.db")
    comp = PowerComposer(storage=storage)
    return comp


def test_composer_quick_channel_routing(composer):
    sent_events = []

    def on_send(channel, recipient, text):
        sent_events.append((channel, recipient, text))

    composer.send_message.connect(on_send)

    # Type '#ops Signal check 123'
    composer.input_field.setText("#ops Signal check 123")
    composer._handle_submit()

    assert len(sent_events) == 1
    assert sent_events[0] == ("ops", None, "Signal check 123")
    assert composer.input_field.text() == ""


def test_composer_quick_dm_routing(composer):
    sent_events = []

    def on_send(channel, recipient, text):
        sent_events.append((channel, recipient, text))

    composer.send_message.connect(on_send)

    # Type '/dm @Dave Are you on frequency?'
    composer.input_field.setText("/dm @Dave Are you on frequency?")
    composer._handle_submit()

    assert len(sent_events) == 1
    assert sent_events[0] == ("DM", "Dave", "Are you on frequency?")


def test_composer_in_channel_reply_public(composer):
    sent_events = []

    def on_send(channel, recipient, text):
        sent_events.append((channel, recipient, text))

    composer.send_message.connect(on_send)

    # In public channel, typing '@Bob got your message' must be public in the channel!
    composer.set_active_target("Public", None)
    composer.input_field.setText("@Bob got your message")
    composer._handle_submit()

    assert len(sent_events) == 1
    assert sent_events[0] == ("Public", None, "@Bob got your message")


def test_composer_search_trigger(composer):
    search_queries = []

    def on_search(q):
        search_queries.append(q)

    composer.search_query.connect(on_search)

    # Type '? emergency'
    composer.input_field.setText("? emergency")

    assert len(search_queries) > 0
    assert search_queries[-1] == "emergency"


def test_composer_slash_channel_switch(composer):
    switches = []

    def on_switch(chan):
        switches.append(chan)

    composer.switch_channel.connect(on_switch)

    # Type '/public'
    composer.input_field.setText("/public")
    composer._handle_submit()

    assert len(switches) == 1
    assert switches[0] == "Public"


def test_composer_slash_join_channel(composer):
    joins = []

    def on_join(chan):
        joins.append(chan)

    composer.join_channel.connect(on_join)

    # Test /join #cumbria
    composer.input_field.setText("/join #cumbria")
    composer._handle_submit()

    assert len(joins) == 1
    assert joins[0] == "#cumbria"
    assert composer.input_field.text() == ""

    # Test /join #test
    composer.input_field.setText("/join #test")
    composer._handle_submit()

    assert len(joins) == 2
    assert joins[1] == "#test"
    assert composer.input_field.text() == ""


def test_composer_typing_does_not_flicker_or_emit_search_cleared(composer):
    cleared_events = []
    search_queries = []

    composer.search_cleared.connect(lambda: cleared_events.append(True))
    composer.search_query.connect(lambda q: search_queries.append(q))

    # Typing normal text should NOT emit search_cleared
    composer.input_field.setText("H")
    composer.input_field.setText("Hello")
    composer.input_field.setText("Hello world")
    assert len(cleared_events) == 0

    # Deleting characters should NOT emit search_cleared
    composer.input_field.setText("Hello worl")
    composer.input_field.setText("Hello")
    composer.input_field.setText("")
    assert len(cleared_events) == 0

    # Activating search mode emits query
    composer.input_field.setText("? test")
    assert len(search_queries) > 0
    assert len(cleared_events) == 0

    # Clearing search mode SHOULD emit search_cleared exactly once
    composer.input_field.setText("")
    assert len(cleared_events) == 1

    # Typing again after search cleared should not emit further cleared events
    composer.input_field.setText("Back to normal")
    assert len(cleared_events) == 1
