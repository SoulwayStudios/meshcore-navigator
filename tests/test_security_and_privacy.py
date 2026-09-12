"""Tests for gateway security, DM privacy, and password redaction."""

from unittest.mock import MagicMock
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.core.gateway import GatewayManager
from meshcore_tray.core.models import CommandPacket, MessageEnvelope
from meshcore_tray.pixoo.pixoo_service import PixooService
from meshcore_tray.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(db_path=tmp_path / "sec_test.db")


def test_storage_get_messages_excludes_dms_by_default(storage):
    # Save a public message and a DM
    msg_pub = MessageEnvelope(
        id="pub-1",
        channel="Public",
        text="Hello world",
        is_direct_message=False,
    )
    msg_dm = MessageEnvelope(
        id="dm-1",
        channel="",
        sender_id="alice",
        recipient_id="bob",
        text="Super secret private DM",
        is_direct_message=True,
    )
    storage.save_message(msg_pub)
    storage.save_message(msg_dm)

    # By default, without specifying channel or contact_id, include_dms=False
    msgs = storage.get_messages(include_dms=False)
    ids = [m.id for m in msgs]
    assert "pub-1" in ids
    assert "dm-1" not in ids

    # With include_dms=True
    all_msgs = storage.get_messages(include_dms=True)
    all_ids = [m.id for m in all_msgs]
    assert "pub-1" in all_ids
    assert "dm-1" in all_ids


def test_gateway_get_messages_never_exposes_dms(storage):
    gw = GatewayManager(storage=storage)
    msg_pub = MessageEnvelope(
        id="pub-2",
        channel="Public",
        text="Public chat",
        is_direct_message=False,
    )
    msg_dm = MessageEnvelope(
        id="dm-2",
        channel="",
        sender_id="alice",
        recipient_id="bob",
        text="Secret DM",
        is_direct_message=True,
    )
    storage.save_message(msg_pub)
    storage.save_message(msg_dm)

    res = gw.execute_command(CommandPacket(command_type="GET_MESSAGES"))
    assert res["status"] == "ok"
    returned_ids = [m["id"] for m in res["messages"]]
    assert "pub-2" in returned_ids
    assert "dm-2" not in returned_ids


def test_pixoo_service_excludes_dms_from_display_by_default():
    cfg = AppConfig()
    cfg.pixoo.show_direct_messages = False
    svc = PixooService(config=cfg)
    svc.renderer = MagicMock()

    dm_msg = MessageEnvelope(
        id="dm-pixoo",
        is_direct_message=True,
        sender_name="Alice",
        text="Private whisper",
    )
    svc._on_message_received(dm_msg)
    assert not svc.renderer.trigger_message_alert.called

    pub_msg = MessageEnvelope(
        id="pub-pixoo",
        is_direct_message=False,
        channel="Public",
        sender_name="Bob",
        text="Public announcement",
    )
    svc._on_message_received(pub_msg)
    assert svc.renderer.trigger_message_alert.called
