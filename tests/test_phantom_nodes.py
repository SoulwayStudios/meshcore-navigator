import os
import tempfile
import pytest
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import MessageEnvelope, NodeContact
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.settings_widget import SettingsDialog


@pytest.fixture
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app_instance = QApplication.instance()
    if app_instance is None:
        app_instance = QApplication([])
    return app_instance


def test_storage_phantom_nodes_crud():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)

        # Initially false
        assert not storage.is_phantom_node("!ac981234", "ac98 RPTR")
        assert not storage.is_phantom_node("!abcd9999", "MaaS")

        # Mark phantom node
        assert storage.mark_phantom_node("!ac981234", "ac98 RPTR")
        assert storage.is_phantom_node("!ac981234", "ac98 RPTR")
        # Prefix match
        assert storage.is_phantom_node("ac98")
        assert storage.is_phantom_node("!ac98")
        assert storage.is_phantom_node("", "ac98 RPTR")

        assert storage.mark_phantom_node("!12345678", "MaaS")
        assert storage.is_phantom_node("!12345678")
        assert storage.is_phantom_node("", "MaaS")

        all_phantoms = storage.get_all_phantom_nodes()
        assert len(all_phantoms) == 2
        aliases = [p["alias"] for p in all_phantoms]
        assert "ac98 RPTR" in aliases
        assert "MaaS" in aliases

        # Unmark one
        assert storage.unmark_phantom_node("!ac981234")
        assert not storage.is_phantom_node("!ac981234", "ac98 RPTR")
        assert storage.is_phantom_node("", "MaaS")

        # Clear all
        storage.clear_all_phantom_nodes()
        assert len(storage.get_all_phantom_nodes()) == 0
        assert not storage.is_phantom_node("", "MaaS")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_phantom_node_removes_from_map_and_colors_line_yellow(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()
        config.app_colors.map_visualised_path_color = "#FF00FF"
        config.app_colors.map_phantom_path_color = "#FFFF00"
        config.meshcore.latitude = 54.65897
        config.meshcore.longitude = -3.4346
        config.meshcore.node_alias = "M7NCY"

        # Sender in Sheffield
        sender = NodeContact(
            node_id="!11111111",
            alias="SheffieldSender",
            latitude=53.3811,
            longitude=-1.4701,
            is_repeater=False
        )
        storage.save_contact(sender)

        # Hop 1: Normal repeater in Manchester
        r1 = NodeContact(
            node_id="!bb112233",
            alias="MCR-Repeater",
            latitude=53.4808,
            longitude=-2.2426,
            is_repeater=True
        )
        storage.save_contact(r1)

        # Hop 2: ac98 RPTR in Northern Ireland (collision with local node)
        r2 = NodeContact(
            node_id="!ac985566",
            alias="ac98 RPTR",
            latitude=54.5973,
            longitude=-5.9301,
            is_repeater=True
        )
        storage.save_contact(r2)

        # Mark ac98 RPTR as phantom node
        storage.mark_phantom_node("!ac985566", "ac98 RPTR")

        from unittest.mock import patch
        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            widget = MeshMapWidget(storage=storage, config=config)

        # Create message with path: sender -> bb (Manchester) -> ac98 (NI phantom) -> home
        msg = MessageEnvelope(
            id="msg-phantom-test",
            sender_id="!11111111",
            sender_name="SheffieldSender",
            channel="Public",
            text="Testing phantom node routing",
            metadata={"repeaters": ["bb", "ac98"]}
        )

        widget.visualise_message_path(msg)

        # Verify repeaters_info:
        # bb is known, has coords, not phantom
        # ac98 is marked as phantom: lat=None, lon=None, is_phantom=True
        assert hasattr(widget, "_active_visualise_msg")
        assert widget._active_visualise_msg.id == "msg-phantom-test"

        # Check chain resolution
        chain = storage.resolve_hop_chain_with_candidates(
            ["bb", "ac98"],
            sender_coord=(53.3811, -1.4701),
            home_coord=(54.65897, -3.4346)
        )
        assert len(chain) == 2
        assert chain[0]["contact"].alias == "MCR-Repeater"
        assert not chain[0]["is_phantom"]
        assert chain[1]["contact"].alias == "ac98 RPTR"
        assert chain[1]["is_phantom"]

        # Call toggle handler
        widget._on_phantom_node_toggled("!ac985566", "ac98 RPTR", False)
        # Should now be unmarked
        assert not storage.is_phantom_node("!ac985566", "ac98 RPTR")

        # Toggle it back on
        widget._on_phantom_node_toggled("!ac985566", "ac98 RPTR", True)
        assert storage.is_phantom_node("!ac985566", "ac98 RPTR")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_settings_dialog_phantom_nodes_card_and_color_picker(app):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        storage.mark_phantom_node("!ac985566", "ac98 RPTR")
        storage.mark_phantom_node("!99990000", "MaaS")

        config = AppConfig()
        config.app_colors.map_phantom_path_color = "#FACC15"

        dialog = SettingsDialog(config=config, storage=storage)

        # Check color picker button exists
        assert hasattr(dialog, "btn_col_phantom_path")
        assert dialog.btn_col_phantom_path.current_hex.upper() == "#FACC15"

        # Check phantom nodes card exists and has the marked nodes
        assert hasattr(dialog, "card_phantom_nodes")
        phantoms = storage.get_all_phantom_nodes()
        assert len(phantoms) == 2

        # Unmark one via dialog callback
        dialog._on_delete_phantom_node("!ac985566", "ac98 RPTR")
        assert not storage.is_phantom_node("!ac985566", "ac98 RPTR")
        assert storage.is_phantom_node("!99990000", "MaaS")

        # Change color and apply
        dialog.btn_col_phantom_path.current_hex = "#EAB308"
        dialog.btn_col_phantom_path._update_swatch()
        dialog._apply_settings(close_on_finish=False)
        assert config.app_colors.map_phantom_path_color == "#EAB308"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
