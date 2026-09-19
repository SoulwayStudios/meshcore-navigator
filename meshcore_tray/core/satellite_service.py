"""Offline-First Real-Time Satellite Tracking Service for MeshCore Tray.

Fetches, parses, caches, and prepares orbital elements (TLEs) for amateur radio,
weather, space stations, and cubesats from CelesTrak. Supports offline fallback,
frequency telemetry enrichment, and RF Doppler calculation.
"""

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from meshcore_tray.config import AppConfig, SatelliteConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.storage import Storage

logger = logging.getLogger("meshcore_tray.satellite_service")

# CelesTrak Curated Endpoints (Bulk, unauthenticated, updated multiple times daily)
CELESTRAK_ENDPOINTS: Dict[str, str] = {
    "stations": "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
    "amateur": "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle",
    "weather": "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
    "cubesat": "https://celestrak.org/NORAD/elements/gp.php?GROUP=cubesat&FORMAT=tle",
}

USER_AGENT = "MeshCoreNavigator/0.7.0 (Satellite Tracking Layer; AmateurRadioAware)"
SPEED_OF_LIGHT_KM_S = 299792.458
EARTH_RADIUS_KM = 6371.0

# Curated amateur radio and weather frequency metadata catalog (SatNOGS / AMSAT aligned)
KNOWN_SATELLITE_FREQUENCIES: Dict[str, List[Dict[str, Any]]] = {
    # ISS (ZARYA)
    "25544": [
        {"label": "APRS Packet", "freq_mhz": 145.825, "mode": "1200bps AFSK"},
        {"label": "Voice / Crossband", "freq_mhz": 437.800, "mode": "FM"},
    ],
    # TIANGONG (CSS)
    "48274": [
        {"label": "Amateur Downlink", "freq_mhz": 145.800, "mode": "FM"},
    ],
    # NOAA 15
    "25338": [
        {"label": "APT Weather", "freq_mhz": 137.620, "mode": "FM-APT"},
    ],
    # NOAA 18
    "28654": [
        {"label": "APT Weather", "freq_mhz": 137.9125, "mode": "FM-APT"},
    ],
    # NOAA 19
    "33591": [
        {"label": "APT Weather", "freq_mhz": 137.100, "mode": "FM-APT"},
    ],
    # METEOR-M2-3
    "57166": [
        {"label": "LRPT Weather", "freq_mhz": 137.900, "mode": "QPSK"},
    ],
    # METEOR-M2-4
    "59051": [
        {"label": "LRPT Weather", "freq_mhz": 137.100, "mode": "QPSK"},
    ],
    # AO-91 (RadFxSat / Fox-1B)
    "43017": [
        {"label": "U/V FM Downlink", "freq_mhz": 145.960, "mode": "FM"},
        {"label": "Telemetry", "freq_mhz": 145.960, "mode": "DUV"},
    ],
    # AO-92 (Fox-1D)
    "43137": [
        {"label": "U/V FM Downlink", "freq_mhz": 145.880, "mode": "FM"},
    ],
    # SO-50 (SaudiSat 1C)
    "27607": [
        {"label": "V/U FM Downlink", "freq_mhz": 436.795, "mode": "FM (CTCSS 67Hz)"},
    ],
    # PO-101 (DIWATA-2)
    "43678": [
        {"label": "U/V FM Downlink", "freq_mhz": 145.900, "mode": "FM"},
    ],
    # LILACSAT-2
    "40908": [
        {"label": "FM Downlink", "freq_mhz": 437.200, "mode": "FM"},
    ],
    # IO-86 (LAPAN-A2)
    "40931": [
        {"label": "Voice Repeater", "freq_mhz": 435.880, "mode": "FM"},
        {"label": "APRS", "freq_mhz": 145.825, "mode": "Packet"},
    ],
    # FOSSASAT-1B (LoRa)
    "48868": [
        {"label": "LoRa Downlink", "freq_mhz": 436.700, "mode": "LoRa SF11"},
    ],
    # TINYGS-1 (LoRa)
    "56181": [
        {"label": "LoRa Downlink", "freq_mhz": 436.700, "mode": "LoRa SF10"},
    ],
    # RS-44 (DOSAAF)
    "44909": [
        {"label": "Beacon", "freq_mhz": 435.605, "mode": "CW"},
        {"label": "SSB Transponder", "freq_mhz": 435.640, "mode": "USB"},
    ],
}

# Embedded starter catalog for Day 1 offline operation
CURATED_OFFLINE_TLES: List[Dict[str, Any]] = [
    {
        "norad_id": "25544",
        "name": "ISS (ZARYA)",
        "group_name": "stations",
        "line1": "1 25544U 98067A   26077.53488426  .00016717  00000+0  30000-3 0  9993",
        "line2": "2 25544  51.6416 112.9812 0005784  35.2160 324.9123 15.49842514444321",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("25544", []),
    },
    {
        "norad_id": "25338",
        "name": "NOAA 15",
        "group_name": "weather",
        "line1": "1 25338U 98030A   26077.51408102  .00000108  00000+0  78000-4 0  9997",
        "line2": "2 25338  98.7180 145.2201 0011504 110.3450 250.0123 14.25983412431201",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("25338", []),
    },
    {
        "norad_id": "28654",
        "name": "NOAA 18",
        "group_name": "weather",
        "line1": "1 28654U 05018A   26077.49120411  .00000092  00000+0  65000-4 0  9995",
        "line2": "2 28654  98.8890 120.4501 0014201  75.1200 285.3412 14.12501234981234",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("28654", []),
    },
    {
        "norad_id": "33591",
        "name": "NOAA 19",
        "group_name": "weather",
        "line1": "1 33591U 09005A   26077.48123012  .00000115  00000+0  81000-4 0  9998",
        "line2": "2 33591  99.1920 105.1201 0013890  60.4500 300.1200 14.12041234871234",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("33591", []),
    },
    {
        "norad_id": "27607",
        "name": "SO-50",
        "group_name": "amateur",
        "line1": "1 27607U 02058C   26077.51230123  .00000214  00000+0  11000-3 0  9992",
        "line2": "2 27607  64.5510 215.1200 0081200 240.1200 118.4500 14.76012345123456",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("27607", []),
    },
    {
        "norad_id": "43017",
        "name": "AO-91 (RadFxSat)",
        "group_name": "cubesat",
        "line1": "1 43017U 17073E   26077.50123456  .00000312  00000+0  15000-3 0  9991",
        "line2": "2 43017  97.6910  85.1200 0245000  95.1200 268.4500 14.82012345234567",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("43017", []),
    },
    {
        "norad_id": "48274",
        "name": "TIANGONG (CSS)",
        "group_name": "stations",
        "line1": "1 48274U 21035A   26077.51892100  .00021500  00000+0  22000-3 0  9996",
        "line2": "2 48274  41.4720 185.1200 0004500 120.1200 240.4500 15.61012345123456",
        "frequencies": KNOWN_SATELLITE_FREQUENCIES.get("48274", []),
    },
]


def resolve_satellite_group(name: str, norad_id: str, default_group: str = "amateur") -> str:
    """Accurately classifies a satellite into stations, weather, cubesat, or amateur.
    
    CelesTrak feeds overlap heavily (e.g. ISS is listed in amateur.txt for ARISS, and CubeSats
    like FunCube, GreenCube, and SwissCube are listed under amateur rather than cubesat).
    """
    n = name.upper()

    # 1. CubeSats & Nanosats take priority if explicit CubeSat terminology is present in name
    if any(k in n for k in [
        "CUBE", "NANOSAT", "POCKETQUBE", "1U", "2U", "3U", "6U", "12U",
        "FOX-1", "AO-91", "AO-92", "AO-73", "FOSSA", "TINYGS", "RADFXSAT"
    ]):
        return "cubesat"

    # 2. Space Stations / Crewed / Orbital complexes
    if (
        re.search(r'\b(ISS|ZARYA|NAUKA|KIBO|COLUMBUS|TIANGONG|TIANHE|WENTIAN|MENGTIAN|DRAGON|STARLINER|CYGNUS|ORION|SHENZHOU|TIANZHOU|SOYUZ|PROGRESS)\b', n)
        or "CSS " in n or "CSS(" in n or n.startswith("CSS-")
    ):
        return "stations"

    # 3. Weather & Earth Observation
    if re.search(r'\b(NOAA|METEOR|GOES|METOP|FENGYUN|ELEKTRO|HIMAWARI)\b', n):
        return "weather"

    # 4. If default_group is explicitly cubesat/stations/weather, preserve it
    if default_group in ("cubesat", "stations", "weather"):
        return default_group

    return "amateur"


def parse_tle_stream(text: str, group_name: str = "amateur") -> List[Dict[str, Any]]:
    """Parses raw TLE text stream into a structured list of satellite element sets."""
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results = []
    i = 0
    now_ts = datetime.now(timezone.utc).isoformat()

    while i < len(lines):
        # 3-line format: Name, Line 1, Line 2
        if lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name = f"SAT-{lines[i][2:7].strip()}"
            line1 = lines[i]
            line2 = lines[i + 1]
            i += 2
        elif i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            name = lines[i].lstrip("0 ").strip()
            line1 = lines[i + 1]
            line2 = lines[i + 2]
            i += 3
        else:
            i += 1
            continue

        try:
            norad_id = str(int(line1[2:7].strip()))
        except Exception:
            continue

        freqs = KNOWN_SATELLITE_FREQUENCIES.get(norad_id, [])
        resolved_group = resolve_satellite_group(name, norad_id, default_group=group_name)

        results.append({
            "norad_id": norad_id,
            "name": name,
            "group_name": resolved_group,
            "line1": line1,
            "line2": line2,
            "updated_at": now_ts,
            "frequencies": freqs,
        })

    return results


def calculate_doppler_shift(
    freq_mhz: float,
    sat_pos_km: Tuple[float, float, float],
    sat_vel_km_s: Tuple[float, float, float],
    obs_pos_km: Tuple[float, float, float]
) -> Tuple[float, float]:
    """Calculates relative radial velocity and resulting RF Doppler shift.
    
    Returns:
        (doppler_shift_hz, observed_freq_mhz)
        Positive shift indicates satellite is approaching observer.
    """
    rx = sat_pos_km[0] - obs_pos_km[0]
    ry = sat_pos_km[1] - obs_pos_km[1]
    rz = sat_pos_km[2] - obs_pos_km[2]

    range_km = math.sqrt(rx * rx + ry * ry + rz * rz)
    if range_km < 1.0:
        return 0.0, freq_mhz

    # Radial velocity = (r . v) / |r| (km/s)
    # Positive when moving AWAY from observer, negative when APPROACHING
    v_radial = (rx * sat_vel_km_s[0] + ry * sat_vel_km_s[1] + rz * sat_vel_km_s[2]) / range_km

    # Doppler equation: delta_f = -f0 * (v_radial / c)
    freq_hz = freq_mhz * 1e6
    delta_f_hz = -freq_hz * (v_radial / SPEED_OF_LIGHT_KM_S)
    obs_freq_mhz = freq_mhz + (delta_f_hz / 1e6)

    return round(delta_f_hz, 1), round(obs_freq_mhz, 6)


def calculate_footprint_radius_km(altitude_km: float) -> float:
    """Calculates ground radio horizon footprint radius in kilometers based on altitude."""
    if altitude_km <= 0:
        return 0.0
    r = EARTH_RADIUS_KM
    ratio = max(0.0, min(1.0, r / (r + altitude_km)))
    theta = math.acos(ratio)
    return round(r * theta, 1)


class SatelliteDownloadWorker(QThread):
    """Background worker that downloads active TLE groups from CelesTrak."""

    finished_signal = pyqtSignal(list, bool)

    def __init__(self, groups: List[str], custom_url: str = "", parent=None):
        super().__init__(parent)
        self.groups = groups or ["stations", "amateur", "weather"]
        self.custom_url = custom_url.strip()

    def run(self):
        all_tles: List[Dict[str, Any]] = []
        success = False

        if self.custom_url:
            try:
                req = urllib.request.Request(self.custom_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=12) as response:
                    text = response.read().decode("utf-8", errors="replace")
                    tles = parse_tle_stream(text, group_name="custom")
                    all_tles.extend(tles)
                    if tles:
                        success = True
            except Exception as e:
                logger.warning(f"Error fetching custom satellite TLE URL ({self.custom_url}): {e}")

        for grp in self.groups:
            url = CELESTRAK_ENDPOINTS.get(grp)
            if not url:
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=12) as response:
                    text = response.read().decode("utf-8", errors="replace")
                    tles = parse_tle_stream(text, group_name=grp)
                    all_tles.extend(tles)
                    if tles:
                        success = True
            except Exception as e:
                logger.warning(f"Error downloading CelesTrak TLEs for group '{grp}': {e}")

        self.finished_signal.emit(all_tles, success)


class SatelliteService(QObject):
    """Manages satellite TLE synchronization, caching, and pass tracking."""

    tles_updated = pyqtSignal(list)
    pass_alert = pyqtSignal(dict)
    _instance = None

    @classmethod
    def get_instance(cls, storage: Optional[Storage] = None, config: Optional[AppConfig] = None, parent=None):
        if cls._instance is None:
            cls._instance = cls(storage=storage, config=config, parent=parent)
        elif storage and not cls._instance.storage:
            cls._instance.storage = storage
        return cls._instance

    def __init__(self, storage: Optional[Storage] = None, config: Optional[AppConfig] = None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self._download_worker: Optional[SatelliteDownloadWorker] = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_timer)

        self._seed_initial_tles_if_needed()

    def _seed_initial_tles_if_needed(self):
        """Seeds offline fallback TLEs if the satellite_tles table is completely empty."""
        if not self.storage:
            return
        try:
            count = self.storage.get_satellite_tle_count()
            if count == 0:
                logger.info(f"Seeding {len(CURATED_OFFLINE_TLES)} initial offline satellite TLEs...")
                self.storage.save_satellite_tles(CURATED_OFFLINE_TLES)
        except Exception as e:
            logger.debug(f"Error checking initial satellite seed: {e}")

    def start(self):
        """Starts periodic TLE background refresh."""
        interval_hours = 24
        if self.config and hasattr(self.config, "satellites"):
            interval_hours = getattr(self.config.satellites, "update_interval_hours", 24) or 24
        interval_ms = max(3600 * 1000, interval_hours * 3600 * 1000)
        self._refresh_timer.start(interval_ms)

        self._check_and_refresh()

    def stop(self):
        """Stops the service and terminates any running workers."""
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.quit()
            self._download_worker.wait(1500)

    def _on_refresh_timer(self):
        self._check_and_refresh()

    def _check_and_refresh(self):
        """Refreshes TLEs if older than update interval or database is missing data."""
        if not self.storage:
            return
        tles = self.storage.get_satellite_tles()
        if not tles:
            self.refresh_now()
            return

        try:
            newest = max(tles, key=lambda s: s.get("updated_at", ""))
            updated_ts = datetime.fromisoformat(newest["updated_at"].replace("Z", "+00:00")).timestamp()
            interval_secs = (getattr(self.config.satellites, "update_interval_hours", 24) or 24) * 3600
            if (time.time() - updated_ts) > interval_secs:
                self.refresh_now()
        except Exception:
            self.refresh_now()

    def refresh_now(self, force: bool = False):
        """Spawns background download worker to update TLE sets from CelesTrak."""
        if self._download_worker and self._download_worker.isRunning():
            logger.debug("Satellite download worker is already running.")
            return

        groups = ["stations", "amateur", "weather"]
        custom_url = ""
        if self.config and hasattr(self.config, "satellites"):
            groups = getattr(self.config.satellites, "active_groups", groups)
            custom_url = getattr(self.config.satellites, "custom_tle_url", "")

        logger.info(f"Triggering satellite TLE refresh for groups: {groups}...")
        self._download_worker = SatelliteDownloadWorker(groups=groups, custom_url=custom_url, parent=self)
        self._download_worker.finished_signal.connect(self._on_download_finished)
        self._download_worker.start()

    def refresh_tles(self, force: bool = True):
        """Spawns background download worker to update TLE sets from CelesTrak.

        Alias for refresh_now() for UI and API compatibility.
        """
        self.refresh_now(force=force)

    def _on_download_finished(self, tles: List[Dict[str, Any]], success: bool):
        if success and tles and self.storage:
            saved = self.storage.save_satellite_tles(tles)
            logger.info(f"Successfully downloaded and cached {saved} satellite TLEs from CelesTrak.")
            all_cached = self.storage.get_satellite_tles()
            self.tles_updated.emit(all_cached)
            bus.emit(EventType.SATELLITES_UPDATED, all_cached)
        else:
            logger.info("Satellite TLE download finished or using offline cache.")
            if self.storage:
                all_cached = self.storage.get_satellite_tles()
                self.tles_updated.emit(all_cached)

    def get_satellites_for_map(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Returns satellite records ready to be pushed to Leaflet JavaScript."""
        if not self.storage:
            return CURATED_OFFLINE_TLES

        selected = getattr(self.config.satellites, "selected_satellites", []) if self.config else []
        all_sats = self.storage.get_satellite_tles()
        if not all_sats:
            return CURATED_OFFLINE_TLES

        if active_only and selected:
            sel_set = set(str(s) for s in selected)
            filtered = [s for s in all_sats if str(s["norad_id"]) in sel_set]
            return filtered if filtered else all_sats[:30]

        return all_sats
