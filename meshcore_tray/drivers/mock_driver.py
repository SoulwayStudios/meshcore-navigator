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

    def connect(self):
        """Simulates connecting the mock radio."""
        self._running = True
        self._connected = True
        bus.emit(EventType.CONNECTION_STATUS_CHANGED, {
            "connected": True,
            "port": "SIMULATOR",
            "mode": "mock",
            "message": "Connected (Mock Radio Engine)"
        })
        self.query_telemetry()
        self.query_neighbours()

    def reconnect(self):
        """Forces an immediate reconnection cycle for mock driver."""
        self.connect()


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

        # In mock simulator mode, simulate hearing 1 repeater rebroadcast after 1.5s
        def _sim_repeat(target_id: str):
            import time
            time.sleep(1.5)
            if self.storage:
                self.storage.update_message_repeats(target_id, 1)
                updated = self.storage.get_message(target_id)
                if updated:
                    bus.emit(EventType.MESSAGE_UPDATED, updated)

        import threading
        threading.Thread(target=_sim_repeat, args=(msg_id,), daemon=True).start()

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
            NeighbourInfo(node_id="!rm01", alias="North-BBS [Room]", snr_db=9.5, rssi_dbm=-82.0, is_repeater=False, is_room_server=True, is_favorite=False, latitude=54.662, longitude=-3.428),
        ]
        if self.storage:
            for n in neighbours:
                self.storage.save_neighbour(n)
            self.storage.save_contact(NodeContact(
                node_id="!rm01",
                alias="North-BBS [Room]",
                is_room_server=True,
                latitude=54.662,
                longitude=-3.428,
                snr_db=9.5,
                rssi_dbm=-82.0
            ))
        bus.emit(EventType.NEIGHBOURS_UPDATED, neighbours)
        return {"status": "ok", "neighbours": [n.to_dict() for n in neighbours]}

    def join_channel(self, channel_name: str, slot: Optional[int] = None) -> Dict[str, Any]:
        clean_name = channel_name if channel_name.startswith("#") else f"#{channel_name}"
        if slot is None:
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
        return {"status": "ok", "channel_id": slot, "name": clean_name}

    def set_radio_params(
        self,
        frequency_mhz: float,
        bandwidth_khz: float,
        spreading_factor: int,
        coding_rate: str,
        tx_power_dbm: int,
        path_hash_mode: Optional[int] = None
    ) -> Dict[str, Any]:
        if self.config:
            self.config.meshcore.frequency_mhz = frequency_mhz
            self.config.meshcore.bandwidth_khz = bandwidth_khz
            self.config.meshcore.spreading_factor = spreading_factor
            self.config.meshcore.coding_rate = coding_rate
            self.config.meshcore.tx_power_dbm = tx_power_dbm
            if path_hash_mode is not None:
                self.config.meshcore.path_hash_mode = path_hash_mode

        telem = TelemetryEnvelope(
            node_id="local",
            alias=self.config.meshcore.node_alias if self.config else "Simulator",
            frequency_mhz=frequency_mhz,
            bandwidth_khz=bandwidth_khz,
            spreading_factor=spreading_factor,
            coding_rate=coding_rate,
            tx_power_dbm=tx_power_dbm
        )
        if self.storage:
            self.storage.save_telemetry(telem)
        bus.emit(EventType.TELEMETRY_UPDATED, telem)
        return {"status": "ok", "frequency_mhz": frequency_mhz, "bandwidth_khz": bandwidth_khz, "spreading_factor": spreading_factor, "coding_rate": coding_rate, "tx_power_dbm": tx_power_dbm}

    def resync(self):
        bus.emit(EventType.SYNC_STATUS, {
            "stage": "complete",
            "message": "✓ Simulator Up to date (Mock Driver)",
            "is_synced": True
        })

    def set_node_name(self, name: str) -> Dict[str, Any]:
        clean_name = name.strip()
        if self.config:
            self.config.meshcore.node_alias = clean_name
        return {"status": "ok", "name": clean_name}

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

    def send_repeater_command(self, repeater_id: str, command: str) -> Dict[str, Any]:
        """Simulates response to repeater CLI commands in mock mode."""
        msg_id = f"mock-cmd-{int(datetime.now().timestamp()*1000)}"
        cmd = command.strip()
        if cmd.lower().startswith("!login"):
            resp_text = "✅ Logged in successfully (ADMIN)."
        elif cmd.lower() == "!status":
            resp_text = "Battery: 4.15V | Temp: 21.4°C | TX Airtime: 14s | Ch Util: 2.1%"
        elif cmd.lower() in ["!neighbors", "!neighbours"]:
            lines = [
                "🌐 Neighbors (3 heard):",
                "  • !8f3a (SNR: +12.0dB, 5m ago)",
                "  • !ff01 (SNR: -3.5dB, 22m ago)",
                "  • !3705 (SNR: +8.2dB, 1h ago)"
            ]
            resp_text = "\n".join(lines)
        elif cmd.lower() == "!info":
            resp_text = "Firmware: MeshCore-Companion v1.10.0 | Uptime: 4d 12h"
        else:
            resp_text = f"ACK: Command '{cmd}' executed."

        resp_msg = MessageEnvelope(
            id=f"mock-rep-res-{int(datetime.now().timestamp()*1000)}",
            source_driver="mock_radio",
            sender_id=repeater_id,
            sender_name="MockRepeater",
            is_direct_message=True,
            text=resp_text,
            metadata={"is_repeater_response": True}
        )
        bus.emit(EventType.MESSAGE_RECEIVED, resp_msg)
        return {"status": "ok", "message_id": msg_id}

    def send_advert(self, flood: bool = False) -> bool:
        adv_type = "Flood-routed" if flood else "Zero-hop"
        logger.info(f"[MockRadioDriver] Broadcasted {adv_type} node advert")
        return True

    def send_room_login(self, node_id: str, password: str) -> Dict[str, Any]:
        """Simulates room server login in mock mode."""
        if self.storage:
            self.storage.set_room_password(node_id, password)
            self.storage.set_contact_room_server_status(node_id, True)

        msg_id = f"mock-room-login-{int(datetime.now().timestamp()*1000)}"
        resp_msg = MessageEnvelope(
            id=f"mock-room-res-{int(datetime.now().timestamp()*1000)}",
            source_driver="mock_radio",
            sender_id=node_id,
            sender_name="North-BBS [Room]",
            is_direct_message=True,
            text="✅ Welcome to North-BBS Room Server!\nAuthenticated successfully. Type !help for commands or !read to catch up on latest mesh posts.",
            metadata={"is_room_response": True, "snr": 9.5, "rssi": -82.0}
        )
        if self.storage:
            self.storage.save_message(resp_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, resp_msg)
        return {"status": "ok", "message_id": msg_id}

    def send_room_logout(self, node_id: str) -> Dict[str, Any]:
        """Simulates room server logout in mock mode."""
        msg_id = f"mock-room-logout-{int(datetime.now().timestamp()*1000)}"
        resp_msg = MessageEnvelope(
            id=f"mock-room-res-{int(datetime.now().timestamp()*1000)}",
            source_driver="mock_radio",
            sender_id=node_id,
            sender_name="North-BBS [Room]",
            is_direct_message=True,
            text="👋 Logged out from North-BBS Room Server. Goodbye!",
            metadata={"is_room_response": True}
        )
        if self.storage:
            self.storage.save_message(resp_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, resp_msg)
        return {"status": "ok", "message_id": msg_id}

    def send_room_command(self, node_id: str, command: str) -> Dict[str, Any]:
        """Simulates room server command responses in mock mode."""
        msg_id = f"mock-room-cmd-{int(datetime.now().timestamp()*1000)}"
        cmd = command.strip().lower()
        if cmd == "!help":
            resp_text = (
                "📖 North-BBS Room Server Commands:\n"
                "  • !help   - Show this command reference\n"
                "  • !info   - Room server stats & uptime\n"
                "  • !status - System health & battery\n"
                "  • !read   - Read latest 5 bulletin posts\n"
                "  • !list   - List active chat channels in this room\n"
                "  • Send any text to post to the public bulletin stream."
            )
        elif cmd == "!info":
            resp_text = "🏢 North-BBS [Room] | Software: MeshCore RoomServer v1.4 | Max Users: 64 | Retention: 7 days"
        elif cmd == "!status":
            resp_text = "🔋 Power: Solar Float (13.8V) | Storage: 4.2MB / 16MB | Messages Stored: 142"
        elif cmd == "!read":
            resp_text = "📢 Latest Posts:\n1. [Alice] Weather station on Scafell Pike operational.\n2. [Bob] Gateway testing complete on 868.125MHz.\n3. [Sysop] BBS database backed up."
        elif cmd == "!list":
            resp_text = "📁 Active Rooms:\n  #general (Main discussion)\n  #alerts (Emergency traffic)\n  #tech (RF & antenna chatter)"
        else:
            resp_text = f"ACK: Room received command '{command}'."

        resp_msg = MessageEnvelope(
            id=f"mock-room-res-{int(datetime.now().timestamp()*1000)}",
            source_driver="mock_radio",
            sender_id=node_id,
            sender_name="North-BBS [Room]",
            is_direct_message=True,
            text=resp_text,
            metadata={"is_room_response": True, "snr": 9.5, "rssi": -82.0}
        )
        if self.storage:
            self.storage.save_message(resp_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, resp_msg)
        return {"status": "ok", "message_id": msg_id}

    def send_room_message(self, node_id: str, text: str) -> Dict[str, Any]:
        """Simulates sending a message to the room bulletin stream."""
        return self.send_direct_message(node_id, text)

