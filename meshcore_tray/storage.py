"""SQLite Storage Layer for MeshCore Pixoo Tray."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import List, Optional
from meshcore_tray.core.models import (
    ChannelInfo, MessageEnvelope, NeighbourInfo, NodeContact, TelemetryEnvelope
)

logger = logging.getLogger("meshcore_tray.storage")

DB_DIR = Path.home() / ".config" / "meshcore-tray"
DB_FILE = DB_DIR / "meshcore_tray.db"


class Storage:
    """Manages SQLite storage for messages, contacts, channels, telemetry, and neighbours."""

    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    source_driver TEXT,
                    sender_id TEXT,
                    sender_name TEXT,
                    is_favorite INTEGER,
                    channel TEXT,
                    channel_id INTEGER,
                    is_direct_message INTEGER,
                    recipient_id TEXT,
                    recipient_name TEXT,
                    text TEXT,
                    metadata_json TEXT,
                    delivery_status TEXT,
                    is_outgoing INTEGER,
                    is_mention INTEGER,
                    matched_keywords_json TEXT
                )
            """)

            # Contacts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    is_favorite INTEGER,
                    last_seen TEXT,
                    public_key TEXT,
                    is_repeater INTEGER,
                    snr_db REAL,
                    rssi_dbm REAL
                )
            """)

            # Channels table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE,
                    is_favorite INTEGER,
                    is_pixoo_enabled INTEGER,
                    last_activity_ts TEXT,
                    unread_count INTEGER
                )
            """)

            # Telemetry table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    node_id TEXT,
                    frequency_mhz REAL,
                    bandwidth_khz REAL,
                    spreading_factor INTEGER,
                    coding_rate TEXT,
                    tx_power_dbm INTEGER,
                    noise_floor_dbm REAL,
                    snr_db REAL,
                    rssi_dbm REAL,
                    battery_pct INTEGER,
                    voltage_volts REAL
                )
            """)

            # Neighbours table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS neighbours (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    snr_db REAL,
                    rssi_dbm REAL,
                    last_heard_ts TEXT,
                    is_repeater INTEGER,
                    is_favorite INTEGER,
                    via_node_id TEXT
                )
            """)

            # Prepopulate default channels if empty
            cursor.execute("SELECT COUNT(*) as cnt FROM channels")
            if cursor.fetchone()["cnt"] == 0:
                defaults = [
                    (0, "Public", 1, 1, datetime.now(timezone.utc).isoformat(), 0),
                    (1, "#general", 0, 1, datetime.now(timezone.utc).isoformat(), 0),
                    (2, "#ops", 1, 1, datetime.now(timezone.utc).isoformat(), 0),
                    (3, "#test", 0, 0, datetime.now(timezone.utc).isoformat(), 0),
                    (4, "#telemetry", 0, 0, datetime.now(timezone.utc).isoformat(), 0),
                ]
                cursor.executemany(
                    "INSERT INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count) VALUES (?, ?, ?, ?, ?, ?)",
                    defaults
                )

            conn.commit()

    # --- Message Operations ---

    def save_message(self, msg: MessageEnvelope):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO messages (
                    id, timestamp, source_driver, sender_id, sender_name, is_favorite,
                    channel, channel_id, is_direct_message, recipient_id, recipient_name,
                    text, metadata_json, delivery_status, is_outgoing, is_mention, matched_keywords_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.id, msg.timestamp, msg.source_driver, msg.sender_id, msg.sender_name,
                int(msg.is_favorite), msg.channel, msg.channel_id, int(msg.is_direct_message),
                msg.recipient_id, msg.recipient_name, msg.text, json.dumps(msg.metadata),
                msg.delivery_status, int(msg.is_outgoing), int(msg.is_mention),
                json.dumps(msg.matched_keywords)
            ))
            # Touch channel activity
            if not msg.is_direct_message and msg.channel:
                cursor.execute("""
                    UPDATE channels SET last_activity_ts = ? WHERE name = ?
                """, (msg.timestamp, msg.channel))

            # Auto-record contact if incoming message
            if not msg.is_outgoing and msg.sender_name and msg.sender_name not in ("Unknown", "Anonymous"):
                cursor.execute("""
                    INSERT OR REPLACE INTO contacts (
                        node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm
                    ) VALUES (
                        ?, ?, ?, ?,
                        COALESCE((SELECT public_key FROM contacts WHERE node_id = ?), ''),
                        COALESCE((SELECT is_repeater FROM contacts WHERE node_id = ?), 0),
                        ?, ?
                    )
                """, (
                    msg.sender_id or msg.sender_name,
                    msg.sender_name,
                    int(msg.is_favorite),
                    msg.timestamp,
                    msg.sender_id or msg.sender_name,
                    msg.sender_id or msg.sender_name,
                    msg.metadata.get("snr", 0.0),
                    msg.metadata.get("rssi", -100.0)
                ))
            conn.commit()

    def get_messages(self, channel: Optional[str] = None, contact_id: Optional[str] = None, limit: int = 100) -> List[MessageEnvelope]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if contact_id:
                cursor.execute("""
                    SELECT * FROM messages
                    WHERE is_direct_message = 1 AND (sender_id = ? OR recipient_id = ?)
                    ORDER BY timestamp ASC LIMIT ?
                """, (contact_id, contact_id, limit))
            elif channel:
                cursor.execute("""
                    SELECT * FROM messages
                    WHERE is_direct_message = 0 AND channel = ?
                    ORDER BY timestamp ASC LIMIT ?
                """, (channel, limit))
            else:
                cursor.execute("SELECT * FROM messages ORDER BY timestamp ASC LIMIT ?", (limit,))

            rows = cursor.fetchall()
            return [self._row_to_message(r) for r in rows]

    def search_messages(self, query: str, limit: int = 50) -> List[MessageEnvelope]:
        """Full-text keyword and sender search across all message history."""
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        pattern = f"%{cleaned_query}%"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages
                WHERE text LIKE ? OR sender_name LIKE ? OR channel LIKE ?
                ORDER BY timestamp DESC LIMIT ?
            """, (pattern, pattern, pattern, limit))
            rows = cursor.fetchall()
            return [self._row_to_message(r) for r in rows]

    def _row_to_message(self, r: sqlite3.Row) -> MessageEnvelope:
        return MessageEnvelope(
            id=r["id"],
            timestamp=r["timestamp"],
            source_driver=r["source_driver"],
            sender_id=r["sender_id"] or "",
            sender_name=r["sender_name"] or "Unknown",
            is_favorite=bool(r["is_favorite"]),
            channel=r["channel"] or "Public",
            channel_id=r["channel_id"] or 0,
            is_direct_message=bool(r["is_direct_message"]),
            recipient_id=r["recipient_id"],
            recipient_name=r["recipient_name"],
            text=r["text"] or "",
            metadata=json.loads(r["metadata_json"]) if r["metadata_json"] else {},
            delivery_status=r["delivery_status"] or "received",
            is_outgoing=bool(r["is_outgoing"]),
            is_mention=bool(r["is_mention"]),
            matched_keywords=json.loads(r["matched_keywords_json"]) if r["matched_keywords_json"] else []
        )

    # --- Channel & Contact Operations ---

    def get_channels(self) -> List[ChannelInfo]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channels ORDER BY channel_id ASC")
            return [
                ChannelInfo(
                    channel_id=r["channel_id"],
                    name=r["name"],
                    is_favorite=bool(r["is_favorite"]),
                    is_pixoo_enabled=bool(r["is_pixoo_enabled"]),
                    last_activity_ts=r["last_activity_ts"] or "",
                    unread_count=r["unread_count"] or 0
                )
                for r in cursor.fetchall()
            ]

    def save_channel(self, ch: ChannelInfo):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ch.channel_id, ch.name, int(ch.is_favorite), int(ch.is_pixoo_enabled), ch.last_activity_ts, ch.unread_count))
            conn.commit()

    def get_channels_by_recent_activity(self, prefix: str = "") -> List[ChannelInfo]:
        """Ranked list of channels matching prefix, sorted by most recent activity timestamp."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            pattern = f"%{prefix.lstrip('#')}%"
            cursor.execute("""
                SELECT * FROM channels
                WHERE name LIKE ? OR ('#' || name) LIKE ?
                ORDER BY last_activity_ts DESC
            """, (pattern, pattern))
            return [
                ChannelInfo(
                    channel_id=r["channel_id"],
                    name=r["name"],
                    is_favorite=bool(r["is_favorite"]),
                    is_pixoo_enabled=bool(r["is_pixoo_enabled"]),
                    last_activity_ts=r["last_activity_ts"] or "",
                    unread_count=r["unread_count"] or 0
                )
                for r in cursor.fetchall()
            ]

    def get_contacts(self) -> List[NodeContact]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM contacts ORDER BY alias ASC")
            return [
                NodeContact(
                    node_id=r["node_id"],
                    alias=r["alias"] or r["node_id"],
                    is_favorite=bool(r["is_favorite"]),
                    last_seen=r["last_seen"] or "",
                    public_key=r["public_key"] or "",
                    is_repeater=bool(r["is_repeater"]),
                    snr_db=r["snr_db"] or 0.0,
                    rssi_dbm=r["rssi_dbm"] or -100.0
                )
                for r in cursor.fetchall()
            ]

    def save_contact(self, contact: NodeContact):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO contacts (node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (contact.node_id, contact.alias, int(contact.is_favorite), contact.last_seen, contact.public_key, int(contact.is_repeater), contact.snr_db, contact.rssi_dbm))
            conn.commit()

    def get_contacts_by_recent_activity(self, prefix: str = "") -> List[NodeContact]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cleaned_prefix = prefix.lstrip("@").strip()
            pattern = f"%{cleaned_prefix}%"
            cursor.execute("""
                SELECT alias, node_id, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm
                FROM (
                    SELECT alias, node_id, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm FROM contacts
                    UNION
                    SELECT sender_name as alias, sender_id as node_id, is_favorite, MAX(timestamp) as last_seen, '' as public_key, 0 as is_repeater, 0.0 as snr_db, -100.0 as rssi_dbm
                    FROM messages
                    WHERE sender_name IS NOT NULL AND sender_name != '' AND sender_name NOT IN ('Unknown', 'Anonymous', 'Local', 'Local Node')
                    GROUP BY sender_name
                )
                WHERE alias LIKE ? OR node_id LIKE ?
                ORDER BY is_favorite DESC, last_seen DESC
            """, (pattern, pattern))
            return [
                NodeContact(
                    node_id=r["node_id"] or r["alias"],
                    alias=r["alias"] or r["node_id"],
                    is_favorite=bool(r["is_favorite"]),
                    last_seen=r["last_seen"] or "",
                    public_key=r["public_key"] or "",
                    is_repeater=bool(r["is_repeater"]),
                    snr_db=r["snr_db"] or 0.0,
                    rssi_dbm=r["rssi_dbm"] or -100.0
                )
                for r in cursor.fetchall()
            ]

    # --- Telemetry & Neighbours ---

    def save_telemetry(self, t: TelemetryEnvelope):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO telemetry (
                    timestamp, node_id, frequency_mhz, bandwidth_khz, spreading_factor,
                    coding_rate, tx_power_dbm, noise_floor_dbm, snr_db, rssi_dbm,
                    battery_pct, voltage_volts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.timestamp, t.node_id, t.frequency_mhz, t.bandwidth_khz, t.spreading_factor,
                t.coding_rate, t.tx_power_dbm, t.noise_floor_dbm, t.snr_db, t.rssi_dbm,
                t.battery_pct, t.voltage_volts
            ))
            conn.commit()

    def get_latest_telemetry(self, node_id: str = "local") -> Optional[TelemetryEnvelope]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM telemetry WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1
            """, (node_id,))
            r = cursor.fetchone()
            if not r:
                return None
            return TelemetryEnvelope(
                timestamp=r["timestamp"],
                node_id=r["node_id"],
                frequency_mhz=r["frequency_mhz"],
                bandwidth_khz=r["bandwidth_khz"],
                spreading_factor=r["spreading_factor"],
                coding_rate=r["coding_rate"],
                tx_power_dbm=r["tx_power_dbm"],
                noise_floor_dbm=r["noise_floor_dbm"],
                snr_db=r["snr_db"],
                rssi_dbm=r["rssi_dbm"],
                battery_pct=r["battery_pct"],
                voltage_volts=r["voltage_volts"]
            )

    def save_neighbour(self, n: NeighbourInfo):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO neighbours (
                    node_id, alias, snr_db, rssi_dbm, last_heard_ts, is_repeater, is_favorite, via_node_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (n.node_id, n.alias, n.snr_db, n.rssi_dbm, n.last_heard_ts, int(n.is_repeater), int(n.is_favorite), n.via_node_id))
            conn.commit()

    def get_neighbours(self, limit: int = 10) -> List[NeighbourInfo]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM neighbours ORDER BY snr_db DESC LIMIT ?", (limit,))
            return [
                NeighbourInfo(
                    node_id=r["node_id"],
                    alias=r["alias"] or r["node_id"],
                    snr_db=r["snr_db"] or 0.0,
                    rssi_dbm=r["rssi_dbm"] or -100.0,
                    last_heard_ts=r["last_heard_ts"] or "",
                    is_repeater=bool(r["is_repeater"]),
                    is_favorite=bool(r["is_favorite"]),
                    via_node_id=r["via_node_id"]
                )
                for r in cursor.fetchall()
            ]
