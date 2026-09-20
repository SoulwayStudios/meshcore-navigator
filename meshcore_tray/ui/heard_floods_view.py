"""Heard Floods & CoreScope-styled RF Packet Feed View for MeshCore Navigator.

Provides a real-time live packet feed, filter pill bar, full byte-level packet inspector,
formatted hex dump, and dynamic Leaflet map trace animations matching CoreScope's architecture.
Replaces the old Heard Floods pane while maintaining full backward compatibility.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPointF, QEvent
from PyQt6.QtGui import QEnterEvent, QMouseEvent, QClipboard, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QDialog, QTextEdit, QTabWidget, QApplication
)

from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import PacketPathInfo
from meshcore_tray.core.packet_decoder import (
    PacketDecoder, DecodedPacket, generate_field_breakdown,
    decode_meshcore_packet, TYPE_COLORS
)
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
        self.is_active_selection = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("floodRow")
        self._setup_ui()
        self._setup_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(4)

        # Header Row: Timestamp | Route Badge | Type Badge | Unread Badge | Sender | SNR
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        hdr_layout.setSpacing(6)

        # Formatted Time
        t_display = "--:--:--"
        if self.path.timestamp:
            try:
                dt = datetime.fromisoformat(str(self.path.timestamp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                t_display = dt.astimezone().strftime("%H:%M:%S")
            except Exception:
                t_display = str(self.path.timestamp)[:8]

        self.lbl_time = QLabel(f"[{t_display}]")
        self.lbl_time.setStyleSheet("color: #9CA3AF; font-family: monospace; font-size: 11px; font-weight: bold;")
        hdr_layout.addWidget(self.lbl_time)

        # Route Badge (FLOOD vs DIRECT)
        rtype = (self.path.route_type or "FLOOD").upper()
        badge_col = "#3B82F6" if "FLOOD" in rtype else "#F59E0B"
        bg_col = "rgba(59, 130, 246, 0.15)" if "FLOOD" in rtype else "rgba(245, 158, 11, 0.15)"
        self.lbl_badge = QLabel(f"⚡ {rtype}")
        self.lbl_badge.setStyleSheet(f"""
            background-color: {bg_col};
            color: {badge_col};
            border: 1px solid {badge_col};
            border-radius: 3px;
            padding: 1px 6px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.lbl_badge)

        # Source Badge (📻 RF vs 🌐 MQTT)
        src = (getattr(self.path, "source", "radio") or "radio").lower()
        if src == "mqtt" or self.path.packet_id.startswith("mqtt-"):
            src_lbl = "🌐 MQTT"
            src_col = "#F59E0B"
            src_bg = "rgba(245, 158, 11, 0.18)"
            src_tip = "Ingested from MQTT Broker Feed"
        else:
            src_lbl = "📻 RF"
            src_col = "#10B981"
            src_bg = "rgba(16, 185, 129, 0.18)"
            src_tip = "Direct local LoRa physical radio reception"

        self.lbl_source = QLabel(src_lbl)
        self.lbl_source.setToolTip(src_tip)
        self.lbl_source.setStyleSheet(f"""
            background-color: {src_bg};
            color: {src_col};
            border: 1px solid {src_col};
            border-radius: 3px;
            padding: 1px 5px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.lbl_source)

        # Payload Type Badge (CoreScope colors)
        ptype = getattr(self.path, "payload_type", "FLOOD") or "FLOOD"
        type_col = TYPE_COLORS.get(ptype, "#94A3B8")
        type_icon = "📢" if ptype == "ADVERT" else ("💬" if "TXT" in ptype else ("📡" if ptype == "TRACE" else ("📥" if "REQ" in ptype else ("🛣️" if ptype == "PATH" else ("✅" if ptype == "ACK" else "📦")))))
        self.lbl_type_badge = QLabel(f"{type_icon} {ptype}")
        self.lbl_type_badge.setStyleSheet(f"""
            background-color: rgba(255, 255, 255, 0.08);
            color: {type_col};
            border: 1px solid {type_col};
            border-radius: 3px;
            padding: 1px 5px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.lbl_type_badge)

        # Unread / New Badge
        if self.is_unread:
            self.lbl_unread = QLabel("● NEW")
            self.lbl_unread.setStyleSheet("""
                background-color: rgba(59, 130, 246, 0.2);
                color: #93C5FD;
                border: 1px solid #3B82F6;
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

        # SNR Metric if available
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
        elif self.path.coordinates:
            hops_str = f"{len(self.path.coordinates)} coordinate hops"
        else:
            hops_str = "Direct (0 hops)"

        self.lbl_hops = QLabel(f"📍 {hops_str}")
        self.lbl_hops.setStyleSheet("color: #CBD5E1; font-family: monospace; font-size: 11px; font-weight: 500;")
        self.lbl_hops.setWordWrap(True)
        layout.addWidget(self.lbl_hops)

        # Optional Payload Preview if decoded info exists
        preview_text = self._extract_payload_preview()
        if preview_text:
            self.lbl_preview = QLabel(preview_text)
            self.lbl_preview.setStyleSheet("color: #94A3B8; font-family: monospace; font-size: 10px; font-style: italic;")
            self.lbl_preview.setWordWrap(True)
            layout.addWidget(self.lbl_preview)

    def _extract_payload_preview(self) -> Optional[str]:
        if getattr(self.path, "decoded_info", None):
            dec = self.path.decoded_info
            if isinstance(dec, dict):
                p = dec.get("payload", {})
                if p.get("text"):
                    return f"💬 \"{p['text']}\""
                if p.get("name"):
                    return f"🏷️ Advert Node: {p['name']}"
                if p.get("lat") is not None and p.get("lon") is not None:
                    return f"🌍 GPS: {p['lat']:.4f}, {p['lon']:.4f}"
        return None

    def _setup_style(self):
        if self.is_active_selection:
            self.setStyleSheet("""
                QFrame#floodRow {
                    background-color: #1E293B;
                    border: 1px solid #3B82F6;
                    border-left: 3px solid #60A5FA;
                    border-radius: 4px;
                    margin-bottom: 2px;
                }
            """)
        elif self.is_unread:
            self.setStyleSheet("""
                QFrame#floodRow {
                    background-color: #1E293B;
                    border: 1px solid #2563EB;
                    border-left: 3px solid #3B82F6;
                    border-radius: 4px;
                    margin-bottom: 2px;
                }
                QFrame#floodRow:hover {
                    background-color: #162032;
                    border: 1px solid #3B82F6;
                    border-left: 3px solid #60A5FA;
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
                    background-color: #1E293B;
                    border: 1px solid #3B82F6;
                }
            """)

    def set_active(self, active: bool):
        self.is_active_selection = active
        self._setup_style()

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


class DecodePacketDialog(QDialog):
    """CoreScope BYOP (Bring Your Own Packet) Raw Hex Decoder Modal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📦 CoreScope Packet Byte Decoder")
        self.resize(720, 560)
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1F22;
                color: #FFFFFF;
            }
            QLabel {
                color: #E2E8F0;
                font-family: monospace;
            }
            QTextEdit, QLineEdit {
                background-color: #121316;
                color: #F8FAFC;
                border: 1px solid #3E4048;
                border-radius: 4px;
                padding: 6px;
                font-family: monospace;
                font-size: 11px;
            }
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-family: monospace;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QTableWidget {
                background-color: #121316;
                color: #F8FAFC;
                gridline-color: #2E3035;
                border: 1px solid #3E4048;
                font-family: monospace;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #2B2D31;
                color: #94A3B8;
                border: 1px solid #2E3035;
                padding: 4px;
                font-weight: bold;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        title = QLabel("📦 CoreScope Wire-Level Packet Decoder")
        title.setStyleSheet("color: #60A5FA; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        hint = QLabel("Paste raw hex bytes from radio logs, MQTT, or traceroute:")
        hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        layout.addWidget(hint)

        self.input_hex = QTextEdit()
        self.input_hex.setPlaceholderText("e.g. 15024A8F00... or raw hex packet")
        self.input_hex.setMaximumHeight(70)
        layout.addWidget(self.input_hex)

        btn_row = QHBoxLayout()
        self.btn_decode = QPushButton("🔍 Decode Packet")
        self.btn_decode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_decode.clicked.connect(self._do_decode)
        btn_row.addWidget(self.btn_decode)

        self.status_lbl = QLabel("")
        btn_row.addWidget(self.status_lbl, 1)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet("background-color: #2B2D31; color: #FFFFFF;")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

        # Field table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Offset", "Field", "Value", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

    def _do_decode(self):
        raw = self.input_hex.toPlainText().strip().replace(" ", "").replace("\n", "")
        if not raw:
            self.status_lbl.setText("⚠️ Enter hex string")
            return
        pkt = PacketDecoder.decode(raw)
        if not pkt:
            self.status_lbl.setText("❌ Failed to decode packet bytes")
            self.table.setRowCount(0)
            return

        self.status_lbl.setText(f"✅ {pkt.header.route_type_name} | {pkt.header.payload_type_name} ({len(raw)//2}B)")
        self.status_lbl.setStyleSheet("color: #10B981; font-weight: bold;")

        breakdown = generate_field_breakdown(pkt)
        self.table.setRowCount(len(breakdown))
        for row_idx, item in enumerate(breakdown):
            self.table.setItem(row_idx, 0, QTableWidgetItem(f"{item['offset']:02d}"))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(item["field"])))
            val_item = QTableWidgetItem(str(item["value"]))
            val_item.setForeground(Qt.GlobalColor.cyan)
            self.table.setItem(row_idx, 2, val_item)
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(item["desc"])))


class ByteInspectorDrawer(QFrame):
    """Collapsible bottom inspector panel showing byte breakdown and formatted hex dump."""

    trace_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_packet: Optional[DecodedPacket] = None
        self.current_path: Optional[PacketPathInfo] = None
        self.setObjectName("byteInspectorDrawer")
        self._setup_ui()
        self._setup_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Header Bar
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        self.title_lbl = QLabel("🔍 BYTE-LEVEL INSPECTOR")
        self.title_lbl.setStyleSheet("color: #60A5FA; font-family: monospace; font-size: 11px; font-weight: bold;")
        hdr.addWidget(self.title_lbl)

        self.meta_badge = QLabel("No packet selected")
        self.meta_badge.setStyleSheet("color: #94A3B8; font-family: monospace; font-size: 10px;")
        hdr.addWidget(self.meta_badge, 1)

        # Trace on Map Button
        self.btn_trace = QPushButton("🗺️ Trace on Map")
        self.btn_trace.setToolTip("Trigger CoreScope traveling particle beam animation on Leaflet map")
        self.btn_trace.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_trace.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border-radius: 4px;
                padding: 3px 10px;
                font-family: monospace;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
        """)
        self.btn_trace.clicked.connect(self._on_trace_clicked)
        hdr.addWidget(self.btn_trace)

        # Copy Hex Button
        self.btn_copy_hex = QPushButton("📋 Copy Hex")
        self.btn_copy_hex.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_hex.setStyleSheet("""
            QPushButton {
                background-color: #2B2D31;
                color: #93C5FD;
                border: 1px solid #3E4048;
                border-radius: 4px;
                padding: 3px 8px;
                font-family: monospace;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #35363C;
                color: #FFFFFF;
                border-color: #3B82F6;
            }
        """)
        self.btn_copy_hex.clicked.connect(self._on_copy_hex_clicked)
        hdr.addWidget(self.btn_copy_hex)

        layout.addLayout(hdr)

        # Tabs for Field Table and Hex Dump
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2E3035;
                background-color: #121316;
            }
            QTabBar::tab {
                background-color: #1E1F22;
                color: #94A3B8;
                border: 1px solid #2E3035;
                padding: 4px 10px;
                font-family: monospace;
                font-size: 10px;
            }
            QTabBar::tab:selected {
                background-color: #2B2D31;
                color: #C084FC;
                border-bottom: 2px solid #C084FC;
            }
        """)

        # Tab 1: Field Breakdown Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Offset", "Field", "Value", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #121316;
                color: #F8FAFC;
                gridline-color: #1E1F22;
                border: none;
                font-family: monospace;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #1E1F22;
                color: #94A3B8;
                border: 1px solid #2E3035;
                padding: 3px;
                font-family: monospace;
                font-size: 10px;
                font-weight: bold;
            }
        """)
        self.tabs.addTab(self.table, "Byte Breakdown")

        # Tab 2: Raw Hex Dump
        self.hex_text = QTextEdit()
        self.hex_text.setReadOnly(True)
        self.hex_text.setStyleSheet("""
            QTextEdit {
                background-color: #121316;
                color: #38BDF8;
                border: none;
                font-family: monospace;
                font-size: 11px;
                padding: 6px;
            }
        """)
        self.tabs.addTab(self.hex_text, "Hex Dump")

        layout.addWidget(self.tabs, 1)

    def _setup_style(self):
        self.setStyleSheet("""
            QFrame#byteInspectorDrawer {
                background-color: #1A1B1E;
                border-top: 2px solid #3E4048;
            }
        """)

    def inspect_path(self, path: PacketPathInfo):
        self.current_path = path
        raw_hex = getattr(path, "raw_hex", "") or ""

        # Decode if raw hex exists
        if raw_hex:
            self.current_packet = PacketDecoder.decode(raw_hex)
        else:
            self.current_packet = None

        rtype = (path.route_type or "FLOOD").upper()
        ptype = getattr(path, "payload_type", "FLOOD") or "FLOOD"
        sender = path.sender_name or path.sender_id or "Unknown"
        hops_count = len(path.hop_nodes) if path.hop_nodes else 0
        self.meta_badge.setText(f"{rtype} | {ptype} | Orig: {sender} | {hops_count} hops")

        # Populate Field Table
        if self.current_packet:
            breakdown = generate_field_breakdown(self.current_packet)
        else:
            # Synthesize basic breakdown from PacketPathInfo
            breakdown = [
                {"offset": 0, "field": "Route Type", "value": rtype, "desc": "RF propagation mode"},
                {"offset": 1, "field": "Payload Type", "value": ptype, "desc": "Wire payload identifier"},
                {"offset": 2, "field": "Sender Node", "value": sender, "desc": path.sender_id or ""},
                {"offset": 3, "field": "Hop Chain", "value": " ➔ ".join(path.hop_nodes) if path.hop_nodes else "Direct", "desc": f"{hops_count} intermediate repeaters"}
            ]
            if path.hop_snrs:
                breakdown.append({"offset": 4, "field": "Link SNR", "value": f"{path.hop_snrs[0]:+.1f} dB", "desc": "First hop signal-to-noise ratio"})

        self.table.setRowCount(len(breakdown))
        for r_idx, item in enumerate(breakdown):
            off_item = QTableWidgetItem(f"{item['offset']:02d}")
            off_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r_idx, 0, off_item)
            self.table.setItem(r_idx, 1, QTableWidgetItem(str(item["field"])))

            val_item = QTableWidgetItem(str(item["value"]))
            val_item.setForeground(Qt.GlobalColor.cyan)
            self.table.setItem(r_idx, 2, val_item)

            self.table.setItem(r_idx, 3, QTableWidgetItem(str(item["desc"])))

        # Format Hex Dump
        if raw_hex:
            try:
                buf = bytes.fromhex(raw_hex)
                lines = []
                for i in range(0, len(buf), 16):
                    chunk = buf[i:i + 16]
                    hex_str = " ".join(f"{b:02X}" for b in chunk)
                    ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
                    lines.append(f"{i:04X}  {hex_str:<48}  |{ascii_str}|")
                self.hex_text.setPlainText("\n".join(lines))
            except Exception:
                self.hex_text.setPlainText(raw_hex)
        else:
            self.hex_text.setPlainText("No raw wire bytes captured for this historical packet.")

    def _on_trace_clicked(self):
        if self.current_path:
            self.trace_requested.emit(self.current_path)

    def _on_copy_hex_clicked(self):
        raw = getattr(self.current_path, "raw_hex", "") if self.current_path else ""
        if raw:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(raw)
                self.btn_copy_hex.setText("✅ Copied!")
                QTimer.singleShot(1500, lambda: self.btn_copy_hex.setText("📋 Copy Hex"))


class HeardFloodsWidget(QWidget):
    """Monospace watcher-styled pane displaying live and historical heard floods.

    Upgraded with CoreScope-styled Live Packet Feed, Filter Pills, and Byte Inspector.
    """

    flood_hovered = pyqtSignal(object)    # Emits PacketPathInfo to preview on map
    flood_unhovered = pyqtSignal()        # Restores normal map visualization
    flood_selected = pyqtSignal(object)   # Emits PacketPathInfo to pin route / inspect
    back_to_chat_requested = pyqtSignal() # Request to restore regular chat window
    packet_traced = pyqtSignal(object)    # Emits PacketPathInfo to run particle beam trace

    def __init__(self, storage: Optional[Storage] = None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.paused = False
        self.setObjectName("heardFloodsWidget")
        self._rows: List[FloodRowWidget] = []
        self._all_paths: List[PacketPathInfo] = []
        self._active_filter: str = "ALL"
        self._search_query: str = ""

        self._setup_ui()
        self._load_recent_floods()
        self._subscribe_events()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Top Header Frame
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
            color: #60A5FA;
            font-family: monospace;
            font-size: 11px;
            font-weight: bold;
        """)
        header_layout.addWidget(self.title_lbl)

        self.count_badge = QLabel("0 Floods")
        self.count_badge.setStyleSheet("""
            background-color: rgba(59, 130, 246, 0.15);
            color: #93C5FD;
            border: 1px solid rgba(59, 130, 246, 0.3);
            border-radius: 10px;
            padding: 2px 8px;
            font-family: monospace;
            font-size: 10px;
            font-weight: bold;
        """)
        header_layout.addWidget(self.count_badge)

        header_layout.addStretch()

        # BYOP / Decode Raw Hex Button
        self.btn_byop = QPushButton("📦 Decode Hex")
        self.btn_byop.setToolTip("Open CoreScope Wire-Level Packet Decoder modal")
        self.btn_byop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_byop.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #38BDF8;
                border: 1px solid #3E4048;
                border-radius: 4px;
                padding: 3px 8px;
                font-family: monospace;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #38BDF8;
                color: #000000;
            }
        """)
        self.btn_byop.clicked.connect(self._open_byop_dialog)
        header_layout.addWidget(self.btn_byop)

        # Pause / Resume Button
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setToolTip("Pause / Resume live stream")
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.setStyleSheet(self._btn_style())
        self.btn_pause.clicked.connect(self._toggle_pause)
        header_layout.addWidget(self.btn_pause)

        # Clear Button
        self.btn_clear = QPushButton("🗑 Clear")
        self.btn_clear.setToolTip("Clear list")
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

        main_layout.addWidget(header_frame)

        # 2. Filter Bar (Pills + Search input)
        filter_frame = QFrame()
        filter_frame.setStyleSheet("""
            QFrame {
                background-color: #16171A;
                border-bottom: 1px solid #2E3035;
                padding: 4px 10px;
            }
        """)
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(5)

        self._filter_buttons = {}
        filters = [
            ("ALL", "All"),
            ("RADIO", "📻 Radio"),
            ("MQTT", "🌐 MQTT"),
            ("FLOOD", "⚡ Floods"),
            ("ADVERT", "📢 Adverts"),
            ("GRP_TXT", "💬 Chat"),
            ("REQ", "📥 Requests"),
            ("TRACE", "📡 Traces"),
            ("ACK", "✅ ACKs"),
        ]
        for fid, flabel in filters:
            btn = QPushButton(flabel)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            if fid == "ALL":
                btn.setChecked(True)
            btn.setStyleSheet(self._pill_style(btn.isChecked()))
            btn.clicked.connect(lambda checked, f=fid: self._on_filter_clicked(f))
            self._filter_buttons[fid] = btn
            filter_layout.addWidget(btn)

        filter_layout.addStretch()

        # Search Bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filter nodes, hops, or text...")
        self.search_input.setMaximumWidth(220)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #121316;
                color: #F8FAFC;
                border: 1px solid #2E3035;
                border-radius: 4px;
                padding: 3px 8px;
                font-family: monospace;
                font-size: 10px;
            }
            QLineEdit:focus {
                border-color: #3B82F6;
            }
        """)
        self.search_input.textChanged.connect(self._on_search_changed)
        filter_layout.addWidget(self.search_input)

        main_layout.addWidget(filter_frame)

        # 3. Main Splitter: Top Scroll Area + Bottom Byte Inspector Drawer
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #2E3035;
                height: 4px;
            }
            QSplitter::handle:hover {
                background-color: #C084FC;
            }
        """)

        # Scroll area for packet/flood rows
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
        self.splitter.addWidget(self.scroll_area)

        # Bottom Byte Inspector Drawer
        self.inspector = ByteInspectorDrawer()
        self.inspector.trace_requested.connect(self._on_inspector_trace_requested)
        self.splitter.addWidget(self.inspector)

        # Split proportions: 65% top, 35% bottom
        self.splitter.setSizes([350, 180])

        main_layout.addWidget(self.splitter, 1)

    def _pill_style(self, checked: bool) -> str:
        if checked:
            return """
                QPushButton {
                    background-color: #1E3A8A;
                    color: #93C5FD;
                    border: 1px solid #3B82F6;
                    border-radius: 10px;
                    padding: 2px 10px;
                    font-family: monospace;
                    font-size: 10px;
                    font-weight: bold;
                }
            """
        return """
            QPushButton {
                background-color: #1A1B1E;
                color: #94A3B8;
                border: 1px solid #2E3035;
                border-radius: 10px;
                padding: 2px 10px;
                font-family: monospace;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #1E293B;
                color: #FFFFFF;
                border-color: #3B82F6;
            }
        """

    def _on_filter_clicked(self, selected_filter: str):
        self._active_filter = selected_filter
        for fid, btn in self._filter_buttons.items():
            btn.setChecked(fid == selected_filter)
            btn.setStyleSheet(self._pill_style(btn.isChecked()))
        self._apply_filters()

    def _on_search_changed(self, text: str):
        self._search_query = text.strip().lower()
        self._apply_filters()

    def _apply_filters(self):
        visible_count = 0
        for row in self._rows:
            p = row.path
            ptype = (getattr(p, "payload_type", "FLOOD") or "FLOOD").upper()
            rtype = (p.route_type or "FLOOD").upper()
            sname = (p.sender_name or "").upper()

            # Check Filter Pill
            type_match = True
            psrc = (getattr(p, "source", "radio") or "radio").lower()
            if self._active_filter == "RADIO":
                type_match = (psrc == "radio" and not p.packet_id.startswith("mqtt-"))
            elif self._active_filter == "MQTT":
                type_match = (psrc == "mqtt" or p.packet_id.startswith("mqtt-"))
            elif self._active_filter == "FLOOD":
                type_match = ("FLOOD" in rtype or "FLOOD" in ptype)
            elif self._active_filter == "ADVERT":
                type_match = ("ADVERT" in ptype or "ADV" in ptype or "ADVERT" in sname or "ADV" in sname)
            elif self._active_filter == "GRP_TXT":
                type_match = ("TXT" in ptype or "CHAT" in ptype or "MSG" in ptype or "CHAN" in ptype or "GRP_TXT" in ptype or "DM" in sname)
            elif self._active_filter == "REQ":
                type_match = ("REQ" in ptype or "ANON" in ptype or "RESPONSE" in ptype or "REQ" in rtype)
            elif self._active_filter == "TRACE":
                type_match = ("TRACE" in ptype or "TRACE" in rtype or "PATH" in ptype or "DISCOVERY" in rtype or "TRACE" in sname)
            elif self._active_filter == "ACK":
                type_match = ("ACK" in ptype or "ACK" in rtype or "ACK" in sname)
            elif self._active_filter != "ALL":
                type_match = (self._active_filter in ptype or self._active_filter in rtype)

            # Check Search Query
            search_match = True
            if self._search_query:
                q = self._search_query
                search_match = bool(
                    q in (p.sender_name or "").lower() or
                    q in (p.sender_id or "").lower() or
                    q in (p.route_type or "").lower() or
                    q in (getattr(p, "payload_type", "") or "").lower() or
                    q in (getattr(p, "source", "") or "").lower() or
                    any(q in str(h).lower() for h in (p.hop_nodes or [])) or
                    (bool(p.decoded_info) and q in str(p.decoded_info).lower())
                )

            is_visible = bool(type_match and search_match)
            row.setVisible(is_visible)

            if is_visible:
                visible_count += 1

        self.count_badge.setText(f"{visible_count} Packets" if self._active_filter != "FLOOD" else f"{visible_count} Floods")

    def _open_byop_dialog(self):
        dlg = DecodePacketDialog(self)
        dlg.exec()

    def _on_inspector_trace_requested(self, path: PacketPathInfo):
        self.packet_traced.emit(path)
        self.flood_selected.emit(path)

    def _btn_style(self) -> str:
        return """
            QPushButton {
                background-color: #24262B;
                color: #60A5FA;
                border: 1px solid #3E4048;
                border-radius: 4px;
                padding: 3px 8px;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #35363C;
                color: #FFFFFF;
                border-color: #3B82F6;
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

        self.list_container.setUpdatesEnabled(False)
        try:
            for p in paths:
                # Auto-infer payload type if missing or generic "FLOOD"
                if not getattr(p, "payload_type", None) or p.payload_type == "FLOOD":
                    if getattr(p, "raw_hex", None):
                        try:
                            dec = decode_meshcore_packet(p.raw_hex)
                            if dec and dec.header and dec.header.payload_type_name:
                                p.payload_type = dec.header.payload_type_name
                                if not p.decoded_info:
                                    p.decoded_info = dec.to_dict()
                        except Exception:
                            pass
                    if not getattr(p, "payload_type", None) or p.payload_type == "FLOOD":
                        sname = (p.sender_name or "").upper()
                        rtype = (p.route_type or "").upper()
                        if "TRACE" in sname or "TRACE" in rtype:
                            p.payload_type = "TRACE"
                        elif "ADVERT" in sname or "ADV" in sname:
                            p.payload_type = "ADVERT"
                        elif "CHAT" in sname or "CHAN" in sname or "DM" in sname or "[" in sname or "#" in sname:
                            p.payload_type = "GRP_TXT"
                        elif "ACK" in sname or "ACK" in rtype:
                            p.payload_type = "ACK"

                is_unread = False
                if last_read_ts and p.timestamp:
                    is_unread = (p.timestamp > last_read_ts)
                elif not last_read_ts:
                    is_unread = True

                row = self._add_path_row(p, prepend=False, is_unread=is_unread)
                if is_unread:
                    first_unread_row = row
        finally:
            self.list_container.setUpdatesEnabled(True)

        if first_unread_row:
            QTimer.singleShot(80, lambda: self._scroll_to_row(first_unread_row))
        else:
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

    def _on_row_selected(self, path: PacketPathInfo, row: FloodRowWidget):
        for r in self._rows:
            r.set_active(r == row)
        self.inspector.inspect_path(path)
        self.flood_selected.emit(path)

    def _add_path_row(self, path: PacketPathInfo, prepend: bool = False, is_unread: bool = False) -> Optional[FloodRowWidget]:
        if not path:
            return None

        is_radio = getattr(path, "source", "radio") != "mqtt" and not path.packet_id.startswith("mqtt-")

        def _get_sig(p: PacketPathInfo) -> str:
            hex_val = (getattr(p, "raw_hex", "") or "").strip().upper()
            if hex_val and len(hex_val) >= 8:
                return f"hex:{hex_val}"
            txt = ""
            chan = ""
            if getattr(p, "decoded_info", None) and isinstance(p.decoded_info, dict):
                txt = str(p.decoded_info.get("text", "") or "").strip()
                chan = str(p.decoded_info.get("channel", "") or "").strip().lower()
            if not txt and "[" in (p.sender_name or "") and "]" in (p.sender_name or ""):
                txt = p.sender_name
            if txt:
                return f"msg:{chan}:{txt}"
            if p.sender_id and getattr(p, "payload_type", "") == "ADVERT":
                return f"adv:{p.sender_id.lower().lstrip('!')}"
            return ""

        cur_sig = _get_sig(path)
        if cur_sig:
            for existing_row in list(self._rows[:40]):
                e_path = existing_row.path
                if not e_path:
                    continue
                e_is_radio = getattr(e_path, "source", "radio") != "mqtt" and not e_path.packet_id.startswith("mqtt-")
                if _get_sig(e_path) == cur_sig:
                    if is_radio and not e_is_radio:
                        # Radio heard the same event that MQTT previously logged:
                        # Remove the MQTT row so the authoritative RF radio row replaces it!
                        if existing_row in self._rows:
                            self._rows.remove(existing_row)
                        if e_path in self._all_paths:
                            self._all_paths.remove(e_path)
                        self.list_layout.removeWidget(existing_row)
                        existing_row.deleteLater()
                        break
                    elif not is_radio and e_is_radio:
                        # MQTT incoming packet already heard on physical radio: DROP MQTT!
                        return None
                    elif is_radio == e_is_radio:
                        # Duplicate within recent window: ignore duplicate
                        return None

        row = FloodRowWidget(path, is_unread=is_unread)
        row.hovered.connect(self.flood_hovered.emit)
        row.unhovered.connect(self.flood_unhovered.emit)
        row.selected.connect(lambda p: self._on_row_selected(p, row))

        if prepend:
            self.list_layout.insertWidget(0, row)
            self._rows.insert(0, row)
            self._all_paths.insert(0, path)
        else:
            idx = max(0, self.list_layout.count() - 1)
            self.list_layout.insertWidget(idx, row)
            self._rows.append(row)
            self._all_paths.append(path)

        # Cap at 150 rows in memory
        if len(self._rows) > 150:
            oldest = self._rows.pop()
            self.list_layout.removeWidget(oldest)
            oldest.deleteLater()
            if self._all_paths:
                self._all_paths.pop()

        self.count_badge.setText(f"{len(self._rows)} Floods")
        return row

    def _toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.setText("▶ Resume")
            self.title_lbl.setText("⏸️ PAUSED")
        else:
            self.btn_pause.setText("⏸ Pause")
            self.title_lbl.setText("⚡ HEARD FLOODS")

    def clear_list(self):
        for r in self._rows:
            self.list_layout.removeWidget(r)
            r.deleteLater()
        self._rows.clear()
        self._all_paths.clear()
        self.count_badge.setText("0 Floods")

    def reload(self):
        """Reloads recent packet paths from storage."""
        self.clear_list()
        self._load_recent_floods()


# Backward compatibility and modern naming alias
PacketFeedWidget = HeardFloodsWidget


