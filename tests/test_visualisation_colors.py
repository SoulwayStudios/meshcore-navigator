import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch
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


def test_settings_dialog_visualisation_colors_grouping(app):
    """Verifies that route visualisation colors have their own dedicated card grouping
    in SettingsDialog with all 5 configurable colors: known, heading, phantom, unknown, no_gps."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()
        config.app_colors.map_visualised_path_color = "#112233"
        config.app_colors.map_visualised_heading_color = "#223344"
        config.app_colors.map_phantom_path_color = "#334455"
        config.app_colors.map_unknown_path_color = "#445566"
        config.app_colors.map_no_gps_path_color = "#556677"

        dialog = SettingsDialog(config=config, storage=storage)

        # Check all 5 color picker buttons exist
        assert hasattr(dialog, "btn_col_visualised_path")
        assert hasattr(dialog, "btn_col_visualised_heading")
        assert hasattr(dialog, "btn_col_phantom_path")
        assert hasattr(dialog, "btn_col_unknown_path")
        assert hasattr(dialog, "btn_col_no_gps_path")

        assert dialog.btn_col_visualised_path.current_hex.upper() == "#112233"
        assert dialog.btn_col_visualised_heading.current_hex.upper() == "#223344"
        assert dialog.btn_col_phantom_path.current_hex.upper() == "#334455"
        assert dialog.btn_col_unknown_path.current_hex.upper() == "#445566"
        assert dialog.btn_col_no_gps_path.current_hex.upper() == "#556677"

        # Change colors via pickers and apply
        dialog.btn_col_visualised_path.current_hex = "#AA00AA"
        dialog.btn_col_visualised_heading.current_hex = "#BB00BB"
        dialog.btn_col_phantom_path.current_hex = "#FFFF11"
        dialog.btn_col_unknown_path.current_hex = "#FF1111"
        dialog.btn_col_no_gps_path.current_hex = "#111111"

        dialog._apply_settings(close_on_finish=False)

        assert config.app_colors.map_visualised_path_color == "#AA00AA"
        assert config.app_colors.map_visualised_heading_color == "#BB00BB"
        assert config.app_colors.map_phantom_path_color == "#FFFF11"
        assert config.app_colors.map_unknown_path_color == "#FF1111"
        assert config.app_colors.map_no_gps_path_color == "#111111"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_visualised_path_custom_unknown_and_no_gps_colors(app):
    """Verifies that custom unknown_path_color and no_gps_path_color set in config
    are applied to route segments in visualise_message_path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path)
        config = AppConfig()
        config.meshcore.node_alias = "M7NCY"
        config.meshcore.latitude = 54.65897
        config.meshcore.longitude = -3.4346
        # Custom user-defined colors:
        config.app_colors.map_visualised_path_color = "#00E5FF" # Cyan for known
        config.app_colors.map_unknown_path_color = "#FF3366"    # Coral red for unknown
        config.app_colors.map_no_gps_path_color = "#333333"     # Dark grey for no-gps

        # 1. Sender
        sender = NodeContact("node_custom_send", "SenderNode", latitude=53.48, longitude=-2.24)
        # 2. Known repeater with GPS
        rep1 = NodeContact("rep_with_gps", "RptrGPS", latitude=54.10, longitude=-2.90, is_repeater=True)
        # 3. Known repeater without GPS
        rep2_no_gps = NodeContact("rep_no_gps", "RptrNoGPS", latitude=None, longitude=None, is_repeater=True)

        storage.save_contact(sender)
        storage.save_contact(rep1)
        storage.save_contact(rep2_no_gps)

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            widget = MeshMapWidget(storage=storage, config=config)
        widget.web_view = MagicMock()
        widget._page_ready = True

        # Path: Sender -> rep1 (known GPS) -> rep2_no_gps (no GPS) -> unk_hop3 (unknown) -> Home
        msg = MessageEnvelope(
            id="m_custom_colors",
            sender_id="node_custom_send",
            sender_name="SenderNode",
            channel="Public",
            text="Custom colors test",
            is_outgoing=False,
            metadata={"repeaters": ["rep_with_gps", "rep_no_gps", "unk_hop3"]}
        )
        widget.visualise_message_path(msg)

        assert widget.web_view.page().runJavaScript.called
        call_arg = widget.web_view.page().runJavaScript.call_args[0][0]

        # Check that segments use custom colors
        assert '"color": "#00E5FF"' in call_arg # Sender to rep1
        assert '"unknown_color": "#FF3366"' in call_arg
        assert '"no_gps_color": "#333333"' in call_arg
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
