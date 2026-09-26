"""MeshCore Binary Packet Decoder & AES-128 Channel Decryptor.

Complete wire-level parser for all 13 MeshCore packet payload types, route modes,
hop sequences, and channel payload decryption, faithfully ported from CoreScope.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import hmac
import struct
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# Route Type Constants (Header bits 1-0)
ROUTE_TRANSPORT_FLOOD = 0
ROUTE_FLOOD = 1
ROUTE_DIRECT = 2
ROUTE_TRANSPORT_DIRECT = 3

ROUTE_TYPE_NAMES = {
    0: "TRANSPORT_FLOOD",
    1: "FLOOD",
    2: "DIRECT",
    3: "TRANSPORT_DIRECT",
}

# Payload Type Constants (Header bits 5-2)
PAYLOAD_REQ = 0x00
PAYLOAD_RESPONSE = 0x01
PAYLOAD_TXT_MSG = 0x02
PAYLOAD_ACK = 0x03
PAYLOAD_ADVERT = 0x04
PAYLOAD_GRP_TXT = 0x05
PAYLOAD_GRP_DATA = 0x06
PAYLOAD_ANON_REQ = 0x07
PAYLOAD_PATH = 0x08
PAYLOAD_TRACE = 0x09
PAYLOAD_MULTIPART = 0x0A
PAYLOAD_CONTROL = 0x0B
PAYLOAD_RAW_CUSTOM = 0x0F

PAYLOAD_TYPE_NAMES = {
    0x00: "REQ",
    0x01: "RESPONSE",
    0x02: "TXT_MSG",
    0x03: "ACK",
    0x04: "ADVERT",
    0x05: "GRP_TXT",
    0x06: "GRP_DATA",
    0x07: "ANON_REQ",
    0x08: "PATH",
    0x09: "TRACE",
    0x0A: "MULTIPART",
    0x0B: "CONTROL",
    0x0F: "RAW_CUSTOM",
}

# CoreScope Type Colors for Badges and UI
TYPE_COLORS = {
    "ADVERT": "#22C55E",      # Green
    "GRP_TXT": "#3B82F6",     # Blue
    "TXT_MSG": "#F59E0B",     # Amber
    "ACK": "#6B7280",         # Gray
    "REQ": "#A855F7",         # Purple
    "RESPONSE": "#06B6D4",    # Cyan
    "TRACE": "#EC4899",       # Pink
    "PATH": "#14B8A6",        # Teal
    "ANON_REQ": "#F43F5E",    # Rose
    "GRP_DATA": "#8B5CF6",    # Violet
    "MULTIPART": "#0D9488",   # Dark Teal
    "CONTROL": "#B45309",     # Rust / Ochre
    "RAW_CUSTOM": "#C026D3",  # Fuchsia
    "FLOOD": "#3B82F6",       # CoreScope Primary Blue
    "DIRECT": "#F59E0B",      # CoreScope Direct Amber
}

# CoreScope Default Public Channel Key
DEFAULT_PUBLIC_CHANNEL_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")

COMMUNITY_CHANNELS: List[str] = [
    "thenorf",
    "northeast",
    "cumbria",
    "yorkshire",
    "northwest",
    "scotland",
    "wales",
    "midlands",
    "london",
    "south",
    "southwest",
    "southeast",
    "primary",
    "emergency",
    "testing",
    "chat",
    "ops"
]


def build_known_channel_keys(storage: Optional[Any] = None) -> Dict[str, bytes]:
    """Builds a complete dictionary of channel name -> 16-byte AES keys.

    Includes:
    - Default 'Public' channel (both CoreScope default hex and derived "public")
    - Well-known regional community channels (e.g. #thenorf, #northeast, #cumbria)
    - All user-joined channels stored in local database
    """
    keys: Dict[str, bytes] = {
        "Public": DEFAULT_PUBLIC_CHANNEL_KEY,
        "public": derive_channel_key("public"),
    }

    # Add community channels (deriving keys for both clean name and with # prefix)
    for name in COMMUNITY_CHANNELS:
        clean = name.lstrip("#").lower()
        key_clean = derive_channel_key(clean)
        keys[f"#{clean}"] = key_clean
        keys[clean] = key_clean
        keys[f"#{clean}_raw"] = derive_channel_key(f"#{clean}")

    # Add user joined channels from storage
    if storage and hasattr(storage, "get_channels"):
        try:
            channels = storage.get_channels()
            for ch in channels:
                if ch.name:
                    clean = ch.name.strip().lstrip("#").lower()
                    if clean and clean != "public":
                        k_clean = derive_channel_key(clean)
                        keys[ch.name] = k_clean
                        keys[f"#{clean}"] = k_clean
                        keys[clean] = k_clean
                        keys[f"#{clean}_raw"] = derive_channel_key(f"#{clean}")
        except Exception:
            pass

    return keys


@dataclass
class PacketHeader:
    route_type: int
    route_type_name: str
    payload_type: int
    payload_type_name: str
    payload_version: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PacketPath:
    hash_size: int = 1
    hash_count: int = 0
    hops: List[str] = field(default_factory=list)  # Hex strings, e.g. ["4A", "B2", "9C"]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdvertFlags:
    raw: int = 0
    type: int = 0
    chat: bool = False
    repeater: bool = False
    room: bool = False
    sensor: bool = False
    has_location: bool = False
    has_feat1: bool = False
    has_feat2: bool = False
    has_name: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecodedPayload:
    type_name: str
    dest_hash: Optional[str] = None
    src_hash: Optional[str] = None
    src_pubkey: Optional[str] = None
    mac: Optional[str] = None
    encrypted_data: Optional[str] = None
    extra_hash: Optional[str] = None
    ack_len: Optional[int] = None
    ack_attempt: Optional[int] = None
    ack_rand: Optional[int] = None
    pub_key: Optional[str] = None
    timestamp: Optional[int] = None
    timestamp_iso: Optional[str] = None
    signature: Optional[str] = None
    flags: Optional[AdvertFlags] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    name: Optional[str] = None
    battery_mv: Optional[int] = None
    temperature_c: Optional[float] = None
    channel_hash: Optional[int] = None
    channel_hash_hex: Optional[str] = None
    channel: Optional[str] = None
    decryption_status: Optional[str] = None
    text: Optional[str] = None
    sender: Optional[str] = None
    tag: Optional[int] = None
    auth_code: Optional[int] = None
    trace_flags: Optional[int] = None
    raw_hex: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class DecodedPacket:
    header: PacketHeader
    path: PacketPath
    payload: DecodedPayload
    raw_hex: str
    transport_code1: Optional[str] = None
    transport_code2: Optional[str] = None
    snr: Optional[float] = None
    rssi: Optional[float] = None
    timestamp_iso: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def derive_channel_key(channel_name: str) -> bytes:
    """Derives a 16-byte channel key from channel name (e.g. #primary, #emergency) using SHA-256."""
    clean = channel_name.strip().lower()
    return hashlib.sha256(clean.encode("utf-8")).digest()[:16]


def decrypt_channel_message(
    ciphertext_hex: str,
    mac_hex: str,
    channel_key: bytes
) -> Optional[Tuple[int, int, str, Optional[str]]]:
    """Decrypts AES-128-ECB channel message with HMAC-SHA256 verification.

    Returns (timestamp, flags, text, sender) or None on failure.
    """
    if not HAS_CRYPTO or len(channel_key) != 16:
        return None

    try:
        ct = bytes.fromhex(ciphertext_hex)
        mac = bytes.fromhex(mac_hex)
    except ValueError:
        return None

    if len(mac) != 2 or len(ct) == 0 or len(ct) % 16 != 0:
        return None

    # 32-byte secret: 16-byte key + 16 zero bytes
    secret = channel_key + (b"\x00" * 16)

    # HMAC-SHA256 verification (first 2 bytes must match)
    h = hmac.new(secret, ct, hashlib.sha256).digest()
    if h[0] != mac[0] or h[1] != mac[1]:
        return None

    # AES-128-ECB decryption
    cipher = AES.new(channel_key, AES.MODE_ECB)
    pt = cipher.decrypt(ct)

    if len(pt) < 5:
        return None

    ts = struct.unpack("<I", pt[0:4])[0]
    flags = pt[4]
    raw_text = pt[5:]

    # Remove null terminator
    null_pos = raw_text.find(b"\x00")
    if null_pos >= 0:
        raw_text = raw_text[:null_pos]

    try:
        msg_str = raw_text.decode("utf-8")
    except UnicodeDecodeError:
        return None

    sender = None
    colon_idx = msg_str.find(": ")
    if 0 < colon_idx < 50:
        candidate_sender = msg_str[:colon_idx]
        if ":" not in candidate_sender and "[" not in candidate_sender:
            sender = candidate_sender
            msg_str = msg_str[colon_idx + 2:]

    return ts, flags, msg_str, sender


def decode_header(b: int) -> PacketHeader:
    """Decodes MeshCore 1-byte header."""
    rt = b & 0x03
    pt = (b >> 2) & 0x0F
    pv = (b >> 6) & 0x03
    return PacketHeader(
        route_type=rt,
        route_type_name=ROUTE_TYPE_NAMES.get(rt, "UNKNOWN"),
        payload_type=pt,
        payload_type_name=PAYLOAD_TYPE_NAMES.get(pt, "UNKNOWN"),
        payload_version=pv,
    )


def decode_advert_payload(buf: bytes) -> DecodedPayload:
    """Decodes type 0x04 ADVERT payload."""
    if len(buf) < 100:
        return DecodedPayload(type_name="ADVERT", error="too short for advert", raw_hex=buf.hex())

    pubkey = buf[0:32].hex()
    ts = struct.unpack("<I", buf[32:36])[0]
    sig = buf[36:100].hex()
    appdata = buf[100:]

    adv = DecodedPayload(
        type_name="ADVERT",
        pub_key=pubkey,
        timestamp=ts,
        timestamp_iso=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if 1000000000 < ts < 2500000000 else None,
        signature=sig,
        raw_hex=buf.hex(),
    )

    if appdata:
        flag_byte = appdata[0]
        f_type = flag_byte & 0x0F
        flags = AdvertFlags(
            raw=flag_byte,
            type=f_type,
            chat=(f_type == 1),
            repeater=(f_type == 2),
            room=(f_type == 3),
            sensor=(f_type == 4),
            has_location=bool(flag_byte & 0x10),
            has_feat1=bool(flag_byte & 0x20),
            has_feat2=bool(flag_byte & 0x40),
            has_name=bool(flag_byte & 0x80),
        )
        adv.flags = flags

        offset = 1
        if flags.has_location and len(appdata) >= offset + 8:
            lat_i, lon_i = struct.unpack("<ii", appdata[offset:offset + 8])
            # MeshCore encodes coordinates in microdegrees (1e6, 6 decimal places) or 1e7
            scale = 10000000.0 if (abs(lat_i) > 90000000 or abs(lon_i) > 180000000) else 1000000.0
            adv.lat = round(lat_i / scale, 6)
            adv.lon = round(lon_i / scale, 6)
            offset += 8

        if flags.has_feat1 and len(appdata) >= offset + 2:
            adv.battery_mv = struct.unpack("<H", appdata[offset:offset + 2])[0]
            offset += 2

        if flags.has_feat2 and len(appdata) >= offset + 2:
            raw_temp = struct.unpack("<h", appdata[offset:offset + 2])[0]
            adv.temperature_c = round(raw_temp / 100.0, 1)
            offset += 2

        if flags.has_name and len(appdata) > offset:
            name_bytes = appdata[offset:]
            null_pos = name_bytes.find(b"\x00")
            if null_pos >= 0:
                name_bytes = name_bytes[:null_pos]
            try:
                adv.name = name_bytes.decode("utf-8", errors="ignore").strip()
            except Exception:
                pass

    return adv


def decode_ack_payload(buf: bytes) -> DecodedPayload:
    """Decodes type 0x03 ACK payload."""
    if len(buf) < 4:
        return DecodedPayload(type_name="ACK", error="too short", raw_hex=buf.hex())

    crc = struct.unpack("<I", buf[0:4])[0]
    p = DecodedPayload(
        type_name="ACK",
        extra_hash=f"{crc:08x}",
        ack_len=min(6, len(buf)),
        raw_hex=buf.hex(),
    )
    if len(buf) >= 5:
        p.ack_attempt = int(buf[4])
    if len(buf) >= 6:
        p.ack_rand = int(buf[5])
    return p


def decode_trace_payload(buf: bytes) -> DecodedPayload:
    """Decodes type 0x09 TRACE payload."""
    p = DecodedPayload(type_name="TRACE", raw_hex=buf.hex())
    if len(buf) >= 8:
        tag, auth = struct.unpack("<II", buf[0:8])
        p.tag = tag
        p.auth_code = auth
        if len(buf) >= 9:
            p.trace_flags = int(buf[8])
    return p


def decode_meshcore_packet(
    raw: Union[bytes, str],
    channel_keys: Optional[Dict[str, Union[bytes, str]]] = None,
    snr: Optional[float] = None,
    rssi: Optional[float] = None,
) -> Optional[DecodedPacket]:
    """Decodes raw MeshCore packet bytes or hex string into a structured DecodedPacket."""
    if isinstance(raw, str):
        try:
            buf = bytes.fromhex(raw.strip())
        except ValueError:
            return None
    elif isinstance(raw, (bytes, bytearray)):
        buf = bytes(raw)
    else:
        return None

    if len(buf) < 2:
        return None

    # 1. Header
    hdr = decode_header(buf[0])
    offset = 1

    # 2. Transport Codes (if TRANSPORT_FLOOD or TRANSPORT_DIRECT)
    tc1 = None
    tc2 = None
    if hdr.route_type in (ROUTE_TRANSPORT_FLOOD, ROUTE_TRANSPORT_DIRECT):
        if len(buf) < offset + 4:
            return None
        tc1 = buf[offset:offset + 2].hex().upper()
        tc2 = buf[offset + 2:offset + 4].hex().upper()
        offset += 4

    # 3. Path
    path_byte = buf[offset]
    offset += 1
    hash_count = path_byte & 0x3F
    hash_size = (path_byte >> 6) + 1

    hops = []
    if hash_size < 4 and hash_count * hash_size <= 64:
        for i in range(hash_count):
            h_start = offset + (i * hash_size)
            h_end = h_start + hash_size
            if h_end <= len(buf):
                hops.append(buf[h_start:h_end].hex().upper())
        offset += (hash_count * hash_size)

    packet_path = PacketPath(hash_size=hash_size, hash_count=hash_count, hops=hops)
    payload_buf = buf[offset:]

    # 4. Payload Parsing
    pt = hdr.payload_type
    pt_name = hdr.payload_type_name

    if pt == PAYLOAD_ADVERT:
        payload = decode_advert_payload(payload_buf)
    elif pt == PAYLOAD_ACK:
        payload = decode_ack_payload(payload_buf)
    elif pt == PAYLOAD_TRACE:
        payload = decode_trace_payload(payload_buf)
    elif pt in (PAYLOAD_GRP_TXT, PAYLOAD_GRP_DATA):
        payload = DecodedPayload(type_name=pt_name, raw_hex=payload_buf.hex())
        if len(payload_buf) >= 4:
            payload.channel_hash = payload_buf[0]
            payload.channel_hash_hex = f"{payload_buf[0]:02x}"
            payload.mac = payload_buf[1:3].hex()
            payload.encrypted_data = payload_buf[3:].hex()

            # Attempt Decryption
            keys_to_try = {"Public": DEFAULT_PUBLIC_CHANNEL_KEY}
            if channel_keys:
                for cname, k in channel_keys.items():
                    if isinstance(k, str):
                        try:
                            keys_to_try[cname] = bytes.fromhex(k)
                        except ValueError:
                            keys_to_try[cname] = derive_channel_key(cname)
                    elif isinstance(k, bytes):
                        keys_to_try[cname] = k

            decrypted = False
            for cname, ckey in keys_to_try.items():
                res = decrypt_channel_message(payload.encrypted_data, payload.mac, ckey)
                if res:
                    ts, flags, text, sender = res
                    payload.decryption_status = "DECRYPTED"
                    clean_cname = cname.replace("_raw", "")
                    if clean_cname.lower() in ("public", "public_raw"):
                        payload.channel = "Public"
                    else:
                        payload.channel = clean_cname if clean_cname.startswith("#") else f"#{clean_cname}"
                    payload.text = text
                    payload.sender = sender
                    payload.timestamp = ts
                    decrypted = True
                    break
            if not decrypted:
                payload.decryption_status = "ENCRYPTED"
    else:
        # Default encrypted / direct envelope: destHash, srcHash, MAC, encryptedData
        payload = DecodedPayload(type_name=pt_name, raw_hex=payload_buf.hex())
        if len(payload_buf) >= 4:
            payload.dest_hash = payload_buf[0:1].hex().upper()
            payload.src_hash = payload_buf[1:2].hex().upper()
            payload.mac = payload_buf[2:4].hex().upper()
            payload.encrypted_data = payload_buf[4:].hex()

    return DecodedPacket(
        header=hdr,
        path=packet_path,
        payload=payload,
        raw_hex=buf.hex().upper(),
        transport_code1=tc1,
        transport_code2=tc2,
        snr=snr,
        rssi=rssi,
    )


def generate_field_breakdown(packet: DecodedPacket) -> List[Dict[str, Any]]:
    """Generates an offset-by-offset breakdown of fields in the raw packet bytes.

    Faithfully reproduces CoreScope's byte inspector table:
    Offset | Field | Value | Description
    """
    rows: List[Dict[str, Any]] = []
    try:
        buf = bytes.fromhex(packet.raw_hex)
    except Exception:
        return rows

    if not buf:
        return rows

    # Offset 0: Header (1B)
    hdr_b = buf[0]
    rows.append({
        "offset": 0,
        "field": "Header (1B)",
        "value": f"0x{hdr_b:02X}",
        "desc": f"Route: {packet.header.route_type_name} ({packet.header.route_type}), Type: {packet.header.payload_type_name} (0x{packet.header.payload_type:02X}), Ver: {packet.header.payload_version}"
    })

    curr_off = 1
    # Transport codes if present
    if packet.transport_code1:
        rows.append({
            "offset": curr_off,
            "field": "Transport Code 1 (2B)",
            "value": packet.transport_code1,
            "desc": "Routing transport authorization code 1"
        })
        curr_off += 2
    if packet.transport_code2:
        rows.append({
            "offset": curr_off,
            "field": "Transport Code 2 (2B)",
            "value": packet.transport_code2,
            "desc": "Routing transport authorization code 2"
        })
        curr_off += 2

    # Path section if route is flood
    if packet.header.route_type in (ROUTE_FLOOD, ROUTE_TRANSPORT_FLOOD):
        if curr_off < len(buf):
            path_hdr = buf[curr_off]
            rows.append({
                "offset": curr_off,
                "field": "Path Header (1B)",
                "value": f"0x{path_hdr:02X}",
                "desc": f"Hash Size: {packet.path.hash_size}B, Hop Count: {packet.path.hash_count}"
            })
            curr_off += 1

            for i, hop in enumerate(packet.path.hops):
                rows.append({
                    "offset": curr_off,
                    "field": f"Hop {i + 1} ({packet.path.hash_size}B)",
                    "value": hop,
                    "desc": f"Repeater prefix hop #{i + 1}"
                })
                curr_off += packet.path.hash_size

    # Payload section
    pt = packet.header.payload_type
    if pt == PAYLOAD_ADVERT:
        if curr_off + 32 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Public Key (32B)",
                "value": packet.payload.pub_key[:16] + "..." if packet.payload.pub_key else "",
                "desc": "Node ED25519 public key"
            })
            curr_off += 32
        if curr_off + 4 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Timestamp (4B)",
                "value": str(packet.payload.timestamp or ""),
                "desc": packet.payload.timestamp_iso or ""
            })
            curr_off += 4
        if curr_off + 64 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Signature (64B)",
                "value": packet.payload.signature[:16] + "..." if packet.payload.signature else "",
                "desc": "ED25519 signature of node advertisement"
            })
            curr_off += 64
        if curr_off < len(buf):
            f_val = buf[curr_off]
            rows.append({
                "offset": curr_off,
                "field": "Flags (1B)",
                "value": f"0x{f_val:02X}",
                "desc": f"Chat={packet.payload.flags.chat if packet.payload.flags else False}, Repeater={packet.payload.flags.repeater if packet.payload.flags else False}"
            })
            curr_off += 1
        if packet.payload.lat is not None and packet.payload.lon is not None:
            rows.append({
                "offset": curr_off,
                "field": "Coordinates (8B)",
                "value": f"{packet.payload.lat:.5f}, {packet.payload.lon:.5f}",
                "desc": "GPS Latitude and Longitude"
            })
            curr_off += 8
        if packet.payload.name:
            rows.append({
                "offset": curr_off,
                "field": "Node Name",
                "value": packet.payload.name,
                "desc": "Announced advertised node nickname"
            })

    elif pt in (PAYLOAD_GRP_TXT, PAYLOAD_GRP_DATA):
        if curr_off < len(buf):
            ch_b = buf[curr_off]
            rows.append({
                "offset": curr_off,
                "field": "Channel Hash (1B)",
                "value": f"0x{ch_b:02X}",
                "desc": f"Target channel hash ({packet.payload.channel or 'Unknown'})"
            })
            curr_off += 1
        if curr_off + 2 <= len(buf):
            mac_b = buf[curr_off:curr_off + 2]
            rows.append({
                "offset": curr_off,
                "field": "MAC / Auth (2B)",
                "value": mac_b.hex().upper(),
                "desc": "HMAC-SHA256 message authentication prefix"
            })
            curr_off += 2
        if curr_off < len(buf):
            c_len = len(buf) - curr_off
            dec_info = ""
            if packet.payload.decryption_status == "DECRYPTED":
                dec_info = f" [DECRYPTED: \"{packet.payload.text}\"]"
            rows.append({
                "offset": curr_off,
                "field": f"Ciphertext ({c_len}B)",
                "value": buf[curr_off:].hex().upper(),
                "desc": f"AES-128 encrypted payload{dec_info}"
            })

    elif pt == PAYLOAD_ACK:
        if curr_off + 4 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "ACK Nonce (4B)",
                "value": f"0x{packet.payload.ack_rand:08X}" if packet.payload.ack_rand is not None else "",
                "desc": "Packet acknowledgement sequence number"
            })
            curr_off += 4

    elif pt == PAYLOAD_TRACE:
        if curr_off + 4 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Auth Code (4B)",
                "value": f"0x{packet.payload.auth_code:08X}" if packet.payload.auth_code is not None else "",
                "desc": "Traceroute authentication hash"
            })
            curr_off += 4
        if curr_off < len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Trace Flags (1B)",
                "value": f"0x{buf[curr_off]:02X}",
                "desc": f"Flags: 0x{packet.payload.trace_flags:02X}" if packet.payload.trace_flags is not None else ""
            })
            curr_off += 1

    else:
        # Default destination / source hashes if available
        if packet.payload.dest_hash and curr_off < len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Dest Hash (1B)",
                "value": packet.payload.dest_hash,
                "desc": "Destination node prefix"
            })
            curr_off += 1
        if packet.payload.src_hash and curr_off < len(buf):
            rows.append({
                "offset": curr_off,
                "field": "Src Hash (1B)",
                "value": packet.payload.src_hash,
                "desc": "Source node prefix"
            })
            curr_off += 1
        if packet.payload.mac and curr_off + 2 <= len(buf):
            rows.append({
                "offset": curr_off,
                "field": "MAC (2B)",
                "value": packet.payload.mac,
                "desc": "Message authentication code"
            })
            curr_off += 2
        if curr_off < len(buf):
            rows.append({
                "offset": curr_off,
                "field": f"Remaining Payload ({len(buf) - curr_off}B)",
                "value": buf[curr_off:].hex().upper(),
                "desc": "Raw payload data"
            })

    return rows


class PacketDecoder:
    """Convenience helper and channel-key manager for decoding MeshCore packets."""

    def __init__(self, channel_keys: Optional[Dict[str, Union[bytes, str]]] = None):
        self.channel_keys: Dict[str, Union[bytes, str]] = dict(channel_keys) if channel_keys else {}

    def add_channel(self, name: str, key: Union[bytes, str]):
        self.channel_keys[name] = key

    def decode_hex(self, hex_str: str, snr: Optional[float] = None, rssi: Optional[float] = None) -> Optional[DecodedPacket]:
        return decode_meshcore_packet(hex_str, channel_keys=self.channel_keys, snr=snr, rssi=rssi)

    def decode_bytes(self, raw: bytes, snr: Optional[float] = None, rssi: Optional[float] = None) -> Optional[DecodedPacket]:
        return decode_meshcore_packet(raw, channel_keys=self.channel_keys, snr=snr, rssi=rssi)

    @staticmethod
    def decode(raw: Union[bytes, str], channel_keys: Optional[Dict[str, Union[bytes, str]]] = None, snr: Optional[float] = None, rssi: Optional[float] = None) -> Optional[DecodedPacket]:
        return decode_meshcore_packet(raw, channel_keys=channel_keys, snr=snr, rssi=rssi)

