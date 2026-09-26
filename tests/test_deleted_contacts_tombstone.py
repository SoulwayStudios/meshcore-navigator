import os
import tempfile
import pytest
from unittest.mock import AsyncMock, MagicMock

from meshcore_tray.storage import Storage
from meshcore_tray.core.models import NodeContact
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.core.event_bus import bus, EventType
from meshcore.events import Event, EventType as McEventType


def test_storage_deleted_contacts_tombstone():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)

        c = NodeContact(
            node_id="180967b7bd8c",
            alias="ac98 RPTR",
            is_repeater=True,
            latitude=54.4,
            longitude=6.2
        )
        storage.save_contact(c)
        assert len(storage.get_contacts()) == 1

        # Delete contact
        storage.delete_contact("180967b7bd8c")
        assert len(storage.get_contacts()) == 0
        assert storage.is_contact_deleted("180967b7bd8c")
        assert storage.is_contact_deleted("!180967b7bd8c")

        # Attempt to re-save via save_contact should be ignored
        storage.save_contact(c)
        assert len(storage.get_contacts()) == 0

        # Attempt to re-save via save_contacts_bulk should be ignored
        storage.save_contacts_bulk([c])
        assert len(storage.get_contacts()) == 0

        # Inverted coordinates test: even for a new node with ac98 and 54.4, 6.2, coords are sanitized
        bad_c = NodeContact(
            node_id="999988887777",
            alias="ac98 RPTR",
            is_repeater=True,
            latitude=54.4,
            longitude=6.2
        )
        storage.save_contact(bad_c)
        saved = storage.get_contact("999988887777")
        assert saved is not None
        assert saved.latitude is None
        assert saved.longitude is None
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.mark.asyncio
async def test_driver_contact_deleted_removes_from_hardware():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        driver = MeshCoreDriver(storage=storage)
        driver._connected = True
        driver.client = MagicMock()
        driver.client.commands = MagicMock()

        # Mock get_contacts returning 64-character pubkey
        full_pk = "180967b7bd8c00112233445566778899aabbccddeeff00112233445566778899"
        mock_contacts_event = Event(
            type=McEventType.CONTACTS,
            payload={
                full_pk: {
                    "adv_name": "ac98 RPTR",
                    "type": 2,
                    "adv_lat": 54.4,
                    "adv_lon": 6.2,
                }
            }
        )
        driver.client.commands.get_contacts = AsyncMock(return_value=mock_contacts_event)
        driver.client.commands.remove_contact = AsyncMock(return_value=Event(type=McEventType.OK, payload={}))

        # When contact_deleted event is triggered
        await driver._async_remove_hardware_contact("180967b7bd8c")

        # Verify remove_contact was called with the full 64-char pubkey
        driver.client.commands.remove_contact.assert_called_once_with(full_pk)
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_driver_handle_contacts_skips_tombstoned():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        storage.delete_contact("180967b7bd8c")

        driver = MeshCoreDriver(storage=storage)
        full_pk = "180967b7bd8c00112233445566778899aabbccddeeff00112233445566778899"
        mock_event = Event(
            type=McEventType.CONTACTS,
            payload={
                full_pk: {
                    "adv_name": "ac98 RPTR",
                    "type": 2,
                    "adv_lat": 54.4,
                    "adv_lon": 6.2,
                }
            }
        )
        driver._handle_contacts(mock_event)

        # Storage should have 0 contacts saved
        assert len(storage.get_contacts()) == 0
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
