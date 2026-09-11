import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
from meshcore_tray.config import AppConfig, MeshcoreConfig
from meshcore_tray.storage import Storage, NodeContact, PacketPathInfo
from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver


def test_meshcore_config_path_hash_mode_persistence(tmp_path):
    config = AppConfig()
    config.meshcore.path_hash_mode = 1  # 2-byte mode
    config.meshcore.autoadd_contacts = True
    config.meshcore.advert_loc_policy = 1  # Approximate
    config.meshcore.multi_acks = True
    config.meshcore.rx_delay_ms = 45

    cfg_dict = config.to_dict()
    loaded = AppConfig.from_dict(cfg_dict)

    assert loaded.meshcore.path_hash_mode == 1
    assert loaded.meshcore.autoadd_contacts is True
    assert loaded.meshcore.advert_loc_policy == 1
    assert loaded.meshcore.multi_acks is True
    assert loaded.meshcore.rx_delay_ms == 45


def test_storage_get_overheard_path_modes(tmp_path):
    db_file = tmp_path / "test_paths.db"
    storage = Storage(db_file)

    # 1. Add contact
    contact = NodeContact(node_id="!node_1b", alias="NodeOneByte", out_path_len=-1)
    storage.save_contact(contact)

    # 2. Add message from node_1b with 1-byte path (chunk size 2: "66ee" with len 2)
    msg1 = MessageEnvelope(
        id="m1",
        timestamp="2026-09-04T12:00:00Z",
        source_driver="meshcore_serial",
        sender_id="!node_1b",
        sender_name="NodeOneByte",
        channel="Public",
        text="Hello 1-byte",
        metadata={
            "path": "66ee",
            "path_len": 2,
            "route_type": "FLOOD"
        }
    )
    storage.save_message(msg1)

    # 3. Add message from another node with multibyte path (chunk size 6: "118d9ab2bb22" with len 2)
    msg2 = MessageEnvelope(
        id="m2",
        timestamp="2026-09-04T12:01:00Z",
        source_driver="meshcore_serial",
        sender_id="!node_mb",
        sender_name="NodeMultiByte",
        channel="Public",
        text="Hello multi-byte",
        metadata={
            "path": "118d9ab2bb22",
            "path_len": 2,
            "route_type": "FLOOD"
        }
    )
    storage.save_message(msg2)

    overheard = storage.get_overheard_path_modes()
    assert "node_1b" in overheard
    assert overheard["node_1b"]["path_mode"] == 0  # 1-byte
    assert overheard["node_1b"]["path_len"] == 2

    assert "node_mb" in overheard
    assert overheard["node_mb"]["path_mode"] == 2  # 3-byte multibyte
    assert overheard["node_mb"]["path_len"] == 2


@pytest.mark.asyncio
async def test_meshcore_driver_set_path_hash_mode():
    config = AppConfig()
    driver = MeshCoreDriver(config=config)
    mock_client = MagicMock()
    mock_client.commands.set_path_hash_mode = AsyncMock(return_value=MagicMock(type="command_ok"))
    mock_client.commands.set_autoadd_config = AsyncMock(return_value=MagicMock(type="command_ok"))
    mock_client.commands.set_advert_loc_policy = AsyncMock(return_value=MagicMock(type="command_ok"))
    mock_client.commands.set_multi_acks = AsyncMock(return_value=MagicMock(type="command_ok"))
    driver.client = mock_client
    driver._connected = True

    # Test set_path_hash_mode
    driver.set_path_hash_mode(1)
    assert config.meshcore.path_hash_mode == 1

    # Execute async task directly
    await driver._async_set_path_hash_mode(1)
    mock_client.commands.set_path_hash_mode.assert_called_with(1)
