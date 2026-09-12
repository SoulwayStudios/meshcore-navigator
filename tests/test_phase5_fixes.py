"""Tests for Phase 5 radio routing, data integrity, and atomic config fixes."""

import json
from unittest.mock import MagicMock
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import ChannelInfo, NodeContact
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(db_path=tmp_path / "p5_test.db")


def test_send_channel_message_rejects_unconfigured_channel(storage):
    cfg = AppConfig()
    driver = MeshCoreDriver(config=cfg, storage=storage)

    # Channel that does not exist in storage
    res = driver.send_channel_message("NonExistentChannel", "Hello secret")
    assert res["status"] == "error"
    assert "not configured on this radio" in res["message"]

    # Public channel is allowed
    res_pub = driver.send_channel_message("Public", "Hello public")
    assert res_pub["status"] == "ok"


def test_join_channel_rejects_when_slots_full(storage):
    cfg = AppConfig()
    driver = MeshCoreDriver(config=cfg, storage=storage)

    # Fill slots 1 to 7
    for slot in range(1, 8):
        storage.save_channel(ChannelInfo(channel_id=slot, name=f"#Chan{slot}"))

    # Attempting to join another channel without specifying slot should fail, NOT overwrite slot 1
    res = driver.join_channel("ExtraChan")
    assert res["status"] == "error"
    assert "Maximum channel capacity reached" in res["message"]

    # Verify slot 1 was not overwritten
    slot1_ch = storage.get_channel("#Chan1")
    assert slot1_ch is not None
    assert slot1_ch.name == "#Chan1"


def test_equator_coordinates_preserved_in_sanitization(storage):
    # Quito node near equator (lat -0.18)
    quito = NodeContact(
        node_id="!quito001",
        alias="QuitoNode",
        latitude=-0.1807,
        longitude=-78.4678,
    )
    # True Null Island node (lat 0.0, lon 0.0)
    null_island = NodeContact(
        node_id="!null001",
        alias="NullIslandNode",
        latitude=0.0,
        longitude=0.0,
    )
    storage.save_contact(quito)
    storage.save_contact(null_island)

    # Run sanitization with ref coords near Quito so haversine doesn't flag it as distant
    report = storage.verify_and_sanitize_database(home_lat=-0.18, home_lon=-78.46)

    # Quito should still have its coordinates
    q = storage.get_contact("!quito001")
    assert q.latitude is not None
    assert abs(q.latitude - (-0.1807)) < 0.001

    # Null island coordinates should be sanitized to None
    ni = storage.get_contact("!null001")
    assert ni.latitude is None
    assert ni.longitude is None


def test_sanitization_does_not_prune_alias_prefix(storage):
    # Repeater named NORTH-REPEATER
    repeater = NodeContact(
        node_id="!rep111111",
        alias="NORTH-REPEATER",
        is_repeater=True,
        public_key="0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20",
    )
    # Legitimate user named NORTH
    user = NodeContact(
        node_id="!usr222222",
        alias="NORTH",
        is_repeater=False,
        public_key="aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
    )
    storage.save_contact(repeater)
    storage.save_contact(user)

    storage.verify_and_sanitize_database()

    # User NORTH must NOT be pruned
    assert storage.get_contact("!usr222222") is not None


def test_atomic_config_save_and_last_active_channel_restoration(tmp_path):
    cfg_file = tmp_path / "test_config.json"
    cfg = AppConfig()
    cfg.last_active_channel = "EmergencyResponse"
    cfg.save(cfg_file)

    # Check file exists and is valid JSON
    assert cfg_file.exists()
    assert not (tmp_path / "test_config.json.tmp").exists()
    with open(cfg_file, "r") as f:
        data = json.load(f)
    assert data["last_active_channel"] == "EmergencyResponse"

    # Reload config and check restoration
    loaded = AppConfig.load(cfg_file)
    assert loaded.last_active_channel == "EmergencyResponse"
