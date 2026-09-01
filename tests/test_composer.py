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

    # Type '@Alice Are you on frequency?'
    composer.input_field.setText("@Alice Are you on frequency?")
    composer._handle_submit()

    assert len(sent_events) == 1
    assert sent_events[0] == ("DM", "Alice", "Are you on frequency?")


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
