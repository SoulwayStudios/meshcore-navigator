"""Direct Messages & Contacts View Widget for MeshCore Tray."""

from datetime import datetime, timezone
import logging
from typing import Optional, List
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QPushButton, QMenu
)

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact, MessageEnvelope, is_room_server_contact
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.ui.chat_widget import ChatWidget
from meshcore_tray.ui.composer import PowerComposer
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon

logger = logging.getLogger("meshcore_tray.dms_view")


def get_letter_avatar(name: str) -> str:
    """Returns first letter of the name or emoji if available."""
    import html
    clean = html.unescape(name).strip().lstrip("@\"'# ")
    if not clean:
        clean = name.strip()
    if not clean:
        return "?"
    return clean[0].upper()


def format_last_seen(ts: Optional[str]) -> str:
    """Formats last seen timestamp as DD/MM/YY HH:MM in local time."""
    if not ts:
        return "No radio activity yet"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"Last seen: {dt.astimezone().strftime('%d/%m/%y %H:%M')}"
    except Exception:
        return f"Last seen: {str(ts)[:16]}"


class ContactItemWidget(QWidget):
    """Custom row widget for contacts with letter badge, gold star, transparent text, and last seen."""

    def __init__(self, contact: NodeContact, is_favorite: bool = False, is_room: bool = False, parent=None):
        super().__init__(parent)
        self.contact = contact
        self.is_favorite = is_favorite
        self.is_room = is_room
        self.setFixedHeight(52)
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

        # Procedural avatar badge (Style C Cyberpunk Droid for users, Style B Tactical Radar for repeaters)
        self.badge = QLabel()
        self.badge.setFixedSize(36, 36)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        is_rep = False
        if is_room:
            self.badge.setText("🏢")
            self.badge.setStyleSheet("""
                background-color: #5865F2;
                color: #FFFFFF;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
            """)
        else:
            alias_upper = (contact.alias or "").upper()
            role_upper = (getattr(contact, "role", "") or "").upper()
            type_upper = (getattr(contact, "type", "") or "").upper()
            is_rep = bool(
                getattr(contact, "is_repeater", False)
                or any(kw in alias_upper for kw in ("[REP]", "[REPEATER]", "[ROUTER]", "[RTR]", "[GW]", "REPEATER", "ROUTER"))
                or any(kw in role_upper for kw in ("REPEATER", "ROUTER"))
                or any(kw in type_upper for kw in ("REPEATER", "ROUTER"))
            )
            badge_letter = get_letter_avatar(contact.alias or contact.node_id)
            self.badge.setProperty("letter", badge_letter)
            avatar_icon = get_contact_avatar_icon(contact.node_id, contact.alias, is_repeater=is_rep, size=36)
            if avatar_icon and not avatar_icon.isNull():
                self.badge.setPixmap(avatar_icon.pixmap(36, 36))
                self.badge.setStyleSheet("background: transparent; border: none; border-radius: 8px;")
            else:
                self.badge.setText(badge_letter)
                bg_col = self._color_for_name(contact.alias or contact.node_id)
                self.badge.setStyleSheet(f"""
                    background-color: {bg_col};
                    color: #FFFFFF;
                    border-radius: 8px;
                    font-size: 15px;
                    font-weight: bold;
                """)
        layout.addWidget(self.badge)

        # Details
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        raw_name = contact.alias or contact.node_id
        import html
        clean_name = html.unescape(raw_name).replace("[room]", "").replace("[server]", "").strip()

        self.name_lbl = QLabel(clean_name)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.name_lbl.setAutoFillBackground(False)
        self.name_lbl.setStyleSheet("background: transparent; border: none; color: #F2F3F5; font-size: 13px; font-weight: 600;")
        name_row.addWidget(self.name_lbl)

        self.star_lbl = QLabel("★")
        self.star_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.star_lbl.setAutoFillBackground(False)
        self.star_lbl.setStyleSheet("background: transparent; border: none; color: #FFD700; font-size: 13px; font-weight: bold;")
        if not is_favorite:
            self.star_lbl.hide()
        name_row.addWidget(self.star_lbl)

        name_row.addStretch()
        info_layout.addLayout(name_row)

        # Subtitle: Show last seen, NO ID!
        if is_room:
            sub_text = "🏢 Room Server"
        else:
            last_time = getattr(contact, "last_seen", None) or getattr(contact, "last_heard", None)
            sub_text = format_last_seen(last_time)
        self.sub_lbl = QLabel(sub_text)
        self.sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.sub_lbl.setAutoFillBackground(False)
        self.sub_lbl.setStyleSheet("background: transparent; border: none; color: #949BA4; font-size: 11px;")
        info_layout.addWidget(self.sub_lbl)

        layout.addLayout(info_layout, 1)

        # Rich Tooltip with Full Name and Node Details
        role_desc = "🏢 Room Server" if is_room else ("📡 Repeater Node" if is_rep else "👤 User / Client Node")
        hw_model = getattr(contact, "hw_model", "") or getattr(contact, "hardware", "")
        hw_line = f"\n📟 Hardware: {hw_model}" if hw_model else ""
        snr = getattr(contact, "snr_db", 0.0)
        rssi = getattr(contact, "rssi_dbm", -100.0)
        rf_line = f"\n📶 Signal: {rssi:.0f} dBm (SNR {snr:+.1f} dB)" if (snr != 0.0 or rssi != -100.0) else ""
        hops = getattr(contact, "out_path_len", -1)
        hops_line = f"\n🔀 Path: {hops} hop{'s' if hops != 1 else ''}" if hops >= 0 else ""
        lat = getattr(contact, "latitude", None)
        lon = getattr(contact, "longitude", None)
        loc_line = f"\n📍 Location: {lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else ""
        seen_line = f"\n🕒 Last Seen: {last_time}" if (not is_room and last_time) else ""

        tip = (
            f"👤 {raw_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ Role: {role_desc}\n"
            f"🔑 Node ID: {contact.node_id}{hw_line}{rf_line}{hops_line}{loc_line}{seen_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Click to open Direct Message"
        )
        self.setToolTip(tip)

    def _color_for_name(self, name: str) -> str:
        palette = [
            "#5865F2", "#57F287", "#FEE75C", "#EB459E", "#ED4245",
            "#F26522", "#34D399", "#38BDF8", "#A855F7", "#EC4899"
        ]
        val = sum(ord(c) for c in name)
        return palette[val % len(palette)]


class DMsViewWidget(QWidget):
    """Discord Direct Messages Page with Contact list and Direct Chat split."""

    send_dm_requested = pyqtSignal(str, str)  # (recipient_id, text)
    show_on_map_requested = pyqtSignal(str, object, object, str)  # (node_id, lat, lon, alias)
    track_adsb_requested = pyqtSignal(str, float, float, str)  # (node_id, lat, lon, alias)

    def __init__(self, storage=None, config: Optional[AppConfig] = None, radio_driver=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.radio_driver = radio_driver
        self.current_contact: Optional[NodeContact] = None
        self.sort_mode = "alpha"  # "alpha" or "recent"

        self._init_ui()
        self.reload_contacts()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #35363C;
            }
        """)

        # 1. Left Contact List Column
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        # Header Row: Title and Discover / Add button
        header_row = QHBoxLayout()
        header_title = QLabel("DIRECT MESSAGES")
        header_title.setStyleSheet("color: #949BA4; font-size: 11px; font-weight: bold; letter-spacing: 0.8px;")
        header_row.addWidget(header_title)
        header_row.addStretch()

        self.btn_add_contact = QPushButton("➕ Discover / Add")
        self.btn_add_contact.setToolTip("Discover overheard mesh contacts or add a contact manually")
        self.btn_add_contact.setStyleSheet("""
            QPushButton {
                background: #2D3139;
                color: #38BDF8;
                border: 1px solid #414856;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #38BDF8;
                color: #0F172A;
                border-color: #38BDF8;
            }
        """)
        self.btn_add_contact.clicked.connect(self._open_add_contact_dialog)
        header_row.addWidget(self.btn_add_contact)
        left_layout.addLayout(header_row)

        # Unified Search Bar (styled with magnifying glass)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search contacts...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E1F22;
                color: #FFFFFF;
                border: 1px solid #3F4147;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #5865F2;
            }
        """)
        self.search_input.textChanged.connect(self._on_search_filter)
        left_layout.addWidget(self.search_input)

        # Dedicated Sorting Buttons Toolbar
        sort_row = QHBoxLayout()
        sort_row.setSpacing(6)
        lbl_sort = QLabel("Sort:")
        lbl_sort.setStyleSheet("color: #9CA3AF; font-size: 11px; font-weight: bold;")
        sort_row.addWidget(lbl_sort)

        self.btn_sort_alpha = QPushButton("🔤 Name")
        self.btn_sort_alpha.setCheckable(True)
        self.btn_sort_alpha.setChecked(True)
        self.btn_sort_alpha.setToolTip("Sort contacts alphabetically A-Z (Favorites stay at top)")
        self.btn_sort_alpha.clicked.connect(lambda: self._set_sort_mode("alpha"))
        sort_row.addWidget(self.btn_sort_alpha, 1)

        self.btn_sort_recent = QPushButton("🕒 Recent")
        self.btn_sort_recent.setCheckable(True)
        self.btn_sort_recent.setChecked(False)
        self.btn_sort_recent.setToolTip("Sort contacts by most recently heard (Favorites stay at top)")
        self.btn_sort_recent.clicked.connect(lambda: self._set_sort_mode("recent"))
        sort_row.addWidget(self.btn_sort_recent, 1)

        left_layout.addLayout(sort_row)
        self._update_sort_buttons()

        # Contact List
        self.contact_list = QListWidget()
        self.contact_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contact_list.setStyleSheet("""
            QListWidget {
                background-color: #222327;
                border: 1px solid #414143;
                border-radius: 8px;
                padding: 4px;
                outline: none;
            }
            QListWidget QLabel {
                background: transparent;
                border: none;
            }
            QListWidget::item {
                border-radius: 6px;
                margin-bottom: 3px;
                padding: 0px;
                background: transparent;
            }
            QListWidget::item:disabled {
                background: transparent;
            }
            QListWidget::item:hover {
                background-color: #35373C;
            }
            QListWidget::item:selected {
                background-color: #404249;
            }
        """)
        self.contact_list.itemClicked.connect(self._on_contact_item_clicked)
        self.contact_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.contact_list.customContextMenuRequested.connect(self._show_contact_context_menu)
        left_layout.addWidget(self.contact_list, 1)

        left_pane.setMinimumWidth(260)
        left_pane.setMaximumWidth(340)
        self.splitter.addWidget(left_pane)

        # 2. Right Direct Chat Pane (Single clean ChatWidget header, NO duplicate headers)
        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.chat_widget = ChatWidget(storage=self.storage, config=self.config)
        self.chat_widget.resend_requested.connect(self._on_chat_resend_requested)
        right_layout.addWidget(self.chat_widget, 1)

        self.composer = PowerComposer(storage=self.storage, config=self.config)
        self.composer.send_message.connect(self._on_composer_send)
        right_layout.addWidget(self.composer)

        self.splitter.addWidget(right_pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        layout.addWidget(self.splitter)

    def _set_sort_mode(self, mode: str):
        self.sort_mode = mode
        self._update_sort_buttons()
        self.reload_contacts()

    def _update_sort_buttons(self):
        is_alpha = (self.sort_mode == "alpha")
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

    def reload_contacts(self):
        self.contact_list.clear()
        if not self.storage:
            return

        self.contact_list.setUpdatesEnabled(False)
        try:
            all_contacts = self.storage.get_contacts()
            query = self.search_input.text().strip().lower()

            favorites = []
            rooms = []
            directs = []

            for c in all_contacts:
                if query:
                    name_match = c.alias and query in c.alias.lower()
                    id_match = c.node_id and query in c.node_id.lower()
                    if not (name_match or id_match):
                        continue

                is_rep = c.is_repeater or "[rep]" in (c.alias or "").lower()
                if is_rep:
                    continue  # Repeaters live in the Repeaters view

                is_room = getattr(c, "is_room_server", False) or is_room_server_contact(c)
                is_fav = bool(c.is_favorite or (self.config and self.config.is_user_favorite(c.node_id, c.alias or "")))

                if is_fav:
                    favorites.append((c, is_fav, is_room))
                elif is_room:
                    rooms.append((c, is_fav, is_room))
                else:
                    directs.append((c, is_fav, is_room))

            def get_sort_key(item_tuple):
                c = item_tuple[0]
                if self.sort_mode == "recent":
                    ts = getattr(c, "last_seen", None) or getattr(c, "last_heard", None) or ""
                    return str(ts)
                return (c.alias or c.node_id).lower()

            reverse_sort = (self.sort_mode == "recent")
            favorites.sort(key=get_sort_key, reverse=reverse_sort)
            rooms.sort(key=get_sort_key, reverse=reverse_sort)
            directs.sort(key=get_sort_key, reverse=reverse_sort)

            # Add Favorites (no top divider)
            if favorites:
                for c, fav, room in favorites:
                    self._add_contact_row(c, fav, room)

            # Thin divider between favorite users and non-favorite users
            if favorites and (rooms or directs):
                self._add_thin_divider()

            # Add Room Servers
            if rooms:
                for c, fav, room in rooms:
                    self._add_contact_row(c, fav, room)
                if directs:
                    self._add_thin_divider()

            # Add Direct Messages (capped at top 60 when browsing without a search query)
            if directs:
                directs_to_show = directs if query else directs[:60]
                for c, fav, room in directs_to_show:
                    self._add_contact_row(c, fav, room)

                if not query and len(directs) > 60:
                    info_item = QListWidgetItem(f"Showing top 60 recent contacts • Use 🔍 Search for all {len(directs)}")
                    info_item.setFlags(Qt.ItemFlag.NoItemFlags)
                    info_item.setForeground(QColor("#6B7280"))
                    font = info_item.font()
                    font.setPointSize(9)
                    font.setItalic(True)
                    info_item.setFont(font)
                    info_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.contact_list.addItem(info_item)
        finally:
            self.contact_list.setUpdatesEnabled(True)

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
        self.contact_list.addItem(item)
        self.contact_list.setItemWidget(item, container)

    def _add_contact_row(self, contact: NodeContact, is_fav: bool, is_room: bool):
        item = QListWidgetItem()
        widget = ContactItemWidget(contact, is_favorite=is_fav, is_room=is_room)
        item.setSizeHint(QSize(220, 54))
        item.setData(Qt.ItemDataRole.UserRole, contact.node_id)
        self.contact_list.addItem(item)
        self.contact_list.setItemWidget(item, widget)

    def _on_search_filter(self, text: str):
        self.reload_contacts()

    def _on_contact_item_clicked(self, item: QListWidgetItem):
        node_id = item.data(Qt.ItemDataRole.UserRole)
        if not node_id:
            return
        self.select_contact(node_id)

    def select_contact(self, contact_id: str):
        if not self.storage:
            return
        contact = self.storage.get_contact(contact_id)
        if not contact:
            return

        self.current_contact = contact
        self.chat_widget.set_target("Direct", contact_id)
        self.composer.set_active_target("Direct", contact_id)

    def _on_composer_send(self, channel: str, recipient_id: Optional[str], text: str):
        target_id = recipient_id or (self.current_contact.node_id if self.current_contact else None)
        if target_id:
            self.send_dm_requested.emit(target_id, text)

    def _on_chat_resend_requested(self, msg):
        if not msg:
            return
        if hasattr(self, "composer"):
            self.composer.populate_resend(msg.text)
        target_id = getattr(msg, "recipient_id", None) or (self.current_contact.node_id if self.current_contact else None)
        if target_id:
            self.send_dm_requested.emit(target_id, msg.text)

    def _show_contact_context_menu(self, pos):
        item = self.contact_list.itemAt(pos)
        if not item:
            return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        if not node_id or not self.storage:
            return
        contact = self.storage.get_contact(node_id)
        if not contact:
            return

        is_fav = bool(contact and contact.is_favorite) or (self.config and self.config.is_user_favorite(node_id, contact.alias or ""))
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
                background-color: #5865F2;
                color: #FFFFFF;
            }
        """)

        fav_action = menu.addAction("⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites")
        map_action = menu.addAction("🗺️ Show on Map")
        adsb_action = None
        if contact and contact.latitude is not None and contact.longitude is not None:
            adsb_action = menu.addAction("✈️ Track ADS-B Around Contact")
        edit_action = menu.addAction("✏️ Edit Contact / Coordinates")
        menu.addSeparator()
        del_action = menu.addAction("🗑️ Remove Contact")

        action = menu.exec(self.contact_list.mapToGlobal(pos))
        if action == fav_action:
            new_fav = not is_fav
            self.storage.set_contact_favorite(node_id, new_fav)
            if self.config:
                if new_fav:
                    if node_id not in self.config.favorite_users:
                        self.config.favorite_users.append(node_id)
                else:
                    self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != contact.alias]
                self.config.save()
            bus.emit(EventType.FAVORITES_UPDATED, node_id)
            self.reload_contacts()
        elif action == map_action:
            self.show_on_map_requested.emit(node_id, contact.latitude, contact.longitude, contact.alias or node_id)
        elif adsb_action and action == adsb_action:
            self.track_adsb_requested.emit(node_id, float(contact.latitude), float(contact.longitude), contact.alias or node_id)
        elif action == edit_action:
            self._open_edit_contact_dialog(contact)
        elif action == del_action:
            self.storage.delete_contact(node_id)
            if self.config:
                self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != contact.alias]
                self.config.save()
            bus.emit(EventType.MAP_NODES_UPDATED, None)
            self.reload_contacts()

    def _open_add_contact_dialog(self, initial_name: str = ""):
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.radio_driver,
            parent=self.window(),
            initial_name=initial_name
        )
        dlg.exec()
        self.reload_contacts()

    def _open_edit_contact_dialog(self, contact: NodeContact):
        if not contact:
            return
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.radio_driver,
            parent=self.window(),
            initial_contact=contact
        )
        dlg.exec()
        self.reload_contacts()
