"""Unit tests for satellite favorites persistence and NavDockWidget mixed favorites."""

import pytest
from unittest.mock import patch
from PyQt6.QtCore import Qt

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact
from meshcore_tray.core.satellite_service import CURATED_OFFLINE_TLES
from meshcore_tray.storage import Storage
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.main_window import MainWindow


@pytest.fixture
def test_storage(tmp_path):
    db_path = tmp_path / "test_dock_favs.db"
    storage = Storage(db_path)
    storage.save_satellite_tles(CURATED_OFFLINE_TLES)
    return storage


def test_storage_satellite_favorites(test_storage):
    """Verifies that satellite favorites are saved and survive TLE refreshes."""
    # Initially no favorites
    assert len(test_storage.get_favorite_satellites()) == 0

    # Mark ISS as favorite
    res = test_storage.set_satellite_favorite("25544", True)
    assert res is True
    favs = test_storage.get_favorite_satellites()
    assert len(favs) == 1
    assert favs[0]["norad_id"] == "25544"
    assert favs[0]["is_favorite"] is True
    assert favs[0]["is_satellite"] is True

    # Mark NOAA 19 as favorite
    test_storage.set_satellite_favorite("33591", True)
    favs = test_storage.get_favorite_satellites()
    assert len(favs) == 2

    # Refresh TLEs batch from CelesTrak and ensure favorites are preserved
    test_storage.save_satellite_tles(CURATED_OFFLINE_TLES)
    favs_after = test_storage.get_favorite_satellites()
    assert len(favs_after) == 2
    assert any(s["norad_id"] == "25544" for s in favs_after)
    assert any(s["norad_id"] == "33591" for s in favs_after)

    # Unmark favorite
    test_storage.set_satellite_favorite("25544", False)
    favs_reduced = test_storage.get_favorite_satellites()
    assert len(favs_reduced) == 1
    assert favs_reduced[0]["norad_id"] == "33591"


def test_nav_dock_mixed_favorites_rendering(qapp):
    """Verifies that NavDockWidget renders mixed contacts, repeaters, and satellites."""
    dock = NavDockWidget()

    # Create mixed favorites list: contact, repeater, and satellite
    c1 = NodeContact(node_id="!11111111", alias="Alice", is_favorite=True)
    c2 = NodeContact(node_id="!22222222", alias="[Rep] High Peak", is_favorite=True, is_repeater=True)
    sat1 = {
        "norad_id": "25544",
        "name": "ISS (ZARYA)",
        "group_name": "stations",
        "frequencies": [{"label": "APRS", "freq_mhz": 145.825, "mode": "Packet"}],
        "is_satellite": True,
        "is_favorite": True,
    }

    mixed_favs = [c1, c2, sat1]
    dock.show()
    dock.update_favorite_contacts(mixed_favs)

    assert dock.fav_divider.isVisible() is True
    assert dock.fav_layout.count() == 3

    # Verify satellite button properties and tooltip
    sat_btn = dock.fav_layout.itemAt(2).widget()
    assert "ISS" in sat_btn.toolTip()
    assert "25544" in sat_btn.toolTip()
    assert "Space Station" in sat_btn.toolTip()


def test_nav_dock_favorite_satellite_clicked(qapp):
    """Verifies that clicking a favorite satellite button emits satellite_selected."""
    dock = NavDockWidget()
    sat1 = {
        "norad_id": "25544",
        "name": "ISS (ZARYA)",
        "group_name": "stations",
        "frequencies": [],
        "is_satellite": True,
        "is_favorite": True,
    }

    selected_sat = []
    selected_item = []
    dock.satellite_selected.connect(lambda nid: selected_sat.append(nid))
    dock.favorite_item_selected.connect(lambda t, i: selected_item.append((t, i)))

    dock.update_favorite_contacts([sat1])
    btn = dock.fav_layout.itemAt(0).widget()
    btn.click()

    assert len(selected_sat) == 1
    assert selected_sat[0] == "25544"
    assert len(selected_item) == 1
    assert selected_item[0] == ("satellite", "25544")


def test_main_window_nav_dock_satellites_navigation(qapp, test_storage):
    """Verifies switching to Satellites page and clicking favorite satellite in MainWindow."""
    config = AppConfig()
    test_storage.set_satellite_favorite("25544", True)

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        win = MainWindow(config=config, storage=test_storage, radio_driver=None)

    # Verify Satellites button exists in nav dock
    assert hasattr(win.nav_dock, "btn_sats")

    # Switch to satellites view via nav dock button
    win.nav_dock.btn_sats.click()
    assert win.main_stack.currentWidget() == win.satellites_view

    # Verify dock favorites updated with favorite satellite
    assert win.nav_dock.fav_layout.count() >= 1

    # Simulate clicking favorite satellite pill in nav dock
    win.nav_dock.switch_view("main")
    assert win.main_stack.currentIndex() == 0

    win.nav_dock.satellite_selected.emit("25544")
    assert win.main_stack.currentWidget() == win.satellites_view
    assert win.satellites_view.selected_satellite["norad_id"] == "25544"

    win.cleanup()


def test_nav_dock_favorite_room_server_icon(qapp):
    """Verifies that room server favorites render using the Room Server SVG icon without robot avatar or text initials."""
    dock = NavDockWidget()
    room_contact = NodeContact(node_id="!rm123456", alias="North-Hub [Room]", is_room_server=True, is_favorite=True)

    dock.show()
    dock.update_favorite_contacts([room_contact])

    assert dock.fav_layout.count() == 1
    btn = dock.fav_layout.itemAt(0).widget()

    # Must have an icon set and NOT be showing initials text
    assert not btn.icon().isNull()
    assert btn.text() == ""
    assert "Room Server" in btn.toolTip()
    assert "#FF55FF" in btn.styleSheet()


def test_satellites_view_real_imagery_and_payload_visibility(qapp, test_storage):
    """Verifies that satellites view uses authentic NASA/NOAA imagery or cyberpunk blueprints, and only shows secondary payload box when observation scan exists."""
    from meshcore_tray.ui.satellites_view import SatellitesViewWidget, STATIC_SATELLITE_DIR

    view = SatellitesViewWidget(storage=test_storage)
    view.show()

    # 1. ISS (has authentic NASA photo + Cupola Earth view)
    view.select_satellite("25544")
    assert view.selected_satellite["norad_id"] == "25544"
    assert view.img_spacecraft_lbl.pixmap() is not None
    assert (STATIC_SATELLITE_DIR / "iss_real_photo.jpg").exists()
    assert (STATIC_SATELLITE_DIR / "iss_cupola_earth.jpg").exists()
    assert view.img_payload_lbl.isVisible() is True
    assert view.img_payload_lbl.pixmap() is not None

    # 2. NOAA-19 (has authentic NOAA photo + APT cloud scan)
    view.select_satellite("33591")
    assert view.selected_satellite["norad_id"] == "33591"
    assert (STATIC_SATELLITE_DIR / "noaa19_real_photo.jpg").exists()
    assert (STATIC_SATELLITE_DIR / "noaa_cloud_scan.jpg").exists()
    assert view.img_payload_lbl.isVisible() is True

    # 3. Amateur satellite without observation scan (e.g. SO-50 or AO-91)
    # Payload label MUST be hidden (no empty placeholder)
    view.select_satellite("27607")
    assert view.selected_satellite["norad_id"] == "27607"
    assert (STATIC_SATELLITE_DIR / "blueprint_amateur.png").exists()
    assert view.img_payload_lbl.isVisible() is False

