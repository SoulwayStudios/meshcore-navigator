"""Heard Floods & RF Packet Path Inspector View for MeshCore Tray.

Replaces the chat window on the main page with a real-time list of heard RF floods
and routed packets, styled with the same dark monospace aesthetic as the watcher status bar.
Hovering over any flood in the list dynamically visualises its multi-hop trajectory on the map.
"""

from datetime import datetime
import json
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QScrollArea, QSizePolicy
)

from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import PacketPathInfo
from meshcore_tray.storage import Storage

logger = logging.getLogger("meshcore_tray.heard_floods_view")


class FloodRowWidget(QFrame):
    """Compact monospace card representing a single heard flood or packet path."""

    hovered = pyqtSignal(object)
    unhovered = pyqtSignal()
    selected = pyqtSignal(object)

    def __init__(self, path: PacketPathInfo, parent=None):
        super().__init__(parent)
        self.path = path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("floodRow")
        self._setup_ui()
        self._set_idle_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Header Row: Timestamp | Route Type Badge | Sender | Metrics
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        hdr_layout.setSpacing(8)

        # Formatted Time
        t_display = "--:--:--"
        if self.path.timestamp:
            try:
                dt = datetime.fromisoformat(str(self.path.timestamp).replace("Z", "+00:00"))
                t_display = dt.strftime("%H:%M:%S")
            except Exception:
                t_display = str(self.path.timestamp)[:8]

        self.lbl_time = QLabel(f"[{t_display}]")
        self.lbl_time.setStyleSheet("color: #9CA3AF; font-family: monospace; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_time)

        # Badge: Route Type
        rtype = (self.path.route_type or "FLOOD").upper()
        badge_col = "#C084FC" if "FLOOD" in rtype else "#38BDF8"
        self.lbl_badge = QLabel(f"⚡ {rtype}")
        self.lbl_badge.setStyleSheet(f"""
            background-color: rgba(192, 132, 252, 0.15);
            color: {badge_col};
            border: 1px solid {badge_col};
            border-radius: 3px;
            padding: 1px 6px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.lbl_badge)

        # Sender Info
        sender_disp = self.path.sender_name or self.path.sender_id or "Unknown"
        self.lbl_sender = QLabel(f"Orig: {sender_disp}")
        self.lbl_sender.setStyleSheet("color: #10B981; font-family: monospace; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_sender, 1)

        # SNR / Metric if available
        if self.path.hop_snrs:
            snr_val = self.path.hop_snrs[0]
            self.lbl_snr = QLabel(f"{snr_val:+.1f} dB")
            self.lbl_snr.setStyleSheet("color: #FBBF24; font-family: monospace; font-size: 11px; font-weight: bold;")
            hdr_layout.addWidget(self.lbl_snr)

        layout.addLayout(hdr_layout)

        # Path Hops Chain: Origin ➔ Repeater1 ➔ Repeater2
        hops = list(self.path.hop_nodes) if self.path.hop_nodes else []
        if hops:
            hops_str = " ➔ ".join(hops)
            hop_count = len(hops)
        elif self.path.coordinates:
            hops_str = f"{len(self.path.coordinates)} coordinate hops"
            hop_count = len(self.path.coordinates)
        else:
            hops_str = "Direct (0 hops)"
            hop_count = 0

        self.lbl_hops = QLabel(f"📍 {hops_str}")
        self.lbl_hops.setStyleSheet("color: #E2E8F0; font-family: monospace; font-size: 11px; font-weight: 500;")
        self.lbl_hops.setWordWrap(True)
        layout.addWidget(self.lbl_hops)

    def _set_idle_style(self):
        self.setStyleSheet("""
            QFrame#floodRow {
                background-color: #1A1B1E;
                border: 1px solid #2E3035;
                border-radius: 4px;
                margin-bottom: 2px;
            }
        """)

    def _set_hover_style(self):
        self.setStyleSheet("""
            QFrame#floodRow {
                background-color: #242233;
                border: 1.5px solid #C084FC;
                border-radius: 4px;
                margin-bottom: 2px;
            }
        """)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._set_hover_style()
        self.hovered.emit(self.path)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_idle_style()
        self.unhovered.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.path)


class HeardFloodsWidget(QWidget):
    """Monospace watcher-styled pane displaying live and historical heard floods."""

    flood_hovered = pyqtSignal(object)    # Emits PacketPathInfo to preview on map
    flood_unhovered = pyqtSignal()        # Restores normal map visualization
    flood_selected = pyqtSignal(object)   # Emits PacketPathInfo to pin route
    back_to_chat_requested = pyqtSignal() # Request to restore regular chat window

    def __init__(self, storage: Optional[Storage] = None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.paused = False
        self.setObjectName("heardFloodsWidget")
        self._rows: List[FloodRowWidget] = []
        self._setup_ui()
        self._load_recent_floods()
        self._subscribe_events()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar matching purple status bar underneath map
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #1A1B1E;
                border-bottom: 1px solid #2E3035;
                padding: 6px 12px;
            }
        """)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.title_lbl = QLabel("⚡ HEARD FLOODS")
        self.title_lbl.setStyleSheet("""
            color: #C084FC;
            font-family: monospace;
            font-size: 11px;
            font-weight: bold;
        """)
        header_layout.addWidget(self.title_lbl)

        self.count_badge = QLabel("0 Floods")
        self.count_badge.setStyleSheet("""
            background-color: rgba(192, 132, 252, 0.15);
            color: #C084FC;
            border-radius: 10px;
            padding: 2px 8px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        header_layout.addWidget(self.count_badge)

        header_layout.addStretch()

        # Pause / Resume Button
        self.btn_pause = QPushButton("⏸️")
        self.btn_pause.setToolTip("Pause / Resume live stream")
        self.btn_pause.setFixedSize(28, 24)
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.setStyleSheet(self._btn_style())
        self.btn_pause.clicked.connect(self._toggle_pause)
        header_layout.addWidget(self.btn_pause)

        # Clear Button
        self.btn_clear = QPushButton("🧹")
        self.btn_clear.setToolTip("Clear list")
        self.btn_clear.setFixedSize(28, 24)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setStyleSheet(self._btn_style())
        self.btn_clear.clicked.connect(self.clear_list)
        header_layout.addWidget(self.btn_clear)

        # Return to Chat Button
        self.btn_chat = QPushButton("💬 Chat")
        self.btn_chat.setToolTip("Return to normal Chat view")
        self.btn_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_chat.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #FFFFFF;
                border: 1px solid #3E4048;
                border-radius: 4px;
                padding: 3px 10px;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #38BDF8;
                color: #000000;
                border-color: #7DD3FC;
            }
        """)
        self.btn_chat.clicked.connect(self.back_to_chat_requested.emit)
        header_layout.addWidget(self.btn_chat)

        layout.addWidget(header_frame)

        # Scroll area for flood rows
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #121316;
                border: none;
            }
            QScrollBar:vertical {
                background-color: #121316;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #2E3035;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5865F2;
            }
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background-color: #121316;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()

        self.scroll_area.setWidget(self.list_container)
        layout.addWidget(self.scroll_area, 1)

    def _btn_style(self) -> str:
        return """
            QPushButton {
                background-color: #24262B;
                color: #C084FC;
                border: 1px solid #3E4048;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #35363C;
                color: #FFFFFF;
            }
        """

    def _subscribe_events(self):
        bus.subscribe(EventType.PACKET_PATH_TRACED, self._on_live_path_traced)

    def _on_live_path_traced(self, path: PacketPathInfo):
        if self.paused or not path:
            return
        self._add_path_row(path, prepend=True)

    def _load_recent_floods(self):
        if not self.storage:
            return
        paths = self.storage.get_recent_packet_paths(limit=75)
        for p in paths:
            self._add_path_row(p, prepend=False)

    def _add_path_row(self, path: PacketPathInfo, prepend: bool = False):
        row = FloodRowWidget(path)
        row.hovered.connect(self.flood_hovered.emit)
        row.unhovered.connect(self.flood_unhovered.emit)
        row.selected.connect(self.flood_selected.emit)

        if prepend:
            self.list_layout.insertWidget(0, row)
            self._rows.insert(0, row)
        else:
            # Insert before final stretch
            idx = max(0, self.list_layout.count() - 1)
            self.list_layout.insertWidget(idx, row)
            self._rows.append(row)

        # Cap at 150 rows in memory
        if len(self._rows) > 150:
            oldest = self._rows.pop()
            self.list_layout.removeWidget(oldest)
            oldest.deleteLater()

        self.count_badge.setText(f"{len(self._rows)} Floods")

    def _toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.setText("▶️")
            self.title_lbl.setText("⏸️ PAUSED")
        else:
            self.btn_pause.setText("⏸️")
            self.title_lbl.setText("⚡ HEARD FLOODS")

    def clear_list(self):
        for r in self._rows:
            self.list_layout.removeWidget(r)
            r.deleteLater()
        self._rows.clear()
        self.count_badge.setText("0 Floods")

    def reload(self):
        """Reloads recent packet paths from storage."""
        self.clear_list()
        self._load_recent_floods()

