"""Unit tests verifying DMs, Contacts list, and Repeaters UI cleanup and map centering."""

from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact
from meshcore_tray.storage import Storage
from meshcore_tray.ui.dms_view import DMsViewWidget, ContactItemWidget
from meshcore_tray.ui.repeaters_view import RepeatersViewWidget, RepeaterRowWidget
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(["meshcore-test"])


def test_dms_view_row_height_and_no_id_visible(app, tmp_path):
    """Verifies that ContactItemWidget is at least 50px tall, does not show raw hex ID, and shows last seen."""
    storage = Storage(tmp_path / "dms_test.db")
    contact = NodeContact(
        node_id="47016b27b221",
        alias="M7NCY Mobile",
        is_favorite=True,
        last_seen="2026-09-04T14:20:00+00:00"
    )
    storage.save_contact(contact)

    dms_view = DMsViewWidget(storage=storage, config=AppConfig())
    assert dms_view.contact_list.count() > 0

    # Find the item widget
    item = None
    widget = None
    for i in range(dms_view.contact_list.count()):
        it = dms_view.contact_list.item(i)
        w = dms_view.contact_list.itemWidget(it)
        if isinstance(w, ContactItemWidget):
            item = it
            widget = w
            break

    assert widget is not None
    # Verify height is at least 50px so square badge is never cut off
    assert widget.height() >= 50 or widget.sizeHint().height() >= 50 or item.sizeHint().height() >= 50
    assert widget.badge.height() == 36

    # Verify raw ID is NOT shown in username or sub label
    assert "47016b27" not in widget.name_lbl.text()
    assert "47016b27" not in widget.sub_lbl.text()
    assert "ID:" not in widget.sub_lbl.text()

    # Verify last seen is displayed in DD/MM/YY HH:MM format
    assert "Last seen:" in widget.sub_lbl.text()
    assert "04/09/26 14:20" in widget.sub_lbl.text()

    # Verify star is gold (#FFD700)
    assert not widget.star_lbl.isHidden()
    assert "★" in widget.star_lbl.text()
    assert "#FFD700" in widget.star_lbl.styleSheet()


def test_dms_view_search_box_and_sort_buttons(app, tmp_path):
    """Verifies search box has magnifying glass and sort buttons work."""
    storage = Storage(tmp_path / "dms_sort_test.db")
    storage.save_contact(NodeContact("id1", "Bravo", last_seen="2026-09-01T10:00:00"))
    storage.save_contact(NodeContact("id2", "Alpha", last_seen="2026-09-05T10:00:00"))

    dms_view = DMsViewWidget(storage=storage, config=AppConfig())

    # Check search placeholder
    assert "🔍" in dms_view.search_input.placeholderText()

    # Default sort is alpha -> Alpha should appear before Bravo
    items_alpha = [
        dms_view.contact_list.itemWidget(dms_view.contact_list.item(i)).name_lbl.text()
        for i in range(dms_view.contact_list.count())
        if isinstance(dms_view.contact_list.itemWidget(dms_view.contact_list.item(i)), ContactItemWidget)
    ]
    assert items_alpha[0] == "Alpha"
    assert items_alpha[1] == "Bravo"

    # Click Recent sort -> Bravo has older timestamp than Alpha
    dms_view.btn_sort_recent.click()
    assert dms_view.sort_mode == "recent"
    items_recent = [
        dms_view.contact_list.itemWidget(dms_view.contact_list.item(i)).name_lbl.text()
        for i in range(dms_view.contact_list.count())
        if isinstance(dms_view.contact_list.itemWidget(dms_view.contact_list.item(i)), ContactItemWidget)
    ]
    assert items_recent[0] == "Alpha"


def test_repeaters_view_row_height_and_no_id_visible(app, tmp_path):
    """Verifies that RepeatersView rows are tall, show last seen, and do not show raw hex ID."""
    storage = Storage(tmp_path / "rep_test.db")
    rep = NodeContact(
        node_id="627c8262beef",
        alias="Skiddaw Peak [Rep]",
        is_repeater=True,
        snr_db=12.5,
        last_seen="2026-09-05T15:30:00+00:00"
    )
    storage.save_contact(rep)

    rep_view = RepeatersViewWidget(storage=storage, config=AppConfig())
    assert rep_view.repeater_list.count() > 0

    widget = None
    item = None
    for i in range(rep_view.repeater_list.count()):
        it = rep_view.repeater_list.item(i)
        w = rep_view.repeater_list.itemWidget(it)
        if isinstance(w, RepeaterRowWidget):
            widget = w
            item = it
            break

    assert widget is not None
    assert widget.height() >= 50 or widget.sizeHint().height() >= 50 or item.sizeHint().height() >= 50
    assert "627c8262" not in widget.name_lbl.text()
    assert "627c8262" not in widget.sub_lbl.text()
    assert "ID:" not in widget.sub_lbl.text()
    assert "Last seen:" in widget.sub_lbl.text()
    assert "05/09/26 15:30" in widget.sub_lbl.text()
    assert "SNR: +12.5dB" in widget.sub_lbl.text()
    assert "🔍" in rep_view.search_input.placeholderText()
    assert "Discover / Add" in rep_view.btn_add_repeater.text()
    assert rep_view.btn_sort_alpha.isChecked() is True
    assert rep_view.btn_sort_recent.isChecked() is False

    # Add second repeater and test sorting
    rep2 = NodeContact(
        node_id="aaaa11112222",
        alias="Alpha Peak [Rep]",
        is_repeater=True,
        last_seen="2026-09-01T10:00:00+00:00"
    )
    storage.save_contact(rep2)
    rep_view.reload_repeaters()

    # Alpha should be first in alpha sort
    rep_items = [
        rep_view.repeater_list.itemWidget(rep_view.repeater_list.item(i)).name_lbl.text()
        for i in range(rep_view.repeater_list.count())
        if isinstance(rep_view.repeater_list.itemWidget(rep_view.repeater_list.item(i)), RepeaterRowWidget)
    ]
    assert rep_items[0] == "Alpha Peak"
    assert rep_items[1] == "Skiddaw Peak"

    # Click Recent sort -> Skiddaw Peak has more recent timestamp
    rep_view.btn_sort_recent.click()
    assert rep_view.sort_mode == "recent"
    rep_items_recent = [
        rep_view.repeater_list.itemWidget(rep_view.repeater_list.item(i)).name_lbl.text()
        for i in range(rep_view.repeater_list.count())
        if isinstance(rep_view.repeater_list.itemWidget(rep_view.repeater_list.item(i)), RepeaterRowWidget)
    ]
    assert rep_items_recent[0] == "Skiddaw Peak"
    assert rep_items_recent[1] == "Alpha Peak"


def test_main_window_sidebar_contacts_hidden(app, tmp_path):
    """Verifies that MainWindow sidebar hides contacts list on the Main Chat & Map page."""
    storage = Storage(tmp_path / "win_side_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=storage)

    # The sidebar in MainWindow should have show_contacts=False and contact_container not visible
    assert win.sidebar.show_contacts is False
    assert win.sidebar.contact_container.isVisible() is False


def test_mesh_map_center_on_node(app, tmp_path):
    """Verifies MeshMapWidget.center_on_node properly executes JavaScript or updates status."""
    storage = Storage(tmp_path / "map_center_test.db")
    c = NodeContact(node_id="node123", alias="Keswick Base", latitude=54.60, longitude=-3.13)
    storage.save_contact(c)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=storage, config=AppConfig())

    map_widget.web_view = MagicMock()
    map_widget._page_ready = True

    # Center on existing node
    res = map_widget.center_on_node("node123")
    assert res is True
    assert "Keswick Base" in map_widget.watcher_status.text()

    # Center on node without coordinates
    storage.save_contact(NodeContact(node_id="nogps", alias="No GPS Node"))
    res_fail = map_widget.center_on_node("nogps")
    assert res_fail is False
    assert "no GPS coordinates" in map_widget.watcher_status.text()


def test_contact_quoted_name_and_avatar(app, tmp_path):
    """Verifies that names with quotes or &quot; are properly decoded and avatar is first letter, not quote."""
    from meshcore_tray.ui.dms_view import get_letter_avatar
    assert get_letter_avatar('"BGT"') == "B"
    assert get_letter_avatar("&quot;BGT&quot;") == "B"
    assert get_letter_avatar("'Echo'") == "E"

    contact = NodeContact(node_id="bgt1", alias='&quot;BGT&quot;')
    widget = ContactItemWidget(contact=contact, is_favorite=False)
    assert widget.name_lbl.text() == '"BGT"'
    assert widget.badge.property("letter") == "B" or widget.badge.text() == "B"
    assert widget.badge.pixmap() is not None
    assert widget.star_lbl.isHidden()


def test_composer_emoji_button_not_cut_off(app):
    """Verifies that the composer emoji button is sized appropriately with zero padding."""
    from meshcore_tray.ui.composer import PowerComposer
    composer = PowerComposer()
    assert composer.emoji_btn.width() >= 38
    assert "padding: 0px;" in composer.emoji_btn.styleSheet()


def test_message_bubble_labels_transparent(app, tmp_path):
    """Verifies that MessageBubble labels have transparent backgrounds so they match the bubble."""
    from meshcore_tray.core.models import MessageEnvelope
    from meshcore_tray.ui.chat_widget import MessageBubble
    msg = MessageEnvelope(
        id="msg1",
        channel="#public",
        sender_id="m7ncy",
        sender_name="M7NCY",
        text="Test message transparency",
        timestamp="2026-09-05T15:16:00+00:00"
    )
    bubble = MessageBubble(msg=msg)
    # Check that MessageBubble specifies QLabel transparency in stylesheet
    assert "background: transparent" in bubble.styleSheet()
    # Check child labels have WA_TranslucentBackground set
    from PyQt6.QtWidgets import QLabel
    labels = bubble.findChildren(QLabel)
    assert len(labels) >= 2
    for lbl in labels:
        if "badge" not in lbl.objectName() and "alert" not in lbl.text().lower():
            assert lbl.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True

