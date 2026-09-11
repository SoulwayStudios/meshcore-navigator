"""Unit tests for ElevationService, ViewshedService, and ElevationProfileWidget."""

import math
from pathlib import Path
import tempfile
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from meshcore_tray.core.elevation_service import (
    haversine_distance_m,
    forward_bearing_deg,
    interpolate_points,
    earth_curvature_bulge_m,
    fresnel_zone_radius_m,
    ElevationCache,
    ElevationProfileWorker,
    ElevationService,
)
from meshcore_tray.core.viewshed_service import (
    destination_point,
    ViewshedWorker,
    ViewshedService,
)
from meshcore_tray.ui.elevation_profile_widget import (
    ElevationCanvasWidget,
    ElevationProfileWidget,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestElevationMath:
    def test_haversine_distance(self):
        # London (51.5074, -0.1278) to Paris (48.8566, 2.3522) ~ 343 km
        dist = haversine_distance_m(51.5074, -0.1278, 48.8566, 2.3522)
        assert 340000 < dist < 350000

        # Same point should be 0
        assert haversine_distance_m(51.5, -0.1, 51.5, -0.1) < 0.1

    def test_forward_bearing(self):
        # Due north
        b_north = forward_bearing_deg(50.0, 0.0, 51.0, 0.0)
        assert abs(b_north - 0.0) < 0.1 or abs(b_north - 360.0) < 0.1

        # Due east
        b_east = forward_bearing_deg(0.0, 0.0, 0.0, 1.0)
        assert abs(b_east - 90.0) < 0.5

    def test_destination_point(self):
        lat1, lon1 = 51.5, -0.1
        # Move 10 km north
        lat2, lon2 = destination_point(lat1, lon1, 0.0, 10000.0)
        dist = haversine_distance_m(lat1, lon1, lat2, lon2)
        assert abs(dist - 10000.0) < 20.0
        assert lat2 > lat1

    def test_interpolate_points(self):
        lat1, lon1 = 51.0, 0.0
        lat2, lon2 = 52.0, 0.0
        pts = interpolate_points(lat1, lon1, lat2, lon2, num_points=10)
        assert len(pts) == 10
        assert pts[0][0] == pytest.approx(51.0, abs=0.01)
        assert pts[-1][0] == pytest.approx(52.0, abs=0.01)
        assert pts[0][2] == 0.0
        assert pts[-1][2] > 100000.0

    def test_earth_curvature_bulge(self):
        # At midpoint of 20km link with standard 4/3 refraction:
        # h = (10,000 * 10,000) / (2 * (4/3) * 6371000) ~ 5.88 m
        bulge_radio = earth_curvature_bulge_m(10000.0, 20000.0, k=1.3333333333333333)
        assert 5.5 < bulge_radio < 6.2

        # At endpoints (d1=0 or d2=0), bulge is 0
        assert earth_curvature_bulge_m(0.0, 20000.0) == 0.0
        assert earth_curvature_bulge_m(20000.0, 20000.0) == 0.0

    def test_fresnel_zone_radius(self):
        # 868 MHz: wavelength lambda = 3e8 / 868e6 = 0.3456 m
        # At midpoint of 10km link (d1=5000, d2=5000):
        # r1 = sqrt((0.3456 * 5000 * 5000) / 10000) = sqrt(864) ~ 29.39 m
        r1 = fresnel_zone_radius_m(5000.0, 5000.0, freq_mhz=868.0)
        assert 28.0 < r1 < 31.0


class TestElevationCache:
    def test_cache_persistence_and_quantization(self, tmp_path):
        db_path = tmp_path / "test_elev.db"
        cache = ElevationCache(db_path=db_path)

        # Store elevations
        coords_dict = {
            (51.50004, -0.12003): 25.4,
            (52.12344, 0.45671): 78.9,
        }
        cache.put_many(coords_dict)

        # Retrieve using slightly different float (within 4 decimals)
        cached = cache.get_many([(51.50001, -0.12002), (52.12342, 0.45674)])
        assert len(cached) == 2
        assert (51.5000, -0.1200) in cached
        assert cached[(51.5000, -0.1200)] == pytest.approx(25.4, abs=0.1)


class TestProfileAndClearanceWorker:
    def test_worker_clear_los(self, tmp_path):
        db_path = tmp_path / "test_worker.db"
        cache = ElevationCache(db_path=db_path)

        # Flat terrain at 10m ASL along a 5km path with 35m towers for 100% 1st Fresnel clearance
        worker = ElevationProfileWorker(
            lat1=51.0, lon1=0.0,
            lat2=51.045, lon2=0.0,
            tx_height_m=35.0,
            rx_height_m=35.0,
            freq_mhz=868.0,
            num_points=25,
            cache=cache,
        )

        # Mock query so no network request is made
        coords = [pt[:2] for pt in interpolate_points(51.0, 0.0, 51.045, 0.0, 25)]
        cache.put_many({(c[0], c[1]): 10.0 for c in coords})

        results = []
        worker.profile_ready.connect(results.append)
        worker.run()

        assert len(results) == 1
        payload = results[0]
        assert payload["status"] == "CLEAR"
        assert payload["min_optical_clearance_m"] > 0.0
        assert payload["min_fresnel_clearance_m"] > 0.0
        assert payload["total_distance_km"] > 4.0
        assert len(payload["points"]) == 25

    def test_worker_obstructed_los(self, tmp_path):
        db_path = tmp_path / "test_worker2.db"
        cache = ElevationCache(db_path=db_path)

        worker = ElevationProfileWorker(
            lat1=51.0, lon1=0.0,
            lat2=51.09, lon2=0.0,
            tx_height_m=2.0,
            rx_height_m=2.0,
            freq_mhz=868.0,
            num_points=25,
            cache=cache,
        )

        coords = [pt[:2] for pt in interpolate_points(51.0, 0.0, 51.09, 0.0, 25)]
        # Insert a high mountain ridge in the middle
        elev_map = {}
        for i, c in enumerate(coords):
            if 10 <= i <= 14:
                elev_map[(c[0], c[1])] = 300.0  # mountain ridge
            else:
                elev_map[(c[0], c[1])] = 20.0
        cache.put_many(elev_map)

        results = []
        worker.profile_ready.connect(results.append)
        worker.run()

        assert len(results) == 1
        payload = results[0]
        assert payload["status"] == "OBSTRUCTED"
        assert payload["min_optical_clearance_m"] < 0.0
        assert payload["worst_obstacle"] is not None


class TestViewshedWorker:
    def test_viewshed_computation(self, tmp_path):
        db_path = tmp_path / "test_viewshed.db"
        cache = ElevationCache(db_path=db_path)

        # 10 km radius viewshed with 12 rays and 10 samples/ray
        worker = ViewshedWorker(
            center_lat=51.5,
            center_lon=-0.12,
            tx_height_m=15.0,
            rx_height_m=2.0,
            radius_km=10.0,
            num_rays=12,
            samples_per_ray=10,
            cache=cache,
        )

        # Pre-seed flat terrain 30m ASL
        coords_to_seed = {(51.5, -0.12): 30.0}
        for az_idx in range(12):
            az = az_idx * (360.0 / 12)
            for s in range(1, 11):
                d = (s / 10) * 10000.0
                p = destination_point(51.5, -0.12, az, d)
                coords_to_seed[(p[0], p[1])] = 30.0
        cache.put_many(coords_to_seed)
        worker._fetch_all_tiles = lambda *args: {}
        worker._fetch_open_meteo = lambda coords: {}

        results = []
        worker.viewshed_ready.connect(results.append)
        worker.run()

        assert len(results) == 1
        data = results[0]
        assert data["radius_km"] == 10.0
        assert data["visible_pct"] > 80.0  # Flat terrain should have high visibility
        assert "image_data_url" in data and data["image_data_url"].startswith("data:image/png;base64,")
        assert "bounds" in data and len(data["bounds"]) == 2
        assert data["geojson"]["type"] == "FeatureCollection"
        assert len(data["geojson"]["features"]) == 1
        assert data["geojson"]["features"][0]["geometry"]["type"] == "MultiPolygon"

    def test_viewshed_high_res_tile_computation(self, tmp_path):
        from PIL import Image
        db_path = tmp_path / "test_viewshed_hires.db"
        cache = ElevationCache(db_path=db_path)
        worker = ViewshedWorker(
            center_lat=54.67,
            center_lon=-3.45,
            tx_height_m=10.0,
            rx_height_m=2.0,
            radius_km=5.0,
            cache=cache,
        )
        # Mock 256x256 tile of flat 50m ASL: (R*256 + G + B/256) - 32768 = 50 -> R=128, G=50, B=0
        mock_tile = Image.new("RGB", (256, 256), (128, 50, 0))
        worker._fetch_all_tiles = lambda zoom, coords: {c: mock_tile for c in coords}

        results = []
        worker.viewshed_ready.connect(results.append)
        worker.run()

        assert len(results) == 1
        data = results[0]
        assert data["radius_km"] == 5.0
        assert data["visible_pct"] > 80.0
        assert data["image_data_url"].startswith("data:image/png;base64,")
        assert len(data["bounds"]) == 2

    def test_viewshed_service_with_observer_alias(self, tmp_path):
        service = ViewshedService()
        # Ensure calculate_viewshed accepts observer_alias and keyword arguments without TypeError
        service.calculate_viewshed(
            center_lat=54.5,
            center_lon=-2.5,
            tx_height_m=12.0,
            rx_height_m=3.0,
            radius_km=15.0,
            observer_alias="GB3CF Repeater",
            num_rays=36,
            samples_per_ray=15
        )
        assert service._active_worker is not None
        assert service._active_worker.observer_alias == "GB3CF Repeater"
        assert service._active_worker.tx_height_m == 12.0
        if service._active_worker.isRunning():
            service._active_worker.terminate()
            service._active_worker.wait(200)


class TestElevationProfileWidgetUI:
    def test_profile_widget_display_and_signals(self, qapp):
        widget = ElevationProfileWidget()
        assert widget.canvas is not None
        assert widget.status_badge is not None

        # Set clear profile
        sample_profile = {
            "alias1": "Home Base",
            "alias2": "Repeater 1",
            "lat1": 51.5,
            "lon1": -0.12,
            "lat2": 51.6,
            "lon2": -0.15,
            "tx_height_m": 8.0,
            "rx_height_m": 15.0,
            "total_distance_km": 11.4,
            "bearing_deg": 345.0,
            "status": "CLEAR",
            "status_label": "Line of Sight Clear",
            "min_optical_clearance_m": 14.2,
            "min_fresnel_clearance_m": 6.5,
            "points": [
                {
                    "distance_km": 0.0,
                    "distance_m": 0.0,
                    "lat": 51.5,
                    "lon": -0.12,
                    "terrain_asl_m": 25.0,
                    "curvature_bulge_m": 0.0,
                    "optical_los_asl_m": 33.0,
                    "fresnel_lower_asl_m": 33.0,
                    "optical_clearance_m": 8.0,
                    "fresnel_clearance_m": 8.0,
                    "is_blocked": False,
                },
                {
                    "distance_km": 11.4,
                    "distance_m": 11400.0,
                    "lat": 51.6,
                    "lon": -0.15,
                    "terrain_asl_m": 45.0,
                    "curvature_bulge_m": 0.0,
                    "optical_los_asl_m": 60.0,
                    "fresnel_lower_asl_m": 60.0,
                    "optical_clearance_m": 15.0,
                    "fresnel_clearance_m": 15.0,
                    "is_blocked": False,
                }
            ],
            "worst_obstacle": None
        }

        widget.set_profile(sample_profile)
        assert "Home Base" in widget.path_label.text()
        assert "Repeater 1" in widget.path_label.text()
        assert "Clear" in widget.status_badge.text()

        # Test height changed signal
        emitted_heights = []
        widget.heights_changed.connect(lambda tx, rx: emitted_heights.append((tx, rx)))
        widget.spin_tx.setValue(12.0)
        assert len(emitted_heights) == 1
        assert emitted_heights[0][0] == 12.0


class TestMeshMapWidgetLOSIntegration:
    def test_map_widget_los_components(self, qapp, monkeypatch):
        from meshcore_tray.ui.mesh_map_widget import MeshMapWidget

        # Create widget
        widget = MeshMapWidget(storage=None, config=None)
        assert hasattr(widget, "map_splitter")
        assert hasattr(widget, "elevation_profile_dock")
        assert widget.elevation_profile_dock.isHidden()

        if hasattr(widget, "los_controls"):
            assert hasattr(widget, "btn_toggle_los")
            assert hasattr(widget, "btn_los_ground")
            assert hasattr(widget, "btn_los_rooftop")
            assert hasattr(widget, "btn_los_mast")
            assert hasattr(widget, "combo_los_radius")
            assert hasattr(widget, "btn_profile_path")

            # Check defaults
            assert widget.btn_los_rooftop.isChecked()
            assert widget.combo_los_radius.currentText() == "25 km"
            assert not widget.btn_toggle_los.isChecked()

            # Test height selection
            widget.btn_los_mast.click()
            assert widget._current_tx_height == 15.0

            widget.btn_los_ground.click()
            assert widget._current_tx_height == 2.0

            # Test radius selection
            widget.combo_los_radius.setCurrentText("50 km")
            assert widget._current_los_radius == 50.0

            # Test profile path button toggle
            widget.btn_profile_path.click()
            assert widget.btn_profile_path.isChecked()
            widget.btn_profile_path.click()
            assert not widget.btn_profile_path.isChecked()

    def test_map_widget_profile_docking_and_closing(self, qapp):
        from meshcore_tray.ui.mesh_map_widget import MeshMapWidget

        widget = MeshMapWidget(storage=None, config=None)
        assert widget.elevation_profile_dock.isHidden()

        # Simulate profile ready
        sample_payload = {
            "alias1": "Station",
            "alias2": "Remote",
            "lat1": 51.5,
            "lon1": -0.12,
            "lat2": 51.55,
            "lon2": -0.10,
            "tx_height_m": 8.0,
            "rx_height_m": 2.0,
            "total_distance_km": 5.7,
            "bearing_deg": 45.0,
            "status": "CLEAR",
            "status_label": "Line of Sight Clear",
            "min_optical_clearance_m": 12.0,
            "min_fresnel_clearance_m": 4.5,
            "points": [
                {
                    "distance_km": 0.0,
                    "distance_m": 0.0,
                    "lat": 51.5,
                    "lon": -0.12,
                    "terrain_asl_m": 20.0,
                    "curvature_bulge_m": 0.0,
                    "optical_los_asl_m": 28.0,
                    "fresnel_lower_asl_m": 28.0,
                    "optical_clearance_m": 8.0,
                    "fresnel_clearance_m": 8.0,
                    "is_blocked": False,
                }
            ],
            "worst_obstacle": None
        }

        widget.resize(1000, 600)
        widget.show()
        qapp.processEvents()

        widget._on_elevation_profile_ready(sample_payload)
        qapp.processEvents()
        assert not widget.elevation_profile_dock.isHidden()

        # Check splitter sizes are split ~ 4/5ths and 1/5th
        sizes = widget.map_splitter.sizes()
        assert len(sizes) == 2
        assert sizes[0] > sizes[1]

        # Close dock
        widget._on_close_elevation_profile()
        assert widget.elevation_profile_dock.isHidden()

    def test_map_widget_window_state_change_and_debounced_resize(self, qapp):
        from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QWindowStateChangeEvent

        widget = MeshMapWidget()
        widget.resize(800, 600)
        widget.show()
        qapp.processEvents()

        # Check splitter properties
        assert widget.map_splitter.childrenCollapsible() is False

        # Simulate window state change (e.g. user clicking Maximize)
        ev = QEvent(QEvent.Type.WindowStateChange)
        widget.changeEvent(ev)
        qapp.processEvents()

        # Verify timer is scheduled and debounced cleanly
        if hasattr(widget, "_map_resize_timer"):
            assert widget._map_resize_timer.isActive()

        # Trigger viewshed calculation with alias directly on widget
        widget._trigger_viewshed_calc(lat=54.5, lon=-2.5, alias="Test Repeater Node")
        assert widget.viewshed_service._active_worker is not None
        assert widget.viewshed_service._active_worker.observer_alias == "Test Repeater Node"
        if widget.viewshed_service._active_worker.isRunning():
            widget.viewshed_service._active_worker.terminate()
            widget.viewshed_service._active_worker.wait(150)

    def test_los_view_toggle_and_base_layer_selection(self, qapp):
        from meshcore_tray.config import AppConfig
        from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, LEAFLET_HTML_TEMPLATE
        from meshcore_tray.ui.nav_dock import NavDockWidget

        cfg = AppConfig()
        cfg.map_show_rf_los = False
        cfg.map_base_layer = "canvas"

        widget = MeshMapWidget(config=cfg)
        widget.resize(800, 600)
        widget.show()
        qapp.processEvents()

        # 1. Initially LOS controls are hidden to prevent clutter
        if hasattr(widget, "los_controls"):
            assert widget.los_controls.isHidden()

        # 2. Toggle LOS active
        widget.set_los_view_active(True)
        qapp.processEvents()
        if hasattr(widget, "los_controls"):
            assert not widget.los_controls.isHidden()
        assert cfg.map_show_rf_los is True

        # 3. Toggle base layer to topo
        widget.set_base_map_layer("topo")
        assert widget._current_base_layer == "topo"
        assert cfg.map_base_layer == "topo"
        if hasattr(widget, "btn_base_map"):
            assert "Canvas" in widget.btn_base_map.text()

        # 4. Toggle base layer back to canvas
        widget.set_base_map_layer("canvas")
        assert widget._current_base_layer == "canvas"
        assert cfg.map_base_layer == "canvas"
        if hasattr(widget, "btn_base_map"):
            assert "Topo" in widget.btn_base_map.text()

        # 5. NavDock button 10 emits rf_los
        dock = NavDockWidget(config=cfg)
        assert hasattr(dock, "btn_rf_los")
        toggled_events = []
        dock.layer_toggled.connect(lambda key, state: toggled_events.append((key, state)))
        dock.btn_rf_los.setChecked(True)
        assert ("rf_los", True) in toggled_events

        # 6. Leaflet template contains Dark Topo, radial gradient, and layer switching
        assert "dark-topo-tiles" in LEAFLET_HTML_TEMPLATE
        assert "topoBaseLayer" in LEAFLET_HTML_TEMPLATE
        assert "setBaseMapLayer" in LEAFLET_HTML_TEMPLATE
        assert "losRadialGrad" in LEAFLET_HTML_TEMPLATE
        assert "losBeaconPulse" in LEAFLET_HTML_TEMPLATE


