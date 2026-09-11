"""Unit tests for Contacts list enhancements, Repeater Console, HTML entity punctuation fixes, and Pixoo Queue Pacing."""

import html
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import ChannelInfo, MessageEnvelope, NodeContact
from meshcore_tray.storage import Storage
from meshcore_tray.ui.chat_widget import ChatWidget, MessageBubble
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget
from meshcore_tray.pixoo.pixoo_renderer import PixooRenderer


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        import sys
        app = QApplication(sys.argv)
    return app


def test_message_bubble_punctuation_unescaping(qapp, tmp_path):
    """Verifies that quotes, apostrophes, and encoded HTML entities render cleanly as punctuation."""
    storage = Storage(tmp_path / "punct_test.db")
    raw_text = '@[M7NCY] &quot;M7NCY West Yagi v4&quot; in its neighbors. Ah nice. I&#x27;ll log back in!'
    msg = MessageEnvelope(
        id="m_punct",
        sender_name="G1CAE-XiaoS3-WiFi",
        channel="cumbria",
        text=raw_text
    )
    bubble = MessageBubble(msg, storage=storage)

    from PyQt6.QtWidgets import QLabel
    labels = bubble.findChildren(QLabel)
    # Find the body label
    body_texts = [lbl.text() for lbl in labels if "West Yagi" in lbl.text()]
    assert len(body_texts) == 1
    body_text = body_texts[0]

    assert '"M7NCY West Yagi v4"' in body_text
    assert "I'll log back in!" in body_text
    assert "&quot;" not in body_text
    assert "&#x27;" not in body_text


def test_sidebar_contacts_favorites_at_top_and_icons(qapp, tmp_path):
    """Verifies that favorite contacts sort to top and display 👤 for companions and 📡 for repeaters."""
    storage = Storage(tmp_path / "contacts_test.db")

    # Insert contacts: 1 regular companion, 1 favorite companion, 1 regular repeater, 1 favorite repeater
    c1 = NodeContact("!node1", "Dave", is_favorite=False, is_repeater=False)
    c2 = NodeContact("!node2", "Zoe", is_favorite=True, is_repeater=False)
    c3 = NodeContact("!node3", "Cumbria Central [Rep]", is_favorite=False, is_repeater=True)
    c4 = NodeContact("!node4", "Stoke Repeater [Rep]", is_favorite=True, is_repeater=True)

    storage.save_contact(c1)
    storage.save_contact(c2)
    storage.save_contact(c3)
    storage.save_contact(c4)

    sidebar = Sidebar(storage=storage)
    sidebar.reload()

    # Verify contact list items
    items = [sidebar.contact_list.item(i).text() for i in range(sidebar.contact_list.count())]
    assert len(items) == 4

    # Top two items must be favorites (contain ★)
    assert "★" in items[0]
    assert "★" in items[1]
    # Bottom two items must NOT be favorites
    assert "★" not in items[2]
    assert "★" not in items[3]

    # Check foreground colors: favorite items have gold/yellow foreground
    assert sidebar.contact_list.item(0).foreground().color().name() == "#ffd700"
    assert sidebar.contact_list.item(1).foreground().color().name() == "#ffd700"

    # Verify icons: repeaters have 📡 and companions have 👤
    # Check Zoe (favorite companion) has 👤
    zoe_item = [it for it in items if "Zoe" in it][0]
    assert "👤" in zoe_item

    # Check Stoke Repeater (favorite repeater) has 📡
    stoke_item = [it for it in items if "Stoke" in it][0]
    assert "📡" in stoke_item

    # Check Cumbria Central (regular repeater) has 📡
    cumbria_item = [it for it in items if "Cumbria" in it][0]
    assert "📡" in cumbria_item

    # Check Dave (regular companion) has 👤
    dave_item = [it for it in items if "Dave" in it][0]
    assert "👤" in dave_item


def test_sidebar_channel_red_star_and_context_menus(qapp, tmp_path):
    """Verifies that favorite channels display a red star (★ with #FF5555 foreground) and context menu toggling."""
    storage = Storage(tmp_path / "chan_fav_test.db")
    config = AppConfig()

    storage.save_channel(type("Ch", (), {"channel_id": 1, "name": "cumbria", "is_favorite": True, "is_pixoo_enabled": True, "last_activity_ts": "", "unread_count": 0}))
    storage.save_channel(type("Ch", (), {"channel_id": 2, "name": "test", "is_favorite": False, "is_pixoo_enabled": True, "last_activity_ts": "", "unread_count": 0}))

    sidebar = Sidebar(storage=storage, config=config)
    sidebar.reload()

    # Cumbria is favorite -> top of list, contains ★, foreground #ff5555 (red)
    items = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    cumbria_idx = [i for i, it in enumerate(items) if "cumbria" in it][0]
    cumbria_item = sidebar.channel_list.item(cumbria_idx)

    assert "★" in cumbria_item.text()
    assert cumbria_item.foreground().color().name() == config.app_colors.favorite_channel_color.lower()

    # Toggle favorite off via storage
    storage.set_channel_favorite("cumbria", False)
    sidebar.reload()
    items_after = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    cumbria_after = [it for it in items_after if "cumbria" in it][0]
    assert "★" not in cumbria_after

    # Test clearing all favorites
    storage.set_channel_favorite("cumbria", True)
    storage.set_contact_favorite("!node1", True)
    storage.clear_all_favorites()
    assert all(not ch.is_favorite for ch in storage.get_channels())
    assert all(not c.is_favorite for c in storage.get_contacts())


def test_main_window_switches_to_repeater_console(qapp, tmp_path):
    """Verifies that clicking a repeater switches to RepeaterConsoleWidget."""
    storage = Storage(tmp_path / "rep_window_test.db")
    config = AppConfig()

    rep_contact = NodeContact("!rep123", "Shap Summit [Rep]", is_favorite=True, is_repeater=True)
    comp_contact = NodeContact("!comp456", "Alan-H", is_favorite=False, is_repeater=False)
    storage.save_contact(rep_contact)
    storage.save_contact(comp_contact)

    from unittest.mock import patch
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage)

    # Initially on Chat (index 0)
    assert win.center_stack.currentIndex() == 0

    # Select repeater contact -> switches to Repeater Console (index 1)
    win._on_contact_selected("!rep123")
    assert win.center_stack.currentIndex() == 1
    assert win.repeater_console.current_contact.node_id == "!rep123"

    # Select companion contact -> switches back to Chat (index 0)
    win._on_contact_selected("!comp456")
    assert win.center_stack.currentIndex() == 0


def test_pixoo_queue_and_backlog_pacing_acceleration():
    """Verifies Pixoo 10s vibrant hold, FIFO message queue, and 3s acceleration on backlog > 60s."""
    config = AppConfig()
    renderer = PixooRenderer(config=config)

    # 1. First message arrives -> displays immediately
    msg1 = MessageEnvelope(id="q1", sender_name="User1", channel="Public", text="Msg 1")
    renderer.trigger_message_alert(msg1)

    assert renderer.active_message is not None
    assert renderer.active_message.text == "Msg 1"
    assert len(renderer.message_queue) == 0
    assert renderer.get_effective_display_duration() == 10.0

    # 2. Second message arrives while Msg 1 is vibrant -> queued
    msg2 = MessageEnvelope(id="q2", sender_name="User2", channel="Public", text="Msg 2")
    renderer.trigger_message_alert(msg2)

    assert len(renderer.message_queue) == 1
    assert renderer.active_message.text == "Msg 1"  # Still showing Msg 1!
    assert renderer.get_effective_display_duration() == 10.0  # 1 * 10s = 10s <= 60s

    # 3. Simulate high traffic burst: 6 more messages arrive (total 7 queued)
    for i in range(3, 9):
        m = MessageEnvelope(id=f"q{i}", sender_name=f"User{i}", channel="Public", text=f"Msg {i}")
        renderer.trigger_message_alert(m)

    assert len(renderer.message_queue) == 7
    # Cumulative backlog at standard 10s is 7 * 10s = 70s (> 60s)!
    # Dynamic pacing must accelerate duration to 3 seconds!
    assert renderer.get_effective_display_duration() == 3.0

    # 4. Advance frames past 3.0s (75 frames) -> next message should pop and display
    renderer.anim_frame = 76
    renderer.render_frame()

    # Msg 2 was popped and is now the active message
    assert renderer.active_message.text == "Msg 2"
    assert len(renderer.message_queue) == 6  # 6 * 10s = 60s <= 60s!

    # Backlog dropped to <= 60s, so pacing restores back to 10.0 seconds!
    assert renderer.get_effective_display_duration() == 10.0


def test_repeater_login_without_password(qapp):
    """Verifies that user can click login without entering a password and !login is sent."""
    console = RepeaterConsoleWidget()
    contact = NodeContact("!rep99", "Hilltop Repeater [Rep]", is_favorite=False, is_repeater=True)
    console.set_repeater(contact)

    captured_cmds = []
    console.send_command_requested.connect(lambda nid, cmd: captured_cmds.append((nid, cmd)))

    # 1. Login with empty password
    console.pwd_input.clear()
    console._on_login_clicked()
    assert len(captured_cmds) == 1
    assert captured_cmds[0] == ("!rep99", "!login")

    # 2. Login with password entered
    console.pwd_input.setText("secret123")
    console._on_login_clicked()
    assert len(captured_cmds) == 2
    assert captured_cmds[1] == ("!rep99", "!login secret123")


def test_sidebar_contact_search_and_sorting_modes(qapp, tmp_path):
    """Verifies searchbox filtering and alphabetical vs recent heard sorting with favorites pinned to top."""
    storage = Storage(tmp_path / "search_sort_test.db")
    config = AppConfig()

    # Create 4 contacts:
    # 2 favorites: Bravo (seen at 10:00), Alpha (seen at 12:00)
    # 2 non-favorites: Zulu (seen at 14:00), Charlie (seen at 08:00)
    c1 = NodeContact("!c1", "Bravo", is_favorite=True, last_seen="2026-09-03T10:00:00Z")
    c2 = NodeContact("!c2", "Alpha", is_favorite=True, last_seen="2026-09-03T12:00:00Z")
    c3 = NodeContact("!c3", "Zulu", is_favorite=False, last_seen="2026-09-03T14:00:00Z")
    c4 = NodeContact("!c4", "Charlie", is_favorite=False, last_seen="2026-09-03T08:00:00Z")

    storage.save_contact(c1)
    storage.save_contact(c2)
    storage.save_contact(c3)
    storage.save_contact(c4)

    sidebar = Sidebar(storage=storage, config=config)

    # 1. Test Alphabetical Sort ("alpha")
    sidebar.contact_sort.setCurrentIndex(0)  # "alpha"
    items = [sidebar.contact_list.item(i).text() for i in range(sidebar.contact_list.count())]
    # Favorites at top sorted A-Z: Alpha then Bravo
    assert "Alpha" in items[0] and "★" in items[0]
    assert "Bravo" in items[1] and "★" in items[1]
    # Non-favorites below sorted A-Z: Charlie then Zulu
    assert "Charlie" in items[2]
    assert "Zulu" in items[3]

    # 2. Test Most Recent Sort ("recent")
    sidebar.contact_sort.setCurrentIndex(1)  # "recent"
    items_recent = [sidebar.contact_list.item(i).text() for i in range(sidebar.contact_list.count())]
    # Favorites at top sorted newest first: Alpha (12:00) then Bravo (10:00)
    assert "Alpha" in items_recent[0] and "★" in items_recent[0]
    assert "Bravo" in items_recent[1] and "★" in items_recent[1]
    # Non-favorites below sorted newest first: Zulu (14:00) then Charlie (08:00)
    assert "Zulu" in items_recent[2]
    assert "Charlie" in items_recent[3]

    # 3. Test Search Filtering
    sidebar.contact_search.setText("bra")
    filtered_items = [sidebar.contact_list.item(i).text() for i in range(sidebar.contact_list.count())]
    assert len(filtered_items) == 1
    assert "Bravo" in filtered_items[0]

    # Clear search
    sidebar.contact_search.clear()
    assert sidebar.contact_list.count() == 4


def test_sidebar_vertical_splitter(qapp, tmp_path):
    """Verifies that QSplitter exists and channels/contacts are resizable."""
    storage = Storage(tmp_path / "splitter_test.db")
    sidebar = Sidebar(storage=storage)

    assert hasattr(sidebar, "splitter")
    assert sidebar.splitter.orientation() == Qt.Orientation.Vertical
    assert sidebar.splitter.count() == 2
    assert not sidebar.splitter.childrenCollapsible()


def test_sidebar_dedicated_sort_buttons(qapp, tmp_path):
    """Verifies that dedicated sort buttons toggle sorting mode and update styles."""
    storage = Storage(tmp_path / "sort_buttons_test.db")
    sidebar = Sidebar(storage=storage)

    assert hasattr(sidebar, "btn_sort_alpha")
    assert hasattr(sidebar, "btn_sort_recent")

    # Click Recent button
    sidebar.btn_sort_recent.click()
    assert sidebar.contact_sort.currentIndex() == 1
    assert sidebar.btn_sort_recent.isChecked()
    assert not sidebar.btn_sort_alpha.isChecked()

    # Click Alpha button
    sidebar.btn_sort_alpha.click()
    assert sidebar.contact_sort.currentIndex() == 0
    assert sidebar.btn_sort_alpha.isChecked()
    assert not sidebar.btn_sort_recent.isChecked()


def test_main_window_horizontal_splitter(qapp, tmp_path):
    """Verifies that main horizontal splitter is non-collapsible and enforces minimum widths."""
    storage = Storage(tmp_path / "main_splitter_test.db")
    config = AppConfig()
    from unittest.mock import patch
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage)

    assert hasattr(win, "main_splitter")
    assert win.main_splitter.orientation() == Qt.Orientation.Horizontal
    assert not win.main_splitter.childrenCollapsible()
    assert win.sidebar.minimumWidth() >= 180
    assert win.center_stack.minimumWidth() >= 320
    assert win.pixoo_panel.minimumWidth() >= 200


@pytest.mark.asyncio
async def test_meshcore_driver_repeater_commands_and_responses(tmp_path):
    """Verifies that MeshCoreDriver dispatches repeater commands and handles CLI responses."""
    from unittest.mock import AsyncMock, MagicMock
    from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
    from meshcore_tray.core.event_bus import bus, EventType

    storage = Storage(tmp_path / "rep_cmd_test.db")
    config = AppConfig()
    contact = NodeContact(
        node_id="d79870ac338f",
        alias="M7NCY West Yagi v4",
        public_key="d79870ac338fd70fbed09bde2c13d050edd4e1b0cbf12370d1976c588a5e2cfe",
        is_repeater=True
    )
    storage.save_contact(contact)

    driver = MeshCoreDriver(config=config, storage=storage)
    mock_client = MagicMock()
    mock_client.commands = MagicMock()
    mock_client.commands.send_login_sync = AsyncMock(return_value=None)
    mock_client.commands.req_status_sync = AsyncMock(return_value={"bat": 4200, "uptime": 86400})
    mock_client.commands.req_neighbours_sync = AsyncMock(return_value={"neighbours": []})
    mock_client.commands.send_path_discovery_sync = AsyncMock(return_value=None)
    mock_client.commands.send_cmd = AsyncMock()
    driver.client = mock_client
    driver.is_connected = MagicMock(return_value=True)

    # 1. Send passwordless login
    await driver._async_send_repeater_cmd(contact.public_key, "!login", contact.node_id)
    assert mock_client.commands.send_login_sync.await_count == 1

    # 2. Send status request
    await driver._async_send_repeater_cmd(contact.public_key, "!status", contact.node_id)
    assert mock_client.commands.req_status_sync.await_count == 1

    # 3. Test CLI Reply event handling
    received_msgs = []
    bus.subscribe(EventType.MESSAGE_RECEIVED, lambda m: received_msgs.append(m))

    driver._handle_cli_reply({"text": "Uptime: 3 days, battery=4.1V", "src": "d79870ac338f"})
    assert len(received_msgs) == 1
    assert "Uptime: 3 days" in received_msgs[0].text
    assert received_msgs[0].metadata.get("is_repeater_response") is True


def test_sidebar_channel_unread_count_and_bold(qapp, tmp_path):
    """Verifies that channels with unread messages show bold font and (x) count, cleared on active."""
    storage = Storage(tmp_path / "unread_sidebar_test.db")
    config = AppConfig()

    storage.save_channel(ChannelInfo(channel_id=0, name="Public", is_favorite=False, is_pixoo_enabled=True))
    storage.save_channel(ChannelInfo(channel_id=1, name="Ops", is_favorite=False, is_pixoo_enabled=True))

    sidebar = Sidebar(storage=storage, config=config)
    sidebar.set_active_channel("Public")

    # Initially, 0 unread
    ops_item = None
    for i in range(sidebar.channel_list.count()):
        it = sidebar.channel_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == "Ops":
            ops_item = it
            break
    assert ops_item is not None
    assert ops_item.text() == "#Ops"
    assert not ops_item.font().bold()

    # Add 2 incoming unread messages to Ops
    storage.save_message(MessageEnvelope(id="m_op1", channel="Ops", text="Ops msg 1", is_outgoing=False))
    storage.save_message(MessageEnvelope(id="m_op2", channel="Ops", text="Ops msg 2", is_outgoing=False))

    sidebar.reload()
    for i in range(sidebar.channel_list.count()):
        it = sidebar.channel_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == "Ops":
            ops_item = it
            break
    assert ops_item.text() == "#Ops (2)"
    assert ops_item.font().bold()

    # Now user switches to Ops channel
    sidebar.set_active_channel("Ops")
    storage.mark_as_read("chan:Ops", "m_op2", "2026-09-03T18:00:00Z")
    sidebar.reload()

    for i in range(sidebar.channel_list.count()):
        it = sidebar.channel_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == "Ops":
            ops_item = it
            break
    assert ops_item.text() == "#Ops"
    assert not ops_item.font().bold()
