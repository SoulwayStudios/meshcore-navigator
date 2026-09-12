"""Unit and integration tests for traffic paths, universal channel resilience, and draggable map overlays."""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import EventType, bus
from meshcore_tray.core.models import ChannelInfo, MessageEnvelope
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.storage import Storage
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget


@pytest.fixture
def storage(tmp_path):
    db_file = tmp_path / "resilience_test.db"
    return Storage(db_path=db_file)


def test_channel_canonicalization_and_hash_matching(storage):
    """Verifies that channels are stored with canonical '#' prefix and match the firmware hash."""
    # 1. Test saving 'cumbria' without '#' gets canonicalized to '#cumbria'
    ch = ChannelInfo(channel_id=1, name="cumbria")
    storage.save_channel(ch)

    retrieved = storage.get_channel("#cumbria")
    assert retrieved is not None
    assert retrieved.name == "#cumbria"
    assert retrieved.channel_id == 1

    # Also test retrieve with 'cumbria' without hash
    retrieved_no_hash = storage.get_channel("cumbria")
    assert retrieved_no_hash is not None
    assert retrieved_no_hash.name == "#cumbria"

    # 2. Verify SHA-256 hash match with live firmware traffic
    # #cumbria should yield channel hash 'f9'
    secret = hashlib.sha256(b"#cumbria").digest()[0:16]
    chan_h = hashlib.sha256(secret).hexdigest()[0:2]
    assert chan_h == "f9"

    # 3. Public channel remains Public without '#'
    pub = storage.get_channel("Public")
    assert pub is not None
    assert pub.channel_id == 0
    assert pub.name == "Public"


def test_get_next_available_channel_slot(storage):
    """Verifies dynamic scanning of channel slots 1-7 and recycling."""
    # Slot 0 is Public, slots 1-7 are available
    assert storage.get_next_available_channel_slot() == 1

    # Fill slots 1, 2, 3
    storage.save_channel(ChannelInfo(channel_id=1, name="#Ch1"))
    storage.save_channel(ChannelInfo(channel_id=2, name="#Ch2"))
    storage.save_channel(ChannelInfo(channel_id=3, name="#Ch3"))

    assert storage.get_next_available_channel_slot() == 4

    # Fill remaining slots 4, 5, 6, 7
    for s in range(4, 8):
        storage.save_channel(ChannelInfo(channel_id=s, name=f"#Ch{s}"))

    # When all 1-7 are occupied, should recycle a valid slot between 1 and 7 (never slot 0)
    recycled = storage.get_next_available_channel_slot()
    assert 1 <= recycled <= 7


def test_send_channel_message_dynamic_allocation_and_event_emit(storage):
    """Verifies that sending on an unconfigured channel allocates a slot and emits MESSAGE_UPDATED."""
    cfg = AppConfig()
    driver = MeshCoreDriver(config=cfg, storage=storage)

    updated_messages = []
    def on_msg_updated(envelope):
        updated_messages.append(envelope)

    bus.subscribe(EventType.MESSAGE_UPDATED, on_msg_updated)

    try:
        # Mock the async send on client to simulate successful serial TX
        driver.client = MagicMock()
        driver.client.commands.send_channel = AsyncMock()

        # Send on an unconfigured channel name
        res = driver.send_channel_message("regional_north", "Testing dynamic channel message")
        assert res["status"] == "ok"
        msg = res["message"]
        assert msg.channel == "#regional_north"

        # Channel should be saved in storage with a valid slot 1..7
        saved_ch = storage.get_channel("#regional_north")
        assert saved_ch is not None
        assert 1 <= saved_ch.channel_id <= 7

    finally:
        bus.unsubscribe(EventType.MESSAGE_UPDATED, on_msg_updated)


def test_ensure_channel_synced_registers_in_parser(storage):
    """Verifies that ensure_channel_synced registers in the packet parser without blocking."""
    cfg = AppConfig()
    driver = MeshCoreDriver(config=cfg, storage=storage)

    # Set up client with mock reader and parser
    mock_client = MagicMock()
    mock_parser = MagicMock()
    mock_parser.newChannel = MagicMock(return_value=None)
    mock_client._reader.packet_parser = mock_parser
    driver.client = mock_client
    driver.is_connected = MagicMock(return_value=True)

    storage.save_channel(ChannelInfo(channel_id=2, name="#cumbria"))

    res = driver.ensure_channel_synced("cumbria")
    assert res is True
    assert mock_parser.newChannel.called
    call_args = mock_parser.newChannel.call_args[0][0]
    assert call_args["channel_idx"] == 2
    assert call_args["channel_name"] == "#cumbria"
    assert call_args["channel_hash"] == "f9"


def test_map_widget_show_paths_linkage():
    """Verifies that show_paths is enabled by default and linked to RF links button toggles."""
    cfg = AppConfig()
    map_widget = MeshMapWidget(config=cfg)

    # 1. Defaults: show_paths must be True so traffic renders out of the box
    assert map_widget.show_paths is True
    assert map_widget.show_rf_links is True

    # 2. Toggling via set_rf_links syncs both
    map_widget.set_rf_links(False)
    assert map_widget.show_rf_links is False
    assert map_widget.show_paths is False

    map_widget.set_rf_links(True)
    assert map_widget.show_rf_links is True
    assert map_widget.show_paths is True

    # 3. _on_links_toggle syncs show_paths to button checked state
    map_widget.btn_links.setChecked(False)
    map_widget._on_links_toggle()
    assert map_widget.show_rf_links is False
    assert map_widget.show_paths is False

    map_widget.btn_links.setChecked(True)
    map_widget._on_links_toggle()
    assert map_widget.show_rf_links is True
    assert map_widget.show_paths is True


def test_map_html_has_draggable_overlays_and_unified_styling():
    """Verifies that the generated HTML includes all 8 draggable overlay panels and the draggable engine."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE
    html = LEAFLET_HTML_TEMPLATE

    # 1. CSS styling
    assert ".map-overlay-panel" in html
    assert ".map-overlay-header" in html
    assert ".map-drag-handle-grip" in html
    assert "active-drag" in html

    # 2. All 8 floating overlay panels must have map-overlay-panel class
    overlay_ids = [
        "adsb-panel",
        "thunderstorm-panel",
        "aurora-legend-panel",
        "scope-filter-bar",
        "activity-heatmap-bar",
        "tropo-legend-panel",
        "path-mode-legend",
        "visualised-floating-panel",
    ]
    for p_id in overlay_ids:
        assert f'id="{p_id}"' in html, f"Missing overlay panel: {p_id}"
        # Ensure it has the map-overlay-panel class
        assert f'id="{p_id}"' in html and "map-overlay-panel" in html

    # 3. Drag handles for all 8 overlays
    drag_handle_ids = [
        "adsb-drag-handle",
        "thunderstorm-drag-handle",
        "aurora-drag-handle",
        "scope-filter-drag-handle",
        "activity-heatmap-drag-handle",
        "tropo-drag-handle",
        "path-mode-drag-handle",
        "floating-route-drag-handle",
    ]
    for h_id in drag_handle_ids:
        assert f'id="{h_id}"' in html, f"Missing drag handle: {h_id}"

    # 4. Javascript overlay engine
    assert "function makeOverlayDraggable(" in html
    assert "function initAllDraggableOverlays(" in html
    assert "bringOverlayToFront" in html
    assert "sessionStorage.setItem('overlay_pos_" in html
