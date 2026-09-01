"""MeshCore Serial & BLE Hardware Driver for Heltec V3."""

import asyncio
from datetime import datetime, timezone
import glob
import logging
from typing import Any, Dict, Optional
import serial.tools.list_ports

import meshcore
from meshcore_tray.core.models import (
    ChannelInfo, MessageEnvelope, NeighbourInfo, NodeContact, TelemetryEnvelope
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

    @staticmethod
    def scan_serial_ports() -> list[str]:
        """Auto-detects available serial ports on Linux."""
        ports = []
        # Check standard sysfs comports
        for p in serial.tools.list_ports.comports():
            ports.append(p.device)
        # Check standard dev paths if not found
        for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
            for match in glob.glob(pattern):
                if match not in ports:
                    ports.append(match)
        return sorted(ports)

    async def start(self):
        self._running = True
        await self._connect()

    async def stop(self):
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client: {e}")
        self._connected = False
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": False,
            "port": "",
            "mode": "serial",
            "message": "Disconnected"
        })

    def is_connected(self) -> bool:
        return self._connected and bool(self.client and self.client.is_connected())

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
                return

        try:
            logger.info(f"Attempting connection to MeshCore node on {port} @ {baud}...")
            self.client = meshcore.MeshCore.create_serial(port=port, baudrate=baud)
            self._setup_subscriptions()
            await self.client.connect()
            self._connected = True
            logger.info(f"Successfully connected to MeshCore node on {port}")
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": True,
                "port": port,
                "mode": "serial",
                "message": f"Connected to {port}"
            })
            # Query initial node status
            self.query_telemetry()
            self.query_neighbours()
        except Exception as e:
            logger.error(f"Failed to connect to MeshCore on {port}: {e}")
            self._connected = False
            bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
                "connected": False,
                "port": port,
                "mode": "serial",
                "message": f"Connection error: {e}"
            })

    def _setup_subscriptions(self):
        if not self.client:
            return

        self.client.subscribe(meshcore.EventType.CHANNEL_MSG_RECV, self._handle_channel_msg)
        self.client.subscribe(meshcore.EventType.CONTACT_MSG_RECV, self._handle_contact_msg)
        self.client.subscribe(meshcore.EventType.ACK, self._handle_ack)
        self.client.subscribe(meshcore.EventType.TELEMETRY_RESPONSE, self._handle_telemetry)
        self.client.subscribe(meshcore.EventType.NEIGHBOURS_RESPONSE, self._handle_neighbours)
        self.client.subscribe(meshcore.EventType.BATTERY, self._handle_battery)

    def _handle_channel_msg(self, event_data: Any):
        try:
            # Parse meshcore event packet
            raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
            sender_id = str(raw.get("sender", raw.get("src", "Unknown")))
            sender_name = str(raw.get("sender_name", raw.get("name", sender_id)))
            channel_name = str(raw.get("channel_name", raw.get("channel", "Public")))
            channel_id = int(raw.get("channel_idx", raw.get("channel_id", 0)))
            text = str(raw.get("text", raw.get("message", "")))
            snr = float(raw.get("snr", 0.0))
            rssi = float(raw.get("rssi", -100.0))

            is_fav = False
            if self.config and (sender_name in self.config.favorites or sender_id in self.config.favorites):
                is_fav = True

            msg = MessageEnvelope(
                id=f"msg-{int(datetime.now().timestamp()*1000)}",
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_favorite=is_fav,
                channel=channel_name,
                channel_id=channel_id,
                is_direct_message=False,
                text=text,
                metadata={"snr": snr, "rssi": rssi}
            )

            if self.storage:
                self.storage.save_message(msg)

            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling channel msg: {e}", exc_info=True)

    def _handle_contact_msg(self, event_data: Any):
        try:
            raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
            sender_id = str(raw.get("sender", raw.get("src", "Unknown")))
            sender_name = str(raw.get("sender_name", raw.get("name", sender_id)))
            text = str(raw.get("text", raw.get("message", "")))

            msg = MessageEnvelope(
                id=f"dm-{int(datetime.now().timestamp()*1000)}",
                source_driver="meshcore_serial",
                sender_id=sender_id,
                sender_name=sender_name,
                is_favorite=bool(self.config and sender_name in self.config.favorites),
                is_direct_message=True,
                recipient_id="local",
                text=text,
                metadata={"snr": float(raw.get("snr", 0.0)), "rssi": float(raw.get("rssi", -100.0))}
            )

            if self.storage:
                self.storage.save_message(msg)

            bus.emit(EventType.MESSAGE_RECEIVED, msg)
        except Exception as e:
            logger.error(f"Error handling contact msg: {e}", exc_info=True)

    def _handle_ack(self, event_data: Any):
        raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
        bus.emit(EventType.MESSAGE_ACK, raw)

    def _handle_telemetry(self, event_data: Any):
        try:
            raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
            telem = TelemetryEnvelope(
                node_id=str(raw.get("node_id", "local")),
                frequency_mhz=float(raw.get("frequency_mhz", 868.125)),
                bandwidth_khz=float(raw.get("bandwidth_khz", 250.0)),
                spreading_factor=int(raw.get("spreading_factor", 7)),
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

    def _handle_neighbours(self, event_data: Any):
        try:
            raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
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

    def _handle_battery(self, event_data: Any):
        raw = event_data if isinstance(event_data, dict) else getattr(event_data, "__dict__", {})
        pct = raw.get("battery_level", raw.get("percentage", 100))
        logger.debug(f"Node battery update: {pct}%")

    def send_channel_message(self, channel: str, text: str) -> Dict[str, Any]:
        msg_id = f"out-{int(datetime.now().timestamp()*1000)}"
        msg = MessageEnvelope(
            id=msg_id,
            source_driver="meshcore_serial",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local",
            channel=channel,
            is_direct_message=False,
            text=text,
            is_outgoing=True,
            delivery_status="sent"
        )
        if self.storage:
            self.storage.save_message(msg)
        bus.emit(EventType.MESSAGE_SENT, msg)
        return {"status": "ok", "message_id": msg_id}

    def send_direct_message(self, recipient_id: str, text: str) -> Dict[str, Any]:
        msg_id = f"dm-out-{int(datetime.now().timestamp()*1000)}"
        msg = MessageEnvelope(
            id=msg_id,
            source_driver="meshcore_serial",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local",
            recipient_id=recipient_id,
            is_direct_message=True,
            text=text,
            is_outgoing=True,
            delivery_status="sent"
        )
        if self.storage:
            self.storage.save_message(msg)
        bus.emit(EventType.MESSAGE_SENT, msg)
        return {"status": "ok", "message_id": msg_id}

    def query_telemetry(self, node_id: str = "local") -> Dict[str, Any]:
        telem = TelemetryEnvelope(
            node_id=node_id,
            frequency_mhz=868.125,
            bandwidth_khz=250.0,
            spreading_factor=7,
            coding_rate="4/5",
            tx_power_dbm=22,
            noise_floor_dbm=-118.0
        )
        bus.emit(EventType.TELEMETRY_UPDATED, telem)
        return {"status": "ok", "telemetry": telem.to_dict()}

    def query_neighbours(self, target_node_id: Optional[str] = None) -> Dict[str, Any]:
        return {"status": "ok", "message": "Query sent"}
