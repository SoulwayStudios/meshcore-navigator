"""MeshCore Serial Hardware Driver for Heltec V3 and compatible LoRa nodes."""

import asyncio
from datetime import datetime, timezone, timedelta
import glob
import logging
import re
import sys
from typing import Any, Dict, List, Optional
import serial.tools.list_ports

import meshcore
from meshcore.events import Event, EventType as McEventType
from meshcore_tray.core.models import (
    ChannelInfo, MessageEnvelope, NeighbourInfo, NodeContact, TelemetryEnvelope, PacketPathInfo, DockedCompanionInfo,
    is_valid_alias, is_valid_node_id, is_valid_coordinate, is_plausible_rf_coordinate
)
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.base_driver import BaseRadioDriver

logger = logging.getLogger("meshcore_tray.meshcore_driver")


class MeshCoreDriver(BaseRadioDriver):
    """Driver connecting to physical Heltec V3 node via MeshCore Companion protocol."""

    def __init__(self, config=None, storage=None):
        self.config = config
        self.storage = storage
        self.client: Optional[meshcore.MeshCore] = None
        self._connected = False
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._periodic_task: Optional[asyncio.Task] = None
        self._active_subscriptions: List[Any] = []
        self._current_port: str = ""
        self._recent_outgoing: List[Dict[str, Any]] = []
        self._recent_rx_logs: List[Dict[str, Any]] = []
        self._recent_heard_msg_ids: set = set()
        self._cmd_lock: Optional[asyncio.Lock] = None

    def _get_cmd_lock(self) -> asyncio.Lock:
        if not hasattr(self, "_cmd_lock") or self._cmd_lock is None:
            self._cmd_lock = asyncio.Lock()
        return self._cmd_lock

    def _dispatch_task(self, coro):
        """Dispatches an asynchronous task safely from either the main thread or a background thread."""
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if hasattr(bus, "_loop") and bus._loop and bus._loop.is_running():
                loop = bus._loop
        if loop and loop.is_running():
            try:
                return loop.create_task(coro)
            except RuntimeError:
                return asyncio.run_coroutine_threadsafe(coro, loop)
        logger.warning("No running asyncio event loop found to dispatch task")
        return None


    def _record_outgoing_message(self, msg_id: str, channel: str, text: str):
        """Records an outgoing transmission to track repeats heard over the mesh."""
        self._recent_outgoing.append({
            "msg_id": msg_id,
            "time": datetime.now(timezone.utc).timestamp(),
            "channel": channel,
            "text": text,
            "repeats": 0,
            "repeaters": set()
        })
        if len(self._recent_outgoing) > 20:
            self._recent_outgoing.pop(0)

    def _increment_message_repeat(self, target_msg_id: str, repeater_id: Optional[str] = None):
        """Increments repeats heard for an outgoing message and emits MESSAGE_UPDATED."""
        for item in reversed(self._recent_outgoing):
            if item["msg_id"] == target_msg_id:
                if repeater_id:
                    if repeater_id in item["repeaters"]:
                        return
                    item["repeaters"].add(repeater_id)
                item["repeats"] += 1
                new_count = item["repeats"]
                if self.storage:
                    self.storage.update_message_repeats(target_msg_id, new_count)
                    updated = self.storage.get_message(target_msg_id)
                    if updated:
                        bus.emit(EventType.MESSAGE_UPDATED, updated)
                break

    @staticmethod
    def scan_serial_ports() -> list[str]:
        """Auto-detects available serial ports on Linux/Windows, prioritizing USB radio devices."""
        usb_ports = []
        other_ports = []

        if sys.platform != "win32":
            # Check standard dev paths for USB serial devices first on Linux/macOS
            for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
                for match in sorted(glob.glob(pattern)):
                    if match not in usb_ports:
                        usb_ports.append(match)

        # Check sysfs / Windows registry comports via pyserial
        try:
            for p in serial.tools.list_ports.comports():
                desc = (p.description or "").lower()
                hwid = (p.hwid or "").lower()
                dev = p.device
                if any(term in desc or term in hwid for term in ["usb", "uart", "cp210", "ch340", "ftdi", "meshcore", "heltec"]):
                    if dev not in usb_ports:
                        usb_ports.append(dev)
                elif not dev.startswith("/dev/ttyS"):
                    if dev not in other_ports and dev not in usb_ports:
                        other_ports.append(dev)
        except Exception as e:
            logger.debug(f"Serial port scan notice: {e}")

        # On Windows, if no devices detected yet, check COM1-COM32
        if sys.platform == "win32" and not usb_ports and not other_ports:
            for i in range(1, 33):
                com_name = f"COM{i}"
                if com_name in other_ports and com_name not in usb_ports:
                    usb_ports.append(com_name)

        return usb_ports + other_ports

    async def start(self):
        self._running = True
        await self._connect()
        if not self._periodic_task or self._periodic_task.done():
            self._periodic_task = asyncio.create_task(self._periodic_maintenance_loop())

    async def stop(self):
        self._running = False
        if self._periodic_task:
            self._periodic_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        self._cleanup_client()
        self._connected = False
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": False,
            "port": "",
            "mode": "serial",
            "message": "Disconnected"
        })

    def is_connected(self) -> bool:
        return self._connected and bool(self.client and self.client.is_connected)

    def connect(self):
        """Dispatches an asynchronous connection attempt or reconnection."""
        self._running = True
        self._cleanup_client()
        self._connected = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if not self._periodic_task or self._periodic_task.done():
            self._dispatch_task(self._periodic_maintenance_loop())
        self._dispatch_task(self._connect())

    def reconnect(self):
        """Forces an immediate reconnection cycle."""
        self.connect()


    def _cleanup_client(self):
        if self.client:
            for sub in self._active_subscriptions:
                try:
                    self.client.unsubscribe(sub)
                except Exception:
                    pass
            self._active_subscriptions.clear()
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None

    async def _connect(self):
        port = self.config.meshcore.serial_port if self.config else "auto"
        baud = self.config.meshcore.baudrate if self.config else 115200

        if port == "auto":
            detected = self.scan_serial_ports()
            if detected:
                port = detected[0]
                logger.info(f"Auto-selected serial port: {port}")
            else:
                logger.warning("No serial ports detected for Heltec V3 radio.")
                bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                    "connected": False,
                    "port": "auto",
                    "mode": "serial",
                    "message": "No serial devices detected"
                })
                self._schedule_reconnect()
                return

        self._current_port = port
        try:
            logger.info(f"Attempting connection to MeshCore node on {port} @ {baud}...")
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": False,
                "port": port,
                "mode": "serial",
                "message": f"Connecting to {port}..."
            })
            mc = await meshcore.MeshCore.create_serial(
                port=port,
                baudrate=baud,
                auto_reconnect=True
            )
            if not mc:
                raise ConnectionError(f"MeshCore.create_serial returned None for {port}")

            self.client = mc
            if hasattr(self.client, "_reader") and hasattr(self.client._reader, "decrypt_channels"):
                self.client._reader.decrypt_channels = True
            self._connected = True
            self._setup_subscriptions()

            # Start the auto-fetching loop
            await self.client.start_auto_message_fetching()

            logger.info(f"Successfully connected to MeshCore node on {port}")
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": True,
                "port": port,
                "mode": "serial",
                "message": f"Connected to {port}"
            })

            # Sync node information and state
            await self._sync_initial_node_state()

        except Exception as e:
            logger.error(f"Failed to connect to MeshCore on {port}: {e}")
            self._connected = False
            self._cleanup_client()
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": False,
                "port": port,
                "mode": "serial",
                "message": f"Connection error: {e}"
            })
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._running and (not self._reconnect_task or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        while self._running and not self.is_connected():
            await asyncio.sleep(5)
            logger.info("Attempting auto-reconnect to MeshCore node...")
            await self._connect()

    def _setup_subscriptions(self):
        if not self.client:
            return

        events_to_subscribe = [
            (McEventType.CHANNEL_MSG_RECV, self._handle_channel_msg),
            (McEventType.CONTACT_MSG_RECV, self._handle_contact_msg),
            (McEventType.ACK, self._handle_ack),
            (McEventType.TELEMETRY_RESPONSE, self._handle_telemetry),
            (McEventType.NEIGHBOURS_RESPONSE, self._handle_neighbours),
            (McEventType.BATTERY, self._handle_battery),
            (McEventType.CONTACTS, self._handle_contacts),
            (McEventType.NEXT_CONTACT, self._handle_single_contact),
            (McEventType.NEW_CONTACT, self._handle_single_contact),
            (McEventType.ADVERTISEMENT, self._handle_advertisement),
            (McEventType.CLI_REPLY, self._handle_cli_reply),
            (McEventType.LOGIN_SUCCESS, self._handle_login_success),
            (McEventType.LOGIN_FAILED, self._handle_login_failed),
            (McEventType.STATUS_RESPONSE, self._handle_status_response),
        ]
        if hasattr(McEventType, "RX_LOG_DATA"):
            events_to_subscribe.append((McEventType.RX_LOG_DATA, self._handle_rx_log_data))
        if hasattr(McEventType, "TRACE_DATA"):
            events_to_subscribe.append((McEventType.TRACE_DATA, self._handle_trace_data))
        if hasattr(McEventType, "PATH_RESPONSE"):
            events_to_subscribe.append((McEventType.PATH_RESPONSE, self._handle_path_response))
        if hasattr(McEventType, "CHANNEL_DATA_RECV"):
            events_to_subscribe.append((McEventType.CHANNEL_DATA_RECV, self._handle_channel_data))
        events_to_subscribe.append((McEventType.DISCONNECTED, self._handle_disconnected))

        for ev_type, handler in events_to_subscribe:
            try:
                sub = self.client.subscribe(ev_type, handler)
                if sub:
                    self._active_subscriptions.append(sub)
            except Exception as e:
                logger.error(f"Failed to subscribe to {ev_type}: {e}")


    async def _sync_initial_node_state(self):
        """Fetches self-info, contacts, battery, and drains initial messages."""
        if not self.client:
            return

        freq = 869.618
        sf = 8
        bw = 62.5

        # 1. Self Info & RF Telemetry
        self_info = self.client.self_info
        if self_info:
            hw_name = str(self_info.get("name", "Heltec-V3"))
            pubkey = str(self_info.get("public_key", ""))
            freq = float(self_info.get("radio_freq", 869.618))
            bw = float(self_info.get("radio_bw", 62.5))
            sf = int(self_info.get("radio_sf", 8))
            cr_num = self_info.get("radio_cr", 5)
            cr = f"4/{cr_num}" if isinstance(cr_num, int) else str(cr_num)
            tx = int(self_info.get("tx_power", 22))

            name = hw_name
            if self.config and self.config.meshcore.node_alias and self.config.meshcore.node_alias not in ("Heltec-V3", ""):
                name = self.config.meshcore.node_alias
                if name != hw_name:
                    try:
                        logger.info(f"Applying configured node name '{name}' to hardware node (was '{hw_name}')...")
                        await self.client.commands.set_name(name)
                    except Exception as e:
                        logger.debug(f"Error setting node name on hardware: {e}")
            elif self.config:
                self.config.meshcore.node_alias = name

            if self.config:
                self.config.meshcore.node_id = f"!{pubkey[:8]}" if pubkey else f"!{name}"

            # Clean up any legacy echo rows in database for this local alias
            if self.storage and name:
                self.storage.cleanup_echo_messages(name, self.config.meshcore.node_id if self.config else None)

            telem = TelemetryEnvelope(
                node_id="local",
                alias=name,
                frequency_mhz=freq,
                bandwidth_khz=bw,
                spreading_factor=sf,
                coding_rate=cr,
                tx_power_dbm=tx,
                noise_floor_dbm=-118.0,
                snr_db=9.5,
                rssi_dbm=-85.0
            )
            if self.storage:
                self.storage.save_telemetry(telem)
            bus.emit(EventType.TELEMETRY_UPDATED, telem)

            bus.emit(EventType.SYNC_STATUS, {
                "stage": "radio",
                "message": f"Connected • Radio {freq} MHz (BW {bw}k, SF{sf})",
                "is_synced": False
            })

        # Query and synchronize hardware path hash mode (byte path setting)
        try:
            hw_path_mode = await self.client.commands.get_path_hash_mode()
            desired_mode = self.config.meshcore.path_hash_mode if (self.config and hasattr(self.config.meshcore, "path_hash_mode")) else 1
            if isinstance(hw_path_mode, int) and hw_path_mode >= 0:
                logger.info(f"Hardware node path_hash_mode reported: {hw_path_mode} ({hw_path_mode+1} bytes/hop)")
                if hw_path_mode != desired_mode:
                    logger.info(f"Synchronizing hardware path_hash_mode: {hw_path_mode} -> {desired_mode} ({desired_mode+1} bytes/hop)...")
                    await self.client.commands.set_path_hash_mode(desired_mode)
                    if self.config:
                        self.config.meshcore.path_hash_mode = desired_mode
                elif self.config:
                    self.config.meshcore.path_hash_mode = hw_path_mode
        except Exception as e:
            logger.debug(f"Error querying/synchronizing hardware path_hash_mode: {e}")

        # 2. Battery
        try:
            bat_event = await self.client.commands.get_bat()
            if bat_event and bat_event.type != McEventType.ERROR:
                self._handle_battery(bat_event)
        except Exception as e:
            logger.debug(f"Error querying initial battery: {e}")

        # 3. Channels from Hardware & App-to-Radio Synchronization
        try:
            await self._async_sync_channels()
        except Exception as e:
            logger.debug(f"Error in initial channel synchronization: {e}")

        # 4. Contacts
        bus.emit(EventType.SYNC_STATUS, {
            "stage": "contacts",
            "message": "Syncing mesh contacts from node flash...",
            "is_synced": False
        })
        try:
            contacts_event = await self.client.commands.get_contacts()
            if contacts_event and contacts_event.type != McEventType.ERROR:
                self._handle_contacts(contacts_event)
        except Exception as e:
            logger.debug(f"Error querying initial contacts: {e}")

        # 5. Drain initial pending messages
        bus.emit(EventType.SYNC_STATUS, {
            "stage": "messages",
            "message": "Draining buffered incoming messages...",
            "is_synced": False
        })
        try:
            await self.drain_messages()
        except Exception as e:
            logger.debug(f"Error draining initial messages: {e}")

        contact_count = len(self.storage.get_contacts()) if self.storage else 0
        channel_count = len(self.storage.get_channels()) if self.storage else 1
        bus.emit(EventType.SYNC_STATUS, {
            "stage": "complete",
            "message": f"✓ Up to date ({contact_count} contacts • {channel_count} channels • {freq} MHz • SF{sf})",
            "is_synced": True,
            "contact_count": contact_count,
            "channel_count": channel_count,
            "frequency_mhz": freq
        })

    async def drain_messages(self):
        """Actively retrieves any buffered messages from the hardware queue until empty."""
        if not self.client or not self.is_connected():
            return
        async with self._get_cmd_lock():
            for _ in range(30):
                try:
                    res = await self.client.commands.get_msg(timeout=1.5)
                    if not res or res.type in (McEventType.NO_MORE_MSGS, McEventType.ERROR):
                        break
                    await asyncio.sleep(0.04)
                except Exception as e:
                    logger.debug(f"Error draining message from hardware: {e}")
                    break

    async def _delayed_message_drain(self):
        try:
            await asyncio.sleep(0.2)
            await self.drain_messages()
        except Exception:
            pass

    async def _periodic_maintenance_loop(self):
        """Continuous fast background message drain and periodic battery refresh."""
        counter = 0
        while self._running:
            try:
                await asyncio.sleep(2.5)
                if self.is_connected() and self.client:
                    await self.drain_messages()
                    counter += 1
                    if counter >= 24:  # every ~60s
                        counter = 0
                        async with self._get_cmd_lock():
                            await self.client.commands.get_bat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Error in periodic maintenance loop: {e}")

    def _handle_disconnected(self, event_data: Any):
        logger.warning("MeshCore hardware disconnected event received.")
        self._connected = False
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": False,
            "port": self._current_port,
            "mode": "serial",
            "message": "Connection lost"
        })
        self._schedule_reconnect()

    def _extract_payload(self, event_data: Any) -> Dict[str, Any]:
        if isinstance(event_data, Event):
            return event_data.payload if isinstance(event_data.payload, dict) else {}
        elif isinstance(event_data, dict):
            return event_data
        return getattr(event_data, "__dict__", {})

    def _handle_channel_msg(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            raw_text = str(raw.get("text", raw.get("message", "")))
            channel_idx = int(raw.get("channel_idx", raw.get("channel_id", 0)))
            snr = float(raw.get("SNR", raw.get("snr", 0.0)))
            rssi = float(raw.get("RSSI", raw.get("rssi", -100.0)))
            sender_ts = int(raw.get("sender_timestamp", 0))

            channel_name = "Public" if channel_idx == 0 else f"#channel_{channel_idx}"
            if raw.get("chan_name"):
                channel_name = str(raw.get("chan_name"))
            elif self.storage:
                channels = self.storage.get_channels()
                for ch in channels:
                    if ch.channel_id == channel_idx:
                        channel_name = ch.name
                        break

            # Parse sender name from message prefix if present (e.g. "G7VQV: Morning Eastleigh" or "Andrianach Wadamesh TD+: morning")
            sender_name = "MeshNode"
            sender_id = f"chan_{channel_idx}"
            clean_text = raw_text

            if ": " in raw_text:
                cand_sender, sep, cand_text = raw_text.partition(": ")
                cand_sender_clean = cand_sender.strip()
                if (
                    cand_sender_clean
                    and len(cand_sender_clean) <= 32
                    and "\n" not in cand_sender_clean
                    and not cand_sender_clean.lower().startswith("http")
                ):
                    sender_name = cand_sender_clean
                    clean_text = cand_text
                    sender_id = f"!{sender_name}"
            else:
                match = re.match(r"^([^:\n]{1,32}):\s*(.*)$", raw_text)
                if match:
                    cand = match.group(1).strip()
                    if not cand.lower().startswith("http"):
                        sender_name = cand
                        clean_text = match.group(2)
                        sender_id = f"!{sender_name}"

            # Deterministic message ID and deduplication
            clean_chan_tag = channel_name.lstrip("#").lower()
            if sender_ts > 0:
                msg_id = f"chan-{clean_chan_tag}-{sender_ts}-{abs(hash(clean_text)) % 1000000}"
            else:
                now_slot = int(datetime.now().timestamp()) // 4
                msg_id = f"chan-{clean_chan_tag}-{now_slot}-{abs(hash(clean_text)) % 1000000}"

            if not hasattr(self, "_recent_heard_msg_ids"):
                self._recent_heard_msg_ids = set()

            if msg_id in self._recent_heard_msg_ids:
                logger.debug(f"Ignoring duplicate channel message {msg_id}")
                return
            if self.storage and self.storage.get_message(msg_id):
                logger.debug(f"Channel message {msg_id} already exists in database")
                self._recent_heard_msg_ids.add(msg_id)
                return

            self._recent_heard_msg_ids.add(msg_id)
            if len(self._recent_heard_msg_ids) > 300:
                self._recent_heard_msg_ids.pop()

            is_fav = False
            if self.config and self.config.is_user_favorite(sender_id, sender_name):
                is_fav = True

            iso_timestamp = datetime.now(timezone.utc).isoformat()
            if sender_ts > 0:
                try:
                    iso_timestamp = datetime.fromtimestamp(sender_ts, tz=timezone.utc).isoformat()
                except Exception:
                    pass

            path_str = str(raw.get("path", "") or "")
            path_len = int(raw.get("path_len", 0) or 0)
            route_typename = str(raw.get("route_typename", raw.get("route_type", "FLOOD")))
            hop_nodes = []

            # Correlate with recent RX_LOG_DATA to recover the packet path if empty
            if hasattr(self, "_recent_rx_logs") and self._recent_rx_logs:
                now_s = datetime.now(timezone.utc).timestamp()
                for rx in reversed(self._recent_rx_logs):
                    if now_s - rx["time"] <= 4.5:
                        if path_len > 0 and rx["path_len"] == path_len:
                            path_str = rx["path"]
                            route_typename = rx["route_typename"]
                            hop_nodes = list(rx["hop_nodes"])
                            break
                        elif not path_str and rx["path"]:
                            path_str = rx["path"]
                            path_len = rx["path_len"]
                            route_typename = rx["route_typename"]
                            hop_nodes = list(rx["hop_nodes"])
                            break

            # If we have path_str but hop_nodes wasn't resolved yet
            if path_str and path_len > 0 and not hop_nodes:
                chunk_size = max(2, len(path_str) // path_len)
                raw_hops = [path_str[i * chunk_size : (i + 1) * chunk_size] for i in range(path_len)]
                home_lat, home_lon, home_alias = 54.65897, -3.4346, "M7NCY"
                if self.config:
                    if self.config.meshcore.latitude is not None:
                        home_lat = float(self.config.meshcore.latitude)
                    if self.config.meshcore.longitude is not None:
                        home_lon = float(self.config.meshcore.longitude)
                    if self.config.meshcore.node_alias:
                        home_alias = str(self.config.meshcore.node_alias)

                sender_c = self.storage.get_contact(sender_id) if (self.storage and sender_id) else None
                s_coord = (sender_c.latitude, sender_c.longitude) if (sender_c and sender_c.latitude is not None and sender_c.longitude is not None) else None

                if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                    chain = self.storage.resolve_hop_chain_with_candidates(
                        raw_hops,
                        sender_coord=s_coord,
                        home_coord=(home_lat, home_lon),
                        user_station_prefix=home_alias,
                        sender_name=sender_name
                    )
                    for item in chain:
                        c = item.get("contact")
                        sub_h = item.get("hash", "")
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")
                else:
                    last_ref_lat, last_ref_lon = home_lat, home_lon
                    for sub_h in raw_hops:
                        c = self.storage.get_best_contact_for_hop(sub_h, ref_lat=last_ref_lat, ref_lon=last_ref_lon, user_station_prefix=home_alias) if (self.storage and hasattr(self.storage, "get_best_contact_for_hop")) else (self.storage.get_contact(sub_h) if self.storage else None)
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                            if c.latitude is not None and c.longitude is not None:
                                last_ref_lat = float(c.latitude)
                                last_ref_lon = float(c.longitude)
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")

            msg = MessageEnvelope(
                id=msg_id,
                timestamp=iso_timestamp,
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_favorite=is_fav,
                channel=channel_name,
                channel_id=channel_idx,
                is_direct_message=False,
                text=clean_text,
                metadata={
                    "snr": snr,
                    "rssi": rssi,
                    "raw": raw_text,
                    "path": path_str,
                    "path_len": path_len,
                    "route_type": route_typename,
                    "hop_nodes": hop_nodes,
                    "repeaters": hop_nodes
                }
            )

            # Check if this incoming message is a repeater echo / loopback of our outgoing message
            now_ts = datetime.now(timezone.utc).timestamp()
            matching_out_msg = None
            for out_msg in reversed(self._recent_outgoing):
                if now_ts - out_msg["time"] <= 120.0:
                    if out_msg.get("text") == clean_text:
                        out_chan = (out_msg.get("channel") or "").lstrip("#").lower()
                        in_chan = channel_name.lstrip("#").lower()
                        if not out_chan or out_chan == in_chan:
                            matching_out_msg = out_msg
                            break

            local_alias = (self.config.meshcore.node_alias if self.config else "").strip().lower()
            local_id = (self.config.meshcore.node_id if self.config else "").strip().lower()
            is_own_sender = False
            if local_alias and local_alias not in ("heltec-v3", "local node", "local", "") and sender_name.strip().lower() == local_alias:
                is_own_sender = True
            elif local_id and (sender_id.strip().lower() == local_id or sender_id.strip().lower() == f"!{local_id}"):
                is_own_sender = True

            if matching_out_msg or is_own_sender:
                repeater_id = None
                if hop_nodes:
                    repeater_id = hop_nodes[-1]
                elif sender_id and (not local_alias or sender_id.lower() != f"!{local_alias}"):
                    repeater_id = sender_id

                if matching_out_msg:
                    logger.info(
                        f"Detected repeater echo for outgoing message '{clean_text}' on {channel_name} "
                        f"(repeater: {repeater_id}). Incrementing repeat count and suppressing duplicate chat bubble."
                    )
                    self._increment_message_repeat(matching_out_msg["msg_id"], repeater_id)
                else:
                    for out_msg in reversed(self._recent_outgoing):
                        if out_msg.get("text") == clean_text:
                            logger.info(
                                f"Detected repeater echo for local sender '{sender_name}' "
                                f"(repeater: {repeater_id}). Incrementing repeat count and suppressing duplicate."
                            )
                            self._increment_message_repeat(out_msg["msg_id"], repeater_id)
                            break

                self._recent_heard_msg_ids.add(msg_id)
                # Suppress echo: DO NOT save to storage, DO NOT emit MESSAGE_RECEIVED
                return

            if self.storage:
                self.storage.save_message(msg)
                self._dock_companion_if_gpsless(
                    sender_id=sender_id,
                    sender_name=sender_name,
                    hop_nodes=hop_nodes,
                    snr=snr,
                    timestamp=iso_timestamp,
                    channel=channel_name
                )

            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling channel msg: {e}", exc_info=True)

    def _handle_contact_msg(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            pubkey_prefix = str(raw.get("pubkey_prefix", raw.get("src", "Unknown")))
            text = str(raw.get("text", raw.get("message", "")))
            snr = float(raw.get("SNR", raw.get("snr", 0.0)))
            rssi = float(raw.get("RSSI", raw.get("rssi", -100.0)))
            sender_ts = int(raw.get("sender_timestamp", 0))

            sender_name = pubkey_prefix
            if self.client:
                contact = self.client.get_contact_by_key_prefix(pubkey_prefix)
                if contact:
                    sender_name = contact.get("adv_name") or pubkey_prefix
            if (sender_name == pubkey_prefix or not sender_name) and self.storage:
                stored = self.storage.get_contact(pubkey_prefix)
                if stored and stored.alias:
                    sender_name = stored.alias

            is_fav = False
            if self.config and self.config.is_user_favorite(pubkey_prefix, sender_name):
                is_fav = True

            iso_timestamp = datetime.now(timezone.utc).isoformat()
            if sender_ts > 0:
                try:
                    iso_timestamp = datetime.fromtimestamp(sender_ts, tz=timezone.utc).isoformat()
                except Exception:
                    pass

            path_str = str(raw.get("path", "") or "")
            path_len = int(raw.get("path_len", 0) or 0)
            route_typename = str(raw.get("route_typename", raw.get("route_type", "DIRECT")))
            hop_nodes = []

            # Correlate with recent RX_LOG_DATA to recover the packet path if empty
            if hasattr(self, "_recent_rx_logs") and self._recent_rx_logs:
                now_s = datetime.now(timezone.utc).timestamp()
                for rx in reversed(self._recent_rx_logs):
                    if now_s - rx["time"] <= 4.5:
                        if path_len > 0 and rx["path_len"] == path_len:
                            path_str = rx["path"]
                            route_typename = rx["route_typename"]
                            hop_nodes = list(rx["hop_nodes"])
                            break
                        elif not path_str and rx["path"]:
                            path_str = rx["path"]
                            path_len = rx["path_len"]
                            route_typename = rx["route_typename"]
                            hop_nodes = list(rx["hop_nodes"])
                            break

            if path_str and path_len > 0 and not hop_nodes:
                chunk_size = max(2, len(path_str) // path_len)
                raw_hops = [path_str[i * chunk_size : (i + 1) * chunk_size] for i in range(path_len)]
                home_lat, home_lon, home_alias = 54.65897, -3.4346, "M7NCY"
                if self.config:
                    if self.config.meshcore.latitude is not None:
                        home_lat = float(self.config.meshcore.latitude)
                    if self.config.meshcore.longitude is not None:
                        home_lon = float(self.config.meshcore.longitude)
                    if self.config.meshcore.node_alias:
                        home_alias = str(self.config.meshcore.node_alias)

                sender_c = self.storage.get_contact(pubkey_prefix) if (self.storage and pubkey_prefix) else None
                s_coord = (sender_c.latitude, sender_c.longitude) if (sender_c and sender_c.latitude is not None and sender_c.longitude is not None) else None

                if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                    chain = self.storage.resolve_hop_chain_with_candidates(
                        raw_hops,
                        sender_coord=s_coord,
                        home_coord=(home_lat, home_lon),
                        user_station_prefix=home_alias,
                        sender_name=sender_name
                    )
                    for item in chain:
                        c = item.get("contact")
                        sub_h = item.get("hash", "")
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")
                else:
                    last_ref_lat, last_ref_lon = home_lat, home_lon
                    for sub_h in raw_hops:
                        c = self.storage.get_best_contact_for_hop(sub_h, ref_lat=last_ref_lat, ref_lon=last_ref_lon, user_station_prefix=home_alias) if (self.storage and hasattr(self.storage, "get_best_contact_for_hop")) else (self.storage.get_contact(sub_h) if self.storage else None)
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                            if c.latitude is not None and c.longitude is not None:
                                last_ref_lat = float(c.latitude)
                                last_ref_lon = float(c.longitude)
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")

            msg = MessageEnvelope(
                id=f"dm-{int(datetime.now().timestamp()*1000)}",
                timestamp=iso_timestamp,
                source_driver="meshcore_serial",
                sender_id=pubkey_prefix,
                sender_name=sender_name,
                is_favorite=is_fav,
                is_direct_message=True,
                recipient_id="local",
                text=text,
                metadata={
                    "snr": snr,
                    "rssi": rssi,
                    "path": path_str,
                    "path_len": path_len,
                    "route_type": route_typename,
                    "hop_nodes": hop_nodes,
                    "repeaters": hop_nodes
                }
            )

            # Check if this incoming DM is an echo / loopback of our outgoing DM
            now_ts = datetime.now(timezone.utc).timestamp()
            matching_out_dm = None
            for out_msg in reversed(self._recent_outgoing):
                if now_ts - out_msg["time"] <= 120.0:
                    if out_msg.get("text") == text and out_msg.get("channel") == "DM":
                        matching_out_dm = out_msg
                        break

            local_alias = (self.config.meshcore.node_alias if self.config else "").strip().lower()
            local_id = (self.config.meshcore.node_id if self.config else "").strip().lower()
            is_own_dm_sender = False
            if local_alias and local_alias not in ("heltec-v3", "local node", "local", "") and sender_name.strip().lower() == local_alias:
                is_own_dm_sender = True
            elif local_id and (pubkey_prefix.strip().lower() == local_id or f"!{pubkey_prefix.strip().lower()}" == local_id):
                is_own_dm_sender = True

            if matching_out_dm or is_own_dm_sender:
                logger.info(f"Detected DM echo/loopback for '{text}' from {sender_name}. Suppressing duplicate chat bubble.")
                if matching_out_dm:
                    self._increment_message_repeat(matching_out_dm["msg_id"], pubkey_prefix)
                return

            if self.storage:
                self.storage.save_message(msg)
                self._dock_companion_if_gpsless(
                    sender_id=pubkey_prefix,
                    sender_name=sender_name,
                    hop_nodes=hop_nodes,
                    snr=snr,
                    timestamp=iso_timestamp,
                    channel="DM"
                )

            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling contact msg: {e}", exc_info=True)

    def _dock_companion_if_gpsless(
        self,
        sender_id: str,
        sender_name: str,
        hop_nodes: List[str],
        snr: float,
        timestamp: str,
        channel: str = "Public"
    ):
        """If sender is a companion without GPS coordinates, docks them to their first relay repeater."""
        if not self.storage or not sender_id:
            return
        try:
            clean_id = sender_id.lstrip("!")
            contact = self.storage.get_contact(clean_id) or self.storage.get_contact(sender_id)
            if not contact and sender_name:
                all_c = self.storage.get_contacts()
                for c in all_c:
                    if c.alias and c.alias.lower() == sender_name.lower():
                        contact = c
                        break

            # If contact is a repeater, repeaters do not orbit
            if contact and contact.is_repeater:
                return

            # If contact has valid GPS coordinates, do not orbit
            if contact and contact.latitude is not None and contact.longitude is not None:
                if abs(contact.latitude) > 0.0001 and abs(contact.longitude) > 0.0001:
                    return

            all_contacts = self.storage.get_contacts()
            known_reps = [
                c for c in all_contacts
                if c.is_repeater and c.latitude is not None and c.longitude is not None
                and (abs(c.latitude) > 0.0001 or abs(c.longitude) > 0.0001)
            ]

            def find_rep_in_known(target_str: str) -> Optional[NodeContact]:
                if not target_str:
                    return None
                tgt = target_str.strip().lower()
                clean_tgt = tgt.lstrip("@")
                for r in known_reps:
                    r_alias = (r.alias or "").lower()
                    r_clean = r_alias.lstrip("@")
                    r_id = (r.node_id or "").lower()
                    if r_alias == tgt or r_clean == clean_tgt or r_id == tgt or clean_tgt in r_clean:
                        return r
                return None

            rep_contact = None
            is_unknown_first = False
            orig_first_hop = ""

            if hop_nodes and len(hop_nodes) > 0:
                first_raw = str(hop_nodes[0]).strip()
                orig_first_hop = first_raw
                first_match = find_rep_in_known(first_raw)
                if first_match:
                    rep_contact = first_match
                    is_unknown_first = False
                else:
                    # First hop unknown: scan route for first known repeater
                    for hop in hop_nodes[1:]:
                        next_match = find_rep_in_known(str(hop).strip())
                        if next_match:
                            rep_contact = next_match
                            is_unknown_first = True
                            break

            # If no hops matched, only dock if node belongs to M7NCY (user's home callsign)
            if not rep_contact:
                node_clean = (sender_name or sender_id or "").lower()
                if "m7ncy" in node_clean:
                    rep_contact = find_rep_in_known("M7NCY West Yagi")
                    is_unknown_first = False
                    orig_first_hop = "M7NCY West Yagi"

            if not rep_contact:
                return

            rep_id = rep_contact.node_id
            rep_alias = rep_contact.alias or rep_id

            dock_info = DockedCompanionInfo(
                node_id=contact.node_id if contact else sender_id,
                alias=contact.alias if (contact and contact.alias) else sender_name,
                repeater_id=rep_id,
                repeater_alias=rep_alias,
                channel=channel,
                snr=snr,
                last_heard=timestamp,
                is_unknown_first_hop=is_unknown_first,
                first_hop_alias=orig_first_hop
            )
            self.storage.save_docked_companion(dock_info)
            bus.emit(EventType.MAP_NODES_UPDATED, None)
        except Exception as e:
            logger.debug(f"Error updating companion docking: {e}")

    def _handle_ack(self, event_data: Any):
        raw = self._extract_payload(event_data)
        bus.emit(EventType.MESSAGE_ACK, raw)
        now_ts = datetime.now(timezone.utc).timestamp()
        for out_msg in reversed(self._recent_outgoing):
            if now_ts - out_msg["time"] <= 60.0:
                self._increment_message_repeat(out_msg["msg_id"])
                break

    def _handle_telemetry(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            telem = TelemetryEnvelope(
                node_id=str(raw.get("node_id", "local")),
                alias=str(raw.get("alias", self.config.meshcore.node_alias if self.config else "Local Node")),
                frequency_mhz=float(raw.get("frequency_mhz", 869.618)),
                bandwidth_khz=float(raw.get("bandwidth_khz", 62.5)),
                spreading_factor=int(raw.get("spreading_factor", 8)),
                coding_rate=str(raw.get("coding_rate", "4/5")),
                tx_power_dbm=int(raw.get("tx_power_dbm", 22)),
                noise_floor_dbm=float(raw.get("noise_floor_dbm", -118.0)),
                snr_db=float(raw.get("snr", 9.5)),
                battery_pct=int(raw.get("battery_pct", 100))
            )
            if self.storage:
                self.storage.save_telemetry(telem)
            bus.emit(EventType.TELEMETRY_UPDATED, telem)
        except Exception as e:
            logger.error(f"Error handling telemetry: {e}", exc_info=True)

    def _handle_battery(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            level = raw.get("level", raw.get("percentage", 100))
            voltage = 4.15
            pct = 100
            if isinstance(level, (int, float)):
                if level > 100:  # millivolts
                    voltage = round(level / 1000.0, 2)
                    pct = max(0, min(100, int((voltage - 3.3) / (4.2 - 3.3) * 100)))
                else:
                    pct = int(level)
                    voltage = round(3.3 + (pct / 100.0) * 0.9, 2)

            latest = self.storage.get_latest_telemetry("local") if self.storage else None
            if latest:
                latest.battery_pct = pct
                latest.voltage_volts = voltage
                latest.timestamp = datetime.now(timezone.utc).isoformat()
                if self.storage:
                    self.storage.save_telemetry(latest)
                bus.emit(EventType.TELEMETRY_UPDATED, latest)
            else:
                telem = TelemetryEnvelope(
                    node_id="local",
                    alias=self.config.meshcore.node_alias if self.config else "Local Node",
                    battery_pct=pct,
                    voltage_volts=voltage
                )
                if self.storage:
                    self.storage.save_telemetry(telem)
                bus.emit(EventType.TELEMETRY_UPDATED, telem)
        except Exception as e:
            logger.error(f"Error handling battery: {e}", exc_info=True)

    def _handle_contacts(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            contacts_list = []
            neighbours_list = []
            for pubkey, c in raw.items():
                if not isinstance(c, dict):
                    continue
                pubkey = str(pubkey or "").strip().lower()
                if not is_valid_node_id(pubkey):
                    continue

                raw_alias = str(c.get("adv_name") or "").strip()
                alias = raw_alias if is_valid_alias(raw_alias) else pubkey[:8]
                existing = self.storage.get_contact(pubkey[:12]) if self.storage else None
                if not is_valid_alias(raw_alias) and existing and is_valid_alias(existing.alias):
                    alias = existing.alias

                is_rep = bool(c.get("type") == 2)
                last_seen = ""
                last_adv = c.get("last_advert") or c.get("lastmod")
                if last_adv:
                    try:
                        dt = datetime.fromtimestamp(int(last_adv), tz=timezone.utc)
                        now_utc = datetime.now(timezone.utc)
                        if 2024 <= dt.year and dt <= now_utc + timedelta(days=1):
                            last_seen = dt.isoformat()
                    except Exception:
                        pass

                is_fav = bool(self.config and self.config.is_user_favorite(pubkey[:12], alias))

                # Detect corrupt phantom shifted duplicates
                if is_rep or (alias and "rep" in alias.lower()) or (alias and alias.startswith("noc-")):
                    if self.storage:
                        all_known = self.storage.get_contacts()
                        is_corrupt_phantom = False
                        for kr in all_known:
                            if kr.is_repeater and kr.alias and kr.node_id != pubkey[:12]:
                                if len(alias) >= 5 and kr.alias.lower().startswith(alias.lower()) and kr.alias.lower() != alias.lower():
                                    logger.warning(f"Rejecting corrupt shifted phantom repeater {alias} ({pubkey[:12]}) - amputated duplicate of {kr.alias} ({kr.node_id})")
                                    is_corrupt_phantom = True
                                    break
                                if kr.node_id in pubkey[8:]:
                                    logger.warning(f"Rejecting corrupt shifted pubkey {pubkey[:12]} containing existing node ID {kr.node_id}")
                                    is_corrupt_phantom = True
                                    break
                        if is_corrupt_phantom:
                            continue

                lat_raw = c.get("adv_lat") or c.get("latitude")
                lon_raw = c.get("adv_lon") or c.get("longitude")
                lat, lon = None, None
                if is_valid_coordinate(lat_raw, lon_raw):
                    lat = float(lat_raw)
                    lon = float(lon_raw)

                # Protect existing valid coordinates from corrupted flash dumps
                if existing and existing.latitude is not None and existing.longitude is not None:
                    if not is_valid_alias(raw_alias):
                        lat = existing.latitude
                        lon = existing.longitude
                    elif (-12.0 <= existing.longitude <= 3.0 and 49.0 <= existing.latitude <= 62.0) and lon is not None and (lon > 10.0 or lon < -15.0 or lat < 45.0 or lat > 65.0):
                        logger.warning(f"Rejecting out-of-region coordinates ({lat}, {lon}) for known UK node {alias} ({pubkey[:12]})")
                        lat = existing.latitude
                        lon = existing.longitude
                out_path_len = int(c.get("out_path_len", -1) if c.get("out_path_len") is not None else -1)
                if out_path_len > 16 or out_path_len < -1:
                    out_path_len = -1
                out_path_hash_mode = int(c.get("out_path_hash_mode", -1) if c.get("out_path_hash_mode") is not None else -1)
                out_path = str(c.get("out_path", "") or "")

                contact = NodeContact(
                    node_id=pubkey[:12],
                    alias=alias,
                    is_favorite=is_fav,
                    last_seen=last_seen,
                    public_key=pubkey,
                    is_repeater=is_rep,
                    latitude=lat,
                    longitude=lon,
                    out_path_len=out_path_len,
                    out_path_hash_mode=out_path_hash_mode,
                    out_path=out_path
                )
                contacts_list.append(contact)

                if is_rep or last_adv:
                    neighbours_list.append(NeighbourInfo(
                        node_id=pubkey[:12],
                        alias=alias,
                        snr_db=0.0,
                        rssi_dbm=-85.0 if is_rep else -100.0,
                        last_heard_ts=last_seen or datetime.now(timezone.utc).isoformat(),
                        is_repeater=is_rep,
                        is_favorite=is_fav,
                        latitude=lat,
                        longitude=lon
                    ))

            if self.storage and contacts_list:
                self.storage.save_contacts_bulk(contacts_list)

            if self.storage and neighbours_list:
                for n in neighbours_list:
                    self.storage.save_neighbour(n)

            if neighbours_list:
                bus.emit(EventType.NEIGHBOURS_UPDATED, neighbours_list)
            if contacts_list:
                bus.emit(EventType.MAP_NODES_UPDATED, None)
            logger.info(f"Synchronized {len(contacts_list)} mesh contacts from node flash.")
        except Exception as e:
            logger.error(f"Error handling contacts: {e}", exc_info=True)

    def _handle_single_contact(self, event_data: Any):
        try:
            c = self._extract_payload(event_data)
            pubkey = str(c.get("public_key", "")).strip().lower()
            if not is_valid_node_id(pubkey):
                return
            raw_alias = str(c.get("adv_name") or "").strip()
            alias = raw_alias if is_valid_alias(raw_alias) else pubkey[:8]
            if not is_valid_alias(raw_alias) and self.storage:
                c_exist = self.storage.get_contact(pubkey[:12])
                if c_exist and is_valid_alias(c_exist.alias):
                    alias = c_exist.alias


            is_rep = bool(c.get("type") == 2)
            is_fav = bool(self.config and (alias in self.config.favorites or pubkey in self.config.favorites))
            lat_raw = c.get("adv_lat") or c.get("latitude")
            lon_raw = c.get("adv_lon") or c.get("longitude")
            lat, lon = None, None
            if is_valid_coordinate(lat_raw, lon_raw):
                lat = float(lat_raw)
                lon = float(lon_raw)

            # Protect existing valid coordinates from corrupted single contact updates
            c_exist = self.storage.get_contact(pubkey[:12]) if self.storage else None
            if c_exist and c_exist.latitude is not None and c_exist.longitude is not None:
                if not is_valid_alias(raw_alias):
                    lat = c_exist.latitude
                    lon = c_exist.longitude
                elif (-12.0 <= c_exist.longitude <= 3.0 and 49.0 <= c_exist.latitude <= 62.0) and lon is not None and (lon > 10.0 or lon < -15.0 or lat < 45.0 or lat > 65.0):
                    logger.warning(f"Rejecting out-of-region coordinates ({lat}, {lon}) for known UK node {alias} ({pubkey[:12]})")
                    lat = c_exist.latitude
                    lon = c_exist.longitude

            out_path_len = int(c.get("out_path_len", -1) if c.get("out_path_len") is not None else -1)
            if out_path_len > 16 or out_path_len < -1:
                out_path_len = -1
            out_path_hash_mode = int(c.get("out_path_hash_mode", -1) if c.get("out_path_hash_mode") is not None else -1)
            out_path = str(c.get("out_path", "") or "")

            contact = NodeContact(
                node_id=pubkey[:12],
                alias=alias,
                is_favorite=is_fav,
                public_key=pubkey,
                is_repeater=is_rep,
                latitude=lat,
                longitude=lon,
                out_path_len=out_path_len,
                out_path_hash_mode=out_path_hash_mode,
                out_path=out_path
            )
            if self.storage:
                self.storage.save_contact(contact)
            bus.emit(EventType.MAP_NODES_UPDATED, None)
        except Exception as e:
            logger.error(f"Error handling single contact: {e}", exc_info=True)

    def _handle_advertisement(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            pubkey = str(raw.get("public_key") or "").strip().lower()
            if not is_valid_node_id(pubkey):
                return

            alias = pubkey[:8]
            is_rep = False
            lat, lon = None, None

            c = None
            if self.client and hasattr(self.client, "get_contact_by_key_prefix"):
                try:
                    c = self.client.get_contact_by_key_prefix(pubkey)
                except Exception:
                    c = None

            adv_name = str(raw.get("adv_name") or (c.get("adv_name") if c else "") or "").strip()
            if is_valid_alias(adv_name):
                alias = adv_name
            if raw.get("type") == 2 or (c and c.get("type") == 2):
                is_rep = True

            lat_raw = raw.get("adv_lat") or raw.get("latitude") or (c.get("adv_lat") or c.get("latitude") if c else None)
            lon_raw = raw.get("adv_lon") or raw.get("longitude") or (c.get("adv_lon") or c.get("longitude") if c else None)
            home_lat, home_lon = 54.65897, -3.4346
            if self.config and hasattr(self.config, "meshcore"):
                if self.config.meshcore.latitude is not None:
                    home_lat = float(self.config.meshcore.latitude)
                if self.config.meshcore.longitude is not None:
                    home_lon = float(self.config.meshcore.longitude)

            if is_plausible_rf_coordinate(lat_raw, lon_raw, ref_lat=home_lat, ref_lon=home_lon, max_distance_km=2000.0):
                lat = float(lat_raw)
                lon = float(lon_raw)
            else:
                if lat_raw is not None and lon_raw is not None:
                    logger.warning(f"Rejecting implausible RF coordinates ({lat_raw}, {lon_raw}) for node {alias} ({pubkey[:12]})")

            # Protect existing coordinates if any
            c_exist = self.storage.get_contact(pubkey[:12]) if self.storage else None
            if c_exist and c_exist.latitude is not None and c_exist.longitude is not None:
                if not is_valid_alias(adv_name):
                    lat = c_exist.latitude
                    lon = c_exist.longitude
                elif (-12.0 <= c_exist.longitude <= 3.0 and 49.0 <= c_exist.latitude <= 62.0) and lon is not None and (lon > 10.0 or lon < -15.0 or lat < 45.0 or lat > 65.0):
                    logger.warning(f"Rejecting out-of-region coordinates ({lat}, {lon}) for known UK node {alias} ({pubkey[:12]})")
                    lat = c_exist.latitude
                    lon = c_exist.longitude

            info = NeighbourInfo(
                node_id=pubkey[:12],
                alias=alias,
                snr_db=float(raw.get("snr", 0.0)),
                rssi_dbm=float(raw.get("rssi", -100.0)),
                last_heard_ts=datetime.now(timezone.utc).isoformat(),
                is_repeater=is_rep,
                is_favorite=bool(self.config and alias in self.config.favorites),
                latitude=lat,
                longitude=lon
            )
            if self.storage:
                self.storage.save_neighbour(info)
                c_update = NodeContact(
                    node_id=pubkey[:12].lower(),
                    alias=alias if alias != pubkey[:8] else (c_exist.alias if c_exist and is_valid_alias(c_exist.alias) else alias),
                    is_favorite=bool(self.config and alias in self.config.favorites) or (c_exist.is_favorite if c_exist else False),
                    last_seen=datetime.now(timezone.utc).isoformat(),
                    public_key=pubkey.lower(),
                    is_repeater=is_rep or (c_exist.is_repeater if c_exist else False),
                    latitude=lat if lat is not None else (c_exist.latitude if c_exist else None),
                    longitude=lon if lon is not None else (c_exist.longitude if c_exist else None),
                    out_path_len=c_exist.out_path_len if c_exist else -1,
                    out_path_hash_mode=c_exist.out_path_hash_mode if c_exist else -1,
                    out_path=c_exist.out_path if c_exist else ""
                )
                self.storage.save_contact(c_update)
                if lat is not None and lon is not None:
                    bus.emit(EventType.MAP_NODES_UPDATED, None)
            bus.emit(EventType.NEIGHBOURS_UPDATED, [info])
        except Exception as e:
            logger.error(f"Error handling advertisement: {e}", exc_info=True)

    _handle_device_info = _handle_advertisement

    def _handle_rx_log_data(self, event_data: Any):
        """Processes live over-the-air packet path telemetry (Watcher)."""
        try:
            data = self._extract_payload(event_data)
            if not isinstance(data, dict):
                return
            path_str = str(data.get("path", "") or "")
            path_len = int(data.get("path_len", 0) or 0)
            route_typename = str(data.get("route_typename", "FLOOD"))
            snr = float(data.get("snr", 0.0) or 0.0)

            hop_nodes = []
            hop_coords = []
            hop_snrs = [snr]

            if path_str and path_len > 0:
                chunk_size = max(2, len(path_str) // path_len)
                raw_hops = [path_str[i * chunk_size : (i + 1) * chunk_size] for i in range(path_len)]
                home_lat, home_lon, home_alias = 54.65897, -3.4346, "M7NCY"
                if self.config:
                    if self.config.meshcore.latitude is not None:
                        home_lat = float(self.config.meshcore.latitude)
                    if self.config.meshcore.longitude is not None:
                        home_lon = float(self.config.meshcore.longitude)
                    if self.config.meshcore.node_alias:
                        home_alias = str(self.config.meshcore.node_alias)

                if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                    chain = self.storage.resolve_hop_chain_with_candidates(
                        raw_hops,
                        home_coord=(home_lat, home_lon),
                        user_station_prefix=home_alias
                    )
                    for item in chain:
                        c = item.get("contact")
                        sub_h = item.get("hash", "")
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                            if c.latitude is not None and c.longitude is not None:
                                hop_coords.append([c.latitude, c.longitude])
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")
                else:
                    last_ref_lat, last_ref_lon = home_lat, home_lon
                    for sub_h in raw_hops:
                        c = self.storage.get_best_contact_for_hop(sub_h, ref_lat=last_ref_lat, ref_lon=last_ref_lon, user_station_prefix=home_alias) if (self.storage and hasattr(self.storage, "get_best_contact_for_hop")) else (self.storage.get_contact(sub_h) if self.storage else None)
                        if c:
                            hop_nodes.append(f"@{c.alias}")
                            if c.latitude is not None and c.longitude is not None:
                                hop_coords.append([c.latitude, c.longitude])
                                last_ref_lat = float(c.latitude)
                                last_ref_lon = float(c.longitude)
                        else:
                            hop_nodes.append(f"<Unknown Repeater {sub_h}>")

            if path_str or path_len > 0:
                self._recent_rx_logs.append({
                    "time": datetime.now(timezone.utc).timestamp(),
                    "path": path_str,
                    "path_len": path_len,
                    "route_typename": route_typename,
                    "snr": snr,
                    "hop_nodes": hop_nodes,
                    "hop_coords": hop_coords
                })
                if len(self._recent_rx_logs) > 50:
                    self._recent_rx_logs.pop(0)

            raw_payload = data.get("payload", "")
            raw_hex = ""
            if isinstance(raw_payload, (bytes, bytearray)):
                raw_hex = raw_payload.hex()
            elif isinstance(raw_payload, str):
                raw_hex = raw_payload

            pkt_id = f"path-{int(datetime.now().timestamp()*1000)}"
            path_info = PacketPathInfo(
                packet_id=pkt_id,
                sender_id="mesh",
                sender_name=f"RF Packet ({route_typename})",
                timestamp=datetime.now(timezone.utc).isoformat(),
                hop_nodes=hop_nodes,
                hop_snrs=hop_snrs,
                route_type=route_typename,
                coordinates=hop_coords
            )
            if self.storage and hop_coords:
                self.storage.save_packet_path(path_info)
            bus.emit(EventType.PACKET_PATH_TRACED, path_info)

            # Check if this packet carries decrypted channel payload
            msg_text = data.get("message") or data.get("msg_text") or data.get("text")
            if msg_text and str(msg_text).strip():
                chan_name = data.get("chan_name", "")
                logger.info(f"Live OTA decrypted channel message on '{chan_name}': {str(msg_text)[:40]}")
                if "channel_idx" not in data and chan_name and self.storage:
                    ch = self.storage.get_channel(chan_name)
                    if ch:
                        data["channel_idx"] = ch.channel_id
                self._handle_channel_msg(data)

            # Live learn node advertisement and GPS coordinates over-the-air
            adv_key = str(data.get("adv_key") or "").strip().lower()
            if adv_key and is_valid_node_id(adv_key):
                raw_name = str(data.get("adv_name") or "").strip()
                # If an advertisement name was parsed but contains unprintable/corrupt chars,
                # do NOT learn from this corrupted packet.
                if raw_name and not is_valid_alias(raw_name):
                    logger.debug(f"Discarded corrupt OTA advertisement name for {adv_key}: {repr(raw_name)}")
                else:
                    node_id = adv_key[:12].lower()
                    existing = self.storage.get_contact(node_id) if self.storage else None
                    alias = raw_name or (existing.alias if (existing and is_valid_alias(existing.alias)) else adv_key[:8])
                    if is_valid_alias(alias):
                        adv_lat = data.get("adv_lat")
                        adv_lon = data.get("adv_lon")
                        adv_type = data.get("adv_type", 1)
                        is_rep = bool(adv_type == 2)

                        # Detect corrupt phantom shifted duplicates in OTA adverts
                        if is_rep or (alias and "rep" in alias.lower()) or (alias and alias.startswith("noc-")):
                            if self.storage:
                                all_known = self.storage.get_contacts()
                                is_corrupt_phantom = False
                                for kr in all_known:
                                    if kr.is_repeater and kr.alias and kr.node_id != node_id:
                                        if len(alias) >= 5 and kr.alias.lower().startswith(alias.lower()) and kr.alias.lower() != alias.lower():
                                            logger.warning(f"Rejecting corrupt shifted OTA repeater {alias} ({node_id}) - amputated duplicate of {kr.alias} ({kr.node_id})")
                                            is_corrupt_phantom = True
                                            break
                                        if kr.node_id in adv_key.lower()[8:]:
                                            logger.warning(f"Rejecting corrupt shifted OTA pubkey {node_id} containing existing node ID {kr.node_id}")
                                            is_corrupt_phantom = True
                                            break
                                if not is_corrupt_phantom:
                                    lat, lon = None, None
                                    if adv_lat is not None and adv_lon is not None:
                                        h_lat, h_lon = 54.65897, -3.4346
                                        if self.config and hasattr(self.config, "meshcore"):
                                            if self.config.meshcore.latitude is not None:
                                                h_lat = float(self.config.meshcore.latitude)
                                            if self.config.meshcore.longitude is not None:
                                                h_lon = float(self.config.meshcore.longitude)
                                        if is_plausible_rf_coordinate(adv_lat, adv_lon, ref_lat=h_lat, ref_lon=h_lon, max_distance_km=2000.0):
                                            lat = float(adv_lat)
                                            lon = float(adv_lon)
                                        else:
                                            logger.warning(
                                                f"Rejecting implausible/corrupt over-the-air GPS coordinates ({adv_lat}, {adv_lon}) "
                                                f"for node {alias or adv_key} (exceeds physical RF reach of station)"
                                            )

                                    if self.storage:
                                        fav = existing.is_favorite if existing else bool(self.config and self.config.is_user_favorite(node_id, alias))
                                        contact = NodeContact(
                                            node_id=node_id,
                                            alias=alias,
                                            is_favorite=fav,
                                            last_seen=datetime.now(timezone.utc).isoformat(),
                                            public_key=adv_key.lower(),
                                            is_repeater=is_rep or (existing.is_repeater if existing else False),
                                            latitude=lat if lat is not None else (existing.latitude if existing else None),
                                            longitude=lon if lon is not None else (existing.longitude if existing else None),
                                            out_path_len=existing.out_path_len if existing else -1,
                                            out_path_hash_mode=existing.out_path_hash_mode if existing else -1,
                                            out_path=existing.out_path if existing else ""
                                        )
                                        self.storage.save_contact(contact)
                                        if lat is not None and lon is not None:
                                            logger.info(f"Learned over-the-air GPS location for {contact.alias}: ({lat:.6f}, {lon:.6f})")
                                            bus.emit(EventType.MAP_NODES_UPDATED, None)
                                        bus.emit(EventType.NODE_DISCOVERED, contact)
                        else:
                            lat, lon = None, None
                            if adv_lat is not None and adv_lon is not None:
                                h_lat, h_lon = 54.65897, -3.4346
                                if self.config and hasattr(self.config, "meshcore"):
                                    if self.config.meshcore.latitude is not None:
                                        h_lat = float(self.config.meshcore.latitude)
                                    if self.config.meshcore.longitude is not None:
                                        h_lon = float(self.config.meshcore.longitude)
                                if is_plausible_rf_coordinate(adv_lat, adv_lon, ref_lat=h_lat, ref_lon=h_lon, max_distance_km=2000.0):
                                    lat = float(adv_lat)
                                    lon = float(adv_lon)
                                else:
                                    logger.warning(
                                        f"Rejecting implausible/corrupt over-the-air GPS coordinates ({adv_lat}, {adv_lon}) "
                                        f"for node {alias or adv_key} (exceeds physical RF reach of station)"
                                    )

                            if self.storage:
                                fav = existing.is_favorite if existing else bool(self.config and self.config.is_user_favorite(node_id, alias))
                                contact = NodeContact(
                                    node_id=node_id,
                                    alias=alias,
                                    is_favorite=fav,
                                    last_seen=datetime.now(timezone.utc).isoformat(),
                                    public_key=adv_key.lower(),
                                    is_repeater=is_rep or (existing.is_repeater if existing else False),
                                    latitude=lat if lat is not None else (existing.latitude if existing else None),
                                    longitude=lon if lon is not None else (existing.longitude if existing else None),
                                    out_path_len=existing.out_path_len if existing else -1,
                                    out_path_hash_mode=existing.out_path_hash_mode if existing else -1,
                                    out_path=existing.out_path if existing else ""
                                )
                                self.storage.save_contact(contact)
                                if lat is not None and lon is not None:
                                    logger.info(f"Learned over-the-air GPS location for {contact.alias}: ({lat:.6f}, {lon:.6f})")
                                    bus.emit(EventType.MAP_NODES_UPDATED, None)
                                bus.emit(EventType.NODE_DISCOVERED, contact)


            # Trigger message drain when an over-the-air packet arrives
            if self._running and self.is_connected():
                self._dispatch_task(self._delayed_message_drain())
        except Exception as e:
            logger.debug(f"Error in _handle_rx_log_data: {e}")

    def _handle_channel_data(self, event_data: Any):
        """Processes group channel binary/text data (PAYLOAD_TYPE_GRP_DATA)."""
        try:
            raw = self._extract_payload(event_data)
            channel_idx = int(raw.get("channel_idx", 0))
            payload_hex = str(raw.get("payload", "") or "")
            data_type = raw.get("data_type", 0)
            snr = float(raw.get("SNR", 0.0))

            try:
                text = bytes.fromhex(payload_hex).decode("utf-8")
            except Exception:
                text = f"[Data type={data_type}: {payload_hex[:32]}...]"

            channel_name = "Public" if channel_idx == 0 else f"#channel_{channel_idx}"
            if self.storage:
                channels = self.storage.get_channels()
                for ch in channels:
                    if ch.channel_id == channel_idx:
                        channel_name = ch.name
                        break

            msg = MessageEnvelope(
                id=f"grp-{int(datetime.now().timestamp()*1000)}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="meshcore_serial",
                sender_id=f"grp_chan_{channel_idx}",
                sender_name="Group Data",
                channel=channel_name,
                channel_id=channel_idx,
                is_direct_message=False,
                text=text,
                metadata={"snr": snr, "data_type": data_type, "raw_hex": payload_hex}
            )
            if self.storage:
                self.storage.save_message(msg)
            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.debug(f"Error handling channel data: {e}")

    def _handle_trace_data(self, event_data: Any):
        """Processes trace route path responses."""
        try:
            data = self._extract_payload(event_data)
            if not isinstance(data, dict):
                return
            path_hashes = data.get("path_hashes", [])
            path_snrs = [float(s) for s in data.get("path_snrs", [])]
            tag = data.get("tag", "")

            # vendor/meshcore_py returns a list of node dicts in "path": [{"hash": "..."}, ...]
            if not path_hashes and "path" in data and isinstance(data["path"], list):
                for p_item in data["path"]:
                    if isinstance(p_item, dict):
                        if "hash" in p_item:
                            path_hashes.append(p_item["hash"])
                        if "snr" in p_item:
                            path_snrs.append(float(p_item["snr"]))

            raw_hops = []
            for h in path_hashes:
                raw_hops.append(hex(h)[2:] if isinstance(h, int) else str(h))

            hop_nodes = []
            hop_coords = []
            home_lat, home_lon, home_alias = 54.65897, -3.4346, "M7NCY"
            if self.config:
                if self.config.meshcore.latitude is not None:
                    home_lat = float(self.config.meshcore.latitude)
                if self.config.meshcore.longitude is not None:
                    home_lon = float(self.config.meshcore.longitude)
                if self.config.meshcore.node_alias:
                    home_alias = str(self.config.meshcore.node_alias)

            if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                chain = self.storage.resolve_hop_chain_with_candidates(
                    raw_hops,
                    home_coord=(home_lat, home_lon),
                    user_station_prefix=home_alias
                )
                for item in chain:
                    c = item.get("contact")
                    h_str = item.get("hash", "")
                    if c:
                        hop_nodes.append(f"@{c.alias}")
                        if c.latitude and c.longitude:
                            hop_coords.append([c.latitude, c.longitude])
                    else:
                        hop_nodes.append(f"!{h_str}")
            else:
                last_ref_lat, last_ref_lon = home_lat, home_lon
                for h_str in raw_hops:
                    c = self.storage.get_best_contact_for_hop(h_str, ref_lat=last_ref_lat, ref_lon=last_ref_lon, user_station_prefix=home_alias) if (self.storage and hasattr(self.storage, "get_best_contact_for_hop")) else (self.storage.get_contact(h_str) if self.storage else None)
                    if c:
                        hop_nodes.append(f"@{c.alias}")
                        if c.latitude and c.longitude:
                            hop_coords.append([c.latitude, c.longitude])
                            last_ref_lat = float(c.latitude)
                            last_ref_lon = float(c.longitude)
                    else:
                        hop_nodes.append(f"!{h_str}")

            pkt_id = f"trace-{tag or int(datetime.now().timestamp()*1000)}"
            path_info = PacketPathInfo(
                packet_id=pkt_id,
                sender_id="trace",
                sender_name="TraceRoute",
                timestamp=datetime.now(timezone.utc).isoformat(),
                hop_nodes=hop_nodes,
                hop_snrs=path_snrs,
                route_type="TRACEROUTE",
                coordinates=hop_coords
            )
            if self.storage and hop_coords:
                self.storage.save_packet_path(path_info)
            bus.emit(EventType.PACKET_PATH_TRACED, path_info)
        except Exception as e:
            logger.debug(f"Error in _handle_trace_data: {e}")

    def _handle_path_response(self, event_data: Any):
        """Processes path discovery responses."""
        try:
            data = self._extract_payload(event_data)
            if not isinstance(data, dict):
                return
            pub_pre = data.get("pubkey_pre", "")
            out_len = data.get("out_path_len", 0)
            c = self.storage.get_contact(pub_pre) if (self.storage and pub_pre) else None
            alias = c.alias if c else pub_pre
            coords = []
            if c and c.latitude and c.longitude:
                coords.append([c.latitude, c.longitude])

            pkt_id = f"path-resp-{int(datetime.now().timestamp()*1000)}"
            path_info = PacketPathInfo(
                packet_id=pkt_id,
                sender_id=pub_pre,
                sender_name=f"Path to @{alias}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                hop_nodes=[f"@{alias} ({out_len} hops)"],
                hop_snrs=[],
                route_type="DISCOVERY",
                coordinates=coords
            )
            bus.emit(EventType.PACKET_PATH_TRACED, path_info)
        except Exception as e:
            logger.debug(f"Error in _handle_path_response: {e}")

    def _handle_neighbours(self, event_data: Any):
        try:
            raw = self._extract_payload(event_data)
            nodes = raw.get("neighbours", [])
            neighbour_list = []
            for n in nodes:
                info = NeighbourInfo(
                    node_id=str(n.get("node_id", n.get("id", ""))),
                    alias=str(n.get("alias", n.get("name", ""))),
                    snr_db=float(n.get("snr", 0.0)),
                    rssi_dbm=float(n.get("rssi", -100.0)),
                    is_repeater=bool(n.get("is_repeater", False))
                )
                if self.storage:
                    self.storage.save_neighbour(info)
                neighbour_list.append(info)

            bus.emit(EventType.NEIGHBOURS_UPDATED, neighbour_list)
        except Exception as e:
            logger.error(f"Error handling neighbours: {e}", exc_info=True)

    def _handle_cli_reply(self, event_data: Any):
        """Dispatches CLI response from repeaters or companion nodes."""
        try:
            raw = self._extract_payload(event_data)
            text = str(raw.get("text", raw.get("msg", raw.get("payload", "")))).strip()
            src = str(raw.get("src", raw.get("pubkey_prefix", "")))
            if not text:
                return
            contact = self.storage.get_contact(src) if (src and self.storage) else None
            sender_name = contact.alias if contact else (src or "Repeater")
            sender_id = contact.node_id if contact else src

            msg = MessageEnvelope(
                id=f"rep-cli-{int(datetime.now().timestamp()*1000)}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_direct_message=True,
                text=text,
                metadata={"is_repeater_response": True, "raw": raw}
            )
            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling cli reply: {e}", exc_info=True)

    def _handle_login_success(self, event_data: Any):
        """Dispatches successful login notification into console."""
        try:
            raw = self._extract_payload(event_data)
            src = str(raw.get("pubkey_prefix", raw.get("src", "")))
            contact = self.storage.get_contact(src) if (src and self.storage) else None
            sender_name = contact.alias if contact else (src or "Repeater")
            sender_id = contact.node_id if contact else src
            is_admin = raw.get("is_admin", False)
            role = "ADMIN" if is_admin else "USER"
            text = f"✅ Logged in successfully ({role})."

            msg = MessageEnvelope(
                id=f"rep-login-ok-{int(datetime.now().timestamp()*1000)}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_direct_message=True,
                text=text,
                metadata={"is_repeater_response": True, "raw": raw}
            )
            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling login success: {e}", exc_info=True)

    def _handle_login_failed(self, event_data: Any):
        """Dispatches failed login notification into console."""
        try:
            raw = self._extract_payload(event_data)
            src = str(raw.get("pubkey_prefix", raw.get("src", "")))
            contact = self.storage.get_contact(src) if (src and self.storage) else None
            sender_name = contact.alias if contact else (src or "Repeater")
            sender_id = contact.node_id if contact else src
            text = "❌ Login failed. Incorrect password or not authorized."

            msg = MessageEnvelope(
                id=f"rep-login-fail-{int(datetime.now().timestamp()*1000)}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_direct_message=True,
                text=text,
                metadata={"is_repeater_response": True, "raw": raw}
            )
            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling login failed: {e}", exc_info=True)

    def _handle_status_response(self, event_data: Any):
        """Dispatches structured status response into console."""
        try:
            raw = self._extract_payload(event_data)
            src = str(raw.get("pubkey_prefix", raw.get("src", "")))
            contact = self.storage.get_contact(src) if (src and self.storage) else None
            sender_name = contact.alias if contact else (src or "Repeater")
            sender_id = contact.node_id if contact else src

            lines = []
            if "batt_v" in raw:
                lines.append(f"Battery: {raw['batt_v']:.2f}V")
            if "temp_c" in raw:
                lines.append(f"Temp: {raw['temp_c']:.1f}°C")
            if "tx_airtime" in raw:
                lines.append(f"TX Airtime: {raw['tx_airtime']}s")
            text = " | ".join(lines) if lines else f"Status: {raw}"

            msg = MessageEnvelope(
                id=f"rep-status-{int(datetime.now().timestamp()*1000)}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_direct_message=True,
                text=text,
                metadata={"is_repeater_response": True, "raw": raw}
            )
            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling status response: {e}", exc_info=True)

    def send_channel_message(self, channel: str, text: str) -> Dict[str, Any]:
        msg_id = f"out-{int(datetime.now().timestamp()*1000)}"
        channel_clean = channel.lstrip("#")
        channel_idx = 0
        if self.storage:
            channels = self.storage.get_channels()
            for ch in channels:
                if ch.name.lower() == channel.lower() or ch.name.lstrip("#").lower() == channel_clean.lower():
                    channel_idx = ch.channel_id
                    break

        msg = MessageEnvelope(
            id=msg_id,
            source_driver="meshcore_serial",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local",
            channel=channel,
            channel_id=channel_idx,
            is_direct_message=False,
            text=text,
            is_outgoing=True,
            delivery_status="sending"
        )
        if self.storage:
            self.storage.save_message(msg)
        self._record_outgoing_message(msg_id, channel, text)
        bus.emit(EventType.MESSAGE_SENT, msg)

        # Dispatch transmission via asyncio task
        if self.client and self.is_connected():
            self._dispatch_task(self._async_send_channel(msg, channel_idx, text))

        return {"status": "ok", "message_id": msg_id}

    async def _async_send_channel(self, msg: MessageEnvelope, channel_idx: int, text: str):
        async with self._get_cmd_lock():
            try:
                res = await self.client.commands.send_chan_msg(channel_idx, text)
                if res and res.type != McEventType.ERROR:
                    msg.delivery_status = "sent"
                else:
                    msg.delivery_status = "failed"
                if self.storage:
                    self.storage.save_message(msg)
            except Exception as e:
                logger.error(f"Failed to transmit channel message: {e}")
                msg.delivery_status = "failed"
                if self.storage:
                    self.storage.save_message(msg)

    def send_direct_message(self, recipient_id: str, text: str) -> Dict[str, Any]:
        msg_id = f"dm-out-{int(datetime.now().timestamp()*1000)}"
        recipient_alias = recipient_id
        if self.storage:
            contact = self.storage.get_contact(recipient_id)
            if contact:
                recipient_alias = contact.alias

        msg = MessageEnvelope(
            id=msg_id,
            source_driver="meshcore_serial",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local",
            recipient_id=recipient_id,
            recipient_name=recipient_alias,
            is_direct_message=True,
            text=text,
            is_outgoing=True,
            delivery_status="sending"
        )
        if self.storage:
            self.storage.save_message(msg)
        self._record_outgoing_message(msg_id, "DM", text)
        bus.emit(EventType.MESSAGE_SENT, msg)

        # Dispatch transmission via asyncio task
        if self.client and self.is_connected():
            self._dispatch_task(self._async_send_dm(msg, recipient_id, text))

        return {"status": "ok", "message_id": msg_id}

    async def _async_send_dm(self, msg: MessageEnvelope, recipient_id: str, text: str):
        async with self._get_cmd_lock():
            try:
                # Resolve destination contact or public key
                dst = recipient_id
                if self.client:
                    c = self.client.get_contact_by_key_prefix(recipient_id) or self.client.get_contact_by_name(recipient_id)
                    if c:
                        dst = c
                if isinstance(dst, str) and self.storage:
                    c_stored = self.storage.get_contact(recipient_id)
                    if c_stored:
                        if self.client and c_stored.public_key:
                            c_client = self.client.get_contact_by_key_prefix(c_stored.public_key)
                            if c_client:
                                dst = c_client
                            else:
                                dst = c_stored.public_key
                        elif c_stored.public_key:
                            dst = c_stored.public_key

                res = await self.client.commands.send_msg(dst, text)
                if res and res.type != McEventType.ERROR:
                    msg.delivery_status = "sent"
                else:
                    msg.delivery_status = "failed"
                if self.storage:
                    self.storage.save_message(msg)
            except Exception as e:
                logger.error(f"Failed to transmit DM: {e}")
                msg.delivery_status = "failed"
                if self.storage:
                    self.storage.save_message(msg)

    def send_repeater_command(self, repeater_id: str, command: str) -> Dict[str, Any]:
        """Sends a CLI command or login request to a repeater node."""
        msg_id = f"rep-cmd-{int(datetime.now().timestamp()*1000)}"
        cmd = command.strip()

        # Resolve contact and full public key
        dst_pubkey = repeater_id
        if self.storage:
            c = self.storage.get_contact(repeater_id)
            if c and c.public_key and len(c.public_key) >= 64:
                dst_pubkey = c.public_key

        if self.client and hasattr(self.client, "get_contact_by_key_prefix"):
            c_client = self.client.get_contact_by_key_prefix(repeater_id) or self.client.get_contact_by_name(repeater_id)
            if c_client and isinstance(c_client, dict):
                pub = c_client.get("public_key")
                if pub and len(pub) >= 64:
                    dst_pubkey = pub

        # Log and dispatch via asyncio task
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_send_repeater_cmd(dst_pubkey, cmd, repeater_id))
            except RuntimeError:
                asyncio.create_task(self._async_send_repeater_cmd(dst_pubkey, cmd, repeater_id))

        return {"status": "ok", "message_id": msg_id}

    async def _async_send_repeater_cmd(self, dst_pubkey: str, command: str, repeater_id: str = ""):
        try:
            target_id = repeater_id or dst_pubkey[:12]
            logger.info(f"Processing repeater command for {target_id}: {command}")
            if not self.client or not hasattr(self.client, "commands"):
                return

            # Resolve contact dictionary for client commands
            contact_obj = dst_pubkey
            if hasattr(self.client, "get_contact_by_key_prefix"):
                c_client = self.client.get_contact_by_key_prefix(target_id) or self.client.get_contact_by_key_prefix(dst_pubkey)
                if c_client:
                    contact_obj = c_client
                else:
                    contact_obj = {"public_key": dst_pubkey, "type": 2}

            # Retrieve contact alias from storage
            alias = target_id
            if self.storage:
                c_store = self.storage.get_contact(target_id)
                if c_store:
                    alias = c_store.alias

            cmd_lower = command.lower().strip()

            def emit_repeater_reply(text: str, snr: Optional[float] = None, rssi: Optional[float] = None, extra_meta: Optional[Dict[str, Any]] = None):
                meta = {"is_repeater_response": True}
                if snr is not None:
                    meta["snr"] = snr
                if rssi is not None:
                    meta["rssi"] = rssi
                if extra_meta:
                    meta.update(extra_meta)
                msg = MessageEnvelope(
                    id=f"rep-res-{int(datetime.now().timestamp()*1000)}",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source_driver="meshcore_serial",
                    sender_id=target_id,
                    sender_name=alias,
                    is_direct_message=True,
                    text=text,
                    metadata=meta
                )
                bus.emit(EventType.MESSAGE_RECEIVED, msg)

            if cmd_lower.startswith("!login"):
                pwd = command[7:].strip() if cmd_lower.startswith("!login ") else ""
                login_ev = await self.client.commands.send_login_sync(contact_obj, pwd, min_timeout=6)
                if login_ev and login_ev.type == McEventType.LOGIN_SUCCESS:
                    is_admin = bool(login_ev.payload.get("is_admin", False))
                    role = "ADMIN" if is_admin else "PUBLIC / GUEST (No Password)"
                    emit_repeater_reply(f"✅ Logged in successfully to @{alias} as {role}.")

                    # Query live repeater status immediately upon login
                    try:
                        status_data = await self.client.commands.req_status_sync(contact_obj, min_timeout=5)
                        if status_data:
                            self._format_and_emit_status(status_data, target_id, alias)
                    except Exception as ex:
                        logger.warning(f"Post-login status query error: {ex}")
                elif login_ev and login_ev.type == McEventType.LOGIN_FAILED:
                    emit_repeater_reply(f"❌ Login rejected by @{alias}. Invalid password.")
                else:
                    emit_repeater_reply(f"⚠️ Login timed out. No response received from @{alias}.")

            elif cmd_lower == "!status":
                status_data = await self.client.commands.req_status_sync(contact_obj, min_timeout=6)
                if status_data:
                    self._format_and_emit_status(status_data, target_id, alias)
                else:
                    emit_repeater_reply(f"⚠️ No status response received from @{alias}.")

            elif cmd_lower in ["!bat", "!battery"]:
                status_data = await self.client.commands.req_status_sync(contact_obj, min_timeout=6)
                if status_data:
                    bat_mv = status_data.get("bat", 0)
                    bat_v = bat_mv / 1000.0 if bat_mv else 0.0
                    snr = status_data.get("last_snr")
                    rssi = status_data.get("last_rssi")
                    emit_repeater_reply(f"🔋 Battery: {bat_v:.2f}V ({bat_mv} mV)", snr=snr, rssi=rssi)
                else:
                    emit_repeater_reply(f"⚠️ No battery telemetry received from @{alias}.")

            elif cmd_lower in ["!neighbors", "!neighbours"]:
                neigh_data = await self.client.commands.req_neighbours_sync(contact_obj, min_timeout=6)
                if neigh_data and "neighbours" in neigh_data:
                    nodes = neigh_data["neighbours"]
                    total = neigh_data.get("neighbours_count", len(nodes))
                    lines = [f"🌐 Neighbors ({total} heard):"]
                    for n in nodes[:10]:
                        pk = n.get("pubkey", "????")
                        s = n.get("snr", 0)
                        sec = n.get("secs_ago", 0)
                        time_str = f"{sec//3600}h ago" if sec >= 3600 else f"{sec//60}m ago"
                        lines.append(f"  • !{pk} (SNR: {s:+.1f}dB, {time_str})")
                    if len(nodes) > 10:
                        lines.append(f"  ... and {len(nodes) - 10} more")
                    emit_repeater_reply("\n".join(lines), extra_meta={"neighbours": nodes, "neighbours_count": total})
                else:
                    emit_repeater_reply(f"⚠️ No neighbor telemetry received from @{alias}.")

            elif cmd_lower == "!path":
                path_ev = await self.client.commands.send_path_discovery_sync(contact_obj, min_timeout=6)
                if path_ev and path_ev.type == McEventType.PATH_RESPONSE:
                    p_len = path_ev.payload.get("out_path_len", 0)
                    path_desc = "Direct (Line-of-Sight, 0 intermediate hops)" if p_len == 0 else f"{p_len} hop(s)"
                    emit_repeater_reply(f"🧭 Path to @{alias}: {path_desc}")
                else:
                    emit_repeater_reply(f"⚠️ No path response received from @{alias}.")

            elif cmd_lower == "!info":
                status_data = await self.client.commands.req_status_sync(contact_obj, min_timeout=6)
                if status_data:
                    uptime_days = status_data.get("uptime", 0) // 86400
                    uptime_hrs = (status_data.get("uptime", 0) % 86400) // 3600
                    lines = [
                        f"ℹ️ Node Info: @{alias} ({target_id})",
                        f"  • Uptime: {uptime_days}d {uptime_hrs}h",
                        f"  • Packets: RX {status_data.get('nb_recv', 0):,} | TX {status_data.get('nb_sent', 0):,}",
                        f"  • Noise Floor: {status_data.get('noise_floor', -100)} dBm"
                    ]
                    emit_repeater_reply("\n".join(lines))
                else:
                    emit_repeater_reply(f"⚠️ No info response received from @{alias}.")

            else:
                # Custom CLI command (e.g. !reboot)
                res = await self.client.commands.send_cmd(contact_obj, command, dst_type=2)
                logger.info(f"Custom command sent: {res}")
        except Exception as e:
            logger.error(f"Failed to execute repeater command '{command}': {e}", exc_info=True)

    def _format_and_emit_status(self, data: dict, repeater_id: str, alias: str):
        bat_mv = data.get("bat", 0)
        bat_v = bat_mv / 1000.0 if bat_mv else 0.0
        uptime = data.get("uptime", 0)
        uptime_days = uptime // 86400
        uptime_hrs = (uptime % 86400) // 3600
        uptime_mins = (uptime % 3600) // 60
        snr = data.get("last_snr")
        rssi = data.get("last_rssi")
        airtime = data.get("airtime", 0)
        rx_cnt = data.get("nb_recv", 0)
        tx_cnt = data.get("nb_sent", 0)

        lines = [
            f"📊 Status for @{alias}:",
            f"  • Battery: {bat_v:.2f}V ({bat_mv} mV)",
            f"  • Uptime: {uptime_days}d {uptime_hrs}h {uptime_mins}m ({uptime:,}s)",
            f"  • Packets: RX {rx_cnt:,} | TX {tx_cnt:,} (Airtime: {airtime:,}s)",
        ]
        if snr is not None and rssi is not None:
            lines.append(f"  • Signal: SNR {snr:+.1f}dB | RSSI {rssi}dBm")

        meta = {"is_repeater_response": True}
        if snr is not None:
            meta["snr"] = snr
        if rssi is not None:
            meta["rssi"] = rssi

        msg = MessageEnvelope(
            id=f"rep-status-{int(datetime.now().timestamp()*1000)}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_driver="meshcore_serial",
            sender_id=repeater_id,
            sender_name=alias,
            is_direct_message=True,
            text="\n".join(lines),
            metadata=meta
        )
        bus.emit(EventType.MESSAGE_RECEIVED, msg)

    def query_telemetry(self, node_id: str = "local") -> Dict[str, Any]:
        latest = self.storage.get_latest_telemetry(node_id) if self.storage else None
        if latest:
            bus.emit(EventType.TELEMETRY_UPDATED, latest)
            return {"status": "ok", "telemetry": latest.to_dict()}
        return {"status": "ok", "message": "Query issued"}

    def query_neighbours(self, target_node_id: Optional[str] = None) -> Dict[str, Any]:
        neighbours = self.storage.get_neighbours(10) if self.storage else []
        bus.emit(EventType.NEIGHBOURS_UPDATED, neighbours)
        return {"status": "ok", "neighbours": [n.to_dict() for n in neighbours]}

    async def _async_sync_channels(self) -> Dict[str, Any]:
        """Queries and bi-directionally synchronizes channels with the hardware radio."""
        if not self.client or not self.is_connected():
            return {"status": "error", "message": "Radio not connected"}

        synced = []
        async with self._get_cmd_lock():
            bus.emit(EventType.SYNC_STATUS, {
                "stage": "channels",
                "message": "Syncing channels with radio hardware (0..7)...",
                "is_synced": False
            })
            hw_channels = {}
            try:
                for i in range(8):
                    ch_event = await self.client.commands.get_channel(i)
                    if ch_event and ch_event.type == McEventType.CHANNEL_INFO:
                        payload = ch_event.payload if isinstance(ch_event.payload, dict) else {}
                        c_name = payload.get("channel_name", "").strip()
                        if c_name:
                            hw_channels[i] = c_name
                    await asyncio.sleep(0.04)
            except Exception as e:
                logger.debug(f"Error querying initial hardware channels: {e}")

            if self.storage:
                app_channels = self.storage.get_channels()
                from hashlib import sha256
                for app_ch in app_channels:
                    slot = app_ch.channel_id
                    name = app_ch.name.strip()
                    if slot < 0 or slot > 7 or not name:
                        continue
                    hw_name = hw_channels.get(slot, "")
                    if hw_name.lower() != name.lower():
                        try:
                            logger.info(f"Syncing app channel to hardware slot {slot}: '{name}' (hardware had '{hw_name}')...")
                            res = await self.client.commands.set_channel(slot, name)
                            if res and res.type != McEventType.ERROR:
                                hw_channels[slot] = name
                                synced.append({"slot": slot, "name": name, "status": "updated"})
                                logger.info(f"Hardware slot {slot} successfully synced to '{name}'")
                            else:
                                logger.warning(f"Failed syncing channel to hardware slot {slot}: {res}")
                            await asyncio.sleep(0.12)
                        except Exception as e:
                            logger.error(f"Error pushing channel '{name}' to slot {slot}: {e}")
                    else:
                        synced.append({"slot": slot, "name": name, "status": "matching"})

                    # Ensure local packet parser has this channel registered for decryption
                    if hasattr(self.client, "_reader") and hasattr(self.client._reader, "packet_parser"):
                        try:
                            secret = sha256(name.encode("utf-8")).digest()[0:16]
                            chan_h = sha256(secret).hexdigest()[0:2]
                            await self.client._reader.packet_parser.newChannel({
                                "channel_idx": slot,
                                "channel_name": name,
                                "channel_secret": secret,
                                "channel_hash": chan_h
                            })
                        except Exception as e:
                            logger.debug(f"Error registering channel {name} in parser: {e}")

                # Also ensure any channels found on hardware that weren't in SQLite are added to SQLite
                for slot, c_name in hw_channels.items():
                    existing = self.storage.get_channel(c_name)
                    if not existing:
                        clean_c = c_name.lstrip("#")
                        is_fav = bool(self.config and self.config.is_channel_favorite(c_name))
                        ch_info = ChannelInfo(
                            channel_id=slot,
                            name=c_name,
                            is_favorite=is_fav,
                            is_pixoo_enabled=True
                        )
                        self.storage.save_channel(ch_info)

        return {"status": "ok", "synced": synced, "hw_channels": hw_channels}

    def sync_channels(self) -> Dict[str, Any]:
        """Dispatches asynchronous channel synchronization."""
        if self.client and self.is_connected():
            self._dispatch_task(self._async_sync_channels())
            return {"status": "sync_scheduled"}
        return {"status": "error", "message": "Radio hardware not connected"}

    def ensure_channel_synced(self, channel_name: str) -> bool:
        """Ensures that a channel is programmed into a radio hardware slot."""
        if not channel_name:
            return False
        clean_name = channel_name.strip()
        if clean_name.lower() == "public":
            return True
        if not self.client or not self.is_connected():
            return False
        if self.storage:
            ch = self.storage.get_channel(clean_name)
            if ch and 0 <= ch.channel_id <= 7:
                slot = ch.channel_id
                self._dispatch_task(self._async_set_channel(slot, clean_name))
                return True
        return False

    def join_channel(self, channel_name: str, slot: Optional[int] = None) -> Dict[str, Any]:
        """Joins and programs a MeshCore channel slot on the hardware radio."""
        clean_name = channel_name if channel_name.startswith("#") else f"#{channel_name}"
        if slot is None:
            # Check if channel already exists
            existing = self.storage.get_channel_by_name(clean_name) if self.storage else None
            if existing:
                slot = existing.channel_id
            else:
                used_slots = set()
                if self.storage:
                    for ch in self.storage.get_channels():
                        used_slots.add(ch.channel_id)
                for i in range(1, 8):
                    if i not in used_slots:
                        slot = i
                        break
                if slot is None:
                    slot = 1

        existing = self.storage.get_channel(clean_name) if self.storage else None
        is_fav = bool(self.config and self.config.is_channel_favorite(clean_name))
        if not is_fav and existing and existing.is_favorite:
            is_fav = True

        ch = ChannelInfo(
            channel_id=slot,
            name=clean_name,
            is_favorite=is_fav,
            is_pixoo_enabled=True if existing is None else existing.is_pixoo_enabled
        )
        if self.storage:
            self.storage.save_channel(ch)

        if self.client and self.is_connected():
            self._dispatch_task(self._async_set_channel(slot, clean_name))

        return {"status": "ok", "channel_id": slot, "name": clean_name}

    async def _async_set_channel(self, slot: int, channel_name: str):
        async with self._get_cmd_lock():
            try:
                from hashlib import sha256
                logger.info(f"Setting hardware MeshCore channel slot {slot} to '{channel_name}'...")
                res = await self.client.commands.set_channel(slot, channel_name)
                if res and res.type != McEventType.ERROR:
                    logger.info(f"Hardware channel slot {slot} successfully set to '{channel_name}'")
                else:
                    logger.error(f"Hardware error setting channel slot {slot}: {res}")

                # Register in parser for immediate over-the-air decryption
                if hasattr(self.client, "_reader") and hasattr(self.client._reader, "packet_parser"):
                    try:
                        secret = sha256(channel_name.encode("utf-8")).digest()[0:16]
                        chan_h = sha256(secret).hexdigest()[0:2]
                        await self.client._reader.packet_parser.newChannel({
                            "channel_idx": slot,
                            "channel_name": channel_name,
                            "channel_secret": secret,
                            "channel_hash": chan_h
                        })
                    except Exception as e:
                        logger.debug(f"Error registering channel {channel_name} in parser: {e}")
            except Exception as e:
                logger.error(f"Failed to set hardware channel slot {slot}: {e}")

    def set_radio_params(
        self,
        frequency_mhz: float,
        bandwidth_khz: float,
        spreading_factor: int,
        coding_rate: str,
        tx_power_dbm: int,
        path_hash_mode: Optional[int] = None
    ) -> Dict[str, Any]:
        """Sets and transmits LoRa RF parameters and byte path mode to the hardware radio."""
        cr_int = 5
        if "/" in str(coding_rate):
            try:
                cr_int = int(str(coding_rate).split("/")[1])
            except Exception:
                cr_int = 5
        elif str(coding_rate).isdigit():
            cr_int = int(coding_rate)

        # Update config
        if self.config:
            self.config.meshcore.frequency_mhz = frequency_mhz
            self.config.meshcore.bandwidth_khz = bandwidth_khz
            self.config.meshcore.spreading_factor = spreading_factor
            self.config.meshcore.coding_rate = f"4/{cr_int}"
            self.config.meshcore.tx_power_dbm = tx_power_dbm
            if path_hash_mode is not None:
                self.config.meshcore.path_hash_mode = path_hash_mode

        # Update telemetry in DB and emit
        telem = TelemetryEnvelope(
            node_id="local",
            alias=self.config.meshcore.node_alias if self.config else "Local Node",
            frequency_mhz=frequency_mhz,
            bandwidth_khz=bandwidth_khz,
            spreading_factor=spreading_factor,
            coding_rate=f"4/{cr_int}",
            tx_power_dbm=tx_power_dbm
        )
        if self.storage:
            self.storage.save_telemetry(telem)
        bus.emit(EventType.TELEMETRY_UPDATED, telem)

        # Dispatch command to hardware radio node
        if self.client and self.is_connected():
            p_mode = path_hash_mode if path_hash_mode is not None else (self.config.meshcore.path_hash_mode if self.config else 0)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._async_set_radio_and_tx(
                        frequency_mhz, bandwidth_khz, spreading_factor, cr_int, tx_power_dbm, p_mode
                    )
                )
            except RuntimeError:
                asyncio.create_task(
                    self._async_set_radio_and_tx(
                        frequency_mhz, bandwidth_khz, spreading_factor, cr_int, tx_power_dbm, p_mode
                    )
                )

        return {
            "status": "ok",
            "frequency_mhz": frequency_mhz,
            "bandwidth_khz": bandwidth_khz,
            "spreading_factor": spreading_factor,
            "coding_rate": f"4/{cr_int}",
            "tx_power_dbm": tx_power_dbm,
            "path_hash_mode": path_hash_mode
        }

    async def _async_set_radio_and_tx(
        self, freq: float, bw: float, sf: int, cr: int, tx: int, path_hash_mode: Optional[int] = None
    ):
        async with self._get_cmd_lock():
            try:
                logger.info(
                    f"Programming hardware radio: {freq} MHz, BW {bw} kHz, SF {sf}, CR 4/{cr}, TX {tx} dBm, PathMode {path_hash_mode}..."
                )
                res_radio = await self.client.commands.set_radio(freq=freq, bw=bw, sf=sf, cr=cr)
                await asyncio.sleep(0.15)
                res_tx = await self.client.commands.set_tx_power(tx)
                res_path = None
                if path_hash_mode is not None:
                    await asyncio.sleep(0.15)
                    res_path = await self.client.commands.set_path_hash_mode(path_hash_mode)
                logger.info(f"Radio programming result: radio={res_radio}, tx={res_tx}, path={res_path}")
            except Exception as e:
                logger.error(f"Failed to program radio hardware: {e}", exc_info=True)

    def set_path_hash_mode(self, mode: int) -> bool:
        """Sets hardware path hash mode (0: 1-Byte, 1: 2-Byte, 2: 3-Byte)."""
        if self.config:
            self.config.meshcore.path_hash_mode = mode
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_set_path_hash_mode(mode))
            except RuntimeError:
                asyncio.create_task(self._async_set_path_hash_mode(mode))
            return True
        return False

    async def _async_set_path_hash_mode(self, mode: int):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Programming hardware path_hash_mode to: {mode} ({mode+1} bytes/hop)...")
                res = await self.client.commands.set_path_hash_mode(mode)
                logger.info(f"Hardware path_hash_mode result: {res}")
            except Exception as e:
                logger.error(f"Failed to set path_hash_mode on hardware: {e}")

    def set_autoadd_contacts(self, enabled: bool) -> bool:
        """Toggles automatic addition of overheard contact adverts to radio flash."""
        if self.config:
            self.config.meshcore.autoadd_contacts = enabled
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_set_autoadd(enabled))
            except RuntimeError:
                asyncio.create_task(self._async_set_autoadd(enabled))
            return True
        return False

    async def _async_set_autoadd(self, enabled: bool):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Programming hardware autoadd to: {enabled}...")
                res = await self.client.commands.set_autoadd_config(1 if enabled else 0)
                logger.info(f"Hardware autoadd result: {res}")
            except Exception as e:
                logger.error(f"Failed to set autoadd on hardware: {e}")

    def set_advert_location_policy(self, policy: int) -> bool:
        """Sets location sharing policy for adverts (0: GPS, 1: Approx, 2: None)."""
        if self.config:
            self.config.meshcore.advert_loc_policy = policy
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_set_advert_loc_policy(policy))
            except RuntimeError:
                asyncio.create_task(self._async_set_advert_loc_policy(policy))
            return True
        return False

    async def _async_set_advert_loc_policy(self, policy: int):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Programming hardware advert_loc_policy to: {policy}...")
                res = await self.client.commands.set_advert_loc_policy(policy)
                logger.info(f"Hardware advert_loc_policy result: {res}")
            except Exception as e:
                logger.error(f"Failed to set advert_loc_policy on hardware: {e}")

    def set_multi_acks(self, enabled: bool) -> bool:
        """Toggles multiple ACK retries on hardware."""
        if self.config:
            self.config.meshcore.multi_acks = enabled
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_set_multi_acks(enabled))
            except RuntimeError:
                asyncio.create_task(self._async_set_multi_acks(enabled))
            return True
        return False

    async def _async_set_multi_acks(self, enabled: bool):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Programming hardware multi_acks to: {enabled}...")
                res = await self.client.commands.set_multi_acks(1 if enabled else 0)
                logger.info(f"Hardware multi_acks result: {res}")
            except Exception as e:
                logger.error(f"Failed to set multi_acks on hardware: {e}")

    def set_tuning_params(self, rx_delay_ms: int = 0, airtime_factor: int = 100) -> bool:
        """Sets radio fine tuning parameters."""
        if self.config:
            self.config.meshcore.rx_delay_ms = rx_delay_ms
        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_set_tuning(rx_delay_ms, airtime_factor))
            except RuntimeError:
                asyncio.create_task(self._async_set_tuning(rx_delay_ms, airtime_factor))
            return True
        return False

    async def _async_set_tuning(self, rx_delay_ms: int = 0, airtime_factor: int = 100):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Programming hardware tuning params: rx_delay={rx_delay_ms}ms, airtime={airtime_factor}%...")
                res = await self.client.commands.set_tuning(rx_delay_ms, airtime_factor)
                logger.info(f"Hardware tuning params result: {res}")
            except Exception as e:
                logger.error(f"Failed to set tuning params on hardware: {e}")

    def resync(self):
        """Dispatches an asynchronous node resync task."""
        if self.client and self.is_connected():
            self._dispatch_task(self._sync_initial_node_state())

    def set_node_name(self, name: str) -> Dict[str, Any]:
        """Sets and transmits node callsign/alias to the hardware radio."""
        clean_name = name.strip()
        if self.config:
            self.config.meshcore.node_alias = clean_name

        if self.client and self.is_connected():
            self._dispatch_task(self._async_set_node_name(clean_name))

        return {"status": "ok", "name": clean_name}

    async def _async_set_node_name(self, name: str):
        async with self._get_cmd_lock():
            try:
                logger.info(f"Setting hardware MeshCore node name to: '{name}'...")
                res = await self.client.commands.set_name(name)
                logger.info(f"Hardware node name updated: {res}")
            except Exception as e:
                logger.error(f"Failed to set node name on hardware: {e}")

    def send_advert(self, flood: bool = False) -> bool:
        """Broadcasts node advertisement packet (zero-hop direct or flood routed)."""
        if not self.client or not self.is_connected():
            logger.warning("Cannot send advert: radio hardware is not connected")
            return False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_send_advert(flood))
            return True
        except RuntimeError:
            try:
                asyncio.create_task(self._async_send_advert(flood))
                return True
            except RuntimeError:
                try:
                    asyncio.run(self._async_send_advert(flood))
                    return True
                except Exception as e:
                    logger.error("Failed executing advert task: %s", e)
                    return False

    async def _async_send_advert(self, flood: bool = False):
        try:
            adv_type = "Flood-routed" if flood else "Zero-hop"
            logger.info(f"Broadcasting {adv_type} node advertisement to mesh...")
            res = await self.client.commands.send_advert(flood=flood)
            logger.info(f"Broadcast advert response from node: {res}")
        except Exception as e:
            logger.error(f"Failed to broadcast node advertisement: {e}")

    def add_or_update_contact(self, contact: NodeContact) -> bool:
        """Saves contact to SQLite and asynchronously pushes it to radio hardware flash if connected."""
        if self.storage:
            self.storage.save_contact(contact)
            bus.emit(EventType.MAP_NODES_UPDATED, None)
            bus.emit(EventType.NODE_DISCOVERED, contact)

        if self.client and self.is_connected():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_add_contact_to_radio(contact))
            except RuntimeError:
                try:
                    asyncio.create_task(self._async_add_contact_to_radio(contact))
                except RuntimeError:
                    pass
        return True

    async def _async_add_contact_to_radio(self, contact: NodeContact):
        """Pushes contact update to radio hardware flash memory."""
        async with self._get_cmd_lock():
            try:
                import time
                pub_hex = (contact.public_key or "").strip()
                if not pub_hex or len(pub_hex) < 16:
                    pub_hex = (pub_hex + "0" * 64)[:64]

                contact_payload = {
                    "public_key": pub_hex,
                    "adv_name": contact.alias[:32],
                    "type": 2 if contact.is_repeater else 1,
                    "flags": 0,
                    "out_path": contact.out_path or "",
                    "out_path_len": contact.out_path_len if contact.out_path_len >= 0 else -1,
                    "out_path_hash_mode": contact.out_path_hash_mode if contact.out_path_hash_mode >= 0 else 0,
                    "last_advert": int(time.time()),
                    "adv_lat": float(contact.latitude or 0.0),
                    "adv_lon": float(contact.longitude or 0.0)
                }
                logger.info(f"Pushing contact to radio flash: {contact.alias} ({pub_hex[:12]})...")
                res = await self.client.commands.update_contact(contact_payload)
                logger.info(f"Radio update_contact response: {res}")
            except Exception as e:
                logger.warning(f"Could not push contact to radio flash: {e}")

    def sync_contacts_from_device(self) -> bool:
        """Triggers a re-query of all contacts stored in node flash."""
        if not self.client or not self.is_connected():
            return False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_sync_contacts())
            return True
        except RuntimeError:
            try:
                asyncio.create_task(self._async_sync_contacts())
                return True
            except RuntimeError:
                return False

    async def _async_sync_contacts(self):
        try:
            logger.info("Re-syncing mesh contacts from node flash...")
            contacts_event = await self.client.commands.get_contacts()
            if contacts_event and contacts_event.type != McEventType.ERROR:
                self._handle_contacts(contacts_event)
        except Exception as e:
            logger.error(f"Failed re-syncing contacts: {e}")
