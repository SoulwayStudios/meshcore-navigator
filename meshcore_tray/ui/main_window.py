"""Main Modal Window for MeshCore Pixoo System Tray with Discord-Inspired Architecture."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QPoint, QTimer, QUrl
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QFrame, QStackedWidget, QMenu, QApplication,
    QSystemTrayIcon
)

from meshcore_tray import __app_name__, __version__
from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope, ChannelInfo, NodeContact, is_room_server_contact
from meshcore_tray.ui.chat_widget import ChatWidget
from meshcore_tray.ui.composer import PowerComposer
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget
from meshcore_tray.ui.pixoo_preview import PixooPreviewWidget
from meshcore_tray.ui.settings_widget import SettingsWidget, SettingsDialog
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.dms_view import DMsViewWidget
from meshcore_tray.ui.room_servers_view import RoomServersViewWidget
from meshcore_tray.ui.repeaters_view import RepeatersViewWidget
from meshcore_tray.ui.satellites_view import SatellitesViewWidget
from meshcore_tray.ui.heard_floods_view import HeardFloodsWidget
from meshcore_tray.ui.splash_overlay import SplashOverlay
from meshcore_tray.ui.avatar_generator import set_global_avatar_style
from meshcore_tray.core.version_checker import VersionChecker, ReleaseInfo

logger = logging.getLogger("meshcore_tray.main_window")


class MainWindow(QMainWindow):
    """Primary application window featuring Left Navigation Dock, Main Chat & Map, DMs, and Repeaters Views."""

    def __init__(self, config: AppConfig, storage=None, radio_driver=None, pixoo_service=None, gateway=None, mqtt_service=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.storage = storage
        self.radio_driver = radio_driver
        self.pixoo_service = pixoo_service
        self.gateway = gateway
        self.mqtt_service = mqtt_service
        self._tray_icon = None
        self._is_shutting_down = False

        set_global_avatar_style(getattr(self.config, "user_avatar_style", "droid"))
        self._shutdown_completed = False
        self._is_cleaned_up = False
        self._force_close = False

        # Restore last active channel from storage or config
        last_ch = "Public"
        if self.storage:
            saved_ch = self.storage.get_app_state("last_active_channel")
            if saved_ch:
                last_ch = saved_ch
        if last_ch == "Public" and self.config and hasattr(self.config, "last_active_channel") and self.config.last_active_channel:
            last_ch = self.config.last_active_channel
        self.current_channel = last_ch
        self.current_dm: Optional[str] = None
        self._previous_view_index = 0

        self.setWindowTitle(f"{__app_name__} v{__version__}")
        icon_path = Path(__file__).parent / "static" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        win_w = getattr(self.config, "window_width", 1380) or 1380
        win_h = getattr(self.config, "window_height", 800) or 800
        self.resize(win_w, win_h)
        self.setMinimumSize(960, 560)
        if getattr(self.config, "window_maximized", False):
            self.showMaximized()

        self._init_ui()
        self._on_channel_selected(self.current_channel)
        self._setup_event_subscriptions()
        self._update_companion_node_display()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Left Vertical Dock (60px wide, Discord-style)
        self.nav_dock = NavDockWidget(config=self.config, parent=self)
        self.nav_dock.view_changed.connect(self._on_nav_view_changed)
        self.nav_dock.settings_requested.connect(self._open_settings)
        self.nav_dock.resync_requested.connect(self._on_manual_resync)
        self.nav_dock.broadcast_advert_requested.connect(self._trigger_node_broadcast)
        self.nav_dock.layer_toggled.connect(self._on_dock_layer_toggled)
        self.nav_dock.radio_connect_requested.connect(self._on_radio_connect_requested)
        self.nav_dock.contact_selected.connect(self._on_contact_selected)
        self.nav_dock.satellite_selected.connect(self._on_favorite_satellite_selected)
        main_layout.addWidget(self.nav_dock)

        # Compatibility handles for companion node and state
        self.companion_node_btn = QPushButton("📡 Companion Node ▾")
        self.companion_node_btn.clicked.connect(self._show_broadcast_menu)
        self.companion_node_lbl = self.companion_node_btn

        self.sync_badge = QFrame()
        self.sync_icon_lbl = QLabel("🟢")
        self.sync_text_lbl = QLabel("Synchronized with Device")

        self.btn_freshness_toggle = QPushButton("⏳ Age Fade: OFF")
        self.btn_freshness_toggle.setCheckable(True)
        self.btn_freshness_toggle.clicked.connect(self._on_freshness_toggled)

        self.btn_resync = QPushButton("🔄 Re-sync")
        self.btn_resync.clicked.connect(self._on_manual_resync)

        self.btn_toggle_map = QPushButton("🗺️ Map")
        self.btn_toggle_map.setCheckable(True)
        self.btn_toggle_map.setChecked(True)
        self.btn_toggle_map.clicked.connect(self._on_toggle_map)

        self.btn_clear_paths = QPushButton("🧹 Clear Paths")
        self.btn_clear_paths.clicked.connect(self._on_clear_paths_clicked)

        self.btn_settings = QPushButton("⚙️ Settings")
        self.btn_settings.clicked.connect(self._open_settings)

        # 2. Main Stacked Views (0: Main Chat & Map, 1: DMs, 2: Repeaters)
        self.main_stack = QStackedWidget()

        # --- VIEW 1: Main Chat & Map Splitter (Pane 0 | Pane 1 | Pane 2 | Pane 3) ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        self.main_splitter.setOpaqueResize(True)
        self.main_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #35363C;
            }
        """)

        # 1a. Left Sidebar (Channels & Channel Management) - Pane 0
        self.sidebar = Sidebar(storage=self.storage, config=self.config, driver=self.radio_driver, show_contacts=False)
        self.sidebar.setMinimumWidth(180)
        self.sidebar.channel_selected.connect(self._on_channel_selected)
        self.sidebar.contact_selected.connect(self._on_contact_selected)
        self.sidebar.join_channel_requested.connect(self._on_join_channel)
        self.main_splitter.addWidget(self.sidebar)

        # 1b. Center Stack: Chat (0) / Console (1) - Pane 1
        self.center_stack = QStackedWidget()
        self.center_stack.setMinimumWidth(320)

        center_chat_widget = QWidget()
        center_chat_layout = QVBoxLayout(center_chat_widget)
        center_chat_layout.setContentsMargins(0, 0, 0, 0)
        center_chat_layout.setSpacing(0)

        self.chat_widget = ChatWidget(storage=self.storage, config=self.config)
        self.chat_widget.reply_requested.connect(self._on_reply_requested)
        self.chat_widget.dm_requested.connect(self._on_dm_requested)
        self.chat_widget.visualise_path_requested.connect(self._on_visualise_message_path)
        self.chat_widget.resend_requested.connect(self._on_resend_message_requested)
        center_chat_layout.addWidget(self.chat_widget, 1)

        self.composer = PowerComposer(storage=self.storage, config=self.config)
        if self.config and hasattr(self.config, "app_colors"):
            self.composer.apply_theme(
                self.config.app_colors.send_button_color,
                getattr(self.config.app_colors, "send_button_text_color", None)
            )
        self.composer.send_message.connect(self._on_send_message)
        self.composer.switch_channel.connect(self._on_channel_selected)
        self.composer.join_channel.connect(self._on_join_channel)
        self.composer.search_query.connect(self._on_search_query)
        self.composer.search_cleared.connect(self._on_search_cleared)
        center_chat_layout.addWidget(self.composer)

        self.repeater_console = RepeaterConsoleWidget(radio_driver=self.radio_driver, storage=self.storage)
        self.repeater_console.back_to_chat_requested.connect(lambda: self.center_stack.setCurrentIndex(0))
        self.repeater_console.send_command_requested.connect(self._on_send_repeater_command)
        self.repeater_console.show_neighbors_on_map_requested.connect(self._on_show_repeater_neighbors_on_map)

        self.heard_floods_view = HeardFloodsWidget(storage=self.storage, parent=self)

        self.center_stack.addWidget(center_chat_widget)      # Index 0: Chat
        self.center_stack.addWidget(self.repeater_console)    # Index 1: Console
        self.center_stack.addWidget(self.heard_floods_view)   # Index 2: Heard Floods
        self.main_splitter.addWidget(self.center_stack)

        # 1c. Mesh Map (Prominent, goes flush to top edge) - Pane 2
        self.map_layer_dock = self.nav_dock.map_layers
        self.mesh_map = MeshMapWidget(storage=self.storage, config=self.config, driver=self.radio_driver)
        self.mesh_map.attach_layer_dock(self.map_layer_dock)
        if hasattr(self.map_layer_dock, "btn_rf_links") and self.map_layer_dock.btn_rf_links.isChecked():
            self.mesh_map.set_rf_links(True)
        self.mesh_map.setMinimumWidth(280)
        self.mesh_map.node_selected.connect(self._on_contact_selected)
        self.mesh_map.search_node_id_toggled.connect(
            lambda active: self.map_layer_dock.set_layer_active("search_node_id", active)
        )
        if hasattr(self.mesh_map, "packet_hud_toggled"):
            self.mesh_map.packet_hud_toggled.connect(
                lambda active: self.map_layer_dock.set_layer_active("packet_hud", active)
            )
        if hasattr(self.mesh_map, "activity_timeline_toggled"):
            self.mesh_map.activity_timeline_toggled.connect(
                lambda active: self.map_layer_dock.set_layer_active("activity_timeline", active)
            )
        if hasattr(self.mesh_map, "map_legend_toggled"):
            self.mesh_map.map_legend_toggled.connect(
                lambda active: self.map_layer_dock.set_layer_active("map_legend", active)
            )
        self.mesh_map.lightning_proximity_alert.connect(self._on_lightning_proximity_alert)
        self.mesh_map.adsb_proximity_alert.connect(self._on_adsb_proximity_alert)
        self.nav_dock.node_filter_changed.connect(self.mesh_map.set_node_filter_mode)
        self.main_splitter.addWidget(self.mesh_map)
        self.main_splitter.splitterMoved.connect(lambda pos, idx: self.mesh_map.pause_geometry_motion())

        # Wire Heard Floods interactive signals to Mesh Map
        self.heard_floods_view.flood_hovered.connect(self.mesh_map.preview_packet_path)
        self.heard_floods_view.flood_unhovered.connect(self.mesh_map.clear_preview_packet_path)
        self.heard_floods_view.flood_selected.connect(self._on_visualise_packet_path_info)
        if hasattr(self.heard_floods_view, "packet_traced"):
            self.heard_floods_view.packet_traced.connect(self.mesh_map.trigger_corescope_trace)
        self.heard_floods_view.back_to_chat_requested.connect(lambda: self.nav_dock.switch_view("main"))


        # 1d. Pixoo Preview Panel - Pane 3 (Hidden by default, toggleable in Settings)
        self.pixoo_panel = PixooPreviewWidget(pixoo_service=self.pixoo_service)
        self.pixoo_panel.setMinimumWidth(200)
        self.main_splitter.addWidget(self.pixoo_panel)

        show_mirror = getattr(self.config.pixoo, "show_live_mirror", False) if (self.config and hasattr(self.config, "pixoo")) else False
        self.pixoo_panel.setVisible(show_mirror)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 2)
        self.main_splitter.setStretchFactor(3, 0)
        self.main_splitter.setSizes([200, 420, 560, 240 if show_mirror else 0])

        self.main_stack.addWidget(self.main_splitter)  # Index 0: Main Chat & Map

        # --- VIEW 2: Direct Messages & Contacts ---
        self.dms_view = DMsViewWidget(storage=self.storage, config=self.config, radio_driver=self.radio_driver)
        self.dms_view.send_dm_requested.connect(lambda target, txt: self._on_send_message("DM", target, txt))
        self.dms_view.show_on_map_requested.connect(self._on_show_contact_on_map)
        self.dms_view.track_adsb_requested.connect(self._on_track_node_adsb)
        self.main_stack.addWidget(self.dms_view)       # Index 1: DMs View

        # --- VIEW: Room Servers ---
        self.rooms_view = RoomServersViewWidget(storage=self.storage, config=self.config, radio_driver=self.radio_driver)
        self.rooms_view.send_message_requested.connect(lambda target, txt: self._on_send_message("DM", target, txt))
        self.rooms_view.show_on_map_requested.connect(self._on_show_contact_on_map)
        self.rooms_view.track_adsb_requested.connect(self._on_track_node_adsb)
        self.main_stack.addWidget(self.rooms_view)     # Index 2: Room Servers View

        # --- VIEW 3: Repeaters & Infrastructure ---
        self.repeaters_view = RepeatersViewWidget(storage=self.storage, config=self.config, radio_driver=self.radio_driver)
        self.repeaters_view.send_command_requested.connect(self._on_send_repeater_command)
        self.repeaters_view.show_neighbors_on_map_requested.connect(self._on_show_repeater_neighbors_on_map)
        self.repeaters_view.show_on_map_requested.connect(self._on_show_contact_on_map)
        self.repeaters_view.track_adsb_requested.connect(self._on_track_node_adsb)
        self.repeaters_view.switch_to_chat_requested.connect(lambda: self.nav_dock.switch_view("main"))
        self.main_stack.addWidget(self.repeaters_view) # Index 3: Repeaters View

        # --- VIEW: Satellites & Spacecraft ---
        self.satellites_view = SatellitesViewWidget(storage=self.storage, config=self.config)
        self.satellites_view.show_on_map_requested.connect(self._on_show_satellite_on_map)
        self.satellites_view.favorite_toggled.connect(lambda nid, fav: self._update_dock_favorites())
        self.main_stack.addWidget(self.satellites_view) # Index 4: Satellites View

        # --- VIEW 4: Settings View (Embedded full-width view) ---
        self.settings_view = SettingsWidget(config=self.config, storage=self.storage, radio_driver=self.radio_driver, parent=self)
        self.settings_view.close_requested.connect(self._close_settings)
        self.main_stack.addWidget(self.settings_view)  # Index 5: Settings View

        # Wrap main stack with a vertical container to host dismissable update notifications
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.update_banner = self._create_update_banner()
        content_layout.addWidget(self.update_banner)
        content_layout.addWidget(self.main_stack, 1)

        main_layout.addWidget(content_container, 1)
        self._update_freshness_btn_state()
        self._update_dock_favorites()

        # Connect version checker for fallback notification (when splash is disabled or already dismissed)
        VersionChecker.get_instance().update_available.connect(self._on_update_available)

        # Splash / Loading Mask Overlay
        if getattr(self.config, "show_splash_screen", True):
            self.splash_overlay = SplashOverlay(config=self.config, parent=self)
            self.splash_overlay.dismissed.connect(self._on_splash_dismissed)
            self.splash_overlay.setGeometry(self.rect())
            self.splash_overlay.show()
            if hasattr(self, "mesh_map"):
                self.mesh_map.map_ready.connect(self.splash_overlay.on_map_ready)
        else:
            self.splash_overlay = None
            if getattr(self.config, "check_updates_on_startup", True):
                VersionChecker.get_instance().check_for_updates()

    def _create_update_banner(self) -> QFrame:
        """Creates a subtle, modern update notification banner above the main view."""
        banner = QFrame()
        banner.setObjectName("appUpdateBanner")
        banner.setStyleSheet("""
            QFrame#appUpdateBanner {
                background-color: #0C4A6E;
                border-bottom: 1.5px solid #0284C7;
            }
            QLabel {
                color: #F0F9FF;
                font-size: 12px;
            }
        """)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        self.update_banner_lbl = QLabel()
        layout.addWidget(self.update_banner_lbl, 1)

        self.update_banner_dl_btn = QPushButton("📥 Download Update")
        self.update_banner_dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_banner_dl_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #38BDF8;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
        """)
        layout.addWidget(self.update_banner_dl_btn)

        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94A3B8;
                border: none;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
        """)
        close_btn.clicked.connect(banner.hide)
        layout.addWidget(close_btn)

        banner.hide()
        return banner

    def _on_update_available(self, release_info: ReleaseInfo):
        """Displays fallback update banner if the splash overlay is not actively showing it."""
        if self.splash_overlay is not None and self.splash_overlay.isVisible():
            return
        self.update_banner_lbl.setText(
            f"🚀 <b>Update Available:</b> MeshCore Navigator <b>v{release_info.version}</b> is now available!"
        )
        self.update_banner_dl_btn.setText(f"📥 Download v{release_info.version}")
        try:
            self.update_banner_dl_btn.clicked.disconnect()
        except Exception:
            pass
        self.update_banner_dl_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release_info.html_url)))
        self.update_banner.show()

    def _on_splash_dismissed(self):
        self.splash_overlay = None

    def _setup_event_subscriptions(self):
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_bus_message_received)
        bus.subscribe(EventType.MESSAGE_SENT, self._on_bus_message_sent)
        bus.subscribe(EventType.MESSAGE_UPDATED, self._on_bus_message_updated)
        bus.subscribe(EventType.CONNECTION_STATUS_CHANGED, self._on_connection_changed)
        bus.subscribe(EventType.SETTINGS_UPDATED, self._on_settings_updated)
        bus.subscribe(EventType.SYNC_STATUS, self._on_sync_status_received)
        bus.subscribe(EventType.FAVORITES_UPDATED, lambda _: (
            self.sidebar.reload(),
            self.dms_view.reload_contacts(),
            getattr(self, "rooms_view", None) and self.rooms_view.reload_rooms(),
            self.repeaters_view.reload_repeaters(),
            self.chat_widget.set_target(self.chat_widget.current_channel, self.chat_widget.current_dm),
            self._update_dock_favorites()
        ))
        bus.subscribe(EventType.NODE_DISCOVERED, lambda _: self._update_dock_favorites())

    def _update_dock_favorites(self):
        if not hasattr(self, "nav_dock") or not self.storage:
            return
        try:
            contacts = self.storage.get_contacts()
            fav_users = getattr(self.config, "favorite_users", []) if self.config else []
            favorites = []
            for c in contacts:
                is_fav = bool(c.is_favorite or (c.node_id in fav_users) or (c.alias and c.alias in fav_users))
                if is_fav:
                    favorites.append(c)
            # Include favorite satellites
            if hasattr(self.storage, "get_favorite_satellites"):
                fav_sats = self.storage.get_favorite_satellites()
                favorites.extend(fav_sats)
            self.nav_dock.update_favorite_contacts(favorites)
        except Exception as e:
            logger.warning(f"Error updating dock favorites: {e}")

    def _on_nav_view_changed(self, view_name: str):
        if view_name == "main":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(True)
            self.center_stack.setCurrentIndex(0)
            self.main_stack.setCurrentWidget(self.main_splitter)
        elif view_name == "floods":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.center_stack.setCurrentIndex(2)
            self.main_stack.setCurrentWidget(self.main_splitter)
        elif view_name == "dms":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.main_stack.setCurrentWidget(self.dms_view)
        elif view_name == "rooms":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.main_stack.setCurrentWidget(self.rooms_view)
        elif view_name == "repeaters":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.main_stack.setCurrentWidget(self.repeaters_view)
        elif view_name == "satellites":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.main_stack.setCurrentWidget(self.satellites_view)

    def _on_dock_layer_toggled(self, layer_key: str, is_active: bool):
        if not hasattr(self, "mesh_map"):
            return
        if layer_key == "rf_links":
            self.mesh_map.set_rf_links(is_active)
        elif layer_key == "byte_paths":
            self.mesh_map.set_path_modes(is_active)
        elif layer_key == "orbitals":
            self.mesh_map.set_orbitals(is_active)
        elif layer_key == "scopes":
            self.mesh_map.set_scopes(is_active)
        elif layer_key == "tropo":
            self.mesh_map.set_tropo(is_active)
        elif layer_key == "adsb":
            self.mesh_map.set_adsb(is_active)
        elif layer_key == "activity_heatmap":
            self.mesh_map.set_activity_heatmap(is_active)
        elif layer_key == "thunderstorm":
            self.mesh_map.set_thunderstorm(is_active)
        elif layer_key == "rf_los":
            self.mesh_map.set_los_view_active(is_active)
        elif layer_key == "space_weather":
            self.mesh_map.set_space_weather(is_active)
        elif layer_key == "satellites":
            self.mesh_map.set_satellites(is_active)
        elif layer_key == "search_node_id":
            if hasattr(self.mesh_map, "set_search_node_id"):
                self.mesh_map.set_search_node_id(is_active)
        elif layer_key == "packet_hud":
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.map_show_packet_hud = is_active
                try:
                    self.config.save()
                except Exception:
                    pass
            if hasattr(self.mesh_map, "set_packet_hud_visible"):
                self.mesh_map.set_packet_hud_visible(is_active)
        elif layer_key == "activity_timeline":
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.map_show_activity_timeline = is_active
                try:
                    self.config.save()
                except Exception:
                    pass
            if hasattr(self.mesh_map, "set_activity_timeline_visible"):
                self.mesh_map.set_activity_timeline_visible(is_active)
        elif layer_key == "map_legend":
            if hasattr(self.mesh_map, "set_map_legend_visible"):
                self.mesh_map.set_map_legend_visible(is_active)
        elif layer_key == "new_nodes":
            if hasattr(self.mesh_map, "set_new_nodes"):
                self.mesh_map.set_new_nodes(is_active)
        elif layer_key == "mqtt_nodes":
            if hasattr(self.mesh_map, "set_mqtt_nodes"):
                self.mesh_map.set_mqtt_nodes(is_active)
        elif layer_key == "map_3d":
            if hasattr(self.mesh_map, "set_3d_mode"):
                self.mesh_map.set_3d_mode(is_active)
        elif layer_key == "age_fade":
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.node_freshness_fading = is_active
                try:
                    self.config.save()
                except Exception:
                    pass
            self.mesh_map.set_freshness_fading(is_active)
            self._update_freshness_btn_state()

    def _on_lightning_proximity_alert(self, distance_mi: float, bearing_deg: int):
        """Displays desktop/tray notification for nearby lightning strikes within 25 miles."""
        logger.warning(f"Lightning proximity alert: strike {distance_mi:.1f} mi away at {bearing_deg}°")
        if getattr(self, "_tray_icon", None) and self._tray_icon.isVisible():
            try:
                self._tray_icon.showMessage(
                    "⚡ Thunderstorm Proximity Alert",
                    f"Lightning strike detected {distance_mi:.1f} mi away (bearing {bearing_deg}°).",
                    QSystemTrayIcon.MessageIcon.Warning,
                    6000,
                )
            except Exception as e:
                logger.debug(f"Failed to show proximity tray message: {e}")

    def _on_adsb_proximity_alert(self, hex_code: str, flight: str, dist_mi: float, category: str):
        """Displays desktop/tray notification for nearby watched aircraft within 10 miles."""
        cat_title = category.capitalize()
        icon_str = "⚔️" if category == "military" else ("🚁" if category == "helicopter" else "✈️")
        msg = f"{icon_str} {cat_title} Proximity Alert: {flight} ({hex_code.upper()}) is {dist_mi:.1f} mi away."
        logger.warning(msg)
        if hasattr(self, "mesh_map") and hasattr(self.mesh_map, "_notify_user"):
            self.mesh_map._notify_user(msg)
        if getattr(self, "_tray_icon", None) and self._tray_icon.isVisible():
            try:
                self._tray_icon.showMessage(
                    f"{icon_str} {cat_title} Aircraft Proximity Alert",
                    f"{flight} ({hex_code.upper()}) is within {dist_mi:.1f} miles of observed location.",
                    QSystemTrayIcon.MessageIcon.Warning if category == "military" else QSystemTrayIcon.MessageIcon.Information,
                    8000,
                )
            except Exception as e:
                logger.debug(f"Failed to show ADS-B proximity tray message: {e}")

    def _on_visualise_packet_path_info(self, path):
        """Replays and visualises the selected packet path with animation on the map."""
        if not path:
            return
        if hasattr(self, "mesh_map"):
            # Close any open node popup so it does not obscure the path or leave broken boxes
            self.mesh_map.run_js(
                "if (typeof map !== 'undefined' && map && map.closePopup) map.closePopup(); "
                "if (window._map3dNodePopup) { try { window._map3dNodePopup.remove(); } catch(e){} }"
            )
            # 1. Draw persistent route line preview
            self.mesh_map.preview_packet_path(path)
            # 2. Trigger dynamic traveling CoreScope particle beam animation on 2D and 3D
            self.mesh_map.trigger_corescope_trace(path)
            # 3. Fit bounds comfortably around the entire path route
            coords = getattr(path, "coordinates", None) or []
            if len(coords) >= 2:
                coords_json = json.dumps(coords)
                self.mesh_map.run_js(f"""
                    (function() {{
                        var pts = {coords_json};
                        if (pts && pts.length >= 2) {{
                            if (window._is3DActive && typeof map3d !== 'undefined' && map3d) {{
                                var bounds = new maplibregl.LngLatBounds();
                                pts.forEach(function(p) {{ bounds.extend([p[1], p[0]]); }});
                                map3d.fitBounds(bounds, {{ padding: 60, maxZoom: 12, duration: 800 }});
                            }} else if (typeof map !== 'undefined' && map && map.fitBounds) {{
                                map.fitBounds(pts, {{ padding: [50, 50], maxZoom: 12 }});
                            }}
                        }}
                    }})();
                """)
            elif len(coords) == 1:
                lat, lon = coords[0]
                self.mesh_map.run_js(f"""
                    (function() {{
                        if (window._is3DActive && typeof map3d !== 'undefined' && map3d) {{
                            map3d.easeTo({{ center: [{lon}, {lat}], zoom: 11, duration: 800 }});
                        }} else if (typeof map !== 'undefined' && map && map.setView) {{
                            map.setView([{lat}, {lon}], 11);
                        }}
                    }})();
                """)

    def _on_track_node_adsb(self, node_id: str, lat: float, lon: float, alias: str):
        self.nav_dock.switch_view("main")
        if hasattr(self.nav_dock, "btn_adsb"):
            self.nav_dock.btn_adsb.blockSignals(True)
            self.nav_dock.btn_adsb.setChecked(True)
            self.nav_dock.btn_adsb.blockSignals(False)
        if hasattr(self, "mesh_map"):
            self.mesh_map.set_adsb(True)
            self.mesh_map.set_adsb_target(node_id, alias, lat, lon)

    def _update_freshness_btn_state(self):
        fading = getattr(self.config.meshcore, "node_freshness_fading", False) if (self.config and hasattr(self.config, "meshcore")) else False
        if hasattr(self, "btn_freshness_toggle"):
            self.btn_freshness_toggle.blockSignals(True)
            self.btn_freshness_toggle.setChecked(fading)
            if fading:
                self.btn_freshness_toggle.setText("⏳ Age Fade: ON")
            else:
                self.btn_freshness_toggle.setText("⏳ Age Fade: OFF")
            self.btn_freshness_toggle.blockSignals(False)
        if hasattr(self, "mesh_map") and hasattr(self.mesh_map, "btn_age_fade"):
            self.mesh_map.btn_age_fade.blockSignals(True)
            self.mesh_map.btn_age_fade.setChecked(fading)
            self.mesh_map.btn_age_fade.blockSignals(False)

    def _on_freshness_toggled(self):
        fading = self.btn_freshness_toggle.isChecked()
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.node_freshness_fading = fading
            self.config.save()
        self._update_freshness_btn_state()
        if hasattr(self, "mesh_map"):
            self.mesh_map.set_freshness_fading(fading)
        bus.emit(EventType.SETTINGS_UPDATED, self.config)

    def _on_toggle_map(self):
        visible = self.btn_toggle_map.isChecked()
        self.mesh_map.setVisible(visible)
        if visible:
            self.mesh_map.refresh_map_data()

    def _on_clear_paths_clicked(self):
        if hasattr(self, "mesh_map"):
            self.mesh_map.clear_visualised_path()
        bus.emit(EventType.CLEAR_VISUALISED_PATHS, None)

    def _on_sync_status_received(self, data: dict):
        self._last_sync_data = data
        stage = data.get("stage", "syncing")
        message = data.get("message", "")
        is_synced = data.get("is_synced", False)

        if hasattr(self, "sync_text_lbl"):
            self.sync_text_lbl.setText(message)
        if hasattr(self, "nav_dock"):
            self.nav_dock.update_sync_status(stage, message, is_synced)
        self._update_companion_node_display()

        if is_synced or stage == "complete":
            if hasattr(self, "sync_icon_lbl"):
                self.sync_icon_lbl.setText("🟢")
            self.sidebar.reload()
            if hasattr(self, "dms_view"):
                self.dms_view.reload_contacts()
            if hasattr(self, "repeaters_view"):
                self.repeaters_view.reload_repeaters()
        elif stage == "connecting":
            if hasattr(self, "sync_icon_lbl"):
                self.sync_icon_lbl.setText("🔄")
        else:
            if hasattr(self, "sync_icon_lbl"):
                self.sync_icon_lbl.setText("🔄")

    def _on_manual_resync(self):
        if self.radio_driver:
            if hasattr(self, "sync_icon_lbl"):
                self.sync_icon_lbl.setText("🔄")
            if hasattr(self, "sync_text_lbl"):
                self.sync_text_lbl.setText("Re-syncing with hardware...")
            if hasattr(self, "nav_dock"):
                self.nav_dock.update_sync_status("syncing", "Re-syncing with hardware...", False)
            self.radio_driver.resync()

    def _on_radio_connect_requested(self):
        """Initiates radio hardware connection or reconnection from navigation dock."""
        logger.info("User requested radio connection/reconnection from navigation dock")
        if hasattr(self, "nav_dock"):
            self.nav_dock.update_sync_status("connecting", "Connecting to radio hardware...", False)
        if self.radio_driver:
            if hasattr(self.radio_driver, "connect"):
                self.radio_driver.connect()
            elif hasattr(self.radio_driver, "start"):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.radio_driver.start())
                except RuntimeError:
                    pass


    def _update_companion_node_display(self):
        """Updates the companion node alias display in the left dock tooltip and button."""
        alias = ""
        if self.radio_driver and hasattr(self.radio_driver, "client") and self.radio_driver.client:
            self_info = getattr(self.radio_driver.client, "self_info", None)
            if self_info and isinstance(self_info, dict):
                hw_name = self_info.get("name")
                if hw_name and str(hw_name).strip():
                    alias = str(hw_name).strip()
        if not alias and self.config and hasattr(self.config, "meshcore") and self.config.meshcore.node_alias:
            a = str(self.config.meshcore.node_alias).strip()
            if a and a != "Heltec-V3":
                alias = a
        if not alias and self.storage:
            try:
                local_contact = self.storage.get_contact("local")
                if local_contact and local_contact.alias:
                    alias = local_contact.alias.strip()
            except Exception:
                pass
        if not alias and self.config and hasattr(self.config, "meshcore") and self.config.meshcore.node_alias:
            alias = str(self.config.meshcore.node_alias).strip()
        if not alias:
            alias = "Companion Node"

        if hasattr(self, "companion_node_btn"):
            self.companion_node_btn.setText(f"📡 {alias} ▾")
            self.companion_node_btn.setToolTip(f"Companion Node: {alias}\nClick to broadcast node adverts")
        elif hasattr(self, "companion_node_lbl"):
            self.companion_node_lbl.setText(f"📡 {alias}")

        if hasattr(self, "nav_dock"):
            self.nav_dock.update_companion_alias(alias)

    def _on_channel_selected(self, channel_name: str):
        self.current_channel = channel_name
        self.current_dm = None
        self.sidebar.set_active_channel(channel_name)
        self.chat_widget.set_target(channel_name, None)
        self.composer.set_active_target(channel_name, None)
        self.center_stack.setCurrentIndex(0)

        if channel_name and channel_name.lower() != "public":
            if self.radio_driver and hasattr(self.radio_driver, "ensure_channel_synced"):
                self.radio_driver.ensure_channel_synced(channel_name)

        if self.storage:
            self.storage.set_app_state("last_active_channel", channel_name)
        if self.config and hasattr(self.config, "last_active_channel"):
            self.config.last_active_channel = channel_name

    def _on_join_channel(self, raw_channel_name: str):
        raw = raw_channel_name.strip()
        if not raw:
            return
        clean_name = raw if raw.startswith("#") else f"#{raw}"

        slot = 1
        if self.radio_driver:
            res = self.radio_driver.join_channel(clean_name)
            slot = res.get("channel_id", 1)
        elif self.storage:
            is_fav = bool(self.config and self.config.is_channel_favorite(clean_name))
            self.storage.save_channel(ChannelInfo(channel_id=slot, name=clean_name, is_favorite=is_fav, is_pixoo_enabled=True))

        from datetime import datetime
        sys_msg = MessageEnvelope(
            id=f"sys-{int(datetime.now().timestamp()*1000)}",
            source_driver="system",
            sender_id="system",
            sender_name="System",
            channel=clean_name,
            text=f"Joined channel {clean_name} (Hardware Slot {slot}). Ready for mesh communication.",
            is_outgoing=False
        )
        if self.storage:
            self.storage.save_message(sys_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, sys_msg)

        self._on_channel_selected(clean_name)
        self.sidebar.reload()

    def _on_dm_requested(self, contact_id: str):
        contact = self.storage.get_contact(contact_id) if self.storage else None
        target_id = contact.node_id if contact else contact_id
        self.current_dm = target_id
        self.chat_widget.set_target(self.current_channel, target_id)
        self.composer.set_active_target(self.current_channel, target_id)
        self.center_stack.setCurrentIndex(0)
        self.dms_view.select_contact(target_id)

    def _on_contact_selected(self, contact_id: str, force_dm: bool = False):
        if force_dm:
            self._on_dm_requested(contact_id)
            return

        contact = self.storage.get_contact(contact_id) if self.storage else None
        is_rep = False
        is_room = False
        if contact:
            is_room = getattr(contact, "is_room_server", False) or is_room_server_contact(contact)
            is_rep = contact.is_repeater or "[rep]" in (contact.alias or "").lower()

        if is_room and contact:
            self.nav_dock.switch_view("rooms")
            if hasattr(self, "rooms_view"):
                self.rooms_view.set_active_room(contact)
        elif is_rep and contact:
            self.repeater_console.set_repeater(contact)
            self.center_stack.setCurrentIndex(1)
            self.repeaters_view.set_active_repeater(contact)
        else:
            self._on_dm_requested(contact_id)

    def _on_favorite_satellite_selected(self, norad_id: str):
        self.nav_dock.switch_view("satellites")
        if hasattr(self, "satellites_view"):
            self.satellites_view.select_satellite(norad_id)

    def _on_show_satellite_on_map(self, norad_id: str):
        self.nav_dock.switch_view("main")
        if hasattr(self, "map_layer_dock") and hasattr(self.map_layer_dock, "btn_satellites"):
            if not self.map_layer_dock.btn_satellites.isChecked():
                self.map_layer_dock.btn_satellites.setChecked(True)
        if hasattr(self, "mesh_map"):
            self.mesh_map.select_satellite(norad_id)

    def _on_send_repeater_command(self, repeater_id: str, command: str):
        if self.radio_driver:
            if hasattr(self.radio_driver, "send_repeater_command"):
                self.radio_driver.send_repeater_command(repeater_id, command)
            else:
                self.radio_driver.send_direct_message(repeater_id, command)

    def _on_send_message(self, channel: str, recipient_id: Optional[str], text: str):
        if recipient_id:
            if self.radio_driver:
                self.radio_driver.send_direct_message(recipient_id, text)
        else:
            if self.radio_driver:
                self.radio_driver.send_channel_message(channel, text)

    def _on_show_contact_on_map(self, node_id: str, lat: Optional[float], lon: Optional[float], alias: str):
        self.nav_dock.switch_view("main")
        if hasattr(self, "mesh_map"):
            self.mesh_map.center_on_node(node_id, lat, lon, alias)

    def _on_search_query(self, query: str):
        self.chat_widget.show_search_results(query)

    def _on_search_cleared(self):
        if self.chat_widget.is_searching:
            self.chat_widget.set_target(self.current_channel, self.current_dm)

    def _on_bus_message_received(self, msg: MessageEnvelope):
        self.chat_widget.add_message(msg)
        self.repeater_console.handle_incoming_message(msg)
        if hasattr(self, "dms_view") and hasattr(self.dms_view, "chat_widget"):
            self.dms_view.chat_widget.add_message(msg)
        if hasattr(self, "repeaters_view") and hasattr(self.repeaters_view, "console"):
            self.repeaters_view.console.handle_incoming_message(msg)
        self.sidebar.reload()
        if hasattr(self, "dms_view"):
            self.dms_view.reload_contacts()
        if hasattr(self, "repeaters_view"):
            self.repeaters_view.reload_repeaters()

    def _on_bus_message_sent(self, msg: MessageEnvelope):
        self.chat_widget.add_message(msg)
        if hasattr(self, "dms_view") and hasattr(self.dms_view, "chat_widget"):
            self.dms_view.chat_widget.add_message(msg)
        self.sidebar.reload()

    def _on_bus_message_updated(self, msg: MessageEnvelope):
        self.chat_widget.update_message(msg)
        if hasattr(self, "dms_view") and hasattr(self.dms_view, "chat_widget"):
            self.dms_view.chat_widget.update_message(msg)

    def _on_connection_changed(self, status: dict):
        connected = status.get("connected", False)
        port = status.get("port", "")
        mode = status.get("mode", "serial")
        if hasattr(self, "nav_dock"):
            self.nav_dock.update_connection_status(connected, port, mode)
        self.sidebar.update_connection_status(connected, port, mode)
        self._update_companion_node_display()

    def _on_settings_updated(self, config: AppConfig):
        self.config = config
        set_global_avatar_style(getattr(config, "user_avatar_style", "droid"))
        self._update_companion_node_display()
        self._update_freshness_btn_state()
        self._update_dock_favorites()
        if hasattr(self, "composer") and hasattr(config, "app_colors"):
            self.composer.apply_theme(
                config.app_colors.send_button_color,
                getattr(config.app_colors, "send_button_text_color", None)
            )
        if hasattr(self, "mesh_map"):
            if hasattr(config, "app_colors"):
                self.mesh_map.apply_colors(config.app_colors)
            if hasattr(config, "meshcore"):
                self.mesh_map.set_freshness_fading(getattr(config.meshcore, "node_freshness_fading", False))
            self.mesh_map.refresh_map_data()
        if hasattr(self, "pixoo_panel"):
            show_mirror = getattr(config.pixoo, "show_live_mirror", False)
            self.pixoo_panel.setVisible(show_mirror)
        if hasattr(self, "_last_sync_data") and self._last_sync_data:
            self._on_sync_status_received(self._last_sync_data)
        self.sidebar.reload()
        if hasattr(self, "dms_view"):
            self.dms_view.reload_contacts()
        if hasattr(self, "rooms_view"):
            self.rooms_view.reload_rooms()
        if hasattr(self, "repeaters_view"):
            self.repeaters_view.reload_repeaters()
        self.chat_widget.reload_messages()
        if hasattr(self, "mqtt_service") and self.mqtt_service:
            self.mqtt_service.restart()


    def _open_settings(self):
        """Opens embedded settings view, hiding channels, chat and map."""
        if hasattr(self, "main_stack") and hasattr(self, "settings_view"):
            if self.main_stack.currentWidget() == self.settings_view:
                self._close_settings()
            else:
                self._previous_view_widget = self.main_stack.currentWidget()
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(False)
                self.settings_view.reload()
                self.main_stack.setCurrentWidget(self.settings_view)

    def _close_settings(self):
        """Returns to the previous view from the embedded settings view."""
        if hasattr(self, "main_stack"):
            prev_widget = getattr(self, "_previous_view_widget", self.main_splitter)
            if prev_widget == self.settings_view or not prev_widget:
                prev_widget = self.main_splitter
            if prev_widget == self.main_splitter:
                is_main_chat = (not hasattr(self, "center_stack")) or (self.center_stack.currentIndex() == 0)
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(is_main_chat)
            else:
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(False)
            self.main_stack.setCurrentWidget(prev_widget)

    def _on_reply_requested(self, reply_prefix: str):
        cur = self.composer.input_field.text()
        if not cur:
            self.composer.input_field.setText(reply_prefix)
        self.composer.focus()

    def _on_resend_message_requested(self, msg: MessageEnvelope):
        if not msg:
            return
        if hasattr(self, "composer"):
            self.composer.populate_resend(msg.text)
        if getattr(msg, "is_direct_message", False) or getattr(msg, "recipient_id", None):
            self._on_send_message("DM", msg.recipient_id, msg.text)
        else:
            self._on_send_message(msg.channel or "Public", None, msg.text)

    def _on_visualise_message_path(self, msg: MessageEnvelope):
        """Switches to main view and visualises the message's multi-hop path."""
        self.nav_dock.switch_view("main")
        if not self.btn_toggle_map.isChecked():
            self.btn_toggle_map.setChecked(True)
            self._on_toggle_map()
        if hasattr(self, "mesh_map"):
            self.mesh_map.visualise_message_path(msg)

    def _show_broadcast_menu(self):
        """Opens context menu to trigger node adverts."""
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
                padding: 8px 18px 8px 12px;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 500;
            }
            QMenu::item:selected {
                background-color: #2B303C;
                color: #38BDF8;
            }
        """)
        act_zero = menu.addAction("📡 Broadcast Node (Advert Zero Hop)")
        act_flood = menu.addAction("🌊 Broadcast Node (Advert Flood Routed)")

        action = menu.exec(self.companion_node_btn.mapToGlobal(QPoint(0, self.companion_node_btn.height() + 4)))
        if action == act_zero:
            self._trigger_node_broadcast(flood=False)
        elif action == act_flood:
            self._trigger_node_broadcast(flood=True)

    def _trigger_node_broadcast(self, flood: bool = False):
        """Dispatches an advert broadcast request through the radio driver."""
        adv_name = "Flood-Routed" if flood else "Zero-Hop"
        if not self.radio_driver or not self.radio_driver.is_connected():
            self.statusBar().showMessage(f"⚠️ Radio not connected - could not broadcast {adv_name} advert", 4000)
            return

        success = self.radio_driver.send_advert(flood=flood)
        if success:
            logger.info(f"Broadcasted {adv_name} node advert")
            self.statusBar().showMessage(f"✓ Broadcasted {adv_name} advert to mesh", 4000)
        else:
            self.statusBar().showMessage(f"❌ Failed to broadcast {adv_name} advert", 4000)

    def _on_show_repeater_neighbors_on_map(self, repeater_contact, neighbors_data):
        """Displays repeater neighbours on the Leaflet map."""
        self.nav_dock.switch_view("main")
        if not self.btn_toggle_map.isChecked():
            self.btn_toggle_map.setChecked(True)
            self._on_toggle_map()
        if hasattr(self, "mesh_map") and self.mesh_map:
            # Allow Qt & Wayland layout/geometry pass to complete before rendering map layers
            QTimer.singleShot(150, lambda: self.mesh_map.display_repeater_neighbors(repeater_contact, neighbors_data) if (hasattr(self, "mesh_map") and self.mesh_map) else None)

    def _save_window_state(self):
        """Persists window geometry and active channel cleanly."""
        try:
            if self.config:
                self.config.window_maximized = self.isMaximized()
                if not self.isMaximized():
                    self.config.window_width = self.width()
                    self.config.window_height = self.height()
                if hasattr(self, "current_channel") and self.current_channel:
                    self.config.last_active_channel = self.current_channel
                self.config.save()
            if self.storage and hasattr(self, "current_channel") and self.current_channel:
                try:
                    self.storage.set_app_state("last_active_channel", self.current_channel)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed to persist window state: {e}")

    def initiate_clean_exit(self):
        """Coordinates an orderly asynchronous shutdown: stops drivers, checkpoints DB, cleans up WebEngine, and exits."""
        if getattr(self, "_is_shutting_down", False):
            return
        self._is_shutting_down = True
        self._force_close = True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None

        if loop and loop.is_running():
            asyncio.ensure_future(self._async_clean_exit())
        else:
            self._sync_clean_exit()

    async def _async_clean_exit(self):
        logger.info("Graceful application shutdown initiated...")
        self._save_window_state()

        # Stop radio driver
        if self.radio_driver and hasattr(self.radio_driver, "stop"):
            try:
                await asyncio.wait_for(self.radio_driver.stop(), timeout=2.5)
            except Exception as e:
                logger.debug(f"Error stopping radio driver during exit: {e}")

        # Stop pixoo service
        if self.pixoo_service and hasattr(self.pixoo_service, "stop"):
            try:
                await asyncio.wait_for(self.pixoo_service.stop(), timeout=2.5)
            except Exception as e:
                logger.debug(f"Error stopping pixoo service during exit: {e}")

        # Stop local HTTP bridge
        if getattr(self, "gateway", None) and hasattr(self.gateway, "stop_http_bridge"):
            try:
                self.gateway.stop_http_bridge()
            except Exception as e:
                logger.debug(f"Error stopping gateway during exit: {e}")

        # Release WebEngine and child widgets
        self.cleanup()

        # Checkpoint and park database
        if self.storage and hasattr(self.storage, "backup_database"):
            try:
                self.storage.backup_database(reason="app_quit")
            except Exception as e:
                logger.warning(f"Database parking backup on exit failed: {e}")

        self._shutdown_completed = True
        app = QApplication.instance()
        if app:
            app.quit()

    def _sync_clean_exit(self):
        logger.info("Synchronous fallback shutdown initiated...")
        self._save_window_state()

        if getattr(self, "gateway", None) and hasattr(self.gateway, "stop_http_bridge"):
            try:
                self.gateway.stop_http_bridge()
            except Exception as e:
                logger.debug(f"Error stopping gateway: {e}")

        self.cleanup()

        if self.storage and hasattr(self.storage, "backup_database"):
            try:
                self.storage.backup_database(reason="app_quit")
            except Exception as e:
                logger.warning(f"Database parking backup failed: {e}")

        self._shutdown_completed = True
        app = QApplication.instance()
        if app:
            app.quit()

    def cleanup(self):
        """Releases WebEngine and async resources cleanly before exit."""
        if getattr(self, "_is_cleaned_up", False):
            return
        self._is_cleaned_up = True
        if hasattr(self, "mesh_map") and self.mesh_map and hasattr(self.mesh_map, "cleanup"):
            try:
                self.mesh_map.cleanup()
            except Exception as e:
                logger.debug(f"Map cleanup note: {e}")
        try:
            VersionChecker.get_instance().stop(100)
        except Exception:
            pass
        if hasattr(self, "mqtt_service") and self.mqtt_service:
            try:
                self.mqtt_service.stop()
            except Exception as e:
                logger.debug(f"MQTT service stop note: {e}")


    def closeEvent(self, event):
        """Handles window close. If tray is active and quit is not forced, hide to tray."""
        tray_active = getattr(self, "_tray_icon", None) is not None and self._tray_icon.isVisible()
        if tray_active and not getattr(self, "_force_close", False):
            self._save_window_state()
            event.ignore()
            self.hide()
            return

        self._save_window_state()
        if not getattr(self, "_shutdown_completed", False) and not getattr(self, "_is_shutting_down", False):
            event.ignore()
            self.initiate_clean_exit()
            return

        self.cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        overlay = getattr(self, "splash_overlay", None)
        if overlay is not None:
            try:
                if overlay.isVisible():
                    overlay.setGeometry(self.rect())
                    overlay.raise_()
            except (RuntimeError, AttributeError):
                self.splash_overlay = None

