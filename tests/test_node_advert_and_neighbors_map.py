import pytest
import os
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch
from PyQt6.QtWidgets import QApplication, QMenu
from PyQt6.QtCore import QPoint

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import NodeContact, MessageEnvelope
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication(["meshcore-test"])
    return app_instance


def test_mock_driver_send_advert():
    driver = MockRadioDriver()

    # Test zero-hop advert
    res_zero = driver.send_advert(flood=False)
    assert res_zero is True

    # Test flood-routed advert
    res_flood = driver.send_advert(flood=True)
    assert res_flood is True


def test_meshcore_driver_send_advert():
    config = AppConfig()
    driver = MeshCoreDriver(config=config)
    
    # When not connected
    assert driver.send_advert(flood=False) is False

    # When connected with client
    driver._connected = True
    driver.client = MagicMock()
    driver.client.commands.send_advert = AsyncMock(return_value="OK")
    
    # Success zero hop
    assert driver.send_advert(flood=False) is True
    driver.client.commands.send_advert.assert_called_with(flood=False)

    # Success flood routed
    driver.client.commands.send_advert.reset_mock()
    assert driver.send_advert(flood=True) is True
    driver.client.commands.send_advert.assert_called_with(flood=True)

    # Failure / exception
    driver.client.commands.send_advert.side_effect = RuntimeError("Radio timeout")
    # Will log error and return True or False safely
    res = driver.send_advert(flood=True)
    assert isinstance(res, bool)


def test_main_window_broadcast_node_actions(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()
        config.meshcore.node_alias = "M7NCY"

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            win = MainWindow(config=config, storage=storage)

        # Verify companion node button exists and has click handler
        assert hasattr(win, "companion_node_btn")
        assert "M7NCY" in win.companion_node_btn.text()

        # Mock radio driver
        mock_driver = MagicMock()
        mock_driver.is_connected.return_value = True
        mock_driver.send_advert.return_value = True
        win.radio_driver = mock_driver

        # Test trigger zero hop
        win._trigger_node_broadcast(flood=False)
        mock_driver.send_advert.assert_called_with(flood=False)

        # Test trigger flood routed
        win._trigger_node_broadcast(flood=True)
        mock_driver.send_advert.assert_called_with(flood=True)

        # Test disconnected radio handling
        mock_driver.is_connected.return_value = False
        win._trigger_node_broadcast(flood=False)
        assert "⚠️ Radio not connected" in win.statusBar().currentMessage()

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_repeater_console_neighbors_parsing(app):
    console = RepeaterConsoleWidget()
    
    # 1. Test regex parsing from text output
    sample_text = """
    🌐 Neighbors (3 heard):
    • !627c8262 (SNR: +0.2dB, 8m ago)
    • !4b1c6e7b (SNR: -4.5dB, 1h ago)
    • !0a2b3c4d (SNR: +14dB, 30s ago)
    """
    parsed = console._parse_neighbors(sample_text)
    assert len(parsed) == 3
    assert parsed[0]["node_id"] == "627c8262"
    assert parsed[0]["snr_str"] == "+0.2 dB"
    assert parsed[0]["time_str"] == "8m ago"
    assert parsed[1]["node_id"] == "4b1c6e7b"
    assert parsed[1]["snr_str"] == "-4.5 dB"
    assert parsed[1]["time_str"] == "1h ago"
    assert parsed[2]["node_id"] == "0a2b3c4d"
    assert parsed[2]["snr_str"] == "+14.0 dB"
    assert parsed[2]["time_str"] == "30s ago"

    # 2. Test metadata fallback parsing
    meta = {
        "neighbours": [
            {"node_id": "11223344", "snr": 8.5, "time_ago": "2m ago"},
            {"pubkey": "55667788", "snr": -2.0, "time_ago": "15m ago"}
        ]
    }
    parsed_meta = console._parse_neighbors("Some header text", metadata=meta)
    assert len(parsed_meta) == 2
    assert parsed_meta[0]["node_id"] == "11223344"
    assert parsed_meta[0]["snr_str"] == "+8.5 dB"
    assert parsed_meta[0]["time_str"] == "2m ago"
    assert parsed_meta[1]["node_id"] == "55667788"
    assert parsed_meta[1]["snr_str"] == "-2.0 dB"
    assert parsed_meta[1]["time_str"] == "15m ago"

    # 3. Test handle_incoming_message emission
    rep_contact = NodeContact(node_id="repeater_01", alias="West Yagi", latitude=54.5, longitude=-3.1, is_repeater=True)
    console.set_repeater(rep_contact)

    signal_emitted = []
    console.show_neighbors_on_map_requested.connect(lambda rep, n: signal_emitted.append((rep, n)))

    env = MessageEnvelope(
        id="msg_01",
        sender_id="repeater_01",
        sender_name="West Yagi",
        text="🌐 Neighbors (1 heard):\n• !627c8262 (SNR: +1.5dB, 4m ago)"
    )
    console.handle_incoming_message(env)

    assert len(signal_emitted) == 1
    assert signal_emitted[0][0].node_id == "repeater_01"
    assert len(signal_emitted[0][1]) == 1
    assert signal_emitted[0][1][0]["node_id"] == "627c8262"
    assert console.btn_map_neighbors.isEnabled() is True
    assert "1" in console.btn_map_neighbors.text()


def test_mesh_map_display_repeater_neighbors(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()

        # Seed host repeater and neighbour contacts into storage
        host_rep = NodeContact(node_id="rep_host_01", alias="M7NCY West Yagi v4", latitude=54.60, longitude=-3.20, is_repeater=True)
        neighbor_1 = NodeContact(node_id="627c8262", alias="Skiddaw RPTR", latitude=54.65, longitude=-3.15, is_repeater=True)
        neighbor_2 = NodeContact(node_id="4b1c6e7b", alias="Keswick Base", latitude=54.58, longitude=-3.12, is_repeater=False)
        storage.save_contact(host_rep)
        storage.save_contact(neighbor_1)
        storage.save_contact(neighbor_2)

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            map_widget = MeshMapWidget(config=config, storage=storage)

        # Attach mock web_view and set _page_ready
        mock_web_view = MagicMock()
        mock_js_calls = []
        mock_web_view.page().runJavaScript = lambda js: mock_js_calls.append(js)
        map_widget.web_view = mock_web_view
        map_widget._page_ready = True

        # Call display_repeater_neighbors
        neighbors_data = [
            {"node_id": "627c8262", "snr": "+0.2", "time_ago": "8m ago"},
            {"node_id": "4b1c6e7b", "snr": "-4.5", "time_ago": "1h ago"},
        ]

        # First test with host repeater passed directly
        map_widget.display_repeater_neighbors(host_rep, neighbors_data)

        # Verify JS was executed with drawRepeaterNeighbors payload
        assert len(mock_js_calls) > 0
        js_call = mock_js_calls[-1]
        assert "drawRepeaterNeighbors" in js_call
        assert "M7NCY West Yagi v4" in js_call
        assert "Skiddaw RPTR" in js_call
        assert "+0.2" in js_call
        assert "8m ago" in js_call

        # Test clear
        map_widget.clear_repeater_neighbors()
        assert "clearRepeaterNeighbors" in mock_js_calls[-1]

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


