"""Normalized Data Models for MeshCore Pixoo Tray."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Union


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
    repeats_heard: int = 0

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
    is_room_server: bool = False
    is_favorite: bool = False
    via_node_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NeighbourInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def is_valid_alias(alias: Optional[str]) -> bool:
    """Validates that a node alias contains human-readable characters and no corrupt binary control chars."""
    if not alias or not isinstance(alias, str):
        return False
    alias_str = alias.strip()
    if not alias_str:
        return False
    # Reject non-printable ASCII/Latin control characters (0x00-0x1F, 0x7F-0x9F)
    for ch in alias_str:
        o = ord(ch)
        if o < 32 or (127 <= o <= 159):
            return False
    # Reject strings that are merely mangled packet path fragments (e.g., '> > > >' or '>/4%,')
    stripped = alias_str.replace(">", "").replace(" ", "").replace("/", "").replace("%", "").replace(",", "").strip()
    if len(stripped) == 0:
        return False
    # Maximum reasonable alias length in MeshCore is 40 characters
    if len(alias_str) > 64:
        return False
    return True


def is_valid_node_id(node_id: Optional[str]) -> bool:
    """Validates that a node ID is not empty or all zeroes/corrupt."""
    if not node_id or not isinstance(node_id, str):
        return False
    clean = node_id.strip().lstrip("!@")
    if not clean or clean.replace("0", "") == "":
        return False
    # Node ID in MeshCore is hex (or callsign-like), usually 8-16 chars; reject control chars
    for ch in clean:
        o = ord(ch)
        if o < 32 or (127 <= o <= 159):
            return False
    return True


def is_valid_coordinate(lat: Optional[Union[float, int, str]], lon: Optional[Union[float, int, str]]) -> bool:
    """Validates that coordinates are legitimate numbers and not Null Island / equatorial ocean / 0,0 corrupted values."""
    if lat is None or lon is None:
        return False
    try:
        flat = float(lat)
        flon = float(lon)
    except (ValueError, TypeError):
        return False
    # Check standard range
    if not (-85.0 <= flat <= 85.0 and -180.0 <= flon <= 180.0):
        return False
    # Reject Null Island and uninitialized/corrupt equatorial ocean coordinates (Gulf of Guinea)
    if abs(flat) < 0.001:
        return False
    if abs(flat) < 1.0 and -10.0 <= flon <= 6.0:
        return False
    return True


def calculate_haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance in km between two GPS coordinates."""
    import math
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def is_plausible_rf_coordinate(
    lat: Optional[Union[float, int, str]],
    lon: Optional[Union[float, int, str]],
    ref_lat: Optional[float] = None,
    ref_lon: Optional[float] = None,
    max_distance_km: float = 2000.0,
) -> bool:
    """Validates that a coordinate is structurally valid and within plausible physical RF range of reference station."""
    if not is_valid_coordinate(lat, lon):
        return False
    if ref_lat is not None and ref_lon is not None and is_valid_coordinate(ref_lat, ref_lon):
        dist = calculate_haversine_distance_km(float(ref_lat), float(ref_lon), float(lat), float(lon))
        if dist > max_distance_km:
            return False
    return True



@dataclass
class NodeContact:
    """Contact profile for DM or known mesh node."""
    node_id: str
    alias: str = ""
    is_favorite: bool = False
    last_seen: str = field(default_factory=current_iso_time)
    public_key: str = ""
    is_repeater: bool = False
    is_room_server: bool = False
    snr_db: float = 0.0
    rssi_dbm: float = -100.0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    out_path_len: int = -1
    out_path_hash_mode: int = -1
    out_path: str = ""
    scope_name: Optional[str] = None
    allowed_regions: Optional[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeContact":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def is_room_server_contact(contact: Optional[Union[NodeContact, dict]]) -> bool:
    """Helper to detect if a contact represents a Room Server (by flag, type code, or alias tag)."""
    if not contact:
        return False
    if isinstance(contact, dict):
        if contact.get("is_room_server"):
            return True
        if contact.get("type") == 3:
            return True
        alias = str(contact.get("alias") or contact.get("adv_name") or "").lower()
    else:
        if getattr(contact, "is_room_server", False):
            return True
        alias = (contact.alias or "").lower()
    return "[room]" in alias or "[server]" in alias or alias.endswith("-bbs") or "-room" in alias


@dataclass
class DockedCompanionInfo:
    """Represents a companion node without GPS docked to its relay repeater."""
    node_id: str
    alias: str = ""
    repeater_id: str = ""
    repeater_alias: str = ""
    channel: str = ""
    snr: float = 0.0
    last_heard: str = field(default_factory=current_iso_time)
    is_unknown_first_hop: bool = False
    first_hop_alias: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DockedCompanionInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PacketPathInfo:
    """Multi-hop trace route path for watcher maps."""
    packet_id: str
    sender_id: str
    sender_name: str
    recipient_id: str = ""
    timestamp: str = field(default_factory=current_iso_time)
    hop_nodes: List[str] = field(default_factory=list)           # List of node IDs / aliases in hop sequence
    hop_snrs: List[float] = field(default_factory=list)          # SNR at each hop
    route_type: str = "FLOOD"                                    # "DIRECT", "FLOOD", "ROUTED"
    coordinates: List[List[float]] = field(default_factory=list) # [[lat, lon], ...] along the path

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PacketPathInfo":
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
