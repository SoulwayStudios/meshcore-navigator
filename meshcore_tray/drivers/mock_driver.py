"""Mock Radio Driver for Simulation, Testing, and Offline Demonstrations."""

import asyncio
from datetime import datetime, timezone
import logging
import random
from typing import Any, Dict, Optional
from meshcore_tray.core.models import (
    ChannelInfo, MessageEnvelope, NeighbourInfo, NodeContact, TelemetryEnvelope
)
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.base_driver import BaseRadioDriver

logger = logging.getLogger("meshcore_tray.mock_driver")


class MockRadioDriver(BaseRadioDriver):
    """Simulates realistic Heltec V3 mesh radio behavior."""

    def __init__(self, config=None, storage=None):
        self.config = config
        self.storage = storage
        self._connected = False
        self._running = False
        self._sim_task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._connected = True
        logger.info("Started Mock Radio Driver in simulation mode.")
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": True,
            "port": "SIMULATOR",
            "mode": "mock",
            "message": "Connected (Mock Radio Engine)"
        })
        # Emit initial telemetry and neighbours
        self.query_telemetry()
        self.query_neighbours()

    async def stop(self):
        self._running = False
        self._connected = False
        if self._sim_task:
            self._sim_task.cancel()
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": False,
            "port": "SIMULATOR",
            "mode": "mock",
            "message": "Disconnected"
        })

    def is_connected(self) -> bool:
        return self._connected

    def send_channel_message(self, channel: str, text: str) -> Dict[str, Any]:
        msg_id = f"mock-out-{int(datetime.now().timestamp()*1000)}"
        msg = MessageEnvelope(
            id=msg_id,
            source_driver="mock_radio",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local Node",
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
        msg_id = f"mock-dm-out-{int(datetime.now().timestamp()*1000)}"
        msg = MessageEnvelope(
            id=msg_id,
            source_driver="mock_radio",
            sender_id=self.config.meshcore.node_id if self.config else "!local",
            sender_name=self.config.meshcore.node_alias if self.config else "Local Node",
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
            noise_floor_dbm=-118.0,
            snr_db=round(random.uniform(7.0, 11.5), 1),
            rssi_dbm=round(random.uniform(-92.0, -78.0), 1),
            battery_pct=95,
            voltage_volts=4.12
        )
        if self.storage:
            self.storage.save_telemetry(telem)
        bus.emit(EventType.TELEMETRY_UPDATED, telem)
        return {"status": "ok", "telemetry": telem.to_dict()}

    def query_neighbours(self, target_node_id: Optional[str] = None) -> Dict[str, Any]:
        neighbours = [
            NeighbourInfo(node_id="!8f3a", alias="Alice-Rep", snr_db=11.2, rssi_dbm=-75.0, is_repeater=True, is_favorite=True),
            NeighbourInfo(node_id="!9c21", alias="Bob-Node", snr_db=4.5, rssi_dbm=-88.0, is_repeater=False, is_favorite=False),
            NeighbourInfo(node_id="!10a4", alias="Charlie-Base", snr_db=-3.2, rssi_dbm=-108.0, is_repeater=False, is_favorite=False),
            NeighbourInfo(node_id="!ff01", alias="Hilltop-Repeater", snr_db=8.4, rssi_dbm=-82.0, is_repeater=True, is_favorite=True),
        ]
        if self.storage:
            for n in neighbours:
                self.storage.save_neighbour(n)
        bus.emit(EventType.NEIGHBOURS_UPDATED, neighbours)
        return {"status": "ok", "neighbours": [n.to_dict() for n in neighbours]}

    def inject_incoming_message(
        self,
        sender_name: str = "Alice",
        sender_id: str = "!8f3a",
        channel: str = "Public",
        text: str = "Hello mesh! Quick radio check on 868MHz.",
        is_favorite: bool = False
    ) -> MessageEnvelope:
        """Helper to inject an incoming simulated packet into the system."""
        msg = MessageEnvelope(
            id=f"sim-{int(datetime.now().timestamp()*1000)}",
            source_driver="mock_radio",
            sender_id=sender_id,
            sender_name=sender_name,
            is_favorite=is_favorite or (self.config and sender_name in self.config.favorites),
            channel=channel,
            is_direct_message=False,
            text=text,
            metadata={"snr": round(random.uniform(5.0, 12.0), 1), "rssi": round(random.uniform(-90.0, -70.0), 1)}
        )
        if self.storage:
            self.storage.save_message(msg)
        bus.emit(EventType.MESSAGE_RECEIVED, msg)
        return msg
