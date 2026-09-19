"""Unit tests for MeshCore packet decoder and channel decryptor."""

import hashlib
import hmac
import struct
import pytest

from Crypto.Cipher import AES
from meshcore_tray.core.packet_decoder import (
    decode_meshcore_packet,
    decode_header,
    derive_channel_key,
    decrypt_channel_message,
    DEFAULT_PUBLIC_CHANNEL_KEY,
    ROUTE_FLOOD,
    PAYLOAD_ADVERT,
    PAYLOAD_ACK,
    PAYLOAD_GRP_TXT,
    PAYLOAD_TRACE,
    PAYLOAD_TXT_MSG,
)


def test_decode_header():
    # Route: FLOOD (1), Payload: ADVERT (4), Version: 0 -> (4 << 2) | 1 = 0x11
    hdr = decode_header(0x11)
    assert hdr.route_type == ROUTE_FLOOD
    assert hdr.route_type_name == "FLOOD"
    assert hdr.payload_type == PAYLOAD_ADVERT
    assert hdr.payload_type_name == "ADVERT"

    # Route: DIRECT (2), Payload: TXT_MSG (2) -> (2 << 2) | 2 = 0x0A
    hdr2 = decode_header(0x0A)
    assert hdr2.route_type_name == "DIRECT"
    assert hdr2.payload_type_name == "TXT_MSG"


def test_decode_ack_packet():
    # Header: FLOOD (1), ACK (3) -> (3 << 2) | 1 = 0x0D
    # Path: 0 hops (pathByte = 0x00)
    # Checksum: 0x12345678 (4 bytes LE)
    # Attempt: 2, Rand: 99
    raw = bytes([0x0D, 0x00]) + struct.pack("<I", 0x12345678) + bytes([2, 99])
    pkt = decode_meshcore_packet(raw, snr=8.5, rssi=-90.0)

    assert pkt is not None
    assert pkt.header.payload_type_name == "ACK"
    assert pkt.path.hash_count == 0
    assert pkt.payload.extra_hash == "12345678"
    assert pkt.payload.ack_attempt == 2
    assert pkt.payload.ack_rand == 99
    assert pkt.snr == 8.5
    assert pkt.rssi == -90.0


def test_decode_advert_with_location_and_name():
    # Header: FLOOD | ADVERT (0x11)
    # Path: 1 hop with 1-byte hash 0xAB -> pathByte = (0 << 6) | 1 = 0x01, hop = [0xAB]
    # PubKey: 32 bytes of 0x01
    # Timestamp: 1726000000
    # Signature: 64 bytes of 0x02
    # AppData:
    #   Flag: has_location (0x10) | has_name (0x80) | type=repeater (2) = 0x92
    #   Lat/Lon: 54.6589700 * 1e7, -3.4346000 * 1e7
    #   Name: "TestRepeater\x00"
    hdr_b = bytes([0x11, 0x01, 0xAB])
    pubkey = b"\x01" * 32
    ts = struct.pack("<I", 1726000000)
    sig = b"\x02" * 64
    flag = bytes([0x92])
    coords = struct.pack("<ii", int(54.65897 * 10000000), int(-3.43460 * 10000000))
    name = b"TestRepeater\x00"
    raw = hdr_b + pubkey + ts + sig + flag + coords + name

    pkt = decode_meshcore_packet(raw)
    assert pkt is not None
    assert pkt.header.payload_type_name == "ADVERT"
    assert pkt.path.hash_count == 1
    assert pkt.path.hops == ["AB"]
    assert pkt.payload.pub_key == ("01" * 32)
    assert pkt.payload.flags.repeater is True
    assert pkt.payload.lat == pytest.approx(54.65897, abs=1e-4)
    assert pkt.payload.lon == pytest.approx(-3.43460, abs=1e-4)
    assert pkt.payload.name == "TestRepeater"


def test_decode_and_decrypt_channel_message():
    # Create encrypted channel message
    channel_key = DEFAULT_PUBLIC_CHANNEL_KEY
    plaintext_msg = b"M7NCY: Hello from MeshCore Navigator!\x00"
    # Pad to AES block size (16)
    pad_len = (16 - (5 + len(plaintext_msg)) % 16) % 16
    payload_body = struct.pack("<IB", 1726000500, 0x00) + plaintext_msg + (b"\x00" * pad_len)

    cipher = AES.new(channel_key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(payload_body)

    # Calculate HMAC-SHA256
    secret = channel_key + (b"\x00" * 16)
    mac = hmac.new(secret, ciphertext, hashlib.sha256).digest()[:2]

    # Wire format for GRP_TXT:
    # Header: FLOOD | GRP_TXT (0x15)
    # Path: 2 hops: 0x4A, 0x8F (pathByte = (0 << 6) | 2 = 0x02)
    # ChannelHash: 0x00
    # MAC: 2 bytes
    # Ciphertext: ...
    raw = bytes([0x15, 0x02, 0x4A, 0x8F, 0x00]) + mac + ciphertext

    pkt = decode_meshcore_packet(raw)
    assert pkt is not None
    assert pkt.header.payload_type_name == "GRP_TXT"
    assert pkt.path.hops == ["4A", "8F"]
    assert pkt.payload.decryption_status == "DECRYPTED"
    assert pkt.payload.channel == "Public"
    assert pkt.payload.sender == "M7NCY"
    assert pkt.payload.text == "Hello from MeshCore Navigator!"


def test_derive_channel_key():
    key = derive_channel_key("#emergency")
    assert len(key) == 16
    assert key == hashlib.sha256(b"#emergency").digest()[:16]


def test_generate_field_breakdown():
    from meshcore_tray.core.packet_decoder import generate_field_breakdown, PacketDecoder
    # Flood packet with 1 hop and group text payload
    raw_hex = "1501A1008899DEADBEEF"
    decoder = PacketDecoder()
    pkt = decoder.decode_hex(raw_hex)
    assert pkt is not None
    breakdown = generate_field_breakdown(pkt)
    assert len(breakdown) >= 4
    assert breakdown[0]["field"] == "Header (1B)"
    assert breakdown[0]["offset"] == 0
    assert "FLOOD" in breakdown[0]["desc"]
    assert "GRP_TXT" in breakdown[0]["desc"]
    assert any("Path Header" in b["field"] for b in breakdown)
    assert any("Hop 1" in b["field"] for b in breakdown)
    assert any("Channel Hash" in b["field"] for b in breakdown)

