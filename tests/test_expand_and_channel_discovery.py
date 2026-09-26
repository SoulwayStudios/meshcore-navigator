"""Tests for Expand Map Button, Overlay Minimise & Hover Flyout, and Unified RF/MQTT Channel Join."""

import hashlib
import hmac
import struct
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope, ChannelInfo
from meshcore_tray.core.packet_decoder import (
    COMMUNITY_CHANNELS,
    build_known_channel_keys,
    derive_channel_key,
    decode_meshcore_packet,
    PacketDecoder,
)
from meshcore_tray.core.mqtt_service import MqttService
from meshcore_tray.storage import Storage
from meshcore_tray.ui.nav_dock import MapLayerDockWidget, ExpandMapButton, LayerButton
from meshcore_tray.ui.main_window import MainWindow

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def tmp_storage(tmp_path):
    db_file = tmp_path / "test_meshcore.db"
    return Storage(db_path=str(db_file))


def test_expand_map_button(qapp):
    """Verifies ExpandMapButton toggle states and styling."""
    btn = ExpandMapButton()
    assert not btn.is_expanded
    assert "Expand Map" in btn.toolTip()

    # Trigger click
    events = []
    btn.expand_toggled.connect(events.append)
    btn.click()
    assert btn.is_expanded
    assert len(events) == 1
    assert events[0] is True
    assert "Restore" in btn.toolTip()

    # Programmatic set_expanded
    btn.set_expanded(False)
    assert not btn.is_expanded
    assert "Expand Map" in btn.toolTip()


def test_map_layer_dock_expand_and_hover_signals(qapp):
    """Verifies that MapLayerDockWidget places btn_expand_map at index 0 and wires hover signals."""
    dock = MapLayerDockWidget(config=AppConfig())
    
    # Check index 0 in layout is btn_expand_map
    layout = dock.layout()
    assert layout.count() > 0
    item0 = layout.itemAt(0).widget()
    assert isinstance(item0, ExpandMapButton)
    assert item0 is dock.btn_expand_map

    # Test expand signal forwarding
    expand_events = []
    dock.expand_map_toggled.connect(expand_events.append)
    dock.btn_expand_map.click()
    assert len(expand_events) == 1
    assert expand_events[0] is True

    # Test layer hover signal forwarding
    hover_events = []
    dock.layer_hovered.connect(lambda k, h, y: hover_events.append((k, h, y)))
    dock.btn_adsb.hover_changed.emit("adsb", True, 120)
    assert len(hover_events) == 1
    assert hover_events[0] == ("adsb", True, 120)

    dock.btn_adsb.hover_changed.emit("adsb", False, 120)
    assert len(hover_events) == 2
    assert hover_events[1] == ("adsb", False, 120)


def test_community_channels_and_key_derivation():
    """Verifies community channel definitions and deterministic key derivation."""
    assert "thenorf" in COMMUNITY_CHANNELS
    assert "northeast" in COMMUNITY_CHANNELS
    assert "cumbria" in COMMUNITY_CHANNELS

    # Hash verification matching MeshCore spec
    k_thenorf = hashlib.sha256(b"thenorf").digest()[:16]
    h_thenorf = hashlib.sha256(k_thenorf).digest()[0]
    assert h_thenorf == 0xAA

    k_northeast = hashlib.sha256(b"northeast").digest()[:16]
    h_northeast = hashlib.sha256(k_northeast).digest()[0]
    assert h_northeast == 0xF0

    keys = build_known_channel_keys()
    assert "Public" in keys
    assert "#thenorf" in keys
    assert "thenorf" in keys
    assert "#northeast" in keys
    assert "northeast" in keys


def test_channel_decryption_with_known_keys():
    """Verifies that GRP_TXT packets encrypted for #thenorf and #northeast decrypt accurately."""
    if not HAS_CRYPTO:
        pytest.skip("PyCryptodome not installed")

    chan_key = hashlib.sha256(b"northeast").digest()[:16]
    chan_hash = hashlib.sha256(chan_key).digest()[0]  # 0xF0

    # Form MeshCore message payload
    ts = 1727380000
    flags = 0
    plaintext = b"M7NCY: Hello North East Mesh!"
    pt = struct.pack("<IB", ts, flags) + plaintext
    pad_len = 16 - (len(pt) % 16)
    if pad_len < 16:
        pt += b"\x00" * pad_len

    cipher = AES.new(chan_key, AES.MODE_ECB)
    ct = cipher.decrypt(pt)  # In reverse, encrypt
    cipher_enc = AES.new(chan_key, AES.MODE_ECB)
    ct = cipher_enc.encrypt(pt)

    secret = chan_key + (b"\x00" * 16)
    mac = hmac.new(secret, ct, hashlib.sha256).digest()[:2]

    # Construct wire packet: Route FLOOD (1), GRP_TXT (5), PV (0) -> Header 0x15
    # Path: 0 hops (path byte 0x00)
    # Payload: chan_hash (1B) + mac (2B) + ct
    header_b = 0x15
    path_b = 0x00
    raw_packet = bytes([header_b, path_b, chan_hash]) + mac + ct
    raw_hex = raw_packet.hex().upper()

    # Decode with known keys
    all_keys = build_known_channel_keys()
    pkt = decode_meshcore_packet(raw_hex, channel_keys=all_keys)

    assert pkt is not None
    assert pkt.header.payload_type_name == "GRP_TXT"
    assert pkt.payload.decryption_status == "DECRYPTED"
    assert pkt.payload.channel in ("#northeast", "northeast")
    assert pkt.payload.sender == "M7NCY"
    assert "Hello North East Mesh!" in pkt.payload.text


def test_unjoined_channel_activity_detection(qapp, tmp_storage):
    """Verifies that traffic on unjoined channels emits CHANNEL_ACTIVITY_DETECTED and is saved to storage."""
    if not HAS_CRYPTO:
        pytest.skip("PyCryptodome not installed")

    cfg = AppConfig()
    mqtt_svc = MqttService(config=cfg, storage=tmp_storage)

    detected_events = []
    received_events = []
    bus.subscribe(EventType.CHANNEL_ACTIVITY_DETECTED, detected_events.append)
    bus.subscribe(EventType.MESSAGE_RECEIVED, received_events.append)

    # User has only Public channel initially
    assert tmp_storage.get_channel("#thenorf") is None

    # Simulate incoming JSON or hex MQTT message for #thenorf
    fake_json_payload = (
        b'{"raw":"1500AA11223344", "channel":"thenorf", "text":"Testing The Norf traffic", "sender":"M0ABC"}'
    )
    mqtt_svc._process_payload("meshcore/channel/thenorf", fake_json_payload)

    # Since user is NOT in #thenorf, CHANNEL_ACTIVITY_DETECTED must be emitted
    assert len(detected_events) == 1
    evt = detected_events[0]
    assert evt["channel"] == "#thenorf"
    assert evt["sender"] == "M0ABC"
    assert evt["text"] == "Testing The Norf traffic"
    assert evt["source"] == "mqtt"

    # Pre-cached in SQLite backlog
    saved_msgs = tmp_storage.get_messages("#thenorf")
    assert len(saved_msgs) >= 1
    assert saved_msgs[0].text == "Testing The Norf traffic"

    # Now simulate user joining #thenorf
    tmp_storage.save_channel(ChannelInfo(channel_id=2, name="#thenorf"))

    # Next message on #thenorf should go directly to MESSAGE_RECEIVED
    fake_json_2 = (
        b'{"raw":"1500AA11223345", "channel":"thenorf", "text":"Second message on joined channel", "sender":"M0ABC"}'
    )
    mqtt_svc._process_payload("meshcore/channel/thenorf", fake_json_2)
    assert len(received_events) >= 1
    assert received_events[-1].text == "Second message on joined channel"


def test_main_window_expand_map_and_channel_banner(qapp, tmp_storage):
    """Verifies MainWindow toggle_expand_map splitter behavior and channel activity prompt banner."""
    cfg = AppConfig()
    win = MainWindow(config=cfg, storage=tmp_storage)

    # Verify dock index in normal mode
    assert win.mesh_map.map_content_layout.indexOf(win.map_layer_dock) == 0

    # 1. Expand Map
    win.toggle_expand_map(True)
    assert win._map_is_expanded is True
    assert win.sidebar.isHidden()
    assert win.center_stack.isHidden()
    # In expanded mode, dock moves to far right (after map_splitter)
    assert win.mesh_map.map_content_layout.indexOf(win.map_layer_dock) == 1

    # 2. Restore Map
    win.toggle_expand_map(False)
    assert win._map_is_expanded is False
    assert not win.sidebar.isHidden()
    assert not win.center_stack.isHidden()
    # In restored mode, dock returns to left edge of map (index 0)
    assert win.mesh_map.map_content_layout.indexOf(win.map_layer_dock) == 0

    # 3. Test Channel Activity Prompt Banner
    assert win.channel_activity_banner.isHidden()

    bus.emit(EventType.CHANNEL_ACTIVITY_DETECTED, {
        "channel": "#northeast",
        "sender": "G7XYZ",
        "text": "Are repeaters online?",
        "source": "mqtt",
        "timestamp": "2026-09-26T18:00:00Z"
    })

    assert not win.channel_activity_banner.isHidden()
    assert "#northeast" in win.channel_activity_lbl.text()
    assert "+ Join #northeast" == win.channel_activity_join_btn.text()

    # Click join button
    win.channel_activity_join_btn.click()
    assert win.channel_activity_banner.isHidden()

    # Channel #northeast should now be saved in storage and active
    ch = tmp_storage.get_channel("#northeast")
    assert ch is not None
    assert ch.name == "#northeast"


def test_overlay_minimise_and_hover_flyout_js(qapp):
    """Verifies that overlay minimising, hover flyout (left/right dock positions), and pinning work in JS without errors."""
    from PyQt6.QtCore import QEventLoop, QTimer
    from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
    
    cfg = AppConfig()
    map_w = MeshMapWidget(config=cfg)
    map_w.show()
    
    loop = QEventLoop()
    map_w.web_view.loadFinished.connect(lambda ok: loop.quit())
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    
    # 1. Test minimiseOverlay on adsb panel (has nested flex container for close button)
    res_minimize = []
    map_w.run_js("""
    window.minimizeOverlay('adsb');
    Boolean(window._minimisedOverlays['adsb']);
    """, res_minimize.append)
    
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert res_minimize and res_minimize[0] is True
    
    # 2. Test hover flyout when dock is on left (normal mode) -> flyout to the right (left: 8px)
    map_w.on_layer_hovered('adsb', True, 200)
    res_hover_left = []
    map_w.run_js("""
    var el = document.getElementById('adsb-panel');
    var pin = el.querySelector('.map-overlay-pin-btn');
    JSON.stringify({
        display: el.style.display,
        left: el.style.left,
        right: el.style.right,
        hasPin: !!pin
    });
    """, res_hover_left.append)
    
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert res_hover_left
    import json
    data_left = json.loads(res_hover_left[0])
    assert data_left["display"] == "flex"
    assert data_left["left"] == "8px"
    assert data_left["hasPin"] is True
    
    # 3. Test dock expanded mode (dock on right) -> flyout to the left (right: 8px)
    map_w.set_layer_dock_expanded(True)
    map_w.on_layer_hovered('adsb', True, 200)
    res_hover_right = []
    map_w.run_js("""
    var el = document.getElementById('adsb-panel');
    JSON.stringify({
        display: el.style.display,
        left: el.style.left,
        right: el.style.right,
        hasRightClass: el.classList.contains('flyout-from-right')
    });
    """, res_hover_right.append)
    
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert res_hover_right
    data_right = json.loads(res_hover_right[0])
    assert data_right["display"] == "flex"
    assert data_right["right"] == "8px"
    assert data_right["hasRightClass"] is True
    
    # 4. Test pinOverlay restores panel to pinned state
    res_pin = []
    map_w.run_js("""
    window.pinOverlay('adsb');
    var el = document.getElementById('adsb-panel');
    var pin = el.querySelector('.map-overlay-pin-btn');
    JSON.stringify({
        display: el.style.display,
        minimised: Boolean(window._minimisedOverlays['adsb']),
        pinHidden: pin ? (pin.style.display === 'none') : false
    });
    """, res_pin.append)
    
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert res_pin
    data_pin = json.loads(res_pin[0])
    assert data_pin["display"] == "flex"
    assert data_pin["minimised"] is False
    assert data_pin["pinHidden"] is True

