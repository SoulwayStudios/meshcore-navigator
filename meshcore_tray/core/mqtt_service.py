"""MQTT Ingestor & Gateway Service for MeshCore Navigator.

Connects to local or community MQTT brokers (e.g. CoreScope, meshcoretomqtt, IPNet, Lincomatic),
subscribes to packet feeds, decodes raw packet bytes using PacketDecoder, deduplicates
against local radio traffic, and dispatches live traces and messages into the EventBus.
"""

from collections import OrderedDict
from datetime import datetime, timezone
import json
import logging
import ssl
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO_MQTT = True
except ImportError:
    HAS_PAHO_MQTT = False

from meshcore_tray.config import AppConfig, MqttConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import PacketPathInfo, MessageEnvelope
from meshcore_tray.core.packet_decoder import PacketDecoder, DecodedPacket
from meshcore_tray.core.deduplicator import get_deduplicator
from meshcore_tray.storage import Storage


logger = logging.getLogger("meshcore_tray.mqtt_service")


class MqttService:
    """Background MQTT client managing broker connection, subscription, and packet ingest."""

    def __init__(self, config: AppConfig, storage: Optional[Storage] = None):
        self.config = config
        self.storage = storage
        self.client: Optional[Any] = None
        self._connected = False
        self._running = False
        self._lock = threading.Lock()

        # Packet Deduplication Cache: raw_hex -> timestamp
        self._dedup_cache: OrderedDict[str, float] = OrderedDict()
        self._max_dedup_cache = 1000

        # Dedicated packet decoder
        self.decoder = PacketDecoder()

        # Subscribe to outgoing radio packet events for gateway forwarding
        bus.subscribe(EventType.PACKET_PATH_TRACED, self._on_local_packet_traced)

    @property
    def mqtt_config(self) -> MqttConfig:
        return self.config.mqtt

    def is_available(self) -> bool:
        """Returns True if paho-mqtt dependency is available in the environment."""
        return HAS_PAHO_MQTT

    def is_connected(self) -> bool:
        """Returns True if currently connected to an MQTT broker."""
        return self._connected

    def start(self):
        """Starts the MQTT ingestor client if enabled in configuration."""
        if not HAS_PAHO_MQTT:
            logger.warning("paho-mqtt is not installed; MQTT broker service disabled.")
            return

        cfg = self.mqtt_config
        if not cfg.enabled:
            logger.debug("MQTT service is disabled in configuration.")
            return

        with self._lock:
            if self._running:
                return
            self._running = True

        try:
            # Paho-MQTT 2.0 vs 1.x compatibility
            if hasattr(mqtt, "CallbackAPIVersion"):
                self.client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=cfg.client_id or f"meshcore-nav-{int(time.time())}"
                )
            else:
                self.client = mqtt.Client(
                    client_id=cfg.client_id or f"meshcore-nav-{int(time.time())}"
                )

            # Authentication
            if cfg.username:
                self.client.username_pw_set(cfg.username, cfg.password or None)

            # TLS
            if cfg.use_tls:
                self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

            # Wire Callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            logger.info("Connecting to MQTT broker %s:%d...", cfg.broker_host, cfg.broker_port)
            self.client.connect_async(cfg.broker_host, cfg.broker_port, keepalive=60)
            self.client.loop_start()

        except Exception as e:
            logger.error("Failed to start MQTT service: %s", e)
            with self._lock:
                self._running = False
                self._connected = False

    def stop(self):
        """Stops the MQTT client and disconnects from the broker."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._connected = False

        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception as e:
                logger.debug("Error disconnecting MQTT client: %s", e)
            finally:
                self.client = None
        logger.info("MQTT service stopped.")

    def restart(self):
        """Restarts the MQTT client with updated configuration settings."""
        self.stop()
        if self.mqtt_config.enabled:
            self.start()

    def _on_connect(self, client, userdata, flags, rc, *args):
        """Paho on_connect callback."""
        # Handle both v1 (rc is int) and v2 (rc is ReasonCode object)
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            self._connected = True
            logger.info("Successfully connected to MQTT broker.")
            # Subscribe to configured topics
            topics = self.mqtt_config.subscribe_topics or ["meshcore/#"]
            for t in topics:
                if t.strip():
                    client.subscribe(t.strip())
                    logger.debug("Subscribed to MQTT topic: %s", t.strip())
        else:
            self._connected = False
            logger.warning("Failed to connect to MQTT broker, return code: %s", rc)

    def _on_disconnect(self, client, userdata, *args):
        """Paho on_disconnect callback."""
        self._connected = False
        logger.debug("Disconnected from MQTT broker.")

    def _is_duplicate(self, raw_hex: str) -> bool:
        """Checks and records packet hex in rolling deduplication cache."""
        if not raw_hex:
            return False
        clean_hex = raw_hex.upper().strip()
        now = time.time()
        window = getattr(self.mqtt_config, "dedup_window_secs", 5.0) or 5.0

        with self._lock:
            # Purge expired entries
            cutoff = now - window
            while self._dedup_cache:
                first_key, first_time = next(iter(self._dedup_cache.items()))
                if first_time < cutoff:
                    self._dedup_cache.popitem(last=False)
                else:
                    break

            if clean_hex in self._dedup_cache:
                return True

            self._dedup_cache[clean_hex] = now
            if len(self._dedup_cache) > self._max_dedup_cache:
                self._dedup_cache.popitem(last=False)
            return False

    def _on_message(self, client, userdata, msg):
        """Dispatches incoming MQTT messages to packet decoder and EventBus."""
        try:
            topic = msg.topic
            payload = msg.payload
            self._process_payload(topic, payload)
        except Exception as e:
            logger.debug("Error handling MQTT message on %s: %s", getattr(msg, "topic", "unknown"), e)

    def _process_payload(self, topic: str, payload: bytes):
        """Parses JSON or raw hex payloads from MQTT and dispatches decoded packets."""
        raw_hex: Optional[str] = None
        sender_hint: Optional[str] = None
        snr_hint: Optional[float] = None
        rssi_hint: Optional[float] = None

        # 1. Check if JSON payload (e.g. CoreScope / meshcoretomqtt)
        try:
            text_str = payload.decode("utf-8", errors="ignore").strip()
            if text_str.startswith("{") and text_str.endswith("}"):
                data = json.loads(text_str)
                raw_hex = data.get("raw") or data.get("hex") or data.get("payload_raw") or data.get("packet")
                sender_hint = data.get("sender") or data.get("from") or data.get("node_id")
                snr_hint = data.get("snr")
                rssi_hint = data.get("rssi")
            elif all(c in "0123456789abcdefABCDEF \n\r" for c in text_str) and len(text_str) >= 4:
                # Raw hex string
                raw_hex = text_str.replace(" ", "").replace("\n", "").replace("\r", "")
        except Exception:
            pass

        # 2. Check if binary bytes
        if not raw_hex and len(payload) >= 2:
            raw_hex = payload.hex().upper()

        if not raw_hex or len(raw_hex) < 4:
            return

        # 3. Deduplicate against local LoRa radio receptions and rolling MQTT cache
        win_secs = getattr(self.mqtt_config, "dedup_window_secs", 60.0) or 60.0
        dedup = get_deduplicator()
        if dedup.is_duplicate_or_radio(raw_hex=raw_hex, window_secs=win_secs) or self._is_duplicate(raw_hex):
            logger.info("Dropped duplicate MQTT packet (already heard on local radio or seen recently): %s...", raw_hex[:16])
            return

        # 4. Decode using PacketDecoder
        decoded = self.decoder.decode_hex(raw_hex, snr=snr_hint, rssi=rssi_hint)
        if not decoded:
            logger.debug("Could not decode packet from MQTT: %s", raw_hex[:16])
            return

        # 5. Extract Sender and Hop Coordinates
        sender_id = sender_hint or decoded.payload.src_hash or (decoded.payload.pub_key[:8] if decoded.payload.pub_key else "MQTT-Node")
        sender_name = decoded.payload.name or sender_id

        # Check logical message deduplication if text is present
        msg_txt = decoded.payload.text or ""
        msg_chan = decoded.payload.channel or ""
        if msg_txt and dedup.is_duplicate_or_radio(raw_hex=raw_hex, sender_id=sender_id, channel=msg_chan, text=msg_txt, window_secs=win_secs):
            logger.info("Dropped duplicate MQTT message text (already heard on local radio): %s", msg_txt[:30])
            return

        # Register accepted MQTT packet in deduplicator
        dedup.register_mqtt_packet(raw_hex=raw_hex, sender_id=sender_id, channel=msg_chan, text=msg_txt, window_secs=win_secs)

        # Resolve contacts & coordinates from storage
        resolved_coords = []
        hop_names = []

        if decoded.payload.lat is not None and decoded.payload.lon is not None:
            resolved_coords.append([decoded.payload.lat, decoded.payload.lon])

        if self.storage:
            c = self.storage.get_contact(sender_id.lstrip("!@"))
            if c:
                if c.alias:
                    sender_name = c.alias
                if not resolved_coords and c.latitude and c.longitude:
                    resolved_coords.append([c.latitude, c.longitude])

            for h in decoded.path.hops:
                h_name = f"Hop-{h}"
                rep = self.storage.get_contact(h)
                if rep:
                    h_name = rep.alias or rep.node_id
                    if rep.latitude and rep.longitude:
                        resolved_coords.append([rep.latitude, rep.longitude])
                hop_names.append(h_name)
        else:
            hop_names = [f"Hop-{h}" for h in decoded.path.hops]

        path_info = PacketPathInfo(
            packet_id=f"mqtt-{int(time.time() * 1000)}",
            sender_id=sender_id,
            sender_name=sender_name,
            route_type=decoded.header.route_type_name,
            hop_nodes=hop_names,
            hop_snrs=[snr_hint] if snr_hint is not None else [],
            coordinates=resolved_coords,
            payload_type=decoded.header.payload_type_name,
            raw_hex=raw_hex,
            decoded_info=decoded.to_dict(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="mqtt"
        )

        # 6. Save to storage
        if self.storage:
            try:
                self.storage.save_packet_path(path_info)
            except Exception as e:
                logger.debug("Failed to save MQTT packet path: %s", e)

        # 7. Dispatch to EventBus
        bus.emit(EventType.PACKET_PATH_TRACED, path_info)

        # If decrypted group text message, also emit MESSAGE_RECEIVED
        if decoded.payload.type_name == "GRP_TXT" and decoded.payload.text:
            msg_obj = MessageEnvelope(
                id=path_info.packet_id,
                channel=decoded.payload.channel or "Public",
                sender_id=sender_id,
                sender_name=decoded.payload.sender or sender_name,
                text=decoded.payload.text,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="mqtt",
                metadata={"snr": snr_hint, "rssi": rssi_hint, "hops": len(decoded.path.hops)}
            )
            bus.emit(EventType.MESSAGE_RECEIVED, msg_obj)


    def publish_packet(self, raw_hex: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Publishes a radio packet to the MQTT broker (Gateway forwarding mode)."""
        if not self._connected or not self.client:
            return False
        cfg = self.mqtt_config
        if not cfg.publish_enabled:
            return False

        topic = cfg.publish_topic or "meshcore/packets"
        payload_data = {
            "raw": raw_hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gateway": self.config.meshcore.node_alias or self.config.meshcore.node_id or "MeshCore-Navigator",
        }
        if metadata:
            payload_data.update(metadata)

        try:
            res = self.client.publish(topic, json.dumps(payload_data), qos=0)
            return res.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.debug("Failed to publish packet to MQTT: %s", e)
            return False

    def _on_local_packet_traced(self, path: PacketPathInfo):
        """Forwards local RF packet traces to MQTT if gateway publishing is enabled."""
        if not path:
            return

        # Immediately record local radio packet in deduplicator so broker loopbacks/echoes are dropped
        if getattr(path, "source", "radio") == "radio" or not path.packet_id.startswith("mqtt-"):
            raw = getattr(path, "raw_hex", "")
            txt = ""
            if getattr(path, "decoded_info", None) and isinstance(path.decoded_info, dict):
                txt = path.decoded_info.get("text", "")
            get_deduplicator().register_radio_packet(
                raw_hex=raw,
                sender_id=path.sender_id,
                text=txt
            )

        if not self.mqtt_config.publish_enabled or not self._connected:
            return
        # Do not bounce back packets that originated from MQTT
        if path.packet_id.startswith("mqtt-") or getattr(path, "source", "") == "mqtt":
            return
        raw = getattr(path, "raw_hex", "")
        if raw:
            meta = {
                "sender_id": path.sender_id,
                "sender_name": path.sender_name,
                "route_type": path.route_type,
                "hop_count": len(path.hop_nodes) if path.hop_nodes else 0
            }
            self.publish_packet(raw, meta)
