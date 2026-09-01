"""Unit tests for GatewayManager and clean command dispatcher."""

import pytest
from meshcore_tray.core.models import CommandPacket
from meshcore_tray.core.gateway import GatewayManager
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.storage import Storage


@pytest.fixture
def test_gateway(tmp_path):
    storage = Storage(db_path=tmp_path / "gw_test.db")
    driver = MockRadioDriver(storage=storage)
    return GatewayManager(storage=storage, radio_driver=driver)


def test_gateway_send_msg_command(test_gateway):
    cmd = CommandPacket(
        command_type="SEND_MSG",
        payload={"channel": "Public", "text": "Gateway integration test"}
    )
    res = test_gateway.execute_command(cmd)
    assert res["status"] == "ok"
    assert "message_id" in res


def test_gateway_query_telemetry(test_gateway):
    cmd = CommandPacket(command_type="QUERY_TELEMETRY")
    res = test_gateway.execute_command(cmd)
    assert res["status"] == "ok"
    assert "telemetry" in res
    assert res["telemetry"]["frequency_mhz"] == 868.125


def test_gateway_query_neighbours(test_gateway):
    cmd = CommandPacket(command_type="QUERY_NEIGHBOURS")
    res = test_gateway.execute_command(cmd)
    assert res["status"] == "ok"
    assert "neighbours" in res
    assert len(res["neighbours"]) > 0
