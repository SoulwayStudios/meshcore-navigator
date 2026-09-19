"""Unit tests for offline satellite tracking, SGP4 integration, Doppler shift calculations, and layer toggles."""

import json
import math
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from meshcore_tray.config import AppConfig, SatelliteConfig
from meshcore_tray.core.satellite_service import (
    SatelliteService,
    parse_tle_stream,
    resolve_satellite_group,
    calculate_doppler_shift,
    calculate_footprint_radius_km,
    CURATED_OFFLINE_TLES,
    KNOWN_SATELLITE_FREQUENCIES,
    SPEED_OF_LIGHT_KM_S,
    EARTH_RADIUS_KM,
)
from meshcore_tray.storage import Storage
from meshcore_tray.ui.nav_dock import NavDockWidget, MapLayerDockWidget
from meshcore_tray.ui.mesh_map_widget import get_leaflet_html, MeshMapWidget, WEBENGINE_AVAILABLE


SAMPLE_TLE_TEXT = """
ISS (ZARYA)
1 25544U 98067A   26077.53488426  .00016717  00000+0  30000-3 0  9993
2 25544  51.6416 112.9812 0005784  35.2160 324.9123 15.49842514444321
SO-50
1 27607U 02058C   26077.51230123  .00000214  00000+0  11000-3 0  9992
2 27607  64.5510 215.1200 0081200 240.1200 118.4500 14.76012345123456
"""


def test_parse_tle_stream():
    parsed = parse_tle_stream(SAMPLE_TLE_TEXT, group_name="amateur")
    assert len(parsed) == 2
    
    iss = parsed[0]
    assert iss["norad_id"] == "25544"
    assert "ISS" in iss["name"]
    # ISS is canonically categorized as stations
    assert iss["group_name"] == "stations"
    assert iss["line1"].startswith("1 25544")
    assert iss["line2"].startswith("2 25544")
    assert len(iss["frequencies"]) > 0  # ISS is in KNOWN_SATELLITE_FREQUENCIES
    
    so50 = parsed[1]
    assert so50["norad_id"] == "27607"
    assert so50["name"] == "SO-50"
    assert so50["group_name"] == "amateur"
    assert len(so50["frequencies"]) > 0


def test_resolve_satellite_group():
    """Verifies that resolve_satellite_group properly segregates cubesats and stations."""
    # Cubesats
    assert resolve_satellite_group("SWISSCUBE", "35932", "amateur") == "cubesat"
    assert resolve_satellite_group("CROCUBE", "59864", "amateur") == "cubesat"
    assert resolve_satellite_group("FUNCUBE-1 (AO-73)", "39444", "amateur") == "cubesat"
    assert resolve_satellite_group("CUBESAT XI-IV (CO-57)", "27848", "amateur") == "cubesat"
    assert resolve_satellite_group("FOX-1B (AO-91/RADFXSAT)", "43017", "amateur") == "cubesat"
    assert resolve_satellite_group("ITASAT 1 (1U CUBESAT)", "43786", "amateur") == "cubesat"
    assert resolve_satellite_group("RADFXSAT (AO-91)", "43017", "amateur") == "cubesat"

    # Space stations & human spaceflight / cargo
    assert resolve_satellite_group("ISS (ZARYA)", "25544", "amateur") == "stations"
    assert resolve_satellite_group("CSS (TIANHE)", "48274", "amateur") == "stations"
    assert resolve_satellite_group("CSS (WENTIAN)", "53239", "amateur") == "stations"
    assert resolve_satellite_group("TIANGONG", "48274", "amateur") == "stations"
    assert resolve_satellite_group("CREW DRAGON 12", "67796", "amateur") == "stations"
    assert resolve_satellite_group("SPACEX DRAGON", "40000", "amateur") == "stations"
    assert resolve_satellite_group("CYGNUS NG-20", "58888", "amateur") == "stations"

    # Weather satellites
    assert resolve_satellite_group("NOAA 19", "33591", "weather") == "weather"
    assert resolve_satellite_group("METEOR-M2 3", "57166", "weather") == "weather"

    # Standard Amateur
    assert resolve_satellite_group("SO-50", "27607", "amateur") == "amateur"
    assert resolve_satellite_group("AO-07", "07530", "amateur") == "amateur"


def test_calculate_doppler_shift():
    # Observer at (6371, 0, 0), Satellite directly overhead moving towards observer (pure radial approach)
    freq = 145.825  # MHz
    obs_pos = (6371.0, 0.0, 0.0)
    sat_pos = (6371.0 + 500.0, 0.0, 0.0)
    
    # Moving towards observer at 7 km/s: velocity along x is -7 km/s
    sat_vel_approaching = (-7.0, 0.0, 0.0)
    shift_hz, observed_mhz = calculate_doppler_shift(freq, sat_pos, sat_vel_approaching, obs_pos)
    assert shift_hz > 0  # Approaching produces positive blue shift
    assert observed_mhz > freq
    expected_shift = freq * 1e6 * (7.0 / SPEED_OF_LIGHT_KM_S)
    assert math.isclose(shift_hz, expected_shift, rel_tol=1e-3)
    
    # Moving away from observer at 7 km/s: velocity along x is +7 km/s
    sat_vel_receding = (7.0, 0.0, 0.0)
    shift_receding, observed_receding = calculate_doppler_shift(freq, sat_pos, sat_vel_receding, obs_pos)
    assert shift_receding < 0
    assert observed_receding < freq
    
    # Zero relative velocity
    shift_zero, _ = calculate_doppler_shift(freq, sat_pos, (0.0, 7.0, 0.0), obs_pos)
    assert math.isclose(shift_zero, 0.0, abs_tol=1e-3)


def test_calculate_footprint_radius_km():
    # ISS at 420 km altitude: s = R * acos(R / (R + h))
    radius_km = calculate_footprint_radius_km(420.0)
    assert 2200 < radius_km < 2350
    
    # Zero altitude
    assert calculate_footprint_radius_km(0.0) == 0.0
    assert calculate_footprint_radius_km(-10.0) == 0.0


def test_satellite_storage_integration():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        storage = Storage(db_path)
        assert storage.get_satellite_tle_count() == 0
        
        # Save parsed TLEs
        parsed = parse_tle_stream(SAMPLE_TLE_TEXT, group_name="stations")
        saved_count = storage.save_satellite_tles(parsed)
        assert saved_count == 2
        assert storage.get_satellite_tle_count() == 2
        
        # Retrieve by ID
        iss = storage.get_satellite_by_id("25544")
        assert iss is not None
        assert iss["norad_id"] == "25544"
        assert "ISS" in iss["name"]
        assert len(iss["frequencies"]) > 0
        
        # Retrieve all
        all_sats = storage.get_satellite_tles()
        assert len(all_sats) == 2
        
        # Filter by group
        stations = storage.get_satellite_tles(group_name="stations")
        assert len(stations) == 2
        weather = storage.get_satellite_tles(group_name="weather")
        assert len(weather) == 0
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_satellite_service_offline_seeding(ensure_qapp):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        storage = Storage(db_path)
        cfg = AppConfig()
        
        # Initializing service should auto-seed offline catalog
        svc = SatelliteService(storage=storage, config=cfg)
        assert storage.get_satellite_tle_count() >= len(CURATED_OFFLINE_TLES)
        
        # By default, active_only filters to selected_satellites (ISS, NOAA 15, NOAA 18)
        sats_active = svc.get_satellites_for_map(active_only=True)
        assert len(sats_active) == len(cfg.satellites.selected_satellites)
        
        # active_only=False returns full catalog
        sats_all = svc.get_satellites_for_map(active_only=False)
        assert len(sats_all) >= len(CURATED_OFFLINE_TLES)
        
        svc.stop()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_leaflet_html_includes_satellite_assets():
    html = get_leaflet_html()
    assert "satellite.min.js" in html or "twoline2satrec" in html
    assert "satellite-panel" in html
    assert "SATELLITE TRACKER" in html
    assert "sat-pass-badge" in html
    assert "sat-freq-table" in html
    assert "setSatellitesVisible" in html
    assert "onSatellitesDataReady" in html


def test_nav_dock_satellites_button(ensure_qapp):
    cfg = AppConfig()
    dock = NavDockWidget(config=cfg)
    
    assert hasattr(dock, "btn_satellites")
    assert hasattr(dock.map_layers, "btn_satellites")
    
    received_events = []
    dock.layer_toggled.connect(lambda k, v: received_events.append((k, v)))
    
    dock.btn_satellites.setChecked(True)
    assert ("satellites", True) in received_events
    
    dock.btn_satellites.setChecked(False)
    assert ("satellites", False) in received_events
    
    # Programmatic sync without re-emitting
    dock.map_layers.set_layer_active("satellites", True)
    assert dock.btn_satellites.isChecked() is True


def test_mesh_map_satellite_layer_toggle(ensure_qapp):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        storage = Storage(db_path)
        cfg = AppConfig()
        
        map_widget = MeshMapWidget(storage=storage, config=cfg)
        
        assert hasattr(map_widget, "show_satellites")
        assert hasattr(map_widget, "satellite_service")
        
        # Toggle satellites layer on
        map_widget.set_satellites(True)
        assert map_widget.show_satellites is True
        assert cfg.satellites.enabled is True
        
        # Toggle satellites layer off
        map_widget.set_satellites(False)
        assert map_widget.show_satellites is False
        assert cfg.satellites.enabled is False
        
        map_widget.cleanup()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_satellite_service_refresh_tles_alias(ensure_qapp, monkeypatch):
    """Verifies that SatelliteService.refresh_tles exists and delegates to refresh_now."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        storage = Storage(db_path)
        cfg = AppConfig()
        svc = SatelliteService(storage=storage, config=cfg)

        called = []
        monkeypatch.setattr(svc, "refresh_now", lambda force=False: called.append(force))

        assert hasattr(svc, "refresh_tles")
        svc.refresh_tles()
        assert len(called) == 1
        assert called[0] is True

        svc.refresh_tles(force=False)
        assert len(called) == 2
        assert called[1] is False

        svc.stop()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

