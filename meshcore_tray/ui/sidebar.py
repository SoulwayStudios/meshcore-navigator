"""Channels & Contacts Sidebar with Favorites (⭐)."""

import logging
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFrame, QSplitter
)
from meshcore_tray.core.models import ChannelInfo, NodeContact

logger = logging.getLogger("meshcore_tray.sidebar")


class Sidebar(QWidget):
    """Sidebar containing Channel list, DM contacts, and ⭐ Favorites."""

    channel_selected = pyqtSignal(str)
    contact_selected = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)

    def __init__(self, storage=None, config=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Connection Status Pill
        self.status_pill = QLabel("📡 Radio: Ready")
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_pill)

        # Channels Header
        chan_header = QHBoxLayout()
        chan_lbl = QLabel("<b>CHANNELS</b>")
        chan_lbl.setStyleSheet("color: #8B949E; font-size: 11px; letter-spacing: 1px;")
        chan_header.addWidget(chan_lbl)
        chan_header.addStretch()
        layout.addLayout(chan_header)

        # Channels List
        self.channel_list = QListWidget()
        self.channel_list.itemClicked.connect(self._on_channel_clicked)
        layout.addWidget(self.channel_list, 1)

        # Direct Messages Header
        dm_header = QHBoxLayout()
        dm_lbl = QLabel("<b>DIRECT MESSAGES</b>")
        dm_lbl.setStyleSheet("color: #8B949E; font-size: 11px; letter-spacing: 1px;")
        dm_header.addWidget(dm_lbl)
        dm_header.addStretch()
        layout.addLayout(dm_header)

        # Contacts List
        self.contact_list = QListWidget()
        self.contact_list.itemClicked.connect(self._on_contact_clicked)
        layout.addWidget(self.contact_list, 1)

        self.reload()

    def update_connection_status(self, connected: bool, port: str, mode: str):
        if connected:
            if mode == "mock":
                self.status_pill.setText("🧪 Radio: Simulator Mode")
                self.status_pill.setStyleSheet("background-color: #1F385C; color: #58A6FF; border-radius: 10px; padding: 4px;")
            else:
                self.status_pill.setText(f"🟢 Radio: Connected ({port})")
                self.status_pill.setStyleSheet("background-color: #1B4728; color: #3FB950; border-radius: 10px; padding: 4px;")
        else:
            self.status_pill.setText("🔴 Radio: Disconnected")
            self.status_pill.setStyleSheet("background-color: #5C1D24; color: #FF7B72; border-radius: 10px; padding: 4px;")

    def reload(self):
        """Refreshes channels and contacts from storage."""
        # Channels
        self.channel_list.clear()
        channels = self.storage.get_channels() if self.storage else [
            ChannelInfo(0, "Public", True, True),
            ChannelInfo(1, "#general", False, True),
            ChannelInfo(2, "#ops", True, True)
        ]

        for ch in channels:
            star = "⭐ " if ch.is_favorite else ""
            item = QListWidgetItem(f"{star}#{ch.name}")
            item.setData(Qt.ItemDataRole.UserRole, ch.name)
            self.channel_list.addItem(item)

        # Contacts
        self.contact_list.clear()
        contacts = self.storage.get_contacts() if self.storage else [
            NodeContact("!8f3a", "Alice", True),
            NodeContact("!9c21", "Bob", False)
        ]

        for c in contacts:
            star = "⭐ " if c.is_favorite else ""
            rep = " [Rep]" if c.is_repeater else ""
            item = QListWidgetItem(f"{star}@{c.alias}{rep}")
            item.setData(Qt.ItemDataRole.UserRole, c.node_id)
            self.contact_list.addItem(item)

    def _on_channel_clicked(self, item: QListWidgetItem):
        self.contact_list.clearSelection()
        chan_name = item.data(Qt.ItemDataRole.UserRole)
        self.channel_selected.emit(chan_name)

    def _on_contact_clicked(self, item: QListWidgetItem):
        self.channel_list.clearSelection()
        contact_id = item.data(Qt.ItemDataRole.UserRole)
        self.contact_selected.emit(contact_id)
