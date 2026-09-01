"""Chat Stream & Search Results View Widget."""

import html
import logging
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QPushButton, QSizePolicy
)
from meshcore_tray.core.models import MessageEnvelope

logger = logging.getLogger("meshcore_tray.chat_widget")


class MessageBubble(QFrame):
    """Modern chat bubble card for incoming or outgoing mesh message."""

    def __init__(self, msg: MessageEnvelope, parent=None):
        super().__init__(parent)
        self.msg = msg
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Header Row: Sender, Star, Channel Tag, Time
        header = QHBoxLayout()
        header.setSpacing(6)

        star_str = "⭐ " if self.msg.is_favorite else ""
        sender_lbl = QLabel(f"<b>{star_str}{html.escape(self.msg.sender_name)}</b>")
        sender_lbl.setStyleSheet("color: #FFD700;" if self.msg.is_favorite else "color: #58A6FF;")

        chan_tag = QLabel(f"#{html.escape(self.msg.channel)}" if not self.msg.is_direct_message else "DM")
        chan_tag.setStyleSheet("color: #8B949E; font-size: 11px;")

        time_str = self.msg.timestamp[11:16] if len(self.msg.timestamp) >= 16 else ""
        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet("color: #6E7681; font-size: 11px;")

        header.addWidget(sender_lbl)
        header.addWidget(chan_tag)
        header.addStretch()

        # Telemetry badge if available (SNR/RSSI)
        if "snr" in self.msg.metadata:
            snr_val = self.msg.metadata["snr"]
            telem_lbl = QLabel(f"SNR: {snr_val:+.1f}dB")
            telem_lbl.setStyleSheet("color: #3FB950; font-size: 10px; font-weight: bold;")
            header.addWidget(telem_lbl)

        header.addWidget(time_lbl)
        layout.addLayout(header)

        # Watched keyword / Mention warning badge
        if self.msg.is_mention or self.msg.matched_keywords:
            kw_str = ", ".join(self.msg.matched_keywords) if self.msg.matched_keywords else "Mention"
            alert_badge = QLabel(f"🚨 ALERT: {html.escape(kw_str)}")
            alert_badge.setStyleSheet(
                "background-color: #5C1D24; color: #FF7B72; border: 1px solid #F85149; "
                "border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: bold;"
            )
            layout.addWidget(alert_badge)

        # Message Body
        body_lbl = QLabel(html.escape(self.msg.text))
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_lbl.setStyleSheet("color: #F0F6FC; font-size: 13px; line-height: 1.4;")
        layout.addWidget(body_lbl)

        # Styling depending on Outgoing vs Incoming
        if self.msg.is_outgoing:
            self.setStyleSheet(
                "MessageBubble { background-color: #1F385C; border: 1px solid #388BFD; border-radius: 8px; margin-left: 40px; }"
            )
        else:
            self.setStyleSheet(
                "MessageBubble { background-color: #161B22; border: 1px solid #21262D; border-radius: 8px; margin-right: 40px; }"
            )


class ChatWidget(QWidget):
    """Scrollable message stream with real-time update and search view."""

    jump_to_channel = pyqtSignal(str)

    def __init__(self, storage=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.current_channel = "Public"
        self.current_dm: Optional[str] = None
        self.is_searching = False
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header Title Bar
        self.header_bar = QFrame()
        self.header_bar.setStyleSheet("background-color: #161B22; border-bottom: 1px solid #21262D;")
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(16, 10, 16, 10)

        self.title_label = QLabel("# Public")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #58A6FF;")
        self.sub_label = QLabel("Public Channel Broadcast")
        self.sub_label.setStyleSheet("color: #8B949E; font-size: 12px; margin-left: 10px;")

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.sub_label)
        header_layout.addStretch()

        main_layout.addWidget(self.header_bar)

        # Scroll Area for Messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(12, 12, 12, 12)
        self.container_layout.setSpacing(10)
        self.container_layout.addStretch()

        self.scroll_area.setWidget(self.container)
        main_layout.addWidget(self.scroll_area)

    def set_target(self, channel: str, dm_recipient: Optional[str] = None):
        self.current_channel = channel
        self.current_dm = dm_recipient
        self.is_searching = False

        if dm_recipient:
            self.title_label.setText(f"@{dm_recipient}")
            self.sub_label.setText("Direct Message")
        else:
            self.title_label.setText(f"#{channel}")
            self.sub_label.setText("Mesh Channel Stream")

        self.reload_messages()

    def reload_messages(self):
        """Fetches messages from SQLite and populates the bubble list."""
        self._clear_container()

        messages = []
        if self.storage:
            if self.current_dm:
                messages = self.storage.get_messages(contact_id=self.current_dm)
            else:
                messages = self.storage.get_messages(channel=self.current_channel)

        if not messages:
            empty_lbl = QLabel(f"No messages in #{self.current_channel} yet. Say hello!")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #6E7681; font-size: 13px; margin-top: 40px;")
            self.container_layout.addWidget(empty_lbl)
        else:
            for msg in messages:
                bubble = MessageBubble(msg)
                self.container_layout.addWidget(bubble)

        self.container_layout.addStretch()
        self._scroll_to_bottom()

    def add_message(self, msg: MessageEnvelope):
        """Dynamically appends a new message if it matches active view."""
        if self.is_searching:
            return

        is_match = False
        if self.current_dm and msg.is_direct_message:
            if msg.sender_id == self.current_dm or msg.recipient_id == self.current_dm:
                is_match = True
        elif not self.current_dm and not msg.is_direct_message:
            if msg.channel == self.current_channel:
                is_match = True

        if is_match:
            # Remove stretch at bottom, add bubble, re-add stretch
            count = self.container_layout.count()
            if count > 0:
                item = self.container_layout.takeAt(count - 1)
                del item

            bubble = MessageBubble(msg)
            self.container_layout.addWidget(bubble)
            self.container_layout.addStretch()
            self._scroll_to_bottom()

    def show_search_results(self, query: str):
        """Renders search results across all channels."""
        self.is_searching = True
        self.title_label.setText(f"🔍 Search: \"{query}\"")
        self.sub_label.setText("All Channels & DMs")
        self._clear_container()

        if not self.storage or not query.strip():
            return

        results = self.storage.search_messages(query)
        if not results:
            no_res = QLabel(f"No messages found matching \"{query}\"")
            no_res.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_res.setStyleSheet("color: #8B949E; margin-top: 40px;")
            self.container_layout.addWidget(no_res)
        else:
            for msg in results:
                bubble = MessageBubble(msg)
                self.container_layout.addWidget(bubble)

        self.container_layout.addStretch()

    def _clear_container(self):
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())
