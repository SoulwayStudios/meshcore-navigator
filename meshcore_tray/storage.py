"""SQLite Storage Layer for MeshCore Pixoo Tray."""

from datetime import datetime, timezone, timedelta
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
from meshcore_tray.core.models import (
    ChannelInfo, MessageEnvelope, NeighbourInfo, NodeContact, TelemetryEnvelope, PacketPathInfo, DockedCompanionInfo,
    is_valid_alias, is_valid_node_id, is_valid_coordinate, calculate_haversine_distance_km, is_plausible_rf_coordinate,
    is_room_server_contact
)
from meshcore_tray.core.event_bus import bus, EventType

logger = logging.getLogger("meshcore_tray.storage")

def get_db_dir() -> Path:
    """Returns platform-appropriate database directory."""
    import os
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "meshcore-navigator"
    return Path.home() / ".config" / "meshcore-tray"


DB_DIR = get_db_dir()
DB_FILE = DB_DIR / "meshcore_tray.db"


class Storage:
    """Manages SQLite storage for messages, contacts, channels, telemetry, and neighbours."""

    def __init__(self, db_path: Union[str, Path] = DB_FILE):
        self.db_path = Path(db_path) if db_path else DB_FILE
        self._contact_by_key_cache: Dict[str, Optional[NodeContact]] = {}
        self._phantom_nodes_cache: Optional[List[Tuple[str, str]]] = None
        self._init_db()

    def _invalidate_contact_cache(self):
        if hasattr(self, "_contact_by_key_cache"):
            self._contact_by_key_cache.clear()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -16000;")
            conn.execute("PRAGMA temp_store = MEMORY;")
        except Exception:
            pass
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
                    matched_keywords_json TEXT,
                    repeats_heard INTEGER DEFAULT 0
                )
            """)

            cursor.execute("PRAGMA table_info(messages)")
            msg_cols = [col[1] for col in cursor.fetchall()]
            if "repeats_heard" not in msg_cols:
                cursor.execute("ALTER TABLE messages ADD COLUMN repeats_heard INTEGER DEFAULT 0")

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
                    rssi_dbm REAL,
                    latitude REAL,
                    longitude REAL,
                    out_path_len INTEGER DEFAULT -1,
                    out_path_hash_mode INTEGER DEFAULT -1,
                    out_path TEXT DEFAULT '',
                    scope_name TEXT DEFAULT '',
                    allowed_regions TEXT DEFAULT '[]',
                    is_room_server INTEGER DEFAULT 0,
                    first_seen TEXT DEFAULT ''
                )
            """)

            cursor.execute("PRAGMA table_info(contacts)")
            contact_cols = [col[1] for col in cursor.fetchall()]
            if "out_path_len" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN out_path_len INTEGER DEFAULT -1")
            if "out_path_hash_mode" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN out_path_hash_mode INTEGER DEFAULT -1")
            if "out_path" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN out_path TEXT DEFAULT ''")
            if "scope_name" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN scope_name TEXT DEFAULT ''")
            if "allowed_regions" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN allowed_regions TEXT DEFAULT '[]'")
            if "is_room_server" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN is_room_server INTEGER DEFAULT 0")
            if "first_seen" not in contact_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN first_seen TEXT DEFAULT ''")
                try:
                    cursor.execute("UPDATE contacts SET first_seen = '2024-01-01T00:00:00+00:00' WHERE (first_seen IS NULL OR first_seen = '')")
                except Exception:
                    pass

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

            cursor.execute("""
                INSERT OR IGNORE INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count)
                VALUES (0, 'Public', 1, 1, '', 0)
            """)
            try:
                cursor.execute("UPDATE channels SET name = '#' || name WHERE name NOT LIKE '#%' AND LOWER(name) != 'public'")
                cursor.execute("UPDATE messages SET channel = '#' || channel WHERE channel NOT LIKE '#%' AND LOWER(channel) != 'public' AND is_direct_message = 0")
            except Exception:
                pass

            # Discovered Scopes table (automatic OTA scope discoverability)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovered_scopes (
                    scope_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    color TEXT,
                    source_type TEXT,
                    last_heard_ts TEXT,
                    message_count INTEGER DEFAULT 1,
                    center_lat REAL,
                    center_lon REAL
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
                    via_node_id TEXT,
                    latitude REAL,
                    longitude REAL,
                    is_room_server INTEGER DEFAULT 0
                )
            """)

            cursor.execute("PRAGMA table_info(neighbours)")
            neigh_cols = [col[1] for col in cursor.fetchall()]
            if "is_room_server" not in neigh_cols:
                cursor.execute("ALTER TABLE neighbours ADD COLUMN is_room_server INTEGER DEFAULT 0")

            # Room Credentials table (securely stores passwords and auto-login preferences for room servers)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS room_credentials (
                    node_id TEXT PRIMARY KEY,
                    password TEXT,
                    last_login_ts TEXT,
                    auto_login INTEGER DEFAULT 1
                )
            """)

            # Channel Read State table (tracks last read/viewed message per channel or DM)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_read_state (
                    target_key TEXT PRIMARY KEY,
                    last_read_msg_id TEXT,
                    last_read_timestamp TEXT,
                    updated_at TEXT
                )
            """)

            cursor.execute("PRAGMA table_info(channel_read_state)")
            r_cols = [col[1] for col in cursor.fetchall()]
            if "updated_at" not in r_cols:
                cursor.execute("ALTER TABLE channel_read_state ADD COLUMN updated_at TEXT")

            # Packet Paths table (for Watcher map path visualization)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS packet_paths (
                    packet_id TEXT PRIMARY KEY,
                    sender_id TEXT,
                    sender_name TEXT,
                    recipient_id TEXT,
                    timestamp TEXT,
                    hop_nodes TEXT,
                    hop_snrs TEXT,
                    route_type TEXT,
                    coordinates TEXT,
                    payload_type TEXT DEFAULT 'FLOOD',
                    raw_hex TEXT,
                    decoded_info TEXT,
                    source TEXT DEFAULT 'radio'
                )
            """)
            p_cols = [c[1] for c in cursor.execute("PRAGMA table_info(packet_paths)").fetchall()]
            if "payload_type" not in p_cols:
                cursor.execute("ALTER TABLE packet_paths ADD COLUMN payload_type TEXT DEFAULT 'FLOOD'")
            if "raw_hex" not in p_cols:
                cursor.execute("ALTER TABLE packet_paths ADD COLUMN raw_hex TEXT")
            if "decoded_info" not in p_cols:
                cursor.execute("ALTER TABLE packet_paths ADD COLUMN decoded_info TEXT")
            if "source" not in p_cols:
                cursor.execute("ALTER TABLE packet_paths ADD COLUMN source TEXT DEFAULT 'radio'")


            # Hop Route Preferences table (user-chosen repeater overrides for prefixes)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hop_route_preferences (
                    hop_prefix TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    alias TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_hop_pref_prefix ON hop_route_preferences(hop_prefix)")

            # Phantom Nodes table (user-marked false-positive faraway collisions to exclude from map)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS phantom_nodes (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    created_at TEXT
                )
            """)

            # Generic App State table (key-value settings and active state persistence)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT
                )
            """)

            # One-time baseline migration: Mark all currently discovered node states as known (legacy baseline)
            # so that "new nodes" starts clean from now.
            cursor.execute("SELECT value FROM app_state WHERE key = 'new_nodes_baseline_v2'")
            if not cursor.fetchone():
                try:
                    cursor.execute("UPDATE contacts SET first_seen = '2024-01-01T00:00:00+00:00'")
                    cursor.execute(
                        "INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES ('new_nodes_baseline_v2', '1', datetime('now'))"
                    )
                except Exception:
                    pass

            # Docked Companions table (Companion Orbitals docked to first relay repeater)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS docked_companions (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    repeater_id TEXT NOT NULL,
                    repeater_alias TEXT,
                    channel TEXT,
                    snr REAL,
                    last_heard TEXT,
                    is_unknown_first_hop INTEGER DEFAULT 0,
                    first_hop_alias TEXT DEFAULT ''
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docked_repeater ON docked_companions(repeater_id)")

            cursor.execute("PRAGMA table_info(docked_companions)")
            dc_cols = [col[1] for col in cursor.fetchall()]
            if "is_unknown_first_hop" not in dc_cols:
                cursor.execute("ALTER TABLE docked_companions ADD COLUMN is_unknown_first_hop INTEGER DEFAULT 0")
            if "first_hop_alias" not in dc_cols:
                cursor.execute("ALTER TABLE docked_companions ADD COLUMN first_hop_alias TEXT DEFAULT ''")
            # Satellite TLEs table (Orbital elements and radio frequency metadata)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS satellite_tles (
                    norad_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    line1 TEXT NOT NULL,
                    line2 TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    frequencies_json TEXT DEFAULT '[]',
                    is_favorite INTEGER DEFAULT 0
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sat_group ON satellite_tles(group_name)")
            cursor.execute("PRAGMA table_info(satellite_tles)")
            sat_cols = [col[1] for col in cursor.fetchall()]
            if "is_favorite" not in sat_cols:
                cursor.execute("ALTER TABLE satellite_tles ADD COLUMN is_favorite INTEGER DEFAULT 0")

            # Self-healing categorization: Ensure CubeSats and Space Stations are classified accurately in existing caches
            cursor.execute("""
                UPDATE satellite_tles
                SET group_name = 'cubesat'
                WHERE group_name = 'amateur'
                  AND (
                      UPPER(name) LIKE '%CUBE%'
                      OR UPPER(name) LIKE '%NANOSAT%'
                      OR UPPER(name) LIKE '%POCKETQUBE%'
                      OR UPPER(name) LIKE '%FOX-1%'
                      OR UPPER(name) LIKE '%RADFXSAT%'
                  )
            """)
            cursor.execute("""
                UPDATE satellite_tles
                SET group_name = 'stations'
                WHERE (
                    UPPER(name) LIKE '%ISS%'
                    OR UPPER(name) LIKE '%ZARYA%'
                    OR UPPER(name) LIKE '%NAUKA%'
                    OR UPPER(name) LIKE '%CSS%'
                    OR UPPER(name) LIKE '%TIANGONG%'
                    OR UPPER(name) LIKE '%TIANHE%'
                    OR UPPER(name) LIKE '%WENTIAN%'
                    OR UPPER(name) LIKE '%MENGTIAN%'
                    OR UPPER(name) LIKE '%DRAGON%'
                )
                AND UPPER(name) NOT LIKE '%SWISSCUBE%'
            """)

            # Run migrations for coordinate columns on existing databases
            cursor.execute("PRAGMA table_info(contacts)")
            c_cols = [col[1] for col in cursor.fetchall()]
            if "latitude" not in c_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN latitude REAL")
            if "longitude" not in c_cols:
                cursor.execute("ALTER TABLE contacts ADD COLUMN longitude REAL")

            cursor.execute("PRAGMA table_info(neighbours)")
            n_cols = [col[1] for col in cursor.fetchall()]
            if "latitude" not in n_cols:
                cursor.execute("ALTER TABLE neighbours ADD COLUMN latitude REAL")
            if "longitude" not in n_cols:
                cursor.execute("ALTER TABLE neighbours ADD COLUMN longitude REAL")

            # Prepopulate default channel (Public only) if empty
            cursor.execute("SELECT COUNT(*) as cnt FROM channels")
            if cursor.fetchone()["cnt"] == 0:
                defaults = [
                    (0, "Public", 0, 1, datetime.now(timezone.utc).isoformat(), 0),
                ]
                cursor.executemany(
                    "INSERT INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count) VALUES (?, ?, ?, ?, ?, ?)",
                    defaults
                )
            # Remove legacy default mockups and test simulation messages if present
            cursor.execute("DELETE FROM channels WHERE name IN ('#general', '#ops', '#telemetry')")
            cursor.execute("DELETE FROM messages WHERE id LIKE 'sim-%' OR sender_name IN ('Alice', 'Bob_Node', 'Charlie_Base')")
            cursor.execute("DELETE FROM contacts WHERE alias IN ('Alice', 'Bob', 'Bob_Node', 'Charlie') AND node_id IN ('!8f3a', '!9c21', '!10a4')")

            # Consolidate and deduplicate contacts with case discrepancies (e.g. DD2DF7A117C6 and dd2df7a117c6)
            try:
                cursor.execute("SELECT node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude, out_path_len, out_path_hash_mode, out_path FROM contacts")
                all_contacts = cursor.fetchall()
                merged_contacts = {}
                for row in all_contacts:
                    raw_nid = row["node_id"]
                    if not raw_nid:
                        continue
                    key = raw_nid.lower()
                    if key not in merged_contacts:
                        merged_contacts[key] = dict(row)
                    else:
                        cur = merged_contacts[key]
                        if not cur.get("alias") and row["alias"]:
                            cur["alias"] = row["alias"]
                        if row["is_favorite"]:
                            cur["is_favorite"] = 1
                        if row["is_repeater"]:
                            cur["is_repeater"] = 1
                        if (cur.get("latitude") is None or abs(cur["latitude"]) <= 0.0001) and row["latitude"] is not None and abs(row["latitude"]) > 0.0001:
                            cur["latitude"] = row["latitude"]
                            cur["longitude"] = row["longitude"]
                        if (cur.get("out_path_len") is None or cur.get("out_path_len") == -1) and row["out_path_len"] is not None and row["out_path_len"] >= 0:
                            cur["out_path_len"] = row["out_path_len"]
                            cur["out_path_hash_mode"] = row["out_path_hash_mode"]
                            cur["out_path"] = row["out_path"]
                        if not cur.get("public_key") and row["public_key"]:
                            cur["public_key"] = row["public_key"]

                if len(merged_contacts) < len(all_contacts):
                    for key, data in merged_contacts.items():
                        cursor.execute("DELETE FROM contacts WHERE lower(node_id) = ?", (key,))
                        cursor.execute("""
                            INSERT INTO contacts (
                                node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude,
                                out_path_len, out_path_hash_mode, out_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            data["node_id"], data["alias"], data["is_favorite"], data["last_seen"],
                            data["public_key"], data["is_repeater"], data["snr_db"], data["rssi_dbm"],
                            data["latitude"], data["longitude"], data["out_path_len"], data["out_path_hash_mode"], data["out_path"]
                        ))
            except Exception as e:
                logger.debug(f"Contacts deduplication migration note: {e}")

            self._backfill_message_paths(cursor)
            self._backfill_docked_companions(cursor)

            # Enable WAL mode and composite performance indexes
            try:
                cursor.execute("PRAGMA journal_mode = WAL")
            except Exception:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_chan_unread ON messages(channel, is_direct_message, delivery_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_dm ON messages(is_direct_message, sender_id, recipient_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_last_seen ON contacts(last_seen DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_alias ON contacts(alias)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_node_id ON contacts(node_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_packet_paths_ts ON packet_paths(timestamp DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_neighbours_ts ON neighbours(last_heard_ts DESC)")

            conn.commit()

    def backup_database(self, reason: str = "shutdown") -> Optional[Path]:
        """Creates a timestamped rolling backup of the SQLite database."""
        if not self.db_path or not self.db_path.exists():
            return None
        try:
            # Checkpoint WAL first to flush all commits to the main DB file
            with self._get_connection() as conn:
                conn.commit()
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass

            backup_dir = self.db_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"meshcore_tray_{ts}_{reason}.db"

            # Use sqlite3 online backup API for safe, non-blocking copy
            with self._get_connection() as src_conn:
                dest_conn = sqlite3.connect(backup_file)
                src_conn.backup(dest_conn)
                dest_conn.close()

            # Also maintain a copy as meshcore_tray.db.bak
            bak_file = self.db_path.parent / "meshcore_tray.db.bak"
            try:
                import shutil
                shutil.copy2(backup_file, bak_file)
            except Exception:
                pass

            # Rotate backups: keep the 10 most recent
            backups = sorted(backup_dir.glob("meshcore_tray_*.db"), key=lambda p: p.stat().st_mtime)
            if len(backups) > 10:
                for old in backups[:-10]:
                    try:
                        old.unlink()
                    except Exception:
                        pass

            logger.info(f"Created database backup at {backup_file} (reason: {reason})")
            return backup_file
        except Exception as e:
            logger.error(f"Failed to backup database: {e}", exc_info=True)
            return None

    def verify_and_sanitize_database(
        self,
        home_lat: Optional[float] = None,
        home_lon: Optional[float] = None,
        max_rf_distance_km: float = 2000.0
    ) -> dict:
        """Verifies database health on startup, performs PRAGMA integrity check,
        sanitizes ocean/Null Island and implausible remote coordinates, fixes future timestamps,
        and purges corrupt phantom nodes (e.g. framing-shifted public keys)."""
        report = {
            "integrity_ok": False,
            "corrupt_coords_cleared": 0,
            "future_timestamps_fixed": 0,
            "phantom_nodes_removed": 0,
            "backup_created": None,
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. PRAGMA integrity check
            try:
                cursor.execute("PRAGMA integrity_check")
                row = cursor.fetchone()
                report["integrity_ok"] = bool(row and row[0] == "ok")
                if not report["integrity_ok"]:
                    logger.error(f"Database integrity check warning: {row}")
            except Exception as e:
                logger.error(f"Error running PRAGMA integrity_check: {e}")

            # 2. Find and sanitize ocean / equator coordinates in contacts and neighbours
            cursor.execute("""
                SELECT node_id, alias, latitude, longitude FROM contacts
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                  AND ((abs(latitude) < 0.01 AND abs(longitude) < 0.01)
                       OR latitude < -85.0 OR latitude > 85.0 OR longitude < -180.0 OR longitude > 180.0)
            """)
            bad_coords = cursor.fetchall()
            if bad_coords:
                if not report["backup_created"]:
                    report["backup_created"] = str(self.backup_database("pre_sanitize") or "")
                for bc in bad_coords:
                    logger.warning(
                        f"Sanitizing ocean/corrupt coordinates ({bc['latitude']}, {bc['longitude']}) "
                        f"for contact {bc['alias']} ({bc['node_id']})"
                    )
                cursor.execute("""
                    UPDATE contacts
                    SET latitude = NULL, longitude = NULL
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                      AND ((abs(latitude) < 0.01 AND abs(longitude) < 0.01)
                           OR latitude < -85.0 OR latitude > 85.0 OR longitude < -180.0 OR longitude > 180.0)
                """)
                cursor.execute("""
                    UPDATE neighbours
                    SET latitude = NULL, longitude = NULL
                    WHERE latitude IS NOT NULL
                      AND ((abs(latitude) < 0.01 AND abs(longitude) < 0.01) OR latitude < -85.0 OR latitude > 85.0)
                """)
                report["corrupt_coords_cleared"] = len(bad_coords)

            # 2b. Sanitize physically impossible RF coordinates (> max_rf_distance_km from home/reference station)
            ref_lat, ref_lon = home_lat, home_lon
            if ref_lat is None or ref_lon is None:
                cursor.execute("SELECT latitude, longitude FROM contacts WHERE is_repeater = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL")
                rep_coords = cursor.fetchall()
                if rep_coords:
                    lats = sorted([r["latitude"] for r in rep_coords])
                    lons = sorted([r["longitude"] for r in rep_coords])
                    ref_lat = lats[len(lats) // 2]
                    ref_lon = lons[len(lons) // 2]
                else:
                    ref_lat, ref_lon = 54.65897, -3.4346

            cursor.execute("SELECT node_id, alias, latitude, longitude FROM contacts WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
            all_known_coords = cursor.fetchall()
            impossible_nodes = []
            for c in all_known_coords:
                try:
                    d = calculate_haversine_distance_km(float(ref_lat), float(ref_lon), float(c["latitude"]), float(c["longitude"]))
                    if d > max_rf_distance_km:
                        impossible_nodes.append((c, d))
                except Exception:
                    pass

            if impossible_nodes:
                if not report["backup_created"]:
                    report["backup_created"] = str(self.backup_database("pre_sanitize") or "")
                for c_bad, dist_val in impossible_nodes:
                    logger.warning(
                        f"Sanitizing implausible remote RF coordinates ({c_bad['latitude']:.4f}, {c_bad['longitude']:.4f}) "
                        f"({dist_val:.0f}km from station > {max_rf_distance_km:.0f}km) for contact {c_bad['alias']} ({c_bad['node_id']})"
                    )
                    cursor.execute("UPDATE contacts SET latitude = NULL, longitude = NULL WHERE node_id = ?", (c_bad["node_id"],))
                    cursor.execute("UPDATE neighbours SET latitude = NULL, longitude = NULL WHERE node_id = ?", (c_bad["node_id"],))
                report["corrupt_coords_cleared"] += len(impossible_nodes)

            # 3. Future timestamps fix (> 24 hours from current UTC time)
            now_utc = datetime.now(timezone.utc)
            now_iso = now_utc.isoformat()
            future_cutoff = (now_utc + timedelta(days=1)).isoformat()
            cursor.execute("SELECT node_id, alias, last_seen FROM contacts WHERE last_seen > ?", (future_cutoff,))
            future_contacts = cursor.fetchall()
            if future_contacts:
                if not report["backup_created"]:
                    report["backup_created"] = str(self.backup_database("pre_sanitize") or "")
                for fc in future_contacts:
                    logger.warning(
                        f"Fixing future timestamp {fc['last_seen']} for contact {fc['alias']} ({fc['node_id']})"
                    )
                cursor.execute("UPDATE contacts SET last_seen = ? WHERE last_seen > ?", (now_iso, future_cutoff))
                cursor.execute("UPDATE neighbours SET last_heard_ts = ? WHERE last_heard_ts > ?", (now_iso, future_cutoff))
                report["future_timestamps_fixed"] = len(future_contacts)

            # 4. Detect and prune corrupt phantom / shifted nodes:
            cursor.execute("SELECT node_id, alias, public_key, is_repeater FROM contacts")
            all_c = cursor.fetchall()
            reps = [c for c in all_c if c["is_repeater"] and c["alias"]]
            phantoms_to_delete = []

            for c in all_c:
                c_id = (c["node_id"] or "").strip().lower()
                c_pk = (c["public_key"] or "").strip().lower()
                c_alias = (c["alias"] or "").strip().lower()

                for r in reps:
                    r_id = (r["node_id"] or "").strip().lower()
                    r_alias = (r["alias"] or "").strip().lower()
                    if r_id == c_id:
                        continue

                    # Check: public key contains another known node's ID shifted (framing corruption)
                    pk_shifted_match = (len(c_pk) >= 32 and r_id in c_pk[8:])

                    if pk_shifted_match:
                        cursor.execute("SELECT COUNT(*) as cnt FROM messages WHERE sender_id = ? OR recipient_id = ?", (c_id, c_id))
                        msg_cnt = cursor.fetchone()["cnt"]
                        if msg_cnt == 0:
                            phantoms_to_delete.append((c_id, c["alias"], r["alias"]))
                            break

            if phantoms_to_delete:
                if not report["backup_created"]:
                    report["backup_created"] = str(self.backup_database("pre_sanitize") or "")
                for p_id, p_alias, parent_alias in phantoms_to_delete:
                    logger.warning(
                        f"Purging corrupt phantom shifted node {p_alias} ({p_id}) "
                        f"(shifted duplicate of repeater {parent_alias})"
                    )
                    cursor.execute("DELETE FROM contacts WHERE node_id = ?", (p_id,))
                    cursor.execute("DELETE FROM neighbours WHERE node_id = ?", (p_id,))
                    cursor.execute("DELETE FROM docked_companions WHERE node_id = ?", (p_id,))
                report["phantom_nodes_removed"] = len(phantoms_to_delete)

            # 5. Consolidate and deduplicate channels (e.g. 'northwest' vs '#northwest')
            cursor.execute("SELECT * FROM channels")
            all_chans = cursor.fetchall()
            chan_groups_map = {}
            for ch in all_chans:
                name = ch["name"]
                if not name:
                    continue
                clean = name.strip().lstrip("#").lower()
                key = "public" if clean == "public" else clean
                if key not in chan_groups_map:
                    chan_groups_map[key] = []
                chan_groups_map[key].append(ch)

            for key, dupes in chan_groups_map.items():
                if len(dupes) > 1:
                    master = next((c for c in dupes if (c["name"].startswith("#") or c["name"] == "Public")), dupes[0])
                    others = [c for c in dupes if c["channel_id"] != master["channel_id"]]
                    combined_fav = any(bool(c["is_favorite"]) for c in dupes)
                    combined_unread = max(int(c["unread_count"] or 0) for c in dupes)
                    combined_pixoo = any(bool(c["is_pixoo_enabled"]) for c in dupes)
                    combined_ts = max((c["last_activity_ts"] or "") for c in dupes)
                    canonical_name = "Public" if key == "public" else f"#{key}"

                    cursor.execute("""
                        UPDATE channels
                        SET name = ?, is_favorite = ?, unread_count = ?, is_pixoo_enabled = ?, last_activity_ts = ?
                        WHERE channel_id = ?
                    """, (canonical_name, int(combined_fav), combined_unread, int(combined_pixoo), combined_ts, master["channel_id"]))

                    for o in others:
                        cursor.execute("UPDATE messages SET channel = ? WHERE channel = ?", (canonical_name, o["name"]))
                        cursor.execute("DELETE FROM channels WHERE channel_id = ?", (o["channel_id"],))
                        logger.info(f"Merged duplicate channel '{o['name']}' into '{canonical_name}'")

            conn.commit()

        return report

    def _backfill_docked_companions(self, cursor: sqlite3.Cursor):
        """Scans message history for GPS-less companion nodes and docks them to their first heard relay repeater."""
        try:
            # Build lookup map of known repeaters with valid GPS coordinates
            cursor.execute("""
                SELECT node_id, alias, latitude, longitude FROM contacts
                WHERE is_repeater = 1
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                  AND abs(latitude) >= 1.0 AND NOT (abs(latitude) < 5.0 AND abs(longitude) < 5.0)
            """)
            known_rep_rows = cursor.fetchall()
            known_rep_map = {}
            for kr in known_rep_rows:
                r_nid = str(kr["node_id"]).strip()
                r_alias = str(kr["alias"] or "").strip()
                known_rep_map[r_nid.lower()] = kr
                if r_alias:
                    known_rep_map[r_alias.lower()] = kr
                    clean_a = r_alias.lstrip("@").lower()
                    known_rep_map[clean_a] = kr

            cursor.execute("""
                SELECT node_id, alias FROM contacts
                WHERE is_repeater = 0
                  AND (latitude IS NULL OR abs(latitude) < 1.0 OR (abs(latitude) < 5.0 AND abs(longitude) < 5.0))
            """)
            gpsless_nodes = cursor.fetchall()


            for c in gpsless_nodes:
                nid = c["node_id"]
                alias = c["alias"] or nid

                # Query all messages from this node in reverse chronological order
                cursor.execute("""
                    SELECT metadata_json, channel, timestamp FROM messages
                    WHERE (sender_id = ? OR sender_id = ? OR sender_name = ?)
                      AND metadata_json IS NOT NULL AND metadata_json != ''
                    ORDER BY timestamp DESC
                """, (nid, f"!{nid}", alias))
                msgs = cursor.fetchall()

                rep_contact = None
                is_unknown_first = False
                orig_first_hop = ""
                matched_channel = "Public"
                matched_snr = 0.0
                matched_ts = None

                for m in msgs:
                    try:
                        meta = json.loads(m["metadata_json"] or "{}")
                    except Exception:
                        meta = {}

                    hop_nodes = meta.get("hop_nodes") or []
                    if not hop_nodes or len(hop_nodes) == 0:
                        continue

                    first_raw = str(hop_nodes[0]).strip()
                    first_clean = first_raw.lstrip("@").lower()
                    orig_first_hop = first_raw

                    if first_clean in known_rep_map:
                        rep_contact = known_rep_map[first_clean]
                        is_unknown_first = False
                        matched_channel = m["channel"] or "Public"
                        matched_snr = meta.get("snr", 0.0)
                        matched_ts = m["timestamp"]
                        break
                    else:
                        # First hop is unknown: inspect route for first known repeater in the list
                        for hop in hop_nodes[1:]:
                            h_clean = str(hop).strip().lstrip("@").lower()
                            if h_clean in known_rep_map:
                                rep_contact = known_rep_map[h_clean]
                                is_unknown_first = True
                                matched_channel = m["channel"] or "Public"
                                matched_snr = meta.get("snr", 0.0)
                                matched_ts = m["timestamp"]
                                break
                        if rep_contact:
                            break

                # If no hops resolved, check if node alias or ID belongs to M7NCY (user's home station)
                if not rep_contact:
                    is_user_node = ("m7ncy" in alias.lower() or "m7ncy" in nid.lower())
                    if is_user_node and "m7ncy west yagi" in known_rep_map:
                        rep_contact = known_rep_map["m7ncy west yagi"]
                        is_unknown_first = False
                        orig_first_hop = "M7NCY West Yagi"
                        matched_channel = "Public"
                        matched_ts = msgs[0]["timestamp"] if msgs else datetime.now(timezone.utc).isoformat()
                        matched_snr = 0.0

                if rep_contact:
                    rep_id = rep_contact["node_id"]
                    rep_alias = rep_contact["alias"] or rep_id
                    cursor.execute("""
                        INSERT OR REPLACE INTO docked_companions (
                            node_id, alias, repeater_id, repeater_alias, channel, snr, last_heard,
                            is_unknown_first_hop, first_hop_alias
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        nid, alias, rep_id, rep_alias, matched_channel, matched_snr,
                        matched_ts or datetime.now(timezone.utc).isoformat(),
                        1 if is_unknown_first else 0,
                        orig_first_hop
                    ))
                else:
                    # Remove any previous invalid docking (e.g. from previous hardcoded fallback)
                    cursor.execute("DELETE FROM docked_companions WHERE node_id = ?", (nid,))
        except Exception as e:
            logger.debug(f"Error backfilling docked companions: {e}")

    def _backfill_message_paths(self, cursor: sqlite3.Cursor):
        """Correlates past messages with packet_paths table to backfill missing hop_nodes / path metadata."""
        try:
            cursor.execute("""
                SELECT id, timestamp, metadata_json FROM messages
                WHERE metadata_json LIKE '%"path": ""%' AND metadata_json NOT LIKE '%"path_len": 0%'
            """)
            rows = cursor.fetchall()
            for r in rows:
                msg_id = r["id"]
                msg_ts = r["timestamp"]
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    continue

                p_len = meta.get("path_len", 0)
                if p_len <= 0 or meta.get("path"):
                    continue

                cursor.execute("""
                    SELECT packet_id, hop_nodes, route_type FROM packet_paths
                    WHERE ABS(strftime('%s', timestamp) - strftime('%s', ?)) <= 5
                    ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC
                    LIMIT 1
                """, (msg_ts, msg_ts))
                match = cursor.fetchone()
                if match:
                    try:
                        hops = json.loads(match["hop_nodes"] or "[]")
                    except Exception:
                        hops = []
                    if hops:
                        meta["hop_nodes"] = hops
                        meta["repeaters"] = hops
                        meta["route_type"] = match["route_type"] or meta.get("route_type", "FLOOD")
                        cursor.execute("UPDATE messages SET metadata_json = ? WHERE id = ?", (json.dumps(meta), msg_id))
        except Exception as e:
            logger.debug(f"Error backfilling message paths: {e}")

    # --- Message Operations ---

    def save_message(self, msg: MessageEnvelope):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Precedence rule: Physical RF Radio messages supersede MQTT network messages
            clean_chan = (msg.channel or "").strip()
            clean_text = (msg.text or "").strip()
            if clean_chan and clean_text:
                if msg.source_driver == "mqtt":
                    # Drop incoming MQTT message if radio already heard this exact message recently
                    cursor.execute("""
                        SELECT id FROM messages
                        WHERE (source_driver != 'mqtt' OR source_driver IS NULL)
                          AND (channel = ? OR channel = ? OR channel = ?)
                          AND text = ?
                          AND timestamp >= datetime('now', '-120 seconds')
                        LIMIT 1
                    """, (clean_chan, clean_chan.lstrip('#'), f"#{clean_chan.lstrip('#')}", clean_text))
                    if cursor.fetchone():
                        logger.debug("Dropped MQTT message '%s' on %s because it was already heard over RF radio", clean_text[:20], clean_chan)
                        return
                else:
                    # Physical radio message: remove any earlier MQTT placeholder for the same text
                    cursor.execute("""
                        DELETE FROM messages
                        WHERE source_driver = 'mqtt'
                          AND (channel = ? OR channel = ? OR channel = ?)
                          AND text = ?
                          AND timestamp >= datetime('now', '-120 seconds')
                    """, (clean_chan, clean_chan.lstrip('#'), f"#{clean_chan.lstrip('#')}", clean_text))

            cursor.execute("""
                INSERT OR REPLACE INTO messages (
                    id, timestamp, source_driver, sender_id, sender_name, is_favorite,
                    channel, channel_id, is_direct_message, recipient_id, recipient_name,
                    text, metadata_json, delivery_status, is_outgoing, is_mention, matched_keywords_json,
                    repeats_heard
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.id, msg.timestamp, msg.source_driver, msg.sender_id, msg.sender_name,
                int(msg.is_favorite), msg.channel, msg.channel_id, int(msg.is_direct_message),
                msg.recipient_id, msg.recipient_name, msg.text, json.dumps(msg.metadata),
                msg.delivery_status, int(msg.is_outgoing), int(msg.is_mention),
                json.dumps(msg.matched_keywords), getattr(msg, "repeats_heard", 0)
            ))
            # Touch channel activity
            if not msg.is_direct_message and msg.channel:
                cursor.execute("""
                    UPDATE channels SET last_activity_ts = ? WHERE name = ?
                """, (msg.timestamp, msg.channel))

            # Auto-record contact if incoming message (preserve existing favorite status, GPS coords, default 0)
            if not msg.is_outgoing and msg.sender_name and msg.sender_name not in ("Unknown", "Anonymous"):
                cursor.execute("""
                    INSERT OR REPLACE INTO contacts (
                        node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude
                    ) VALUES (
                        ?, ?,
                        COALESCE((SELECT is_favorite FROM contacts WHERE node_id = ?), 0),
                        ?,
                        COALESCE((SELECT public_key FROM contacts WHERE node_id = ?), ''),
                        COALESCE((SELECT is_repeater FROM contacts WHERE node_id = ?), 0),
                        ?, ?,
                        (SELECT latitude FROM contacts WHERE node_id = ?),
                        (SELECT longitude FROM contacts WHERE node_id = ?)
                    )
                """, (
                    msg.sender_id or msg.sender_name,
                    msg.sender_name,
                    msg.sender_id or msg.sender_name,
                    msg.timestamp,
                    msg.sender_id or msg.sender_name,
                    msg.sender_id or msg.sender_name,
                    msg.metadata.get("snr", 0.0),
                    msg.metadata.get("rssi", -100.0),
                    msg.sender_id or msg.sender_name,
                    msg.sender_id or msg.sender_name
                ))
            conn.commit()

        # Auto-discover regional scopes from channel and message hashtags
        try:
            self.discover_scopes_from_text(
                text=msg.text or "",
                channel=msg.channel or "",
                sender_id=msg.sender_id or ""
            )
        except Exception as e:
            logger.debug(f"Scope discovery hook note: {e}")

    def get_messages(self, channel: Optional[str] = None, contact_id: Optional[str] = None, limit: int = 150, include_dms: bool = False) -> List[MessageEnvelope]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if contact_id:
                clean_id = contact_id.lstrip("!@").strip()
                contact = self.get_contact(contact_id)
                alias = contact.alias if contact else clean_id
                pubkey = contact.public_key if contact else clean_id
                node_id = contact.node_id if contact else clean_id
                cursor.execute("""
                    SELECT * FROM messages
                    WHERE is_direct_message = 1 AND (
                        sender_id IN (?, ?, ?, ?) OR
                        recipient_id IN (?, ?, ?, ?) OR
                        sender_name = ? OR
                        recipient_name = ?
                    )
                    ORDER BY timestamp DESC LIMIT ?
                """, (contact_id, clean_id, pubkey, node_id, contact_id, clean_id, pubkey, node_id, alias, alias, limit))
            elif channel:
                clean_ch = channel.lstrip("#")
                cursor.execute("""
                    SELECT * FROM messages
                    WHERE is_direct_message = 0 AND (channel = ? OR channel = ? OR channel = ?)
                    ORDER BY timestamp DESC LIMIT ?
                """, (channel, clean_ch, f"#{clean_ch}", limit))
            else:
                if include_dms:
                    cursor.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,))
                else:
                    cursor.execute("SELECT * FROM messages WHERE is_direct_message = 0 ORDER BY timestamp DESC LIMIT ?", (limit,))

            rows = cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(rows)]

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

    def delete_message(self, message_id: str):
        """Deletes a message by ID from SQLite history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.commit()

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
            matched_keywords=json.loads(r["matched_keywords_json"]) if r["matched_keywords_json"] else [],
            repeats_heard=r["repeats_heard"] if ("repeats_heard" in r.keys() and r["repeats_heard"] is not None) else 0
        )

    def get_message(self, message_id: str) -> Optional[MessageEnvelope]:
        """Lookup a message by its ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            r = cursor.fetchone()
            return self._row_to_message(r) if r else None

    def update_message_repeats(self, message_id: str, repeats_heard: int):
        """Updates the number of repeater rebroadcasts heard for an outgoing message."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET repeats_heard = ? WHERE id = ?", (repeats_heard, message_id))
            conn.commit()

    def cleanup_echo_messages(self, local_alias: str, local_node_id: Optional[str] = None) -> int:
        """Purges any legacy incoming messages that were echoes/rebroadcasts of local transmissions."""
        if not local_alias or local_alias.strip() in ("Heltec-V3", "Local Node", "Local", ""):
            return 0
        clean_alias = local_alias.strip().lower()
        deleted_count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if local_node_id:
                clean_id = local_node_id.strip().lower()
                cursor.execute("""
                    DELETE FROM messages
                    WHERE is_outgoing = 0
                      AND (
                          lower(trim(sender_name)) = ?
                          OR lower(trim(sender_id)) = ?
                          OR lower(trim(sender_id)) = ?
                      )
                """, (clean_alias, clean_id, f"!{clean_alias}"))
            else:
                cursor.execute("""
                    DELETE FROM messages
                    WHERE is_outgoing = 0
                      AND (
                          lower(trim(sender_name)) = ?
                          OR lower(trim(sender_id)) = ?
                      )
                """, (clean_alias, f"!{clean_alias}"))
            deleted_count = cursor.rowcount
            conn.commit()
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} legacy echo message(s) for local station '{local_alias}'")
        return deleted_count


    def get_read_state(self, target_key: str) -> Optional[dict]:
        return self.get_last_read(target_key)

    def get_channel_unread_count(self, channel_name: str) -> int:
        """Returns the count of unread incoming messages for a channel."""
        clean = channel_name.lstrip("#")
        target_key = f"chan:{clean}"
        read_state = self.get_last_read(target_key)
        last_ts = read_state["last_read_timestamp"] if read_state else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if last_ts:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM messages
                    WHERE (channel = ? OR channel = ? OR channel = ?)
                      AND is_direct_message = 0
                      AND is_outgoing = 0
                      AND timestamp > ?
                """, (channel_name, clean, f"#{clean}", last_ts))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM messages
                    WHERE (channel = ? OR channel = ? OR channel = ?)
                      AND is_direct_message = 0
                      AND is_outgoing = 0
                """, (channel_name, clean, f"#{clean}"))
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    # --- Channel & Contact Operations ---

    def get_channels(self) -> List[ChannelInfo]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM channels ORDER BY channel_id ASC")
                raw = cursor.fetchall()
                seen = {}
                for r in raw:
                    name = r["name"]
                    if not name:
                        continue
                    clean = name.strip().lstrip("#").lower()
                    key = clean
                    if key not in seen:
                        seen[key] = ChannelInfo(
                            channel_id=r["channel_id"],
                            name=name,
                            is_favorite=bool(r["is_favorite"]),
                            is_pixoo_enabled=bool(r["is_pixoo_enabled"]),
                            last_activity_ts=r["last_activity_ts"] or "",
                            unread_count=r["unread_count"] or 0
                        )
                    else:
                        if r["is_favorite"]:
                            seen[key].is_favorite = True
                        if r["unread_count"] and r["unread_count"] > seen[key].unread_count:
                            seen[key].unread_count = r["unread_count"]
                return list(seen.values())
        except sqlite3.OperationalError:
            return []

    def save_channel(self, ch: ChannelInfo):
        clean = ch.name.strip().lstrip("#")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT channel_id, name FROM channels WHERE name = ? OR name = ? OR name = ? LIMIT 1",
                           (ch.name, f"#{clean}", clean))
            row = cursor.fetchone()
            cid = row["channel_id"] if row else ch.channel_id
            if ch.name.strip().lower() == "public":
                name_to_use = "Public"
            elif ch.name.startswith("#"):
                name_to_use = ch.name
            elif row and row["name"] and row["name"].startswith("#"):
                name_to_use = row["name"]
            else:
                name_to_use = f"#{clean}"
            cursor.execute("""
                INSERT OR REPLACE INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cid, name_to_use, int(ch.is_favorite), int(ch.is_pixoo_enabled), ch.last_activity_ts, ch.unread_count))
            conn.commit()

    def get_next_available_channel_slot(self) -> int:
        """Finds an unused hardware channel slot (1-7), or evicts/recycles the lowest priority slot if full."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT channel_id FROM channels WHERE channel_id >= 1 AND channel_id <= 7")
            used_slots = {r["channel_id"] for r in cursor.fetchall()}
            for s in range(1, 8):
                if s not in used_slots:
                    return s
            # If all slots 1..7 are in use, pick non-favorite with oldest activity
            cursor.execute("""
                SELECT channel_id FROM channels
                WHERE channel_id >= 1 AND channel_id <= 7
                ORDER BY is_favorite ASC, last_activity_ts ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return row["channel_id"]
            return 1

    def get_channel_by_name(self, name: str) -> Optional[ChannelInfo]:
        """Find channel by exact name or without # prefix."""
        clean_name = name.lstrip("#")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channels
                WHERE name = ? OR name = ? OR name = ?
                LIMIT 1
            """, (name, f"#{clean_name}", clean_name))
            r = cursor.fetchone()
            if not r:
                return None
            return ChannelInfo(
                channel_id=r["channel_id"],
                name=r["name"],
                is_favorite=bool(r["is_favorite"]),
                is_pixoo_enabled=bool(r["is_pixoo_enabled"]),
                last_activity_ts=r["last_activity_ts"] or "",
                unread_count=r["unread_count"] or 0
            )

    get_channel = get_channel_by_name

    def delete_channel(self, channel_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
            conn.commit()

    def update_channel_pixoo_enabled(self, channel_name: str, is_pixoo_enabled: bool):
        """Updates whether a channel is displayed on Pixoo 64 matrix."""
        clean = channel_name.lstrip("#")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE channels SET is_pixoo_enabled = ?
                WHERE name = ? OR name = ? OR name = ?
            """, (int(is_pixoo_enabled), channel_name, clean, f"#{clean}"))
            conn.commit()

    def batch_update_channel_preferences(self, prefs: List[Dict[str, Any]]):
        """Batches updates for channel pixoo_enabled and favorite status in a single atomic transaction."""
        if not prefs:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for p in prefs:
                chan_name = p.get("name", "")
                if not chan_name:
                    continue
                clean = chan_name.lstrip("#")
                is_pix = int(p.get("is_pixoo_enabled", True))
                is_fav = int(p.get("is_favorite", False))
                cursor.execute("""
                    UPDATE channels SET is_pixoo_enabled = ?, is_favorite = ?
                    WHERE name = ? OR name = ? OR name = ?
                """, (is_pix, is_fav, chan_name, clean, f"#{clean}"))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT MAX(channel_id) as max_id FROM channels")
                    row = cursor.fetchone()
                    next_id = (row["max_id"] + 1) if (row and row["max_id"] is not None) else 1
                    cursor.execute("""
                        INSERT OR IGNORE INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count)
                        VALUES (?, ?, ?, ?, '', 0)
                    """, (next_id, f"#{clean}", is_fav, is_pix))
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

    @staticmethod
    def _row_to_contact(r: sqlite3.Row) -> NodeContact:
        keys = r.keys() if hasattr(r, "keys") else []
        allowed_reg = []
        if "allowed_regions" in keys and r["allowed_regions"]:
            try:
                allowed_reg = json.loads(r["allowed_regions"])
            except Exception:
                allowed_reg = []
        return NodeContact(
            node_id=r["node_id"] or (r["alias"] if "alias" in keys else ""),
            alias=r["alias"] or r["node_id"],
            is_favorite=bool(r["is_favorite"]),
            last_seen=r["last_seen"] or "",
            public_key=r["public_key"] or "",
            is_repeater=bool(r["is_repeater"]),
            snr_db=r["snr_db"] or 0.0,
            rssi_dbm=r["rssi_dbm"] or -100.0,
            latitude=r["latitude"],
            longitude=r["longitude"],
            out_path_len=r["out_path_len"] if ("out_path_len" in keys and r["out_path_len"] is not None) else -1,
            out_path_hash_mode=r["out_path_hash_mode"] if ("out_path_hash_mode" in keys and r["out_path_hash_mode"] is not None) else -1,
            out_path=r["out_path"] if ("out_path" in keys and r["out_path"]) else "",
            scope_name=r["scope_name"] if ("scope_name" in keys and r["scope_name"]) else "",
            allowed_regions=allowed_reg,
            is_room_server=bool(r["is_room_server"]) if ("is_room_server" in keys and r["is_room_server"] is not None) else False,
            first_seen=r["first_seen"] if ("first_seen" in keys and r["first_seen"]) else ""
        )

    def mark_all_contacts_as_known(self) -> int:
        """Marks all currently recorded contacts as known by setting first_seen to a legacy baseline timestamp.
        Returns the number of contacts updated."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE contacts SET first_seen = '2024-01-01T00:00:00+00:00'")
            count = cursor.rowcount
            cursor.execute(
                "INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES ('new_nodes_baseline_v2', '1', datetime('now'))"
            )
            conn.commit()
            self._invalidate_contact_cache()
            return count

    def get_contacts(self) -> List[NodeContact]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM contacts ORDER BY alias ASC")
            return [self._row_to_contact(r) for r in cursor.fetchall()]

    def save_contact(self, contact: NodeContact, update_role: bool = False):
        nid = (contact.node_id or "").strip()
        pk = (contact.public_key or "").strip().lower()
        if not is_valid_node_id(nid) and not is_valid_node_id(pk):
            return

        lat = contact.latitude
        lon = contact.longitude
        if not is_valid_coordinate(lat, lon):
            lat, lon = None, None

        last_seen = contact.last_seen or ""
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > datetime.now(timezone.utc) + timedelta(days=1):
                    last_seen = datetime.now(timezone.utc).isoformat()
            except Exception:
                pass

        p_len = getattr(contact, "out_path_len", -1)
        if p_len is not None and (p_len > 16 or p_len < -1):
            p_len = -1

        alias = contact.alias
        valid_alias = is_valid_alias(alias)

        sc_name = getattr(contact, "scope_name", "") or ""
        al_reg = getattr(contact, "allowed_regions", []) or []
        al_reg_json = json.dumps(al_reg) if isinstance(al_reg, list) else str(al_reg)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if nid:
                cursor.execute("SELECT node_id, alias FROM contacts WHERE lower(node_id) = lower(?) LIMIT 1", (nid,))
                r = cursor.fetchone()
                if r:
                    nid = r[0]
                    if not valid_alias and is_valid_alias(r[1]):
                        alias = r[1]
                        valid_alias = True
            elif pk:
                pk_id = pk[:12]
                cursor.execute("SELECT node_id, alias FROM contacts WHERE lower(node_id) = lower(?) LIMIT 1", (pk_id,))
                r = cursor.fetchone()
                if r:
                    nid = r[0]
                    if not valid_alias and is_valid_alias(r[1]):
                        alias = r[1]
                        valid_alias = True
                else:
                    nid = pk_id

            if not valid_alias:
                alias = nid[:8] if is_valid_node_id(nid) else "Node"

            # Detect and preserve room server flag
            is_room_flag = 1 if (getattr(contact, "is_room_server", False) or is_room_server_contact(contact) or is_room_server_contact({"alias": alias, "node_id": nid})) else 0
            if not is_room_flag:
                clean_nid = nid.lstrip("!@").lower()
                cursor.execute("""
                    SELECT 1 FROM room_credentials
                    WHERE lower(node_id) = ?
                       OR lower(node_id) = ('!' || ?)
                       OR ('!' || lower(node_id)) = ?
                    LIMIT 1
                """, (clean_nid, clean_nid, clean_nid))
                if cursor.fetchone():
                    is_room_flag = 1

            first_seen = getattr(contact, "first_seen", "") or ""
            if not first_seen:
                first_seen = datetime.now(timezone.utc).isoformat()

            cursor.execute("""
                INSERT INTO contacts (
                    node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude,
                    out_path_len, out_path_hash_mode, out_path, scope_name, allowed_regions, is_room_server, first_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE contacts.alias END,
                    is_favorite = CASE WHEN excluded.is_favorite != 0 THEN excluded.is_favorite ELSE contacts.is_favorite END,
                    last_seen = COALESCE(NULLIF(excluded.last_seen, ''), contacts.last_seen),
                    public_key = COALESCE(NULLIF(excluded.public_key, ''), contacts.public_key),
                    is_repeater = CASE WHEN ? != 0 THEN excluded.is_repeater WHEN excluded.is_repeater != 0 THEN excluded.is_repeater ELSE contacts.is_repeater END,
                    is_room_server = CASE WHEN ? != 0 THEN excluded.is_room_server WHEN excluded.is_room_server != 0 THEN excluded.is_room_server ELSE contacts.is_room_server END,
                    snr_db = CASE WHEN excluded.snr_db != 0.0 THEN excluded.snr_db ELSE contacts.snr_db END,
                    rssi_dbm = CASE WHEN excluded.rssi_dbm != -100.0 THEN excluded.rssi_dbm ELSE contacts.rssi_dbm END,
                    latitude = CASE WHEN excluded.latitude IS NOT NULL AND abs(excluded.latitude) >= 1.0 AND excluded.latitude BETWEEN -85.0 AND 85.0 AND NOT (abs(excluded.latitude) < 5.0 AND abs(excluded.longitude) < 5.0) THEN excluded.latitude ELSE contacts.latitude END,
                    longitude = CASE WHEN excluded.longitude IS NOT NULL AND excluded.longitude BETWEEN -180.0 AND 180.0 AND excluded.latitude IS NOT NULL AND abs(excluded.latitude) >= 1.0 AND NOT (abs(excluded.latitude) < 5.0 AND abs(excluded.longitude) < 5.0) THEN excluded.longitude ELSE contacts.longitude END,
                    out_path_len = CASE WHEN excluded.out_path_len != -1 AND excluded.out_path_len <= 16 THEN excluded.out_path_len ELSE contacts.out_path_len END,
                    out_path_hash_mode = CASE WHEN excluded.out_path_hash_mode != -1 THEN excluded.out_path_hash_mode ELSE contacts.out_path_hash_mode END,
                    out_path = CASE WHEN excluded.out_path != '' THEN excluded.out_path ELSE contacts.out_path END,
                    scope_name = CASE WHEN excluded.scope_name != '' THEN excluded.scope_name ELSE contacts.scope_name END,
                    allowed_regions = CASE WHEN excluded.allowed_regions != '[]' AND excluded.allowed_regions != '' THEN excluded.allowed_regions ELSE contacts.allowed_regions END,
                    first_seen = CASE WHEN contacts.first_seen IS NOT NULL AND contacts.first_seen != '' THEN contacts.first_seen ELSE excluded.first_seen END
            """, (
                nid, alias, int(contact.is_favorite), last_seen,
                pk,
                int(contact.is_repeater), contact.snr_db, contact.rssi_dbm,
                lat, lon,
                p_len,
                getattr(contact, "out_path_hash_mode", -1),
                getattr(contact, "out_path", ""),
                sc_name,
                al_reg_json,
                is_room_flag,
                first_seen,
                1 if update_role else 0,
                1 if update_role else 0
            ))
            if is_valid_coordinate(lat, lon):
                cursor.execute("DELETE FROM docked_companions WHERE node_id = ? OR alias = ?", (nid, alias))
            conn.commit()
        if hasattr(self, "_contact_by_key_cache"):
            self._contact_by_key_cache.clear()

    def save_contacts_bulk(self, contacts: List[NodeContact]):
        """Efficient batch insertion/update of contacts without clobbering existing coordinates."""
        if not contacts:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT node_id, alias, latitude, longitude, is_room_server FROM contacts")
            existing_map = {r[0].lower(): (r[0], r[1], r[2], r[3], bool(r[4])) for r in cursor.fetchall() if r[0]}

            cursor.execute("SELECT lower(node_id) FROM room_credentials")
            cred_ids = {r[0].lstrip("!@") for r in cursor.fetchall() if r[0]}

            rows = []
            for c in contacts:
                raw_id = (c.node_id or "").strip()
                pk = (c.public_key or "").strip().lower()
                if not is_valid_node_id(raw_id) and not is_valid_node_id(pk):
                    continue
                if not raw_id and pk:
                    raw_id = pk[:12]

                target_id = raw_id
                target_alias = c.alias
                valid_alias = is_valid_alias(target_alias)
                ex_lat, ex_lon = None, None
                ex_room = False
                if raw_id.lower() in existing_map:
                    target_id, existing_alias, ex_lat, ex_lon, ex_room = existing_map[raw_id.lower()]
                    if not valid_alias and is_valid_alias(existing_alias):
                        target_alias = existing_alias
                        valid_alias = True

                if not valid_alias:
                    target_alias = target_id[:8]

                p_len = getattr(c, "out_path_len", -1)
                if p_len is not None and (p_len > 16 or p_len < -1):
                    p_len = -1

                c_lat = c.latitude
                c_lon = c.longitude
                if not is_valid_coordinate(c_lat, c_lon):
                    c_lat, c_lon = None, None

                # Protect existing known UK/Ireland coordinates from far-away corruption
                if ex_lat is not None and ex_lon is not None and (-12.0 <= ex_lon <= 3.0 and 49.0 <= ex_lat <= 62.0):
                    if not is_valid_alias(c.alias) or (c_lon is not None and (c_lon > 10.0 or c_lon < -15.0 or c_lat < 45.0 or c_lat > 65.0)):
                        c_lat = ex_lat
                        c_lon = ex_lon

                c_last_seen = c.last_seen or ""
                if c_last_seen:
                    try:
                        dt = datetime.fromisoformat(c_last_seen.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt > datetime.now(timezone.utc) + timedelta(days=1):
                            c_last_seen = datetime.now(timezone.utc).isoformat()
                    except Exception:
                        pass

                sc_name = getattr(c, "scope_name", "") or ""
                al_reg = getattr(c, "allowed_regions", []) or []
                al_reg_json = json.dumps(al_reg) if isinstance(al_reg, list) else str(al_reg)

                is_room_flag = int(getattr(c, "is_room_server", False) or is_room_server_contact(c) or is_room_server_contact({"alias": target_alias, "node_id": target_id}) or ex_room)
                if not is_room_flag and target_id.lower().lstrip("!@") in cred_ids:
                    is_room_flag = 1

                c_first_seen = getattr(c, "first_seen", "") or ""
                if not c_first_seen:
                    c_first_seen = c_last_seen or datetime.now(timezone.utc).isoformat()

                rows.append((
                    target_id, target_alias, int(c.is_favorite), c_last_seen,
                    pk,
                    int(c.is_repeater), c.snr_db, c.rssi_dbm, c_lat, c_lon,
                    p_len,
                    getattr(c, "out_path_hash_mode", -1),
                    getattr(c, "out_path", ""),
                    sc_name,
                    al_reg_json,
                    is_room_flag,
                    c_first_seen
                ))

            cursor.executemany("""
                INSERT INTO contacts (
                    node_id, alias, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude,
                    out_path_len, out_path_hash_mode, out_path, scope_name, allowed_regions, is_room_server, first_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE contacts.alias END,
                    is_favorite = CASE WHEN excluded.is_favorite != 0 THEN excluded.is_favorite ELSE contacts.is_favorite END,
                    last_seen = COALESCE(NULLIF(excluded.last_seen, ''), contacts.last_seen),
                    public_key = COALESCE(NULLIF(excluded.public_key, ''), contacts.public_key),
                    is_repeater = CASE WHEN excluded.is_repeater != 0 THEN excluded.is_repeater ELSE contacts.is_repeater END,
                    is_room_server = CASE WHEN excluded.is_room_server != 0 THEN excluded.is_room_server ELSE contacts.is_room_server END,
                    snr_db = CASE WHEN excluded.snr_db != 0.0 THEN excluded.snr_db ELSE contacts.snr_db END,
                    rssi_dbm = CASE WHEN excluded.rssi_dbm != -100.0 THEN excluded.rssi_dbm ELSE contacts.rssi_dbm END,
                    latitude = CASE WHEN excluded.latitude IS NOT NULL AND abs(excluded.latitude) >= 1.0 AND excluded.latitude BETWEEN -85.0 AND 85.0 AND NOT (abs(excluded.latitude) < 5.0 AND abs(excluded.longitude) < 5.0) THEN excluded.latitude ELSE contacts.latitude END,
                    longitude = CASE WHEN excluded.longitude IS NOT NULL AND excluded.longitude BETWEEN -180.0 AND 180.0 AND excluded.latitude IS NOT NULL AND abs(excluded.latitude) >= 1.0 AND NOT (abs(excluded.latitude) < 5.0 AND abs(excluded.longitude) < 5.0) THEN excluded.longitude ELSE contacts.longitude END,
                    out_path_len = CASE WHEN excluded.out_path_len != -1 AND excluded.out_path_len <= 16 THEN excluded.out_path_len ELSE contacts.out_path_len END,
                    out_path_hash_mode = CASE WHEN excluded.out_path_hash_mode != -1 THEN excluded.out_path_hash_mode ELSE contacts.out_path_hash_mode END,
                    out_path = CASE WHEN excluded.out_path != '' THEN excluded.out_path ELSE contacts.out_path END,
                    scope_name = CASE WHEN excluded.scope_name != '' THEN excluded.scope_name ELSE contacts.scope_name END,
                    allowed_regions = CASE WHEN excluded.allowed_regions != '[]' AND excluded.allowed_regions != '' THEN excluded.allowed_regions ELSE contacts.allowed_regions END,
                    first_seen = CASE WHEN contacts.first_seen IS NOT NULL AND contacts.first_seen != '' THEN contacts.first_seen ELSE excluded.first_seen END
            """, rows)
            conn.commit()
        if hasattr(self, "_contact_by_key_cache"):
            self._contact_by_key_cache.clear()

    def save_contact_scope(self, node_id: str, scope_name: str, allowed_regions: Optional[List[str]] = None):
        """Updates scope attribution for a repeater node."""
        clean_id = (node_id or "").strip().lstrip("!@")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if allowed_regions is not None:
                regions_json = json.dumps(allowed_regions)
                cursor.execute("""
                    UPDATE contacts
                    SET scope_name = ?, allowed_regions = ?
                    WHERE lower(node_id) = lower(?) OR lower(node_id) = lower(?) OR lower(public_key) LIKE ?
                """, (scope_name, regions_json, clean_id, f"!{clean_id}", f"{clean_id}%"))
            else:
                cursor.execute("""
                    UPDATE contacts
                    SET scope_name = ?
                    WHERE lower(node_id) = lower(?) OR lower(node_id) = lower(?) OR lower(public_key) LIKE ?
                """, (scope_name, clean_id, f"!{clean_id}", f"{clean_id}%"))
            conn.commit()
        self._invalidate_contact_cache()

    def discover_scopes_from_text(
        self,
        text: str = "",
        channel: str = "",
        sender_id: str = "",
        lat: Optional[float] = None,
        lon: Optional[float] = None
    ) -> List[str]:
        """Automatically discovers regional scopes from message text, hashtags, and channel names.
        Saves new scopes to discovered_scopes table and returns list of discovered scope IDs.
        """
        import re
        discovered = []
        candidates = set()

        # 1. From channel name (e.g. #gb-cum, #gb-nwk, #gb-nth, #gb-wales, #sco, #scotland, #cumbria, #northwest, #yorkshire)
        if channel:
            c_clean = channel.strip().lstrip("#").lower()
            if c_clean not in ("public", "general", "test", "ops", "telemetry", "emergency", "all"):
                if re.match(r'^(gb-[a-z0-9]+|sco-[a-z0-9]+|[a-z]{3,15})$', c_clean):
                    candidates.add(c_clean)

        # 2. From message text hashtags: e.g. #scope:gb-mid, #gb-mid, #scope:wales, [scope:xyz]
        if text:
            for m in re.finditer(r'(?:#scope:|\[scope:)([a-zA-Z0-9_-]+)\]?', text, re.IGNORECASE):
                candidates.add(m.group(1).lower())
            for m in re.finditer(r'#(gb-[a-zA-Z0-9]+|sco-[a-zA-Z0-9]+)', text, re.IGNORECASE):
                candidates.add(m.group(1).lower())

        if not candidates:
            return []

        NEON_PALETTE = [
            "#00E5FF",  # Electric Cyan
            "#76FF03",  # Neon Lime
            "#D500F9",  # Vivid Violet
            "#FF6D00",  # Neon Orange
            "#FFD600",  # Radiant Amber
            "#FF1744",  # Neon Red/Coral
            "#1DE9B6",  # Vibrant Teal
            "#F50057",  # Hot Pink
            "#651FFF",  # Deep Indigo
            "#00B0FF",  # Bright Sky
        ]

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for cand in candidates:
                cand_id = cand.strip().lower()
                if cand_id.startswith("gb-"):
                    sub = cand_id[3:].upper()
                    disp = f"{sub} (#{cand_id})"
                elif cand_id.startswith("sco-"):
                    sub = cand_id[4:].upper()
                    disp = f"Scotland {sub} (#{cand_id})"
                else:
                    disp = f"{cand_id.title()} (#{cand_id})"

                color_idx = abs(hash(cand_id)) % len(NEON_PALETTE)
                color = NEON_PALETTE[color_idx]

                cursor.execute("SELECT message_count, center_lat, center_lon FROM discovered_scopes WHERE scope_id = ?", (cand_id,))
                existing = cursor.fetchone()
                if existing:
                    new_cnt = existing["message_count"] + 1
                    c_lat = existing["center_lat"]
                    c_lon = existing["center_lon"]
                    if lat is not None and lon is not None and abs(lat) > 0.0001:
                        c_lat = (c_lat + lat) / 2.0 if c_lat else lat
                        c_lon = (c_lon + lon) / 2.0 if c_lon else lon
                    cursor.execute("""
                        UPDATE discovered_scopes
                        SET last_heard_ts = ?, message_count = ?, center_lat = ?, center_lon = ?
                        WHERE scope_id = ?
                    """, (now_iso, new_cnt, c_lat, c_lon, cand_id))
                else:
                    c_lat = lat if (lat is not None and abs(lat) > 0.0001) else 54.5
                    c_lon = lon if (lon is not None and abs(lon) > 0.0001) else -2.5
                    cursor.execute("""
                        INSERT INTO discovered_scopes (scope_id, display_name, color, source_type, last_heard_ts, message_count, center_lat, center_lon)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (cand_id, disp, color, "ota_activity", now_iso, 1, c_lat, c_lon))
                    logger.info(f"Discovered new regional RF scope: {cand_id} ({disp})")
                discovered.append(cand_id)
            conn.commit()
        return discovered

    def get_discovered_scopes(self) -> Dict[str, Dict[str, Any]]:
        """Returns all dynamically discovered scopes from over-the-air activity."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM discovered_scopes ORDER BY message_count DESC, last_heard_ts DESC")
                res = {}
                for r in cursor.fetchall():
                    c_lat = r["center_lat"] or 54.5
                    c_lon = r["center_lon"] or -2.5
                    res[r["scope_id"]] = {
                        "id": r["scope_id"],
                        "name": r["scope_id"],
                        "display_name": r["display_name"],
                        "color": r["color"],
                        "description": f"OTA Discovered Scope ({r['message_count']} messages heard)",
                        "center": [c_lat, c_lon],
                        "message_count": r["message_count"],
                        "last_heard_ts": r["last_heard_ts"] or "",
                    }
                return res
        except (sqlite3.OperationalError, Exception):
            return {}

    def get_scope_definitions(self=None) -> Dict[str, Dict[str, Any]]:
        """Returns standard scope definitions, human labels, and neon accent colors, merged with discovered scopes."""
        defs = {
            "gb-cum": {
                "id": "gb-cum",
                "name": "gb-cum",
                "display_name": "Cumbria (#gb-cum)",
                "color": "#00E5FF",  # Electric Cyan
                "description": "Cumbria, Lake District, Workington, Penrith, Kendal",
                "center": [54.55, -3.15],
            },
            "gb-nwk": {
                "id": "gb-nwk",
                "name": "gb-nwk",
                "display_name": "North West (#gb-nwk)",
                "color": "#00E676",  # Emerald Green
                "description": "Lancashire, Merseyside, Cheshire, Greater Manchester",
                "center": [53.60, -2.60],
            },
            "cax": {
                "id": "cax",
                "name": "cax",
                "display_name": "Carlisle & Border (#cax)",
                "color": "#2979FF",  # Royal Indigo
                "description": "Carlisle Area Exchange, Brampton, Border Corridor",
                "center": [54.90, -2.93],
            },
            "gb-nth": {
                "id": "gb-nth",
                "name": "gb-nth",
                "display_name": "Northern England (#gb-nth)",
                "color": "#D500F9",  # Vivid Violet
                "description": "Northern England regional backbone (Cumbria, Yorkshire, North East)",
                "center": [54.30, -1.80],
            },
            "sco": {
                "id": "sco",
                "name": "sco",
                "display_name": "Scotland (#sco)",
                "color": "#00B0FF",  # Scottish Blue
                "description": "Scotland (Dumfries & Galloway, Central Belt, Highlands)",
                "center": [55.80, -3.80],
            },
            "iom": {
                "id": "iom",
                "name": "iom",
                "display_name": "Isle of Man (#iom)",
                "color": "#FFD600",  # Warm Amber / Gold
                "description": "Isle of Man (Douglas, Ramsey, Snaefell)",
                "center": [54.23, -4.50],
            },
            "ioi": {
                "id": "ioi",
                "name": "ioi",
                "display_name": "Island of Ireland (#ioi)",
                "color": "#76FF03",  # Celtic Lime
                "description": "Northern Ireland and Republic of Ireland (Belfast, Dublin)",
                "center": [54.40, -6.20],
            },
        }
        if self is not None and hasattr(self, "get_discovered_scopes"):
            try:
                discovered = self.get_discovered_scopes()
                for s_id, s_data in discovered.items():
                    if s_id not in defs:
                        defs[s_id] = s_data
            except Exception as e:
                logger.debug(f"Merge discovered scopes note: {e}")
        return defs

    def get_repeaters_by_scope(self) -> Dict[str, Any]:
        """Groups all known repeaters with GPS coordinates into regional scopes.
        Uses explicit database assignment, alias heuristic patterns, and geographic boundaries.
        Returns a dict containing scope definitions, assigned repeaters, and gateway nodes.
        """
        scopes_meta = self.get_scope_definitions()
        result = {
            s_id: {
                "id": s_id,
                "name": s_info["name"],
                "display_name": s_info["display_name"],
                "color": s_info["color"],
                "description": s_info["description"],
                "center": s_info["center"],
                "nodes": [],
            }
            for s_id, s_info in scopes_meta.items()
        }

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM contacts
                WHERE is_repeater = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL
                  AND abs(latitude) > 0.0001 AND abs(longitude) > 0.0001
            """)
            contacts = [self._row_to_contact(r) for r in cursor.fetchall()]

        for c in contacts:
            lat = float(c.latitude)
            lon = float(c.longitude)
            alias_u = (c.alias or "").upper()

            # Determine primary scope
            assigned_scope = None

            # 1. Explicit database assignment
            if c.scope_name:
                sc_lower = c.scope_name.strip().lower()
                if sc_lower in ("none", "excluded", "hide", "hidden"):
                    # Explicitly excluded by operator from any regional scope overlays
                    continue
                if sc_lower in scopes_meta:
                    assigned_scope = sc_lower

            # 2. Alias heuristic matches (using word boundaries to prevent substrings like 'DOWN' matching 'BLACKDOWN')
            if not assigned_scope:
                if re.search(r'\b(SCO|ALBA|LOMOND|BLANTYRE|EDINBURGH|GLASGOW|ABERDEEN|DUNDEE|FIFE)\b', alias_u) or alias_u.startswith("SCO-") or alias_u.startswith("SCO_"):
                    assigned_scope = "sco"
                elif (re.search(r'\b(BHD|DUB|BELFAST|DUBLIN|SLIEVE|IRELAND|GROOMSPORT|PORTRUSH|ANTRIM|ARMAGH|EIRE)\b', alias_u) or "CO DOWN" in alias_u or alias_u.startswith("DUB-") or alias_u.startswith("BHD-")) and (lon < -5.0 or lat >= 53.0):
                    assigned_scope = "ioi"
                elif re.search(r'\b(IOM|MANX|DOUGLAS|RAMSEY|SNAEFELL|PEEL|CASTLETOWN)\b', alias_u) or alias_u.startswith("IOM-"):
                    assigned_scope = "iom"
                elif re.search(r'\b(CAX|CARLISLE|BRAMPTON|GRETNA|LONGTOWN)\b', alias_u) or alias_u.startswith("CAX-"):
                    assigned_scope = "cax"
                elif re.search(r'\b(CUMBRIA|M7NCY|KESWICK|WORKINGTON|PENRITH|KENDAL|WHITEHAVEN|AMBLESIDE|WINDERMERE|BARROW)\b', alias_u) or alias_u.startswith("CUM-"):
                    assigned_scope = "gb-cum"
                elif re.search(r'\b(NWK|M7FWD|LANCASTER|PRESTON|MANCHESTER|LIVERPOOL|BLACKPOOL|WHITTLE|CHESHIRE|WARRINGTON|BOLTON|WIGAN)\b', alias_u) or alias_u.startswith("NWK-"):
                    assigned_scope = "gb-nwk"
                elif re.search(r'\b(GB-NTH|YORKSHIRE|LEEDS|SHEFFIELD|NEWCASTLE|DURHAM)\b', alias_u) or alias_u.startswith("NTH-"):
                    assigned_scope = "gb-nth"

            # 3. Geographic bounding box fallback
            if not assigned_scope:
                if lon < -5.35 and lat >= 51.3:
                    assigned_scope = "ioi"
                elif 54.0 <= lat <= 54.45 and -4.85 <= lon <= -4.20:
                    assigned_scope = "iom"
                elif lat >= 55.15 and lon > -5.35:
                    assigned_scope = "sco"
                elif 54.80 <= lat <= 55.15 and -3.20 <= lon <= -2.60:
                    assigned_scope = "cax"
                elif 54.10 <= lat < 54.80 and -3.70 <= lon <= -2.60:
                    assigned_scope = "gb-cum"
                elif 53.0 <= lat < 54.10 and -3.30 <= lon <= -2.00:
                    assigned_scope = "gb-nwk"
                elif 53.5 <= lat <= 54.8 and -2.00 < lon <= -0.50:
                    assigned_scope = "gb-nth"

            # 4. Check discovered scopes matching alias
            if not assigned_scope:
                for sc_id in scopes_meta.keys():
                    if sc_id in alias_u.lower() or f"#{sc_id}" in alias_u.lower():
                        assigned_scope = sc_id
                        break

            allowed = c.allowed_regions or []
            is_gateway = len(allowed) > 1 or (assigned_scope == "cax" and "gb-cum" in allowed)

            node_dict = {
                "node_id": c.node_id,
                "alias": c.alias,
                "latitude": lat,
                "longitude": lon,
                "scope_name": assigned_scope or "unscoped",
                "allowed_regions": allowed,
                "is_gateway": is_gateway,
                "is_favorite": c.is_favorite,
                "snr_db": c.snr_db,
                "last_seen": c.last_seen,
            }

            if assigned_scope and assigned_scope in result:
                result[assigned_scope]["nodes"].append(node_dict)

        return result

    def set_contact_repeater_status(self, node_id: str, is_repeater: bool) -> bool:
        """Explicitly sets or toggles a contact's is_repeater flag across contacts and neighbours."""
        if not node_id:
            return False
        raw_id = (node_id or "").strip()
        clean_id = raw_id.lstrip("!@").strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE contacts
                SET is_repeater = ?
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (int(is_repeater), raw_id, clean_id, clean_id, raw_id, clean_id, raw_id))
            updated = cur.rowcount > 0
            cur.execute("""
                UPDATE neighbours
                SET is_repeater = ?
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (int(is_repeater), raw_id, clean_id, clean_id))
            conn.commit()
            self._invalidate_contact_cache()
            return updated

    def set_contact_room_server_status(self, node_id: str, is_room_server: bool) -> bool:
        """Explicitly sets or toggles a contact's is_room_server flag across contacts and neighbours."""
        if not node_id:
            return False
        raw_id = (node_id or "").strip()
        clean_id = raw_id.lstrip("!@").strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE contacts
                SET is_room_server = ?
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (int(is_room_server), raw_id, clean_id, clean_id, raw_id, clean_id, raw_id))
            updated = cur.rowcount > 0
            if not updated and is_room_server:
                cur.execute("""
                    INSERT OR IGNORE INTO contacts (node_id, alias, is_room_server)
                    VALUES (?, ?, 1)
                """, (clean_id, clean_id))
                updated = cur.rowcount > 0
            cur.execute("""
                UPDATE neighbours
                SET is_room_server = ?
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (int(is_room_server), raw_id, clean_id, clean_id))
            conn.commit()
            self._invalidate_contact_cache()
            return updated

    def has_room_credentials(self, node_id: str) -> bool:
        """Checks if saved room server credentials exist for the given node ID or alias."""
        if not node_id:
            return False
        clean_id = node_id.strip().lstrip("!@").lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM room_credentials
                WHERE lower(node_id) = ?
                   OR lower(node_id) = ('!' || ?)
                   OR ('!' || lower(node_id)) = ?
                LIMIT 1
            """, (clean_id, clean_id, clean_id))
            return cur.fetchone() is not None

    def get_room_servers(self) -> List[NodeContact]:
        """Returns all discovered Room Servers ordered by alias."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM contacts
                WHERE is_room_server = 1
                   OR lower(alias) LIKE '%[room]%'
                   OR lower(alias) LIKE '%[server]%'
                   OR lower(alias) LIKE '%-bbs%'
                   OR lower(alias) LIKE '%-room%'
                   OR lower(alias) LIKE '%room%'
                   OR lower(alias) LIKE '%bbs%'
                   OR lower(alias) LIKE '%server%'
                   OR lower(node_id) IN (SELECT lower(node_id) FROM room_credentials)
                   OR ('!' || lower(node_id)) IN (SELECT lower(node_id) FROM room_credentials)
                   OR lower(node_id) IN (SELECT ('!' || lower(node_id)) FROM room_credentials)
                ORDER BY alias ASC
            """)
            rows = cur.fetchall()
            cur.execute("""
                UPDATE contacts SET is_room_server = 1, is_repeater = 0
                WHERE is_room_server = 0 AND (
                    lower(alias) LIKE '%[room]%'
                    OR lower(alias) LIKE '%[server]%'
                    OR lower(alias) LIKE '%-bbs%'
                    OR lower(alias) LIKE '%-room%'
                    OR lower(alias) LIKE '%room%'
                    OR lower(alias) LIKE '%bbs%'
                    OR lower(alias) LIKE '%server%'
                    OR lower(node_id) IN (SELECT lower(node_id) FROM room_credentials)
                    OR ('!' || lower(node_id)) IN (SELECT lower(node_id) FROM room_credentials)
                    OR lower(node_id) IN (SELECT ('!' || lower(node_id)) FROM room_credentials)
                )
            """)
            conn.commit()
            self._invalidate_contact_cache()
            contacts = []
            for r in rows:
                c = self._row_to_contact(r)
                c.is_room_server = True
                contacts.append(c)
            return contacts

    def set_room_password(self, node_id: str, password: str, auto_login: bool = True):
        """Stores or updates the password for a room server and marks contact as room server."""
        if not node_id:
            return
        clean_id = node_id.strip().lstrip("!@").lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO room_credentials (node_id, password, last_login_ts, auto_login)
                VALUES (?, ?, ?, ?)
            """, (clean_id, password, datetime.now(timezone.utc).isoformat(), int(auto_login)))
            cur.execute("""
                UPDATE contacts SET is_room_server = 1
                WHERE lower(node_id) = ?
                   OR lower(node_id) = ('!' || ?)
                   OR ('!' || lower(node_id)) = ?
                   OR lower(alias) = ?
            """, (clean_id, clean_id, clean_id, clean_id))
            conn.commit()
            self._invalidate_contact_cache()

    def get_room_password(self, node_id: str) -> Optional[str]:
        """Retrieves the saved password for a room server, if any."""
        if not node_id:
            return None
        clean_id = node_id.strip().lstrip("!@").lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT password FROM room_credentials WHERE lower(node_id) = ? LIMIT 1", (clean_id,))
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
            return None

    def get_contact(self, node_id: str) -> Optional[NodeContact]:
        """Lookup a contact by node_id, public key, alias, or prefix with strict precedence and no false substring shadowing."""
        if not node_id:
            return None
        raw_id = (node_id or "").strip()
        clean_id = raw_id.lstrip("!@").strip()
        if not clean_id:
            return None

        cache_key = raw_id.lower()
        if hasattr(self, "_contact_by_key_cache") and cache_key in self._contact_by_key_cache:
            return self._contact_by_key_cache[cache_key]

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Exact match on node_id (case-insensitive, with/without '!' or '@')
            cursor.execute("""
                SELECT * FROM contacts
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                ORDER BY is_repeater ASC, rowid DESC
                LIMIT 1
            """, (raw_id, clean_id, clean_id, raw_id))
            r = cursor.fetchone()
            if r:
                res = self._row_to_contact(r)
                if hasattr(self, "_contact_by_key_cache"):
                    if len(self._contact_by_key_cache) > 2048:
                        self._contact_by_key_cache.clear()
                    self._contact_by_key_cache[cache_key] = res
                return res

            # 2. Exact match on alias (case-insensitive, companion/client prioritized over repeater)
            cursor.execute("""
                SELECT * FROM contacts
                WHERE alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                ORDER BY is_repeater ASC, rowid DESC
                LIMIT 1
            """, (clean_id, raw_id))
            r = cursor.fetchone()
            if r:
                res = self._row_to_contact(r)
                if hasattr(self, "_contact_by_key_cache"):
                    if len(self._contact_by_key_cache) > 2048:
                        self._contact_by_key_cache.clear()
                    self._contact_by_key_cache[cache_key] = res
                return res

            # 3. Exact match on public_key (case-insensitive)
            cursor.execute("""
                SELECT * FROM contacts
                WHERE public_key = ? COLLATE NOCASE
                ORDER BY is_repeater ASC, rowid DESC
                LIMIT 1
            """, (clean_id,))
            r = cursor.fetchone()
            if r:
                res = self._row_to_contact(r)
                if hasattr(self, "_contact_by_key_cache"):
                    if len(self._contact_by_key_cache) > 2048:
                        self._contact_by_key_cache.clear()
                    self._contact_by_key_cache[cache_key] = res
                return res

            # 4. Prefix match on node_id or public_key (min 4 hex chars, wildcard-escaped)
            if len(clean_id) >= 4 and all(c in "0123456789abcdefABCDEF" for c in clean_id):
                safe_prefix = clean_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                cursor.execute("""
                    SELECT * FROM contacts
                    WHERE (node_id LIKE ? ESCAPE '\\' OR public_key LIKE ? ESCAPE '\\')
                    ORDER BY is_repeater ASC, rowid DESC
                    LIMIT 1
                """, (safe_prefix, safe_prefix))
                r = cursor.fetchone()
                if r:
                    res = self._row_to_contact(r)
                    if hasattr(self, "_contact_by_key_cache"):
                        if len(self._contact_by_key_cache) > 2048:
                            self._contact_by_key_cache.clear()
                        self._contact_by_key_cache[cache_key] = res
                    return res

            # 5. Prefix match on alias (min 3 chars, wildcard-escaped, companion first)
            if len(clean_id) >= 3:
                safe_prefix = clean_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                cursor.execute("""
                    SELECT * FROM contacts
                    WHERE alias LIKE ? ESCAPE '\\'
                    ORDER BY is_repeater ASC, rowid DESC
                    LIMIT 1
                """, (safe_prefix,))
                r = cursor.fetchone()
                if r:
                    res = self._row_to_contact(r)
                    if hasattr(self, "_contact_by_key_cache"):
                        if len(self._contact_by_key_cache) > 2048:
                            self._contact_by_key_cache.clear()
                        self._contact_by_key_cache[cache_key] = res
                    return res

            # 6. Fallback substring match on alias (min 3 chars, wildcard-escaped, companion first)
            if len(clean_id) >= 3:
                safe_substr = "%" + clean_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                cursor.execute("""
                    SELECT * FROM contacts
                    WHERE alias LIKE ? ESCAPE '\\'
                    ORDER BY is_repeater ASC, rowid DESC
                    LIMIT 1
                """, (safe_substr,))
                r = cursor.fetchone()
                if r:
                    res = self._row_to_contact(r)
                    if hasattr(self, "_contact_by_key_cache"):
                        if len(self._contact_by_key_cache) > 2048:
                            self._contact_by_key_cache.clear()
                        self._contact_by_key_cache[cache_key] = res
                    return res

            if hasattr(self, "_contact_by_key_cache"):
                if len(self._contact_by_key_cache) > 2048:
                    self._contact_by_key_cache.clear()
                self._contact_by_key_cache[cache_key] = None
            return None

    def get_discovered_nodes(self) -> List[dict]:
        """Scans packet paths, neighbours, and messages for unlinked/overheard nodes.

        Returns a list of discovered candidate dictionaries sorted by most recently seen.
        """
        discovered: dict = {}

        with self._get_connection() as conn:
            cur = conn.cursor()

            # Build known contact lookup sets
            cur.execute("SELECT node_id, alias, public_key FROM contacts")
            known_ids = set()
            known_aliases = set()
            for r in cur.fetchall():
                if r["node_id"]:
                    nid = r["node_id"].lstrip("!@").lower()
                    known_ids.add(nid)
                    if len(nid) >= 8:
                        known_ids.add(nid[:8])
                if r["public_key"]:
                    pk = r["public_key"].lower()
                    known_ids.add(pk)
                    known_ids.add(pk[:8])
                    known_ids.add(pk[:12])
                if r["alias"]:
                    known_aliases.add(r["alias"].strip().lower())

            # 1. Scan hop nodes in packet_paths
            cur.execute("SELECT hop_nodes, timestamp FROM packet_paths ORDER BY rowid DESC LIMIT 300")
            for r in cur.fetchall():
                raw_hops = r["hop_nodes"]
                ts = r["timestamp"] or ""
                if not raw_hops:
                    continue
                try:
                    hops = json.loads(raw_hops) if isinstance(raw_hops, str) else raw_hops
                except Exception:
                    continue

                for h in hops:
                    if not isinstance(h, str):
                        continue
                    clean_h = h.strip()
                    node_id = ""
                    alias = ""
                    is_repeater = True

                    if clean_h.startswith("<Unknown Repeater ") and clean_h.endswith(">"):
                        sub = clean_h[len("<Unknown Repeater "):-1].strip()
                        node_id = sub
                        alias = f"Repeater {sub}"
                    elif clean_h.startswith("!"):
                        sub = clean_h.lstrip("!").strip()
                        if len(sub) >= 2 and all(c in "0123456789abcdefABCDEF" for c in sub):
                            node_id = sub
                            alias = f"Node {sub}"
                            is_repeater = False
                    elif clean_h.startswith("@"):
                        sub = clean_h.lstrip("@").strip()
                        if sub.lower() in known_aliases:
                            continue
                        node_id = sub[:12]
                        alias = sub
                        is_repeater = True

                    if node_id:
                        lower_id = node_id.lower()
                        if lower_id in known_ids or (len(lower_id) >= 8 and lower_id[:8] in known_ids):
                            continue
                        if alias.lower() in known_aliases:
                            continue

                        if lower_id not in discovered:
                            discovered[lower_id] = {
                                "node_id": node_id,
                                "alias": alias,
                                "source": "Packet Path Hop",
                                "count": 1,
                                "last_seen": ts,
                                "is_repeater": is_repeater,
                                "snr_db": 0.0,
                                "rssi_dbm": -100.0,
                                "latitude": None,
                                "longitude": None,
                            }
                        else:
                            discovered[lower_id]["count"] += 1
                            if ts and (not discovered[lower_id]["last_seen"] or ts > discovered[lower_id]["last_seen"]):
                                discovered[lower_id]["last_seen"] = ts

            # 2. Scan neighbours
            cur.execute("SELECT * FROM neighbours ORDER BY rowid DESC")
            for r in cur.fetchall():
                nid = (r["node_id"] or "").lstrip("!@").strip()
                alias = r["alias"] or (f"Node {nid[:8]}" if nid else "Unknown")
                if not nid and not alias:
                    continue
                lower_id = nid.lower()
                if (lower_id and lower_id in known_ids) or alias.lower() in known_aliases:
                    continue

                key = lower_id or alias.lower()
                ts = r["last_heard_ts"] or ""
                if key not in discovered:
                    discovered[key] = {
                        "node_id": nid or key[:12],
                        "alias": alias,
                        "source": "RF Advertisement",
                        "count": 1,
                        "last_seen": ts,
                        "is_repeater": bool(r["is_repeater"]),
                        "snr_db": float(r["snr_db"] or 0.0),
                        "rssi_dbm": float(r["rssi_dbm"] or -100.0),
                        "latitude": r["latitude"],
                        "longitude": r["longitude"],
                    }
                else:
                    discovered[key]["source"] = f"{discovered[key]['source']}, RF Advert"
                    if r["latitude"] and not discovered[key]["latitude"]:
                        discovered[key]["latitude"] = r["latitude"]
                        discovered[key]["longitude"] = r["longitude"]
                    if ts and (not discovered[key]["last_seen"] or ts > discovered[key]["last_seen"]):
                        discovered[key]["last_seen"] = ts

            # 3. Scan messages for unsaved senders
            cur.execute("SELECT sender_id, sender_name, timestamp FROM messages ORDER BY rowid DESC LIMIT 500")
            for r in cur.fetchall():
                sid = (r["sender_id"] or "").lstrip("!@").strip()
                sname = (r["sender_name"] or "").strip()
                if not sid and not sname:
                    continue
                lower_id = sid.lower()
                if (lower_id and lower_id in known_ids) or sname.lower() in known_aliases:
                    continue

                key = lower_id or sname.lower()
                ts = r["timestamp"] or ""
                if key not in discovered:
                    discovered[key] = {
                        "node_id": sid or key[:12],
                        "alias": sname or f"Sender {sid[:8]}",
                        "source": "Message Sender",
                        "count": 1,
                        "last_seen": ts,
                        "is_repeater": False,
                        "snr_db": 0.0,
                        "rssi_dbm": -100.0,
                        "latitude": None,
                        "longitude": None,
                    }
                else:
                    discovered[key]["count"] += 1
                    if ts and (not discovered[key]["last_seen"] or ts > discovered[key]["last_seen"]):
                        discovered[key]["last_seen"] = ts

        # Sort by most recently seen, then by count
        results = list(discovered.values())
        results.sort(key=lambda d: (d["last_seen"] or "", d["count"]), reverse=True)
        return results

    def get_overheard_path_modes(self) -> Dict[str, dict]:
        """Analyzes recent RF packet paths and messages to deduce each node's overheard path mode.

        Returns a dictionary mapping cleaned node_id / alias / sub_hash to:
            {
                "path_mode": 0 (1-byte) or 1 (2-byte) or 2 (3-byte),
                "path_len": int,
                "source": "packet_path" | "message"
            }
        """
        result: Dict[str, dict] = {}

        def _record_mode(key: str, mode: int, p_len: int, src: str):
            if not key:
                return
            k = key.strip().lower()
            if k in result:
                if mode > result[k]["path_mode"]:
                    result[k] = {"path_mode": mode, "path_len": p_len, "source": src}
            else:
                result[k] = {"path_mode": mode, "path_len": p_len, "source": src}

        with self._get_connection() as conn:
            cur = conn.cursor()
            # 1. Inspect recent messages with multi-hop paths
            cur.execute("""
                SELECT sender_id, sender_name, metadata_json, timestamp
                FROM messages
                WHERE metadata_json LIKE '%"path_len":%' AND metadata_json NOT LIKE '%"path_len": 0%'
                ORDER BY rowid DESC LIMIT 400
            """)
            for r in cur.fetchall():
                try:
                    meta = json.loads(r["metadata_json"]) if isinstance(r["metadata_json"], str) else (r["metadata_json"] or {})
                except Exception:
                    continue
                p_len = int(meta.get("path_len", 0) or 0)
                if p_len <= 0 or p_len >= 255:
                    continue
                p_str = str(meta.get("path", "") or "").strip()
                if not p_str:
                    continue
                chunk_sz = max(2, len(p_str) // p_len)
                mode = 0 if chunk_sz <= 2 else (1 if chunk_sz == 4 else 2)

                s_id = str(r["sender_id"] or "").lstrip("!@").lower()
                s_name = str(r["sender_name"] or "").strip().lower()
                _record_mode(s_id, mode, p_len, "message")
                _record_mode(s_name, mode, p_len, "message")

                # Record all repeaters and hop nodes listed in message metadata
                hops = meta.get("hop_nodes") or meta.get("repeaters") or []
                for h in hops:
                    if not isinstance(h, str):
                        continue
                    clean_h = re.sub(r"^<Unknown Repeater ([0-9a-fA-F]+)>$", r"\1", h.strip())
                    clean_h = clean_h.lstrip("!@").strip().lower()
                    _record_mode(clean_h, mode, p_len, "message")

                # Record each raw sub-hash from path string
                for i in range(p_len):
                    sub_h = p_str[i * chunk_sz : (i + 1) * chunk_sz].lower()
                    _record_mode(sub_h, mode, p_len, "message")

            # 2. Inspect recent packet_paths hops
            cur.execute("""
                SELECT hop_nodes, route_type, timestamp
                FROM packet_paths
                ORDER BY rowid DESC LIMIT 300
            """)
            for r in cur.fetchall():
                raw_hops = r["hop_nodes"]
                if not raw_hops:
                    continue
                try:
                    hops = json.loads(raw_hops) if isinstance(raw_hops, str) else raw_hops
                except Exception:
                    continue
                if not isinstance(hops, list):
                    continue

                pkt_mode = 0
                for h in hops:
                    if isinstance(h, str):
                        m = re.search(r"([0-9a-fA-F]{4,6})", h)
                        if m:
                            hex_len = len(m.group(1))
                            if hex_len == 6:
                                pkt_mode = max(pkt_mode, 2)
                            elif hex_len == 4:
                                pkt_mode = max(pkt_mode, 1)

                for h in hops:
                    if not isinstance(h, str):
                        continue
                    clean_h = re.sub(r"^<Unknown Repeater ([0-9a-fA-F]+)>$", r"\1", h.strip())
                    clean_h = clean_h.lstrip("!@").strip().lower()
                    _record_mode(clean_h, pkt_mode, len(hops), "packet_path")

        return result

    # --- Hop Route Preferences (Persistent User Overrides) ---

    def save_hop_preference(self, hop_prefix: str, node_id: str, alias: Optional[str] = None) -> bool:
        """Saves or updates a user-selected repeater preference for a specific hop prefix for all future routing requests."""
        if not hop_prefix or not node_id:
            return False
        clean_prefix = hop_prefix.lstrip("!@").strip().lower()
        now_ts = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO hop_route_preferences (
                    hop_prefix, node_id, alias, created_at, updated_at
                ) VALUES (
                    ?, ?, ?,
                    COALESCE((SELECT created_at FROM hop_route_preferences WHERE lower(hop_prefix) = ?), ?),
                    ?
                )
            """, (clean_prefix, node_id, alias or "", clean_prefix, now_ts, now_ts))
            conn.commit()
            return True

    def get_hop_preference(self, hop_prefix: str) -> Optional[str]:
        """Returns preferred node_id for a given hop prefix if saved, else None."""
        if not hop_prefix:
            return None
        clean_prefix = hop_prefix.lstrip("!@").strip().lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            # 1. Exact match on clean_prefix
            cur.execute("SELECT node_id FROM hop_route_preferences WHERE lower(hop_prefix) = ?", (clean_prefix,))
            r = cur.fetchone()
            if r and r["node_id"]:
                return r["node_id"]
            # 2. Check if clean_prefix starts with or matches any saved prefix
            cur.execute("SELECT hop_prefix, node_id FROM hop_route_preferences")
            all_rows = cur.fetchall()
            for row in all_rows:
                h_pref = row["hop_prefix"].lower()
                n_id = row["node_id"].lstrip("!").lower()
                if (clean_prefix.startswith(h_pref) or h_pref.startswith(clean_prefix)) and n_id.startswith(clean_prefix):
                    return row["node_id"]
            return None

    def get_all_hop_preferences(self) -> List[Dict[str, Any]]:
        """Returns all user-saved hop route overrides."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT hop_prefix, node_id, alias, created_at, updated_at FROM hop_route_preferences ORDER BY updated_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def delete_hop_preference(self, hop_prefix: str) -> bool:
        """Removes a saved hop route preference."""
        if not hop_prefix:
            return False
        clean_prefix = hop_prefix.lstrip("!@").strip().lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM hop_route_preferences WHERE lower(hop_prefix) = ?", (clean_prefix,))
            conn.commit()
            return cur.rowcount > 0

    def clear_all_hop_preferences(self) -> int:
        """Clears all user-saved hop route overrides."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM hop_route_preferences")
            conn.commit()
            return cur.rowcount

    # --- Phantom Nodes (User-Marked Erroneous/Faraway Collision Nodes) ---

    def mark_phantom_node(self, node_id: str, alias: Optional[str] = None) -> bool:
        """Marks a node ID or alias as a phantom node to prevent it from being plotted on the map."""
        if not node_id:
            return False
        clean_id = node_id.lstrip("!@").strip().lower()
        now_ts = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO phantom_nodes (node_id, alias, created_at)
                VALUES (?, ?, ?)
            """, (clean_id, alias or "", now_ts))
            conn.commit()
            self._phantom_nodes_cache = None
            return True

    def unmark_phantom_node(self, node_id_or_alias: str) -> bool:
        """Removes a node ID or alias from the phantom nodes list."""
        if not node_id_or_alias:
            return False
        clean = node_id_or_alias.lstrip("!@").strip().lower()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM phantom_nodes
                WHERE lower(node_id) = ? OR (alias != '' AND lower(alias) = ?)
            """, (clean, clean))
            conn.commit()
            self._phantom_nodes_cache = None
            return cur.rowcount > 0

    def is_phantom_node(self, node_id: str, alias: Optional[str] = None) -> bool:
        """Checks whether a node ID or alias is marked as a phantom node."""
        if not node_id and not alias:
            return False
        clean_id = (node_id or "").lstrip("!@").strip().lower()
        clean_alias = (alias or "").strip().lower()

        if getattr(self, "_phantom_nodes_cache", None) is None:
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT node_id, alias FROM phantom_nodes")
                    self._phantom_nodes_cache = [
                        ((r["node_id"] or "").lstrip("!@").strip().lower(), (r["alias"] or "").strip().lower())
                        for r in cur.fetchall()
                    ]
            except Exception:
                self._phantom_nodes_cache = []

        for p_id, p_al in self._phantom_nodes_cache:
            if clean_id and p_id and (clean_id == p_id or clean_id.startswith(p_id) or p_id.startswith(clean_id)):
                return True
            if clean_alias and p_al and (clean_alias == p_al or clean_alias.startswith(p_al)):
                return True
        return False

    def get_all_phantom_nodes(self) -> List[Dict[str, Any]]:
        """Returns all user-marked phantom nodes."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT node_id, alias, created_at FROM phantom_nodes ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def clear_all_phantom_nodes(self) -> int:
        """Clears all phantom nodes."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM phantom_nodes")
            conn.commit()
            self._phantom_nodes_cache = None
            return cur.rowcount

    @staticmethod
    def calculate_geo_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Computes approximate distance in kilometers between two GPS coordinates."""
        x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
        y = math.radians(lat2 - lat1)
        return math.hypot(x, y) * 6371.0

    def resolve_hop_with_candidates(
        self,
        hop_prefix: str,
        ref_lat: Optional[float] = None,
        ref_lon: Optional[float] = None,
        user_station_prefix: Optional[str] = "M7NCY"
    ) -> Tuple[Optional[NodeContact], List[Dict[str, Any]], bool]:
        """Disambiguates a 1-byte or multi-byte hop prefix, prioritizing user saved preferences, then repeaters closest to home location.
        Returns: (best_candidate, candidate_dicts, is_ambiguous)."""
        if not hop_prefix:
            return None, [], False
        clean_id = hop_prefix.lstrip("!@").strip().lower()
        if not clean_id:
            return None, [], False

        preferred_node_id = self.get_hop_preference(clean_id)

        # Check if clean_id is a short hex hop hash (e.g. 1-byte 'dd' or 2-byte '71cb')
        is_hex_hash = len(clean_id) <= 4 and all(c in "0123456789abcdef" for c in clean_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if is_hex_hash:
                # Protocol-valid hop hash: match node_id prefix, public_key prefix, or exact alias
                cursor.execute("""
                    SELECT * FROM contacts
                    WHERE lower(node_id) LIKE ? OR lower(node_id) LIKE ? OR lower(public_key) LIKE ? OR lower(alias) = ?
                """, (f"{clean_id}%", f"!{clean_id}%", f"{clean_id}%", clean_id))
            else:
                # Long identifier or name search: allow alias substring match
                cursor.execute("""
                    SELECT * FROM contacts
                    WHERE lower(node_id) LIKE ? OR lower(node_id) LIKE ? OR lower(public_key) LIKE ? OR lower(alias) = ? OR lower(alias) LIKE ?
                """, (f"{clean_id}%", f"!{clean_id}%", f"{clean_id}%", clean_id, f"%{clean_id}%"))
            rows = cursor.fetchall()
            if not rows:
                return None, [], False

            candidates = [self._row_to_contact(r) for r in rows]

            def score_and_dist(c: NodeContact) -> Tuple[float, Optional[float], bool]:
                score = 0.0
                c_id_clean = c.node_id.lstrip("!").lower()
                dist_km = None
                is_user_pref = False

                # Multi-byte prefix match bonus (2-byte / 4-char and 3-byte / 6-char prefixes)
                if len(clean_id) >= 6 and (c_id_clean == clean_id or c_id_clean.startswith(clean_id)):
                    score += 10000.0
                elif len(clean_id) >= 4 and c_id_clean.startswith(clean_id):
                    score += 2000.0

                # Intermediate hops are repeaters: strong priority to repeaters
                if c.is_repeater:
                    score += 2000.0

                # Geographic proximity to home station coordinates: closer repeaters win!
                if ref_lat is not None and ref_lon is not None and c.latitude is not None and c.longitude is not None:
                    dist_km = Storage.calculate_geo_distance_km(ref_lat, ref_lon, c.latitude, c.longitude)
                    # Higher score for smaller distance
                    score += max(0.0, 5000.0 - (dist_km * 5.0))
                elif c.latitude is not None and c.longitude is not None:
                    score += 200.0
                else:
                    # Candidates without GPS get baseline score
                    score += 50.0

                # User's home station / repeaters prefix bonus (e.g. M7NCY in alias)
                if user_station_prefix and user_station_prefix.upper() in c.alias.upper():
                    score += 150.0

                # Favorite priority
                if c.is_favorite:
                    score += 30.0

                # Top-priority boost if user explicitly saved this repeater as their preference for this hop
                if preferred_node_id:
                    p_id_clean = preferred_node_id.lstrip("!").lower()
                    if c_id_clean == p_id_clean or c.node_id == preferred_node_id or c_id_clean.startswith(p_id_clean) or p_id_clean.startswith(c_id_clean):
                        score += 50000.0
                        is_user_pref = True

                # Phantom penalty (favors non-phantom candidate if one exists)
                is_phantom = self.is_phantom_node(c.node_id, c.alias)
                if is_phantom and not is_user_pref:
                    score -= 100000.0

                return score, dist_km, is_user_pref, is_phantom

            scored = []
            for c in candidates:
                s, d, is_pref, is_phant = score_and_dist(c)
                scored.append((s, d, c, is_pref, is_phant))

            # Sort descending by score (user-saved preference or closest to home first among repeaters)
            scored.sort(key=lambda item: item[0], reverse=True)

            candidate_dicts = [
                {
                    "contact": item[2],
                    "alias": item[2].alias,
                    "node_id": item[2].node_id,
                    "latitude": item[2].latitude,
                    "longitude": item[2].longitude,
                    "is_repeater": item[2].is_repeater,
                    "dist_km": round(item[1], 1) if item[1] is not None else None,
                    "snr_db": item[2].snr_db,
                    "score": item[0],
                    "is_saved_preference": item[3],
                    "is_phantom": item[4]
                }
                for item in scored
            ]

            best_contact = scored[0][2] if scored else None
            # Duplicate / ambiguous if more than 1 candidate matches this prefix
            is_ambiguous = len(scored) > 1 and any(item[2].is_repeater for item in scored)

            return best_contact, candidate_dicts, is_ambiguous

    def get_best_contact_for_hop(
        self,
        hop_prefix: str,
        ref_lat: Optional[float] = None,
        ref_lon: Optional[float] = None,
        user_station_prefix: Optional[str] = "M7NCY"
    ) -> Optional[NodeContact]:
        """Disambiguates a 1-byte or multi-byte hop prefix by scoring candidates by proximity to home and repeater role."""
        best, _, _ = self.resolve_hop_with_candidates(
            hop_prefix, ref_lat=ref_lat, ref_lon=ref_lon, user_station_prefix=user_station_prefix
        )
        return best

    def resolve_hop_chain_with_candidates(
        self,
        hop_prefixes: List[str],
        sender_coord: Optional[Tuple[float, float]] = None,
        home_coord: Optional[Tuple[float, float]] = (54.65897, -3.4346),
        user_station_prefix: Optional[str] = "M7NCY",
        sender_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Resolves a sequential chain of hop prefixes for a message path.
        For each hop in the transmission sequence, calculates proximity distance relative to:
        1. The last repeater heard in the chain before this hop that has a known GPS location.
        2. If that preceding repeater has no GPS (or if this is the first hop), the nearest prior
           node in the chain that has GPS (e.g. Origin Sender).
        3. If no preceding node has GPS, checks subsequent repeaters in the chain, or defaults
           to the user's Home Station coordinates.
        """
        if not hop_prefixes:
            return []

        resolved_chain: List[Optional[Dict[str, Any]]] = [None] * len(hop_prefixes)

        def find_ref_coord(hop_idx: int, current_chain: list) -> Tuple[float, float, str]:
            # 1. Search backwards through repeaters heard before this hop in the chain
            for k in range(hop_idx - 1, -1, -1):
                c_item = current_chain[k]
                if c_item and c_item.get("contact"):
                    c_obj = c_item["contact"]
                    # Skip phantom nodes as references because their GPS is erroneous
                    if self.is_phantom_node(c_obj.node_id, c_obj.alias):
                        continue
                    if c_obj.latitude is not None and c_obj.longitude is not None:
                        return float(c_obj.latitude), float(c_obj.longitude), c_obj.alias

            # 2. If no preceding repeater has GPS, check Origin Sender
            if sender_coord and sender_coord[0] is not None and sender_coord[1] is not None:
                return float(sender_coord[0]), float(sender_coord[1]), sender_name or "Origin"

            # 3. If no preceding node in chain has GPS, look forward to subsequent repeaters
            for k in range(hop_idx + 1, len(current_chain)):
                c_item = current_chain[k]
                if c_item and c_item.get("contact"):
                    c_obj = c_item["contact"]
                    if self.is_phantom_node(c_obj.node_id, c_obj.alias):
                        continue
                    if c_obj.latitude is not None and c_obj.longitude is not None:
                        return float(c_obj.latitude), float(c_obj.longitude), c_obj.alias

            # 4. Default fallback: Home Station coordinates
            h_lat = home_coord[0] if (home_coord and home_coord[0] is not None) else 54.65897
            h_lon = home_coord[1] if (home_coord and home_coord[1] is not None) else -3.4346
            return h_lat, h_lon, user_station_prefix or "Home Station"

        # Pass 1: Forward resolution
        for i, h_prefix in enumerate(hop_prefixes):
            ref_lat, ref_lon, ref_name = find_ref_coord(i, resolved_chain)
            best_c, cands, is_amb = self.resolve_hop_with_candidates(
                h_prefix, ref_lat=ref_lat, ref_lon=ref_lon, user_station_prefix=user_station_prefix
            )
            resolved_chain[i] = {
                "contact": best_c,
                "candidates": cands,
                "is_ambiguous": is_amb,
                "is_phantom": bool(best_c and self.is_phantom_node(best_c.node_id, best_c.alias)),
                "ref_name": ref_name,
                "ref_coord": (ref_lat, ref_lon),
                "hash": h_prefix
            }

        # Pass 2: Refinement pass if any earlier hops can now benefit from subsequent GPS nodes
        for i, h_prefix in enumerate(hop_prefixes):
            ref_lat, ref_lon, ref_name = find_ref_coord(i, resolved_chain)
            prev_ref = resolved_chain[i]["ref_coord"]
            if (ref_lat, ref_lon) != prev_ref:
                best_c, cands, is_amb = self.resolve_hop_with_candidates(
                    h_prefix, ref_lat=ref_lat, ref_lon=ref_lon, user_station_prefix=user_station_prefix
                )
                resolved_chain[i] = {
                    "contact": best_c,
                    "candidates": cands,
                    "is_ambiguous": is_amb,
                    "is_phantom": bool(best_c and self.is_phantom_node(best_c.node_id, best_c.alias)),
                    "ref_name": ref_name,
                    "ref_coord": (ref_lat, ref_lon),
                    "hash": h_prefix
                }

        return resolved_chain

    def get_contacts_by_recent_activity(self, prefix: str = "") -> List[NodeContact]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cleaned_prefix = prefix.lstrip("@").strip()
            pattern = f"%{cleaned_prefix}%"
            cursor.execute("""
                SELECT alias, node_id, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude
                FROM (
                    SELECT alias, node_id, is_favorite, last_seen, public_key, is_repeater, snr_db, rssi_dbm, latitude, longitude FROM contacts
                    UNION
                    SELECT sender_name as alias, sender_id as node_id, is_favorite, MAX(timestamp) as last_seen, '' as public_key, 0 as is_repeater, 0.0 as snr_db, -100.0 as rssi_dbm, NULL as latitude, NULL as longitude
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
                    rssi_dbm=r["rssi_dbm"] or -100.0,
                    latitude=r["latitude"],
                    longitude=r["longitude"]
                )
                for r in cursor.fetchall()
            ]

    def get_nodes_with_coordinates(self) -> List[NodeContact]:
        """Returns all mesh contacts that have valid GPS coordinates for map plotting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM contacts
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                  AND latitude BETWEEN -85.0 AND 85.0
                  AND longitude BETWEEN -180.0 AND 180.0
                  AND abs(latitude) >= 1.0
                  AND NOT (abs(latitude) < 5.0 AND abs(longitude) < 5.0)
                  AND node_id != '000000000000'
                ORDER BY is_repeater DESC, is_favorite DESC, alias ASC
            """)
            contacts = [self._row_to_contact(r) for r in cursor.fetchall()]
            return [
                c for c in contacts
                if is_valid_alias(c.alias) and is_valid_node_id(c.node_id) and is_valid_coordinate(c.latitude, c.longitude)
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
                    node_id, alias, snr_db, rssi_dbm, last_heard_ts, is_repeater, is_favorite, via_node_id, latitude, longitude, is_room_server
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                n.node_id, n.alias, n.snr_db, n.rssi_dbm, n.last_heard_ts,
                int(n.is_repeater), int(n.is_favorite), n.via_node_id,
                n.latitude, n.longitude, int(getattr(n, "is_room_server", False))
            ))
            conn.commit()

    def get_neighbours(self, limit: int = 20) -> List[NeighbourInfo]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM neighbours ORDER BY snr_db DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            keys = rows[0].keys() if rows and hasattr(rows[0], "keys") else []
            return [
                NeighbourInfo(
                    node_id=r["node_id"],
                    alias=r["alias"] or r["node_id"],
                    snr_db=r["snr_db"] or 0.0,
                    rssi_dbm=r["rssi_dbm"] or -100.0,
                    last_heard_ts=r["last_heard_ts"] or "",
                    is_repeater=bool(r["is_repeater"]),
                    is_room_server=bool(r["is_room_server"]) if ("is_room_server" in keys and r["is_room_server"] is not None) else False,
                    is_favorite=bool(r["is_favorite"]),
                    via_node_id=r["via_node_id"],
                    latitude=r["latitude"],
                    longitude=r["longitude"]
                )
                for r in rows
            ]

    # --- Packet Path Watcher Operations ---

    def save_packet_path(self, path: PacketPathInfo):
        """Saves a multi-hop message packet path for map visualization."""
        p_src = (getattr(path, "source", "radio") or "radio").lower()
        is_radio = (p_src != "mqtt" and not path.packet_id.startswith("mqtt-"))
        with self._get_connection() as conn:
            cursor = conn.cursor()

            raw_hex = (getattr(path, "raw_hex", "") or "").strip().upper()
            txt = ""
            if getattr(path, "decoded_info", None) and isinstance(path.decoded_info, dict):
                txt = str(path.decoded_info.get("text", "") or "").strip()

            if is_radio:
                # If physical radio path arrives, clean up any matching MQTT path recorded in the last 120s
                if raw_hex and len(raw_hex) >= 8:
                    cursor.execute("""
                        DELETE FROM packet_paths
                        WHERE (source = 'mqtt' OR packet_id LIKE 'mqtt-%')
                          AND UPPER(raw_hex) = ?
                          AND timestamp >= datetime('now', '-120 seconds')
                    """, (raw_hex,))
                if txt:
                    cursor.execute("""
                        DELETE FROM packet_paths
                        WHERE (source = 'mqtt' OR packet_id LIKE 'mqtt-%')
                          AND decoded_info LIKE ?
                          AND timestamp >= datetime('now', '-120 seconds')
                    """, (f'%"{txt}"%',))
            else:
                # Incoming is MQTT: drop if radio already heard this packet in the last 120s
                if raw_hex and len(raw_hex) >= 8:
                    cursor.execute("""
                        SELECT packet_id FROM packet_paths
                        WHERE (source != 'mqtt' AND packet_id NOT LIKE 'mqtt-%')
                          AND UPPER(raw_hex) = ?
                          AND timestamp >= datetime('now', '-120 seconds')
                        LIMIT 1
                    """, (raw_hex,))
                    if cursor.fetchone():
                        logger.debug("Dropped MQTT packet path (already recorded from RF radio)")
                        return
                if txt:
                    cursor.execute("""
                        SELECT packet_id FROM packet_paths
                        WHERE (source != 'mqtt' AND packet_id NOT LIKE 'mqtt-%')
                          AND decoded_info LIKE ?
                          AND timestamp >= datetime('now', '-120 seconds')
                        LIMIT 1
                    """, (f'%"{txt}"%',))
                    if cursor.fetchone():
                        logger.debug("Dropped MQTT packet path text (already recorded from RF radio)")
                        return

            cursor.execute("""
                INSERT OR REPLACE INTO packet_paths (
                    packet_id, sender_id, sender_name, recipient_id, timestamp,
                    hop_nodes, hop_snrs, route_type, coordinates,
                    payload_type, raw_hex, decoded_info, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                path.packet_id, path.sender_id, path.sender_name, path.recipient_id,
                path.timestamp, json.dumps(path.hop_nodes), json.dumps(path.hop_snrs),
                path.route_type, json.dumps(path.coordinates),
                getattr(path, "payload_type", "FLOOD") or "FLOOD",
                getattr(path, "raw_hex", "") or "",
                json.dumps(path.decoded_info) if getattr(path, "decoded_info", None) else None,
                getattr(path, "source", "radio") or "radio"
            ))
            conn.commit()

    def get_recent_packet_paths(self, limit: int = 50) -> List[PacketPathInfo]:
        """Retrieves recent packet paths."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM packet_paths
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            results = []
            for r in cursor.fetchall():
                keys = r.keys() if hasattr(r, "keys") else []
                p_type = r["payload_type"] if "payload_type" in keys and r["payload_type"] else "FLOOD"
                r_hex = r["raw_hex"] if "raw_hex" in keys and r["raw_hex"] else ""
                r_source = r["source"] if "source" in keys and r["source"] else "radio"
                dec_info = None
                if "decoded_info" in keys and r["decoded_info"]:
                    try:
                        dec_info = json.loads(r["decoded_info"])
                    except Exception:
                        pass
                results.append(PacketPathInfo(
                    packet_id=r["packet_id"],
                    sender_id=r["sender_id"] or "",
                    sender_name=r["sender_name"] or "",
                    recipient_id=r["recipient_id"] or "",
                    timestamp=r["timestamp"] or "",
                    hop_nodes=json.loads(r["hop_nodes"]) if r["hop_nodes"] else [],
                    hop_snrs=json.loads(r["hop_snrs"]) if r["hop_snrs"] else [],
                    route_type=r["route_type"] or "FLOOD",
                    coordinates=json.loads(r["coordinates"]) if r["coordinates"] else [],
                    payload_type=p_type,
                    raw_hex=r_hex,
                    decoded_info=dec_info,
                    source=r_source
                ))
            return results

    def get_packet_timeline_timestamps(self, hours: int = 24) -> List[float]:
        """Retrieves list of packet/message timestamps in epoch milliseconds over the given hours window."""
        timestamps: List[float] = []
        now_utc = datetime.now(timezone.utc)
        cutoff_dt = now_utc - timedelta(hours=max(1, hours))
        cutoff_iso = cutoff_dt.isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. From packet_paths
            try:
                cursor.execute("""
                    SELECT timestamp FROM packet_paths
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                """, (cutoff_iso,))
                for row in cursor.fetchall():
                    raw_ts = row["timestamp"]
                    if raw_ts:
                        try:
                            dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt >= cutoff_dt:
                                timestamps.append(dt.timestamp() * 1000.0)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Error querying packet_paths timestamps: {e}")

            # 2. From messages
            try:
                cursor.execute("""
                    SELECT timestamp FROM messages
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                """, (cutoff_iso,))
                for row in cursor.fetchall():
                    raw_ts = row["timestamp"]
                    if raw_ts:
                        try:
                            dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt >= cutoff_dt:
                                timestamps.append(dt.timestamp() * 1000.0)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Error querying messages timestamps: {e}")

        timestamps.sort()
        return timestamps


    def get_node_activity_counts(self, timeframe_hours: int = 1) -> Dict[str, int]:
        """Aggregates message and packet transit counts per node/repeater over the specified timeframe."""
        counts: Dict[str, int] = {}
        now_utc = datetime.now(timezone.utc)
        cutoff_dt = now_utc - timedelta(hours=max(1, timeframe_hours))

        def _clean_key(val: str) -> str:
            if not val:
                return ""
            v = str(val).strip()
            v = re.sub(r"^<Unknown Repeater ([0-9a-fA-F]+)>$", r"\1", v)
            return v.lstrip("!@").strip().lower()

        def _inc(key: str):
            clean = _clean_key(key)
            if clean:
                counts[clean] = counts.get(clean, 0) + 1

        with self._get_connection() as conn:
            cur = conn.cursor()
            # 1. Inspect messages within timeframe
            cur.execute("""
                SELECT sender_id, sender_name, metadata_json, timestamp
                FROM messages
                ORDER BY rowid DESC
            """)
            for r in cur.fetchall():
                ts = r["timestamp"]
                if ts:
                    try:
                        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff_dt:
                            continue
                    except Exception:
                        pass

                # Sender activity (record once per message)
                sender_key = r["sender_id"] or r["sender_name"]
                if sender_key:
                    _inc(sender_key)

                # Intermediate repeater hops
                if r["metadata_json"]:
                    try:
                        meta = json.loads(r["metadata_json"]) if isinstance(r["metadata_json"], str) else (r["metadata_json"] or {})
                        hops = meta.get("hops") or meta.get("hop_nodes") or meta.get("repeaters") or []
                        for h in hops:
                            if isinstance(h, str):
                                _inc(h)
                    except Exception:
                        pass

            # 2. Inspect packet_paths within timeframe
            cur.execute("""
                SELECT sender_id, sender_name, hop_nodes, timestamp
                FROM packet_paths
                ORDER BY rowid DESC
            """)
            for r in cur.fetchall():
                ts = r["timestamp"]
                if ts:
                    try:
                        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff_dt:
                            continue
                    except Exception:
                        pass

                # Sender activity (record once per packet path)
                sender_key = r["sender_id"] or r["sender_name"]
                if sender_key:
                    _inc(sender_key)

                raw_hops = r["hop_nodes"]
                if raw_hops:
                    try:
                        hops = json.loads(raw_hops) if isinstance(raw_hops, str) else raw_hops
                        if isinstance(hops, list):
                            for h in hops:
                                if isinstance(h, str):
                                    _inc(h)
                    except Exception:
                        pass

            # 3. Cross-reference known contacts so alias and node_id share the combined score
            cur.execute("SELECT node_id, alias FROM contacts")
            for r in cur.fetchall():
                nid = _clean_key(r["node_id"])
                alias = _clean_key(r["alias"])
                if nid and alias:
                    total = counts.get(nid, 0) + counts.get(alias, 0)
                    if total > 0:
                        counts[nid] = total
                        counts[alias] = total
                        if r["node_id"]:
                            counts[r["node_id"]] = total
                        if r["alias"]:
                            counts[r["alias"]] = total

        return counts

    def get_last_read(self, target_key: str) -> Optional[dict]:
        """Retrieves the last read message info for a given channel or DM."""
        if target_key.startswith("chan:"):
            target_key = f"chan:{target_key[5:].lstrip('#')}"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_read_msg_id, last_read_timestamp FROM channel_read_state WHERE target_key = ?",
                (target_key,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "last_read_msg_id": row["last_read_msg_id"],
                    "last_read_timestamp": row["last_read_timestamp"]
                }
            return None

    def mark_as_read(self, target_key: str, last_read_msg_id: str, last_read_timestamp: str):
        """Marks channel or DM as read up to the given message and timestamp."""
        if target_key.startswith("chan:"):
            target_key = f"chan:{target_key[5:].lstrip('#')}"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO channel_read_state (
                    target_key, last_read_msg_id, last_read_timestamp, updated_at
                ) VALUES (?, ?, ?, ?)
            """, (target_key, last_read_msg_id, last_read_timestamp, now_iso))
            conn.commit()

    def get_last_read_flood_timestamp(self) -> str:
        """Returns the ISO timestamp up to which floods have been read/viewed."""
        res = self.get_last_read("floods")
        if res and res.get("last_read_timestamp"):
            return str(res["last_read_timestamp"])
        return str(self.get_app_state("last_read_flood_ts") or "")

    def set_last_read_flood_timestamp(self, ts: str, packet_id: str = ""):
        """Updates the last read flood timestamp."""
        if not ts:
            return
        self.mark_as_read("floods", packet_id or "flood", ts)
        self.set_app_state("last_read_flood_ts", ts)

    def delete_contact(self, node_id: str):
        """Deletes a contact and its associated records from SQLite."""
        if not node_id:
            return
        raw_id = (node_id or "").strip()
        clean_id = raw_id.lstrip("!@").strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM contacts
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (raw_id, clean_id, clean_id, raw_id, clean_id, raw_id))
            cursor.execute("""
                DELETE FROM neighbours
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (raw_id, clean_id, clean_id, raw_id, clean_id, raw_id))
            cursor.execute("""
                DELETE FROM docked_companions
                WHERE node_id = ? COLLATE NOCASE
                   OR node_id = ? COLLATE NOCASE
                   OR node_id = ('!' || ?) COLLATE NOCASE
                   OR ('!' || node_id) = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
                   OR alias = ? COLLATE NOCASE
            """, (raw_id, clean_id, clean_id, raw_id, clean_id, raw_id))
            cursor.execute("""
                DELETE FROM room_credentials
                WHERE lower(node_id) = ?
                   OR lower(node_id) = ('!' || ?)
                   OR ('!' || lower(node_id)) = ?
            """, (clean_id.lower(), clean_id.lower(), clean_id.lower()))
            conn.commit()
        if hasattr(self, "_contact_by_key_cache"):
            self._contact_by_key_cache.clear()

    def set_contact_favorite(self, node_id: str, is_favorite: bool):
        """Updates favorite status for a contact."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE contacts SET is_favorite = ? WHERE node_id = ? OR alias = ?", (int(is_favorite), node_id, node_id))
            conn.commit()
        if hasattr(self, "_contact_by_key_cache"):
            self._contact_by_key_cache.clear()

    def set_channel_favorite(self, channel_name: str, is_favorite: bool):
        """Updates favorite status for a channel."""
        clean = channel_name.lstrip("#")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE channels SET is_favorite = ? WHERE name = ? OR name = ? OR name = ?",
                (int(is_favorite), channel_name, clean, f"#{clean}")
            )
            if cursor.rowcount == 0:
                cursor.execute("SELECT MAX(channel_id) as max_id FROM channels")
                row = cursor.fetchone()
                next_id = (row["max_id"] + 1) if (row and row["max_id"] is not None) else 1
                cursor.execute("""
                    INSERT OR IGNORE INTO channels (channel_id, name, is_favorite, is_pixoo_enabled, last_activity_ts, unread_count)
                    VALUES (?, ?, ?, 1, '', 0)
                """, (next_id, f"#{clean}", int(is_favorite)))
            conn.commit()

    def mark_channel_as_read(self, channel_name: str):
        """Finds the most recent message in the channel and marks up to it (or current time)."""
        clean = channel_name.lstrip("#")
        target_key = f"chan:{clean}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp FROM messages
                WHERE (channel = ? OR channel = ? OR channel = ?)
                ORDER BY timestamp DESC LIMIT 1
            """, (channel_name, clean, f"#{clean}"))
            row = cursor.fetchone()
            if row:
                msg_id = row["id"]
                ts = row["timestamp"]
            else:
                msg_id = "read_all"
                ts = datetime.now(timezone.utc).isoformat()
        self.mark_as_read(target_key, msg_id, ts)

    def delete_channel(self, channel_name: str):
        """Deletes a channel from SQLite (unless Public)."""
        clean = channel_name.lstrip("#")
        if clean.lower() == "public":
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM channels WHERE name = ? OR name = ? OR name = ?",
                (channel_name, clean, f"#{clean}")
            )
            conn.commit()

    def clear_all_favorites(self):
        """Wipes favorite status across all contacts, channels, and messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE contacts SET is_favorite = 0")
            cursor.execute("UPDATE channels SET is_favorite = 0")
            cursor.execute("UPDATE messages SET is_favorite = 0")
            conn.commit()
        self._invalidate_contact_cache()

    def set_app_state(self, key: str, value: str):
        """Saves an application state key-value pair."""
        if not key:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, str(value), now_iso))
            conn.commit()

    def get_app_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves an application state value by key."""
        if not key:
            return default
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_state WHERE key = ?", (key,))
            r = cursor.fetchone()
            return r["value"] if r else default

    # --- Companion Orbitals (Docked to Repeater) ---

    def save_docked_companion(self, info: DockedCompanionInfo) -> bool:
        """Saves or updates a docked companion node's host repeater."""
        if not info or not info.node_id or not info.repeater_id:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO docked_companions (
                    node_id, alias, repeater_id, repeater_alias, channel, snr, last_heard,
                    is_unknown_first_hop, first_hop_alias
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                info.node_id,
                info.alias,
                info.repeater_id,
                info.repeater_alias,
                info.channel,
                info.snr,
                info.last_heard,
                1 if getattr(info, "is_unknown_first_hop", False) else 0,
                getattr(info, "first_hop_alias", "") or "",
            ))
            conn.commit()
            return True

    def get_docked_companions(self) -> Dict[str, List[Dict[str, Any]]]:
        """Returns docked companion nodes grouped by host repeater key and alias."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT node_id, alias, repeater_id, repeater_alias, channel, snr, last_heard,
                       is_unknown_first_hop, first_hop_alias
                FROM docked_companions
                ORDER BY last_heard DESC
            """)
            rows = cursor.fetchall()
            result: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                col_keys = r.keys()
                item = {
                    "node_id": r["node_id"],
                    "alias": r["alias"] or r["node_id"],
                    "repeater_id": r["repeater_id"],
                    "repeater_alias": r["repeater_alias"] or r["repeater_id"],
                    "channel": r["channel"] or "Public",
                    "snr": r["snr"],
                    "last_heard": r["last_heard"],
                    "is_unknown_first_hop": bool(r["is_unknown_first_hop"]) if "is_unknown_first_hop" in col_keys else False,
                    "first_hop_alias": r["first_hop_alias"] if "first_hop_alias" in col_keys else "",
                }
                rep_id = r["repeater_id"] or ""
                rep_alias = r["repeater_alias"] or ""
                primary_key = rep_id if rep_id else rep_alias
                if primary_key:
                    result.setdefault(primary_key, []).append(item)
                    if rep_alias and rep_alias != primary_key:
                        result.setdefault(rep_alias, result[primary_key])
                        clean_alias = rep_alias.lstrip("@")
                        if clean_alias != rep_alias:
                            result.setdefault(clean_alias, result[primary_key])
            return result

    def get_docked_companion_count(self) -> int:
        """Returns the total number of docked companion nodes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT node_id) as cnt FROM docked_companions")
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def clear_docked_companions(self):
        """Clears all docked companion entries (useful for testing or resync)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM docked_companions")
            conn.commit()

    # --- Satellite Tracking TLEs ---

    def save_satellite_tles(self, tles: List[Dict[str, Any]]) -> int:
        """Saves or updates a batch of satellite TLE records in the database."""
        if not tles:
            return 0
        now_ts = datetime.now(timezone.utc).isoformat()
        count = 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            for sat in tles:
                norad_id = str(sat.get("norad_id") or "").strip()
                name = str(sat.get("name") or "").strip()
                group_name = str(sat.get("group_name") or "amateur").strip()
                line1 = str(sat.get("line1") or "").strip()
                line2 = str(sat.get("line2") or "").strip()
                if not norad_id or not line1 or not line2:
                    continue
                freqs = sat.get("frequencies")
                freq_json = json.dumps(freqs) if freqs is not None else "[]"
                updated_at = str(sat.get("updated_at") or now_ts)
                cur.execute("""
                    INSERT INTO satellite_tles
                    (norad_id, name, group_name, line1, line2, updated_at, frequencies_json, is_favorite)
                    VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT is_favorite FROM satellite_tles WHERE norad_id = ?), 0))
                    ON CONFLICT(norad_id) DO UPDATE SET
                        name = excluded.name,
                        group_name = excluded.group_name,
                        line1 = excluded.line1,
                        line2 = excluded.line2,
                        updated_at = excluded.updated_at,
                        frequencies_json = excluded.frequencies_json
                """, (norad_id, name, group_name, line1, line2, updated_at, freq_json, norad_id))
                count += 1
            conn.commit()
        return count

    def get_satellite_tles(self, group_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves cached satellite TLE records, optionally filtered by category group."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if group_name and group_name != "all":
                cur.execute("""
                    SELECT norad_id, name, group_name, line1, line2, updated_at, frequencies_json, is_favorite
                    FROM satellite_tles
                    WHERE lower(group_name) = ?
                    ORDER BY name ASC
                """, (group_name.lower(),))
            else:
                cur.execute("""
                    SELECT norad_id, name, group_name, line1, line2, updated_at, frequencies_json, is_favorite
                    FROM satellite_tles
                    ORDER BY name ASC
                """)
            rows = cur.fetchall()
            results = []
            for r in rows:
                try:
                    freqs = json.loads(r["frequencies_json"]) if r["frequencies_json"] else []
                except Exception:
                    freqs = []
                results.append({
                    "norad_id": r["norad_id"],
                    "name": r["name"],
                    "group_name": r["group_name"],
                    "line1": r["line1"],
                    "line2": r["line2"],
                    "updated_at": r["updated_at"],
                    "frequencies": freqs,
                    "is_favorite": bool(r["is_favorite"]) if "is_favorite" in r.keys() else False,
                    "is_satellite": True,
                })
            return results

    def get_satellite_by_id(self, norad_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single satellite TLE record by its NORAD catalog ID."""
        if not norad_id:
            return None
        clean_id = str(norad_id).strip()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT norad_id, name, group_name, line1, line2, updated_at, frequencies_json, is_favorite
                FROM satellite_tles
                WHERE norad_id = ?
            """, (clean_id,))
            r = cur.fetchone()
            if not r:
                return None
            try:
                freqs = json.loads(r["frequencies_json"]) if r["frequencies_json"] else []
            except Exception:
                freqs = []
            return {
                "norad_id": r["norad_id"],
                "name": r["name"],
                "group_name": r["group_name"],
                "line1": r["line1"],
                "line2": r["line2"],
                "updated_at": r["updated_at"],
                "frequencies": freqs,
                "is_favorite": bool(r["is_favorite"]) if "is_favorite" in r.keys() else False,
                "is_satellite": True,
            }

    def set_satellite_favorite(self, norad_id: str, is_favorite: bool) -> bool:
        """Sets favorite flag on a satellite record."""
        clean_id = str(norad_id).strip()
        fav_val = 1 if is_favorite else 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE satellite_tles SET is_favorite = ? WHERE norad_id = ?", (fav_val, clean_id))
            conn.commit()
            return cur.rowcount > 0

    def get_favorite_satellites(self) -> List[Dict[str, Any]]:
        """Retrieves all satellites marked as favorite."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT norad_id, name, group_name, line1, line2, updated_at, frequencies_json, is_favorite
                FROM satellite_tles
                WHERE is_favorite = 1
                ORDER BY name ASC
            """)
            rows = cur.fetchall()
            results = []
            for r in rows:
                try:
                    freqs = json.loads(r["frequencies_json"]) if r["frequencies_json"] else []
                except Exception:
                    freqs = []
                results.append({
                    "norad_id": r["norad_id"],
                    "name": r["name"],
                    "group_name": r["group_name"],
                    "line1": r["line1"],
                    "line2": r["line2"],
                    "updated_at": r["updated_at"],
                    "frequencies": freqs,
                    "is_favorite": True,
                    "is_satellite": True,
                })
            return results

    def get_satellite_tle_count(self) -> int:
        """Returns total number of cached satellite TLE records."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM satellite_tles")
            row = cur.fetchone()
            return row[0] if row else 0


