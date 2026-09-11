"""ADS-B Live Flight Data Service for MeshCore Tray.

Fetches live aircraft flight telemetry from open community aggregators
(api.adsb.lol with fallback to opendata.adsb.fi) within a radius around
a target mesh node or local node for situational awareness and RF
aircraft-scatter correlation.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
import math
import time
from typing import Dict, List, Optional
import urllib.request
import urllib.error
from urllib.parse import urlparse

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

logger = logging.getLogger("meshcore_tray.adsb_service")

# Community aggregators with open REST access (merged concurrently for maximum feeder coverage)
PRIMARY_API_URL = "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
FALLBACK_API_URL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
COMMUNITY_API_ENDPOINTS = [
    PRIMARY_API_URL,
    FALLBACK_API_URL,
]
_ENDPOINT_COOLDOWNS: Dict[str, float] = {}
DEFAULT_RADIUS_NM = 50
USER_AGENT = "MeshCore-Tray/1.0 (ADS-B Layer; OpenCommunityFeeds)"


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in nautical miles."""
    r_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(r_nm * c, 1)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate initial compass bearing in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    b = math.degrees(math.atan2(y, x))
    return round((b + 360.0) % 360.0, 1)


MILITARY_TYPES = {
    "EUFI", "F16", "F15", "F18", "F22", "F35", "TYPH", "HAWK", "TORN", "C17", "C130",
    "A400", "K35R", "KC46", "B52", "B1", "B2", "U2", "E3TF", "RC135", "P8", "V22",
    "GR9", "M346", "L39", "T38", "JAS39", "RAFALE", "MIR2", "A10", "SU27", "SU30",
    "SU35", "MIG29", "IL76", "TU95", "TU160", "KC2", "C30J", "V22B", "C27J", "CN35",
    "C295", "E3CF", "KC39", "AT6", "T6", "PC21", "PC9", "T21", "AJET", "F5"
}

HELICOPTER_TYPES = {
    "R22", "R44", "R66", "H120", "H125", "H130", "H135", "H145", "H155", "H160", "H175", "H215", "H225",
    "EC20", "EC25", "EC30", "EC35", "EC45", "EC55", "EC75", "AS50", "AS55", "AS65", "AS32", "AS35",
    "A109", "A119", "A139", "A169", "A189", "AW39", "AW69", "AW89", "AW09", "AW01", "EH10",
    "B06", "B206", "B212", "B412", "B429", "B407", "B505", "UH1", "AH1",
    "S76", "S92", "S61", "S70", "UH60", "SH60", "MH60", "CH53", "H53",
    "CH47", "H47", "AH64", "H64", "W3", "NH90", "T129",
    "LYNX", "WILD", "GAZL", "MD50", "MD52", "MD60", "EXPL", "HUCO", "G2CA", "CABR",
    "EN28", "EN48", "H269", "S300", "B47", "BK11", "SKOR",
    "MI8", "MI17", "MI24", "MI28", "KA27", "KA32", "KA50", "KA52"
}

GLIDER_TYPES = {
    "ASK21", "DG1000", "LS4", "LS8", "DISC", "VENT", "DUOD", "ASW20", "ASW24", "ASW27", "ASW28",
    "ASG29", "ASH30", "ASH31", "K13", "K21", "PUCH", "JANU", "NIMB", "CIRR", "APIS", "FOX",
    "S10", "S12", "DG800", "DG500", "ARCUS", "VENTUS", "DISCUS", "TAIF", "GROB", "G102", "G103"
}

LIGHT_AIRCRAFT_TYPES = {
    "C150", "C152", "C172", "C182", "C206", "C208", "C210", "PA28", "PA38", "PA32", "PA34",
    "PA18", "PA24", "PA30", "DA20", "DA40", "DA42", "DA62", "SR20", "SR22", "RV4", "RV6",
    "RV7", "RV8", "RV9", "RV10", "RV12", "RV14", "P28A", "DR40", "TB10", "TB20", "BE36",
    "BE58", "BE35", "BE33", "BE76", "AA5", "G109", "M20P", "M20T", "B36TC", "B55", "PA46",
    "TBM7", "TBM8", "TBM9", "PC12", "C310", "C340", "C421", "P68", "E300", "A210", "P92",
    "P2002", "P2006", "P2008", "P2010", "AT01", "DV20", "P28R", "PA44", "PA31", "C170",
    "C180", "C185", "B350", "B200", "BE20", "BE90", "C90", "PC6", "DHC2", "DHC3", "C162"
}

AIRLINER_TYPES = {
    "A318", "A319", "A320", "A321", "A330", "A332", "A333", "A338", "A339", "A340", "A343",
    "A345", "A346", "A350", "A359", "A35K", "A380", "A388", "B737", "B738", "B739", "B38M",
    "B39M", "B744", "B748", "B752", "B753", "B762", "B763", "B764", "B772", "B773", "B77W",
    "B788", "B789", "B78X", "E170", "E175", "E190", "E195", "E290", "E295", "E75L", "CRJ2",
    "CRJ7", "CRJ9", "CRJX", "AT72", "AT76", "AT45", "AT46", "DH8D", "Q400", "BCS1", "BCS3",
    "A220", "MD11", "MD80", "MD82", "MD88", "B712", "F70", "F100", "SSJ9", "C919", "ARJ2",
    "RJ85", "RJ1H", "B703", "B722", "DC10", "L101"
}


def classify_aircraft(ac: dict, alt_val: Optional[int] = None, gs: Optional[float] = None) -> tuple[str, str]:
    """Classifies aircraft into category key and display name.

    Returns:
        (category_key, category_name)
        category_key in ('airliner', 'light', 'military', 'helicopter', 'glider', 'general')
    """
    category = str(ac.get("category") or "").upper().strip()
    ac_type = str(ac.get("t") or "").upper().strip()
    desc = str(ac.get("desc") or "").lower().strip()
    flight = str(ac.get("flight") or "").upper().strip()
    db_flags = int(ac.get("dbFlags", 0) or 0)
    is_mil = bool(ac.get("military") or (db_flags & 1))

    # 1. Military
    if is_mil or category == "A6" or ac_type in MILITARY_TYPES:
        return "military", "Military Aircraft"

    # 2. Helicopter / Rotorcraft
    if (
        category == "A7"
        or ac_type in HELICOPTER_TYPES
        or (ac_type.startswith("H") and len(ac_type) <= 4 and ac_type[1:3].isdigit())
        or any(term in desc for term in ["helicopter", "rotorcraft", "eurocopter", "bell ", "sikorsky", "agusta", "robinson", "westland", "guimbal", "leonardo"])
        or any(flight.startswith(pfx) for pfx in ["SRD", "HLE", "UKP", "HELI", "RESCUE", "MEDEVAC"])
    ):
        return "helicopter", "Helicopter"

    # 3. Glider / Sailplane
    if (
        category in ["B1", "B4"]
        or ac_type in GLIDER_TYPES
        or any(term in desc for term in ["glider", "sailplane", "schleicher", "schempp", "alexander schleicher"])
    ):
        return "glider", "Glider / Sailplane"

    # 4. Light Aircraft / Small Plane
    if (
        category == "A1"
        or ac_type in LIGHT_AIRCRAFT_TYPES
        or desc.startswith("l1p")
        or desc.startswith("l2p")
        or any(term in desc for term in ["cessna", "piper", "cirrus", "diamond", "vans", "beechcraft", "robin", "mooney", "socata", "tecnam", "aquila"])
    ):
        return "light", "Light Aircraft"

    # 5. Commercial Airliner
    if (
        category in ["A2", "A3", "A4", "A5"]
        or ac_type in AIRLINER_TYPES
        or any(term in desc for term in ["airbus", "boeing", "embraer", "bombardier", "atr", "mcdonnell", "fokker"])
        or (alt_val is not None and alt_val > 15000)
        or (gs is not None and gs > 280)
    ):
        return "airliner", "Commercial Airliner"

    # 6. Fallback General Aviation
    return "general", "General Aviation"



class ADSBFetchWorker(QThread):
    """Background thread worker to query ADS-B REST APIs without blocking GUI."""

    success_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, lat: float, lon: float, radius_nm: int, target_info: dict, parent=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.radius_nm = radius_nm
        self.target_info = target_info

    def run(self):
        urls = [
            endpoint.format(lat=self.lat, lon=self.lon, dist=self.radius_nm)
            for endpoint in COMMUNITY_API_ENDPOINTS
        ]

        def _fetch_single_feed(target_url: str) -> Optional[dict]:
            parsed = urlparse(target_url)
            host = parsed.netloc
            now = time.time()
            if host in _ENDPOINT_COOLDOWNS:
                if now < _ENDPOINT_COOLDOWNS[host]:
                    # In active rate-limit cooldown, skip request
                    return None
                else:
                    _ENDPOINT_COOLDOWNS.pop(host, None)

            try:
                req = urllib.request.Request(target_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=4.5) as response:
                    if response.status == 200:
                        raw = response.read().decode("utf-8")
                        return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    cooldown = 60.0
                    if retry_after:
                        try:
                            cooldown = float(retry_after)
                        except (ValueError, TypeError):
                            pass
                    _ENDPOINT_COOLDOWNS[host] = now + cooldown
                    logger.info("ADS-B community aggregator %s requested backoff (HTTP 429 Too Many Requests); cooling down for %ds", host, int(cooldown))
                else:
                    logger.debug("ADS-B query to %s failed: HTTP %s", target_url, e.code)
            except Exception as e:
                logger.debug("ADS-B query to %s failed: %s", target_url, e)
            return None

        merged_raw_ac: Dict[str, dict] = {}
        successful_feeds = 0
        last_err = None

        with ThreadPoolExecutor(max_workers=len(urls)) as executor:
            future_to_url = {executor.submit(_fetch_single_feed, u): u for u in urls}
            for future in as_completed(future_to_url):
                u = future_to_url[future]
                try:
                    data = future.result()
                    if not data:
                        continue
                    successful_feeds += 1
                    raw_list = data.get("ac", data.get("aircraft", []))
                    if not isinstance(raw_list, list):
                        continue
                    for ac in raw_list:
                        if not isinstance(ac, dict):
                            continue
                        hex_code = str(ac.get("hex") or "").lower().strip()
                        if not hex_code:
                            continue
                        if hex_code not in merged_raw_ac:
                            merged_raw_ac[hex_code] = dict(ac)
                        else:
                            existing = merged_raw_ac[hex_code]
                            # Prefer fresher telemetry position if available
                            e_seen = existing.get("seen_pos", existing.get("seen", 999.0))
                            n_seen = ac.get("seen_pos", ac.get("seen", 999.0))
                            if n_seen < e_seen and ac.get("lat") is not None and ac.get("lon") is not None:
                                for k in ["lat", "lon", "alt_baro", "alt_geom", "gs", "track", "squawk", "seen", "seen_pos"]:
                                    if ac.get(k) is not None:
                                        existing[k] = ac[k]
                            # Complement missing descriptive metadata
                            for k in ["flight", "t", "desc", "r", "category", "emergency"]:
                                if not existing.get(k) and ac.get(k):
                                    existing[k] = ac[k]
                except Exception as e:
                    last_err = e

        if successful_feeds == 0 and not merged_raw_ac:
            err_msg = f"ADS-B fetch failed on all community aggregators: {last_err}"
            logger.warning(err_msg)
            self.error_signal.emit(err_msg)
            return

        raw_ac = list(merged_raw_ac.values())

        parsed_aircraft: List[Dict] = []
        for ac in raw_ac:
            if not isinstance(ac, dict):
                continue
            ac_lat = ac.get("lat")
            ac_lon = ac.get("lon")
            if ac_lat is None or ac_lon is None:
                continue

            try:
                ac_lat = float(ac_lat)
                ac_lon = float(ac_lon)
            except (ValueError, TypeError):
                continue

            # Altitude
            alt_baro = ac.get("alt_baro")
            if alt_baro == "ground":
                alt_val = 0
            else:
                try:
                    alt_val = int(alt_baro) if alt_baro is not None else None
                except (ValueError, TypeError):
                    alt_val = None

            # Distance and bearing relative to target
            dst = ac.get("dst")
            if dst is not None:
                try:
                    dst = round(float(dst), 1)
                except (ValueError, TypeError):
                    dst = haversine_nm(self.lat, self.lon, ac_lat, ac_lon)
            else:
                dst = haversine_nm(self.lat, self.lon, ac_lat, ac_lon)

            dir_bearing = ac.get("dir")
            if dir_bearing is not None:
                try:
                    dir_bearing = round(float(dir_bearing), 1)
                except (ValueError, TypeError):
                    dir_bearing = bearing_deg(self.lat, self.lon, ac_lat, ac_lon)
            else:
                dir_bearing = bearing_deg(self.lat, self.lon, ac_lat, ac_lon)

            flight = (ac.get("flight") or "").strip()
            reg = (ac.get("r") or "").strip()
            ac_type = (ac.get("t") or "").strip()
            desc = (ac.get("desc") or "").strip()
            squawk = str(ac.get("squawk") or "").strip()
            emergency = (ac.get("emergency") or "none").strip()
            cat_key, cat_name = classify_aircraft(ac, alt_val, ac.get("gs"))

            parsed_aircraft.append({
                "hex": str(ac.get("hex") or "").lower().strip(),
                "flight": flight,
                "r": reg,
                "t": ac_type,
                "desc": desc,
                "category": cat_key,
                "category_name": cat_name,
                "alt_baro": alt_val,
                "alt_geom": ac.get("alt_geom"),
                "gs": ac.get("gs"),
                "track": ac.get("track") if ac.get("track") is not None else ac.get("true_heading", 0),
                "lat": ac_lat,
                "lon": ac_lon,
                "squawk": squawk,
                "emergency": emergency,
                "dst": dst,
                "dir": dir_bearing,
            })

        payload = {
            "aircraft": parsed_aircraft,
            "target_info": self.target_info,
            "total": len(parsed_aircraft),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.success_signal.emit(payload)


class ADSBService(QObject):
    """Manages periodic polling and target node state for the ADS-B map layer."""

    flights_updated = pyqtSignal(dict)
    loading_signal = pyqtSignal(bool)
    error_signal = pyqtSignal(str)
    photo_received = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self.radius_nm = DEFAULT_RADIUS_NM
        self.target_lat: Optional[float] = None
        self.target_lon: Optional[float] = None
        self.target_node_id: str = ""
        self.target_alias: str = "Local Node"

        self._active_worker: Optional[ADSBFetchWorker] = None
        self._retired_workers: List[QThread] = []
        self._query_generation: int = 0
        self._photo_cache: Dict[str, dict] = {}
        self._negative_photo_cache: Dict[str, float] = {}
        self._photo_workers: Dict[str, AircraftPhotoWorker] = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)  # 5s refresh to match 5s radar sweep
        self._poll_timer.timeout.connect(self._on_poll_timer)


    def set_enabled(self, enabled: bool):
        """Enable or disable polling for the ADS-B layer."""
        self.enabled = bool(enabled)
        if self.enabled:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            self.refresh()
        else:
            self._poll_timer.stop()

    def set_target(self, node_id: str, alias: str, lat: float, lon: float, radius_nm: int = DEFAULT_RADIUS_NM):
        """Set the target center point for ADS-B radius queries."""
        self.target_node_id = str(node_id or "")
        self.target_alias = str(alias or "Target Node")
        self.target_lat = float(lat)
        self.target_lon = float(lon)
        self.radius_nm = max(10, min(int(radius_nm), 250))
        self._query_generation += 1

        if self.enabled:
            if self._active_worker is not None and self._active_worker.isRunning():
                # Worker is already in flight. Queue pending refresh so when it finishes,
                # the new target query starts immediately without thread-unsafe disconnect().
                self._pending_refresh = True
            else:
                self.refresh()

    def set_target_from_local_or_default(self, lat: Optional[float], lon: Optional[float], alias: str = "Local Node"):
        """Set target to local radio node or fallback coordinates."""
        self.target_node_id = ""
        self.target_alias = alias or "Local Node"
        if lat is not None and lon is not None:
            self.target_lat = float(lat)
            self.target_lon = float(lon)
        else:
            # Fallback UK center if zero GPS available
            self.target_lat = 54.5
            self.target_lon = -3.5
        self._query_generation += 1

        if self.enabled:
            if self._active_worker is not None and self._active_worker.isRunning():
                self._pending_refresh = True
            else:
                self.refresh()

    def refresh(self):
        """Trigger an immediate asynchronous query."""
        if not self.enabled or self.target_lat is None or self.target_lon is None:
            return

        if self._active_worker is not None:
            if self._active_worker.isRunning():
                logger.debug("ADS-B worker already running, skipping overlapping request")
                return
            else:
                try:
                    self._active_worker.wait(50)
                except Exception:
                    pass
                self._retired_workers.append(self._active_worker)
                self._active_worker = None

        self._clean_retired_workers()

        self._query_generation += 1
        current_gen = self._query_generation

        target_info = {
            "node_id": self.target_node_id,
            "alias": self.target_alias,
            "lat": self.target_lat,
            "lon": self.target_lon,
            "radius_nm": self.radius_nm,
            "generation": current_gen,
        }

        self.loading_signal.emit(True)
        self._active_worker = ADSBFetchWorker(
            lat=self.target_lat,
            lon=self.target_lon,
            radius_nm=self.radius_nm,
            target_info=target_info,
            parent=None
        )
        self._active_worker.success_signal.connect(self._on_worker_success)
        self._active_worker.error_signal.connect(self._on_worker_error)
        self._active_worker.finished.connect(self._on_worker_finished)
        self._active_worker.start()

    def _on_poll_timer(self):
        if self.enabled:
            self.refresh()

    def _on_worker_success(self, payload: dict):
        self.loading_signal.emit(False)
        ti = payload.get("target_info", {})
        payload_gen = ti.get("generation", 0)
        # Verify response matches current target generation and target coordinates
        if payload_gen and payload_gen != self._query_generation:
            logger.debug("Discarding stale ADS-B payload (generation %s != %s)", payload_gen, self._query_generation)
            return
        if self.target_lat is not None and self.target_lon is not None:
            if abs(ti.get("lat", 0) - self.target_lat) > 0.001 or abs(ti.get("lon", 0) - self.target_lon) > 0.001:
                logger.debug("Discarding stale ADS-B payload for previous target location")
                return
        self.flights_updated.emit(payload)

    def _on_worker_error(self, err: str):
        self.loading_signal.emit(False)
        if getattr(self, "_pending_refresh", False):
            return
        self.error_signal.emit(err)

    def _on_worker_finished(self):
        worker = self._active_worker
        if worker is not None:
            try:
                worker.wait(100)
            except Exception:
                pass
            self._retired_workers.append(worker)
            self._active_worker = None
        self._clean_retired_workers()
        if getattr(self, "_pending_refresh", False):
            self._pending_refresh = False
            self.refresh()

    def _clean_retired_workers(self):
        survivors = []
        for w in self._retired_workers:
            if w.isFinished():
                try:
                    w.wait(50)
                except Exception:
                    pass
            else:
                survivors.append(w)
        self._retired_workers = survivors

    def request_aircraft_photo(self, hex_code: str):
        """Asynchronously query Planespotters / Airport-Data photo for aircraft hex."""
        h = (hex_code or "").lower().strip()
        if not h:
            return
        if h in self._photo_cache:
            self.photo_received.emit(h, self._photo_cache[h])
            return
        if h in self._negative_photo_cache:
            # 180s cooldown for empty/404 photos to prevent hammering external APIs
            if time.time() - self._negative_photo_cache[h] < 180:
                self.photo_received.emit(h, {})
                return
            else:
                self._negative_photo_cache.pop(h, None)
        if h in self._photo_workers and self._photo_workers[h].isRunning():
            return

        worker = AircraftPhotoWorker(h, parent=None)
        self._photo_workers[h] = worker
        worker.photo_ready.connect(self._on_photo_ready)
        worker.finished.connect(lambda w=worker, h=h: self._on_photo_worker_finished(h, w))
        worker.start()

    def _on_photo_worker_finished(self, hex_code: str, worker: AircraftPhotoWorker):
        try:
            worker.wait(100)
        except Exception:
            pass
        self._photo_workers.pop(hex_code, None)
        self._retired_workers.append(worker)
        self._clean_retired_workers()

    def cleanup(self):
        """Safely stops timers and waits for any running threads to terminate."""
        self.enabled = False
        if hasattr(self, "_poll_timer") and self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._active_worker is not None:
            if self._active_worker.isRunning():
                self._active_worker.wait(1000)
            self._active_worker = None
        for h, w in list(self._photo_workers.items()):
            if w.isRunning():
                w.wait(1000)
        self._photo_workers.clear()
        for w in list(self._retired_workers):
            if w.isRunning():
                w.wait(1000)
        self._retired_workers.clear()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _on_photo_ready(self, hex_code: str, photo_info: dict):
        if photo_info and photo_info.get("thumbnail"):
            self._photo_cache[hex_code] = photo_info
            self._negative_photo_cache.pop(hex_code, None)
        else:
            self._negative_photo_cache[hex_code] = time.time()
        self.photo_received.emit(hex_code, photo_info or {})


class AircraftPhotoWorker(QThread):
    """Background worker to query Planespotters.net or Airport-Data.com API for aircraft imagery."""

    photo_ready = pyqtSignal(str, dict)

    def __init__(self, hex_code: str, parent=None):
        super().__init__(parent)
        self.hex_code = hex_code.lower().strip()

    def run(self):
        photo_info = {}

        # 1. Try Planespotters.net first (high-quality airline photos)
        try:
            url = f"https://api.planespotters.net/pub/photos/hex/{self.hex_code}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MeshCore-Navigator/0.1.0 (+https://github.com/SoulwayStudios/meshcore-navigator)"}
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                photos = data.get("photos", [])
                if photos:
                    p = photos[0]
                    thumb = (p.get("thumbnail_large") or {}).get("src") or (p.get("thumbnail") or {}).get("src") or ""
                    if thumb:
                        photographer = p.get("photographer") or "Planespotters"
                        link = p.get("link") or f"https://www.planespotters.net/hex/{self.hex_code}"
                        ac_type = p.get("aircraft_type") or ""
                        airline = (p.get("airline") or {}).get("name") or ""
                        photo_info = {
                            "thumbnail": thumb,
                            "photographer": photographer,
                            "link": link,
                            "aircraft_type": ac_type,
                            "airline": airline,
                            "source": "Planespotters.net",
                            "source_label": "Planespotters ↗",
                        }
        except Exception as e:
            logger.debug(f"Planespotters photo lookup failed for hex {self.hex_code}: {e}")

        # 2. Fallback to Airport-Data.com (covers general aviation, turboprops, helicopters, private aircraft)
        if not photo_info or not photo_info.get("thumbnail"):
            try:
                ap_url = f"https://airport-data.com/api/ac_thumb.json?m={self.hex_code}&n=1"
                ap_req = urllib.request.Request(
                    ap_url,
                    headers={"User-Agent": "MeshCore-Navigator/0.1.0 (+https://github.com/SoulwayStudios/meshcore-navigator)"}
                )
                with urllib.request.urlopen(ap_req, timeout=3.5) as ap_resp:
                    ap_data = json.loads(ap_resp.read().decode("utf-8"))
                    if ap_data.get("status") == 200 and ap_data.get("data"):
                        item = ap_data["data"][0]
                        img_url = item.get("image") or ""
                        if img_url:
                            photographer = item.get("photographer") or "Airport-Data"
                            link = item.get("link") or f"https://airport-data.com/aircraft/photo/{img_url.split('/')[-1].replace('.jpg', '')}"
                            photo_info = {
                                "thumbnail": img_url,
                                "photographer": photographer,
                                "link": link,
                                "source": "Airport-Data.com",
                                "source_label": "Airport-Data ↗",
                            }
            except Exception as e:
                logger.debug(f"Airport-Data photo lookup failed for hex {self.hex_code}: {e}")

        self.photo_ready.emit(self.hex_code, photo_info or {})
