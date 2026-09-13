#!/usr/bin/env python3
"""Automated High-Resolution Map Showcase Screenshot Generator for MESHCORE NAVIGATOR.

Generates crisp, professional screenshots of all advanced map features:
1. Tropospheric Ducting Forecast over the entire UK
2. Thunderstorm Precipitation Radar & Blitzortung Lightning over the entire UK
3. Observer Air Traffic (ADS-B Aircraft Tracking with Range Rings & Altitudes)
4. Companion Orbitals at a high-zoom tactical level
5. Multi-Hop Packet Route Tracing across repeaters
"""

import os
import sys
import time
import math
import struct
import base64
import json
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "vendor" / "pixoo" / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "vendor" / "meshcore_py" / "src"))

os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["MESHCORE_TEST_MODE"] = "1"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, Qt
from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import (
    NodeContact, MessageEnvelope, ChannelInfo, NeighbourInfo, PacketPathInfo
)
from meshcore_tray.core.thunderstorm_service import fetch_rainviewer_metadata
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget


OUTPUT_DIR = PROJECT_ROOT / "docs" / "assets" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SCRATCH_DIR = PROJECT_ROOT / "scratch"
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
DEMO_DB = SCRATCH_DIR / "demo_map_showcase.db"


def wait_events(app: QApplication, duration_seconds: float = 2.0):
    """Pumps Qt event loop continuously so WebEngine and Leaflet animations settle."""
    start = time.time()
    while time.time() - start < duration_seconds:
        app.processEvents()
        time.sleep(0.02)


def init_uk_demo_database() -> Storage:
    """Populates an isolated demo database with nodes across the British Isles."""
    if DEMO_DB.exists():
        try:
            DEMO_DB.unlink()
        except Exception:
            pass

    storage = Storage(DEMO_DB)

    storage.save_channel(ChannelInfo(channel_id=0, name="Public", is_favorite=True))
    storage.save_channel(ChannelInfo(channel_id=1, name="Emergency", is_favorite=True))
    storage.save_channel(ChannelInfo(channel_id=2, name="Tech-Talk"))
    storage.save_channel(ChannelInfo(channel_id=3, name="UK-Narrow"))

    uk_nodes = [
        # London & South East
        NodeContact(node_id="!c0ffee01", alias="M7NCY-Base (London)", is_favorite=True, latitude=51.5074, longitude=-0.1278, snr_db=12.5, rssi_dbm=-72.0, is_repeater=False),
        NodeContact(node_id="!c0ffee02", alias="BoxHill-Rpt [RPT]", is_favorite=True, latitude=51.2560, longitude=-0.3060, snr_db=9.0, rssi_dbm=-86.0, is_repeater=True),
        NodeContact(node_id="!c0ffee03", alias="CrystalPalace-Rpt [RPT]", is_favorite=True, latitude=51.4240, longitude=-0.0710, snr_db=11.2, rssi_dbm=-80.0, is_repeater=True),
        NodeContact(node_id="!c0ffee04", alias="London-Hub [ROOM]", is_favorite=True, latitude=51.5150, longitude=-0.0900, snr_db=8.5, rssi_dbm=-89.0, is_room_server=True),
        NodeContact(node_id="!c0ffee05", alias="G4XYZ-Mobile", is_favorite=False, latitude=51.4600, longitude=-0.3000, snr_db=6.0, rssi_dbm=-98.0, is_repeater=False),
        NodeContact(node_id="!c0ffee06", alias="SurreyHills-Node", is_favorite=False, latitude=51.2000, longitude=-0.4000, snr_db=5.2, rssi_dbm=-102.0, is_repeater=False),
        NodeContact(node_id="!c0ffee07", alias="KentGateway [RPT]", is_favorite=False, latitude=51.2800, longitude=0.5200, snr_db=7.1, rssi_dbm=-95.0, is_repeater=True),
        # Midlands & North
        NodeContact(node_id="!c0ffee08", alias="WinterHill-Rpt [RPT]", is_favorite=True, latitude=53.6290, longitude=-2.5200, snr_db=10.0, rssi_dbm=-82.0, is_repeater=True),
        NodeContact(node_id="!c0ffee09", alias="PeakDistrict-Node", is_favorite=False, latitude=53.3500, longitude=-1.8200, snr_db=6.8, rssi_dbm=-96.0, is_repeater=False),
        NodeContact(node_id="!c0ffee10", alias="Bilsdale-Rpt [RPT]", is_favorite=False, latitude=54.3600, longitude=-1.1600, snr_db=8.0, rssi_dbm=-88.0, is_repeater=True),
        # Scotland
        NodeContact(node_id="!c0ffee11", alias="ArthurSeat-Rpt [RPT]", is_favorite=True, latitude=55.9440, longitude=-3.1610, snr_db=9.5, rssi_dbm=-85.0, is_repeater=True),
        NodeContact(node_id="!c0ffee12", alias="Highlands-Hub [ROOM]", is_favorite=False, latitude=57.1497, longitude=-2.0943, snr_db=7.2, rssi_dbm=-94.0, is_room_server=True),
        # Wales
        NodeContact(node_id="!c0ffee13", alias="Snowdon-Rpt [RPT]", is_favorite=True, latitude=53.0685, longitude=-4.0763, snr_db=8.8, rssi_dbm=-87.0, is_repeater=True),
        NodeContact(node_id="!c0ffee14", alias="Cardiff-Bay-Node", is_favorite=False, latitude=51.4650, longitude=-3.1650, snr_db=6.5, rssi_dbm=-97.0, is_repeater=False),
        # Northern Ireland & South West
        NodeContact(node_id="!c0ffee15", alias="DivisMountain-Rpt [RPT]", is_favorite=True, latitude=54.6130, longitude=-6.0120, snr_db=9.1, rssi_dbm=-86.0, is_repeater=True),
        NodeContact(node_id="!c0ffee16", alias="Dartmoor-Rpt [RPT]", is_favorite=False, latitude=50.5700, longitude=-3.9200, snr_db=7.5, rssi_dbm=-92.0, is_repeater=True),
    ]
    for n in uk_nodes:
        storage.save_contact(n)

    return storage


def create_tropo_payload() -> dict:
    """Fetches real live NOAA GFS refractivity GeoTIFF from F5LEN and extracts Europe/UK grid."""
    from meshcore_tray.core.tropo_service import (
        compute_timestep,
        extract_tropo_europe_grid,
        DEFAULT_CACHE_DIR,
        BASE_TROPO_URL,
    )
    import requests

    dt, fn, label = compute_timestep(offset_hours=0)
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tif_path = DEFAULT_CACHE_DIR / fn
    if not tif_path.exists() or tif_path.stat().st_size < 10000:
        url = f"{BASE_TROPO_URL.rstrip('/')}/{fn}"
        r = requests.get(url, headers={"User-Agent": "MeshCore-Tray/1.0"}, timeout=15)
        if r.status_code == 200 and len(r.content) > 10000:
            with open(tif_path, "wb") as f:
                f.write(r.content)

    grid_dict = extract_tropo_europe_grid(tif_path)
    return {
        "grid": grid_dict,
        "filename": fn,
        "label": label,
    }



def create_adsb_payload() -> dict:
    """Generates realistic commercial and GA flights around London/Surrey airspace."""
    target_info = {
        "node_id": "!c0ffee01",
        "alias": "M7NCY-Base (London)",
        "lat": 51.5074,
        "lon": -0.1278,
        "radius_nm": 50
    }
    aircraft = [
        {"hex": "400a01", "flight": "BAW142", "r": "G-ZBKB", "t": "B789", "lat": 51.4700, "lon": -0.4543, "alt_baro": 2400, "track": 270.0, "gs": 160.0, "squawk": "2411", "category": "airliner"},
        {"hex": "400a02", "flight": "EZY892", "r": "G-EZTG", "t": "A320", "lat": 51.3500, "lon": -0.2200, "alt_baro": 8500, "track": 110.0, "gs": 280.0, "squawk": "6201", "category": "airliner"},
        {"hex": "400a03", "flight": "VIR45", "r": "G-VNEW", "t": "B789", "lat": 51.6200, "lon": -0.0500, "alt_baro": 28000, "track": 290.0, "gs": 440.0, "squawk": "7104", "category": "airliner"},
        {"hex": "400a04", "flight": "RYR712", "r": "EI-EFR", "t": "B738", "lat": 51.8850, "lon": 0.2350, "alt_baro": 3400, "track": 45.0, "gs": 180.0, "squawk": "1422", "category": "airliner"},
        {"hex": "400a05", "flight": "KLM1012", "r": "PH-EXA", "t": "E190", "lat": 51.5500, "lon": 0.1500, "alt_baro": 14200, "track": 75.0, "gs": 340.0, "squawk": "3210", "category": "airliner"},
        {"hex": "400a06", "flight": "G-ABCD", "r": "G-ABCD", "t": "C172", "lat": 51.2800, "lon": -0.3800, "alt_baro": 2200, "track": 195.0, "gs": 110.0, "squawk": "7000", "category": "light"},
        {"hex": "400a07", "flight": "AFR1680", "r": "F-HEPI", "t": "A320", "lat": 51.2200, "lon": -0.1000, "alt_baro": 21000, "track": 140.0, "gs": 410.0, "squawk": "5512", "category": "airliner"},
        {"hex": "400a08", "flight": "DLH905", "r": "D-AISV", "t": "A321", "lat": 51.7200, "lon": -0.3500, "alt_baro": 19500, "track": 95.0, "gs": 395.0, "squawk": "4421", "category": "airliner"},
        {"hex": "400a09", "flight": "UAE003", "r": "A6-EOX", "t": "A388", "lat": 51.4000, "lon": 0.4000, "alt_baro": 33000, "track": 115.0, "gs": 490.0, "squawk": "6130", "category": "airliner"},
        {"hex": "400a10", "flight": "G-LOCO", "r": "G-LOCO", "t": "PA28", "lat": 51.1800, "lon": -0.2500, "alt_baro": 1800, "track": 320.0, "gs": 105.0, "squawk": "7000", "category": "light"},
        {"hex": "400a11", "flight": "RRR2204", "r": "ZZ330", "t": "A332", "lat": 51.8000, "lon": -1.2500, "alt_baro": 26000, "track": 240.0, "gs": 460.0, "squawk": "4321", "category": "military"},
        {"hex": "400a12", "flight": "G-HELI", "r": "G-HELI", "t": "EC35", "lat": 51.5200, "lon": -0.2800, "alt_baro": 1200, "track": 60.0, "gs": 120.0, "squawk": "0020", "category": "helicopter"},
    ]
    return {
        "target_info": target_info,
        "total": len(aircraft),
        "aircraft": aircraft
    }


def generate_all_map_screenshots():
    """Builds and captures high-resolution screenshots for each unique map feature."""
    print("Initializing UK mesh database...")
    storage = init_uk_demo_database()

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = AppConfig()
    cfg.meshcore.node_id = "!c0ffee01"
    cfg.meshcore.node_alias = "M7NCY-Base (London)"
    cfg.meshcore.latitude = 51.5074
    cfg.meshcore.longitude = -0.1278

    map_w = MeshMapWidget(config=cfg, storage=storage)
    map_w.resize(1380, 800)
    map_w.show()

    print("Waiting for Leaflet engine to load...")
    wait_events(app, 4.0)

    # -------------------------------------------------------------
    # 1. Tropospheric Ducting Forecast over the entire UK
    # -------------------------------------------------------------
    print("Capturing 01_map_uk_tropospheric_ducting.png...")
    map_w.run_js("map.setView([54.4, -2.5], 6); void 0;")
    wait_events(app, 1.0)
    tropo_payload = create_tropo_payload()
    map_w.run_js(f"window.onTropoDataReady({json.dumps(tropo_payload)}); void 0;")
    if hasattr(map_w, "watcher_status"):
        map_w.watcher_status.setText("📡 <b>Tropo:</b> Loaded 13 Sep 15:00 UTC (NOAA GFS Refractivity Forecast • High VHF/UHF Ducting over UK)")
    wait_events(app, 1.5)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "01_map_uk_tropospheric_ducting.png"))
    map_w.run_js("clearTropoLayer(); void 0;")

    # -------------------------------------------------------------
    # 2. Thunderstorm Precipitation Radar & Blitzortung Lightning over UK
    # -------------------------------------------------------------
    print("Capturing 02_map_uk_thunderstorm_radar.png...")
    map_w.run_js("map.setView([54.4, -2.5], 6); void 0;")
    wait_events(app, 0.8)
    radar_meta = fetch_rainviewer_metadata() or {
        "host": "https://tilecache.rainviewer.com",
        "path": "/v2/radar/nowcast_latest"
    }
    map_w.set_thunderstorm(True)
    map_w.run_js(f"updateThunderstormRadar({json.dumps(radar_meta)}); void 0;")
    strikes = [
        {"lat": 53.48, "lon": -2.24},
        {"lat": 53.38, "lon": -1.90},
        {"lat": 53.62, "lon": -2.48},
        {"lat": 52.95, "lon": -1.15},
        {"lat": 52.48, "lon": -1.89},
        {"lat": 53.08, "lon": -3.85},
        {"lat": 53.25, "lon": -3.50},
        {"lat": 54.10, "lon": -1.50},
        {"lat": 54.35, "lon": -1.20},
        {"lat": 51.60, "lon": -0.80},
    ]
    for s in strikes:
        map_w.run_js(f"addLightningStrike({json.dumps(s)}); void 0;")

    if hasattr(map_w, "watcher_status"):
        map_w.watcher_status.setText("🌩️ <b>Thunderstorms:</b> Live RainViewer radar & Blitzortung lightning active (42 strikes detected over UK)")
    wait_events(app, 2.0)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "02_map_uk_thunderstorm_radar.png"))
    map_w.set_thunderstorm(False)

    # -------------------------------------------------------------
    # 3. Observer / ADS-B Aircraft Traffic (Centered on London Observer Node)
    # -------------------------------------------------------------
    print("Capturing 03_map_uk_aircraft_observer.png...")
    map_w.set_adsb_target("!c0ffee01", "M7NCY-Base (London)", 51.5074, -0.1278, radius_nm=50)
    map_w.run_js("map.setView([51.5074, -0.1278], 9); void 0;")
    wait_events(app, 1.0)
    map_w.set_adsb(True)
    adsb_payload = create_adsb_payload()
    map_w.run_js(f"setAdsbVisible(true); onAdsbDataReady({json.dumps(adsb_payload)}); void 0;")
    if hasattr(map_w, "watcher_status"):
        map_w.watcher_status.setText("✈️ <b>ADS-B Observer:</b> Monitoring 12 aircraft within 50 NM radar range around M7NCY-Base (London)")
    wait_events(app, 2.0)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "03_map_uk_aircraft_observer.png"))
    map_w.set_adsb(False)

    # -------------------------------------------------------------
    # 4. Companion Orbitals at a Zoomed-In Tactical Level
    # -------------------------------------------------------------
    print("Capturing 04_map_companion_orbitals_zoomed.png...")
    map_w.run_js("map.setView([51.2560, -0.3060], 15); void 0;")
    wait_events(app, 1.0)
    map_w.set_orbitals(True)
    docked_data = {
        "!c0ffee02": [
            {"node_id": "!c0ffee05", "alias": "G4XYZ-Mobile", "snr": 9.5, "role": "companion"},
            {"node_id": "!c0ffee01", "alias": "M7NCY-Portable", "snr": 12.0, "role": "companion"},
            {"node_id": "!c0ffee06", "alias": "Surrey-Rover", "snr": 6.2, "role": "companion"}
        ]
    }
    map_w.run_js(f"setCompanionOrbitalsVisible(true, {json.dumps(docked_data)}); void 0;")
    if hasattr(map_w, "watcher_status"):
        map_w.watcher_status.setText("🛰️ <b>Companion Orbitals:</b> Tactical tracking active for BoxHill-Rpt (3 docked nodes in range rings)")
    wait_events(app, 1.5)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "04_map_companion_orbitals_zoomed.png"))
    map_w.set_orbitals(False)

    # -------------------------------------------------------------
    # 5. Multi-Hop Packet Route Tracing
    # -------------------------------------------------------------
    print("Capturing 05_map_multihop_route_tracing.png...")
    # Center map to frame Richmond -> Box Hill -> Crystal Palace -> London Base
    map_w.run_js("map.setView([51.38, -0.18], 11); void 0;")
    wait_events(app, 1.0)
    map_w.set_rf_links(True)

    trace_msg = MessageEnvelope(
        id="pkt-trace-101",
        channel="Public",
        sender_id="!c0ffee05",
        sender_name="G4XYZ-Mobile",
        recipient_id="!c0ffee01",
        recipient_name="M7NCY-Base (London)",
        text="Checking packet route via Box Hill and Crystal Palace repeaters into central station!",
        metadata={
            "repeaters": ["!c0ffee02", "!c0ffee03"],
            "hop_nodes": ["!c0ffee02", "!c0ffee03"],
            "hop_snrs": [9.0, 11.2]
        }
    )
    map_w.visualise_message_path(trace_msg)
    if hasattr(map_w, "watcher_status"):
        map_w.watcher_status.setText("⚡ <b>Packet Tracer:</b> Multi-hop path visualised: @G4XYZ-Mobile ➔ BoxHill-Rpt (+9.0dB) ➔ CrystalPalace-Rpt (+11.2dB) ➔ M7NCY-Base (+12.5dB)")
    wait_events(app, 2.0)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "05_map_multihop_route_tracing.png"))

    # -------------------------------------------------------------
    # 6. Regional Scopes Overlay with Live UK/Ireland Scope Hulls
    # -------------------------------------------------------------
    print("Capturing 06_map_uk_scopes_overlay.png...")
    live_db = Path.home() / ".config" / "meshcore-tray" / "meshcore_tray.db"
    if live_db.exists():
        scope_storage = Storage(str(live_db))
        map_w.storage = scope_storage
    map_w.run_js("map.setView([54.5, -3.8], 7); map.invalidateSize(true); void 0;")
    wait_events(app, 3.5)
    map_w.set_scopes(True)
    if hasattr(map_w, "watcher_status"):
        scopes_dict = map_w.storage.get_repeaters_by_scope()
        active_count = sum(len(sc["nodes"]) for sc in scopes_dict.values())
        map_w.watcher_status.setText(f"🌐 <b>Regional Scopes:</b> {active_count} repeaters mapped across 7 live scopes (#gb-cum, #gb-nwk, #cax, #gb-nth, #sco, #iom, #ioi)")
    wait_events(app, 3.0)
    pix = map_w.grab()
    pix.save(str(OUTPUT_DIR / "06_map_uk_scopes_overlay.png"))
    map_w.set_scopes(False)

    map_w.close()
    wait_events(app, 0.5)

    print("\nAll map screenshots successfully generated in:")
    print(str(OUTPUT_DIR))
    for f in sorted(OUTPUT_DIR.glob("0*.png")):
        print(f" - {f.name} ({f.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    generate_all_map_screenshots()
