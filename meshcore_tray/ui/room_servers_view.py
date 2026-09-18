"""Room Servers View Widget for MeshCore Tray.

Provides discovery, saved password authentication, and interactive bulletin
board / message stream for MeshCore Room Servers.
"""

import html
import logging
from datetime import datetime, timezone
from typing import Optional, List
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QPushButton, QCheckBox,
    QScrollArea, QTextEdit, QSizePolicy, QMenu, QApplication
)

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact, MessageEnvelope, is_room_server_contact
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon
from meshcore_tray.ui.link_parser import format_message_text_with_links

logger = logging.getLogger("meshcore_tray.room_servers_view")


def format_last_seen(ts: Optional[str]) -> str:
    """Formats timestamp into DD/MM/YY HH:MM format."""
    if not ts:
        return "No activity yet"
    try:
        clean = str(ts).strip().replace("T", " ")
        if "." in clean:
            clean = clean.split(".")[0]
        if "+" in clean:
            clean = clean.split("+")[0]

        parts = clean.split(" ")
        date_part = parts[0]
        time_part = parts[1][:5] if len(parts) > 1 else ""

        if "-" in date_part:
            ymd = date_part.split("-")
            if len(ymd) == 3:
                year, month, day = ymd[0], ymd[1], ymd[2]
                short_year = year[-2:]
                formatted_date = f"{day}/{month}/{short_year}"
                if time_part:
                    return f"Seen: {formatted_date} {time_part}"
                return f"Seen: {formatted_date}"
        return f"Seen: {clean[:16]}"
    except Exception:
        return f"Seen: {str(ts)[:16]}"


class RoomServerRowWidget(QWidget):
    """Sidebar list item for a discovered Room Server."""

    def __init__(self, contact: NodeContact, has_saved_password: bool = False, is_favorite: bool = False, parent=None):
        super().__init__(parent)
        self.contact = contact
        self.has_saved_password = has_saved_password
        self.is_favorite = is_favorite
        self.setFixedHeight(54)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("""
            QWidget {
                background: transparent;
                border: none;
            }
            QLabel {
                background: transparent;
                border: none;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        # 45-degree diamond room badge
        self.badge = QLabel()
        self.badge.setFixedSize(36, 36)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setText("◆")
        self.badge.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4A0E4E, stop:1 #2D0830);
            color: #FF00FF;
            border: 1.5px solid #FF00FF;
            border-radius: 6px;
            font-size: 18px;
            font-weight: bold;
        """)
        layout.addWidget(self.badge)

        # Information column
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        clean_name = contact.alias.replace("[Room]", "").replace("[room]", "").replace("[Server]", "").replace("[server]", "").strip()
        clean_name = html.unescape(clean_name) or contact.node_id

        self.name_lbl = QLabel(clean_name)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setStyleSheet("color: #F2F3F5; font-size: 13px; font-weight: 600;")
        name_row.addWidget(self.name_lbl)

        if self.is_favorite:
            self.star_lbl = QLabel("★")
            self.star_lbl.setStyleSheet("color: #FFD700; font-size: 13px; font-weight: bold;")
            name_row.addWidget(self.star_lbl)

        name_row.addStretch()

        if self.has_saved_password:
            self.key_badge = QLabel("🔑 Saved")
            self.key_badge.setStyleSheet("""
                background-color: #1E293B;
                color: #38BDF8;
                border: 1px solid #0284C7;
                border-radius: 4px;
                padding: 1px 5px;
                font-size: 9px;
                font-weight: bold;
            """)
            name_row.addWidget(self.key_badge)

        info_layout.addLayout(name_row)

        # Subtitle: Signal & Last Seen
        last_time = getattr(contact, "last_seen", None) or getattr(contact, "last_heard", None)
        sub_text = format_last_seen(last_time)
        if getattr(contact, "snr_db", None) is not None and contact.snr_db != 0:
            sub_text += f" • SNR: {contact.snr_db:+.1f}dB"
        self.sub_lbl = QLabel(sub_text)
        self.sub_lbl.setStyleSheet("color: #949BA4; font-size: 11px;")
        info_layout.addWidget(self.sub_lbl)

        layout.addLayout(info_layout, 1)


class MessageBubbleWidget(QFrame):
    """Clean message bubble for room server bulletin board."""

    def __init__(self, message: MessageEnvelope, parent=None):
        super().__init__(parent)
        self.message = message
        self._init_ui()

    def _init_ui(self):
        is_out = bool(self.message.is_outgoing)
        is_room_resp = bool(self.message.metadata.get("is_room_response", False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Top sender and timestamp line
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        sender = self.message.sender_name or self.message.sender_id or "Unknown"
        if is_out:
            sender_color = "#34D399"
            sender_label = f"You ({sender})"
            bg_color = "#132E27"
            border_color = "#10B981"
        elif is_room_resp:
            sender_color = "#FF55FF"
            sender_label = f"🏢 {sender}"
            bg_color = "#2A142E"
            border_color = "#C026D3"
        else:
            sender_color = "#38BDF8"
            sender_label = sender
            bg_color = "#1E222B"
            border_color = "#333842"

        lbl_sender = QLabel(sender_label)
        lbl_sender.setStyleSheet(f"color: {sender_color}; font-weight: bold; font-size: 12px;")
        top_row.addWidget(lbl_sender)

        # Timestamp
        ts_clean = self.message.timestamp.split("T")[-1][:5] if "T" in self.message.timestamp else self.message.timestamp[:5]
        lbl_ts = QLabel(ts_clean)
        lbl_ts.setStyleSheet("color: #64748B; font-size: 10px;")
        top_row.addWidget(lbl_ts)

        top_row.addStretch()

        # Signal info if available
        snr = self.message.metadata.get("snr")
        if snr is not None:
            lbl_sig = QLabel(f"SNR: {float(snr):+.1f} dB")
            lbl_sig.setStyleSheet("color: #64748B; font-size: 10px;")
            top_row.addWidget(lbl_sig)

        layout.addLayout(top_row)

        # Message Body
        lbl_text = QLabel()
        lbl_text.setTextFormat(Qt.TextFormat.RichText)
        lbl_text.setWordWrap(True)
        lbl_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        lbl_text.setOpenExternalLinks(True)
        lbl_text.setText(format_message_text_with_links(self.message.text))
        lbl_text.setStyleSheet("color: #E2E8F0; font-size: 13px; line-height: 1.4;")
        layout.addWidget(lbl_text)

        self.setStyleSheet(f"""
            MessageBubbleWidget {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)


class RoomServersViewWidget(QWidget):
    """Full-featured Room Servers management and bulletin board interface."""

    show_on_map_requested = pyqtSignal(str, float, float, str)  # (node_id, lat, lon, alias)
    track_adsb_requested = pyqtSignal(str, float, float, str)   # (node_id, lat, lon, alias)
    send_message_requested = pyqtSignal(str, str)               # (node_id, text)

    def __init__(self, storage=None, config: Optional[AppConfig] = None, radio_driver=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.radio_driver = radio_driver
        self.active_room: Optional[NodeContact] = None
        self.rooms_list: List[NodeContact] = []
        self.sort_mode = "alpha"
        self._init_ui()
        self._setup_bus_events()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #23262D;
                width: 1px;
            }
        """)

        # LEFT PANE: Discovered Room Servers List
        left_widget = QWidget()
        left_widget.setMinimumWidth(260)
        left_widget.setMaximumWidth(360)
        left_widget.setStyleSheet("background-color: #17191E; border-right: 1px solid #23262D;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        # Header
        hdr_layout = QHBoxLayout()
        title_lbl = QLabel("Room Servers")
        title_lbl.setStyleSheet("color: #F8FAFC; font-size: 15px; font-weight: bold;")
        hdr_layout.addWidget(title_lbl)

        self.badge_count = QLabel("0")
        self.badge_count.setStyleSheet("""
            background-color: #3B0764;
            color: #E9D5FF;
            border: 1px solid #9333EA;
            border-radius: 10px;
            padding: 1px 8px;
            font-size: 11px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.badge_count)
        hdr_layout.addStretch()

        btn_refresh = QPushButton("🔄")
        btn_refresh.setFixedSize(28, 28)
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setToolTip("Refresh Room Servers List")
        btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #94A3B8;
                border: 1px solid #333842;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #333842;
                color: #FFFFFF;
            }
        """)
        btn_refresh.clicked.connect(self.reload_rooms)
        hdr_layout.addWidget(btn_refresh)
        left_layout.addLayout(hdr_layout)

        # Search / Filter Bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter room servers...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E2026;
                color: #F8FAFC;
                border: 1px solid #2D3139;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #FF00FF;
            }
        """)
        self.search_input.textChanged.connect(self._filter_rooms_list)
        left_layout.addWidget(self.search_input)

        # Sort Mode Toolbar
        sort_layout = QHBoxLayout()
        sort_layout.setSpacing(6)
        lbl_sort = QLabel("Sort:")
        lbl_sort.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold;")
        sort_layout.addWidget(lbl_sort)

        self.btn_sort_alpha = QPushButton("🔤 Name")
        self.btn_sort_alpha.setCheckable(True)
        self.btn_sort_alpha.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sort_alpha.clicked.connect(lambda: self._set_sort_mode("alpha"))
        sort_layout.addWidget(self.btn_sort_alpha)

        self.btn_sort_recent = QPushButton("🕒 Recent")
        self.btn_sort_recent.setCheckable(True)
        self.btn_sort_recent.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sort_recent.clicked.connect(lambda: self._set_sort_mode("recent"))
        sort_layout.addWidget(self.btn_sort_recent)
        sort_layout.addStretch()
        left_layout.addLayout(sort_layout)
        self._update_sort_buttons()

        # Room Servers QListWidget
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
            }
            QListWidget::item {
                border-radius: 8px;
                margin-bottom: 4px;
                padding: 2px;
            }
            QListWidget::item:hover {
                background-color: #21242C;
            }
            QListWidget::item:selected {
                background-color: #2D1B36;
                border: 1px solid #FF00FF;
            }
        """)
        self.list_widget.itemSelectionChanged.connect(self._on_room_selection_changed)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_room_context_menu)
        left_layout.addWidget(self.list_widget, 1)

        # Bottom tip
        tip_lbl = QLabel("📡 Room servers announce via AdvType.ROOM (0x03) or tags like North-BBS [Room].")
        tip_lbl.setWordWrap(True)
        tip_lbl.setStyleSheet("color: #64748B; font-size: 10px; line-height: 1.3;")
        left_layout.addWidget(tip_lbl)

        splitter.addWidget(left_widget)

        # RIGHT PANE: Details, Authentication Card, and Bulletin Stream
        self.right_container = QWidget()
        self.right_container.setStyleSheet("background-color: #121418;")
        right_layout = QVBoxLayout(self.right_container)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(14)

        # Placeholder when no room server is selected
        self.placeholder = QWidget()
        ph_layout = QVBoxLayout(self.placeholder)
        ph_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_layout.setSpacing(10)

        ph_icon = QLabel("🏢")
        ph_icon.setStyleSheet("font-size: 48px; color: #FF00FF;")
        ph_layout.addWidget(ph_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        ph_title = QLabel("Select a Room Server")
        ph_title.setStyleSheet("color: #F8FAFC; font-size: 18px; font-weight: bold;")
        ph_layout.addWidget(ph_title, alignment=Qt.AlignmentFlag.AlignCenter)

        ph_sub = QLabel(
            "Select a discovered Room Server on the left to authenticate,\n"
            "save your credentials, and read the public bulletin message stream."
        )
        ph_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_sub.setStyleSheet("color: #94A3B8; font-size: 13px; line-height: 1.5;")
        ph_layout.addWidget(ph_sub, alignment=Qt.AlignmentFlag.AlignCenter)

        right_layout.addWidget(self.placeholder, 1)

        # Active Room Server Interface Container
        self.room_content = QWidget()
        self.room_content.setVisible(False)
        rc_layout = QVBoxLayout(self.room_content)
        rc_layout.setContentsMargins(0, 0, 0, 0)
        rc_layout.setSpacing(12)

        # 1. TOP AUTHENTICATION CARD
        self.auth_card = QFrame()
        self.auth_card.setStyleSheet("""
            QFrame {
                background-color: #1A1C23;
                border: 1px solid #2D3139;
                border-radius: 10px;
                padding: 6px;
            }
        """)
        auth_card_layout = QVBoxLayout(self.auth_card)
        auth_card_layout.setContentsMargins(14, 12, 14, 12)
        auth_card_layout.setSpacing(10)

        # Host Banner Row
        host_row = QHBoxLayout()
        host_row.setSpacing(10)

        self.card_icon = QLabel("◆")
        self.card_icon.setFixedSize(40, 40)
        self.card_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_icon.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4A0E4E, stop:1 #2D0830);
            color: #FF00FF;
            border: 2px solid #FF00FF;
            border-radius: 8px;
            font-size: 20px;
            font-weight: bold;
        """)
        host_row.addWidget(self.card_icon)

        host_meta = QVBoxLayout()
        host_meta.setSpacing(2)
        self.lbl_room_name = QLabel("Room Server")
        self.lbl_room_name.setStyleSheet("color: #F8FAFC; font-size: 16px; font-weight: bold;")
        self.lbl_room_id = QLabel("!00000000 • MeshCore Room Server")
        self.lbl_room_id.setStyleSheet("color: #94A3B8; font-size: 11px; font-family: monospace;")
        host_meta.addWidget(self.lbl_room_name)
        host_meta.addWidget(self.lbl_room_id)
        host_row.addLayout(host_meta)

        host_row.addStretch()

        # Telemetry pills
        self.lbl_telem = QLabel("SNR: -- dB • RSSI: -- dBm")
        self.lbl_telem.setStyleSheet("""
            background-color: #242833;
            color: #94A3B8;
            border: 1px solid #333846;
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
        """)
        host_row.addWidget(self.lbl_telem)

        self.btn_map = QPushButton("📍 Locate on Map")
        self.btn_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_map.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #0369A1;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
        """)
        self.btn_map.clicked.connect(self._on_locate_on_map_clicked)
        host_row.addWidget(self.btn_map)

        self.btn_adsb = QPushButton("✈️ Track ADS-B")
        self.btn_adsb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_adsb.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #38BDF8;
                border: 1px solid #0284C7;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #283548;
            }
        """)
        self.btn_adsb.clicked.connect(self._on_track_adsb_clicked)
        host_row.addWidget(self.btn_adsb)

        self.btn_fav = QPushButton("⭐ Favourite")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #F8FAFC;
                border: 1px solid #333842;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #333842;
                color: #FFD700;
            }
        """)
        self.btn_fav.clicked.connect(self._toggle_active_room_favorite)
        host_row.addWidget(self.btn_fav)

        auth_card_layout.addLayout(host_row)

        # Thin Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #262933; border: none; min-height: 1px; max-height: 1px;")
        auth_card_layout.addWidget(div)

        # Authentication Controls Row
        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)

        lbl_pwd = QLabel("Password:")
        lbl_pwd.setStyleSheet("color: #CBD5E1; font-size: 12px; font-weight: 600;")
        auth_row.addWidget(lbl_pwd)

        self.pwd_input = QLineEdit()
        self.pwd_input.setPlaceholderText("Enter room password...")
        self.pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd_input.setStyleSheet("""
            QLineEdit {
                background-color: #111317;
                color: #F8FAFC;
                border: 1px solid #333842;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #FF00FF;
            }
        """)
        self.pwd_input.returnPressed.connect(self._on_login_clicked)
        auth_row.addWidget(self.pwd_input, 1)

        self.btn_toggle_echo = QPushButton("👁️")
        self.btn_toggle_echo.setFixedSize(32, 32)
        self.btn_toggle_echo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_echo.setToolTip("Show / Hide Password")
        self.btn_toggle_echo.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #94A3B8;
                border: 1px solid #333842;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #333842;
            }
        """)
        self.btn_toggle_echo.clicked.connect(self._toggle_password_echo)
        auth_row.addWidget(self.btn_toggle_echo)

        self.chk_remember = QCheckBox("Save Password")
        self.chk_remember.setChecked(True)
        self.chk_remember.setToolTip("Persist password in local database so you can reconnect automatically.")
        self.chk_remember.setStyleSheet("color: #94A3B8; font-size: 11px;")
        auth_row.addWidget(self.chk_remember)

        self.btn_login = QPushButton("🔑 Log In")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #10B981, stop:1 #059669);
                color: #FFFFFF;
                border: 1px solid #10B981;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34D399, stop:1 #10B981);
            }
        """)
        self.btn_login.clicked.connect(self._on_login_clicked)
        auth_row.addWidget(self.btn_login)

        self.btn_logout = QPushButton("Log Out")
        self.btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_logout.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #E2E8F0;
                border: 1px solid #333842;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #333842;
            }
        """)
        self.btn_logout.clicked.connect(self._on_logout_clicked)
        auth_row.addWidget(self.btn_logout)

        auth_card_layout.addLayout(auth_row)

        # Quick Commands Bar
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(6)

        lbl_cmds = QLabel("Quick Commands:")
        lbl_cmds.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600;")
        cmd_row.addWidget(lbl_cmds)

        for cmd, label in [("!help", "📖 !help"), ("!read", "📢 !read"), ("!info", "ℹ️ !info"), ("!status", "🔋 !status"), ("!list", "📁 !list")]:
            btn_cmd = QPushButton(label)
            btn_cmd.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_cmd.setStyleSheet("""
                QPushButton {
                    background-color: #21242C;
                    color: #CBD5E1;
                    border: 1px solid #333846;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #2D1B36;
                    border-color: #FF00FF;
                    color: #FF00FF;
                }
            """)
            btn_cmd.clicked.connect(lambda _, c=cmd: self._send_command(c))
            cmd_row.addWidget(btn_cmd)

        cmd_row.addStretch()

        self.lbl_auth_status = QLabel("🔒 Not logged in")
        self.lbl_auth_status.setStyleSheet("color: #94A3B8; font-size: 11px; font-style: italic;")
        cmd_row.addWidget(self.lbl_auth_status)

        auth_card_layout.addLayout(cmd_row)
        rc_layout.addWidget(self.auth_card)

        # 2. BOTTOM BULLETIN STREAM / MESSAGE BOARD
        stream_card = QFrame()
        stream_card.setStyleSheet("""
            QFrame {
                background-color: #17191E;
                border: 1px solid #262932;
                border-radius: 10px;
            }
        """)
        stream_layout = QVBoxLayout(stream_card)
        stream_layout.setContentsMargins(12, 10, 12, 10)
        stream_layout.setSpacing(8)

        # Stream header
        st_hdr = QHBoxLayout()
        st_title = QLabel("📢 Room Messages & Bulletin Stream")
        st_title.setStyleSheet("color: #F8FAFC; font-size: 13px; font-weight: bold;")
        st_hdr.addWidget(st_title)

        self.lbl_msg_count = QLabel("0 messages")
        self.lbl_msg_count.setStyleSheet("color: #64748B; font-size: 11px;")
        st_hdr.addWidget(self.lbl_msg_count)

        st_hdr.addStretch()

        self.chk_autoscroll = QCheckBox("Auto-scroll")
        self.chk_autoscroll.setChecked(True)
        self.chk_autoscroll.setStyleSheet("color: #94A3B8; font-size: 11px;")
        st_hdr.addWidget(self.chk_autoscroll)

        stream_layout.addLayout(st_hdr)

        # Scroll Area for Message Bubbles
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #121418;
                border: 1px solid #1E2026;
                border-radius: 6px;
            }
            QScrollBar:vertical {
                background: #17191E;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #333842;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.bubble_container = QWidget()
        self.bubble_container.setStyleSheet("background-color: transparent;")
        self.bubble_layout = QVBoxLayout(self.bubble_container)
        self.bubble_layout.setContentsMargins(8, 8, 8, 8)
        self.bubble_layout.setSpacing(6)
        self.bubble_layout.addStretch()
        self.scroll_area.setWidget(self.bubble_container)
        stream_layout.addWidget(self.scroll_area, 1)

        # Message Composer Bar
        comp_row = QHBoxLayout()
        comp_row.setSpacing(8)

        self.composer_input = QLineEdit()
        self.composer_input.setPlaceholderText("Post message or command to room server (Enter to send)...")
        self.composer_input.setStyleSheet("""
            QLineEdit {
                background-color: #121418;
                color: #F8FAFC;
                border: 1px solid #2D3139;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #FF00FF;
            }
        """)
        self.composer_input.returnPressed.connect(self._on_send_message_clicked)
        comp_row.addWidget(self.composer_input, 1)

        self.btn_send = QPushButton("Post Message")
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #9333EA, stop:1 #7E22CE);
                color: #FFFFFF;
                border: 1px solid #A855F7;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #A855F7, stop:1 #9333EA);
            }
        """)
        self.btn_send.clicked.connect(self._on_send_message_clicked)
        comp_row.addWidget(self.btn_send)

        stream_layout.addLayout(comp_row)
        rc_layout.addWidget(stream_card, 1)

        right_layout.addWidget(self.room_content, 1)
        splitter.addWidget(self.right_container)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)
        self._update_sort_buttons()

    def _setup_bus_events(self):
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_bus_message)
        bus.subscribe(EventType.MESSAGE_SENT, self._on_bus_message)
        bus.subscribe(EventType.MAP_NODES_UPDATED, lambda _: self.reload_rooms())
        bus.subscribe(EventType.NODE_DISCOVERED, lambda _: self.reload_rooms())
        bus.subscribe(EventType.NEIGHBOURS_UPDATED, lambda _: self.reload_rooms())

    def reload_rooms(self):
        """Fetches room servers from storage and populates sidebar list."""
        if not self.storage:
            return

        current_sel_id = self.active_room.node_id if self.active_room else None
        try:
            self.rooms_list = self.storage.get_room_servers()
        except Exception as e:
            logger.warning(f"Error querying room servers: {e}")
            self.rooms_list = []

        self.badge_count.setText(str(len(self.rooms_list)))
        self._render_rooms_list(current_sel_id)

    def _set_sort_mode(self, mode: str):
        self.sort_mode = mode
        self._update_sort_buttons()
        self.reload_rooms()

    def _update_sort_buttons(self):
        is_alpha = (getattr(self, "sort_mode", "alpha") == "alpha")
        if hasattr(self, "btn_sort_alpha") and hasattr(self, "btn_sort_recent"):
            self.btn_sort_alpha.setChecked(is_alpha)
            self.btn_sort_recent.setChecked(not is_alpha)

            active_style = """
                QPushButton {
                    background-color: #464C5A;
                    color: #FFFFFF;
                    font-weight: bold;
                    border: 1px solid #60687A;
                    border-radius: 5px;
                    padding: 4px 8px;
                    font-size: 11px;
                }
            """
            inactive_style = """
                QPushButton {
                    background-color: #2B2F38;
                    color: #9CA3AF;
                    font-weight: normal;
                    border: 1px solid #414143;
                    border-radius: 5px;
                    padding: 4px 8px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #353842;
                    color: #FFFFFF;
                }
            """
            self.btn_sort_alpha.setStyleSheet(active_style if is_alpha else inactive_style)
            self.btn_sort_recent.setStyleSheet(inactive_style if is_alpha else active_style)

    def _add_thin_divider(self):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        container = QWidget()
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.setAutoFillBackground(False)
        container.setStyleSheet("background: transparent; border: none;")
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(12, 4, 12, 4)
        c_layout.setSpacing(0)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #383A40; border: none; max-height: 1px; min-height: 1px;")
        c_layout.addWidget(line)
        item.setSizeHint(QSize(200, 9))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, container)

    def _update_favorite_button(self, is_fav: bool):
        if not hasattr(self, "btn_fav"):
            return
        if is_fav:
            self.btn_fav.setText("★ Favourited")
            self.btn_fav.setStyleSheet("""
                QPushButton {
                    background-color: #3B2E1E;
                    color: #FFD700;
                    border: 1px solid #D97706;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #4D3C25;
                }
            """)
        else:
            self.btn_fav.setText("⭐ Favourite")
            self.btn_fav.setStyleSheet("""
                QPushButton {
                    background-color: #24262B;
                    color: #F8FAFC;
                    border: 1px solid #333842;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #333842;
                    color: #FFD700;
                }
            """)

    def _toggle_active_room_favorite(self):
        if not self.active_room:
            return
        node_id = self.active_room.node_id
        alias = self.active_room.alias
        is_fav = bool(self.active_room.is_favorite or (self.config and self.config.is_user_favorite(node_id, alias)))
        new_fav = not is_fav
        if self.storage:
            self.storage.set_contact_favorite(node_id, new_fav)
        if self.config:
            if new_fav:
                if node_id not in self.config.favorite_users:
                    self.config.favorite_users.append(node_id)
            else:
                self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != alias]
            self.config.save()
        self.active_room.is_favorite = new_fav
        self._update_favorite_button(new_fav)
        from meshcore_tray.core.event_bus import bus, EventType
        bus.emit(EventType.FAVORITES_UPDATED, node_id)
        self.reload_rooms()

    def _show_room_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        room = item.data(Qt.ItemDataRole.UserRole)
        if not room:
            return
        node_id = room.node_id
        alias = room.alias or node_id
        is_fav = bool(room.is_favorite or (self.config and self.config.is_user_favorite(node_id, alias)))

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #222327;
                color: #F2F3F5;
                border: 1px solid #414143;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #9333EA;
                color: #FFFFFF;
            }
        """)

        fav_action = menu.addAction("⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites")
        map_action = menu.addAction("🗺️ Show on Map")
        adsb_action = None
        if room.latitude is not None and room.longitude is not None:
            adsb_action = menu.addAction("✈️ Track ADS-B Around Room Server")
        pwd_action = menu.addAction("🔑 Enter / Edit Password...")
        menu.addSeparator()
        copy_id_action = menu.addAction(f"📋 Copy Node ID ({node_id})")
        copy_alias_action = menu.addAction(f"📋 Copy Alias ({alias})")
        menu.addSeparator()
        del_action = menu.addAction("🗑️ Remove Room Server")

        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == fav_action:
            new_fav = not is_fav
            if self.storage:
                self.storage.set_contact_favorite(node_id, new_fav)
            if self.config:
                if new_fav:
                    if node_id not in self.config.favorite_users:
                        self.config.favorite_users.append(node_id)
                else:
                    self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != alias]
                self.config.save()
            from meshcore_tray.core.event_bus import bus, EventType
            bus.emit(EventType.FAVORITES_UPDATED, node_id)
            if self.active_room and self.active_room.node_id.lower() == node_id.lower():
                self.active_room.is_favorite = new_fav
                self._update_favorite_button(new_fav)
            self.reload_rooms()
        elif action == map_action:
            self.show_on_map_requested.emit(node_id, room.latitude or 0.0, room.longitude or 0.0, alias)
        elif adsb_action and action == adsb_action:
            self.track_adsb_requested.emit(node_id, float(room.latitude or 0.0), float(room.longitude or 0.0), alias)
        elif action == pwd_action:
            self.set_active_room(room)
            self.pwd_input.setFocus()
        elif action == copy_id_action:
            QApplication.clipboard().setText(node_id)
        elif action == copy_alias_action:
            QApplication.clipboard().setText(alias)
        elif action == del_action:
            if self.storage:
                self.storage.delete_contact(node_id)
            if self.config:
                self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != alias]
                self.config.save()
            from meshcore_tray.core.event_bus import bus, EventType
            bus.emit(EventType.MAP_NODES_UPDATED, None)
            self.reload_rooms()

    def _render_rooms_list(self, keep_id: Optional[str] = None):
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            filter_q = self.search_input.text().strip().lower()

            favorites = []
            others = []
            for room in self.rooms_list:
                alias = room.alias or room.node_id
                if filter_q and (filter_q not in alias.lower() and filter_q not in room.node_id.lower()):
                    continue

                is_fav = bool(room.is_favorite or (self.config and self.config.is_user_favorite(room.node_id, room.alias)))
                if is_fav:
                    favorites.append(room)
                else:
                    others.append(room)

            def get_sort_key(r):
                if getattr(self, "sort_mode", "alpha") == "recent":
                    ts = getattr(r, "last_seen", None) or getattr(r, "last_heard", None) or ""
                    return str(ts)
                return (r.alias or r.node_id).lower()

            rev = (getattr(self, "sort_mode", "alpha") == "recent")
            favorites.sort(key=get_sort_key, reverse=rev)
            others.sort(key=get_sort_key, reverse=rev)

            matched_item = None

            # 1. Favorites
            for room in favorites:
                has_pwd = bool(self.storage.get_room_password(room.node_id)) if self.storage else False
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 54))
                item.setData(Qt.ItemDataRole.UserRole, room)
                row_widget = RoomServerRowWidget(room, has_saved_password=has_pwd, is_favorite=True)
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, row_widget)
                if keep_id and room.node_id.lower() == keep_id.lower():
                    matched_item = item

            # 2. Thin Divider
            if favorites and others:
                self._add_thin_divider()

            # 3. Others
            for room in others:
                has_pwd = bool(self.storage.get_room_password(room.node_id)) if self.storage else False
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 54))
                item.setData(Qt.ItemDataRole.UserRole, room)
                row_widget = RoomServerRowWidget(room, has_saved_password=has_pwd, is_favorite=False)
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, row_widget)
                if keep_id and room.node_id.lower() == keep_id.lower():
                    matched_item = item

            if matched_item:
                self.list_widget.setCurrentItem(matched_item)
            elif self.active_room and not any(r.node_id == self.active_room.node_id for r in self.rooms_list):
                self.room_content.setVisible(False)
                self.placeholder.setVisible(True)
                self.active_room = None
        finally:
            self.list_widget.blockSignals(False)

    def _filter_rooms_list(self):
        keep_id = self.active_room.node_id if self.active_room else None
        self._render_rooms_list(keep_id)

    def set_active_room(self, contact: NodeContact):
        """Sets active room server and updates UI."""
        self.active_room = contact
        self.placeholder.setVisible(False)
        self.room_content.setVisible(True)

        is_fav = bool(contact.is_favorite or (self.config and self.config.is_user_favorite(contact.node_id, contact.alias)))
        self._update_favorite_button(is_fav)

        clean_name = contact.alias.replace("[Room]", "").replace("[room]", "").replace("[Server]", "").replace("[server]", "").strip()
        clean_name = html.unescape(clean_name) or contact.node_id
        self.lbl_room_name.setText(clean_name)
        self.lbl_room_id.setText(f"{contact.node_id} • MeshCore Room Server")

        # Telemetry
        snr = getattr(contact, "snr_db", None)
        rssi = getattr(contact, "rssi_dbm", None)
        if snr is not None and rssi is not None and snr != 0:
            self.lbl_telem.setText(f"SNR: {snr:+.1f} dB • RSSI: {rssi:.0f} dBm")
        else:
            self.lbl_telem.setText("SNR: -- dB • RSSI: -- dBm")

        # Map button state
        has_coords = contact.latitude is not None and contact.longitude is not None
        self.btn_map.setEnabled(has_coords)
        self.btn_adsb.setEnabled(has_coords)

        # Load saved password if available
        saved_pwd = self.storage.get_room_password(contact.node_id) if self.storage else ""
        if saved_pwd:
            self.pwd_input.setText(saved_pwd)
            self.lbl_auth_status.setText("🔑 Saved password loaded")
            self.lbl_auth_status.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600;")
        else:
            self.pwd_input.clear()
            self.lbl_auth_status.setText("🔒 Not logged in")
            self.lbl_auth_status.setStyleSheet("color: #94A3B8; font-size: 11px; font-style: italic;")

        # Highlight in left list
        self.list_widget.blockSignals(True)
        try:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                c = item.data(Qt.ItemDataRole.UserRole)
                if c and c.node_id.lower() == contact.node_id.lower():
                    if self.list_widget.currentItem() != item:
                        self.list_widget.setCurrentItem(item)
                    break
        finally:
            self.list_widget.blockSignals(False)

        # Reload message stream
        self._load_room_messages()

    def _on_room_selection_changed(self):
        sel = self.list_widget.selectedItems()
        if not sel:
            return
        contact = sel[0].data(Qt.ItemDataRole.UserRole)
        if contact and (not self.active_room or self.active_room.node_id.lower() != contact.node_id.lower()):
            self.set_active_room(contact)

    def _toggle_password_echo(self):
        if self.pwd_input.echoMode() == QLineEdit.EchoMode.Password:
            self.pwd_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_echo.setText("🔒")
        else:
            self.pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_echo.setText("👁️")

    def _on_login_clicked(self):
        if not self.active_room:
            return
        pwd = self.pwd_input.text().strip()
        node_id = self.active_room.node_id

        # Save password if requested
        if self.chk_remember.isChecked() and self.storage:
            self.storage.set_room_password(node_id, pwd)
            logger.info(f"Saved password for room server {node_id}")

        self.lbl_auth_status.setText("⏳ Authenticating...")
        self.lbl_auth_status.setStyleSheet("color: #FACC15; font-size: 11px; font-weight: bold;")

        if self.radio_driver and hasattr(self.radio_driver, "send_room_login"):
            self.radio_driver.send_room_login(node_id, pwd)
        elif self.radio_driver and hasattr(self.radio_driver, "send_repeater_command"):
            self.radio_driver.send_repeater_command(node_id, f"!login {pwd}")
        else:
            self.send_message_requested.emit(node_id, f"!login {pwd}")

    def _on_logout_clicked(self):
        if not self.active_room:
            return
        node_id = self.active_room.node_id
        self.lbl_auth_status.setText("⏳ Logging out...")
        self.lbl_auth_status.setStyleSheet("color: #94A3B8; font-size: 11px;")

        if self.radio_driver and hasattr(self.radio_driver, "send_room_logout"):
            self.radio_driver.send_room_logout(node_id)
        elif self.radio_driver and hasattr(self.radio_driver, "send_repeater_command"):
            self.radio_driver.send_repeater_command(node_id, "!logout")
        else:
            self.send_message_requested.emit(node_id, "!logout")

    def _send_command(self, cmd: str):
        if not self.active_room:
            return
        node_id = self.active_room.node_id
        if self.radio_driver and hasattr(self.radio_driver, "send_room_command"):
            self.radio_driver.send_room_command(node_id, cmd)
        elif self.radio_driver and hasattr(self.radio_driver, "send_repeater_command"):
            self.radio_driver.send_repeater_command(node_id, cmd)
        else:
            self.send_message_requested.emit(node_id, cmd)

    def _on_send_message_clicked(self):
        text = self.composer_input.text().strip()
        if not text or not self.active_room:
            return

        node_id = self.active_room.node_id
        self.composer_input.clear()

        # If it's a command, send via room command
        if text.startswith("!"):
            self._send_command(text)
            return

        if self.radio_driver and hasattr(self.radio_driver, "send_room_message"):
            self.radio_driver.send_room_message(node_id, text)
        else:
            self.send_message_requested.emit(node_id, text)

    def _on_locate_on_map_clicked(self):
        if not self.active_room or self.active_room.latitude is None:
            return
        self.show_on_map_requested.emit(
            self.active_room.node_id,
            float(self.active_room.latitude),
            float(self.active_room.longitude),
            self.active_room.alias
        )

    def _on_track_adsb_clicked(self):
        if not self.active_room or self.active_room.latitude is None:
            return
        self.track_adsb_requested.emit(
            self.active_room.node_id,
            float(self.active_room.latitude),
            float(self.active_room.longitude),
            self.active_room.alias
        )

    def _load_room_messages(self):
        """Clears and re-populates the bulletin stream for active room."""
        # Clear existing bubbles
        while self.bubble_layout.count() > 1:
            item = self.bubble_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self.active_room or not self.storage:
            self.lbl_msg_count.setText("0 messages")
            return

        messages = self.storage.get_messages(contact_id=self.active_room.node_id)
        self.lbl_msg_count.setText(f"{len(messages)} messages")

        for msg in messages:
            bubble = MessageBubbleWidget(msg)
            # Insert before the trailing stretch
            self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, bubble)

        if self.chk_autoscroll.isChecked():
            self._scroll_to_bottom()

    def _on_bus_message(self, message: MessageEnvelope):
        """Handles incoming and outgoing messages related to the active room server."""
        if not self.active_room or not message:
            return

        clean_room_id = self.active_room.node_id.lstrip("!@").lower()
        sender_id = (message.sender_id or "").lstrip("!@").lower()
        recip_id = (message.recipient_id or "").lstrip("!@").lower()

        # Check if message is to or from active room server
        if clean_room_id in sender_id or clean_room_id in recip_id or sender_id.startswith(clean_room_id[:6]) or recip_id.startswith(clean_room_id[:6]):
            bubble = MessageBubbleWidget(message)
            self.bubble_layout.insertWidget(self.bubble_layout.count() - 1, bubble)
            current_count = self.bubble_layout.count() - 1
            self.lbl_msg_count.setText(f"{current_count} messages")

            # Check if this was a login or status response
            text = message.text or ""
            if "Logged in successfully" in text or "Welcome to" in text:
                self.lbl_auth_status.setText("🟢 Authenticated")
                self.lbl_auth_status.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
            elif "Login rejected" in text or "Invalid password" in text:
                self.lbl_auth_status.setText("❌ Login Rejected")
                self.lbl_auth_status.setStyleSheet("color: #EF4444; font-size: 11px; font-weight: bold;")
            elif "Logged out" in text:
                self.lbl_auth_status.setText("🔒 Logged out")
                self.lbl_auth_status.setStyleSheet("color: #94A3B8; font-size: 11px;")

            if self.chk_autoscroll.isChecked():
                self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))
