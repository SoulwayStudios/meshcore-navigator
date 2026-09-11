"""Channels & Contacts Sidebar with Favorites (⭐), Search, and Resizable Splitter."""

from datetime import datetime
import logging
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFrame, QSplitter, QMenu,
    QLineEdit, QComboBox, QInputDialog, QAbstractItemView
)
from meshcore_tray.core.models import ChannelInfo, NodeContact
from meshcore_tray.core.event_bus import bus, EventType

logger = logging.getLogger("meshcore_tray.sidebar")


class ChannelListWidget(QListWidget):
    """Custom QListWidget supporting intuitive drag-and-drop of channels between category groups."""

    channel_group_dropped = pyqtSignal(str, str)  # (channel_name, target_group_name)
    channel_reordered = pyqtSignal(str, str, str, bool)  # (chan_name, target_group, target_channel, insert_after)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        is_group = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        data_val = str(item.data(Qt.ItemDataRole.UserRole) or "")
        # Prevent dragging group headers
        if is_group or data_val.startswith("__group__:"):
            return
        super().startDrag(supportedActions)

    def dragEnterEvent(self, event):
        if event.source() == self:
            item = self.currentItem()
            if item:
                is_group = bool(item.data(Qt.ItemDataRole.UserRole + 1))
                data_val = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if not is_group and not data_val.startswith("__group__:"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.source() == self:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        if event.source() != self:
            event.ignore()
            return

        source_item = self.currentItem()
        if not source_item:
            event.ignore()
            return

        chan_name = str(source_item.data(Qt.ItemDataRole.UserRole) or "")
        if not chan_name or chan_name.startswith("__group__:"):
            event.ignore()
            return

        # Determine target item at drop coordinates
        drop_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_item = self.itemAt(drop_pos)

        target_group = None
        target_channel = None
        insert_after = False

        if target_item:
            is_group = bool(target_item.data(Qt.ItemDataRole.UserRole + 1))
            t_data = str(target_item.data(Qt.ItemDataRole.UserRole) or "")
            if is_group or t_data.startswith("__group__:"):
                # Dropped directly on a group header!
                target_group = t_data.split(":", 1)[1] if ":" in t_data else t_data
                target_channel = None
                insert_after = False
            else:
                # Dropped onto a channel item.
                target_channel = t_data
                rect = self.visualItemRect(target_item)
                insert_after = drop_pos.y() > (rect.top() + rect.height() // 2)

                # Find which group it belongs to by scanning backwards
                target_row = self.row(target_item)
                for r in range(target_row, -1, -1):
                    prev_item = self.item(r)
                    p_data = str(prev_item.data(Qt.ItemDataRole.UserRole) or "")
                    if bool(prev_item.data(Qt.ItemDataRole.UserRole + 1)) or p_data.startswith("__group__:"):
                        target_group = p_data.split(":", 1)[1] if ":" in p_data else p_data
                        break
        else:
            # Dropped below all items in empty space - pick the last group header in the list
            for r in range(self.count() - 1, -1, -1):
                item = self.item(r)
                i_data = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if bool(item.data(Qt.ItemDataRole.UserRole + 1)) or i_data.startswith("__group__:"):
                    target_group = i_data.split(":", 1)[1] if ":" in i_data else i_data
                    break
            target_channel = None
            insert_after = True

        if target_group and chan_name:
            event.accept()
            self.channel_reordered.emit(chan_name, target_group, target_channel or "", insert_after)
            self.channel_group_dropped.emit(chan_name, target_group)
        else:
            event.ignore()


def make_star_icon(color_hex: str) -> QIcon:
    """Creates a colored star icon."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(color_hex))
    painter.setBrush(QColor(color_hex))
    painter.setFont(QFont("sans-serif", 10, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "★")
    painter.end()
    return QIcon(pixmap)


class Sidebar(QWidget):
    """Channels & Contacts navigation pane with resizable splitter."""

    channel_selected = pyqtSignal(str)
    contact_selected = pyqtSignal(str)
    join_channel_requested = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)

    def __init__(self, storage=None, config=None, driver=None, show_contacts: bool = True, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.driver = driver
        self.show_contacts = show_contacts
        self.active_channel: str = "Public"
        fav_chan_col = self.config.app_colors.favorite_channel_color if (self.config and hasattr(self.config, "app_colors")) else "#FF5555"
        fav_user_col = self.config.app_colors.favorite_user_color if (self.config and hasattr(self.config, "app_colors")) else "#FFD700"
        self._red_star_icon = make_star_icon(fav_chan_col)
        self._yellow_star_icon = make_star_icon(fav_user_col)

        # Restore group & channel ordering from storage if config missing it
        if self.config and self.storage:
            if not getattr(self.config, "channel_groups", None):
                stored_cg = self.storage.get_app_state("channel_groups")
                if stored_cg:
                    try:
                        self.config.channel_groups = json.loads(stored_cg)
                    except Exception:
                        pass
            if not getattr(self.config, "channel_order", None):
                stored_co = self.storage.get_app_state("channel_order")
                if stored_co:
                    try:
                        self.config.channel_order = json.loads(stored_co)
                    except Exception:
                        pass
            if not getattr(self.config, "group_order", None):
                stored_go = self.storage.get_app_state("group_order")
                if stored_go:
                    try:
                        self.config.group_order = json.loads(stored_go)
                    except Exception:
                        pass

        self._init_ui()
        self._subscribe_events()

    def _subscribe_events(self):
        bus.subscribe(EventType.MESSAGE_RECEIVED, lambda _: self.reload())
        bus.subscribe(EventType.READ_STATE_UPDATED, lambda _: self.reload())
        bus.subscribe(EventType.CHANNELS_UPDATED, lambda _: self.reload())
        bus.subscribe(EventType.FAVORITES_UPDATED, lambda _: self.reload())
        bus.subscribe(EventType.SETTINGS_UPDATED, lambda _: self.reload())
        bus.subscribe(EventType.MAP_NODES_UPDATED, lambda _: self.reload())
        bus.subscribe(EventType.NODE_DISCOVERED, lambda _: self.reload())

    def set_active_channel(self, channel_name: str):
        """Sets the currently active channel and updates read/unread styling."""
        self.active_channel = channel_name
        self.reload()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Connection status is now inside the main app icon tooltip, so status pill is omitted here
        self.status_pill = None

        # Resizable Splitter with Grab Handle between Channels and Contacts
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background-color: #414143;
                height: 4px;
                margin: 2px 4px;
                border-radius: 2px;
            }
            QSplitter::handle:vertical:hover {
                background-color: #60687A;
            }
        """)

        # --- Top Section: Channels ---
        chan_container = QWidget()
        chan_layout = QVBoxLayout(chan_container)
        chan_layout.setContentsMargins(0, 0, 0, 0)
        chan_layout.setSpacing(6)

        chan_header = QHBoxLayout()
        chan_lbl = QLabel("<b>CHANNELS</b>")
        chan_lbl.setStyleSheet("color: #9CA3AF; font-size: 11px; letter-spacing: 1px;")
        chan_header.addWidget(chan_lbl)
        chan_header.addStretch()
        chan_layout.addLayout(chan_header)

        self.btn_add_channel = QPushButton("➕ Add Channel")
        self.btn_add_channel.setToolTip("Add or join a channel")
        self.btn_add_channel.setStyleSheet("""
            QPushButton {
                background: #2D3139;
                color: #38BDF8;
                border: 1px solid #414856;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
                text-align: center;
            }
            QPushButton:hover {
                background: #38BDF8;
                color: #0F172A;
                border-color: #38BDF8;
            }
        """)
        self.btn_add_channel.clicked.connect(self._on_add_channel_clicked)
        chan_layout.addWidget(self.btn_add_channel)

        self.channel_list = ChannelListWidget()
        self.channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channel_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.channel_list.itemClicked.connect(self._on_channel_clicked)
        self.channel_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.channel_list.customContextMenuRequested.connect(self._show_channel_context_menu)
        self.channel_list.channel_group_dropped.connect(self._on_channel_drag_dropped)
        self.channel_list.channel_reordered.connect(self._on_channel_reordered)
        chan_layout.addWidget(self.channel_list, 1)

        # --- Bottom Section: Contacts ---
        self.contact_container = QWidget()
        contact_layout = QVBoxLayout(self.contact_container)
        contact_layout.setContentsMargins(0, 0, 0, 0)
        contact_layout.setSpacing(6)

        dm_header = QHBoxLayout()
        dm_lbl = QLabel("<b>CONTACTS</b>")
        dm_lbl.setStyleSheet("color: #9CA3AF; font-size: 11px; letter-spacing: 1px;")
        dm_header.addWidget(dm_lbl)
        dm_header.addStretch()

        self.btn_add_contact = QPushButton("➕ Discover / Add")
        self.btn_add_contact.setToolTip("Discover overheard mesh contacts or add a contact manually")
        self.btn_add_contact.setStyleSheet("""
            QPushButton {
                background: #2D3139;
                color: #38BDF8;
                border: 1px solid #414856;
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #38BDF8;
                color: #0F172A;
                border-color: #38BDF8;
            }
        """)
        self.btn_add_contact.clicked.connect(lambda: self._open_add_contact_dialog())
        dm_header.addWidget(self.btn_add_contact)
        contact_layout.addLayout(dm_header)

        # Searchbox full-width
        self.contact_search = QLineEdit()
        self.contact_search.setPlaceholderText("🔍 Search contacts...")
        self.contact_search.setClearButtonEnabled(True)
        self.contact_search.setStyleSheet("""
            QLineEdit {
                background-color: #1E1F22;
                color: #FFFFFF;
                border: 1px solid #3F4147;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #5865F2;
            }
        """)
        self.contact_search.textChanged.connect(lambda _: self.reload())
        contact_layout.addWidget(self.contact_search)

        # Clear, Dedicated Sorting Buttons Toolbar
        sort_row = QHBoxLayout()
        sort_row.setSpacing(4)
        lbl_sort = QLabel("Sort:")
        lbl_sort.setStyleSheet("color: #9CA3AF; font-size: 11px; font-weight: bold;")
        sort_row.addWidget(lbl_sort)

        self.btn_sort_alpha = QPushButton("🔤 Name")
        self.btn_sort_alpha.setCheckable(True)
        self.btn_sort_alpha.setChecked(True)
        self.btn_sort_alpha.setToolTip("Sort contacts alphabetically A-Z (Favorites stay at top)")
        self.btn_sort_alpha.clicked.connect(lambda: self.contact_sort.setCurrentIndex(0))
        sort_row.addWidget(self.btn_sort_alpha, 1)

        self.btn_sort_recent = QPushButton("🕒 Recent")
        self.btn_sort_recent.setCheckable(True)
        self.btn_sort_recent.setChecked(False)
        self.btn_sort_recent.setToolTip("Sort contacts by most recently heard (Favorites stay at top)")
        self.btn_sort_recent.clicked.connect(lambda: self.contact_sort.setCurrentIndex(1))
        sort_row.addWidget(self.btn_sort_recent, 1)

        # Synced QComboBox for test and programmatic compatibility
        self.contact_sort = QComboBox()
        self.contact_sort.addItem("🔤 Name", "alpha")
        self.contact_sort.addItem("🕒 Recent", "recent")
        self.contact_sort.setVisible(False)
        self.contact_sort.currentIndexChanged.connect(self._on_sort_changed)
        sort_row.addWidget(self.contact_sort)

        contact_layout.addLayout(sort_row)
        self._update_sort_buttons()

        self.contact_list = QListWidget()
        self.contact_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contact_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.contact_list.itemClicked.connect(self._on_contact_clicked)
        self.contact_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.contact_list.customContextMenuRequested.connect(self._show_contact_context_menu)
        contact_layout.addWidget(self.contact_list, 1)

        # Add to splitter and set proportions
        self.splitter.addWidget(chan_container)
        self.splitter.addWidget(self.contact_container)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        if not self.show_contacts:
            self.contact_container.setVisible(False)
            self.splitter.setSizes([1000, 0])
        layout.addWidget(self.splitter, 1)

        self.reload()

    def _on_sort_changed(self, index: int):
        self._update_sort_buttons()
        self.reload()

    def _update_sort_buttons(self):
        is_alpha = self.contact_sort.currentIndex() == 0
        self.btn_sort_alpha.setChecked(is_alpha)
        self.btn_sort_recent.setChecked(not is_alpha)

        active_style = (
            "background-color: #464C5A; color: #FFFFFF; font-weight: bold; "
            "border: 1px solid #60687A; border-radius: 5px; padding: 4px 8px; font-size: 11px;"
        )
        inactive_style = (
            "background-color: #2B2F38; color: #9CA3AF; font-weight: normal; "
            "border: 1px solid #414143; border-radius: 5px; padding: 4px 8px; font-size: 11px;"
        )
        self.btn_sort_alpha.setStyleSheet(active_style if is_alpha else inactive_style)
        self.btn_sort_recent.setStyleSheet(inactive_style if is_alpha else active_style)

    def update_connection_status(self, connected: bool, port: str, mode: str):
        self._last_conn_info = (connected, port, mode)
        if hasattr(self, "status_pill") and self.status_pill:
            if connected:
                if mode == "mock":
                    self.status_pill.setText("🧪 Radio: Simulator Mode")
                    self.status_pill.setStyleSheet("background-color: #1F385C; color: #58A6FF; border-radius: 10px; padding: 4px;")
                else:
                    conn_col = getattr(self.config.app_colors, "radio_connected_color", "#3FB950") if (self.config and hasattr(self.config, "app_colors")) else "#3FB950"
                    self.status_pill.setText(f"🟢 Radio: Connected ({port})")
                    self.status_pill.setStyleSheet(f"background-color: #16241C; color: {conn_col}; border: 1px solid {conn_col}; border-radius: 10px; padding: 4px;")
            else:
                self.status_pill.setText("🔴 Radio: Disconnected")
                self.status_pill.setStyleSheet("background-color: #5C1D24; color: #FF7B72; border-radius: 10px; padding: 4px;")

    def _on_channel_drag_dropped(self, chan_name: str, target_group: str):
        """Handler for dragging a channel into a category group."""
        if self.config:
            self.config.set_channel_group(chan_name, target_group)
            if self.storage:
                try:
                    self.storage.set_app_state("channel_groups", json.dumps(self.config.channel_groups))
                except Exception:
                    pass
        self.reload()

    def _on_channel_reordered(self, chan_name: str, target_group: str, target_channel: str, insert_after: bool):
        """Handler for reordering channels and updating groups simultaneously."""
        if not self.config:
            return
        clean_chan = chan_name.strip().lstrip("#")
        # 1. Update group
        self.config.set_channel_group(clean_chan, target_group)

        # 2. Update channel order
        current_order = [c.strip().lstrip("#") for c in self.config.get_channel_order()]
        if not current_order and self.storage:
            all_chans = self.storage.get_channels()
            current_order = [c.name.strip().lstrip("#") for c in all_chans]

        # Remove clean_chan from current order
        current_order = [c for c in current_order if c.lower() != clean_chan.lower()]

        if target_channel and target_channel.strip():
            clean_target = target_channel.strip().lstrip("#").lower()
            idx = -1
            for i, c in enumerate(current_order):
                if c.lower() == clean_target:
                    idx = i
                    break
            if idx != -1:
                insert_idx = idx + 1 if insert_after else idx
                current_order.insert(insert_idx, clean_chan)
            else:
                current_order.append(clean_chan)
        else:
            # Dropped on group header or empty area
            grp_chans = [c for c in current_order if self.config.get_channel_group(c).lower() == target_group.lower()]
            if grp_chans:
                if insert_after:
                    last_ch = grp_chans[-1]
                    idx = current_order.index(last_ch)
                    current_order.insert(idx + 1, clean_chan)
                else:
                    first_ch = grp_chans[0]
                    idx = current_order.index(first_ch)
                    current_order.insert(idx, clean_chan)
            else:
                current_order.append(clean_chan)

        self.config.set_channel_order(current_order)

        # 3. Synchronize to SQLite app_state
        if self.storage:
            try:
                self.storage.set_app_state("channel_groups", json.dumps(self.config.channel_groups))
                self.storage.set_app_state("channel_order", json.dumps(self.config.channel_order))
                self.storage.set_app_state("group_order", json.dumps(self.config.group_order))
            except Exception as e:
                logger.debug("Could not backup channel order to SQLite: %s", e)

        self.reload()

    def reload(self):
        """Refreshes channels and contacts from storage with grouping, folding, search, and filtering."""
        if hasattr(self, "_last_conn_info") and self._last_conn_info:
            c_conn, c_port, c_mode = self._last_conn_info
            if c_conn and c_mode != "mock":
                conn_col = getattr(self.config.app_colors, "radio_connected_color", "#3FB950") if (self.config and hasattr(self.config, "app_colors")) else "#3FB950"
                if hasattr(self, "status_pill") and self.status_pill:
                    self.status_pill.setStyleSheet(f"background-color: #16241C; color: {conn_col}; border: 1px solid {conn_col}; border-radius: 10px; padding: 4px;")
        fav_chan_col = self.config.app_colors.favorite_channel_color if (self.config and hasattr(self.config, "app_colors")) else "#FF5555"
        fav_user_col = self.config.app_colors.favorite_user_color if (self.config and hasattr(self.config, "app_colors")) else "#FFD700"
        self._red_star_icon = make_star_icon(fav_chan_col)
        self._yellow_star_icon = make_star_icon(fav_user_col)

        # Channels: Grouped, foldable, with unread notifications on folded groups
        self.channel_list.clear()
        channels = self.storage.get_channels() if self.storage else [
            ChannelInfo(0, "Public", False, True)
        ]

        groups_dict: dict[str, list[ChannelInfo]] = {}
        seen_chans = set()
        for ch in channels:
            clean = ch.name.strip().lstrip("#").lower()
            if clean in seen_chans:
                continue
            seen_chans.add(clean)
            grp = self.config.get_channel_group(ch.name) if self.config else "Channels"
            if grp not in groups_dict:
                groups_dict[grp] = []
            groups_dict[grp].append(ch)

        saved_group_order = [g.strip().lower() for g in (self.config.get_group_order() if self.config else [])]
        def _grp_sort_key(g: str):
            gl = g.strip().lower()
            if gl in saved_group_order:
                return (0, saved_group_order.index(gl))
            if gl == "channels":
                return (1, -1)
            return (1, 0, gl)

        group_keys = sorted(groups_dict.keys(), key=_grp_sort_key)

        saved_channel_order = [c.strip().lstrip("#").lower() for c in (self.config.get_channel_order() if self.config else [])]
        def _chan_sort_key(ch: ChannelInfo):
            clean = ch.name.strip().lstrip("#").lower()
            if clean in saved_channel_order:
                return (0, saved_channel_order.index(clean))
            is_fav = bool(ch.is_favorite or (self.config and self.config.is_channel_favorite(ch.name)))
            return (1 if not is_fav else 0, 999999, clean)

        for grp_name in group_keys:
            grp_channels = groups_dict[grp_name]
            grp_unreads = 0
            for ch in grp_channels:
                is_active = bool(self.active_channel and ch.name.lower().lstrip("#") == self.active_channel.lower().lstrip("#"))
                if not is_active and self.storage:
                    grp_unreads += int(self.storage.get_channel_unread_count(ch.name) or self.storage.get_channel_unread_count(ch.name.lstrip("#")) or 0)

            is_collapsed = self.config.is_group_collapsed(grp_name) if self.config else False

            # Group header item
            if is_collapsed:
                unread_badge = f" ({grp_unreads})" if grp_unreads > 0 else ""
                header_text = f"▶  {grp_name.upper()}{unread_badge}"
            else:
                header_text = f"▼  {grp_name.upper()}"

            grp_item = QListWidgetItem(header_text)
            grp_font = grp_item.font()
            grp_font.setBold(True)
            grp_font.setPointSize(10)
            grp_item.setFont(grp_font)
            if is_collapsed and grp_unreads > 0:
                grp_item.setForeground(QColor("#38BDF8"))
                grp_item.setToolTip(f"{grp_name} (Folded - {grp_unreads} unread messages. Click to unfold)")
            else:
                grp_item.setForeground(QColor("#9CA3AF"))
                grp_item.setToolTip(f"Click to {'unfold' if is_collapsed else 'fold'} {grp_name}")

            grp_item.setData(Qt.ItemDataRole.UserRole, f"__group__:{grp_name}")
            grp_item.setData(Qt.ItemDataRole.UserRole + 1, True)
            self.channel_list.addItem(grp_item)

            if not is_collapsed:
                grp_channels_sorted = sorted(grp_channels, key=_chan_sort_key)

                for ch in grp_channels_sorted:
                    is_fav = ch.is_favorite or (self.config and self.config.is_channel_favorite(ch.name))
                    is_active = bool(self.active_channel and ch.name.lower().lstrip("#") == self.active_channel.lower().lstrip("#"))
                    unread = 0 if is_active else (
                        (self.storage.get_channel_unread_count(ch.name) or self.storage.get_channel_unread_count(ch.name.lstrip("#")) or 0)
                        if self.storage else 0
                    )

                    base_name = ch.name if ch.name.startswith("#") else f"#{ch.name}"
                    display_name = f"{base_name} ({unread})" if unread > 0 else base_name

                    if is_fav:
                        item = QListWidgetItem(f"★ {display_name}")
                        item.setForeground(QColor(fav_chan_col))
                        item.setToolTip(f"Favorite Channel: {display_name}" + (f" • {unread} unread" if unread > 0 else ""))
                    else:
                        item = QListWidgetItem(display_name)
                        item.setForeground(QColor("#FFFFFF") if unread > 0 else QColor("#C9D1D9"))
                        item.setToolTip(f"Channel: {display_name}" + (f" • {unread} unread" if unread > 0 else ""))

                    if unread > 0:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)

                    if is_active:
                        item.setSelected(True)

                    item.setData(Qt.ItemDataRole.UserRole, ch.name)
                    item.setData(Qt.ItemDataRole.UserRole + 1, False)
                    self.channel_list.addItem(item)

        # Contacts: Filtered by searchbox, sorted by Alpha or Recent, Favorites pinned to the top!
        if not getattr(self, "show_contacts", True):
            return
        self.contact_list.clear()
        contacts = self.storage.get_contacts() if self.storage else []

        search_query = self.contact_search.text().strip().lower() if hasattr(self, "contact_search") else ""
        sort_mode = self.contact_sort.currentData() if hasattr(self, "contact_sort") else "alpha"

        # 1. Search Filtering
        if search_query:
            contacts = [
                c for c in contacts
                if search_query in c.alias.lower() or search_query in c.node_id.lower()
            ]

        # 2. Timestamp Parser for "Most Recent Heard"
        def parse_ts(ts_str: str) -> float:
            if not ts_str:
                return 0.0
            try:
                clean = ts_str.replace("Z", "+00:00")
                return datetime.fromisoformat(clean).timestamp()
            except Exception:
                try:
                    return datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
                except Exception:
                    return 0.0

        # 3. Sorting: Favorites strictly first, then by Selected Mode
        if sort_mode == "recent":
            contacts_sorted = sorted(
                contacts,
                key=lambda c: (
                    not (c.is_favorite or (self.config and self.config.is_user_favorite(c.node_id, c.alias))),
                    -parse_ts(c.last_seen)
                )
            )
        else:
            contacts_sorted = sorted(
                contacts,
                key=lambda c: (
                    not (c.is_favorite or (self.config and self.config.is_user_favorite(c.node_id, c.alias))),
                    c.alias.lower()
                )
            )

        for c in contacts_sorted:
            is_fav = c.is_favorite or (self.config and self.config.is_user_favorite(c.node_id, c.alias))
            is_rep = bool(c.is_repeater)
            clean_alias = c.alias.lstrip("@")
            icon = "📡" if is_rep else "👤"
            rep_tag = " [R]" if is_rep else ""
            tip_type = "Repeater Node" if is_rep else "Companion Node"
            last_seen_str = f" • Last heard: {c.last_seen[11:16]}" if c.last_seen and len(c.last_seen) >= 16 else ""

            if is_fav:
                item = QListWidgetItem(f"★ {icon} @{clean_alias}{rep_tag}")
                item.setIcon(self._yellow_star_icon)
                item.setForeground(QColor(fav_user_col))
                item.setToolTip(f"Favorite {tip_type}: @{clean_alias} ({c.node_id}){last_seen_str}")
            else:
                item = QListWidgetItem(f"{icon} @{clean_alias}{rep_tag}")
                item.setForeground(QColor("#C9D1D9"))
                item.setToolTip(f"{tip_type}: @{clean_alias} ({c.node_id}){last_seen_str}")

            item.setData(Qt.ItemDataRole.UserRole, c.node_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_rep)
            self.contact_list.addItem(item)

    def _show_channel_context_menu(self, pos: QPoint):
        item = self.channel_list.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #222327; color: #E5E7EB; border: 1px solid #414143; border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 6px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #464C5A; color: #FFFFFF; }
        """)

        if not item:
            add_chan_action = menu.addAction("➕ Add Channel")
            create_group_action = menu.addAction("📁 Create Channel Group...")
            action = menu.exec(self.channel_list.mapToGlobal(pos))
            if action == add_chan_action:
                self._on_add_channel_clicked()
            elif action == create_group_action:
                self._on_create_group_clicked()
            return

        is_group = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        data_val = str(item.data(Qt.ItemDataRole.UserRole) or "")

        if is_group or data_val.startswith("__group__:"):
            grp_name = data_val.split(":", 1)[1] if ":" in data_val else data_val
            is_collapsed = self.config.is_group_collapsed(grp_name) if self.config else False
            fold_action = menu.addAction("📖 Unfold Group" if is_collapsed else "📕 Fold Group")
            mark_read_action = menu.addAction("✓ Mark Group as Read")
            rename_action = None
            del_group_action = None
            if grp_name.lower() != "channels":
                rename_action = menu.addAction("✏️ Rename Group...")
                del_group_action = menu.addAction("🗑️ Delete Group")

            action = menu.exec(self.channel_list.mapToGlobal(pos))
            if action == fold_action:
                if self.config:
                    self.config.toggle_group_collapsed(grp_name)
                self.reload()
            elif action == mark_read_action:
                self._mark_group_as_read(grp_name)
            elif rename_action and action == rename_action:
                self._rename_group_clicked(grp_name)
            elif del_group_action and action == del_group_action:
                self._delete_group_clicked(grp_name)
            return

        chan_name = data_val
        channels = self.storage.get_channels() if self.storage else []
        ch = next((c for c in channels if c.name.lower().lstrip("#") == chan_name.lower().lstrip("#")), None)
        is_fav = bool(ch and ch.is_favorite) or (self.config and self.config.is_channel_favorite(chan_name))
        current_grp = self.config.get_channel_group(chan_name) if self.config else "Channels"

        mark_read_action = menu.addAction("✓ Mark as Read")
        fav_action = menu.addAction("⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites")
        group_action = menu.addAction(f"📁 Move to Group... (Current: {current_grp})")
        leave_action = None
        if chan_name.lower().lstrip("#") != "public":
            leave_action = menu.addAction("🚪 Leave Channel")

        action = menu.exec(self.channel_list.mapToGlobal(pos))
        if action == mark_read_action:
            if self.storage:
                self.storage.mark_channel_as_read(chan_name)
            self.reload()
        elif action == fav_action:
            new_state = not is_fav
            if self.storage:
                self.storage.set_channel_favorite(chan_name, new_state)
            if self.config:
                self.config.set_channel_favorite(chan_name, new_state)
            self.reload()
            bus.emit(EventType.FAVORITES_UPDATED, chan_name)
        elif action == group_action:
            self._on_assign_channel_group_clicked(chan_name, current_grp)
        elif leave_action and action == leave_action:
            if self.storage:
                self.storage.delete_channel(chan_name)
            self.reload()
            self.channel_selected.emit("Public")

    def _show_contact_context_menu(self, pos: QPoint):
        item = self.contact_list.itemAt(pos)
        if not item:
            return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        contact = self.storage.get_contact(node_id) if self.storage else None
        alias = contact.alias if contact else node_id
        is_fav = bool(contact and contact.is_favorite) or (self.config and self.config.is_user_favorite(node_id, alias))

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #222327; color: #E5E7EB; border: 1px solid #414143; border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 6px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #464C5A; color: #FFFFFF; }
        """)

        fav_action = menu.addAction("⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites (Yellow Star)")
        dm_action = menu.addAction("✉️ Send Direct Message")
        edit_action = menu.addAction("✏️ Edit Contact / Coordinates")
        menu.addSeparator()
        del_action = menu.addAction("🗑️ Remove Contact")

        action = menu.exec(self.contact_list.mapToGlobal(pos))
        if action == fav_action:
            new_state = not is_fav
            if self.storage:
                self.storage.set_contact_favorite(node_id, new_state)
            if self.config:
                if new_state:
                    if node_id not in self.config.favorite_users:
                        self.config.favorite_users.append(node_id)
                else:
                    self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != alias]
                self.config.save()
            self.reload()
            bus.emit(EventType.FAVORITES_UPDATED, node_id)
        elif action == dm_action:
            self.contact_selected.emit(node_id)
        elif action == edit_action and contact:
            self._open_edit_contact_dialog(contact)
        elif action == del_action:
            if self.storage:
                self.storage.delete_contact(node_id)
            if self.config:
                self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != alias]
                self.config.save()
            self.reload()

    def _on_channel_clicked(self, item: QListWidgetItem):
        self.contact_list.clearSelection()
        is_group = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        data_val = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if is_group or data_val.startswith("__group__:"):
            grp_name = data_val.split(":", 1)[1] if ":" in data_val else data_val
            if self.config:
                self.config.toggle_group_collapsed(grp_name)
            self.reload()
            return

        chan_name = data_val
        self.channel_selected.emit(chan_name)

    def _on_contact_clicked(self, item: QListWidgetItem):
        self.channel_list.clearSelection()
        contact_id = item.data(Qt.ItemDataRole.UserRole)
        self.contact_selected.emit(contact_id)

    def _on_add_channel_clicked(self):
        name, ok = QInputDialog.getText(
            self, "Add / Join Channel", "Enter channel name (e.g. #operations):",
            QLineEdit.EchoMode.Normal, ""
        )
        if ok and name.strip():
            clean = name.strip().lstrip("#")
            if clean:
                self.join_channel_requested.emit(clean)

    def _on_create_group_clicked(self):
        name, ok = QInputDialog.getText(
            self, "Create Channel Group", "Enter new group name:",
            QLineEdit.EchoMode.Normal, ""
        )
        if ok and name.strip():
            clean = name.strip()
            if self.active_channel and self.config:
                self.config.set_channel_group(self.active_channel, clean)
            self.reload()

    def _on_assign_channel_group_clicked(self, chan_name: str, current_grp: str):
        name, ok = QInputDialog.getText(
            self, "Assign Channel Group", f"Enter group name for #{chan_name} (type 'Channels' or leave blank for default):",
            QLineEdit.EchoMode.Normal, current_grp
        )
        if ok:
            clean = name.strip()
            if self.config:
                self.config.set_channel_group(chan_name, clean)
            self.reload()

    def _mark_group_as_read(self, group_name: str):
        if not self.storage:
            return
        channels = self.storage.get_channels()
        for ch in channels:
            grp = self.config.get_channel_group(ch.name) if self.config else "Channels"
            if grp.lower() == group_name.lower():
                self.storage.mark_channel_as_read(ch.name)
        self.reload()

    def _rename_group_clicked(self, old_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Rename Group", f"Enter new name for group '{old_name}':",
            QLineEdit.EchoMode.Normal, old_name
        )
        if ok and new_name.strip() and self.config:
            clean = new_name.strip()
            for ch, g in list(self.config.channel_groups.items()):
                if g.lower() == old_name.lower():
                    self.config.channel_groups[ch] = clean
            if old_name in self.config.collapsed_channel_groups:
                self.config.collapsed_channel_groups.remove(old_name)
                self.config.collapsed_channel_groups.append(clean)
            self.config.save()
            self.reload()

    def _delete_group_clicked(self, group_name: str):
        if self.config:
            self.config.channel_groups = {
                ch: g for ch, g in self.config.channel_groups.items()
                if g.lower() != group_name.lower()
            }
            self.config.collapsed_channel_groups = [
                g for g in self.config.collapsed_channel_groups
                if g.lower() != group_name.lower()
            ]
            self.config.save()
            self.reload()

    def _open_add_contact_dialog(self, initial_name: str = ""):
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.driver,
            parent=self.window(),
            initial_name=initial_name
        )
        dlg.exec()

    def _open_edit_contact_dialog(self, contact: NodeContact):
        if not contact:
            return
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.driver,
            parent=self.window(),
            initial_contact=contact
        )
        dlg.exec()
        self.reload()

