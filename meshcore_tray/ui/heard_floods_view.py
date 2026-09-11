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

    def __init__(self, path: PacketPathInfo, is_unread: bool = False, parent=None):
        super().__init__(parent)
        self.path = path
        self.is_unread = is_unread
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("floodRow")
        self._setup_ui()
        self._setup_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Header Row: Timestamp | Route Type Badge | Unread Badge | Sender | Metrics
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

        # Badge: Unread / New
        if self.is_unread:
            self.lbl_unread = QLabel("● NEW")
            self.lbl_unread.setStyleSheet("""
                background-color: rgba(192, 132, 252, 0.25);
                color: #E879F9;
                border: 1px solid #C084FC;
                border-radius: 3px;
                padding: 1px 5px;
                font-family: monospace;
                font-size: 9px;
                font-weight: bold;
            """)
            hdr_layout.addWidget(self.lbl_unread)

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

    def _setup_style(self):
        if self.is_unread:
            self.setStyleSheet("""
                QFrame#floodRow {
                    background-color: #1E1B2E;
                    border: 1px solid #4C1D95;
                    border-left: 3px solid #C084FC;
                    border-radius: 4px;
                    margin-bottom: 2px;
                }
                QFrame#floodRow:hover {
                    background-color: #27213C;
                    border: 1px solid #C084FC;
                    border-left: 3px solid #E879F9;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#floodRow {
                    background-color: #1A1B1E;
                    border: 1px solid #2E3035;
                    border-radius: 4px;
                    margin-bottom: 2px;
                }
                QFrame#floodRow:hover {
                    background-color: #242233;
                    border: 1px solid #C084FC;
                }
            """)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered.emit(self.path)

    def leaveEvent(self, event):
        super().leaveEvent(event)
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
        last_read_ts = self.storage.get_last_read_flood_timestamp()
        paths = self.storage.get_recent_packet_paths(limit=75)

        first_unread_row = None
        newest_ts = ""

        if paths and paths[0].timestamp:
            newest_ts = paths[0].timestamp

        for p in paths:
            is_unread = False
            if last_read_ts and p.timestamp:
                is_unread = (p.timestamp > last_read_ts)
            elif not last_read_ts:
                is_unread = True

            row = self._add_path_row(p, prepend=False, is_unread=is_unread)
            if is_unread:
                first_unread_row = row

        if first_unread_row:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(80, lambda: self._scroll_to_row(first_unread_row))
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(80, lambda: self.scroll_area.verticalScrollBar().setValue(0))

        if newest_ts:
            self.storage.set_last_read_flood_timestamp(newest_ts)

    def _scroll_to_row(self, row: FloodRowWidget):
        if not row:
            return
        try:
            y = row.pos().y()
            self.scroll_area.verticalScrollBar().setValue(max(0, y - 8))
        except Exception as e:
            logger.debug("Could not scroll to row: %s", e)

    def _add_path_row(self, path: PacketPathInfo, prepend: bool = False, is_unread: bool = False) -> FloodRowWidget:
        row = FloodRowWidget(path, is_unread=is_unread)
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
        return row

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

