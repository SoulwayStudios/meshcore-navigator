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
    ch_ops = ChannelInfo(1, "#public-ops", False, True, "2026-09-01T12:00:00Z")  # More recent
    ch_test = ChannelInfo(2, "#public-test", False, False, "2026-09-01T08:00:00Z")

    temp_storage.save_channel(ch_pub)
    temp_storage.save_channel(ch_ops)
    temp_storage.save_channel(ch_test)

    ranked = temp_storage.get_channels_by_recent_activity("pu")
    assert len(ranked) == 3
    # #public-ops was most recent so it should be first
    assert ranked[0].name == "#public-ops"
    assert ranked[1].name == "Public"
    assert ranked[2].name == "#public-test"


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


def test_channel_read_state(temp_storage):
    # Initially None
    assert temp_storage.get_last_read("chan:Public") is None

    # Mark as read
    temp_storage.mark_as_read("chan:Public", "msg-123", "2026-09-03T12:00:00Z")

    read_info = temp_storage.get_last_read("chan:Public")
    assert read_info is not None
    assert read_info["last_read_msg_id"] == "msg-123"
    assert read_info["last_read_timestamp"] == "2026-09-03T12:00:00Z"

    # Update to newer message
    temp_storage.mark_as_read("chan:Public", "msg-456", "2026-09-03T14:30:00Z")
    updated = temp_storage.get_last_read("chan:Public")
    assert updated["last_read_msg_id"] == "msg-456"
    assert updated["last_read_timestamp"] == "2026-09-03T14:30:00Z"


def test_delete_message(temp_storage):
    msg = MessageEnvelope(id="m_del", sender_name="UserX", channel="Public", text="To be deleted")
    temp_storage.save_message(msg)
    assert len(temp_storage.get_messages("Public")) == 1

    temp_storage.delete_message("m_del")
    assert len(temp_storage.get_messages("Public")) == 0


def test_get_messages_latest_chronological_with_limit(temp_storage):
    """Verifies that when message history exceeds limit, get_messages returns the newest messages in ascending order."""
    for i in range(110):
        temp_storage.save_message(MessageEnvelope(
            id=f"msg-{i:03d}",
            sender_name=f"User{i}",
            channel="#test",
            text=f"Test message {i}",
            timestamp=f"2026-09-03T{i // 60:02d}:{i % 60:02d}:00Z",
            is_outgoing=False
        ))

    # Fetch with limit 100
    msgs = temp_storage.get_messages(channel="#test", limit=100)
    assert len(msgs) == 100
    # Must be the NEWEST 100 messages (index 10 through 109)
    assert msgs[0].id == "msg-010"
    assert msgs[-1].id == "msg-109"
    # Must be in chronological order
    assert msgs[0].timestamp < msgs[-1].timestamp


def test_channel_favorite_persistence_and_mark_as_read(temp_storage):
    temp_storage.set_channel_favorite("#test", True)
    ch = temp_storage.get_channel("#test")
    assert ch is not None
    assert ch.is_favorite is True

    # Un-favorite
    temp_storage.set_channel_favorite("#test", False)
    ch2 = temp_storage.get_channel("#test")
    assert ch2.is_favorite is False

    # Test unread count and mark_channel_as_read
    temp_storage.save_message(MessageEnvelope(
        id="m_unread_1",
        sender_name="Alice",
        channel="#test",
        text="Unread 1",
        timestamp="2026-09-04T10:00:00Z",
        is_outgoing=False
    ))
    temp_storage.save_message(MessageEnvelope(
        id="m_unread_2",
        sender_name="Bob",
        channel="#test",
        text="Unread 2",
        timestamp="2026-09-04T10:01:00Z",
        is_outgoing=False
    ))
    assert temp_storage.get_channel_unread_count("#test") == 2

    # Mark as read
    temp_storage.mark_channel_as_read("#test")
    assert temp_storage.get_channel_unread_count("#test") == 0


def test_contact_coordinates_preserved_on_bulk_sync(temp_storage):
    # 1. Contact is initially saved with known coordinates
    rep = NodeContact(
        node_id="dd2df7a117c6",
        alias="M7NCY West Yagi",
        latitude=54.65897,
        longitude=-3.4346,
        is_repeater=True,
        is_favorite=True
    )
    temp_storage.save_contact(rep)

    # Verify present in coordinate map list
    nodes = temp_storage.get_nodes_with_coordinates()
    assert any(n.alias == "M7NCY West Yagi" and abs(n.latitude - 54.65897) < 0.001 for n in nodes)

    # 2. Radio synchronizes contacts without GPS (None / 0.0)
    synced_rep = NodeContact(
        node_id="DD2DF7A117C6",  # Uppercase
        alias="M7NCY West Yagi",
        latitude=None,
        longitude=None,
        is_repeater=True,
        is_favorite=False
    )
    temp_storage.save_contacts_bulk([synced_rep])

    # 3. Coordinates and favorite flag must be preserved
    retrieved = temp_storage.get_contact("dd2df7a117c6")
    assert retrieved is not None
    assert retrieved.latitude == 54.65897
    assert retrieved.longitude == -3.4346
    assert retrieved.is_favorite is True

    # Nodes with coordinates still includes the repeater
    nodes2 = temp_storage.get_nodes_with_coordinates()
    assert any(n.alias == "M7NCY West Yagi" and abs(n.latitude - 54.65897) < 0.001 for n in nodes2)


def test_companion_orbitals_docking(temp_storage):
    from meshcore_tray.core.models import DockedCompanionInfo

    # Save a docked companion
    info = DockedCompanionInfo(
        node_id="!bob123",
        alias="Bob",
        repeater_id="rep_egremont",
        repeater_alias="@Egremont-1",
        channel="#test",
        snr=11.2,
        last_heard="2026-09-05T00:00:00Z"
    )
    temp_storage.save_docked_companion(info)

    assert temp_storage.get_docked_companion_count() == 1
    docked = temp_storage.get_docked_companions()
    assert "rep_egremont" in docked
    assert len(docked["rep_egremont"]) == 1
    assert docked["rep_egremont"][0]["alias"] == "Bob"
    # Also accessible via alias
    assert "Egremont-1" in docked

    # If the node later transmits through a different repeater, dynamic movement
    info2 = DockedCompanionInfo(
        node_id="!bob123",
        alias="Bob",
        repeater_id="rep_pellon",
        repeater_alias="@Pellon Solar",
        channel="Public",
        snr=8.5,
        last_heard="2026-09-05T00:05:00Z"
    )
    temp_storage.save_docked_companion(info2)
    assert temp_storage.get_docked_companion_count() == 1
    docked2 = temp_storage.get_docked_companions()
    assert "rep_egremont" not in docked2
    assert "rep_pellon" in docked2
    assert docked2["rep_pellon"][0]["alias"] == "Bob"

    # If the companion acquires GPS coordinates, undock automatically
    contact_gps = NodeContact(
        node_id="!bob123",
        alias="Bob",
        latitude=54.5,
        longitude=-3.2,
        is_repeater=False
    )
    temp_storage.save_contact(contact_gps)
    assert temp_storage.get_docked_companion_count() == 0


def test_backfill_docked_companions(tmp_path):
    db_file = tmp_path / "test_backfill.db"
    s = Storage(db_path=db_file)

    # Add a repeater contact
    rep = NodeContact(
        node_id="rep001",
        alias="M7NCY West Yagi",
        latitude=54.65897,
        longitude=-3.4346,
        is_repeater=True
    )
    s.save_contact(rep)

    # Add a GPS-less companion contact
    comp = NodeContact(
        node_id="comp001",
        alias="Freda",
        latitude=None,
        longitude=None,
        is_repeater=False
    )
    s.save_contact(comp)

    # Add a message sent by Freda via hop repeater
    msg = MessageEnvelope(
        id="msg-freda-1",
        sender_id="comp001",
        sender_name="Freda",
        channel="#general",
        text="Hello over mesh",
        metadata={
            "hop_nodes": ["@M7NCY West Yagi"],
            "snr": 9.4
        }
    )
    s.save_message(msg)

    # Re-trigger backfill
    with s._get_connection() as conn:
        cursor = conn.cursor()
        s._backfill_docked_companions(cursor)
        conn.commit()

    assert s.get_docked_companion_count() == 1
    docked = s.get_docked_companions()
    assert "rep001" in docked
    assert docked["rep001"][0]["alias"] == "Freda"
    assert docked["rep001"][0]["snr"] == 9.4
    assert docked["rep001"][0]["is_unknown_first_hop"] is False

    # Test unknown first hop fallback (hop 0 unknown, hop 1 known rep001)
    comp2 = NodeContact(
        node_id="comp002",
        alias="Bob",
        latitude=None,
        longitude=None,
        is_repeater=False
    )
    s.save_contact(comp2)
    msg2 = MessageEnvelope(
        id="msg-bob-1",
        sender_id="comp002",
        sender_name="Bob",
        channel="#general",
        text="Testing route fallback",
        metadata={
            "hop_nodes": ["<Unknown Repeater 123456>", "@M7NCY West Yagi"],
            "snr": 5.2
        }
    )
    s.save_message(msg2)

    with s._get_connection() as conn:
        cursor = conn.cursor()
        s._backfill_docked_companions(cursor)
        conn.commit()

    docked2 = s.get_docked_companions()
    bob_info = next(item for item in docked2["rep001"] if item["alias"] == "Bob")
    assert bob_info["is_unknown_first_hop"] is True
    assert bob_info["first_hop_alias"] == "<Unknown Repeater 123456>"

    # Test arbitrary node with no hops and non-M7NCY name is NOT docked to M7NCY
    comp3 = NodeContact(
        node_id="comp003",
        alias="Ricky V3",
        latitude=None,
        longitude=None,
        is_repeater=False
    )
    s.save_contact(comp3)
    msg3 = MessageEnvelope(
        id="msg-ricky-1",
        sender_id="comp003",
        sender_name="Ricky V3",
        channel="#general",
        text="Hotpot without hops",
        metadata={}
    )
    s.save_message(msg3)

    with s._get_connection() as conn:
        cursor = conn.cursor()
        s._backfill_docked_companions(cursor)
        conn.commit()

    docked3 = s.get_docked_companions()
    ricky_found = any(item["alias"] == "Ricky V3" for nodes in docked3.values() for item in nodes)
    assert not ricky_found, "Arbitrary node without hops should not be docked"


def test_is_valid_coordinate_rejects_null_island_and_ocean():
    from meshcore_tray.core.models import is_valid_coordinate

    # Legitimate locations
    assert is_valid_coordinate(54.65897, -3.4346) is True  # Cumbria UK
    assert is_valid_coordinate(53.92365, -9.37733) is True  # Mayo Ireland
    assert is_valid_coordinate(-33.8688, 151.2093) is True  # Sydney
    assert is_valid_coordinate(-0.1807, -78.4678) is True  # Quito Ecuador (equatorial)
    assert is_valid_coordinate(0.3476, 32.5825) is True   # Kampala Uganda (equatorial)

    # Invalid / corrupt coordinates
    assert is_valid_coordinate(None, -3.0) is False
    assert is_valid_coordinate(54.0, None) is False
    assert is_valid_coordinate(0.0, 0.0) is False  # Null Island
    assert is_valid_coordinate(0.000111, -2.743575) is False  # Equatorial Atlantic Ocean
    assert is_valid_coordinate(0.5, 2.0) is False  # Gulf of Guinea open ocean near Null Island
    assert is_valid_coordinate(95.0, 0.0) is False  # Out of range


def test_verify_and_sanitize_database_clears_ocean_coords_and_phantoms(tmp_path):
    from meshcore_tray.storage import Storage
    from meshcore_tray.core.models import NodeContact

    db_path = tmp_path / "sanitize_test.db"
    storage = Storage(db_path)

    # 1. Genuine repeater in Ireland
    genuine = NodeContact(
        node_id="f24cf620a27c",
        alias="noc-croaghmoyle-jlo",
        is_repeater=True,
        latitude=53.92365,
        longitude=-9.37733,
        public_key="f24cf620a27c4d4ba39a9bef865fbd1e10dddcedbef9db242e50fb45dde9457d",
        last_seen="2026-09-05T11:04:02+00:00"
    )
    storage.save_contact(genuine)

    # 2. Shifted corrupt phantom repeater with ocean coordinates & future date
    phantom = NodeContact(
        node_id="2cbc680de9b4",
        alias="noc-croaghmoyl",
        is_repeater=True,
        latitude=0.000111,
        longitude=-2.743575,
        public_key="2cbc680de9b4380093007ca345663e940003f24cf620a27c4d4ba39a9bef865f",
        last_seen="2027-08-22T01:22:45+00:00"
    )
    with storage._get_connection() as conn:
        conn.execute("""
            INSERT INTO contacts (node_id, alias, is_repeater, latitude, longitude, public_key, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (phantom.node_id, phantom.alias, 1, 0.000111, -2.743575, phantom.public_key, phantom.last_seen))
        conn.commit()

    # 3. Run verification & sanitization
    report = storage.verify_and_sanitize_database()
    assert report["integrity_ok"] is True
    assert report["phantom_nodes_removed"] >= 1
    assert report["corrupt_coords_cleared"] >= 1

    # Assert phantom is purged
    assert storage.get_contact("2cbc680de9b4") is None

    # Assert genuine contact is untouched
    saved_genuine = storage.get_contact("f24cf620a27c")
    assert saved_genuine is not None
    assert saved_genuine.alias == "noc-croaghmoyle-jlo"
    assert saved_genuine.latitude == 53.92365
    assert saved_genuine.longitude == -9.37733

    # Map nodes only returns genuine contact
    map_nodes = storage.get_nodes_with_coordinates()
    assert len(map_nodes) == 1
    assert map_nodes[0].node_id == "f24cf620a27c"


def test_backup_database_rotates_and_checkpoints(tmp_path):
    from meshcore_tray.storage import Storage

    db_path = tmp_path / "backup_test.db"
    storage = Storage(db_path)

    # Perform backup
    backup_path = storage.backup_database(reason="test_close")
    assert backup_path is not None
    assert backup_path.exists()
    assert "test_close" in backup_path.name

    # Bak file exists
    bak_file = tmp_path / "meshcore_tray.db.bak"
    assert bak_file.exists()

    # Test rotation
    for i in range(12):
        storage.backup_database(reason=f"rot_{i}")

    backup_dir = tmp_path / "backups"
    backups = list(backup_dir.glob("meshcore_tray_*.db"))
    assert len(backups) <= 10



