"""Tests for Heltec V3 hardware contact pruning and New Nodes gold highlight map view."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import NodeContact
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.storage import Storage
from meshcore_tray.ui.nav_dock import MapLayerDockWidget, LayerButton, create_layer_icon, LAYER_SVGS
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, LEAFLET_HTML_TEMPLATE


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_pruning.db"
    return Storage(db_path=db_file)


# ==============================================================================
# 1. Database Schema & first_seen Persistence
# ==============================================================================

def test_first_seen_persistence_and_immutability(temp_storage):
    """Verifies that first_seen is saved on discovery and permanently preserved on updates."""
    t0 = "2026-09-10T12:00:00+00:00"
    c1 = NodeContact(
        node_id="!11223344",
        alias="CumbriaPeak",
        last_seen=t0,
        first_seen=t0,
        latitude=54.5,
        longitude=-3.2
    )
    temp_storage.save_contact(c1)

    loaded = temp_storage.get_contact("!11223344")
    assert loaded is not None
    assert loaded.first_seen == t0
    assert loaded.last_seen == t0

    # Advance last_seen by 5 days; first_seen MUST remain untouched at t0
    t1 = "2026-09-15T18:30:00+00:00"
    c1_updated = NodeContact(
        node_id="!11223344",
        alias="CumbriaPeak-Updated",
        last_seen=t1,
        first_seen="",  # Simulates an advert without explicit first_seen
        latitude=54.5,
        longitude=-3.2
    )
    temp_storage.save_contact(c1_updated)

    loaded_after = temp_storage.get_contact("!11223344")
    assert loaded_after.last_seen == t1
    assert loaded_after.first_seen == t0  # Preserved!


def test_first_seen_bulk_insert_preservation(temp_storage):
    """Verifies save_contacts_bulk records first_seen and preserves it across subsequent bulk calls."""
    t0 = "2026-09-01T08:00:00+00:00"
    contacts = [
        NodeContact(node_id="!aabbcc01", alias="Node-1", last_seen=t0, first_seen=t0),
        NodeContact(node_id="!aabbcc02", alias="Node-2", last_seen=t0, first_seen=t0),
    ]
    temp_storage.save_contacts_bulk(contacts)

    c1 = temp_storage.get_contact("!aabbcc01")
    assert c1.first_seen == t0

    # Second bulk sync with later last_seen
    t2 = "2026-09-18T10:00:00+00:00"
    contacts_update = [
        NodeContact(node_id="!aabbcc01", alias="Node-1", last_seen=t2, first_seen=t2),
    ]
    temp_storage.save_contacts_bulk(contacts_update)

    c1_after = temp_storage.get_contact("!aabbcc01")
    assert c1_after.last_seen == t2
    assert c1_after.first_seen == t0


# ==============================================================================
# 2. Heltec V3 Hardware Contact Pruning Engine & Local Database Safety
# ==============================================================================

@pytest.mark.asyncio
async def test_hardware_pruning_removes_from_radio_but_preserves_local_db(temp_storage):
    """CRITICAL SAFETY TEST:
    Verifies that pruning strictly calls remove_contact on the physical radio
    and NEVER deletes the contact from the local SQLite storage or app map."""
    config = AppConfig()
    config.meshcore.hardware_contact_limit = 64
    config.meshcore.hardware_prune_threshold = 50
    config.meshcore.hardware_prune_target_free = 15

    driver = MeshCoreDriver(config=config, storage=temp_storage)
    driver._connected = True
    driver.client = MagicMock()

    # Seed 3 contacts in local SQLite storage
    pk_stale = "1111111111111111111111111111111111111111111111111111111111111111"
    pk_fav = "2222222222222222222222222222222222222222222222222222222222222222"
    pk_rep = "3333333333333333333333333333333333333333333333333333333333333333"

    old_ts = int((datetime.now(timezone.utc) - timedelta(days=10)).timestamp())

    temp_storage.save_contact(NodeContact(node_id=pk_stale[:12], alias="StaleNode", last_seen="2026-09-01T00:00:00Z"))
    temp_storage.save_contact(NodeContact(node_id=pk_fav[:12], alias="FavNode", is_favorite=True))
    temp_storage.save_contact(NodeContact(node_id=pk_rep[:12], alias="RepNode", is_repeater=True))

    # Mock get_contacts response from the radio hardware returning 3 entries
    mock_event = MagicMock()
    mock_event.type = "contacts"
    mock_event.payload = {
        pk_stale: {"public_key": pk_stale, "adv_name": "StaleNode", "last_advert": old_ts, "type": 0},
        pk_fav: {"public_key": pk_fav, "adv_name": "FavNode", "last_advert": old_ts, "type": 0},
        pk_rep: {"public_key": pk_rep, "adv_name": "RepNode", "last_advert": old_ts, "type": 2},
    }
    driver.client.commands.get_contacts = AsyncMock(return_value=mock_event)

    # Mock remove_contact on client.commands
    mock_remove_res = MagicMock()
    mock_remove_res.type = "ok"
    driver.client.commands.remove_contact = AsyncMock(return_value=mock_remove_res)

    pruned = await driver.prune_hardware_contacts(target_free_slots=1)

    # Only the stale non-favorite, non-repeater contact should have been pruned from radio hardware
    assert pruned == 1
    driver.client.commands.remove_contact.assert_called_once_with(pk_stale)

    # VERIFY LOCAL DATABASE INTEGRITY:
    # None of the contacts were deleted from the desktop app database!
    all_stored = temp_storage.get_contacts()
    stored_ids = [c.node_id for c in all_stored]
    assert pk_stale[:12] in stored_ids
    assert pk_fav[:12] in stored_ids
    assert pk_rep[:12] in stored_ids


@pytest.mark.asyncio
async def test_hardware_pruning_protects_recent_contacts(temp_storage):
    """Verifies that nodes active within the last 48 hours are never pruned from hardware."""
    config = AppConfig()
    driver = MeshCoreDriver(config=config, storage=temp_storage)
    driver._connected = True
    driver.client = MagicMock()

    pk_recent = "4444444444444444444444444444444444444444444444444444444444444444"
    # Active 2 hours ago
    recent_ts = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())

    mock_event = MagicMock()
    mock_event.type = "contacts"
    mock_event.payload = {
        pk_recent: {"public_key": pk_recent, "adv_name": "ActiveRecentNode", "last_advert": recent_ts, "type": 0}
    }
    driver.client.commands.get_contacts = AsyncMock(return_value=mock_event)
    driver.client.commands.remove_contact = AsyncMock()

    pruned = await driver.prune_hardware_contacts(target_free_slots=5)
    assert pruned == 0
    driver.client.commands.remove_contact.assert_not_called()


# ==============================================================================
# 3. "New Nodes" Discovery Map View & Nav Dock
# ==============================================================================

def test_nav_dock_new_nodes_button_and_icon(qapp):
    """Verifies the new_nodes button in MapLayerDockWidget and its SVG rendering."""
    assert "new_nodes" in LAYER_SVGS

    # Test SVG icon generation in inactive and active states
    icon_inactive = create_layer_icon("new_nodes", active=False)
    icon_active = create_layer_icon("new_nodes", active=True)
    assert icon_inactive is not None
    assert icon_active is not None

    dock = MapLayerDockWidget()
    assert hasattr(dock, "btn_new_nodes")
    assert "Newly Discovered Nodes" in dock.btn_new_nodes.toolTip()
    assert "Waving Hand" in dock.btn_new_nodes.toolTip()

    signals_received = []
    dock.layer_toggled.connect(lambda k, v: signals_received.append((k, v)))

    dock.btn_new_nodes.setChecked(True)
    assert ("new_nodes", True) in signals_received

    dock.set_layer_active("new_nodes", False)
    assert dock.btn_new_nodes.isChecked() is False


def test_mesh_map_new_nodes_panel_and_leaflet_html(qapp, temp_storage):
    """Verifies that the Leaflet map HTML includes the New Nodes floating panel,
    waving hand icon, mark all known button, and Discord grey styling."""
    from meshcore_tray.ui.mesh_map_widget import get_leaflet_html
    html = get_leaflet_html()
    assert 'id="new-nodes-panel"' in html
    assert '👋 NEW NODES DISCOVERY' in html
    assert 'markAllNodesKnown()' in html
    assert 'id="nn-btn-24h"' in html
    assert 'id="nn-btn-72h"' in html
    assert 'id="nn-btn-168h"' in html
    assert 'id="nn-btn-336h"' in html
    assert 'setNewNodes(' in html
    assert 'setNewNodesTimeframe(' in html
    assert '#FFD700' in html  # Radiant gold
    assert '.new-nodes-panel' in html
    # Discord grey styling present
    assert 'rgba(30, 31, 34, 0.96)' in html
    assert '#1E1F22' in html
    assert '#383A40' in html


def test_mark_all_contacts_as_known_and_baseline_storage(temp_storage):
    """Verifies that mark_all_contacts_as_known backdates existing contacts to 2024-01-01 baseline,
    and subsequent new contacts receive a timestamp >= current time."""
    # Seed 2 contacts with current timestamps
    c1 = NodeContact(node_id="!001122", alias="CurrentNode1", last_seen="2026-09-19T12:00:00Z")
    c2 = NodeContact(node_id="!003344", alias="CurrentNode2", last_seen="2026-09-19T12:00:00Z")
    temp_storage.save_contact(c1)
    temp_storage.save_contact(c2)

    assert temp_storage.get_contact("!001122").first_seen != "2024-01-01T00:00:00+00:00"

    # Mark all currently discovered nodes as known
    count = temp_storage.mark_all_contacts_as_known()
    assert count == 2
    assert temp_storage.get_contact("!001122").first_seen == "2024-01-01T00:00:00+00:00"
    assert temp_storage.get_contact("!003344").first_seen == "2024-01-01T00:00:00+00:00"

    # Save a brand new contact discovered from now
    c_new = NodeContact(node_id="!999999", alias="BrandNewNode")
    temp_storage.save_contact(c_new)
    loaded_new = temp_storage.get_contact("!999999")
    assert loaded_new.first_seen.startswith("2026-")  # Discovered from now!


def test_activity_timeline_widget_purple_bars_and_discord_grey(qapp):
    """Verifies that NetworkActivityTimelineWidget uses purple data bars and Discord dark background."""
    from meshcore_tray.ui.activity_timeline_widget import (
        NetworkActivityTimelineWidget,
        BAR_COLOR,
        BAR_HOVER_COLOR,
        DARK_BG,
    )
    # Check signature purple #AA55FF (R=170, G=85, B=255)
    assert BAR_COLOR.red() == 170
    assert BAR_COLOR.green() == 85
    assert BAR_COLOR.blue() == 255

    # Check hover lavender #C084FC
    assert BAR_HOVER_COLOR.red() == 192
    assert BAR_HOVER_COLOR.green() == 132
    assert BAR_HOVER_COLOR.blue() == 252

    # Check recessed background is Discord dark #111214
    assert DARK_BG.name().upper() == "#111214"

    widget = NetworkActivityTimelineWidget()
    assert "#1E1F22" in widget.styleSheet()
    assert "#383A40" in widget.styleSheet()
    assert "#AA55FF" in widget.styleSheet()


def test_mesh_map_widget_set_new_nodes(qapp, temp_storage):
    """Verifies set_new_nodes method on MeshMapWidget."""
    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=temp_storage, config=config)

    map_widget.run_js = MagicMock()
    map_widget.set_new_nodes(True, timeframe_hours=168)
    assert map_widget.new_nodes_active is True
    assert map_widget.new_nodes_timeframe_hours == 168
    map_widget.run_js.assert_called_with("setNewNodes(true, 168);")

    map_widget.set_new_nodes(False)
    assert map_widget.new_nodes_active is False
    map_widget.run_js.assert_called_with("setNewNodes(false, 168);")


def test_settings_widget_hardware_pruning_controls(qapp, temp_storage):
    """Verifies that SettingsWidget contains the hardware capacity label, auto-prune checkbox, and manual prune button."""
    from meshcore_tray.ui.settings_widget import SettingsWidget
    config = AppConfig()
    config.meshcore.hardware_contact_limit = 64
    config.meshcore.auto_prune_hardware_contacts = True

    mock_driver = MagicMock()
    mock_driver.hardware_contacts_count = 42
    mock_driver.is_connected.return_value = True

    widget = SettingsWidget(config=config, storage=temp_storage, radio_driver=mock_driver)
    assert hasattr(widget, "chk_auto_prune_hardware")
    assert widget.chk_auto_prune_hardware.isChecked() is True

    assert hasattr(widget, "lbl_hw_capacity")
    assert "42 / 64 slots used" in widget.lbl_hw_capacity.text()

    assert hasattr(widget, "btn_prune_hw_contacts")
    assert "Prune Stale Contacts" in widget.btn_prune_hw_contacts.text()

    # Test event bus update of capacity label
    bus.emit(EventType.HARDWARE_CONTACTS_UPDATED, {"count": 35, "limit": 64})
    assert "35 / 64 slots used" in widget.lbl_hw_capacity.text()


def test_contact_dialog_hardware_prune_button(qapp, temp_storage):
    """Verifies that ContactDiscoveryDialog hardware tab has the prune button."""
    from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
    mock_driver = MagicMock()
    mock_driver.is_connected.return_value = True

    dialog = ContactDiscoveryDialog(storage=temp_storage, driver=mock_driver)
    # Check that _prune_hardware_contacts method exists and dialog initialized cleanly
    assert hasattr(dialog, "_prune_hardware_contacts")


def test_layer_button_new_nodes_icon_and_styling(qapp):
    """Verifies that the new_nodes LayerButton renders a crisp vector SVG icon and NEVER raw text."""
    btn = LayerButton("new_nodes", "Newly Discovered Nodes")
    assert btn.icon_name == "new_nodes"
    assert btn.text() == ""
    assert not btn.icon().isNull()

    # Active toggle: text remains empty, gold gradient applied
    btn.setChecked(True)
    assert btn.text() == ""
    assert not btn.icon().isNull()
    assert "#F59E0B" in btn.styleSheet()

    # Inactive toggle: text remains empty, standard dark style restored
    btn.setChecked(False)
    assert btn.text() == ""
    assert not btn.icon().isNull()
    assert "#24262B" in btn.styleSheet()


def test_map_marker_styling_border_cleanup():
    """Verifies that applyNodeMarkerStyling in LEAFLET_HTML_TEMPLATE explicitly strips border properties
    on non-new nodes and default reset, preventing persistent ghost outlines on map dots."""
    template = LEAFLET_HTML_TEMPLATE

    # Must strip border properties at the start of applyNodeMarkerStyling
    assert "dot.style.removeProperty('border');" in template
    assert "dot.style.removeProperty('border-color');" in template
    assert "dot.style.removeProperty('border-width');" in template
    assert "dot.style.removeProperty('border-style');" in template

    # Non-new nodes must NOT set an inline border
    assert "dot.style.setProperty('border', '1px solid #2B2D31'" not in template

