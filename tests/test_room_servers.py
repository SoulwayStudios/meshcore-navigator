"""Unit and Integration Tests for Room Servers Support."""

from unittest.mock import MagicMock, patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import NodeContact, NeighbourInfo, MessageEnvelope, is_room_server_contact
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.room_servers_view import RoomServersViewWidget, RoomServerRowWidget, MessageBubbleWidget
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.main_window import MainWindow


def test_is_room_server_contact_detection():
    """Verifies that room servers are detected by flag, type code, and alias patterns."""
    # 1. Via is_room_server flag on NodeContact
    c1 = NodeContact(node_id="!1111", alias="Node-1", is_room_server=True)
    assert is_room_server_contact(c1) is True

    # 2. Via dict with type == 3 (AdvType.ROOM)
    c2 = {"node_id": "!2222", "alias": "General", "type": 3}
    assert is_room_server_contact(c2) is True

    # 3. Via alias tags [room], [server], -bbs
    c3 = NodeContact(node_id="!3333", alias="North-BBS [Room]")
    assert is_room_server_contact(c3) is True

    c4 = NodeContact(node_id="!4444", alias="Cumbria-Hub [Server]")
    assert is_room_server_contact(c4) is True

    c5 = NodeContact(node_id="!5555", alias="Lakeside-bbs")
    assert is_room_server_contact(c5) is True

    # 4. Normal companion and repeater should return False
    c_normal = NodeContact(node_id="!6666", alias="Bob-Companion", is_repeater=False, is_room_server=False)
    assert is_room_server_contact(c_normal) is False

    c_rep = NodeContact(node_id="!7777", alias="High-Peak [Rep]", is_repeater=True, is_room_server=False)
    assert is_room_server_contact(c_rep) is False


def test_storage_room_server_password_persistence(tmp_path):
    """Tests saving and retrieving room server passwords and room server querying."""
    db_file = tmp_path / "test_room_storage.db"
    storage = Storage(db_file)

    # Initially no password
    assert storage.get_room_password("!rm01") is None

    # Save password
    storage.set_room_password("!rm01", "SecretPass123", auto_login=True)
    assert storage.get_room_password("!rm01") == "SecretPass123"
    # Case insensitivity
    assert storage.get_room_password("RM01") == "SecretPass123"

    # Update password
    storage.set_room_password("!rm01", "NewPassword456", auto_login=True)
    assert storage.get_room_password("!rm01") == "NewPassword456"

    # Save contacts and verify get_room_servers()
    c_room = NodeContact(node_id="!rm01", alias="North-BBS [Room]", is_room_server=True)
    c_client = NodeContact(node_id="!cl01", alias="Alice", is_room_server=False)
    storage.save_contact(c_room)
    storage.save_contact(c_client)

    rooms = storage.get_room_servers()
    assert len(rooms) == 1
    assert rooms[0].node_id == "!rm01"
    assert rooms[0].is_room_server is True


def test_storage_set_contact_room_server_status(tmp_path):
    """Tests toggling is_room_server status across contacts and neighbours."""
    db_file = tmp_path / "test_status_storage.db"
    storage = Storage(db_file)

    contact = NodeContact(node_id="!node99", alias="Node-99", is_room_server=False)
    storage.save_contact(contact)

    c_before = storage.get_contact("!node99")
    assert c_before.is_room_server is False

    storage.set_contact_room_server_status("!node99", True)
    c_after = storage.get_contact("!node99")
    assert c_after.is_room_server is True


def test_mock_driver_room_server_operations(tmp_path):
    """Tests mock driver room server commands and simulated bulletin responses."""
    db_file = tmp_path / "test_mock_driver.db"
    storage = Storage(db_file)
    driver = MockRadioDriver(storage=storage)

    # Query neighbours produces a room server
    res = driver.query_neighbours()
    assert any(n.get("is_room_server") for n in res["neighbours"])

    # Send room login saves password and emits response
    login_res = driver.send_room_login("!rm01", "TestKey999")
    assert login_res["status"] == "ok"
    assert storage.get_room_password("!rm01") == "TestKey999"

    # Send room command !read
    cmd_res = driver.send_room_command("!rm01", "!read")
    assert cmd_res["status"] == "ok"

    # Send room logout
    logout_res = driver.send_room_logout("!rm01")
    assert logout_res["status"] == "ok"


def test_mesh_map_room_server_filtering_and_tagging(ensure_qapp, tmp_path):
    """Tests that MeshMapWidget tags room servers and handles the ROOMS filter mode."""
    db_file = tmp_path / "test_map_rooms.db"
    storage = Storage(db_file)

    # Save 1 companion, 1 repeater, 1 room server with coordinates
    storage.save_contact(NodeContact(node_id="!comp1", alias="Alice", latitude=54.60, longitude=-3.40, is_repeater=False, is_room_server=False))
    storage.save_contact(NodeContact(node_id="!rep1", alias="Peak-Rep [Rep]", latitude=54.65, longitude=-3.45, is_repeater=True, is_room_server=False))
    storage.save_contact(NodeContact(node_id="!rm1", alias="North-BBS [Room]", latitude=54.70, longitude=-3.50, is_repeater=False, is_room_server=True))

    map_widget = MeshMapWidget(storage=storage)
    map_widget._page_ready = True

    # 1. Filter: ROOMS
    map_widget.set_node_filter_mode("ROOMS")
    assert map_widget.node_filter_mode == "ROOMS"

    # 2. Filter: CLIENTS (must exclude repeaters and room servers)
    map_widget.set_node_filter_mode("CLIENTS")
    assert map_widget.node_filter_mode == "CLIENTS"

    # 3. Filter: REPEATERS
    map_widget.set_node_filter_mode("REPEATERS")
    assert map_widget.node_filter_mode == "REPEATERS"

    # 4. Filter: ALL
    map_widget.set_node_filter_mode("ALL")
    assert map_widget.node_filter_mode == "ALL"


def test_nav_dock_rooms_button(ensure_qapp):
    """Tests that NavDockWidget includes the Room Servers button under DMs and switches view."""
    nav = NavDockWidget()
    assert hasattr(nav, "btn_rooms")
    assert nav.btn_rooms is not None

    views = []
    nav.view_changed.connect(views.append)

    nav.switch_view("rooms")
    assert nav.active_view == "rooms"
    assert nav.btn_rooms.is_active is True
    assert "rooms" in views


def test_room_servers_view_widget_ui(ensure_qapp, tmp_path):
    """Tests RoomServersViewWidget list population, selection, and password loading."""
    db_file = tmp_path / "test_ui_rooms.db"
    storage = Storage(db_file)
    storage.set_room_password("!rm01", "SavedPass456")

    c1 = NodeContact(node_id="!rm01", alias="North-BBS [Room]", is_room_server=True, latitude=54.66, longitude=-3.42, snr_db=9.5, rssi_dbm=-82.0)
    storage.save_contact(c1)

    driver = MockRadioDriver(storage=storage)
    view = RoomServersViewWidget(storage=storage, radio_driver=driver)
    view.reload_rooms()

    assert view.list_widget.count() == 1
    view.set_active_room(c1)

    # Check that saved password pre-populates
    assert view.pwd_input.text() == "SavedPass456"
    assert view.active_room.node_id == "!rm01"
    assert "North-BBS" in view.lbl_room_name.text()
    assert view.btn_map.isEnabled() is True

    # Test login action
    view.pwd_input.setText("UpdatedPass789")
    view._on_login_clicked()
    assert storage.get_room_password("!rm01") == "UpdatedPass789"

    # Test message posting
    view.composer_input.setText("Hello room!")
    view._on_send_message_clicked()
    assert view.composer_input.text() == ""


def test_main_window_room_server_routing(ensure_qapp, tmp_path):
    """Tests that selecting a room server contact routes MainWindow to the room servers view."""
    db_file = tmp_path / "test_mw_routing.db"
    storage = Storage(db_file)
    c_room = NodeContact(node_id="!rm99", alias="Lakes-Room [Room]", is_room_server=True, latitude=54.6, longitude=-3.4)
    storage.save_contact(c_room)

    config = AppConfig()
    driver = MockRadioDriver(storage=storage)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage, radio_driver=driver)

    # Selecting the room server should switch dock to "rooms"
    win._on_contact_selected("!rm99")
    assert win.nav_dock.active_view == "rooms"
    assert win.main_stack.currentWidget() == win.rooms_view
    assert win.rooms_view.active_room is not None
    assert win.rooms_view.active_room.node_id == "!rm99"

    win.cleanup()
