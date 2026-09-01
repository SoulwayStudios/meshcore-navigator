"""Mention and Watched Words Detector for MeshCore Pixoo Tray."""

import logging
import re
from typing import List, Tuple
from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.core.event_bus import bus, EventType

logger = logging.getLogger("meshcore_tray.mention_detector")


class MentionDetector:
    """Evaluates messages against watched keywords and node callsign/ID."""

    def __init__(self, config=None):
        self.config = config

    def set_config(self, config):
        self.config = config

    def evaluate_message(self, message: MessageEnvelope) -> Tuple[bool, List[str]]:
        """Checks if a message contains watched keywords or node mentions."""
        if not self.config or not message.text:
            return False, []

        text_lower = message.text.lower()
        matched_words = []
        is_mention = False

        # 1. Check Watched Keywords
        watched = self.config.notifications.watched_keywords
        for kw in watched:
            kw_clean = kw.strip().lower()
            if not kw_clean:
                continue
            # Match word boundary or exact token
            pattern = rf"\b{re.escape(kw_clean)}\b"
            if re.search(pattern, text_lower):
                matched_words.append(kw.strip())

        # 2. Check Node Mentions (Callsign, Alias, Node ID)
        if self.config.notifications.notify_on_node_mentions:
            node_identifiers = [
                self.config.meshcore.node_alias,
                self.config.meshcore.node_id,
            ]
            for ident in node_identifiers:
                if ident and ident.strip():
                    ident_clean = ident.strip().lower()
                    pattern = rf"\b{re.escape(ident_clean)}\b"
                    if re.search(pattern, text_lower) or f"@{ident_clean}" in text_lower:
                        is_mention = True
                        matched_words.append(f"@{ident.strip()}")

        message.is_mention = is_mention or bool(matched_words)
        message.matched_keywords = matched_words

        if message.is_mention:
            logger.info(f"Triggered keyword/mention alert for message {message.id}: {matched_words}")
            bus.emit(EventType.NOTIFY_USER, {
                "title": f"🚨 Alert in #{message.channel}" if not message.is_direct_message else f"🚨 DM from {message.sender_name}",
                "body": f"From {message.sender_name}: {message.text[:80]}",
                "message": message,
                "keywords": matched_words,
                "is_priority": True,
            })

        return message.is_mention, matched_words
