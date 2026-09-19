"""Unit tests for SatellitesViewWidget and satellite details/imagery."""

import pytest
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.core.satellite_service import (
    SatelliteService, CURATED_OFFLINE_TLES
)
from meshcore_tray.storage import Storage
from meshcore_tray.ui.satellites_view import (
    SatellitesViewWidget, SatelliteRowWidget, parse_tle_summary
)


@pytest.fixture
def test_storage(tmp_path):
    db_path = tmp_path / "test_sats_view.db"
    storage = Storage(db_path)
    storage.save_satellite_tles(CURATED_OFFLINE_TLES)
    return storage


def test_parse_tle_summary():
    """Verifies that TLE strings are accurately parsed into orbital parameters."""
    iss = CURATED_OFFLINE_TLES[0]
    summary = parse_tle_summary(iss["line1"], iss["line2"])

    assert summary["inclination_deg"] == pytest.approx(51.64, abs=0.1)
    assert 90.0 <= summary["period_min"] <= 95.0
    assert 400.0 <= summary["apogee_km"] <= 440.0
    assert 400.0 <= summary["perigee_km"] <= 440.0
    assert "98067A" in summary["intl_designator"]
    assert "Day" in summary["epoch"]


def test_satellites_view_initialization(qapp, test_storage):
    """Verifies that SatellitesViewWidget initializes properly and populates list."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    assert view.sat_list.count() == len(CURATED_OFFLINE_TLES)
    assert int(view.count_badge.text()) == len(CURATED_OFFLINE_TLES)
    assert view.selected_satellite is not None
    assert view.det_title.text() != ""
    assert "NORAD #" in view.det_norad_pill.text()


def test_satellites_view_category_filtering(qapp, test_storage):
    """Verifies filtering by category groups (Stations, Weather, Amateur, Cubesats, Favorites)."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    # Filter to Weather satellites
    view._on_filter_tab_clicked("weather")
    weather_count = view.sat_list.count()
    assert weather_count > 0
    for i in range(weather_count):
        item = view.sat_list.item(i)
        sat = item.data(Qt.ItemDataRole.UserRole)
        assert sat["group_name"] == "weather"

    # Filter to Stations
    view._on_filter_tab_clicked("stations")
    assert view.sat_list.count() >= 1

    # Filter to Favorites (initially 0)
    view._on_filter_tab_clicked("favorites")
    assert view.sat_list.count() == 0

    # Favorite ISS and verify it appears in Favorites tab
    test_storage.set_satellite_favorite("25544", True)
    view.reload_satellites()
    view._on_filter_tab_clicked("favorites")
    assert view.sat_list.count() == 1
    fav_sat = view.sat_list.item(0).data(Qt.ItemDataRole.UserRole)
    assert fav_sat["norad_id"] == "25544"


def test_satellites_view_search_filtering(qapp, test_storage):
    """Verifies text search filters by name, NORAD ID, and frequencies."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    # Search by NORAD ID
    view.search_input.setText("25338")
    assert view.sat_list.count() == 1
    sat = view.sat_list.item(0).data(Qt.ItemDataRole.UserRole)
    assert sat["norad_id"] == "25338"

    # Search by frequency (NOAA 137.620)
    view.search_input.setText("137.620")
    assert view.sat_list.count() >= 1

    # Clear search
    view.search_input.setText("")
    assert view.sat_list.count() == len(CURATED_OFFLINE_TLES)


def test_satellites_view_favorite_toggle_and_detail(qapp, test_storage):
    """Verifies toggling favorite updates storage and view state."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    signal_received = []
    view.favorite_toggled.connect(lambda nid, fav: signal_received.append((nid, fav)))

    # Select ISS (25544)
    view.select_satellite("25544")
    assert view.selected_satellite["norad_id"] == "25544"
    assert "Add to Favorites" in view.btn_fav_action.text()

    # Click detail favorite action button
    view._on_detail_fav_clicked()
    assert len(signal_received) == 1
    assert signal_received[0] == ("25544", True)
    assert "Favorited" in view.btn_fav_action.text()

    # Verify storage persisted
    favs = test_storage.get_favorite_satellites()
    assert any(s["norad_id"] == "25544" for s in favs)

    # Unfavorite
    view._on_detail_fav_clicked()
    assert signal_received[1] == ("25544", False)
    assert "Add to Favorites" in view.btn_fav_action.text()


def test_satellites_view_track_on_map_signal(qapp, test_storage):
    """Verifies that clicking Track on Live Map emits show_on_map_requested."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    map_signals = []
    view.show_on_map_requested.connect(lambda nid: map_signals.append(nid))

    view.select_satellite("25338")
    view._on_track_on_map_clicked()

    assert len(map_signals) == 1
    assert map_signals[0] == "25338"


def test_resolve_spacecraft_media():
    """Verifies that authentic photographs are chosen for major space assets and blueprints for others."""
    from meshcore_tray.ui.satellites_view import resolve_spacecraft_media

    # ISS -> authentic NASA photo
    img, payload, attr = resolve_spacecraft_media("ISS (ZARYA)", "25544", "stations")
    assert img == "iss_real_photo.jpg"
    assert "NASA" in attr

    # Chinese Space Station -> authentic Shenzhou-16 photograph
    img_css, _, attr_css = resolve_spacecraft_media("CSS (TIANHE)", "48274", "stations")
    assert img_css == "tiangong_real_photo.jpg"
    assert "Shenzhou-16" in attr_css or "CMSA" in attr_css

    img_tg, _, _ = resolve_spacecraft_media("TIANGONG", "99999", "stations")
    assert img_tg == "tiangong_real_photo.jpg"

    # SpaceX Dragon -> authentic NASA photo
    img_dragon, _, attr_dragon = resolve_spacecraft_media("CREW DRAGON 12", "67796", "stations")
    assert img_dragon == "dragon_real_photo.jpg"
    assert "Dragon" in attr_dragon

    # Cygnus -> authentic NASA photo
    img_cyg, _, _ = resolve_spacecraft_media("CYGNUS NG-20", "58888", "stations")
    assert img_cyg == "cygnus_real_photo.jpg"

    # Hubble -> authentic NASA photo
    img_hst, _, _ = resolve_spacecraft_media("HUBBLE SPACE TELESCOPE", "20580", "stations")
    assert img_hst == "hubble_real_photo.jpg"

    # NOAA -> authentic NASA photo
    img_noaa, _, _ = resolve_spacecraft_media("NOAA 19", "33591", "weather")
    assert img_noaa == "noaa19_real_photo.jpg"

    # Cubesat generic -> cyberpunk blueprint
    img_cube, _, attr_cube = resolve_spacecraft_media("SWISSCUBE", "35932", "cubesat")
    assert img_cube == "blueprint_cubesat.png"
    assert "Cyberpunk" in attr_cube


def test_satellites_view_external_web_link(qapp, test_storage, monkeypatch):
    """Verifies the external N2YO web link button triggers desktop browser opening."""
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtCore import QUrl

    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    view.select_satellite("25544")
    assert not view.btn_web_action.isHidden()

    opened_urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString()))

    view._on_open_web_link()
    assert len(opened_urls) == 1
    assert "https://www.n2yo.com/satellite/?s=25544" in opened_urls[0]


def test_satellites_view_discord_styling(qapp, test_storage):
    """Verifies that SatellitesViewWidget adheres strictly to the Discord dark grey palette."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    # Stylesheet should contain discord tokens and avoid bright blue base backgrounds
    ss_main = view.styleSheet()
    ss_list = view.sat_list.styleSheet()
    ss_search = view.search_input.styleSheet()
    ss_map = view.btn_map_action.styleSheet()

    assert "#313338" in ss_main  # Discord main content area
    assert "#2B2D31" in ss_list  # Discord list / sidebar background
    assert "#1E1F22" in ss_search  # Discord deepest background for inputs
    assert "#5865F2" in ss_map  # Discord Blurple accent

    # Disallowed harsh blue backgrounds from previous iteration
    all_css = ss_main + ss_list + ss_search + ss_map
    assert "#0E1117" not in all_css
    assert "#1E3A5F" not in all_css
    assert "#0284C7" not in all_css


def test_telemetry_terminal_widget_streaming_and_controls(qapp, test_storage):
    """Verifies that TelemetryTerminalWidget outputs protocol packets and responds to pause/copy/clear."""
    from meshcore_tray.ui.satellites_view import TelemetryTerminalWidget

    widget = TelemetryTerminalWidget()
    sat = {
        "norad_id": "33591",
        "name": "NOAA 19",
        "group_name": "weather",
        "frequencies": [{"freq_mhz": 137.100, "label": "APT"}],
    }

    widget.set_satellite(sat)
    text = widget.console.toPlainText()
    assert "RECEIVER TUNED: NOAA 19" in text
    assert "137.1" in text

    # Generate additional packet
    initial_len = len(text)
    widget._generate_stream_packet()
    assert len(widget.console.toPlainText()) > initial_len

    # Test Pause / Resume
    widget._toggle_pause()
    assert widget.is_paused is True
    assert "PAUSED" in widget.status_pill.text()
    assert "Resume" in widget.btn_pause.text()

    widget._toggle_pause()
    assert widget.is_paused is False
    assert "STREAM ACTIVE" in widget.status_pill.text()

    # Test Copy Log
    widget._copy_log()
    assert widget.btn_copy.text() == "✓ Copied!"
    cb = QApplication.clipboard()
    assert "NOAA 19" in cb.text()

    # Test Clear
    widget._clear_log()
    assert widget.console.toPlainText() == ""


def test_satellites_view_observation_metadata_and_lightbox_triggers(qapp, test_storage, monkeypatch):
    """Verifies downlinked observation metadata display and lightbox modal opening."""
    from meshcore_tray.ui.satellite_image_modal import SatelliteImageModal

    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)
    view.show()

    # 1. NOAA 19 (imaging weather satellite)
    view.select_satellite("33591")
    assert view.obs_img_card.isVisible() is True
    assert view.img_payload_lbl.isVisible() is True
    assert "137.100" in view.lbl_obs_downlink.text()
    assert "AVHRR" in view.lbl_obs_sensor.text()

    # Track lightbox execution
    modals_opened = []
    def fake_exec(modal_self):
        modals_opened.append(modal_self.modal_title)
        return 1
    monkeypatch.setattr(SatelliteImageModal, "exec", fake_exec)

    # Click downlinked observation image card
    view.obs_img_card.clicked.emit()
    assert len(modals_opened) == 1
    assert "NOAA 19 Downlinked Earth Observation" in modals_opened[0]

    # Click spacecraft configuration image card
    view.craft_img_card.clicked.emit()
    assert len(modals_opened) == 2
    assert "NOAA 19 Spacecraft" in modals_opened[1]

    # 2. Non-imaging Amateur Satellite (SO-50)
    view.select_satellite("27607")
    assert view.obs_img_card.isVisible() is False
    assert view.img_payload_lbl.isVisible() is False
    assert view.lbl_no_obs.isVisible() is True
    assert "No Earth Observation Camera Payload" in view.lbl_no_obs.text()


def test_satellites_view_sync_tles_action(qapp, test_storage, monkeypatch):
    """Verifies that clicking Sync TLEs calls satellite_service without AttributeError."""
    config = AppConfig()
    view = SatellitesViewWidget(storage=test_storage, config=config)

    called = []
    monkeypatch.setattr(view.satellite_service, "refresh_now", lambda force=False: called.append(force))

    assert hasattr(view, "btn_sync")
    assert "Sync TLEs" in view.btn_sync.text()
    assert view.btn_sync.isEnabled() is True

    # Trigger click
    view.btn_sync.click()

    # Verify service was invoked
    assert len(called) == 1
    assert view.btn_sync.isEnabled() is False
    assert "Syncing..." in view.btn_sync.text()

    # Reset button on reload
    view.reload_satellites()
    assert view.btn_sync.isEnabled() is True
    assert "Sync TLEs" in view.btn_sync.text()


