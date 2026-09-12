"""Unit tests for SpaceWeatherService and NOAA SWPC data ingestion."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import base64
import json
import pytest

from meshcore_tray.core.space_weather_service import (
    SpaceWeatherService,
    SpaceWeatherWorker,
    classify_kp_index,
    process_ovation_grid,
)


def test_classify_kp_index():
    """Verify Kp geomagnetic storm scale classifications and colors."""
    status, color, g = classify_kp_index(1.5)
    assert status == "Quiet"
    assert color == "#10B981"
    assert g == "G0"

    status, color, g = classify_kp_index(3.5)
    assert status == "Unsettled"
    assert color == "#3B82F6"
    assert g == "G0"

    status, color, g = classify_kp_index(4.33)
    assert status == "Active"
    assert color == "#F59E0B"
    assert g == "G0"

    status, color, g = classify_kp_index(5.0)
    assert status == "G1 Minor Storm"
    assert color == "#F97316"
    assert g == "G1"

    status, color, g = classify_kp_index(6.67)
    assert status == "G2 Moderate Storm"
    assert color == "#EF4444"
    assert g == "G2"

    status, color, g = classify_kp_index(7.33)
    assert status == "G3 Strong Storm"
    assert color == "#DC2626"
    assert g == "G3"

    status, color, g = classify_kp_index(8.0)
    assert status == "G4 Severe Storm"
    assert color == "#9333EA"
    assert g == "G4"

    status, color, g = classify_kp_index(9.0)
    assert status == "G5 Extreme Storm"
    assert color == "#EC4899"
    assert g == "G5"


def test_process_ovation_grid():
    """Verify conversion of NOAA OVATION coordinates to 360x181 base64 grid."""
    mock_raw = {
        "Observation Time": "2026-09-12T03:19:00Z",
        "Forecast Time": "2026-09-12T04:25:00Z",
        "coordinates": [
            [0, 60, 45],       # Prime meridian, 60N
            [10, 65, 80],      # 10E, 65N
            [350, 70, 30],     # 10W (350), 70N
            [180, -75, 55],    # 180, 75S
        ]
    }

    grid_dict, max_p = process_ovation_grid(mock_raw)
    assert grid_dict["w"] == 360
    assert grid_dict["h"] == 181
    assert max_p == 80
    assert grid_dict["max_prob"] == 80

    decoded = base64.b64decode(grid_dict["b64_grid"])
    assert len(decoded) == 360 * 181

    # Verify point [0, 60, 45]:
    # lon 0 -> lon_adj 0 -> x = 180
    # lat 60 -> y = 150
    # index = 150 * 360 + 180
    assert decoded[150 * 360 + 180] == 45

    # Verify point [10, 65, 80]:
    # lon 10 -> lon_adj 10 -> x = 190
    # lat 65 -> y = 155
    assert decoded[155 * 360 + 190] == 80


def test_space_weather_worker_success(tmp_path: Path):
    """Verify SpaceWeatherWorker fetches, formats, and caches space weather data."""
    mock_kp = [{"time_tag": "2026-09-12T03:00:00", "Kp": 5.67}]
    mock_speed = [{"proton_speed": 485, "time_tag": "2026-09-12T03:22:00Z"}]
    mock_mag = [{"bt": 8.5, "bz_gsm": -4.2, "time_tag": "2026-09-12T03:17:00Z"}]
    mock_flux = [{"flux": 135, "time_tag": "2026-09-11T20:00:00"}]
    mock_scales = {
        "0": {
            "R": {"Scale": "1"},
            "S": {"Scale": "0"},
            "G": {"Scale": "1"}
        }
    }
    mock_ovation = {
        "Observation Time": "2026-09-12T03:19:00Z",
        "Forecast Time": "2026-09-12T04:25:00Z",
        "coordinates": [
            [0, 65, 65],
            [180, 70, 75]
        ]
    }

    worker = SpaceWeatherWorker(cache_dir=tmp_path)
    result_holder = []

    worker.ready_signal.connect(lambda payload: result_holder.append(payload))

    def mock_get(url, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if "planetary-k-index" in url:
            mock_resp.json.return_value = mock_kp
        elif "solar-wind-speed" in url:
            mock_resp.json.return_value = mock_speed
        elif "solar-wind-mag" in url:
            mock_resp.json.return_value = mock_mag
        elif "10cm-flux" in url:
            mock_resp.json.return_value = mock_flux
        elif "noaa-scales" in url:
            mock_resp.json.return_value = mock_scales
        elif "ovation_aurora" in url:
            mock_resp.json.return_value = mock_ovation
        return mock_resp

    with patch("requests.Session.get", side_effect=mock_get):
        worker.run()

    assert len(result_holder) == 1
    payload = result_holder[0]
    assert payload["kp"] == 5.67
    assert payload["kp_status"] == "G1 Minor Storm"
    assert payload["kp_color"] == "#F97316"
    assert payload["g_scale"] == "G1"
    assert payload["solar_wind_speed"] == 485
    assert payload["solar_wind_bz"] == -4.2
    assert payload["solar_flux"] == 135
    assert payload["scales"] == {"R": "1", "S": "0", "G": "1"}
    assert payload["max_prob"] == 75
    assert "b64_grid" in payload["grid"]

    # Verify cache file was written
    cache_file = tmp_path / "space_weather_latest.json"
    assert cache_file.exists()
    cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached_data["kp"] == 5.67


def test_space_weather_worker_offline_fallback(tmp_path: Path):
    """Verify fallback to cached data if network request fails."""
    cached_payload = {
        "kp": 3.0,
        "kp_status": "Unsettled",
        "kp_color": "#3B82F6",
        "solar_wind_speed": 350,
        "solar_wind_bz": 1.2,
        "solar_flux": 100,
        "scales": {"R": "0", "S": "0", "G": "0"},
        "max_prob": 20,
        "grid": {"w": 360, "h": 181, "b64_grid": ""},
    }
    cache_file = tmp_path / "space_weather_latest.json"
    cache_file.write_text(json.dumps(cached_payload), encoding="utf-8")

    worker = SpaceWeatherWorker(cache_dir=tmp_path)
    result_holder = []
    worker.ready_signal.connect(lambda payload: result_holder.append(payload))

    with patch("requests.Session.get", side_effect=RuntimeError("Connection refused")):
        worker.run()

    assert len(result_holder) == 1
    assert result_holder[0]["kp"] == 3.0
    assert result_holder[0]["is_stale"] is True


def test_space_weather_service_lifecycle(ensure_qapp, tmp_path: Path):
    """Verify SpaceWeatherService start_polling and stop_polling control timers properly."""
    service = SpaceWeatherService(cache_dir=tmp_path)
    assert not service._poll_timer.isActive()

    with patch.object(service, "fetch_weather"):
        service.start_polling(interval_min=20)
        assert service._poll_timer.isActive()
        assert service._poll_timer.interval() == 20 * 60 * 1000

        service.stop_polling()
        assert not service._poll_timer.isActive()


def test_space_weather_map_widget_integration(ensure_qapp, tmp_path: Path):
    """Verify MeshMapWidget integrates with SpaceWeatherService and bridge signals."""
    from meshcore_tray.config import AppConfig
    from meshcore_tray.storage import Storage
    from meshcore_tray.ui.mesh_map_widget import MeshMapWidget

    config = AppConfig()
    storage = Storage(tmp_path / "sw_test.db")
    map_widget = MeshMapWidget(config=config, storage=storage)

    assert hasattr(map_widget, "space_weather_service")
    assert not map_widget.show_space_weather

    with patch.object(map_widget.space_weather_service, "fetch_weather"):
        map_widget.set_space_weather(True)
        assert map_widget.show_space_weather
        assert map_widget.config.meshcore.map_show_space_weather is True
        assert map_widget.space_weather_service._poll_timer.isActive()

        # Test opacity change
        map_widget._on_space_weather_opacity_changed(0.85)
        assert map_widget.config.meshcore.space_weather_opacity == 0.85

        # Test bridge signals
        from meshcore_tray.ui.mesh_map_widget import WebBridge
        bridge = map_widget.bridge if hasattr(map_widget, "bridge") else WebBridge()
        if not hasattr(map_widget, "bridge"):
            bridge.space_weather_opacity_signal.connect(map_widget._on_space_weather_opacity_changed)
            bridge.space_weather_toggled_signal.connect(map_widget.set_space_weather)

        bridge.on_space_weather_opacity(0.40)
        assert map_widget.config.meshcore.space_weather_opacity == 0.40

        bridge.on_space_weather_toggled(False)
        assert not map_widget.show_space_weather
        assert not map_widget.space_weather_service._poll_timer.isActive()

    map_widget.cleanup()

