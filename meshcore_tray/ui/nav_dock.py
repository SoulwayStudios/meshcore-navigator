"""Discord-Style Left Navigation & Map Layer Dock Widget for MeshCore Tray."""

import logging
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QFrame, QMenu, QLabel, QToolTip
)

from meshcore_tray.config import AppConfig

logger = logging.getLogger("meshcore_tray.nav_dock")


class DockButton(QPushButton):
    """Discord-style squircle button with pill indicator on left edge."""

    def __init__(self, text: str = "", tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.is_active = False
        self._update_style()

    def set_active(self, active: bool):
        self.is_active = active
        self._update_style()

    def _update_style(self):
        if self.is_active:
            self.setStyleSheet("""
                DockButton {
                    background-color: #5865F2;
                    color: #FFFFFF;
                    border-radius: 14px;
                    border: none;
                    font-size: 18px;
                    font-weight: bold;
                }
            """)
        else:
            self.setStyleSheet("""
                DockButton {
                    background-color: #2B2D31;
                    color: #DBDEE1;
                    border-radius: 22px;
                    border: none;
                    font-size: 18px;
                }
                DockButton:hover {
                    background-color: #5865F2;
                    color: #FFFFFF;
                    border-radius: 14px;
                }
            """)


class LayerButton(QPushButton):
    """Compact toggle button for map layers in the left dock."""

    def __init__(self, text: str = "", tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(tooltip)
        self.toggled.connect(self._on_toggled)
        self._update_style(False)

    def _on_toggled(self, checked: bool):
        self._update_style(checked)

    def _update_style(self, checked: bool):
        if checked:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #1E3A5F;
                    color: #38BDF8;
                    border: 1.5px solid #38BDF8;
                    border-radius: 14px;
                    font-size: 18px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #254B7A;
                    border-color: #7DD3FC;
                    color: #FFFFFF;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #24262B;
                    color: #949BA4;
                    border: 1px solid #33363E;
                    border-radius: 14px;
                    font-size: 18px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #2F3239;
                    color: #FFFFFF;
                    border-color: #4E5058;
                }
            """)


class CycleFilterButton(QPushButton):
    """4-state cycle button for filtering nodes (ALL, CLIENTS, REPEATERS, ROOMS)."""

    filter_cycled = pyqtSignal(str)  # ("ALL", "CLIENTS", "REPEATERS", "ROOMS")

    MODES = [
        ("ALL", "🌐", "Node Type"),
        ("CLIENTS", "📱", "Node Type"),
        ("REPEATERS", "📡", "Node Type"),
        ("ROOMS", "🏢", "Node Type")
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.current_idx = 0
        self.clicked.connect(self._on_clicked)
        self._update_display()

    def _on_clicked(self):
        self.current_idx = (self.current_idx + 1) % len(self.MODES)
        self._update_display()
        self.filter_cycled.emit(self.MODES[self.current_idx][0])

    def set_mode(self, mode_name: str):
        for idx, (m, _, _) in enumerate(self.MODES):
            if m == mode_name:
                self.current_idx = idx
                self._update_display()
                break

    def _update_display(self):
        mode, icon, tip = self.MODES[self.current_idx]
        self.setText(icon)
        self.setToolTip(tip)
        self.setStyleSheet("""
            QPushButton {
                background-color: #24262B;
                color: #FACC15;
                border: 1.5px solid #CA8A04;
                border-radius: 14px;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #3A321A;
                border-color: #FACC15;
                color: #FFFFFF;
            }
        """)


class NavDockWidget(QWidget):
    """Vertical Discord-style navigation & layer control strip (60px wide)."""

    view_changed = pyqtSignal(str)          # "main", "dms", "repeaters"
    settings_requested = pyqtSignal()
    broadcast_advert_requested = pyqtSignal(bool) # flood: True/False
    resync_requested = pyqtSignal()
    node_filter_changed = pyqtSignal(str)   # "ALL", "CLIENTS", "REPEATERS", "ROOMS"
    layer_toggled = pyqtSignal(str, bool)   # (layer_key, is_checked)
    radio_connect_requested = pyqtSignal()  # Trigger radio connect / reconnect


    def __init__(self, config: Optional[AppConfig] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.setFixedWidth(60)
        self.setObjectName("navDock")
        self.active_view = "main"

        # Cached hardware/node info for rich tooltip
        self.radio_connected = False
        self.radio_port = ""
        self.radio_mode = "serial"
        self.companion_alias = "Companion Node"
        self.sync_message = "Synchronized with Device"
        self.sync_stage = "idle"

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 14)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # 1. Top App Icon (MeshCore Hexagon/Radio)
        self.app_icon_btn = QPushButton("⚡")
        self.app_icon_btn.setFixedSize(44, 44)
        self.app_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.app_icon_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.app_icon_btn.customContextMenuRequested.connect(self._show_advert_menu)
        self.app_icon_btn.clicked.connect(self.settings_requested.emit)
        self._update_app_icon_style()
        self._update_app_icon_tooltip()
        layout.addWidget(self.app_icon_btn)

        # Thin Discord Divider
        layout.addWidget(self._create_divider())

        # 2. View 1: Main Chat & Map
        self.btn_main = DockButton("💬", "Main Chat & Mesh Map (Channels, Chat, Map)")
        self.btn_main.clicked.connect(lambda: self.switch_view("main"))
        layout.addWidget(self.btn_main)

        # 3. View 2: Heard RF Floods (Replaces chat with live packet stream)
        self.btn_floods = DockButton("🌊", "Heard RF Floods (Replaces chat with live & historical packet streams)")
        self.btn_floods.clicked.connect(lambda: self.switch_view("floods"))
        layout.addWidget(self.btn_floods)

        # 4. View 3: Direct Contacts & DMs
        self.btn_dms = DockButton("👥", "Direct Messages & Contacts (Favorites, Room Servers, DMs)")
        self.btn_dms.clicked.connect(lambda: self.switch_view("dms"))
        layout.addWidget(self.btn_dms)

        # 5. View 4: Repeaters & Infrastructure
        self.btn_repeaters = DockButton("📡", "Repeaters & Infrastructure (Terminal, Credentials, Neighbors)")
        self.btn_repeaters.clicked.connect(lambda: self.switch_view("repeaters"))
        layout.addWidget(self.btn_repeaters)

        # Thin Discord Divider
        layout.addWidget(self._create_divider())

        # Map Overlays (9 items in exact requested order)
        # 1. Node Type Cycle Filter
        self.btn_cycle_filter = CycleFilterButton()
        self.btn_cycle_filter.filter_cycled.connect(self.node_filter_changed.emit)
        layout.addWidget(self.btn_cycle_filter)

        # 2. Observer Traffic
        self.btn_rf_links = LayerButton("⚡", "Observer Traffic")
        self.btn_rf_links.setChecked(True)
        self.btn_rf_links.toggled.connect(lambda ch: self.layer_toggled.emit("rf_links", ch))
        layout.addWidget(self.btn_rf_links)

        # 3. Path Modes
        show_pm = getattr(self.config.meshcore, "map_show_path_modes", False) if self.config else False
        self.btn_byte_paths = LayerButton("🛣️", "Path Modes")
        self.btn_byte_paths.setChecked(show_pm)
        self.btn_byte_paths.toggled.connect(lambda ch: self.layer_toggled.emit("byte_paths", ch))
        layout.addWidget(self.btn_byte_paths)

        # 4. Node Activity Heatmap
        self.btn_heatmap = LayerButton("🔥", "Node Activity Heatmap")
        self.btn_heatmap.setChecked(False)
        self.btn_heatmap.toggled.connect(lambda ch: self.layer_toggled.emit("activity_heatmap", ch))
        layout.addWidget(self.btn_heatmap)

        # 5. Orbital View
        show_orb = getattr(self.config.meshcore, "map_show_companion_orbitals", False) if self.config else False
        self.btn_orbitals = LayerButton("🛰️", "Orbital View")
        self.btn_orbitals.setChecked(show_orb)
        self.btn_orbitals.toggled.connect(lambda ch: self.layer_toggled.emit("orbitals", ch))
        layout.addWidget(self.btn_orbitals)

        # 6. Tropo Forecast View
        self.btn_tropo = LayerButton("📶", "Tropo Forecast View")
        self.btn_tropo.setChecked(False)
        self.btn_tropo.toggled.connect(lambda ch: self.layer_toggled.emit("tropo", ch))
        layout.addWidget(self.btn_tropo)

        # 7. Thunderstorm View
        self.btn_thunderstorm = LayerButton("⛈️", "Thunderstorm View")
        self.btn_thunderstorm.setChecked(False)
        self.btn_thunderstorm.toggled.connect(lambda ch: self.layer_toggled.emit("thunderstorm", ch))
        layout.addWidget(self.btn_thunderstorm)

        # 8. ADSB View
        show_adsb = getattr(self.config.meshcore, "map_show_adsb", False) if self.config else False
        self.btn_adsb = LayerButton("✈️", "ADSB View")
        self.btn_adsb.setChecked(show_adsb)
        self.btn_adsb.toggled.connect(lambda ch: self.layer_toggled.emit("adsb", ch))
        layout.addWidget(self.btn_adsb)

        # 9. Scope View
        show_scopes = getattr(self.config.meshcore, "map_show_scopes", False) if self.config else False
        self.btn_scopes = LayerButton("🌐", "Scope View")
        self.btn_scopes.setChecked(show_scopes)
        self.btn_scopes.toggled.connect(lambda ch: self.layer_toggled.emit("scopes", ch))
        layout.addWidget(self.btn_scopes)

        # 10. RF Line of Sight & Topo Elevation View
        show_los = getattr(self.config.meshcore, "map_show_rf_los", False) if self.config else False
        self.btn_rf_los = LayerButton("🏔️", "Line of Sight & Topo Elevation")
        self.btn_rf_los.setChecked(show_los)
        self.btn_rf_los.toggled.connect(lambda ch: self.layer_toggled.emit("rf_los", ch))
        layout.addWidget(self.btn_rf_los)

        layout.addStretch()

        # Set default active button
        self.btn_main.set_active(True)

    def _create_divider(self) -> QFrame:
        line = QFrame()
        line.setFixedSize(28, 2)
        line.setStyleSheet("background-color: #35363C; border-radius: 1px;")
        return line

    def _update_app_icon_style(self):
        border_col = "#3BA55D" if self.radio_connected else "#ED4245"
        self.app_icon_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2B303C, stop:1 #1C1C1E);
                color: #FFFFFF;
                border: 2px solid {border_col};
                border-radius: 22px;
                font-size: 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border-radius: 14px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #23A55A, stop:1 #14753C);
                border-color: #57F287;
            }}
        """)

    def _update_app_icon_tooltip(self):
        stat_emoji = "🟢" if self.radio_connected else "🔴"
        stat_text = "Radio Connected" if self.radio_connected else "Radio Disconnected"
        port_text = f"{self.radio_port} ({self.radio_mode})" if self.radio_port else "None"
        action_hint = "Right-click to Reconnect / Actions" if self.radio_connected else "Right-click to Connect"
        tip = (
            f"⚡ MeshCore\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{stat_emoji} Status: {stat_text}\n"
            f"📡 Companion: {self.companion_alias}\n"
            f"🔌 Port: {port_text}\n"
            f"🔄 Sync: {self.sync_message}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️ Click to Open Settings\n"
            f"🖱️ {action_hint}"
        )
        self.app_icon_btn.setToolTip(tip)

    def update_connection_status(self, connected: bool, port: str = "", mode: str = "serial"):
        self.radio_connected = connected
        self.radio_port = port
        self.radio_mode = mode
        self._update_app_icon_style()
        self._update_app_icon_tooltip()

    def update_companion_alias(self, alias: str):
        self.companion_alias = alias
        self._update_app_icon_tooltip()

    def update_sync_status(self, stage: str, message: str, is_synced: bool):
        self.sync_stage = stage
        self.sync_message = message
        self._update_app_icon_tooltip()

    def switch_view(self, view_name: str):
        if view_name == self.active_view:
            return
        self.active_view = view_name
        self.btn_main.set_active(view_name == "main")
        if hasattr(self, "btn_floods"):
            self.btn_floods.set_active(view_name == "floods")
        self.btn_dms.set_active(view_name == "dms")
        self.btn_repeaters.set_active(view_name == "repeaters")
        self.view_changed.emit(view_name)

    def set_layer_active(self, layer_key: str, is_active: bool):
        """Programmatically syncs dock layer toggle button states without re-emitting signals."""
        btn = None
        if layer_key == "rf_links":
            btn = self.btn_rf_links
        elif layer_key == "byte_paths":
            btn = self.btn_byte_paths
        elif layer_key == "orbitals":
            btn = self.btn_orbitals
        elif layer_key == "scopes":
            btn = self.btn_scopes
        elif layer_key == "tropo":
            btn = self.btn_tropo
        elif layer_key == "adsb":
            btn = self.btn_adsb
        elif layer_key == "activity_heatmap":
            btn = self.btn_heatmap
        elif layer_key == "thunderstorm":
            btn = self.btn_thunderstorm

        if btn:
            btn.blockSignals(True)
            btn.setChecked(bool(is_active))
            btn.blockSignals(False)

    def _show_advert_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #222327;
                color: #E5E7EB;
                border: 1px solid #414143;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #2B303C;
                color: #38BDF8;
            }
            QMenu::item:disabled {
                color: #6B7280;
            }
        """)

        # Radio Status header
        status_icon = "🟢" if self.radio_connected else "🔴"
        status_text = "Radio: Connected" if self.radio_connected else "Radio: Disconnected"
        port_info = f" ({self.radio_port})" if self.radio_port else ""
        act_status = menu.addAction(f"{status_icon} {status_text}{port_info}")
        act_status.setEnabled(False)

        menu.addSeparator()

        if not self.radio_connected:
            act_connect = menu.addAction("🔌 Connect to Radio")
        else:
            act_connect = menu.addAction("🔄 Reconnect Radio")

        menu.addSeparator()
        act_zero = menu.addAction("📡 Broadcast Node (Advert Zero Hop)")
        act_flood = menu.addAction("🌊 Broadcast Node (Advert Flood Routed)")
        act_resync = menu.addAction("🔄 Re-sync Hardware State")
        menu.addSeparator()
        act_settings = menu.addAction("⚙️ Open Settings...")

        action = menu.exec(self.app_icon_btn.mapToGlobal(QPoint(self.app_icon_btn.width() + 4, 0)))
        if action == act_connect:
            self.radio_connect_requested.emit()
        elif action == act_zero:
            self.broadcast_advert_requested.emit(False)
        elif action == act_flood:
            self.broadcast_advert_requested.emit(True)
        elif action == act_resync:
            self.resync_requested.emit()
        elif action == act_settings:
            self.settings_requested.emit()

