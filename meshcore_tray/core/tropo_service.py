"""Tropospheric Ducting Forecast Service for MeshCore Tray.

Fetches and caches NOAA GFS-derived refractive index GeoTIFF forecasts
from F5LEN Worldwide Tropospheric Propagation tool (tropo.f5len.org).
Provides asynchronous background downloading and Base64 transfer to the
Leaflet map widget.
"""

from datetime import datetime, timedelta, timezone
import base64
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal
import requests

logger = logging.getLogger("meshcore_tray.tropo_service")

BASE_TROPO_URL = "https://tropo.f5len.org/WW/images/"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "meshcore-pixoo-tray" / "tropo"


def compute_timestep(dt: Optional[datetime] = None, offset_hours: int = 0) -> Tuple[datetime, str, str]:
    """Compute rounded 3-hour forecast timestep and file name.

    Returns:
        (target_dt, filename, display_label)
        e.g., (datetime(2026, 9, 4, 12, 0), "ww04-12.tif", "04 Sep 12:00 UTC")
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Apply hour offset
    target_dt = dt + timedelta(hours=offset_hours)

    # Round to nearest preceding 3-hour interval (00, 03, 06, 09, 12, 15, 18, 21)
    rounded_hour = (target_dt.hour // 3) * 3
    target_dt = target_dt.replace(hour=rounded_hour, minute=0, second=0, microsecond=0)

    filename = f"ww{target_dt.strftime('%d')}-{target_dt.strftime('%H')}.tif"
    display_label = target_dt.strftime("%d %b %H:00 UTC")
    return target_dt, filename, display_label


from PIL import Image


def extract_tropo_europe_grid(cache_file: Path) -> dict:
    """Extract and crop refractive index grid for Europe/UK region.

    Region: Lat 35°N to 65°N, Lon -20°W to 25°E
    Resolution: 0.25° grid
    """
    try:
        im = Image.open(cache_file)
        lat_min, lat_max = 35.0, 65.0
        lon_min, lon_max = -20.0, 25.0

        x0 = int((lon_min - (-180.125)) / 0.25)
        x1 = int((lon_max - (-180.125)) / 0.25)
        y0 = int((70.125 - lat_max) / 0.25)
        y1 = int((70.125 - lat_min) / 0.25)

        crop = im.crop((x0, y0, x1, y1))
        w, h = crop.size
        raw_floats = crop.tobytes()
        b64_floats = base64.b64encode(raw_floats).decode("ascii")

        return {
            "w": w,
            "h": h,
            "lon_min": lon_min,
            "lat_max": lat_max,
            "res": 0.25,
            "b64_floats": b64_floats,
        }
    except Exception as e:
        logger.warning("Could not extract grid via Pillow: %s", e)
        with open(cache_file, "rb") as f:
            raw = f.read()
        return {
            "w": 0,
            "h": 0,
            "lon_min": -180.0,
            "lat_max": 70.0,
            "res": 0.25,
            "b64_floats": base64.b64encode(raw).decode("ascii"),
        }


class TropoDownloadWorker(QThread):
    """Background worker to download and cache a GeoTIFF forecast file."""

    ready_signal = pyqtSignal(dict, str, str)  # grid_dict, filename, display_label
    loading_signal = pyqtSignal(str)           # status message
    error_signal = pyqtSignal(str)             # error description

    def __init__(
        self,
        filename: str,
        display_label: str,
        cache_dir: Optional[Path] = None,
        base_url: str = BASE_TROPO_URL,
    ):
        super().__init__()
        self.filename = filename
        self.display_label = display_label
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.base_url = base_url

    def run(self):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / self.filename

            # If cached and valid, read directly from cache
            if cache_file.exists() and cache_file.stat().st_size > 10000:
                logger.debug("Loading tropo forecast from cache: %s", cache_file)
                self.loading_signal.emit(f"Loading {self.display_label} from cache...")
            else:
                url = f"{self.base_url.rstrip('/')}/{self.filename}"
                logger.info("Downloading tropo forecast from %s", url)
                self.loading_signal.emit(f"Fetching {self.display_label} from F5LEN...")

                headers = {
                    "User-Agent": "MeshCore-Tray/1.0 (Amateur Radio Non-commercial)"
                }
                resp = requests.get(url, headers=headers, timeout=25)
                if resp.status_code != 200:
                    err = f"Failed to download forecast: HTTP {resp.status_code}"
                    logger.warning(err)
                    self.error_signal.emit(err)
                    return

                raw_data = resp.content
                if len(raw_data) < 10000:
                    err = f"Downloaded forecast file is too small ({len(raw_data)} bytes)"
                    logger.warning(err)
                    self.error_signal.emit(err)
                    return

                # Write to temp file then atomic rename
                tmp_file = cache_file.with_suffix(".tmp")
                with open(tmp_file, "wb") as f:
                    f.write(raw_data)
                tmp_file.replace(cache_file)
                logger.debug("Saved tropo forecast to cache: %s (%d bytes)", cache_file, len(raw_data))

            # Extract cropped Europe/UK grid
            grid_dict = extract_tropo_europe_grid(cache_file)
            self.ready_signal.emit(grid_dict, self.filename, self.display_label)

        except requests.exceptions.RequestException as e:
            err = f"Network error downloading tropo forecast: {e}"
            logger.warning(err)
            self.error_signal.emit(err)
        except Exception as e:
            err = f"Error processing tropo forecast: {e}"
            logger.exception(err)
            self.error_signal.emit(err)


class TropoForecastService(QObject):
    """Manages downloading, caching, and stepping through tropospheric ducting forecasts."""

    forecast_ready = pyqtSignal(dict, str, str)  # grid_dict, filename, display_label
    forecast_loading = pyqtSignal(str)           # status message
    forecast_error = pyqtSignal(str)             # error message

    def __init__(self, cache_dir: Optional[Path] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._current_offset_hours = 0
        self._current_dt: Optional[datetime] = None
        self._worker: Optional[TropoDownloadWorker] = None

    @property
    def current_offset_hours(self) -> int:
        return self._current_offset_hours

    def fetch_forecast(self, offset_hours: int = 0):
        """Fetch forecast for current UTC time shifted by offset_hours."""
        self._current_offset_hours = offset_hours
        target_dt, filename, display_label = compute_timestep(offset_hours=offset_hours)
        self._current_dt = target_dt

        # Terminate any existing worker
        if self._worker is not None and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(500)

        self._worker = TropoDownloadWorker(
            filename=filename,
            display_label=display_label,
            cache_dir=self.cache_dir,
        )
        self._worker.ready_signal.connect(self._on_worker_ready)
        self._worker.loading_signal.connect(self.forecast_loading.emit)
        self._worker.error_signal.connect(self.forecast_error.emit)
        self._worker.start()

    def step_forecast(self, delta_hours: int):
        """Step forward or backward by delta_hours (e.g. +3 or -3)."""
        new_offset = self._current_offset_hours + delta_hours
        # Limit forecast range: between -24h (past 1 day) and +120h (future 5 days)
        new_offset = max(-24, min(120, new_offset))
        self.fetch_forecast(offset_hours=new_offset)

    def _on_worker_ready(self, grid_dict: dict, filename: str, display_label: str):
        self.forecast_ready.emit(grid_dict, filename, display_label)
