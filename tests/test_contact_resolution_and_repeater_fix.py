"""Tests for contact resolution, repeater disambiguation, and direct message routing."""

import pytest
from unittest.mock import MagicMock
from meshcore_tray.core.models import NodeContact
from meshcore_tray.storage import Storage


@pytest.fixture
def temp_storage(tmp_path):
    db_path = tmp_path / "test_contacts.db"
    return Storage(db_path=db_path)


def test_contact_resolution_companion_vs_repeater(temp_storage):
    """Verifies that companion nodes with similar names to repeaters are never shadowed by substring matching."""
    # 1. Insert a repeater whose alias contains "M7NCY"
    repeater = NodeContact(
        node_id="DD2DF7A117C6",
        alias="M7NCY West Yagi",
        is_repeater=True,
        is_favorite=True,
        latitude=54.6588,
        longitude=-3.4344
    )
    temp_storage.save_contact(repeater)

    # 2. Insert the actual companion node with exact alias "M7NCY"
    companion = NodeContact(
        node_id="!M7NCY",
        alias="M7NCY",
        is_repeater=False,
        is_favorite=False
    )
    temp_storage.save_contact(companion)

    # 3. Lookup "!M7NCY" -> must resolve to the companion, NOT the repeater
    c1 = temp_storage.get_contact("!M7NCY")
    assert c1 is not None
    assert c1.node_id == "!M7NCY"
    assert c1.alias == "M7NCY"
    assert c1.is_repeater is False

    # 4. Lookup "M7NCY" -> must resolve to the companion
    c2 = temp_storage.get_contact("M7NCY")
    assert c2 is not None
    assert c2.node_id == "!M7NCY"
    assert c2.is_repeater is False

    # 5. Lookup the repeater explicitly -> must resolve to the repeater
    c3 = temp_storage.get_contact("DD2DF7A117C6")
    assert c3 is not None
    assert c3.alias == "M7NCY West Yagi"
    assert c3.is_repeater is True

    c4 = temp_storage.get_contact("M7NCY West Yagi")
    assert c4 is not None
    assert c4.node_id == "DD2DF7A117C6"
    assert c4.is_repeater is True


def test_rook_and_tron_substring_isolation(temp_storage):
    """Verifies that Rook does not match Clayton Brook and T_R_O_N does not match arbitrary SQL underscores."""
    # Add repeaters with names that would trigger loose substring or wildcard matches
    brook_repeater = NodeContact(
        node_id="59ecb1e32a3a",
        alias="Clayton Brook Repeater",
        is_repeater=True
    )
    chester_repeater = NodeContact(
        node_id="01deadcc5495",
        alias="JT Chester omni CH2",
        is_repeater=True
    )
    temp_storage.save_contact(brook_repeater)
    temp_storage.save_contact(chester_repeater)

    # Add companion contacts
    rook_companion = NodeContact(
        node_id="!Rook",
        alias="Rook",
        is_repeater=False
    )
    tron_companion = NodeContact(
        node_id="!T_R_O_N",
        alias="T_R_O_N",
        is_repeater=False
    )
    temp_storage.save_contact(rook_companion)
    temp_storage.save_contact(tron_companion)

    # Resolution checks
    res_rook = temp_storage.get_contact("!Rook")
    assert res_rook is not None
    assert res_rook.node_id == "!Rook"
    assert res_rook.is_repeater is False

    res_tron = temp_storage.get_contact("!T_R_O_N")
    assert res_tron is not None
    assert res_tron.node_id == "!T_R_O_N"
    assert res_tron.is_repeater is False


def test_set_contact_repeater_status(temp_storage):
    """Verifies that set_contact_repeater_status toggles is_repeater cleanly."""
    contact = NodeContact(
        node_id="!testnode",
        alias="TestNode",
        is_repeater=True
    )
    temp_storage.save_contact(contact)

    # Verify initial state
    c = temp_storage.get_contact("!testnode")
    assert c.is_repeater is True

    # Demote to companion
    ok = temp_storage.set_contact_repeater_status("!testnode", False)
    assert ok is True
    c_updated = temp_storage.get_contact("!testnode")
    assert c_updated.is_repeater is False

    # Promote back to repeater
    ok2 = temp_storage.set_contact_repeater_status("!testnode", True)
    assert ok2 is True
    c_updated2 = temp_storage.get_contact("!testnode")
    assert c_updated2.is_repeater is True


def test_save_contact_with_update_role(temp_storage):
    """Verifies that save_contact(..., update_role=True) can unset is_repeater to False."""
    contact = NodeContact(
        node_id="node12345678",
        alias="MyNode",
        is_repeater=True
    )
    temp_storage.save_contact(contact)
    assert temp_storage.get_contact("node12345678").is_repeater is True

    # Save with is_repeater=False and update_role=True
    contact.is_repeater = False
    temp_storage.save_contact(contact, update_role=True)
    assert temp_storage.get_contact("node12345678").is_repeater is False


def test_main_window_dm_requested_routes_to_dm_view():
    """Verifies MainWindow._on_dm_requested sets center stack to index 0 and selects target."""
    from meshcore_tray.ui.main_window import MainWindow
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication.instance() or QApplication(sys.argv)

    mw = MainWindow.__new__(MainWindow)
    mw.storage = MagicMock()
    mw.current_channel = "#test"
    mw.current_dm = None
    mw.center_stack = MagicMock()
    mw.chat_widget = MagicMock()
    mw.composer = MagicMock()
    mw.dms_view = MagicMock()

    mock_contact = NodeContact(node_id="!M7NCY", alias="M7NCY", is_repeater=False)
    mw.storage.get_contact.return_value = mock_contact

    # Trigger _on_dm_requested
    MainWindow._on_dm_requested(mw, "!M7NCY")

    assert mw.current_dm == "!M7NCY"
    mw.chat_widget.set_target.assert_called_once_with("#test", "!M7NCY")
    mw.composer.set_active_target.assert_called_once_with("#test", "!M7NCY")
    mw.center_stack.setCurrentIndex.assert_called_once_with(0)
    mw.dms_view.select_contact.assert_called_once_with("!M7NCY")
