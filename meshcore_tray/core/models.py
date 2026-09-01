"""Normalized Data Models for MeshCore Pixoo Tray."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional


def current_iso_time() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MessageEnvelope:
    """Standardized message format used across all drivers, storage, UI, and external sinks."""
    id: str
    timestamp: str = field(default_factory=current_iso_time)
    source_driver: str = "meshcore_serial"
    sender_id: str = ""
    sender_name: str = "Anonymous"
    is_favorite: bool = False
    channel: str = "Public"
    channel_id: int = 0
    is_direct_message: bool = False
    recipient_id: Optional[str] = None
    recipient_name: Optional[str] = None
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    delivery_status: str = "received"  # received, pending, sent, acked, failed
    is_outgoing: bool = False
    is_mention: bool = False
    matched_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageEnvelope":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TelemetryEnvelope:
    """Normalized Radio RF telemetry parameters."""
    timestamp: str = field(default_factory=current_iso_time)
    node_id: str = "local"
    alias: str = "Local Node"
    frequency_mhz: float = 868.125
    bandwidth_khz: float = 250.0
    spreading_factor: int = 7
    coding_rate: str = "4/5"
    tx_power_dbm: int = 22
    noise_floor_dbm: float = -118.0
    snr_db: float = 9.5
    rssi_dbm: float = -85.0
    battery_pct: int = 100
    voltage_volts: float = 4.15

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TelemetryEnvelope":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class NeighbourInfo:
    """Neighbor heard node information and signal metrics."""
    node_id: str
    alias: str = ""
    snr_db: float = 0.0
    rssi_dbm: float = -100.0
    last_heard_ts: str = field(default_factory=current_iso_time)
    is_repeater: bool = False
    is_favorite: bool = False
    via_node_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NeighbourInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class NodeContact:
    """Contact profile for DM or known mesh node."""
    node_id: str
    alias: str = ""
    is_favorite: bool = False
    last_seen: str = field(default_factory=current_iso_time)
    public_key: str = ""
    is_repeater: bool = False
    snr_db: float = 0.0
    rssi_dbm: float = -100.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeContact":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ChannelInfo:
    """Channel definition and filter settings."""
    channel_id: int
    name: str
    is_favorite: bool = False
    is_pixoo_enabled: bool = True
    last_activity_ts: str = field(default_factory=current_iso_time)
    unread_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class CommandPacket:
    """Standard command packet for dispatching messages, radio queries, or external control."""
    command_type: str  # SEND_MSG, SEND_DM, QUERY_TELEMETRY, QUERY_NEIGHBOURS, SET_RADIO
    payload: Dict[str, Any] = field(default_factory=dict)
    target: Optional[str] = None
    request_id: str = field(default_factory=lambda: f"req-{int(datetime.now().timestamp() * 1000)}")

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "CommandPacket":
        return cls(**json.loads(json_str))
