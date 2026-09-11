"""Unit tests for Contact Discovery, Manual Contact Management, and Peak Presets."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from meshcore_tray.core.models import MessageEnvelope, NeighbourInfo, NodeContact, PacketPathInfo
from meshcore_tray.storage import Storage
from meshcore_tray.ui.contact_dialog import (
    CUMBRIA_PEAK_PRESETS,
    ContactDiscoveryDialog,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def test_storage(tmp_path: Path):
    db_file = tmp_path / "test_contacts.db"
    return Storage(db_file)


def test_storage_get_discovered_nodes(test_storage: Storage):
    """Verifies that unlinked packet hops, neighbours, and message senders are surfaced as discovered candidates."""
    # 1. Existing known contact
    known = NodeContact(
        node_id="known1234567",
        alias="Known Repeater",
        public_key="known1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        latitude=54.5,
        longitude=-3.0,
        is_repeater=True,
    )
    test_storage.save_contact(known)

    # 2. Packet path containing unknown repeaters
    path = PacketPathInfo(
        packet_id="pkt-1",
        sender_id="sender1",
        sender_name="Sender",
        recipient_id="mesh",
        timestamp="2026-09-04T12:00:00Z",
        hop_nodes=["<Unknown Repeater ab12cd>", "@Known Repeater", "<Unknown Repeater ef3456>"],
        hop_snrs=[10.0, 8.0, 6.0],
        route_type="FLOOD",
        coordinates=[[54.5, -3.0]],
    )
    test_storage.save_packet_path(path)

    # 3. Neighbour not in contacts
    neighbour = NeighbourInfo(
        node_id="neigh998877",
        alias="Mountain Rep",
        snr_db=9.5,
        rssi_dbm=-78.0,
        last_heard_ts="2026-09-04T12:30:00Z",
        is_repeater=True,
        is_favorite=False,
        latitude=54.6,
        longitude=-3.1,
    )
    test_storage.save_neighbour(neighbour)

    # 4. Message sender not in contacts
    msg = MessageEnvelope(
        id="msg-1",
        timestamp="2026-09-04T12:45:00Z",
        source_driver="meshcore",
        sender_id="new_sender_42",
        sender_name="New Walker",
        is_favorite=False,
        channel="Public",
        text="Hello world",
    )
    test_storage.save_message(msg)

    # Query discovered nodes
    discovered = test_storage.get_discovered_nodes()
    assert len(discovered) >= 3

    # Check that known contact is NOT in discovered list
    discovered_ids = [d["node_id"].lower() for d in discovered]
    discovered_aliases = [d["alias"].lower() for d in discovered]
    assert "known1234567" not in discovered_ids
    assert "known repeater" not in discovered_aliases

    # Verify extracted unknown repeaters
    assert "ab12cd" in discovered_ids
    assert "ef3456" in discovered_ids

    # Verify neighbour is surfaced with GPS coordinates
    neigh_entry = next((d for d in discovered if d["node_id"] == "neigh998877"), None)
    assert neigh_entry is not None
    assert neigh_entry["alias"] == "Mountain Rep"
    assert neigh_entry["latitude"] == 54.6
    assert neigh_entry["longitude"] == -3.1


def test_contact_dialog_smart_peak_detection(qapp, test_storage: Storage):
    """Verifies that entering 'Skiddaw' detects the summit and allows applying coordinates."""
    dialog = ContactDiscoveryDialog(storage=test_storage, initial_name="")
    dialog.show()

    # Enter Skiddaw
    dialog.edit_name.setText("🌐 MCC Skiddaw Mk3")

    # Verify suggestion banner becomes visible
    assert dialog.suggestion_banner.isVisible()
    assert "Skiddaw" in dialog.lbl_suggestion.text()

    # Apply detected coordinates
    dialog._apply_detected_preset()
    assert dialog.edit_lat.text() == "54.6514"
    assert dialog.edit_lon.text() == "-3.1484"
    assert dialog.radio_repeater.isChecked()

    dialog.close()


def test_contact_dialog_manual_save(qapp, test_storage: Storage):
    """Verifies manual contact creation, deterministic key generation, and SQLite insertion."""
    mock_driver = MagicMock()
    mock_driver.is_connected.return_value = True

    dialog = ContactDiscoveryDialog(storage=test_storage, driver=mock_driver)
    dialog.show()

    dialog.edit_name.setText("🌐 MCC Skiddaw Mk3")
    dialog.edit_lat.setText("54.6514")
    dialog.edit_lon.setText("-3.1484")
    dialog.radio_repeater.setChecked(True)
    dialog.btn_fav.setChecked(True)

    with patch.object(dialog, "accept"):
        with patch("PyQt6.QtWidgets.QMessageBox.information"):
            dialog._save_manual_contact()

    # Verify saved in SQLite storage
    saved = test_storage.get_contact("skiddaw")
    assert saved is not None
    assert saved.alias == "🌐 MCC Skiddaw Mk3"
    assert saved.latitude == 54.6514
    assert saved.longitude == -3.1484
    assert saved.is_repeater is True
    assert saved.is_favorite is True
    assert len(saved.public_key) == 64

    # Verify driver push was called
    assert mock_driver.add_or_update_contact.called

    dialog.close()


def test_contact_dialog_card_import(qapp, test_storage: Storage):
    """Verifies importing contact details from URI and JSON card formats."""
    dialog = ContactDiscoveryDialog(storage=test_storage)
    dialog.show()

    # 1. URI format
    uri = "meshcore://contact?name=Helvellyn%20Rep&key=a1b2c3d4e5f611223344556677889900aabbccddeeff00112233445566778899&lat=54.5270&lon=-3.0175"
    dialog.edit_import_uri.setText(uri)

    with patch("PyQt6.QtWidgets.QMessageBox.information"):
        dialog._import_contact_card()

    assert dialog.edit_name.text() == "Helvellyn Rep"
    assert dialog.edit_lat.text() == "54.5270"
    assert dialog.edit_lon.text() == "-3.0175"
    assert "a1b2c3d4" in dialog.edit_pubkey.text()

    # 2. JSON card format
    json_card = json.dumps({
        "adv_name": "Blake Fell Node",
        "public_key": "1234567890abcdef" * 4,
        "adv_lat": 54.5684,
        "adv_lon": -3.4076,
        "type": 2
    })
    dialog.edit_import_uri.setText(json_card)

    with patch("PyQt6.QtWidgets.QMessageBox.information"):
        dialog._import_contact_card()

    assert dialog.edit_name.text() == "Blake Fell Node"
    assert dialog.edit_lat.text() == "54.5684"
    assert dialog.edit_lon.text() == "-3.4076"
    assert dialog.radio_repeater.isChecked()

    dialog.close()


def test_alias_and_node_id_validation():
    """Verifies that is_valid_alias and is_valid_node_id correctly reject corrupted OTA byte sequences."""
    from meshcore_tray.core.models import is_valid_alias, is_valid_node_id

    # Legitimate aliases
    assert is_valid_alias("M7NCY West Yagi")
    assert is_valid_alias("🍎 Homebase")
    assert is_valid_alias("🐸mossbay madhouse Re")
    assert is_valid_alias("Ben 🇮🇪")
    assert is_valid_alias("Bodffordd Obs")

    # Corrupted binary / OTA noise aliases
    assert not is_valid_alias("\x01\n>\x01\n>\x01\n>\x01\n>\x01\n>/4%\x12,")
    assert not is_valid_alias("rԺ\rr>\x01\n>\x01\n>\x01\n>\x01\n>\x01\n")
    assert not is_valid_alias("\x03bwRi&Js<zQѶ6=U\x13")
    assert not is_valid_alias("j/ץd\x7f\\y@\x1c\x16qv\x13")
    assert not is_valid_alias("/\x1cL$m\x180'~a#A\x06\x1cW\x02")
    assert not is_valid_alias("> > > > >")
    assert not is_valid_alias("")
    assert not is_valid_alias(None)

    # Node ID validation
    assert is_valid_node_id("6ec4553b4a55")
    assert is_valid_node_id("!m7ncy")
    assert not is_valid_node_id("000000000000")
    assert not is_valid_node_id("")
    assert not is_valid_node_id(None)
    assert not is_valid_node_id("\x00\x01\x02")


def test_corrupt_contacts_rejected_from_map_and_storage(test_storage: Storage):
    """Verifies that corrupted contact payloads do not overwrite valid nodes or get plotted on map."""
    # 1. Save legitimate contact
    valid_node = NodeContact(
        node_id="6ec4553b4a55",
        alias="Bodffordd Obs",
        latitude=53.26558,
        longitude=-4.35804,
        is_repeater=True,
        out_path_len=-1
    )
    test_storage.save_contact(valid_node)

    # 2. Attempt to save corrupted OTA payload for the same node
    corrupt_node = NodeContact(
        node_id="6ec4553b4a55",
        alias="\x01\n>\x01\n>\x01\n>\x01\n>\x01\n>/4%\x12,",
        latitude=53.26558,
        longitude=-44.282478,
        is_repeater=True,
        out_path_len=62
    )
    test_storage.save_contact(corrupt_node)

    # Alias must NOT be overwritten with corrupt garbage, and out_path_len must be capped
    saved = test_storage.get_contact("6ec4553b4a55")
    assert saved.alias == "Bodffordd Obs"
    assert saved.out_path_len <= 16

    # 3. Attempt to save all-zero node
    zero_node = NodeContact(
        node_id="000000000000",
        alias="/\x1cL$m\x180'~a#A\x06\x1cW\x02",
        latitude=10.0,
        longitude=20.0
    )
    test_storage.save_contact(zero_node)
    assert test_storage.get_contact("000000000000") is None

    # 4. Map coords query must never return corrupt aliases or all-zero nodes
    map_nodes = test_storage.get_nodes_with_coordinates()
    map_ids = [m.node_id for m in map_nodes]
    assert "000000000000" not in map_ids
    assert all(m.alias == "Bodffordd Obs" for m in map_nodes if m.node_id == "6ec4553b4a55")

