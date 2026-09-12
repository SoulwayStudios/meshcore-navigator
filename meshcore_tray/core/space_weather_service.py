"""Space Weather & Aurora Borealis / Australis Forecast Service for MeshCore Tray.

Fetches, parses, and caches real-time solar activity and aurora probability models
from the authoritative, unauthenticated NOAA Space Weather Prediction Center (SWPC):
- OVATION Aurora Nowcast Model (global 1°x1° probability grid)
- Planetary K-Index (Kp) and geomagnetic storm classification
- Solar wind speed (km/s) and Interplanetary Magnetic Field (IMF Bz in nT)
- 10.7cm Solar Flux Index (SFI in sfu)
- NOAA Space Weather Scales (R, S, G)
"""

from datetime import datetime, timezone
import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
import requests

logger = logging.getLogger("meshcore_tray.space_weather_service")

NOAA_OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
NOAA_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
NOAA_WIND_SPEED_URL = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"
NOAA_WIND_MAG_URL = "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json"
NOAA_10CM_FLUX_URL = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
NOAA_SCALES_URL = "https://services.swpc.noaa.gov/products/noaa-scales.json"

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "meshcore-pixoo-tray" / "space_weather"
USER_AGENT = "MeshCoreNavigator/0.3.0"


def classify_kp_index(kp: float) -> Tuple[str, str, str]:
    """Classify Kp value into (status_label, hex_color, g_scale)."""
    if kp < 3.0:
        return "Quiet", "#10B981", "G0"
    elif kp < 4.0:
        return "Unsettled", "#3B82F6", "G0"
    elif kp < 5.0:
        return "Active", "#F59E0B", "G0"
    elif kp < 6.0:
        return "G1 Minor Storm", "#F97316", "G1"
    elif kp < 7.0:
        return "G2 Moderate Storm", "#EF4444", "G2"
    elif kp < 8.0:
        return "G3 Strong Storm", "#DC2626", "G3"
    elif kp < 9.0:
        return "G4 Severe Storm", "#9333EA", "G4"
    else:
        return "G5 Extreme Storm", "#EC4899", "G5"


def process_ovation_grid(raw_data: dict) -> Tuple[dict, int]:
    """Process NOAA OVATION JSON into a compact 360x181 base64 bytearray grid.

    Grid:
      - Width: 360 (Longitudes 0° to 359°)
      - Height: 181 (Latitudes -90° to +90°)
      - Value: Aurora visibility probability (0 to 100)
    """
    coords = raw_data.get("coordinates", [])
    grid = bytearray(360 * 181)
    max_prob = 0

    for item in coords:
        if len(item) < 3:
            continue
        lon, lat, prob = item[0], item[1], item[2]
        if not (-90 <= lat <= 90):
            continue
        p = min(100, max(0, int(prob)))
        if p > max_prob:
            max_prob = p

        lon_adj = lon if lon < 180 else lon - 360
        x = int(lon_adj + 180) % 360
        y = int(lat) + 90
        grid[y * 360 + x] = p

    b64_grid = base64.b64encode(grid).decode("ascii")

    return {
        "w": 360,
        "h": 181,
        "lon_min": -180,
        "lat_min": -90,
        "res": 1.0,
        "max_prob": max_prob,
        "b64_grid": b64_grid,
    }, max_prob


class SpaceWeatherWorker(QThread):
    """Background thread worker to download and parse NOAA space weather data."""

    ready_signal = pyqtSignal(dict)
    loading_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, cache_dir: Optional[Path] = None):
        super().__init__()
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR

    def run(self):
        try:
            self.loading_signal.emit("Fetching NOAA Space Weather feeds...")
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            session = requests.Session()

            # 1. Telemetry - Kp index
            kp_val = 0.0
            kp_time = ""
            try:
                resp_kp = session.get(NOAA_KP_URL, headers=headers, timeout=5)
                if resp_kp.status_code == 200:
                    kp_data = resp_kp.json()
                    if isinstance(kp_data, list) and len(kp_data) > 0:
                        latest_kp = kp_data[-1]
                        kp_val = float(latest_kp.get("Kp", 0.0))
                        kp_time = latest_kp.get("time_tag", "")
            except Exception as e:
                logger.warning("Could not fetch Kp index: %s", e)

            kp_status, kp_color, g_scale = classify_kp_index(kp_val)

            # 2. Solar wind speed
            wind_speed = None
            try:
                resp_speed = session.get(NOAA_WIND_SPEED_URL, headers=headers, timeout=5)
                if resp_speed.status_code == 200:
                    speed_data = resp_speed.json()
                    if isinstance(speed_data, list) and len(speed_data) > 0:
                        wind_speed = round(float(speed_data[0].get("proton_speed", 0.0)))
            except Exception as e:
                logger.warning("Could not fetch solar wind speed: %s", e)

            # 3. Solar wind magnetic field (Bz / Bt)
            wind_bz = None
            wind_bt = None
            try:
                resp_mag = session.get(NOAA_WIND_MAG_URL, headers=headers, timeout=5)
                if resp_mag.status_code == 200:
                    mag_data = resp_mag.json()
                    if isinstance(mag_data, list) and len(mag_data) > 0:
                        wind_bz = round(float(mag_data[0].get("bz_gsm", 0.0)), 1)
                        wind_bt = round(float(mag_data[0].get("bt", 0.0)), 1)
            except Exception as e:
                logger.warning("Could not fetch solar wind magnetic field: %s", e)

            # 4. Solar flux index (10.7cm SFI)
            sfi_val = None
            try:
                resp_sfi = session.get(NOAA_10CM_FLUX_URL, headers=headers, timeout=5)
                if resp_sfi.status_code == 200:
                    sfi_data = resp_sfi.json()
                    if isinstance(sfi_data, list) and len(sfi_data) > 0:
                        sfi_val = round(float(sfi_data[0].get("flux", 0.0)))
            except Exception as e:
                logger.warning("Could not fetch solar flux index: %s", e)

            # 5. NOAA space weather scales (R, S, G)
            scales = {"R": "0", "S": "0", "G": "0"}
            try:
                resp_scales = session.get(NOAA_SCALES_URL, headers=headers, timeout=5)
                if resp_scales.status_code == 200:
                    scales_data = resp_scales.json()
                    cur = scales_data.get("0", {})
                    scales["R"] = cur.get("R", {}).get("Scale") or "0"
                    scales["S"] = cur.get("S", {}).get("Scale") or "0"
                    scales["G"] = cur.get("G", {}).get("Scale") or g_scale
            except Exception as e:
                logger.warning("Could not fetch NOAA scales: %s", e)

            # 6. OVATION Aurora forecast grid
            self.loading_signal.emit("Downloading NOAA OVATION Aurora grid...")
            resp_ovation = session.get(NOAA_OVATION_URL, headers=headers, timeout=10)
            if resp_ovation.status_code != 200:
                raise RuntimeError(f"NOAA OVATION returned HTTP {resp_ovation.status_code}")

            ovation_raw = resp_ovation.json()
            obs_time = ovation_raw.get("Observation Time", "")
            fcst_time = ovation_raw.get("Forecast Time", "")

            grid_dict, max_prob = process_ovation_grid(ovation_raw)

            payload: Dict[str, Any] = {
                "kp": kp_val,
                "kp_status": kp_status,
                "kp_color": kp_color,
                "kp_time": kp_time,
                "g_scale": g_scale,
                "solar_wind_speed": wind_speed,
                "solar_wind_bz": wind_bz,
                "solar_wind_bt": wind_bt,
                "solar_flux": sfi_val,
                "scales": scales,
                "observation_time": obs_time,
                "forecast_time": fcst_time,
                "max_prob": max_prob,
                "grid": grid_dict,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Cache payload
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file = self.cache_dir / "space_weather_latest.json"
                cache_file.write_text(json.dumps(payload), encoding="utf-8")
            except Exception as e:
                logger.warning("Could not cache space weather: %s", e)

            self.ready_signal.emit(payload)

        except Exception as e:
            logger.error("Failed to fetch NOAA space weather: %s", e, exc_info=True)
            # Attempt to serve cached data if available
            cached_file = self.cache_dir / "space_weather_latest.json"
            if cached_file.exists():
                try:
                    payload = json.loads(cached_file.read_text(encoding="utf-8"))
                    payload["is_stale"] = True
                    self.ready_signal.emit(payload)
                    return
                except Exception:
                    pass
            self.error_signal.emit(f"Space Weather Unavailable: {e}")


class SpaceWeatherService(QObject):
    """Coordinates space weather updates and scheduled polling."""

    weather_updated = pyqtSignal(dict)
    weather_loading = pyqtSignal(str)
    weather_error = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None, cache_dir: Optional[Path] = None):
        super().__init__(parent)
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._worker: Optional[SpaceWeatherWorker] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.fetch_weather)
        self.last_payload: Optional[dict] = None

    def fetch_weather(self, force: bool = False):
        """Trigger an asynchronous fetch of NOAA space weather."""
        if self._worker and self._worker.isRunning():
            logger.debug("Space weather fetch already in progress, skipping.")
            return

        self._worker = SpaceWeatherWorker(cache_dir=self.cache_dir)
        self._worker.loading_signal.connect(self.weather_loading.emit)
        self._worker.ready_signal.connect(self._on_worker_ready)
        self._worker.error_signal.connect(self.weather_error.emit)
        self._worker.start()

    def _on_worker_ready(self, payload: dict):
        self.last_payload = payload
        self.weather_updated.emit(payload)

    def start_polling(self, interval_min: int = 15):
        """Start periodic polling (default: 15 minutes)."""
        interval_ms = max(5, interval_min) * 60 * 1000
        self._poll_timer.start(interval_ms)
        self.fetch_weather()

    def stop_polling(self):
        """Stop periodic polling."""
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
