"""Unit tests for MeshCoreDriver advertisement handling and NavDock radio connect context menu."""

import os
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication(["meshcore-test"])
    return app_instance


def test_meshcore_driver_setup_subscriptions_has_all_handlers(tmp_path):
    """Verifies that all handlers subscribed in _setup_subscriptions exist on MeshCoreDriver."""
    db_file = tmp_path / "test_subs.db"
    storage = Storage(db_file)
    driver = MeshCoreDriver(config=AppConfig(), storage=storage)

    mock_client = MagicMock()
    subscribed_events = []
    def fake_subscribe(ev_type, handler):
        subscribed_events.append((ev_type, handler))
        assert callable(handler), f"Handler for {ev_type} must be callable"
        return f"sub_{len(subscribed_events)}"

    mock_client.subscribe.side_effect = fake_subscribe
    driver.client = mock_client

    driver._setup_subscriptions()
    assert len(subscribed_events) >= 14

    # Specifically verify _handle_advertisement is registered
    handlers_registered = [h.__name__ for _, h in subscribed_events]
    assert "_handle_advertisement" in handlers_registered
    assert hasattr(driver, "_handle_advertisement")
    assert callable(driver._handle_advertisement)


def test_meshcore_driver_handle_advertisement(tmp_path):
    """Tests processing of advertisement packet by MeshCoreDriver."""
    db_file = tmp_path / "test_adv.db"
    storage = Storage(db_file)
    driver = MeshCoreDriver(config=AppConfig(), storage=storage)

    neighbours_received = []
    bus.subscribe(EventType.NEIGHBOURS_UPDATED, lambda n: neighbours_received.append(n))

    adv_payload = {
        "public_key": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
        "adv_name": "TestNode",
        "type": 1,
        "adv_lat": 51.5074,
        "adv_lon": -0.1278,
        "snr": 9.5,
        "rssi": -72.0
    }

    driver._handle_advertisement(adv_payload)

    assert len(neighbours_received) >= 1
    info = neighbours_received[-1][0]
    assert info.alias == "TestNode"
    assert info.snr_db == 9.5
    assert info.rssi_dbm == -72.0
    assert info.latitude == 51.5074
    assert info.longitude == -0.1278

    # Verify storage persisted contact
    contact = storage.get_contact("aabbccddeeff")
    assert contact is not None
    assert contact.alias == "TestNode"
    assert contact.latitude == 51.5074


def test_meshcore_driver_connect_and_reconnect(tmp_path):
    """Tests driver.connect() and driver.reconnect() dispatch connection task."""
    db_file = tmp_path / "test_conn.db"
    storage = Storage(db_file)
    driver = MeshCoreDriver(config=AppConfig(), storage=storage)

    def close_coro(coro):
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    with patch.object(driver, "_dispatch_task", side_effect=close_coro) as mock_dispatch:
        driver.connect()
        assert mock_dispatch.called
        assert driver._running is True

    with patch.object(driver, "_dispatch_task", side_effect=close_coro) as mock_dispatch:
        driver.reconnect()
        assert mock_dispatch.called
        assert driver._running is True


def test_mock_driver_connect_and_reconnect():
    """Tests MockRadioDriver connect and reconnect."""
    driver = MockRadioDriver()
    assert not driver.is_connected()
    driver.connect()
    assert driver.is_connected()
    driver.reconnect()
    assert driver.is_connected()


def test_nav_dock_context_menu_connect_action_when_disconnected(app):
    """Tests that right-clicking main app icon shows 'Connect to Radio' when disconnected."""
    config = AppConfig()
    dock = NavDockWidget(config=config)
    dock.update_connection_status(connected=False, port="", mode="serial")

    emitted = []
    dock.radio_connect_requested.connect(lambda: emitted.append(True))

    created_actions = []
    class FakeAction:
        def __init__(self, text):
            self._text = text
            self._enabled = True
        def text(self):
            return self._text
        def setEnabled(self, val):
            self._enabled = val

    class FakeMenu:
        def __init__(self, parent=None):
            self.actions_list = []
        def setStyleSheet(self, s):
            pass
        def addAction(self, text):
            act = FakeAction(text)
            self.actions_list.append(act)
            created_actions.append(act)
            return act
        def addSeparator(self):
            pass
        def exec(self, point):
            for act in self.actions_list:
                if "Connect to Radio" in act.text():
                    return act
            return None

    with patch("meshcore_tray.ui.nav_dock.QMenu", FakeMenu):
        dock._show_advert_menu(QPoint(0, 0))

    assert any("Connect to Radio" in a.text() for a in created_actions)
    assert not any("Reconnect Radio" in a.text() for a in created_actions)
    assert len(emitted) == 1


def test_nav_dock_context_menu_reconnect_action_when_connected(app):
    """Tests that right-clicking main app icon shows 'Reconnect Radio' when connected."""
    config = AppConfig()
    dock = NavDockWidget(config=config)
    dock.update_connection_status(connected=True, port="/dev/ttyUSB0", mode="serial")

    emitted = []
    dock.radio_connect_requested.connect(lambda: emitted.append(True))

    created_actions = []
    class FakeAction:
        def __init__(self, text):
            self._text = text
            self._enabled = True
        def text(self):
            return self._text
        def setEnabled(self, val):
            self._enabled = val

    class FakeMenu:
        def __init__(self, parent=None):
            self.actions_list = []
        def setStyleSheet(self, s):
            pass
        def addAction(self, text):
            act = FakeAction(text)
            self.actions_list.append(act)
            created_actions.append(act)
            return act
        def addSeparator(self):
            pass
        def exec(self, point):
            for act in self.actions_list:
                if "Reconnect Radio" in act.text():
                    return act
            return None

    with patch("meshcore_tray.ui.nav_dock.QMenu", FakeMenu):
        dock._show_advert_menu(QPoint(0, 0))

    assert any("Reconnect Radio" in a.text() for a in created_actions)
    assert not any("Connect to Radio" in a.text() and "Reconnect" not in a.text() for a in created_actions)
    assert len(emitted) == 1


def test_main_window_radio_connect_requested(app, tmp_path):
    """Tests that MainWindow triggers driver.connect() when nav_dock emits radio_connect_requested."""
    db_file = tmp_path / "test_mw_conn.db"
    storage = Storage(db_file)
    config = AppConfig()

    mock_driver = MagicMock()
    mock_driver.is_connected.return_value = False
    mock_driver.connect = MagicMock()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        mw = MainWindow(config=config, storage=storage, radio_driver=mock_driver)

    mw.nav_dock.radio_connect_requested.emit()
    assert mock_driver.connect.called
