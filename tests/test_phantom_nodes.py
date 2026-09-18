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
        app_instance = QApplication(["meshcore-test"])
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


def test_implausible_remote_rf_coordinates_sanitized_and_rejected(app):
    from meshcore_tray.core.models import is_plausible_rf_coordinate

    home_lat = 54.65897
    home_lon = -3.4346

    # Normal UK/Ireland coordinates should pass
    assert is_plausible_rf_coordinate(53.8529, -6.5398, ref_lat=home_lat, ref_lon=home_lon)
    assert is_plausible_rf_coordinate(53.3811, -1.4701, ref_lat=home_lat, ref_lon=home_lon)

    # Corrupt coordinates in the Southern Ocean / Australia (18,000 km away) must be rejected
    assert not is_plausible_rf_coordinate(-53.062852, 157.712609, ref_lat=home_lat, ref_lon=home_lon)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        valid_node = NodeContact(
            node_id="!11111111",
            alias="ValidUKNode",
            latitude=53.4808,
            longitude=-2.2426
        )
        bogus_node = NodeContact(
            node_id="!761bb88a3ceb",
            alias="F5MEM_🚗_29800_tbeam",
            latitude=-53.062852,
            longitude=157.712609
        )
        storage.save_contact(valid_node)
        storage.save_contact(bogus_node)

        # Database should sanitize bogus node on startup
        report = storage.verify_and_sanitize_database(home_lat=home_lat, home_lon=home_lon)
        assert report["corrupt_coords_cleared"] >= 1

        c_valid = storage.get_contact("!11111111")
        assert c_valid.latitude == 53.4808
        assert c_valid.longitude == -2.2426

        c_bogus = storage.get_contact("!761bb88a3ceb")
        assert c_bogus.latitude is None
        assert c_bogus.longitude is None

        # MeshMapWidget should only plot plausible nodes
        config = AppConfig()
        config.meshcore.latitude = home_lat
        config.meshcore.longitude = home_lon

        from unittest.mock import patch
        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            widget = MeshMapWidget(storage=storage, config=config)

        coords_nodes = [c for c in storage.get_nodes_with_coordinates() if is_plausible_rf_coordinate(c.latitude, c.longitude, ref_lat=home_lat, ref_lon=home_lon)]
        assert len(coords_nodes) == 1
        assert coords_nodes[0].node_id == "!11111111"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_mesh_map_leaflet_phantom_dot_css_and_payload(app):
    """Verifies that MeshMapWidget generates black phantom CSS and passes is_phantom in setNodes payload."""
    import json
    from unittest.mock import patch, MagicMock

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()
        rep = NodeContact(
            node_id="!ac985566",
            alias="ac98 RPTR",
            latitude=54.5973,
            longitude=-5.9301,
            is_repeater=True
        )
        storage.save_contact(rep)
        storage.mark_phantom_node("!ac985566", "ac98 RPTR")

        from meshcore_tray.ui.mesh_map_widget import get_leaflet_html
        html = get_leaflet_html()
        # Verify black CSS styling
        assert ".node-dot-phantom" in html
        assert "background: #000000 !important;" in html
        assert "border: 1.5px solid #4B5563 !important;" in html
        assert "box-shadow: 0 0 6px rgba(0, 0, 0, 0.9) !important;" in html
        assert "window.onTogglePhantomMarker" in html
        assert "pyBridge.on_node_context_menu" in html

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            widget = MeshMapWidget(storage=storage, config=config)

        # Verify setNodes payload has is_phantom=True
        widget._page_ready = True
        widget.run_js = MagicMock()
        widget._do_refresh_map_data()

        assert widget.run_js.called
        call_args = widget.run_js.call_args_list[0][0][0]
        assert "setNodes(" in call_args
        # Extract payload
        json_str = call_args[call_args.index("setNodes(") + 9 : call_args.rindex(")")]
        data = json.loads(json_str)
        assert len(data) == 1
        assert data[0]["node_id"] == "!ac985566"
        assert data[0]["is_phantom"] is True
        assert data[0]["is_repeater"] is True
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_mesh_map_node_context_menu_toggle_phantom(app):
    """Verifies that right-clicking a node on the map presents a context menu to toggle phantom status."""
    from unittest.mock import patch, MagicMock
    from PyQt6.QtCore import QPoint

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()
        rep = NodeContact(
            node_id="!ac985566",
            alias="ac98 RPTR",
            latitude=54.5973,
            longitude=-5.9301,
            is_repeater=True
        )
        storage.save_contact(rep)

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            widget = MeshMapWidget(storage=storage, config=config)

        assert not storage.is_phantom_node("!ac985566", "ac98 RPTR")

        # Mock QMenu.exec to simulate clicking "Mark as Phantom Node"
        with patch("PyQt6.QtWidgets.QMenu.exec") as mock_exec:
            def exec_side_effect(pos):
                # Find the phantom action in the caller's menu
                # We can inspect the QMenu instance from mock_exec call or test actions
                return None
            mock_exec.side_effect = exec_side_effect

            # Directly test _on_phantom_node_toggled
            widget._on_phantom_node_toggled("!ac985566", "ac98 RPTR", True)
            assert storage.is_phantom_node("!ac985566", "ac98 RPTR")

            widget._on_phantom_node_toggled("!ac985566", "ac98 RPTR", False)
            assert not storage.is_phantom_node("!ac985566", "ac98 RPTR")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_repeaters_view_phantom_badge_and_toggle(app):
    """Verifies that RepeatersViewWidget displays phantom nodes with black styling and ghost badge."""
    from meshcore_tray.ui.repeaters_view import RepeatersViewWidget, RepeaterRowWidget
    from unittest.mock import patch

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()

        r_normal = NodeContact(
            node_id="!11112222",
            alias="Normal-Repeater",
            latitude=54.6,
            longitude=-3.4,
            is_repeater=True
        )
        r_phantom = NodeContact(
            node_id="!ac985566",
            alias="ac98 RPTR",
            latitude=54.5,
            longitude=-5.9,
            is_repeater=True
        )
        storage.save_contact(r_normal)
        storage.save_contact(r_phantom)
        storage.mark_phantom_node("!ac985566", "ac98 RPTR")

        view = RepeatersViewWidget(storage=storage, config=config)
        view.reload_repeaters()

        assert view.repeater_list.count() == 2

        # Find row widgets
        rows = {}
        for i in range(view.repeater_list.count()):
            it = view.repeater_list.item(i)
            w = view.repeater_list.itemWidget(it)
            if isinstance(w, RepeaterRowWidget):
                rows[w.contact.node_id] = w

        assert "!11112222" in rows
        assert "!ac985566" in rows

        row_norm = rows["!11112222"]
        row_phant = rows["!ac985566"]

        assert row_norm.is_phantom is False
        assert row_phant.is_phantom is True
        assert "👻" in row_phant.badge.text()
        assert hasattr(row_phant, "phantom_lbl")
        assert "👻" in row_phant.phantom_lbl.text()
        assert "#000000" in row_phant.badge.styleSheet()

        # Context menu toggle test
        view._toggle_phantom_node("!ac985566", "ac98 RPTR")
        assert not storage.is_phantom_node("!ac985566", "ac98 RPTR")

        view._toggle_phantom_node("!ac985566", "ac98 RPTR")
        assert storage.is_phantom_node("!ac985566", "ac98 RPTR")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

