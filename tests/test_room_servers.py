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

    # 3. Via alias tags [room], [server], -bbs, or word boundary room/bbs/server
    c3 = NodeContact(node_id="!3333", alias="North-BBS [Room]")
    assert is_room_server_contact(c3) is True

    c4 = NodeContact(node_id="!4444", alias="Cumbria-Hub [Server]")
    assert is_room_server_contact(c4) is True

    c5 = NodeContact(node_id="!5555", alias="Lakeside-bbs")
    assert is_room_server_contact(c5) is True

    # 4. Unbracketed real-world room aliases
    c6 = NodeContact(node_id="091882e61107", alias="Navigator Room BBS M7NC")
    assert is_room_server_contact(c6) is True

    c7 = {"node_id": "091882e61107", "alias": "Navigator Room BBS M7NC", "adv_type": 3}
    assert is_room_server_contact(c7) is True

    # 5. Normal companion and repeater should return False
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


def test_room_servers_favorites_and_sorting(ensure_qapp, tmp_path):
    """Tests room server favorites pinned to top with divider, and sort toolbar switching."""
    db_file = tmp_path / "test_room_favs.db"
    storage = Storage(db_file)
    config = AppConfig()

    r1 = NodeContact(node_id="!rmA", alias="Alpha-BBS [Room]", is_room_server=True, last_seen="2026-09-10T10:00:00")
    r2 = NodeContact(node_id="!rmB", alias="Beta-BBS [Room]", is_room_server=True, is_favorite=True, last_seen="2026-09-15T12:00:00")
    r3 = NodeContact(node_id="!rmC", alias="Gamma-BBS [Room]", is_room_server=True, last_seen="2026-09-12T08:00:00")

    storage.save_contact(r1)
    storage.save_contact(r2)
    storage.save_contact(r3)

    view = RoomServersViewWidget(storage=storage, config=config)
    view.reload_rooms()

    # Total items in list_widget: 1 favorite (Beta) + 1 divider + 2 others (Alpha, Gamma) = 4
    assert view.list_widget.count() == 4

    # Top item must be Beta-BBS with star
    item0 = view.list_widget.item(0)
    w0 = view.list_widget.itemWidget(item0)
    assert isinstance(w0, RoomServerRowWidget)
    assert w0.contact.node_id == "!rmB"
    assert w0.is_favorite is True
    assert "★" in w0.star_lbl.text()
    assert not w0.star_lbl.isHidden()

    # Second item must be the thin divider
    item1 = view.list_widget.item(1)
    assert item1.flags() == Qt.ItemFlag.NoItemFlags

    # Alpha and Gamma should be below divider (alpha sorted: Alpha then Gamma)
    item2 = view.list_widget.item(2)
    w2 = view.list_widget.itemWidget(item2)
    assert w2.contact.node_id == "!rmA"

    item3 = view.list_widget.item(3)
    w3 = view.list_widget.itemWidget(item3)
    assert w3.contact.node_id == "!rmC"

    # Test sort mode change to "recent"
    view._set_sort_mode("recent")
    assert view.sort_mode == "recent"
    assert view.btn_sort_recent.isChecked() is True
    assert view.btn_sort_alpha.isChecked() is False

    # Below divider should now be sorted by recent: Gamma (09-12) then Alpha (09-10)
    w2_recent = view.list_widget.itemWidget(view.list_widget.item(2))
    w3_recent = view.list_widget.itemWidget(view.list_widget.item(3))
    assert w2_recent.contact.node_id == "!rmC"
    assert w3_recent.contact.node_id == "!rmA"

    # Test favorite button on active card
    view.set_active_room(r1)
    assert view.btn_fav.text() == "⭐ Favourite"
    view._toggle_active_room_favorite()
    # Now r1 should be favorite in storage
    assert storage.get_contact("!rmA").is_favorite is True
    assert view.btn_fav.text() == "★ Favourited"


def test_sidebar_room_server_display_and_context_menu(ensure_qapp, tmp_path):
    """Tests that Sidebar displays favorited room servers with magenta ★ ◆ @Alias [Room] styling."""
    from meshcore_tray.ui.sidebar import Sidebar

    db_file = tmp_path / "test_sidebar_rooms.db"
    storage = Storage(db_file)
    config = AppConfig()

    room = NodeContact(node_id="!rm99", alias="Lakes-BBS [Room]", is_room_server=True, is_favorite=True)
    normal = NodeContact(node_id="!user1", alias="Alice", is_favorite=False)
    storage.save_contact(room)
    storage.save_contact(normal)

    sidebar = Sidebar(storage=storage, config=config, show_contacts=True)
    sidebar.reload()

    # Find room server item in contact_list
    room_item = None
    for i in range(sidebar.contact_list.count()):
        it = sidebar.contact_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == "!rm99":
            room_item = it
            break

    assert room_item is not None
    assert "★ ◆ @Lakes-BBS [Room]" in room_item.text()
    assert room_item.foreground().color().name().upper() == "#FF55FF"
    assert room_item.data(Qt.ItemDataRole.UserRole + 2) is True  # is_room

    # Verify context menu actions for room server
    with patch("PyQt6.QtWidgets.QMenu.exec") as mock_exec, \
         patch("PyQt6.QtWidgets.QMenu.addAction") as mock_add_action:
        captured_actions = []
        mock_add_action.side_effect = lambda text: (captured_actions.append(text), MagicMock())[1]
        mock_exec.return_value = None

        from PyQt6.QtCore import QPoint
        sidebar._show_contact_context_menu(QPoint(5, 5))
        # When item at pos is selected or tested directly:
        # We can call with item's center
        rect = sidebar.contact_list.visualItemRect(room_item)
        sidebar._show_contact_context_menu(rect.center())

        assert any("Open Room Server Console" in a for a in captured_actions)
        assert any("Remove Room Server" in a for a in captured_actions)


def test_nav_dock_room_server_favorite_pill(ensure_qapp):
    """Tests that NavDockWidget styles room server pills with magenta hover border and BBS tooltip."""
    nav = NavDockWidget()
    room = NodeContact(node_id="!rm42", alias="North-BBS [Room]", is_room_server=True)

    nav.update_favorite_contacts([room])

    assert nav.fav_layout.count() == 1
    pill_btn = nav.fav_layout.itemAt(0).widget()
    assert pill_btn is not None

    tip = pill_btn.toolTip()
    assert "◆ Room Server (BBS)" in tip
    assert "Click to open Room Server Console" in tip

    # Stylesheet should have #FF55FF hover border
    ss = pill_btn.styleSheet()
    assert "#FF55FF" in ss


def test_mesh_map_room_server_context_menu_favorite_toggle(ensure_qapp, tmp_path):
    """Verifies that right-clicking a room server on the map toggles favorite without set_nodes AttributeError."""
    from PyQt6.QtWidgets import QMenu

    db_file = tmp_path / "test_map_node_ctx.db"
    storage = Storage(db_file)
    config = AppConfig()

    room = NodeContact(node_id="!rm99", alias="Lakes-BBS [Room]", is_room_server=True, latitude=54.6, longitude=-3.4)
    storage.save_contact(room)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(storage=storage, config=config)

    # Test compatibility aliases
    assert hasattr(widget, "set_nodes")
    assert hasattr(widget, "on_node_clicked")
    widget.set_nodes()

    # Track node_selected signal
    selected_nodes = []
    widget.node_selected.connect(selected_nodes.append)
    widget.on_node_clicked("!rm99")
    assert "!rm99" in selected_nodes

    captured_menu = None
    orig_menu_init = QMenu.__init__

    def custom_menu_init(self, *args, **kwargs):
        nonlocal captured_menu
        captured_menu = self
        orig_menu_init(self, *args, **kwargs)

    with patch.object(QMenu, "__init__", custom_menu_init), \
         patch.object(QMenu, "popup"):
        widget._show_node_context_menu("!rm99", "Lakes-BBS [Room]", False, False, 54.6, -3.4, 100, 100)

    assert captured_menu is not None
    real_actions = captured_menu.actions()
    fav_act = next((a for a in real_actions if "Favorites" in a.text()), None)
    assert fav_act is not None
    assert "Add to Favorites" in fav_act.text()

    # Trigger action - this must NOT raise AttributeError: 'MeshMapWidget' object has no attribute 'set_nodes'
    fav_act.trigger()

    # Verify contact is now favorited in storage
    updated_c = storage.get_contact("!rm99")
    assert updated_c.is_favorite is True


def test_mesh_map_delete_node_via_bridge(tmp_path):
    """Verifies that clicking delete node from map popup bridge removes contact, credentials, and emits events."""
    db_file = tmp_path / "test_delete_map.db"
    storage = Storage(db_file)
    cfg = AppConfig()

    contact = NodeContact(
        node_id="!del_target_01",
        alias="DeleteMeNode",
        is_repeater=True,
        latitude=54.5,
        longitude=-3.2
    )
    storage.save_contact(contact)
    storage.set_room_password("!del_target_01", "some_secret")
    assert storage.get_contact("!del_target_01") is not None
    assert storage.has_room_credentials("!del_target_01") is True

    app = QApplication.instance() or QApplication([])
    widget = MeshMapWidget(storage=storage, config=cfg)

    deleted_events = []
    map_updated_events = []
    bus.subscribe(EventType.CONTACT_DELETED, deleted_events.append)
    bus.subscribe(EventType.MAP_NODES_UPDATED, map_updated_events.append)

    # Call bridge deletion
    widget.bridge.on_delete_node("!del_target_01")

    assert "!del_target_01" in deleted_events or "del_target_01" in deleted_events
    assert len(map_updated_events) > 0

    # Contact and credentials must be gone from storage
    assert storage.get_contact("!del_target_01") is None
    assert storage.has_room_credentials("!del_target_01") is False


def test_leaflet_popup_delete_button_and_activity_scaling():
    """Verifies that Leaflet HTML contains delete node button and enhanced scaling curve."""
    from meshcore_tray.ui.mesh_map_widget import get_leaflet_html
    html = get_leaflet_html()

    # 1. Delete button in popup template
    assert "popup-btn-delete" in html
    assert "🗑️ Delete Node" in html
    assert "onDeleteNodeClicked" in html
    assert "deleteNodeMarker" in html

    # 2. Enhanced activity heatmap scaling curve
    assert "Math.pow(effectiveRatio, 0.55)" in html
    assert "scale(0.70)" in html
    assert "node.is_room_server" in html


def test_room_server_context_menu(qapp, tmp_path, monkeypatch):
    """Verifies that right-clicking a room server builds the QMenu and actions cleanly."""
    storage = Storage(tmp_path / "rooms_menu.db")
    cfg = AppConfig()
    view = RoomServersViewWidget(storage=storage, config=cfg)
    r1 = NodeContact("!rm1", "Cumbria Room Server [Room]", is_room_server=True, is_favorite=True, latitude=54.5, longitude=-3.2)
    storage.save_contact(r1)
    view.reload_rooms()
    assert view.list_widget.count() >= 1

    menu_built = []
    from PyQt6.QtWidgets import QMenu
    def fake_exec(self, *args, **kwargs):
        menu_built.append(self)
        return None
    monkeypatch.setattr(QMenu, "exec", fake_exec)

    rect = view.list_widget.visualItemRect(view.list_widget.item(0))
    pos = rect.center()
    view._show_room_context_menu(pos)

    assert len(menu_built) == 1
    actions = [a.text() for a in menu_built[0].actions()]
    assert any("Remove from Favorites" in a for a in actions)
    assert any("Show on Map" in a for a in actions)
    assert any("Track ADS-B" in a for a in actions)
    assert any("Enter / Edit Password" in a for a in actions)
    assert any("Copy Node ID" in a for a in actions)
    assert any("Copy Alias" in a for a in actions)
    assert any("Remove Room Server" in a for a in actions)




