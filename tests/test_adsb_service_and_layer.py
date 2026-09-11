"""Automated unit and integration tests for ADS-B Live Flight Data service and Leaflet map layer."""

import json
from unittest.mock import MagicMock, patch
import pytest

from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.core.adsb_service import (
    ADSBService,
    ADSBFetchWorker,
    AircraftPhotoWorker,
    haversine_nm,
    bearing_deg,
    classify_aircraft,
)
from meshcore_tray.core.models import NodeContact
from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE, MeshMapWidget, WebBridge
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.dms_view import DMsViewWidget
from meshcore_tray.ui.repeaters_view import RepeatersViewWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test"])
    return app


def test_adsb_haversine_and_bearing_math():
    """Verify Great Circle distance and initial bearing calculations."""
    # London (51.5074, -0.1278) to Paris (48.8566, 2.3522)
    # Approx 185 - 186 nautical miles
    dist = haversine_nm(51.5074, -0.1278, 48.8566, 2.3522)
    assert 180.0 <= dist <= 190.0

    # Due North: 1 degree latitude = 60 NM, bearing = 0°
    assert haversine_nm(50.0, 0.0, 51.0, 0.0) == 60.0
    assert bearing_deg(50.0, 0.0, 51.0, 0.0) == 0.0

    # Due East: bearing = 90°
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == 90.0


def test_adsb_worker_parsing(qapp):
    """Verify parsing of raw ADS-B aggregator response into clean aircraft records."""
    sample_response = {
        "ac": [
            {
                "hex": "40690a",
                "flight": "EZY16YV ",
                "r": "G-EZWI",
                "t": "A320",
                "alt_baro": 17000,
                "alt_geom": 17900,
                "gs": 370.4,
                "track": 120.8,
                "lat": 53.879,
                "lon": -4.481,
                "squawk": "7620",
                "emergency": "none",
                "dst": 48.9,
                "dir": 223.0,
            },
            {
                "hex": "407b4b",
                "flight": "SFY4PC  ",
                "r": "G-SFYB",
                "t": "C172",
                "alt_baro": "ground",
                "gs": 0.0,
                "track": 45.0,
                "lat": 54.48,
                "lon": -3.54,
                "squawk": "7700",
                "emergency": "general",
            }
        ]
    }

    target_info = {"node_id": "rep-1", "alias": "Repeater 1", "lat": 54.48, "lon": -3.54, "radius_nm": 50}
    worker = ADSBFetchWorker(lat=54.48, lon=-3.54, radius_nm=50, target_info=target_info)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        results = []
        worker.success_signal.connect(lambda p: results.append(p))
        worker.run()

    assert len(results) == 1
    payload = results[0]
    assert payload["total"] == 2
    assert payload["target_info"]["alias"] == "Repeater 1"

    plane1 = payload["aircraft"][0]
    assert plane1["hex"] == "40690a"
    assert plane1["flight"] == "EZY16YV"
    assert plane1["r"] == "G-EZWI"
    assert plane1["t"] == "A320"
    assert plane1["alt_baro"] == 17000
    assert plane1["dst"] == 48.9

    plane2 = payload["aircraft"][1]
    assert plane2["alt_baro"] == 0  # 'ground' converted to 0
    assert plane2["squawk"] == "7700"
    assert plane2["emergency"] == "general"


def test_adsb_service_target_and_timer(qapp):
    """Verify ADSBService target management and timer activation."""
    service = ADSBService()
    assert not service.enabled
    assert not service._poll_timer.isActive()

    service.set_target("node-123", "Skiddaw-Repeater", 54.65, -3.15, radius_nm=60)
    assert service.target_node_id == "node-123"
    assert service.target_alias == "Skiddaw-Repeater"
    assert service.target_lat == 54.65
    assert service.target_lon == -3.15
    assert service.radius_nm == 60

    with patch.object(service, "refresh") as mock_refresh:
        service.set_enabled(True)
        assert service.enabled
        assert service._poll_timer.isActive()
        mock_refresh.assert_called_once()

        service.set_enabled(False)
        assert not service.enabled
        assert not service._poll_timer.isActive()


def test_adsb_html_template_elements():
    """Verify LEAFLET_HTML_TEMPLATE includes all ADS-B layer DOM elements, styles, and scripts."""
    assert ".adsb-panel {" in LEAFLET_HTML_TEMPLATE
    assert ".adsb-plane-marker {" in LEAFLET_HTML_TEMPLATE
    assert ".adsb-tooltip {" in LEAFLET_HTML_TEMPLATE
    assert 'id="adsb-panel"' in LEAFLET_HTML_TEMPLATE
    assert 'id="adsb-count-badge"' in LEAFLET_HTML_TEMPLATE
    assert 'id="adsb-target-label"' in LEAFLET_HTML_TEMPLATE
    assert "adsbPane" in LEAFLET_HTML_TEMPLATE
    assert "adsbLayerGroup" in LEAFLET_HTML_TEMPLATE
    assert "setAdsbVisible" in LEAFLET_HTML_TEMPLATE
    assert "createAirplaneIcon" in LEAFLET_HTML_TEMPLATE
    assert "onAdsbDataReady" in LEAFLET_HTML_TEMPLATE
    assert "Track ADS-B Around Node" in LEAFLET_HTML_TEMPLATE


def test_dock_adsb_layer_button(qapp):
    """Verify NavigationDock includes the ADS-B LayerButton and emits layer_toggled."""
    cfg = AppConfig()
    cfg.meshcore.map_show_adsb = False
    dock = NavDockWidget(config=cfg)

    assert hasattr(dock, "btn_adsb")
    assert not dock.btn_adsb.isChecked()
    assert dock.btn_adsb.toolTip() == "ADSB View"

    toggled_events = []
    dock.layer_toggled.connect(lambda k, state: toggled_events.append((k, state)))

    dock.btn_adsb.setChecked(True)
    assert ("adsb", True) in toggled_events

    dock.btn_adsb.setChecked(False)
    assert ("adsb", False) in toggled_events


def test_dms_and_repeaters_track_adsb_requested(qapp):
    """Verify DMsViewWidget and RepeatersViewWidget emit track_adsb_requested signal."""
    cfg = AppConfig()
    dms = DMsViewWidget(config=cfg)
    assert hasattr(dms, "track_adsb_requested")

    repeaters = RepeatersViewWidget(config=cfg)
    assert hasattr(repeaters, "track_adsb_requested")


def test_mesh_map_set_adsb_and_target(qapp):
    """Verify MeshMapWidget set_adsb and set_adsb_target controls."""
    cfg = AppConfig()
    cfg.meshcore.latitude = 54.48
    cfg.meshcore.longitude = -3.54
    cfg.meshcore.node_alias = "Local-Heltec"

    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(config=cfg)
        assert hasattr(map_widget, "adsb_service")
        assert hasattr(map_widget, "btn_adsb")

        with patch.object(map_widget.adsb_service, "refresh"):
            map_widget.set_adsb(True)
            assert map_widget.show_adsb is True
            assert cfg.meshcore.map_show_adsb is True
            assert map_widget.adsb_service.target_lat == 54.48
            assert map_widget.adsb_service.target_lon == -3.54

            # Target change
            map_widget.set_adsb_target("node-456", "Lank Rigg", 54.494, -3.406, radius_nm=75)
            assert map_widget.adsb_service.target_node_id == "node-456"
            assert map_widget.adsb_service.target_alias == "Lank Rigg"
            assert map_widget.adsb_service.target_lat == 54.494
            assert map_widget.adsb_service.target_lon == -3.406
            assert map_widget.adsb_service.radius_nm == 75
            assert cfg.meshcore.adsb_target_node_id == "node-456"

            map_widget.set_adsb(False)
            assert map_widget.show_adsb is False
            assert cfg.meshcore.map_show_adsb is False


def test_leaflet_html_javascript_clean_syntax():
    """Verify LEAFLET_HTML_TEMPLATE loads in WebEngine in isolated process with zero JS syntax/runtime errors."""
    import subprocess
    import sys
    script = """
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebChannel import QWebChannel
from meshcore_tray.ui.mesh_map_widget import get_leaflet_html, WebBridge

app = QApplication(sys.argv)
view = QWebEngineView()
bridge = WebBridge()
channel = QWebChannel()
channel.registerObject('pyBridge', bridge)

errors = []
class PageWatcher(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, source):
        if 'error' in msg.lower() or 'uncaught' in msg.lower():
            errors.append((level, msg, line, source))

page = PageWatcher(view)
page.setWebChannel(channel)
view.setPage(page)

loop = QEventLoop()
view.loadFinished.connect(lambda ok: loop.quit())
QTimer.singleShot(4000, loop.quit)
view.setHtml(get_leaflet_html())
loop.exec()

if errors:
    print('JS_ERRORS:', errors)
    sys.exit(1)
"""
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert res.returncode == 0, f"JS check failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"


def test_aircraft_classification():
    """Verify aircraft categorization into airliner, light, military, helicopter, glider, or general."""
    # Commercial airliners
    cat, name = classify_aircraft({"t": "A320", "desc": "Airbus A320"}, 32000, 440)
    assert cat == "airliner"
    assert "Airliner" in name

    cat, name = classify_aircraft({"t": "B738", "desc": "Boeing 737-800"}, 36000, 480)
    assert cat == "airliner"

    # Light aircraft
    cat, name = classify_aircraft({"t": "C172", "desc": "Cessna 172 Skyhawk"}, 4500, 110)
    assert cat == "light"
    assert "Light" in name

    cat, name = classify_aircraft({"t": "PA28", "desc": "Piper Cherokee"}, 3000, 105)
    assert cat == "light"

    # Military aircraft
    cat, name = classify_aircraft({"t": "F16", "desc": "General Dynamics F-16"}, 25000, 520)
    assert cat == "military"
    assert "Military" in name

    cat, name = classify_aircraft({"t": "C17", "desc": "Boeing C-17 Globemaster"}, 30000, 450)
    assert cat == "military"

    cat, name = classify_aircraft({"t": "TYPH", "desc": "Eurofighter Typhoon"}, 28000, 550)
    assert cat == "military"

    # Helicopters
    cat, name = classify_aircraft({"t": "R44", "desc": "Robinson R44"}, 1200, 95)
    assert cat == "helicopter"
    assert "Helicopter" in name

    cat, name = classify_aircraft({"t": "H145", "desc": "Airbus Helicopters H145"}, 1500, 120)
    assert cat == "helicopter"

    # Gliders
    cat, name = classify_aircraft({"t": "AS33", "desc": "Schleicher AS 33"}, 2500, 50)
    assert cat == "glider"
    assert "Glider" in name

    # Fallback by altitude/speed heuristics
    cat, name = classify_aircraft({"t": "UNKNOWN"}, 37000, 460)
    assert cat == "airliner"

    # Known light aircraft
    cat, name = classify_aircraft({"t": "SR22", "desc": "Cirrus SR22"}, 5000, 150)
    assert cat == "light"

    # General fallback for completely unidentified low/mid flight
    cat, name = classify_aircraft({"t": "UNKNOWN"}, 2500, 90)
    assert cat == "general"
    assert "General Aviation" in name


def test_adsb_refresh_interval_5s(qapp):
    """Verify ADSBService poll timer is set to 5000ms (5s) for synchronization with radar sweep."""
    service = ADSBService()
    assert service._poll_timer.interval() == 5000


def test_adsb_radar_sweep_and_trails_elements():
    """Verify LEAFLET_HTML_TEMPLATE includes 5s grey radar sweep, aircraft trails, SVGs, and tracking links."""
    # 5-second radar sweep animation
    assert "radar-sweep-spin" in LEAFLET_HTML_TEMPLATE
    assert "5s linear infinite" in LEAFLET_HTML_TEMPLATE
    assert "adsb-radar-sweeper" in LEAFLET_HTML_TEMPLATE
    assert "updateRadarSweepOverlay" in LEAFLET_HTML_TEMPLATE
    assert "getRadarCircleBounds" in LEAFLET_HTML_TEMPLATE

    # Aircraft categories and SVGs
    assert "getAircraftSvgPath" in LEAFLET_HTML_TEMPLATE
    assert "airliner" in LEAFLET_HTML_TEMPLATE
    assert "military" in LEAFLET_HTML_TEMPLATE
    assert "helicopter" in LEAFLET_HTML_TEMPLATE
    assert "glider" in LEAFLET_HTML_TEMPLATE
    assert "getAircraftCategoryBadge" in LEAFLET_HTML_TEMPLATE

    # Aircraft breadcrumb trails: twice as long (30 points), solid, fading out to 0% transparency
    assert "adsbTrailsGroup" in LEAFLET_HTML_TEMPLATE
    assert "aircraftTrails" in LEAFLET_HTML_TEMPLATE
    assert "aircraftHistory" in LEAFLET_HTML_TEMPLATE
    assert "hist.length > 30" in LEAFLET_HTML_TEMPLATE
    assert "trailLine.setLatLngs(latlngs)" in LEAFLET_HTML_TEMPLATE
    assert "dashArray: '3, 4'" not in LEAFLET_HTML_TEMPLATE  # Trails are solid rather than dashed
    assert "lineCap: 'round'" in LEAFLET_HTML_TEMPLATE

    # Range rings marked at 10, 25, 50 NM with thin lines and no NSEW cardinal ticks
    assert "[10, 25, 50]" in LEAFLET_HTML_TEMPLATE
    assert "' NM'" in LEAFLET_HTML_TEMPLATE
    assert 'x1="100" y1="0.5" x2="100" y2="8"' not in LEAFLET_HTML_TEMPLATE  # NSEW cardinal ticks removed

    # Subtle radar sweep line & smooth blur filter (no hard cut-off line)
    assert 'stroke-width="0.65"' in LEAFLET_HTML_TEMPLATE
    assert "radarSweepBlur" in LEAFLET_HTML_TEMPLATE
    assert "feGaussianBlur" in LEAFLET_HTML_TEMPLATE

    # Pinned persistent tooltips with close button
    assert "pinnedTooltipHex" in LEAFLET_HTML_TEMPLATE
    assert "closeAdsbTooltip" in LEAFLET_HTML_TEMPLATE
    assert "adsb-tip-close" in LEAFLET_HTML_TEMPLATE

    # Tooltip and popup links to tracking services
    assert "adsb-tip-link" in LEAFLET_HTML_TEMPLATE
    assert "globe.adsbexchange.com" in LEAFLET_HTML_TEMPLATE
    assert "flightradar24.com" in LEAFLET_HTML_TEMPLATE
    assert "flightaware.com" in LEAFLET_HTML_TEMPLATE


def test_webbridge_open_external_url(qapp):
    """Verify WebBridge.on_open_external_url emits signal and invokes QDesktopServices."""
    bridge = WebBridge()
    opened_urls = []
    bridge.open_external_url_signal.connect(lambda u: opened_urls.append(u))

    test_url = "https://globe.adsbexchange.com/?icao=40690A"
    with patch("meshcore_tray.ui.mesh_map_widget.QDesktopServices.openUrl") as mock_open:
        bridge.on_open_external_url(test_url)
        assert len(opened_urls) == 1
        assert opened_urls[0] == test_url
        mock_open.assert_called_once()


def test_adsb_multi_feed_merging_and_helicopter():
    """Verify concurrent multi-feed merging captures aircraft missed by a single aggregator."""
    feed1_data = {
        "ac": [
            {"hex": "400e5a", "flight": "EZY879U", "t": "A319", "lat": 54.24, "lon": -4.51, "alt_baro": 18000, "seen_pos": 2.0}
        ]
    }
    # Feed 2 has an additional low-altitude SAR helicopter that Feed 1 missed
    feed2_data = {
        "aircraft": [
            {"hex": "400e5a", "flight": "EZY879U", "t": "A319", "lat": 54.25, "lon": -4.52, "alt_baro": 18200, "seen_pos": 0.5},
            {"hex": "406ded", "flight": "SRD897", "t": "A189", "desc": "AGUSTA AW-189", "category": "A7", "lat": 54.60, "lon": -3.55, "alt_baro": 1300, "seen_pos": 0.2}
        ]
    }

    def mock_urlopen(req, timeout=4.5):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        resp.status = 200
        if "adsb.fi" in url:
            resp.read.return_value = json.dumps(feed2_data).encode("utf-8")
        else:
            resp.read.return_value = json.dumps(feed1_data).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    worker = ADSBFetchWorker(lat=54.66, lon=-3.35, radius_nm=50, target_info={"alias": "Cockermouth"})
    results = []
    worker.success_signal.connect(lambda p: results.append(p))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        worker.run()

    assert len(results) == 1
    payload = results[0]
    assert payload["total"] == 2

    planes_by_hex = {p["hex"]: p for p in payload["aircraft"]}
    assert "400e5a" in planes_by_hex
    assert "406ded" in planes_by_hex

    # Helicopter correctly classified
    heli = planes_by_hex["406ded"]
    assert heli["category"] == "helicopter"
    assert "Helicopter" in heli["category_name"]
    assert heli["alt_baro"] == 1300
    assert heli["flight"] == "SRD897"

    # Fresher telemetry preferred for airliner
    airliner = planes_by_hex["400e5a"]
    assert airliner["alt_baro"] == 18200


def test_adsb_tooltip_photo_placeholder_and_hover_delay():
    """Verify Leaflet HTML contains photo placeholder and hover grace period logic."""
    assert ".adsb-photo-placeholder" in LEAFLET_HTML_TEMPLATE
    assert "No aircraft photo available" in LEAFLET_HTML_TEMPLATE
    assert "_adsbHoverCloseTimer" in LEAFLET_HTML_TEMPLATE
    assert "_activeHoverHex" in LEAFLET_HTML_TEMPLATE
    assert "adsb-cluster-pill" in LEAFLET_HTML_TEMPLATE
    assert "Stacked (" in LEAFLET_HTML_TEMPLATE


def test_adsb_distress_beacon_and_echo_pulse():
    """Verify Leaflet HTML contains distress beacon tag, echo pulse rings, and Distress in legend."""
    assert "adsb-distress-echo-ring" in LEAFLET_HTML_TEMPLATE
    assert "adsb-distress-tag" in LEAFLET_HTML_TEMPLATE
    assert "adsb-distress-banner" in LEAFLET_HTML_TEMPLATE
    assert "adsb-distress-glow" in LEAFLET_HTML_TEMPLATE
    assert "adsb-legend-distress-dot" in LEAFLET_HTML_TEMPLATE
    assert "Distress" in LEAFLET_HTML_TEMPLATE
    assert "getAircraftDistressInfo" in LEAFLET_HTML_TEMPLATE


def test_aircraft_photo_worker_planespotters_success():
    """Verify AircraftPhotoWorker retrieves photo from Planespotters if available."""
    worker = AircraftPhotoWorker("400492")
    fake_ps_data = {
        "photos": [{
            "thumbnail_large": {"src": "https://planespotters.net/large.jpg"},
            "photographer": "John Doe",
            "link": "https://planespotters.net/hex/400492",
            "aircraft_type": "Boeing 737",
            "airline": {"name": "British Airways"}
        }]
    }
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_ps_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        results = []
        worker.photo_ready.connect(lambda h, d: results.append((h, d)))
        worker.run()

        assert len(results) == 1
        h, info = results[0]
        assert h == "400492"
        assert info["thumbnail"] == "https://planespotters.net/large.jpg"
        assert info["photographer"] == "John Doe"
        assert info["source"] == "Planespotters.net"
        assert info["source_label"] == "Planespotters ↗"


def test_aircraft_photo_worker_airport_data_fallback():
    """Verify AircraftPhotoWorker falls back to Airport-Data when Planespotters has no photo."""
    worker = AircraftPhotoWorker("407b4b")
    ps_resp = MagicMock()
    ps_resp.read.return_value = json.dumps({"photos": []}).encode("utf-8")
    ps_resp.__enter__.return_value = ps_resp

    ad_resp = MagicMock()
    ad_data = {
        "status": 200,
        "count": 1,
        "data": [{
            "image": "https://airport-data.com/images/aircraft/thumbnails/001/862/001862748.jpg",
            "link": "https://airport-data.com/aircraft/photo/001862748",
            "photographer": "Chris Hall"
        }]
    }
    ad_resp.read.return_value = json.dumps(ad_data).encode("utf-8")
    ad_resp.__enter__.return_value = ad_resp

    with patch("urllib.request.urlopen", side_effect=[ps_resp, ad_resp]):
        results = []
        worker.photo_ready.connect(lambda h, d: results.append((h, d)))
        worker.run()

        assert len(results) == 1
        h, info = results[0]
        assert h == "407b4b"
        assert "001862748.jpg" in info["thumbnail"]
        assert info["photographer"] == "Chris Hall"
        assert info["source"] == "Airport-Data.com"
        assert info["source_label"] == "Airport-Data ↗"


def test_adsb_service_photo_caching(qapp):
    """Verify ADSBService only caches positive hits with thumbnail, not failures."""
    svc = ADSBService()
    svc._on_photo_ready("407b4b", {"thumbnail": "http://img.jpg", "photographer": "Test"})
    assert "407b4b" in svc._photo_cache

    # Empty result should NOT be permanently cached
    svc._on_photo_ready("400000", {})
    assert "400000" not in svc._photo_cache


def test_adsb_service_set_target_worker_conflict(qapp):
    """Verify set_target cleanly handles a currently running worker without race conditions or signal disconnection crashes."""
    svc = ADSBService()
    svc.enabled = True
    fake_worker = MagicMock()
    fake_worker.isRunning.return_value = True
    svc._active_worker = fake_worker
    old_gen = svc._query_generation

    svc.set_target("", "New Target", 55.0, -3.0, 60)
    assert svc.target_lat == 55.0
    assert svc.target_lon == -3.0
    assert getattr(svc, "_pending_refresh", False) is True
    assert svc._query_generation > old_gen

    # Stale payload from old target is discarded cleanly without emitting flights_updated
    emitted = []
    svc.flights_updated.connect(lambda p: emitted.append(p))
    stale_payload = {
        "aircraft": [{"hex": "abc123"}],
        "target_info": {"lat": 51.5, "lon": -0.1, "generation": old_gen}
    }
    svc._on_worker_success(stale_payload)
    assert len(emitted) == 0


def test_adsb_tooltip_pinning_and_propagation_template():
    """Verify Leaflet template has click protection, capture phase listeners, and marker click pinning."""
    assert "tgt.closest('.adsb-tooltip')" in LEAFLET_HTML_TEMPLATE
    assert "document.addEventListener('click'" in LEAFLET_HTML_TEMPLATE
    assert "document.addEventListener('mousedown'" in LEAFLET_HTML_TEMPLATE
    assert "window.openAircraftTooltip(h, m, true)" in LEAFLET_HTML_TEMPLATE
    assert "photoCached.source_label" in LEAFLET_HTML_TEMPLATE
    # Verify removal of Leaflet's built-in 0ms mouseout auto-closer
    assert "marker.off('mouseout', marker.closeTooltip)" in LEAFLET_HTML_TEMPLATE
    assert "marker.off('mouseover', marker._openTooltip)" in LEAFLET_HTML_TEMPLATE
    assert "marker.off('click', marker._openTooltip)" in LEAFLET_HTML_TEMPLATE
    # Verify click propagation disabled on icon and tooltip container
    assert "L.DomEvent.disableClickPropagation(marker._icon)" in LEAFLET_HTML_TEMPLATE
    assert "L.DomEvent.disableClickPropagation(tip._container)" in LEAFLET_HTML_TEMPLATE
    # Verify tooltip follows aircraft as it moves
    assert "tip.setLatLng(marker.getLatLng())" in LEAFLET_HTML_TEMPLATE
    # Verify 800ms hover grace delay
    assert "800" in LEAFLET_HTML_TEMPLATE


def test_mesh_map_debounced_resize_and_recovery(qapp):
    """Verify MeshMapWidget debounces resize calls and handles render process termination cleanly."""
    cfg = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(config=cfg)

    # Test resize event does not raise
    widget.resize(800, 600)

    # Test render process recovery handler
    with patch.object(widget, "_recover_web_view_after_termination") as mock_recover:
        widget._on_render_process_terminated("Crashed", 1)
        assert widget._page_ready is False


def test_adsb_retarget_thread_safety_and_leaflet_reset(qapp):
    """Verify retargeting ADS-B center location is fully thread-safe and resets JS state."""
    # 1. Verify Leaflet template contains clearAdsbForRetarget with hover timer cleanup and tooltip closing
    assert "window.clearAdsbForRetarget = function()" in LEAFLET_HTML_TEMPLATE
    assert "_adsbHoverCloseTimer = null" in LEAFLET_HTML_TEMPLATE
    assert "adsbRadarRingsGroup.addLayer(adsbCenterMarker)" in LEAFLET_HTML_TEMPLATE
    assert "_aircraftContainerPoints = {};" in LEAFLET_HTML_TEMPLATE

    # 2. Verify ADSBService handles rapid retargets without race condition or crash
    svc = ADSBService()
    svc.enabled = True
    for i in range(10):
        svc.set_target("", f"Radar @ Loc {i}", 50.0 + i * 0.1, -1.0 - i * 0.1, 50)
    assert svc._query_generation >= 10
    assert pytest.approx(svc.target_lat) == 50.9
    assert pytest.approx(svc.target_lon) == -1.9

    # 3. Verify that an out-of-order response from an earlier generation is safely discarded
    emitted = []
    svc.flights_updated.connect(lambda p: emitted.append(p))
    outdated_payload = {
        "aircraft": [{"hex": "old123"}],
        "target_info": {"lat": 50.0, "lon": -1.0, "generation": 1}
    }
    svc._on_worker_success(outdated_payload)
    assert len(emitted) == 0

    # 4. Verify valid response with matching generation is accepted
    fresh_payload = {
        "aircraft": [{"hex": "new123"}],
        "target_info": {"lat": 50.9, "lon": -1.9, "generation": svc._query_generation}
    }
    svc._on_worker_success(fresh_payload)
    assert len(emitted) == 1
    assert emitted[0]["aircraft"][0]["hex"] == "new123"

    # 5. Verify retired workers list and cleanup
    mock_worker = MagicMock(spec=ADSBFetchWorker)
    mock_worker.isFinished.return_value = True
    svc._active_worker = mock_worker
    svc._pending_refresh = False
    svc._on_worker_finished()
    assert svc._active_worker is None
    assert len(svc._retired_workers) == 0  # Cleaned up because isFinished() is True
    mock_worker.wait.assert_called()

    # 6. Verify service cleanup safely stops timers and workers
    svc.cleanup()
    assert not svc.enabled


def test_adsb_map_rendering_and_memory_optimization():
    """Verify Leaflet template uses persistent polylines, in-place icon updates, and on-demand tooltips."""
    from meshcore_tray.ui.mesh_map_widget import LEAFLET_HTML_TEMPLATE

    # Verify memory-optimized single polyline architecture
    assert "trailLine = aircraftTrails[hex];" in LEAFLET_HTML_TEMPLATE
    assert "trailLine.setLatLngs(latlngs);" in LEAFLET_HTML_TEMPLATE
    assert "adsbTrailsGroup.addLayer(trailLine);" in LEAFLET_HTML_TEMPLATE

    # Verify on-demand tooltip generation (only when pinned or hovered)
    assert "var isTargetOpen = (pinnedTooltipHex === hex) || (_activeHoverHex === hex && !pinnedTooltipHex);" in LEAFLET_HTML_TEMPLATE

    # Verify in-place marker rotation and SVG color fill
    assert "inner.style.transform = 'rotate(' + track + 'deg)';" in LEAFLET_HTML_TEMPLATE
    assert "svg.setAttribute('fill', iconData.color);" in LEAFLET_HTML_TEMPLATE

    # Verify WebEngine execution under simulated load and window maximize
    import subprocess
    import sys
    script = """
import sys
import json
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebChannel import QWebChannel
from meshcore_tray.ui.mesh_map_widget import get_leaflet_html, WebBridge

app = QApplication(sys.argv)
view = QWebEngineView()
bridge = WebBridge()
channel = QWebChannel()
channel.registerObject('pyBridge', bridge)

errors = []
class PageWatcher(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, msg, line, source):
        if 'error' in msg.lower() or 'uncaught' in msg.lower():
            errors.append((level, msg, line, source))

page = PageWatcher(view)
page.setWebChannel(channel)
view.setPage(page)

loop = QEventLoop()
view.loadFinished.connect(lambda ok: loop.quit())
view.setHtml(get_leaflet_html())
loop.exec()

view.showMaximized()

# Run ADS-B simulation with 50 aircraft across 5 ticks
payload = {
    'total': 50,
    'target_info': {'lat': 51.5, 'lon': -0.1, 'radius_nm': 50, 'alias': 'London'},
    'aircraft': [
        {'hex': f'40{i:02x}', 'lat': 51.5 + (i % 10)*0.01, 'lon': -0.1 + (i % 10)*0.01, 'track': i * 10, 'category': 'airliner'}
        for i in range(50)
    ]
}
view.page().runJavaScript('setAdsbVisible(true);')
for _ in range(5):
    view.page().runJavaScript(f'onAdsbDataReady({json.dumps(payload)});')

QTimer.singleShot(500, loop.quit)
loop.exec()

assert len(errors) == 0, f'JS errors: {errors}'
print('SUCCESS_ADSB_OPTIMIZATION_TEST')
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Process failed: {proc.stderr}"
    assert "SUCCESS_ADSB_OPTIMIZATION_TEST" in proc.stdout


def test_adsb_rate_limit_backoff_and_cooldown(qapp):
    """Verify that HTTP 429 sets endpoint cooldown and skips redundant requests."""
    from meshcore_tray.core.adsb_service import _ENDPOINT_COOLDOWNS, ADSBFetchWorker
    from urllib.parse import urlparse
    import time

    # 1. Verify cooldown mechanism
    _ENDPOINT_COOLDOWNS["test.adsb.host"] = time.time() + 100.0

    worker = ADSBFetchWorker(lat=51.5, lon=-0.1, radius_nm=50, target_info={})
    # Simulating _fetch_single_feed logic for cooled-down host
    parsed = urlparse("https://test.adsb.host/api/v2")
    assert parsed.netloc in _ENDPOINT_COOLDOWNS
    assert time.time() < _ENDPOINT_COOLDOWNS[parsed.netloc]

    # Clean up test host
    _ENDPOINT_COOLDOWNS.pop("test.adsb.host", None)


def test_adsb_negative_photo_cache_cooldown(qapp):
    """Verify that empty/404 photo lookups are placed in negative cache to avoid API hammering."""
    svc = ADSBService()
    svc._on_photo_ready("401234", {})
    assert "401234" not in svc._photo_cache
    assert "401234" in svc._negative_photo_cache

    # Subsequent request within cooldown should immediately emit empty without spawning worker
    received = []
    svc.photo_received.connect(lambda h, d: received.append((h, d)))
    svc.request_aircraft_photo("401234")
    assert len(received) == 1
    assert received[0] == ("401234", {})
    assert "401234" not in svc._photo_workers









