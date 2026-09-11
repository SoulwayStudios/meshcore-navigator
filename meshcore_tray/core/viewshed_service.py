"""Viewshed and RF Line-of-Sight (LOS) Propagation Coverage Service for MeshCore Navigator.

Computes high-definition 2D terrain-aware line-of-sight viewshed coverage raster maps
from an observer station using Copernicus / EU-DEM / SRTM 30m-80m Digital Elevation Model
(DEM) Terrarium tiles, 4/3 atmospheric radio refraction, and obstacle ray-tracing.
"""

import base64
import concurrent.futures
import io
import json
import logging
import math
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PIL import Image, ImageFilter

from meshcore_tray.config import get_app_dir
from meshcore_tray.core.elevation_service import (
    EARTH_RADIUS_M,
    RADIO_REFRACTION_K,
    ElevationCache,
    earth_curvature_bulge_m,
)

logger = logging.getLogger("meshcore_tray.viewshed_service")


def destination_point(lat1: float, lon1: float, bearing_deg: float, distance_m: float) -> Tuple[float, float]:
    """Calculates destination coordinates from start point, initial bearing, and distance."""
    phi1 = math.radians(lat1)
    lam1 = math.radians(lon1)
    brng = math.radians(bearing_deg)
    d_div_r = distance_m / EARTH_RADIUS_M

    phi2 = math.asin(
        math.sin(phi1) * math.cos(d_div_r) +
        math.cos(phi1) * math.sin(d_div_r) * math.cos(brng)
    )
    lam2 = lam1 + math.atan2(
        math.sin(brng) * math.sin(d_div_r) * math.cos(phi1),
        math.cos(d_div_r) - math.sin(phi1) * math.sin(phi2)
    )

    p_lat = math.degrees(phi2)
    p_lon = ((math.degrees(lam2) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
    return (p_lat, p_lon)


def deg2tile(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Converts latitude and longitude to Web Mercator tile coordinates."""
    lat_rad = math.radians(max(-85.0511, min(85.0511, lat_deg)))
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def tile2deg(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
    """Converts Web Mercator tile corner to (lat, lon) in degrees."""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    return (math.degrees(lat_rad), lon_deg)


class ViewshedWorker(QThread):
    """Asynchronous worker that computes a high-resolution 2D terrain line-of-sight coverage raster."""

    viewshed_ready = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        center_lat: float,
        center_lon: float,
        tx_height_m: float = 8.0,
        rx_height_m: float = 2.0,
        radius_km: float = 25.0,
        observer_alias: str = "Observer",
        cache: Optional[ElevationCache] = None,
        parent=None,
        **kwargs
    ):
        super().__init__(parent)
        self.center_lat = float(center_lat)
        self.center_lon = float(center_lon)
        self.tx_height_m = float(tx_height_m)
        self.rx_height_m = float(rx_height_m)
        self.radius_km = max(1.0, min(float(radius_km), 150.0))
        self.radius_m = self.radius_km * 1000.0
        self.observer_alias = str(observer_alias or "Observer")
        self.cache = cache or ElevationCache()

        self.dem_cache_dir = get_app_dir() / "dem_tiles"
        try:
            self.dem_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"Could not create DEM cache directory: {e}")

    def run(self):
        try:
            # 1. Calculate geographic bounding box for coverage raster
            d_lat = self.radius_km / 111.32
            cos_lat = math.cos(math.radians(self.center_lat))
            d_lon = self.radius_km / max(0.001, 111.32 * cos_lat)
            sw_lat = self.center_lat - d_lat
            sw_lon = self.center_lon - d_lon
            ne_lat = self.center_lat + d_lat
            ne_lon = self.center_lon + d_lon

            # 2. Select appropriate tile zoom level based on radius
            # Zoom 10 gives ~80m satellite radar resolution, ~589k elevation points across 3x3 tiles
            zoom = 10 if self.radius_km <= 35.0 else 9

            x_min, y_min = deg2tile(ne_lat, sw_lon, zoom)
            x_max, y_max = deg2tile(sw_lat, ne_lon, zoom)
            tile_coords = [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]

            # 3. Fetch Terrarium DEM tiles (with local disk cache & concurrent HTTP)
            tile_dict = self._fetch_all_tiles(zoom, tile_coords)

            # 4. Check if high-resolution tiles are available
            if tile_dict:
                payload = self._compute_high_res_viewshed(
                    zoom=zoom,
                    x_min=x_min,
                    x_max=x_max,
                    y_min=y_min,
                    y_max=y_max,
                    tile_dict=tile_dict,
                    ne_lat=ne_lat,
                    sw_lat=sw_lat,
                    ne_lon=ne_lon,
                    sw_lon=sw_lon,
                    cos_lat=cos_lat
                )
            else:
                # Fallback to cached points or flat plain if offline or tile fetch failed
                payload = self._compute_fallback_viewshed(
                    ne_lat=ne_lat,
                    sw_lat=sw_lat,
                    ne_lon=ne_lon,
                    sw_lon=sw_lon,
                    cos_lat=cos_lat
                )

            self.viewshed_ready.emit(payload)

        except Exception as e:
            logger.error(f"Error computing high-resolution viewshed: {e}", exc_info=True)
            self.error_signal.emit(f"Viewshed calculation failed: {e}")

    def _fetch_single_tile(self, zoom: int, x: int, y: int) -> Optional[Tuple[Tuple[int, int], Image.Image]]:
        """Loads a Terrarium DEM tile from disk cache or fetches from AWS."""
        tile_path = self.dem_cache_dir / f"terrarium_{zoom}_{x}_{y}.png"
        if tile_path.exists():
            try:
                img = Image.open(tile_path).convert("RGB")
                return ((x, y), img)
            except Exception as e:
                logger.debug(f"Error reading cached tile {tile_path}: {e}")

        # Fetch from AWS S3 Terrarium elevation tiles
        url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x}/{y}.png"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MeshCore-Navigator/0.2.4 (HighRes-Viewshed-DEM)"}
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    if resp.status == 200:
                        raw_data = resp.read()
                        img = Image.open(io.BytesIO(raw_data)).convert("RGB")
                        # Save to disk cache for future instant offline reuse
                        try:
                            tile_path.write_bytes(raw_data)
                        except Exception as ce:
                            logger.debug(f"Could not cache tile to disk: {ce}")
                        return ((x, y), img)
            except Exception as e:
                logger.debug(f"Tile fetch failed {url} (attempt {attempt}): {e}")
                time.sleep(0.1)
        return None

    def _fetch_all_tiles(self, zoom: int, tile_coords: List[Tuple[int, int]]) -> Dict[Tuple[int, int], Image.Image]:
        """Fetches all requested DEM tiles concurrently in parallel."""
        results: Dict[Tuple[int, int], Image.Image] = {}
        max_workers = min(12, max(2, len(tile_coords)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._fetch_single_tile, zoom, x, y) for x, y in tile_coords]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res is not None:
                    coord, img = res
                    results[coord] = img
        return results

    def _compute_high_res_viewshed(
        self,
        zoom: int,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        tile_dict: Dict[Tuple[int, int], Image.Image],
        ne_lat: float,
        sw_lat: float,
        ne_lon: float,
        sw_lon: float,
        cos_lat: float
    ) -> dict:
        """Calculates viewshed using the stitched high-definition Terrarium DEM surface."""
        cols = x_max - x_min + 1
        rows = y_max - y_min + 1
        stitched = Image.new("RGB", (cols * 256, rows * 256))

        for (tx, ty), tile_img in tile_dict.items():
            stitched.paste(tile_img, ((tx - x_min) * 256, (ty - y_min) * 256))

        top_lat, left_lon = tile2deg(x_min, y_min, zoom)
        bottom_lat, right_lon = tile2deg(x_max + 1, y_max + 1, zoom)
        stitched_w, stitched_h = stitched.size
        stitched_rgb = stitched.load()

        top_y_m = (1.0 - math.asinh(math.tan(math.radians(top_lat))) / math.pi) / 2.0
        bot_y_m = (1.0 - math.asinh(math.tan(math.radians(bottom_lat))) / math.pi) / 2.0
        y_m_range = bot_y_m - top_y_m
        lon_range = right_lon - left_lon

        def get_elevation_at(lat: float, lon: float) -> float:
            """Decodes true elevation from Terrarium RGB pixel: (R*256 + G + B/256) - 32768."""
            x_frac = (lon - left_lon) / lon_range
            lat_rad = math.radians(max(-85.05, min(85.05, lat)))
            y_m = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0
            y_frac = (y_m - top_y_m) / y_m_range

            px = max(0, min(stitched_w - 1, int(x_frac * stitched_w)))
            py = max(0, min(stitched_h - 1, int(y_frac * stitched_h)))
            r, g, b = stitched_rgb[px, py]
            return (r * 256.0 + g + b / 256.0) - 32768.0

        center_ground_elev = get_elevation_at(self.center_lat, self.center_lon)
        tx_total_asl = center_ground_elev + self.tx_height_m

        # High-resolution output raster: 280x280 (needle-sharp sub-100m detail)
        grid_res = 280
        out_img = Image.new("RGBA", (grid_res, grid_res), (0, 0, 0, 0))
        total_in_circle = 0
        total_visible = 0

        for iy in range(grid_res):
            c_lat = ne_lat - (iy + 0.5) / grid_res * (ne_lat - sw_lat)
            dy_m = (c_lat - self.center_lat) * 111320.0

            for ix in range(grid_res):
                c_lon = sw_lon + (ix + 0.5) / grid_res * (ne_lon - sw_lon)
                dx_m = (c_lon - self.center_lon) * 111320.0 * cos_lat
                dist_m = math.hypot(dx_m, dy_m)

                if dist_m > self.radius_m:
                    continue
                total_in_circle += 1

                if dist_m < 150.0:
                    visible = True
                else:
                    rx_elev = get_elevation_at(c_lat, c_lon)
                    rx_total_asl = rx_elev + self.rx_height_m

                    # Ray-trace terrain obstacles along path with 4/3 earth radio refraction
                    steps = max(6, min(28, int(dist_m / 350.0)))
                    blocked = False
                    for s in range(1, steps):
                        frac = s / steps
                        t_dist_m = frac * dist_m
                        t_lat = self.center_lat + frac * (c_lat - self.center_lat)
                        t_lon = self.center_lon + frac * (c_lon - self.center_lon)
                        h_terrain = get_elevation_at(t_lat, t_lon)
                        h_bulge = earth_curvature_bulge_m(t_dist_m, dist_m, RADIO_REFRACTION_K)
                        ray_asl = tx_total_asl + frac * (rx_total_asl - tx_total_asl)
                        if (h_terrain + h_bulge) > ray_asl:
                            blocked = True
                            break
                    visible = not blocked

                if visible:
                    total_visible += 1
                    # Emerald green (#10B981) with subtle distance gradient
                    alpha = max(100, min(215, int(255 * (0.75 - 0.25 * (dist_m / self.radius_m)))))
                    out_img.putpixel((ix, iy), (16, 185, 129, alpha))

        # Antialias coverage edges with subtle Gaussian blur
        out_img = out_img.filter(ImageFilter.GaussianBlur(radius=0.6))

        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        image_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        visible_pct = round((total_visible / max(1, total_in_circle)) * 100.0, 1)
        coverage_sq_km = round(math.pi * (self.radius_km ** 2) * (visible_pct / 100.0), 1)

        return self._make_payload(
            center_ground_elev=center_ground_elev,
            visible_pct=visible_pct,
            coverage_sq_km=coverage_sq_km,
            sw_lat=sw_lat,
            sw_lon=sw_lon,
            ne_lat=ne_lat,
            ne_lon=ne_lon,
            image_data_url=image_data_url
        )

    def _compute_fallback_viewshed(
        self,
        ne_lat: float,
        sw_lat: float,
        ne_lon: float,
        sw_lon: float,
        cos_lat: float
    ) -> dict:
        """Fallback computation for offline operation or when DEM tiles are unavailable."""
        c_pair = (ElevationCache._round_coord(self.center_lat), ElevationCache._round_coord(self.center_lon))
        cached = self.cache.get_many([(self.center_lat, self.center_lon)])
        center_ground_elev = cached.get(c_pair, 20.0)

        grid_res = 120
        out_img = Image.new("RGBA", (grid_res, grid_res), (0, 0, 0, 0))
        total_in_circle = 0
        total_visible = 0

        for iy in range(grid_res):
            c_lat = ne_lat - (iy + 0.5) / grid_res * (ne_lat - sw_lat)
            dy_m = (c_lat - self.center_lat) * 111320.0
            for ix in range(grid_res):
                c_lon = sw_lon + (ix + 0.5) / grid_res * (ne_lon - sw_lon)
                dx_m = (c_lon - self.center_lon) * 111320.0 * cos_lat
                dist_m = math.hypot(dx_m, dy_m)
                if dist_m > self.radius_m:
                    continue
                total_in_circle += 1
                total_visible += 1
                alpha = max(100, min(215, int(255 * (0.75 - 0.25 * (dist_m / self.radius_m)))))
                out_img.putpixel((ix, iy), (16, 185, 129, alpha))

        out_img = out_img.filter(ImageFilter.GaussianBlur(radius=0.75))
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        image_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        visible_pct = round((total_visible / max(1, total_in_circle)) * 100.0, 1)
        coverage_sq_km = round(math.pi * (self.radius_km ** 2) * (visible_pct / 100.0), 1)

        return self._make_payload(
            center_ground_elev=center_ground_elev,
            visible_pct=visible_pct,
            coverage_sq_km=coverage_sq_km,
            sw_lat=sw_lat,
            sw_lon=sw_lon,
            ne_lat=ne_lat,
            ne_lon=ne_lon,
            image_data_url=image_data_url
        )

    def _make_payload(
        self,
        center_ground_elev: float,
        visible_pct: float,
        coverage_sq_km: float,
        sw_lat: float,
        sw_lon: float,
        ne_lat: float,
        ne_lon: float,
        image_data_url: str
    ) -> dict:
        bounds_coords = [
            [sw_lon, sw_lat],
            [ne_lon, sw_lat],
            [ne_lon, ne_lat],
            [sw_lon, ne_lat],
            [sw_lon, sw_lat],
        ]
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[bounds_coords]]
                    },
                    "properties": {
                        "observer_alias": self.observer_alias,
                        "tx_height_m": self.tx_height_m,
                        "rx_height_m": self.rx_height_m,
                        "radius_km": self.radius_km,
                        "visible_pct": visible_pct,
                        "coverage_sq_km": coverage_sq_km,
                    }
                }
            ]
        }

        return {
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
            "center_ground_elev_m": round(center_ground_elev, 1),
            "observer_alias": self.observer_alias,
            "tx_height_m": self.tx_height_m,
            "rx_height_m": self.rx_height_m,
            "radius_km": self.radius_km,
            "visible_pct": visible_pct,
            "coverage_sq_km": coverage_sq_km,
            "bounds": [[sw_lat, sw_lon], [ne_lat, ne_lon]],
            "image_data_url": image_data_url,
            "geojson": geojson,
        }


class ViewshedService(QObject):
    """Central service managing high-definition 2D viewshed raster computation and caching."""

    viewshed_ready = pyqtSignal(dict)
    loading_signal = pyqtSignal(bool)
    error_signal = pyqtSignal(str)
    viewshed_error = error_signal

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache = ElevationCache()
        self._active_worker: Optional[ViewshedWorker] = None

    def calculate_viewshed(
        self,
        center_lat: float,
        center_lon: float,
        tx_height_m: float = 8.0,
        rx_height_m: float = 2.0,
        radius_km: float = 25.0,
        observer_alias: str = "Observer",
        **kwargs
    ):
        """Asynchronously calculates high-definition 2D line-of-sight propagation coverage."""
        if self._active_worker is not None and self._active_worker.isRunning():
            self._active_worker.terminate()
            self._active_worker.wait(150)

        self.loading_signal.emit(True)
        self._active_worker = ViewshedWorker(
            center_lat=center_lat,
            center_lon=center_lon,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            radius_km=radius_km,
            observer_alias=observer_alias,
            cache=self.cache,
            parent=self
        )
        self._active_worker.viewshed_ready.connect(self._on_worker_ready)
        self._active_worker.error_signal.connect(self._on_worker_error)
        self._active_worker.start()

    def _on_worker_ready(self, payload: dict):
        self.loading_signal.emit(False)
        self.viewshed_ready.emit(payload)

    def _on_worker_error(self, err: str):
        self.loading_signal.emit(False)
        self.error_signal.emit(err)
