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
from meshcore_tray.core.models import PacketPathInfo, MessageEnvelope, NodeContact, is_valid_coordinate
from meshcore_tray.core.packet_decoder import PacketDecoder, DecodedPacket
from meshcore_tray.core.deduplicator import get_deduplicator
from meshcore_tray.storage import Storage


logger = logging.getLogger("meshcore_tray.mqtt_service")


def parse_mqtt_broker_url(
    host: str,
    port: int = 1883,
    use_tls: bool = False,
    transport: str = "tcp",
    ws_path: str = "/mqtt"
) -> Tuple[str, int, bool, str, str]:
    """Parses broker host string, handling URL schemes (wss://, ws://, mqtt://, ssl://) and port suffixes."""
    clean_host = (host or "").strip()
    clean_port = port
    clean_tls = use_tls
    clean_transport = transport or "tcp"
    clean_path = ws_path or "/mqtt"

    if "://" in clean_host:
        from urllib.parse import urlparse
        parsed = urlparse(clean_host)
        scheme = parsed.scheme.lower()
        clean_host = parsed.hostname or clean_host
        if parsed.port:
            clean_port = parsed.port
        if parsed.path and parsed.path != "/":
            clean_path = parsed.path

        if scheme in ("wss", "ws"):
            clean_transport = "websockets"
            clean_tls = (scheme == "wss")
            if not parsed.port:
                clean_port = 443 if scheme == "wss" else 80
        elif scheme in ("ssl", "mqtts", "tls"):
            clean_transport = "tcp"
            clean_tls = True
            if not parsed.port:
                clean_port = 8883
        elif scheme in ("mqtt", "tcp"):
            clean_transport = "tcp"
            clean_tls = False
            if not parsed.port:
                clean_port = 1883
    elif ":" in clean_host and not clean_host.startswith("["):
        parts = clean_host.split(":")
        clean_host = parts[0]
        try:
            clean_port = int(parts[1])
        except ValueError:
            pass

    if clean_port in (443, 8883):
        clean_tls = True
    if clean_port == 443 and clean_transport != "websockets":
        clean_transport = "websockets"

    return clean_host, clean_port, clean_tls, clean_transport, clean_path


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

    @classmethod
    def test_broker_connection(
        cls,
        host: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        use_tls: bool = False,
        transport: str = "tcp",
        ws_path: str = "/mqtt",
        topic: str = "meshcore/#",
        timeout_secs: float = 6.0
    ) -> Dict[str, Any]:
        """Tests MQTT broker connection and subscription, returning latency and status dict."""
        if not HAS_PAHO_MQTT:
            return {"success": False, "error": "paho-mqtt is not installed in the environment."}

        clean_host, clean_port, clean_tls, clean_transport, clean_path = parse_mqtt_broker_url(
            host, port, use_tls, transport, ws_path
        )

        t0 = time.time()
        res = {"success": False, "latency_ms": 0, "error": None, "host": clean_host, "port": clean_port}

        def on_connect(c, userdata, flags, rc, *args):
            rc_val = getattr(rc, "value", rc)
            if rc_val == 0:
                sub_topic = topic.split(",")[0].strip() if topic else "meshcore/#"
                c.subscribe(sub_topic, qos=0)
                res["success"] = True
                res["latency_ms"] = max(1, int((time.time() - t0) * 1000))
            else:
                res["error"] = f"Connection refused by broker (code: {rc})"

        client_kwargs = {
            "client_id": f"meshcore-test-{int(time.time())}",
            "transport": clean_transport
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2

        try:
            test_client = mqtt.Client(**client_kwargs)
            if clean_transport == "websockets" and hasattr(test_client, "ws_set_options"):
                test_client.ws_set_options(path=clean_path)
            if username:
                test_client.username_pw_set(username, password or None)
            if clean_tls:
                test_client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

            test_client.on_connect = on_connect
            test_client.connect(clean_host, clean_port, keepalive=10)
            test_client.loop_start()

            deadline = time.time() + timeout_secs
            while time.time() < deadline:
                if res["success"] or res["error"]:
                    break
                time.sleep(0.1)

            test_client.loop_stop()
            test_client.disconnect()

            if not res["success"] and not res["error"]:
                res["error"] = f"Connection timed out after {timeout_secs}s."
        except Exception as e:
            res["error"] = str(e)

        return res

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
            clean_host, clean_port, clean_tls, clean_transport, clean_path = parse_mqtt_broker_url(
                cfg.broker_host,
                cfg.broker_port,
                cfg.use_tls,
                getattr(cfg, "transport", "tcp"),
                getattr(cfg, "ws_path", "/mqtt")
            )

            client_kwargs = {
                "client_id": cfg.client_id or f"meshcore-nav-{int(time.time())}",
                "transport": clean_transport
            }
            if hasattr(mqtt, "CallbackAPIVersion"):
                client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2

            self.client = mqtt.Client(**client_kwargs)

            # WebSockets path options
            if clean_transport == "websockets" and hasattr(self.client, "ws_set_options"):
                self.client.ws_set_options(path=clean_path)

            # Authentication
            if cfg.username:
                self.client.username_pw_set(cfg.username, cfg.password or None)

            # TLS
            if clean_tls:
                self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

            # Wire Callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            logger.info("Connecting to MQTT broker %s:%d (%s, TLS=%s)...", clean_host, clean_port, clean_transport, clean_tls)
            self.client.connect_async(clean_host, clean_port, keepalive=60)
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
                    client.subscribe(t.strip(), qos=0)
                    logger.debug("Subscribed to MQTT topic: %s", t.strip())
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": True,
                "source": "mqtt",
                "broker": self.mqtt_config.broker_host
            })
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
        origin_hint: Optional[str] = None
        origin_id_hint: Optional[str] = None
        channel_hint: Optional[str] = None
        text_hint: Optional[str] = None

        # Extract channel hint and origin gateway hint from MQTT topic if present (e.g. meshcore/channel/thenorf or meshcore/public/gw/packets)
        if topic:
            t_parts = topic.strip("/").split("/")
            if len(t_parts) >= 2 and t_parts[0].lower() in ("meshcore", "ukmesh"):
                if t_parts[1].lower() == "channel" and len(t_parts) >= 3:
                    channel_hint = t_parts[2]
                elif t_parts[1].lower() not in ("packets", "packet", "raw", "telemetry", "gateway", "status", "adv", "nodes", "public"):
                    channel_hint = t_parts[1]

            # Detect gateway/observer callsign from topic path (e.g. public/<gateway>/packets)
            if len(t_parts) >= 3 and t_parts[-1].lower() in ("packets", "packet", "raw"):
                candidate_gw = t_parts[-2]
                if candidate_gw.lower() not in ("meshcore", "ukmesh", "public", "channel", "packets", "raw"):
                    origin_hint = candidate_gw

        # 1. Check if JSON payload (e.g. CoreScope / meshcoretomqtt / UKMesh)
        try:
            text_str = payload.decode("utf-8", errors="ignore").strip()
            if text_str.startswith("{") and text_str.endswith("}"):
                data = json.loads(text_str)
                raw_hex = data.get("raw") or data.get("hex") or data.get("payload_raw") or data.get("packet")
                sender_hint = data.get("sender") or data.get("from") or data.get("node_id")
                origin_hint = (
                    data.get("origin")
                    or data.get("gateway")
                    or data.get("observer")
                    or data.get("rx_node")
                    or data.get("reporter")
                    or origin_hint
                )
                origin_id_hint = data.get("origin_id")
                if data.get("channel"):
                    channel_hint = str(data.get("channel"))
                if data.get("text") or data.get("message"):
                    text_hint = str(data.get("text") or data.get("message"))
                
                raw_snr = data.get("snr") if data.get("snr") is not None else data.get("SNR")
                if raw_snr is not None:
                    try:
                        snr_hint = float(raw_snr)
                    except Exception:
                        pass

                raw_rssi = data.get("rssi") if data.get("rssi") is not None else data.get("RSSI")
                if raw_rssi is not None:
                    try:
                        rssi_hint = float(raw_rssi)
                    except Exception:
                        pass
            elif all(c in "0123456789abcdefABCDEF \n\r" for c in text_str) and len(text_str) >= 4:
                # Raw hex string
                raw_hex = text_str.replace(" ", "").replace("\n", "").replace("\r", "")
        except Exception:
            pass

        # 2. Check if binary bytes
        if not raw_hex and len(payload) >= 2:
            raw_hex = payload.hex().upper()

        # Handle pure JSON chat message without raw hex if text is present
        if not raw_hex:
            if text_hint:
                raw_hex = hashlib.sha256(text_str.encode("utf-8", errors="ignore")).hexdigest()[:32].upper()
            else:
                return

        # 3. Deduplicate against local LoRa radio receptions and rolling MQTT cache
        win_secs = getattr(self.mqtt_config, "dedup_window_secs", 60.0) or 60.0
        dedup = get_deduplicator()
        is_radio_dup = dedup.is_duplicate_or_radio(raw_hex=raw_hex, sender_id=sender_hint or "", channel=channel_hint or "", text=text_hint or "", window_secs=win_secs)
        if is_radio_dup:
            logger.info("MQTT packet was already heard on local radio or seen recently: %s...", raw_hex[:16])
            # If this is a text message, tag the existing record with dual RF+MQTT sources!
            if self.storage:
                ch = channel_hint or "Public"
                txt = text_hint
                if not txt:
                    try:
                        decoded_try = self.decoder.decode_hex(raw_hex)
                        if decoded_try and hasattr(decoded_try, "payload") and getattr(decoded_try.payload, "text", None):
                            txt = decoded_try.payload.text
                            if getattr(decoded_try.payload, "channel", None):
                                ch = decoded_try.payload.channel
                    except Exception:
                        pass
                if txt:
                    self.storage.tag_message_source(channel=ch, text=txt, source="mqtt")
            return

        # 4. Decode using PacketDecoder
        decoded = self.decoder.decode_hex(raw_hex, snr=snr_hint, rssi=rssi_hint)
        if not decoded and not text_hint:
            logger.debug("Could not decode packet from MQTT: %s", raw_hex[:16])
            return

        # 5. Extract Sender, Recipient and Hop Coordinates
        src_h = getattr(decoded.payload, "src_hash", None) if decoded else None
        dest_h = getattr(decoded.payload, "dest_hash", None) if decoded else None
        p_name = decoded.header.payload_type_name if (decoded and decoded.header) else ""

        sender_id = (
            sender_hint
            or (decoded.payload.pub_key[:8] if (decoded and decoded.payload.pub_key) else None)
            or src_h
            or origin_id_hint
            or origin_hint
            or "MQTT-Node"
        )
        sender_name = (decoded.payload.name if decoded else None) or sender_hint or sender_id

        # Check logical message deduplication if text is present
        msg_txt = (decoded.payload.text if decoded else None) or text_hint or ""
        msg_chan = (decoded.payload.channel if decoded else None) or channel_hint or ""
        if msg_txt and is_radio_dup:
            if self.storage:
                self.storage.tag_message_source(channel=msg_chan or "Public", text=msg_txt, source="mqtt")
            return

        # Register accepted MQTT packet in deduplicator
        dedup.register_mqtt_packet(raw_hex=raw_hex, sender_id=sender_id, channel=msg_chan, text=msg_txt, window_secs=win_secs)

        # Resolve contacts & coordinates from storage
        resolved_coords = []
        hop_names = []

        if decoded.payload.lat is not None and decoded.payload.lon is not None:
            resolved_coords.append([decoded.payload.lat, decoded.payload.lon])

        resolved_sender_alias = None
        if self.storage:
            c = self.storage.get_contact(sender_id.lstrip("!@"))
            if not c and len(sender_id) <= 4:
                c = self.storage.get_best_contact_for_hop(sender_id)
            if c:
                resolved_sender_alias = f"@{c.alias}" if not c.alias.startswith("@") else c.alias
                sender_name = c.alias
                if not resolved_coords and c.latitude and c.longitude:
                    resolved_coords.append([c.latitude, c.longitude])

            # Resolve hops sequentially using hop disambiguation chain
            if decoded.path and decoded.path.hops:
                try:
                    chain = self.storage.resolve_hop_chain_with_candidates(
                        decoded.path.hops,
                        sender_coord=resolved_coords[0] if resolved_coords else None,
                        user_station_prefix="M7NCY",
                        sender_name=sender_name
                    )
                    for item in chain:
                        hop_c = item.get("contact")
                        sub_h = item.get("hash", "")
                        if hop_c:
                            hop_names.append(f"@{hop_c.alias}" if not hop_c.alias.startswith("@") else hop_c.alias)
                            if hop_c.latitude is not None and hop_c.longitude is not None and not self.storage.is_phantom_node(hop_c.node_id, hop_c.alias):
                                resolved_coords.append([float(hop_c.latitude), float(hop_c.longitude)])
                        else:
                            hop_names.append(f"<Unknown Repeater {sub_h}>")
                except Exception as e:
                    logger.debug("Error resolving hop chain in MQTT: %s", e)
                    hop_names = [f"Hop-{h}" for h in decoded.path.hops]
        elif decoded.path and decoded.path.hops:
            hop_names = [f"Hop-{h}" for h in decoded.path.hops]

        # Format descriptive sender label for REQ / control packets or bare short hashes
        if decoded and p_name in ("REQ", "ANON_REQ", "RESP", "RESPONSE", "ACK"):
            src_label = resolved_sender_alias or sender_name
            if src_label == sender_id and len(sender_id) <= 4:
                src_label = f"Node [{sender_id}]"

            dest_label = None
            if dest_h:
                dest_c = self.storage.get_best_contact_for_hop(dest_h) if self.storage else None
                if dest_c:
                    dest_label = f"@{dest_c.alias}" if not dest_c.alias.startswith("@") else dest_c.alias
                else:
                    dest_label = f"Node [{dest_h}]"

            if dest_label:
                display_label = f"{src_label} ➔ {dest_label}"
            elif p_name in ("REQ", "ANON_REQ"):
                display_label = f"{src_label} (Request)"
            else:
                display_label = f"{src_label} ({p_name})"

            if origin_hint and origin_hint.lower() not in display_label.lower():
                display_label += f" (via @{origin_hint})"

            sender_name = display_label
        elif sender_name == sender_id and len(sender_id) <= 4:
            if origin_hint:
                sender_name = f"Node [{sender_id}] (via @{origin_hint})"
            else:
                sender_name = f"Node [{sender_id}]"

        decoded_dict = decoded.to_dict()
        if origin_hint:
            decoded_dict["observer_origin"] = origin_hint
        if origin_id_hint:
            decoded_dict["observer_origin_id"] = origin_id_hint
        if snr_hint is not None:
            decoded_dict["snr"] = snr_hint
        if rssi_hint is not None:
            decoded_dict["rssi"] = rssi_hint

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
            decoded_info=decoded_dict,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="mqtt"
        )

        # 6. Save to storage
        if self.storage:
            try:
                self.storage.save_packet_path(path_info)
            except Exception as e:
                logger.debug("Failed to save MQTT packet path: %s", e)

            # If node advertisement with valid GPS coordinates, register/update contact in storage
            if decoded.payload.type_name == "ADVERT" and decoded.payload.pub_key:
                try:
                    node_id = decoded.payload.pub_key[:12]
                    adv_alias = decoded.payload.name or sender_name
                    lat = decoded.payload.lat
                    lon = decoded.payload.lon
                    is_rep = bool(getattr(decoded.payload.flags, "repeater", False)) if decoded.payload.flags else False
                    is_room = bool(getattr(decoded.payload.flags, "room", False)) if decoded.payload.flags else False
                    if lat is not None and lon is not None and is_valid_coordinate(lat, lon):
                        existing = self.storage.get_contact(node_id) or self.storage.get_contact(decoded.payload.pub_key)
                        contact_to_emit = None
                        if existing:
                            if adv_alias:
                                existing.alias = adv_alias
                            existing.latitude = lat
                            existing.longitude = lon
                            existing.last_seen = datetime.now(timezone.utc).isoformat()
                            if is_rep:
                                existing.is_repeater = True
                            if is_room:
                                existing.is_room_server = True
                            # Only set source to "mqtt" if existing.source is not already "radio"
                            if getattr(existing, "source", None) != "radio":
                                existing.source = "mqtt"
                            self.storage.save_contact(existing)
                            contact_to_emit = existing
                        else:
                            contact_to_emit = NodeContact(
                                node_id=node_id,
                                alias=adv_alias or node_id,
                                public_key=decoded.payload.pub_key,
                                latitude=lat,
                                longitude=lon,
                                is_repeater=is_rep,
                                is_room_server=is_room,
                                last_seen=datetime.now(timezone.utc).isoformat(),
                                source="mqtt"
                            )
                            self.storage.save_contact(contact_to_emit)

                        if contact_to_emit:
                            bus.emit(EventType.NODE_DISCOVERED, contact_to_emit)
                            bus.emit(EventType.MAP_NODES_UPDATED)
                except Exception as e:
                    logger.debug("Failed to update contact from MQTT advert: %s", e)

        # 7. Dispatch to EventBus
        bus.emit(EventType.PACKET_PATH_TRACED, path_info)

        # If decrypted group text message or JSON chat message, save to storage and emit MESSAGE_RECEIVED
        msg_text_to_save = (decoded.payload.text if (decoded and decoded.payload) else None) or text_hint
        if ((decoded and decoded.payload and decoded.payload.type_name == "GRP_TXT") or text_hint) and msg_text_to_save:
            raw_c = (decoded.payload.channel if (decoded and decoded.payload) else None) or channel_hint or "Public"
            norm_c = "Public" if str(raw_c).strip().lower().lstrip("#") == "public" else (raw_c if str(raw_c).startswith("#") else f"#{raw_c}")
            hops_count = len(decoded.path.hops) if (decoded and decoded.path and decoded.path.hops) else 0
            msg_obj = MessageEnvelope(
                id=path_info.packet_id,
                channel=norm_c,
                sender_id=sender_id,
                sender_name=(decoded.payload.sender if (decoded and decoded.payload) else None) or sender_name,
                text=msg_text_to_save,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="mqtt",
                metadata={"snr": snr_hint, "rssi": rssi_hint, "hops": hops_count, "sources": ["mqtt"]}
            )
            if self.storage:
                try:
                    self.storage.save_message(msg_obj)
                except Exception as e:
                    logger.debug("Failed saving MQTT message to storage: %s", e)
            bus.emit(EventType.MESSAGE_RECEIVED, msg_obj)


    def publish_packet(self, raw_hex: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Publishes a radio packet to the MQTT broker (Gateway forwarding mode)."""
        if not self._connected or not self.client:
            return False
        cfg = self.mqtt_config
        if not cfg.publish_enabled:
            return False
        if "ukmesh.com" in (cfg.broker_host or "").lower():
            logger.info("Publishing denied on read-only UKMesh broker.")
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
