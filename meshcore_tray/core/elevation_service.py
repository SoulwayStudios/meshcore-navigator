"""Elevation and Point-to-Point RF Path Profile Service for MeshCore Navigator.

Fetches terrain elevations from Copernicus 30m Digital Elevation Model (DEM)
via Open-Meteo Elevation API with persistent local SQLite caching. Computes
Great Circle interpolation, 4/3 Earth curvature, direct line of sight (LOS),
and 1st Fresnel zone clearance at 868 MHz / 915 MHz.
"""

import concurrent.futures
import io
import json
import logging
import math
from pathlib import Path
import sqlite3
import time
from typing import Dict, List, Optional, Tuple
import urllib.request
import urllib.error
from PIL import Image

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from meshcore_tray.config import get_app_dir

logger = logging.getLogger("meshcore_tray.elevation_service")

EARTH_RADIUS_M = 6371000.0
RADIO_REFRACTION_K = 1.3333333333333333  # Standard 4/3 atmospheric radio refraction
DEFAULT_LORA_FREQ_MHZ = 868.0  # UK / EU LoRa frequency


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


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates Great Circle distance in meters between two lat/lon coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_M * c


def forward_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates forward initial bearing in degrees (0 - 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    b = math.degrees(math.atan2(y, x))
    return (b + 360.0) % 360.0


def interpolate_points(lat1: float, lon1: float, lat2: float, lon2: float, num_points: int = 100) -> List[Tuple[float, float, float]]:
    """Generates evenly spaced intermediate coordinates along the Great Circle path.
    
    Returns a list of tuples: (lat, lon, distance_from_start_m).
    """
    total_dist = haversine_distance_m(lat1, lon1, lat2, lon2)
    if total_dist < 1.0 or num_points < 2:
        return [(lat1, lon1, 0.0), (lat2, lon2, total_dist)]

    phi1, lam1 = math.radians(lat1), math.radians(lon1)
    phi2, lam2 = math.radians(lat2), math.radians(lon2)
    d = total_dist / EARTH_RADIUS_M

    points: List[Tuple[float, float, float]] = []
    for i in range(num_points):
        f = i / float(num_points - 1)
        # Slerp along unit sphere
        a = math.sin((1.0 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
        y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
        z = a * math.sin(phi1) + b * math.sin(phi2)
        p_lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
        p_lon = math.degrees(math.atan2(y, x))
        dist_m = f * total_dist
        points.append((p_lat, p_lon, dist_m))

    return points


def earth_curvature_bulge_m(d_m: float, total_d_m: float, k: float = RADIO_REFRACTION_K) -> float:
    """Computes effective Earth curvature bulge height in meters with 4/3 radio refraction.
    
    Bulge h = (d * (D - d)) / (2 * k * R_earth)
    """
    if total_d_m <= 0.0 or d_m <= 0.0 or d_m >= total_d_m:
        return 0.0
    effective_r = k * EARTH_RADIUS_M
    return (d_m * (total_d_m - d_m)) / (2.0 * effective_r)


def fresnel_zone_radius_m(d1_m: float, d2_m: float, freq_mhz: float = DEFAULT_LORA_FREQ_MHZ) -> float:
    """Calculates 1st Fresnel zone radius in meters at distance d1 from Tx and d2 from Rx.
    
    F1 = sqrt((lambda * d1 * d2) / (d1 + d2))
    """
    total_d = d1_m + d2_m
    if total_d <= 0.0 or d1_m <= 0.0 or d2_m <= 0.0 or freq_mhz <= 0.0:
        return 0.0
    # Speed of light 299,792,458 m/s
    wavelength_m = 299792458.0 / (freq_mhz * 1e6)
    return math.sqrt((wavelength_m * d1_m * d2_m) / total_d)


class ElevationCache:
    """Thread-safe SQLite elevation cache stored in user's config directory."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = get_app_dir() / "elevation_cache.db"
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS elevation_cache (
                    lat_round REAL NOT NULL,
                    lon_round REAL NOT NULL,
                    elevation_m REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (lat_round, lon_round)
                )
            """)
            conn.commit()

    @staticmethod
    def _round_coord(val: float) -> float:
        # Round to 4 decimal places (~11m spatial precision)
        return round(float(val), 4)

    def get_many(self, coords: List[Tuple[float, float]]) -> Dict[Tuple[float, float], float]:
        """Queries cached elevations for a list of (lat, lon) coordinates."""
        results: Dict[Tuple[float, float], float] = {}
        if not coords:
            return results

        rounded_coords = [(self._round_coord(lat), self._round_coord(lon)) for lat, lon in coords]
        unique_rounded = list(set(rounded_coords))

        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                # Query in batches of 500
                for i in range(0, len(unique_rounded), 500):
                    batch = unique_rounded[i:i + 500]
                    placeholders = ",".join(["(?, ?)"] * len(batch))
                    flat_args = [arg for pair in batch for arg in pair]
                    cursor = conn.execute(
                        f"SELECT lat_round, lon_round, elevation_m FROM elevation_cache WHERE (lat_round, lon_round) IN (VALUES {placeholders})",
                        flat_args
                    )
                    for r_lat, r_lon, elev in cursor.fetchall():
                        results[(r_lat, r_lon)] = float(elev)
        except Exception as e:
            logger.debug(f"Elevation cache read error: {e}")

        return results

    def put_many(self, data: Dict[Tuple[float, float], float]):
        """Stores newly fetched elevations into SQLite cache."""
        if not data:
            return
        now = time.time()
        rows = [
            (self._round_coord(lat), self._round_coord(lon), float(elev), now)
            for (lat, lon), elev in data.items()
        ]
        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO elevation_cache (lat_round, lon_round, elevation_m, updated_at)
                    VALUES (?, ?, ?, ?)
                """, rows)
                conn.commit()
        except Exception as e:
            logger.debug(f"Elevation cache write error: {e}")


class ElevationProfileWorker(QThread):
    """Background worker that samples elevations and analyzes RF path clearance."""

    profile_ready = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        alias1: str = "Node A",
        alias2: str = "Node B",
        tx_height_m: float = 2.0,
        rx_height_m: float = 2.0,
        freq_mhz: float = DEFAULT_LORA_FREQ_MHZ,
        num_points: int = 120,
        cache: Optional[ElevationCache] = None,
        parent=None
    ):
        super().__init__(parent)
        self.lat1 = float(lat1)
        self.lon1 = float(lon1)
        self.lat2 = float(lat2)
        self.lon2 = float(lon2)
        self.alias1 = alias1
        self.alias2 = alias2
        self.tx_height_m = float(tx_height_m)
        self.rx_height_m = float(rx_height_m)
        self.freq_mhz = float(freq_mhz)
        self.num_points = max(20, min(num_points, 400))
        self.cache = cache or ElevationCache()
        self.dem_cache_dir = get_app_dir() / "dem_tiles"
        try:
            self.dem_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"Could not create DEM cache directory: {e}")

    def run(self):
        try:
            total_dist_m = haversine_distance_m(self.lat1, self.lon1, self.lat2, self.lon2)
            bearing_deg = forward_bearing_deg(self.lat1, self.lon1, self.lat2, self.lon2)

            if total_dist_m < 10.0:
                self.error_signal.emit("Points are too close to compute a valid RF elevation profile.")
                return

            points = interpolate_points(self.lat1, self.lon1, self.lat2, self.lon2, self.num_points)

            # 1. Check local SQLite cache
            cached_elevs = self.cache.get_many([(p[0], p[1]) for p in points])

            # 2. Identify missing points to query
            missing_coords = [
                (ElevationCache._round_coord(lat), ElevationCache._round_coord(lon))
                for lat, lon, _ in points
                if (ElevationCache._round_coord(lat), ElevationCache._round_coord(lon)) not in cached_elevs
            ]

            # 3. Load high-resolution Terrarium satellite radar DEM tiles
            if missing_coords:
                min_lat = min(self.lat1, self.lat2)
                max_lat = max(self.lat1, self.lat2)
                min_lon = min(self.lon1, self.lon2)
                max_lon = max(self.lon1, self.lon2)
                zoom = 10 if total_dist_m <= 40000.0 else 9

                x_min, y_min = deg2tile(max_lat, min_lon, zoom)
                x_max, y_max = deg2tile(min_lat, max_lon, zoom)
                tile_coords = [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]

                tile_dict = self._fetch_all_tiles(zoom, tile_coords)
                if tile_dict:
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

                    new_elevs = {}
                    for lat, lon, _ in points:
                        r_pair = (ElevationCache._round_coord(lat), ElevationCache._round_coord(lon))
                        if r_pair not in cached_elevs:
                            x_frac = (lon - left_lon) / lon_range
                            lat_rad = math.radians(max(-85.05, min(85.05, lat)))
                            y_m = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0
                            y_frac = (y_m - top_y_m) / y_m_range
                            px = max(0, min(stitched_w - 1, int(x_frac * stitched_w)))
                            py = max(0, min(stitched_h - 1, int(y_frac * stitched_h)))
                            r, g, b = stitched_rgb[px, py]
                            elev_val = (r * 256.0 + g + b / 256.0) - 32768.0
                            new_elevs[r_pair] = elev_val

                    if new_elevs:
                        self.cache.put_many(new_elevs)
                        cached_elevs.update(new_elevs)
                else:
                    # Fallback to Open-Meteo if tiles failed
                    new_elevs = self._fetch_open_meteo(missing_coords)
                    if new_elevs:
                        self.cache.put_many(new_elevs)
                        cached_elevs.update(new_elevs)

            # 4. Construct elevation profile array with intelligent interpolation for any missing values
            known_elev_vals = [
                cached_elevs.get((ElevationCache._round_coord(p[0]), ElevationCache._round_coord(p[1])))
                for p in points
            ]
            first_known = next((e for e in known_elev_vals if e is not None), 20.0)
            last_known = next((e for e in reversed(known_elev_vals) if e is not None), 20.0)

            elev_profile = []
            for i, (lat, lon, dist_m) in enumerate(points):
                r_pair = (ElevationCache._round_coord(lat), ElevationCache._round_coord(lon))
                elev = cached_elevs.get(r_pair)
                if elev is None:
                    frac = i / float(len(points) - 1) if len(points) > 1 else 0.0
                    elev = first_known + frac * (last_known - first_known)
                elev_profile.append((lat, lon, dist_m, elev))

            # 5. Compute RF Line of Sight and Fresnel Clearance
            elev1 = elev_profile[0][3]
            elev2 = elev_profile[-1][3]
            tx_total_asl = elev1 + self.tx_height_m
            rx_total_asl = elev2 + self.rx_height_m

            profile_points = []
            min_optical_clearance = float("inf")
            min_fresnel_clearance = float("inf")
            worst_obstacle = None
            total_d_m = total_dist_m

            for i, (lat, lon, dist_m, ground_elev) in enumerate(elev_profile):
                # Fraction along path
                frac = dist_m / total_d_m if total_d_m > 0 else 0.0
                # Direct beam height ASL (unbent straight ray in flat space)
                los_asl = tx_total_asl + frac * (rx_total_asl - tx_total_asl)
                # Earth curvature bulge at this distance
                bulge_m = earth_curvature_bulge_m(dist_m, total_d_m, RADIO_REFRACTION_K)
                # Effective terrain height including curvature
                effective_terrain_asl = ground_elev + bulge_m
                # Optical clearance above effective terrain
                opt_clearance = los_asl - effective_terrain_asl

                # 1st Fresnel zone radius
                d1 = dist_m
                d2 = max(0.0, total_d_m - dist_m)
                f1_radius = fresnel_zone_radius_m(d1, d2, self.freq_mhz)
                fresnel_60_radius = 0.6 * f1_radius
                # Clearance from bottom of 60% Fresnel zone to terrain
                fresnel_clearance = opt_clearance - fresnel_60_radius

                if opt_clearance < min_optical_clearance:
                    min_optical_clearance = opt_clearance
                if fresnel_clearance < min_fresnel_clearance:
                    min_fresnel_clearance = fresnel_clearance
                    if opt_clearance < 0:
                        worst_obstacle = {
                            "distance_m": dist_m,
                            "distance_km": round(dist_m / 1000.0, 2),
                            "lat": lat,
                            "lon": lon,
                            "terrain_elev_m": round(ground_elev, 1),
                            "bulge_m": round(bulge_m, 1),
                            "los_asl": round(los_asl, 1),
                            "obstruction_m": round(abs(opt_clearance), 1),
                        }

                profile_points.append({
                    "index": i,
                    "lat": lat,
                    "lon": lon,
                    "distance_m": round(dist_m, 1),
                    "distance_km": round(dist_m / 1000.0, 2),
                    "ground_elev_m": round(ground_elev, 1),
                    "bulge_m": round(bulge_m, 1),
                    "effective_terrain_asl": round(effective_terrain_asl, 1),
                    "los_asl": round(los_asl, 1),
                    "f1_radius_m": round(f1_radius, 1),
                    "fresnel_top_asl": round(los_asl + f1_radius, 1),
                    "fresnel_bot_asl": round(los_asl - f1_radius, 1),
                    "fresnel_60_bot_asl": round(los_asl - fresnel_60_radius, 1),
                    "opt_clearance_m": round(opt_clearance, 1),
                    "fresnel_clearance_m": round(fresnel_clearance, 1),
                })

            # Determine overall LOS status
            if min_optical_clearance < 0.0:
                status = "OBSTRUCTED"
                status_label = "Line of Sight Blocked"
                status_color = "#EF4444"
            elif min_fresnel_clearance < 0.0:
                status = "FRESNEL_INCURSION"
                status_label = "Visual LOS Clear (Fresnel Incursion)"
                status_color = "#F59E0B"
            else:
                status = "CLEAR"
                status_label = "100% Line of Sight Clear"
                status_color = "#10B981"

            result = {
                "alias1": self.alias1,
                "alias2": self.alias2,
                "lat1": self.lat1,
                "lon1": self.lon1,
                "lat2": self.lat2,
                "lon2": self.lon2,
                "tx_height_m": self.tx_height_m,
                "rx_height_m": self.rx_height_m,
                "freq_mhz": self.freq_mhz,
                "total_distance_m": round(total_d_m, 1),
                "total_distance_km": round(total_d_m / 1000.0, 2),
                "total_distance_miles": round(total_d_m / 1609.344, 2),
                "bearing_deg": round(bearing_deg, 1),
                "elev1_m": round(elev1, 1),
                "elev2_m": round(elev2, 1),
                "tx_total_asl": round(tx_total_asl, 1),
                "rx_total_asl": round(rx_total_asl, 1),
                "status": status,
                "status_label": status_label,
                "status_color": status_color,
                "min_optical_clearance_m": round(min_optical_clearance, 1),
                "min_fresnel_clearance_m": round(min_fresnel_clearance, 1),
                "worst_obstacle": worst_obstacle,
                "points": profile_points,
            }

            self.profile_ready.emit(result)

        except Exception as e:
            logger.error(f"Error computing RF elevation profile: {e}", exc_info=True)
            self.error_signal.emit(f"Failed generating elevation profile: {e}")

    def _fetch_single_tile(self, zoom: int, x: int, y: int) -> Optional[Tuple[Tuple[int, int], Image.Image]]:
        """Loads a Terrarium DEM tile from disk cache or fetches from AWS."""
        tile_path = self.dem_cache_dir / f"terrarium_{zoom}_{x}_{y}.png"
        if tile_path.exists():
            try:
                img = Image.open(tile_path).convert("RGB")
                return ((x, y), img)
            except Exception as e:
                logger.debug(f"Error reading cached tile {tile_path}: {e}")

        url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x}/{y}.png"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MeshCore-Navigator/0.2.5 (RF-Elevation-Profile)"}
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    if resp.status == 200:
                        raw_data = resp.read()
                        img = Image.open(io.BytesIO(raw_data)).convert("RGB")
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
        max_workers = min(8, max(1, len(tile_coords)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._fetch_single_tile, zoom, x, y) for x, y in tile_coords]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res is not None:
                    coord, img = res
                    results[coord] = img
        return results

    def _fetch_open_meteo(self, coords: List[Tuple[float, float]]) -> Dict[Tuple[float, float], float]:
        """Queries Open-Meteo Elevation API for a batch of coordinates."""
        results: Dict[Tuple[float, float], float] = {}
        # Maximum coordinates per request in Open-Meteo is 100; batch in 80 for safety
        for i in range(0, len(coords), 80):
            batch = coords[i:i + 80]
            lat_str = ",".join(f"{c[0]:.4f}" for c in batch)
            lon_str = ",".join(f"{c[1]:.4f}" for c in batch)
            url = f"https://api.open-meteo.com/v1/elevation?latitude={lat_str}&longitude={lon_str}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MeshCore-Navigator/0.2.2 (RF-Elevation-Profile)"}
            )
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=8.0) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            elevs = data.get("elevation", [])
                            for coord, elev in zip(batch, elevs):
                                if elev is not None:
                                    results[coord] = float(elev)
                            break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    logger.warning(f"Failed querying Open-Meteo elevation batch (attempt {attempt}): {e}")
                    break
                except Exception as e:
                    logger.warning(f"Failed querying Open-Meteo elevation batch: {e}")
                    break
            time.sleep(0.12)
        return results


class ElevationService(QObject):
    """Central service for elevation lookups and RF profile workers."""

    profile_ready = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    profile_error = error_signal

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache = ElevationCache()
        self._active_worker: Optional[ElevationProfileWorker] = None

    def calculate_path_profile(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        alias1: str = "Node A",
        alias2: str = "Node B",
        tx_height_m: float = 2.0,
        rx_height_m: float = 2.0,
        freq_mhz: float = DEFAULT_LORA_FREQ_MHZ,
        num_points: int = 120
    ):
        """Asynchronously calculates topographic profile and RF Fresnel clearance."""
        if self._active_worker is not None and self._active_worker.isRunning():
            self._active_worker.terminate()
            self._active_worker.wait(200)

        self._active_worker = ElevationProfileWorker(
            lat1=lat1,
            lon1=lon1,
            lat2=lat2,
            lon2=lon2,
            alias1=alias1,
            alias2=alias2,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            freq_mhz=freq_mhz,
            num_points=num_points,
            cache=self.cache,
            parent=self
        )
        self._active_worker.profile_ready.connect(self.profile_ready)
        self._active_worker.error_signal.connect(self.error_signal)
        self._active_worker.start()

    calculate_profile = calculate_path_profile
