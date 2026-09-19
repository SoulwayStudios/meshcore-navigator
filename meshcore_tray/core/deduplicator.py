"""Cross-Source Packet Deduplicator for MeshCore Navigator.

Prevents duplicate packet ingestion and visual echoes between physical LoRa radio
hardware receptions and network MQTT packet streams, with strict precedence given
to authoritative local radio packets.
"""

from collections import OrderedDict
import hashlib
import logging
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger("meshcore_tray.deduplicator")


class PacketDeduplicator:
    """Thread-safe rolling LRU cache tracking packet signatures across radio and MQTT sources."""

    def __init__(self, default_window_secs: float = 60.0, max_entries: int = 2000):
        self.default_window_secs = default_window_secs
        self.max_entries = max_entries
        self._lock = threading.Lock()
        # Key -> (timestamp, source) where source is 'radio' or 'mqtt'
        self._cache: OrderedDict[str, Tuple[float, str]] = OrderedDict()

    @staticmethod
    def normalize_hex(raw_hex: Optional[str]) -> str:
        """Normalizes packet hex string by removing whitespace and converting to uppercase."""
        if not raw_hex:
            return ""
        return str(raw_hex).strip().replace(" ", "").replace("\n", "").replace("\r", "").upper()

    @staticmethod
    def make_logical_key(sender_id: str = "", channel: str = "", text: str = "") -> str:
        """Generates a normalized logical key for text messages when raw hex is identical or unavailable."""
        clean_sender = str(sender_id or "").strip().lstrip("!@").lower()
        clean_chan = str(channel or "").strip().lstrip("#").lower()
        clean_text = str(text or "").strip()
        if not clean_text:
            return ""
        text_hash = hashlib.sha256(clean_text.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"msg:{clean_sender}:{clean_chan}:{text_hash}"

    def _purge_expired(self, now: float, window_secs: float):
        """Purges expired entries from the rolling cache."""
        cutoff = now - window_secs
        while self._cache:
            _, (entry_time, _) = next(iter(self._cache.items()))
            if entry_time < cutoff:
                self._cache.popitem(last=False)
            else:
                break

    def register_radio_packet(
        self,
        raw_hex: str = "",
        sender_id: str = "",
        channel: str = "",
        text: str = ""
    ):
        """Authoritatively registers an over-the-air packet received from the local physical LoRa radio."""
        now = time.time()
        hex_key = self.normalize_hex(raw_hex)
        log_key = self.make_logical_key(sender_id, channel, text)

        with self._lock:
            self._purge_expired(now, self.default_window_secs)

            if hex_key and len(hex_key) >= 4:
                self._cache[hex_key] = (now, "radio")
                self._cache.move_to_end(hex_key)

            if log_key:
                self._cache[log_key] = (now, "radio")
                self._cache.move_to_end(log_key)

            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def is_duplicate_or_radio(
        self,
        raw_hex: str = "",
        sender_id: str = "",
        channel: str = "",
        text: str = "",
        window_secs: Optional[float] = None
    ) -> bool:
        """Checks if a packet has already been received via physical radio or seen on MQTT within the window.
        
        Returns True if the packet is a duplicate and should be dropped.
        """
        now = time.time()
        win = window_secs if window_secs is not None else self.default_window_secs
        hex_key = self.normalize_hex(raw_hex)
        log_key = self.make_logical_key(sender_id, channel, text)

        with self._lock:
            self._purge_expired(now, win)

            # 1. Check raw_hex match
            if hex_key and len(hex_key) >= 4 and hex_key in self._cache:
                t, src = self._cache[hex_key]
                if (now - t) <= win:
                    logger.debug("Deduplicator: Hex match found in cache from %s (%s...)", src, hex_key[:12])
                    return True

            # 2. Check logical message match
            if log_key and log_key in self._cache:
                t, src = self._cache[log_key]
                if (now - t) <= win:
                    logger.debug("Deduplicator: Logical message match found in cache from %s (%s)", src, log_key)
                    return True

            return False

    def register_mqtt_packet(
        self,
        raw_hex: str = "",
        sender_id: str = "",
        channel: str = "",
        text: str = "",
        window_secs: Optional[float] = None
    ) -> bool:
        """Records an incoming MQTT packet if not duplicate.
        
        Returns True if duplicate (should be dropped), False if newly recorded (accepted).
        """
        now = time.time()
        win = window_secs if window_secs is not None else self.default_window_secs
        hex_key = self.normalize_hex(raw_hex)
        log_key = self.make_logical_key(sender_id, channel, text)

        with self._lock:
            self._purge_expired(now, win)

            is_dup = False
            # Check hex
            if hex_key and len(hex_key) >= 4 and hex_key in self._cache:
                t, src = self._cache[hex_key]
                if (now - t) <= win:
                    is_dup = True
            # Check logical key
            if not is_dup and log_key and log_key in self._cache:
                t, src = self._cache[log_key]
                if (now - t) <= win:
                    is_dup = True

            if is_dup:
                return True

            # Record newly accepted MQTT packet
            if hex_key and len(hex_key) >= 4:
                self._cache[hex_key] = (now, "mqtt")
                self._cache.move_to_end(hex_key)

            if log_key:
                self._cache[log_key] = (now, "mqtt")
                self._cache.move_to_end(log_key)

            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

            return False

    def clear(self):
        """Clears all cached entries."""
        with self._lock:
            self._cache.clear()


# Global shared singleton
_global_deduplicator: Optional[PacketDeduplicator] = None
_global_lock = threading.Lock()


def get_deduplicator(default_window_secs: float = 15.0) -> PacketDeduplicator:
    """Returns the process-wide PacketDeduplicator instance."""
    global _global_deduplicator
    with _global_lock:
        if _global_deduplicator is None:
            _global_deduplicator = PacketDeduplicator(default_window_secs=default_window_secs)
        return _global_deduplicator
