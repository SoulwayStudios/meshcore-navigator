"""Pixoo 64 Background Service & Animation Engine."""

import asyncio
import logging
import threading
import time
from typing import Optional
from PIL import Image

import requests
from meshcore_tray.core.models import MessageEnvelope, NeighbourInfo, TelemetryEnvelope
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.pixoo.pixoo_renderer import PixooRenderer

logger = logging.getLogger("meshcore_tray.pixoo_service")


class PixooService:
    """Manages the rendering loop, rotation timers, and HTTP push client for Pixoo 64."""

    def __init__(self, config=None):
        self.config = config
        self.renderer = PixooRenderer(config=config)
        self._running = False
        self._render_task: Optional[asyncio.Task] = None
        self._last_telemetry_trigger: float = time.time()
        self._last_neighbours_trigger: float = time.time()
        self._http_push_thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[Image.Image] = None
        self._push_lock = threading.Lock()

        # Connect event bus subscriptions
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_message_received)
        bus.subscribe(EventType.TELEMETRY_UPDATED, self._on_telemetry_updated)
        bus.subscribe(EventType.NEIGHBOURS_UPDATED, self._on_neighbours_updated)
        bus.subscribe(EventType.SETTINGS_UPDATED, self._on_settings_updated)

    def set_config(self, config):
        self.config = config
        self.renderer.set_config(config)

    def _on_message_received(self, msg: MessageEnvelope):
        self.renderer.trigger_message_alert(msg)

    def _on_telemetry_updated(self, telem: TelemetryEnvelope):
        self.renderer.latest_telemetry = telem

    def _on_neighbours_updated(self, neighbours: list[NeighbourInfo]):
        self.renderer.latest_neighbours = neighbours

    def _on_settings_updated(self, config):
        self.set_config(config)

    async def start(self):
        self._running = True
        logger.info("Starting Pixoo Animation & Display Service...")
        # Start background HTTP pusher
        self._http_push_thread = threading.Thread(target=self._http_push_loop, daemon=True)
        self._http_push_thread.start()

        # Start animation frame loop at ~25 FPS (40ms interval)
        while self._running:
            try:
                now = time.time()
                self._check_periodic_rotations(now)

                # Render current matrix frame
                frame = self.renderer.render_frame()
                with self._push_lock:
                    self._latest_frame = frame

                # Emit frame to Qt virtual preview canvas
                bus.emit(EventType.PIXOO_FRAME_READY, frame)

                await asyncio.sleep(0.04)  # ~25 FPS
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Pixoo rendering loop: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def stop(self):
        self._running = False
        logger.info("Stopped Pixoo Service.")

    def _check_periodic_rotations(self, now: float):
        """Checks if it's time to rotate to Telemetry or Neighbours screen."""
        if not self.config:
            return

        # Do not interrupt active message alert
        if self.renderer.mode == "alert":
            return

        # Check Telemetry rotation
        if self.config.telemetry.enabled:
            interval_sec = self.config.telemetry.interval_mins * 60.0
            if (now - self._last_telemetry_trigger) >= interval_sec:
                self._last_telemetry_trigger = now
                self.renderer.trigger_telemetry()
                return

        # Check Neighbours rotation
        if self.config.neighbours.enabled:
            interval_sec = self.config.neighbours.interval_mins * 60.0
            if (now - self._last_neighbours_trigger) >= interval_sec:
                self._last_neighbours_trigger = now
                self.renderer.trigger_neighbours()
                return

    def _http_push_loop(self):
        """Pushes raw pixel buffers to physical Pixoo 64 device over LAN HTTP API."""
        last_push_time = 0.0
        push_interval = 0.25  # Limit HTTP pushes to ~4 FPS to avoid overloading Pixoo WiFi

        while self._running:
            now = time.time()
            if (now - last_push_time) >= push_interval:
                last_push_time = now
                if self.config and self.config.pixoo.ip_address:
                    ip = self.config.pixoo.ip_address.strip()
                    if ip and ip != "192.168.1.150":  # If user configured a real IP
                        with self._push_lock:
                            frame = self._latest_frame

                        if frame:
                            self._send_frame_to_device(ip, frame)

            time.sleep(0.05)

    def _send_frame_to_device(self, ip: str, frame: Image.Image):
        """Encodes frame into Pixoo 64 HTTP payload."""
        try:
            # Pixoo 64 HTTP Push API (Draw.SendHttpGif or Draw.SendHttpRawGif)
            url = f"http://{ip}/post"
            # Extract raw RGB values
            raw_rgb = []
            for y in range(64):
                for x in range(64):
                    r, g, b = frame.getpixel((x, y))[:3]
                    raw_rgb.extend([r, g, b])

            # Convert to hex string
            payload = {
                "Command": "Draw/SendHttpRawGif",
                "PicID": 1,
                "PicSpeed": 1000,
                "PicWidth": 64,
                "PicOffset": 0,
                "PicData": bytes(raw_rgb).hex()
            }
            requests.post(url, json=payload, timeout=0.8)
        except Exception as e:
            # Silence expected offline connection timeouts
            pass

    def test_alert(self):
        """Helper to simulate an alert immediately."""
        test_msg = MessageEnvelope(
            id=f"test-alert-{int(time.time())}",
            sender_name="Alice",
            channel="Public",
            text="Testing Pixoo 64 3-row green flashing alert and vertical message scrolling text!",
            is_favorite=True
        )
        self.renderer.trigger_message_alert(test_msg)

    def test_telemetry(self):
        """Helper to show Telemetry screen immediately."""
        self.renderer.trigger_telemetry()

    def test_neighbours(self):
        """Helper to show Neighbours screen immediately."""
        self.renderer.trigger_neighbours()
