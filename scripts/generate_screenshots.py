#!/usr/bin/env python3
"""Automated High-Resolution Screenshot Generator for MESHCORE NAVIGATOR.

Generates crisp, professional screenshots of all core views with mock
mesh network data for documentation and the README.
"""

import os
import sys
import time
from pathlib import Path
from PIL import Image

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
from meshcore_tray.core.version_checker import ReleaseInfo
from meshcore_tray.pixoo.pixoo_renderer import PixooRenderer
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.settings_widget import SettingsWidget


OUTPUT_DIR = PROJECT_ROOT / "docs" / "assets" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SCRATCH_DIR = PROJECT_ROOT / "scratch"
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
DEMO_DB = SCRATCH_DIR / "demo_screenshots.db"


def wait_events(app: QApplication, duration_seconds: float = 3.5):
    """Pumps the Qt event loop for the requested duration to allow WebEngine and animations to settle."""
    start = time.time()
    while time.time() - start < duration_seconds:
        app.processEvents()
        time.sleep(0.02)


def init_demo_database() -> Storage:
    """Creates an isolated demo database populated with realistic mesh network data."""
    if DEMO_DB.exists():
        try:
            DEMO_DB.unlink()
        except Exception:
            pass

    storage = Storage(DEMO_DB)

    # 1. Channels
    storage.save_channel(ChannelInfo(channel_id=0, name="Public", is_favorite=True))
    storage.save_channel(ChannelInfo(channel_id=1, name="Emergency", is_favorite=True))
    storage.save_channel(ChannelInfo(channel_id=2, name="Tech-Talk"))
    storage.save_channel(ChannelInfo(channel_id=3, name="Surrey-Mesh"))

    # 2. Contacts (London, Surrey, Kent nodes)
    contacts = [
        NodeContact(
            node_id="!c0ffee01",
            alias="M7NCY-Base (Local)",
            is_favorite=True,
            latitude=51.5074,
            longitude=-0.1278,
            snr_db=12.5,
            rssi_dbm=-72.0,
            is_repeater=False
        ),
        NodeContact(
            node_id="!c0ffee02",
            alias="BoxHill-Rpt [RPT]",
            is_favorite=True,
            latitude=51.2560,
            longitude=-0.3060,
            snr_db=9.0,
            rssi_dbm=-86.0,
            is_repeater=True
        ),
        NodeContact(
            node_id="!c0ffee03",
            alias="CrystalPalace-Rpt [RPT]",
            is_favorite=True,
            latitude=51.4240,
            longitude=-0.0710,
            snr_db=11.2,
            rssi_dbm=-80.0,
            is_repeater=True
        ),
        NodeContact(
            node_id="!c0ffee04",
            alias="London-Hub [ROOM]",
            is_favorite=True,
            latitude=51.5150,
            longitude=-0.0900,
            snr_db=8.5,
            rssi_dbm=-89.0,
            is_room_server=True
        ),
        NodeContact(
            node_id="!c0ffee05",
            alias="G4XYZ-Mobile",
            is_favorite=False,
            latitude=51.4800,
            longitude=-0.2200,
            snr_db=6.0,
            rssi_dbm=-98.0,
            is_repeater=False
        ),
        NodeContact(
            node_id="!c0ffee06",
            alias="SurreyHills-Node",
            is_favorite=False,
            latitude=51.2000,
            longitude=-0.4000,
            snr_db=5.2,
            rssi_dbm=-102.0,
            is_repeater=False
        ),
        NodeContact(
            node_id="!c0ffee07",
            alias="KentGateway [RPT]",
            is_favorite=False,
            latitude=51.2800,
            longitude=0.5200,
            snr_db=7.1,
            rssi_dbm=-95.0,
            is_repeater=True
        ),
    ]
    for c in contacts:
        storage.save_contact(c)

    # 3. Neighbors
    neighbours = [
        NeighbourInfo(node_id="!c0ffee03", alias="CrystalPalace-Rpt", snr_db=11.2, rssi_dbm=-80.0, is_repeater=True, latitude=51.4240, longitude=-0.0710),
        NeighbourInfo(node_id="!c0ffee02", alias="BoxHill-Rpt", snr_db=9.0, rssi_dbm=-86.0, is_repeater=True, latitude=51.2560, longitude=-0.3060),
        NeighbourInfo(node_id="!c0ffee04", alias="London-Hub [ROOM]", snr_db=8.5, rssi_dbm=-89.0, is_room_server=True, latitude=51.5150, longitude=-0.0900),
        NeighbourInfo(node_id="!c0ffee05", alias="G4XYZ-Mobile", snr_db=6.0, rssi_dbm=-98.0, is_repeater=False, latitude=51.4800, longitude=-0.2200),
    ]
    for n in neighbours:
        storage.save_neighbour(n)

    # 4. Multi-hop Packet Path
    path = PacketPathInfo(
        packet_id="pkt-901",
        sender_id="!c0ffee05",
        sender_name="G4XYZ-Mobile",
        recipient_id="!c0ffee01",
        hop_nodes=["G4XYZ-Mobile", "BoxHill-Rpt", "CrystalPalace-Rpt", "M7NCY-Base"],
        hop_snrs=[6.0, 9.0, 11.2, 12.5],
        route_type="FLOOD",
        coordinates=[
            [51.4800, -0.2200],
            [51.2560, -0.3060],
            [51.4240, -0.0710],
            [51.5074, -0.1278],
        ]
    )
    storage.save_packet_path(path)

    # 5. Messages
    messages = [
        MessageEnvelope(id="msg-01", channel="Public", sender_id="!c0ffee02", sender_name="BoxHill-Rpt", text="⚡ Repeater BoxHill beacon active on 869.618MHz UK Narrow. Solar battery: 96%. All hops nominal."),
        MessageEnvelope(id="msg-02", channel="Public", sender_id="!c0ffee05", sender_name="G4XYZ-Mobile", text="Good afternoon all! Checking signal into Crystal Palace from Richmond park."),
        MessageEnvelope(id="msg-03", channel="Public", sender_id="!c0ffee03", sender_name="CrystalPalace-Rpt", text="@G4XYZ-Mobile Copy 5/9, SNR +11.2dB through 1 hop via Box Hill!"),
        MessageEnvelope(id="msg-04", channel="Public", sender_id="!c0ffee01", sender_name="M7NCY-Base", text="Monitoring UK Narrow channel. Tropospheric ducting index is elevated over SE England today! 📡", is_outgoing=True),
    ]
    for m in messages:
        storage.save_message(m)

    return storage


def generate_all_screenshots():
    """Generates and saves the suite of app screenshots."""
    print("Initializing demo dataset...")
    storage = init_demo_database()

    app = QApplication.instance() or QApplication(sys.argv)

    cfg = AppConfig()
    cfg.window_width = 1380
    cfg.window_height = 800

    # -------------------------------------------------------------
    # 1. Startup Splash Screen with Version Checker Notifier
    # -------------------------------------------------------------
    print("Capturing 06_startup_splash_updater.png...")
    cfg_splash = AppConfig()
    cfg_splash.show_splash_screen = True
    win_splash = MainWindow(config=cfg_splash, storage=storage)
    win_splash.show()
    wait_events(app, 1.5)
    if hasattr(win_splash, "splash_overlay") and win_splash.splash_overlay:
        demo_rel = ReleaseInfo(
            tag_name="v0.6.0",
            version="0.6.0",
            name="MESHCORE NAVIGATOR v0.6.0",
            html_url="https://github.com/SoulwayStudios/meshcore-navigator/releases/tag/v0.6.0",
            published_at="2026-09-13T16:30:00Z",
            body="Startup version checker, splash release notifier, and map resize crash hardening."
        )
        win_splash.splash_overlay._on_version_check_finished(True, demo_rel, "")
        wait_events(app, 0.5)
    pix_splash = win_splash.grab()
    pix_splash.save(str(OUTPUT_DIR / "06_startup_splash_updater.png"))
    win_splash.close()
    wait_events(app, 0.5)

    # -------------------------------------------------------------
    # 2. Main Window with Interactive Mesh Map & Topology
    # -------------------------------------------------------------
    print("Capturing 01_interactive_mesh_map.png...")
    cfg_main = AppConfig()
    cfg_main.show_splash_screen = False
    cfg_main.pixoo.show_live_mirror = False
    win = MainWindow(config=cfg_main, storage=storage)
    if hasattr(win, "splash_overlay") and win.splash_overlay:
        win.splash_overlay.dismiss(immediate=True)
    win.show()

    # Allow Leaflet WebEngine map to fully load and render tiles
    wait_events(app, 4.0)

    # Enable RF links and map markers
    if hasattr(win, "mesh_map"):
        win.mesh_map.set_rf_links(True)
        wait_events(app, 0.8)

    pix_main = win.grab()
    pix_main.save(str(OUTPUT_DIR / "01_interactive_mesh_map.png"))

    # -------------------------------------------------------------
    # 3. Pixoo 64 Live Mirror Preview
    # -------------------------------------------------------------
    print("Capturing 04_pixoo_64_preview.png...")
    win.pixoo_panel.setVisible(True)
    renderer = PixooRenderer(config=cfg_main)
    alert_msg = MessageEnvelope(
        id="p-alert",
        channel="Public",
        sender_name="M7NCY",
        text="LORA MESH ONLINE"
    )
    renderer.trigger_message_alert(alert_msg)
    pixoo_frame = renderer.render_frame()
    win.pixoo_panel.canvas.update_frame(pixoo_frame)
    win.main_splitter.setSizes([180, 380, 520, 300])
    wait_events(app, 0.8)
    pix_pixoo = win.grab()
    pix_pixoo.save(str(OUTPUT_DIR / "04_pixoo_64_preview.png"))
    win.pixoo_panel.setVisible(False)

    # -------------------------------------------------------------
    # 4. Repeater Console & Interactive Terminal
    # -------------------------------------------------------------
    print("Capturing 02_repeater_console.png...")
    boxhill_contact = storage.get_contact("!c0ffee02")
    if boxhill_contact:
        win.repeater_console.set_repeater(boxhill_contact)
        win.repeater_console.telemetry_badge.setText("SNR: +9.0 dB • RSSI: -86 dBm • Batt: 96% (4.12V)")
        win.repeater_console.term_box.clear()
        win.repeater_console._log_system("Connected to BoxHill-Rpt via USB Serial @ 115200 baud.")
        win.repeater_console._log_system("Authenticated session established [Role: Admin].")
        win.repeater_console._log_system("TX > !status")
        win.repeater_console._log_system("RX < BoxHill-Rpt: Uptime: 18d 04h 12m | Solar Batt: 4.12V (96%) | Noise Floor: -118dBm | Packets RX: 2,841, TX: 619")
        win.repeater_console._log_system("TX > !neighbors")
        win.repeater_console._log_system("RX < BoxHill-Rpt: Active Links: CrystalPalace-Rpt (+11.2dB), M7NCY-Base (+12.5dB), SurreyHills-Node (+5.2dB)")
    win.center_stack.setCurrentIndex(1)  # Index 1: Repeater Console
    win.main_splitter.setSizes([140, 720, 520, 0])
    wait_events(app, 0.8)
    pix_repeater = win.grab()
    pix_repeater.save(str(OUTPUT_DIR / "02_repeater_console.png"))
    win.center_stack.setCurrentIndex(0)  # Reset to Chat

    # -------------------------------------------------------------
    # 5. Room Servers & Mesh Bulletin Boards
    # -------------------------------------------------------------
    print("Capturing 03_room_servers_bbs.png...")
    win.nav_dock.switch_view("rooms")
    room_contact = storage.get_contact("!c0ffee04")
    if room_contact and hasattr(win, "rooms_view"):
        win.rooms_view.reload_rooms()
        win.rooms_view.set_active_room(room_contact)
        room_msg = MessageEnvelope(
            id="bbs-1",
            channel="Public",
            sender_id="!c0ffee04",
            sender_name="London-Hub [ROOM]",
            text="📢 **Welcome to London-Hub Mesh Bulletin Board**\nCommands available: !read, !post <txt>, !info, !list\nActive LoRa Nodes: 14 | Uptime: 42 days\nNext London LoRa Mesh Meetup: Saturday 14:00 BST"
        )
        win.rooms_view._on_bus_message(room_msg)
    wait_events(app, 0.8)
    pix_rooms = win.grab()
    pix_rooms.save(str(OUTPUT_DIR / "03_room_servers_bbs.png"))

    win.close()
    wait_events(app, 0.5)

    # -------------------------------------------------------------
    # 6. Settings Suite & Radio Presets
    # -------------------------------------------------------------
    print("Capturing 05_hardware_and_radio_presets.png...")
    sw = SettingsWidget(config=cfg_main, storage=storage)
    sw.resize(1100, 750)
    sw.show()
    wait_events(app, 0.8)
    pix_settings = sw.grab()
    pix_settings.save(str(OUTPUT_DIR / "05_hardware_and_radio_presets.png"))
    sw.close()
    wait_events(app, 0.5)

    print("\nAll screenshots successfully generated in:")
    print(str(OUTPUT_DIR))
    for f in sorted(OUTPUT_DIR.glob("*.png")):
        print(f" - {f.name} ({f.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    generate_all_screenshots()
