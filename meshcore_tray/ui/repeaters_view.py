"""Repeaters & Infrastructure View Widget for MeshCore Tray."""

from datetime import datetime, timezone
import logging
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QPushButton, QMenu
)

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon

logger = logging.getLogger("meshcore_tray.repeaters_view")


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


class RepeaterRowWidget(QWidget):
    """Row widget for repeaters with status indicator, clean last seen (no raw ID), gold star, and transparent text."""

    def __init__(self, contact: NodeContact, is_favorite: bool = False, is_phantom: bool = False, parent=None):
        super().__init__(parent)
        self.contact = contact
        self.is_favorite = is_favorite
        self.is_phantom = is_phantom
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

        # Repeater badge: Style B Tactical Radar Constellation avatar (or black if phantom)
        self.badge = QLabel()
        self.badge.setFixedSize(36, 36)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self.is_phantom:
            self.badge.setText("👻")
            self.badge.setStyleSheet("""
                background-color: #000000;
                color: #9CA3AF;
                border: 1.5px solid #4B5563;
                border-radius: 8px;
                font-size: 16px;
            """)
        else:
            avatar_icon = get_contact_avatar_icon(contact.node_id, contact.alias, is_repeater=True, size=36)
            if avatar_icon and not avatar_icon.isNull():
                self.badge.setPixmap(avatar_icon.pixmap(36, 36))
                self.badge.setStyleSheet("background: transparent; border: none; border-radius: 8px;")
            else:
                self.badge.setText("📡")
                self.badge.setStyleSheet("""
                    background-color: #FFA500;
                    color: #000000;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: bold;
                """)
        badge = self.badge
        layout.addWidget(badge)

        # Info
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(4)
        clean_name = contact.alias.replace("[Rep]", "").replace("[rep]", "").strip()
        import html
        clean_name = html.unescape(clean_name)

        self.name_lbl = QLabel(clean_name)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.name_lbl.setAutoFillBackground(False)
        self.name_lbl.setStyleSheet("background: transparent; border: none; color: #F2F3F5; font-size: 13px; font-weight: 600;")
        name_row.addWidget(self.name_lbl)

        if self.is_phantom:
            self.phantom_lbl = QLabel("👻")
            self.phantom_lbl.setToolTip("Marked as Phantom Node")
            self.phantom_lbl.setStyleSheet("color: #9CA3AF; font-size: 12px;")
            name_row.addWidget(self.phantom_lbl)

        self.star_lbl = QLabel("★")
        self.star_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.star_lbl.setAutoFillBackground(False)
        self.star_lbl.setStyleSheet("background: transparent; border: none; color: #FFD700; font-size: 13px; font-weight: bold;")
        if not is_favorite:
            self.star_lbl.hide()
        name_row.addWidget(self.star_lbl)

        name_row.addStretch()
        info_layout.addLayout(name_row)

        # Subtitle: Last seen & SNR, NO ID!
        last_time = getattr(contact, "last_seen", None) or getattr(contact, "last_heard", None)
        sub_text = format_last_seen(last_time)
        if getattr(contact, "snr_db", None) is not None:
            sub_text += f" • SNR: {contact.snr_db:+.1f}dB"
        self.sub_lbl = QLabel(sub_text)
        self.sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.sub_lbl.setAutoFillBackground(False)
        self.sub_lbl.setStyleSheet("background: transparent; border: none; color: #949BA4; font-size: 11px;")
        info_layout.addWidget(self.sub_lbl)

        layout.addLayout(info_layout, 1)

        # Rich Tooltip with Full Repeater Name and Node Details
        hw_model = getattr(contact, "hw_model", "") or getattr(contact, "hardware", "")
        hw_line = f"\n📟 Hardware: {hw_model}" if hw_model else ""
        snr = getattr(contact, "snr_db", None)
        rssi = getattr(contact, "rssi_dbm", None)
        rf_line = ""
        if snr is not None and rssi is not None:
            rf_line = f"\n📶 Signal: {rssi:.0f} dBm (SNR {snr:+.1f} dB)"
        elif snr is not None:
            rf_line = f"\n📶 SNR: {snr:+.1f} dB"
        lat = getattr(contact, "latitude", None)
        lon = getattr(contact, "longitude", None)
        loc_line = f"\n📍 Location: {lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else ""
        seen_line = f"\n🕒 Last Seen: {last_time}" if last_time else ""

        tip = (
            f"📡 Repeater: {contact.alias or contact.node_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ Role: 📡 Repeater Node\n"
            f"🔑 Node ID: {contact.node_id}{hw_line}{rf_line}{loc_line}{seen_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ Click to open Repeater Console"
        )
        self.setToolTip(tip)


class RepeatersViewWidget(QWidget):
    """Repeaters View with Repeaters list and Repeater Console."""

    show_neighbors_on_map_requested = pyqtSignal(object, list)  # (contact, neighbors)
    show_on_map_requested = pyqtSignal(str, object, object, str)  # (node_id, lat, lon, alias)
    track_adsb_requested = pyqtSignal(str, float, float, str)  # (node_id, lat, lon, alias)
    send_command_requested = pyqtSignal(str, str)  # (repeater_id, command)
    switch_to_chat_requested = pyqtSignal()

    def __init__(self, storage=None, config: Optional[AppConfig] = None, radio_driver=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.radio_driver = radio_driver
        self.sort_mode = "alpha"  # "alpha" or "recent"

        self._init_ui()
        self.reload_repeaters()

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

        # 1. Left Repeater List Column
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        # Header Row: Title and Discover / Add button
        header_row = QHBoxLayout()
        header_title = QLabel("REPEATERS")
        header_title.setStyleSheet("color: #949BA4; font-size: 11px; font-weight: bold; letter-spacing: 0.8px;")
        header_row.addWidget(header_title)
        header_row.addStretch()

        self.btn_add_repeater = QPushButton("➕ Discover / Add")
        self.btn_add_repeater.setToolTip("Discover overheard repeaters or add a repeater manually")
        self.btn_add_repeater.setStyleSheet("""
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
        self.btn_add_repeater.clicked.connect(self._open_add_repeater_dialog)
        header_row.addWidget(self.btn_add_repeater)
        left_layout.addLayout(header_row)

        # Unified Search Bar (styled with magnifying glass)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search repeaters...")
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
        self.btn_sort_alpha.setToolTip("Sort repeaters alphabetically A-Z (Favorites stay at top)")
        self.btn_sort_alpha.clicked.connect(lambda: self._set_sort_mode("alpha"))
        sort_row.addWidget(self.btn_sort_alpha, 1)

        self.btn_sort_recent = QPushButton("🕒 Recent")
        self.btn_sort_recent.setCheckable(True)
        self.btn_sort_recent.setChecked(False)
        self.btn_sort_recent.setToolTip("Sort repeaters by most recently heard (Favorites stay at top)")
        self.btn_sort_recent.clicked.connect(lambda: self._set_sort_mode("recent"))
        sort_row.addWidget(self.btn_sort_recent, 1)

        left_layout.addLayout(sort_row)
        self._update_sort_buttons()

        # Repeater List
        self.repeater_list = QListWidget()
        self.repeater_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.repeater_list.setStyleSheet("""
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
        self.repeater_list.itemClicked.connect(self._on_repeater_clicked)
        self.repeater_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.repeater_list.customContextMenuRequested.connect(self._show_repeater_context_menu)
        left_layout.addWidget(self.repeater_list, 1)

        left_pane.setMinimumWidth(260)
        left_pane.setMaximumWidth(340)
        self.splitter.addWidget(left_pane)

        # 2. Right Repeater Console Pane
        self.console = RepeaterConsoleWidget(radio_driver=self.radio_driver, storage=self.storage)
        self.console.send_command_requested.connect(self.send_command_requested.emit)
        self.console.show_neighbors_on_map_requested.connect(self.show_neighbors_on_map_requested.emit)
        self.console.back_to_chat_requested.connect(self.switch_to_chat_requested.emit)

        self.splitter.addWidget(self.console)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        layout.addWidget(self.splitter)

    def _set_sort_mode(self, mode: str):
        self.sort_mode = mode
        self._update_sort_buttons()
        self.reload_repeaters()

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

    def reload_repeaters(self):
        self.repeater_list.clear()
        if not self.storage:
            return

        self.repeater_list.setUpdatesEnabled(False)
        try:
            all_contacts = self.storage.get_contacts()
            query = self.search_input.text().strip().lower()

            favorites = []
            others = []

            for c in all_contacts:
                is_rep = c.is_repeater or "[rep]" in (c.alias or "").lower()
                if not is_rep:
                    continue

                if query:
                    name_match = c.alias and query in c.alias.lower()
                    id_match = c.node_id and query in c.node_id.lower()
                    if not (name_match or id_match):
                        continue

                is_fav = bool(c.is_favorite or (self.config and self.config.is_user_favorite(c.node_id, c.alias or "")))
                if is_fav:
                    favorites.append((c, is_fav))
                else:
                    others.append((c, is_fav))

            def get_sort_key(item_tuple):
                c = item_tuple[0]
                if self.sort_mode == "recent":
                    ts = getattr(c, "last_seen", None) or getattr(c, "last_heard", None) or ""
                    return str(ts)
                return (c.alias or c.node_id).lower()

            reverse_sort = (self.sort_mode == "recent")
            favorites.sort(key=get_sort_key, reverse=reverse_sort)
            others.sort(key=get_sort_key, reverse=reverse_sort)

            # Add Favorites (no top divider)
            if favorites:
                for c, fav in favorites:
                    self._add_row(c, fav)

            # Thin divider between favorites and other repeaters
            if favorites and others:
                self._add_thin_divider()

            if others:
                for c, fav in others:
                    self._add_row(c, fav)
        finally:
            self.repeater_list.setUpdatesEnabled(True)

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
        self.repeater_list.addItem(item)
        self.repeater_list.setItemWidget(item, container)

    def _add_row(self, contact: NodeContact, is_fav: bool):
        item = QListWidgetItem()
        is_phantom = bool(self.storage and self.storage.is_phantom_node(contact.node_id, contact.alias))
        widget = RepeaterRowWidget(contact, is_favorite=is_fav, is_phantom=is_phantom)
        item.setSizeHint(QSize(220, 54))
        item.setData(Qt.ItemDataRole.UserRole, contact.node_id)
        self.repeater_list.addItem(item)
        self.repeater_list.setItemWidget(item, widget)

    def _on_search_filter(self, text: str):
        self.reload_repeaters()

    def _on_repeater_clicked(self, item: QListWidgetItem):
        node_id = item.data(Qt.ItemDataRole.UserRole)
        if not node_id or not self.storage:
            return
        contact = self.storage.get_contact(node_id)
        if contact:
            self.console.set_repeater(contact)

    def set_active_repeater(self, contact: NodeContact):
        self.console.set_repeater(contact)

    def _toggle_phantom_node(self, node_id: str, alias: str = ""):
        """Toggles phantom node status for a repeater across storage and config, then refreshes list."""
        is_phantom = bool(self.storage and self.storage.is_phantom_node(node_id, alias))
        if is_phantom:
            if self.storage:
                self.storage.unmark_phantom_node(node_id)
                if alias:
                    self.storage.unmark_phantom_node(alias)
            if self.config:
                self.config.unmark_phantom_node(node_id)
                if alias:
                    self.config.unmark_phantom_node(alias)
        else:
            if self.storage:
                self.storage.mark_phantom_node(node_id, alias)
            if self.config:
                self.config.mark_phantom_node(node_id)
                if alias:
                    self.config.mark_phantom_node(alias)
        bus.emit(EventType.MAP_NODES_UPDATED, None)
        self.reload_repeaters()

    def _show_repeater_context_menu(self, pos):
        item = self.repeater_list.itemAt(pos)
        if not item:
            return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        if not node_id or not self.storage:
            return
        contact = self.storage.get_contact(node_id)
        if not contact:
            return

        is_fav = bool(contact and contact.is_favorite) or (self.config and self.config.is_user_favorite(node_id, contact.alias or ""))
        is_phantom = bool(self.storage and self.storage.is_phantom_node(node_id, contact.alias))
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
        phantom_action = menu.addAction("👻 Remove from Phantom Nodes" if is_phantom else "👻 Mark as Phantom Node")
        map_action = menu.addAction("🗺️ Show on Map")
        adsb_action = None
        if contact and contact.latitude is not None and contact.longitude is not None:
            adsb_action = menu.addAction("✈️ Track ADS-B Around Repeater")
        edit_action = menu.addAction("✏️ Edit Repeater / Coordinates")
        menu.addSeparator()
        del_action = menu.addAction("🗑️ Remove Repeater")

        action = menu.exec(self.repeater_list.mapToGlobal(pos))
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
            self.reload_repeaters()
        elif action == phantom_action:
            self._toggle_phantom_node(node_id, contact.alias or "")
        elif action == map_action:
            self.show_on_map_requested.emit(node_id, contact.latitude, contact.longitude, contact.alias or node_id)
        elif adsb_action and action == adsb_action:
            self.track_adsb_requested.emit(node_id, float(contact.latitude), float(contact.longitude), contact.alias or node_id)
        elif action == edit_action:
            self._open_edit_repeater_dialog(contact)
        elif action == del_action:
            self.storage.delete_contact(node_id)
            if self.config:
                self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != contact.alias]
                self.config.save()
            bus.emit(EventType.MAP_NODES_UPDATED, None)
            self.reload_repeaters()

    def _open_edit_repeater_dialog(self, contact: NodeContact):
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
        self.reload_repeaters()

    def _open_add_repeater_dialog(self, initial_name: str = ""):
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.radio_driver,
            parent=self.window(),
            initial_name=initial_name
        )
        dlg.exec()
        self.reload_repeaters()
