"""Unit tests for Channel grouping and folding, Add Channel, dock sub-button sizing, orbitals, and slate backgrounds."""

from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication, QInputDialog

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import ChannelInfo, NodeContact
from meshcore_tray.storage import Storage
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.nav_dock import NavDockWidget, LayerButton, CycleFilterButton, DockButton
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.dms_view import DMsViewWidget
from meshcore_tray.ui.repeaters_view import RepeatersViewWidget


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_sidebar_add_channel_button_and_emission(app, tmp_path):
    """Verifies that the Add Channel button exists under the header and emits join_channel_requested."""
    storage = Storage(tmp_path / "add_chan_test.db")
    config = AppConfig()
    sidebar = Sidebar(storage=storage, config=config)

    assert hasattr(sidebar, "btn_add_channel")
    assert "Add Channel" in sidebar.btn_add_channel.text()

    emitted_channels = []
    sidebar.join_channel_requested.connect(lambda name: emitted_channels.append(name))

    with patch.object(QInputDialog, "getText", return_value=("operations", True)):
        sidebar.btn_add_channel.click()

    assert emitted_channels == ["operations"]


def test_channel_grouping_folding_and_unread_notification(app, tmp_path):
    """Verifies grouping channels, folding/unfolding groups, and displaying unread badges on folded groups."""
    storage = Storage(tmp_path / "group_fold_test.db")
    config = AppConfig()

    storage.save_channel(ChannelInfo(0, "Public", False, True))
    storage.save_channel(ChannelInfo(1, "cumbria", False, True))
    storage.save_channel(ChannelInfo(2, "emergency", False, True))

    # Assign "emergency" to an "Emergency" group
    config.set_channel_group("emergency", "Emergency")
    # Leave Public and cumbria in default "Channels"

    sidebar = Sidebar(storage=storage, config=config)
    sidebar.set_active_channel("Public")

    # Both groups should be expanded initially
    items = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    assert "▼  CHANNELS" in items
    assert "▼  EMERGENCY" in items
    assert "#cumbria" in items
    assert "#emergency" in items

    # Add unread message count to emergency channel
    storage.get_channel_unread_count = MagicMock(side_effect=lambda name: 7 if name == "emergency" else 0)

    # Fold the "Emergency" group by clicking its header item
    emergency_header_idx = [i for i in range(sidebar.channel_list.count()) if "EMERGENCY" in sidebar.channel_list.item(i).text()][0]
    header_item = sidebar.channel_list.item(emergency_header_idx)
    sidebar._on_channel_clicked(header_item)

    # Now Emergency group should be folded (▶) and show (7) unread notification!
    items_folded = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    assert "▶  EMERGENCY (7)" in items_folded
    # Channel #emergency should now be folded away (hidden from channel list)
    assert "#emergency" not in items_folded
    # Default Channels group still visible
    assert "#cumbria" in items_folded

    # Clicking the folded header again unfolds it
    folded_header_idx = [i for i in range(sidebar.channel_list.count()) if "EMERGENCY" in sidebar.channel_list.item(i).text()][0]
    folded_item = sidebar.channel_list.item(folded_header_idx)
    sidebar._on_channel_clicked(folded_item)

    items_unfolded = [sidebar.channel_list.item(i).text() for i in range(sidebar.channel_list.count())]
    assert any("EMERGENCY" in it and "▼" in it for it in items_unfolded)
    assert "#emergency (7)" in items_unfolded


def test_dock_sub_buttons_size_matches_top_icons(app):
    """Verifies that dock sub-buttons (LayerButton and CycleFilterButton) are 44x44 with 18px font."""
    dock = NavDockWidget(config=AppConfig())

    # Top icons
    assert dock.app_icon_btn.width() == 44
    assert dock.app_icon_btn.height() == 44
    assert dock.btn_main.width() == 44
    assert dock.btn_main.height() == 44

    # Sub buttons (9 map overlay buttons)
    sub_buttons = [
        dock.btn_cycle_filter,
        dock.btn_rf_links,
        dock.btn_byte_paths,
        dock.btn_heatmap,
        dock.btn_orbitals,
        dock.btn_tropo,
        dock.btn_thunderstorm,
        dock.btn_adsb,
        dock.btn_scopes,
    ]

    for btn in sub_buttons:
        assert btn.width() == 44, f"{btn} width is {btn.width()}, expected 44"
        assert btn.height() == 44, f"{btn} height is {btn.height()}, expected 44"
        assert "18px" in btn.styleSheet(), f"{btn} styleSheet missing 18px font-size"
        assert "padding: 0px" in btn.styleSheet(), f"{btn} styleSheet missing padding: 0px"


def test_mesh_map_orbitals_toggle_persistence(app, tmp_path):
    """Verifies that calling set_orbitals(True) maintains companion orbitals visible through refresh_map_data."""
    storage = Storage(tmp_path / "orbitals_test.db")
    config = AppConfig()

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        mesh_map = MeshMapWidget(storage=storage, config=config)
        assert mesh_map.show_companion_orbitals is False

        # Toggle orbitals on
        mesh_map.set_orbitals(True)
        assert mesh_map.show_companion_orbitals is True
        assert config.meshcore.map_show_companion_orbitals is True
        assert mesh_map.btn_orbitals.isChecked() is True

        # Refresh map data should not reset orbitals to false
        mesh_map.refresh_map_data()
        assert mesh_map.show_companion_orbitals is True
        assert mesh_map.btn_orbitals.isChecked() is True

        # Toggle orbitals off
        mesh_map.set_orbitals(False)
        assert mesh_map.show_companion_orbitals is False
        assert config.meshcore.map_show_companion_orbitals is False
        assert mesh_map.btn_orbitals.isChecked() is False


def test_dms_and_repeaters_background_matches_channel_list(app, tmp_path):
    """Verifies that DMsViewWidget and RepeatersViewWidget list background is #222327 with border #414143."""
    storage = Storage(tmp_path / "bg_test.db")
    config = AppConfig()

    dms = DMsViewWidget(storage=storage, config=config)
    repeaters = RepeatersViewWidget(storage=storage, config=config)

    assert "#222327" in dms.contact_list.styleSheet()
    assert "#414143" in dms.contact_list.styleSheet()

    assert "#222327" in repeaters.repeater_list.styleSheet()
    assert "#414143" in repeaters.repeater_list.styleSheet()


def test_scope_selector_vertical_layout_in_html_template():
    """Verifies that the scope filter bar is styled vertically on the left side of the map in the slate grey scheme."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE
    assert ".scope-filter-bar {" in LEAFLET_HTML_TEMPLATE
    assert "flex-direction: column;" in LEAFLET_HTML_TEMPLATE
    assert "left: 10px;" in LEAFLET_HTML_TEMPLATE
    assert "top: 50px;" in LEAFLET_HTML_TEMPLATE
    assert "background: rgba(34, 35, 39, 0.95);" in LEAFLET_HTML_TEMPLATE
    assert "border: 1px solid #414143;" in LEAFLET_HTML_TEMPLATE
    assert "background: #2B2F38;" in LEAFLET_HTML_TEMPLATE
    assert "overflow-y: auto;" in LEAFLET_HTML_TEMPLATE
    assert "overflow-x: hidden;" in LEAFLET_HTML_TEMPLATE
    assert "scope-pill-name" in LEAFLET_HTML_TEMPLATE


