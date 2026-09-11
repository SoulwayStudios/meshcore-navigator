"""Main Modal Window for MeshCore Pixoo System Tray with Discord-Inspired Architecture."""

import logging
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QFrame, QStackedWidget, QMenu
)

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope, ChannelInfo
from meshcore_tray.ui.chat_widget import ChatWidget
from meshcore_tray.ui.composer import PowerComposer
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget
from meshcore_tray.ui.pixoo_preview import PixooPreviewWidget
from meshcore_tray.ui.settings_widget import SettingsWidget, SettingsDialog
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.ui.dms_view import DMsViewWidget
from meshcore_tray.ui.repeaters_view import RepeatersViewWidget
from meshcore_tray.ui.heard_floods_view import HeardFloodsWidget
from meshcore_tray.ui.splash_overlay import SplashOverlay

logger = logging.getLogger("meshcore_tray.main_window")


class MainWindow(QMainWindow):
    """Primary application window featuring Left Navigation Dock, Main Chat & Map, DMs, and Repeaters Views."""

    def __init__(self, config: AppConfig, storage=None, radio_driver=None, pixoo_service=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.storage = storage
        self.radio_driver = radio_driver
        self.pixoo_service = pixoo_service

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

        self.setWindowTitle("MESHCORE NAVIGATOR")
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
        self.chat_widget.dm_requested.connect(self._on_contact_selected)
        self.chat_widget.visualise_path_requested.connect(self._on_visualise_message_path)
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
        self.mesh_map = MeshMapWidget(storage=self.storage, config=self.config, driver=self.radio_driver)
        self.mesh_map.setMinimumWidth(280)
        self.mesh_map.node_selected.connect(self._on_contact_selected)
        self.nav_dock.node_filter_changed.connect(self.mesh_map.set_node_filter_mode)
        self.main_splitter.addWidget(self.mesh_map)

        # Wire Heard Floods interactive signals to Mesh Map
        self.heard_floods_view.flood_hovered.connect(self.mesh_map.preview_packet_path)
        self.heard_floods_view.flood_unhovered.connect(self.mesh_map.clear_preview_packet_path)
        self.heard_floods_view.flood_selected.connect(self._on_visualise_packet_path_info)
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

        # --- VIEW 3: Repeaters & Infrastructure ---
        self.repeaters_view = RepeatersViewWidget(storage=self.storage, config=self.config, radio_driver=self.radio_driver)
        self.repeaters_view.send_command_requested.connect(self._on_send_repeater_command)
        self.repeaters_view.show_neighbors_on_map_requested.connect(self._on_show_repeater_neighbors_on_map)
        self.repeaters_view.show_on_map_requested.connect(self._on_show_contact_on_map)
        self.repeaters_view.track_adsb_requested.connect(self._on_track_node_adsb)
        self.repeaters_view.switch_to_chat_requested.connect(lambda: self.nav_dock.switch_view("main"))
        self.main_stack.addWidget(self.repeaters_view) # Index 2: Repeaters View

        # --- VIEW 4: Settings View (Embedded full-width view) ---
        self.settings_view = SettingsWidget(config=self.config, storage=self.storage, radio_driver=self.radio_driver, parent=self)
        self.settings_view.close_requested.connect(self._close_settings)
        self.main_stack.addWidget(self.settings_view)  # Index 3: Settings View

        main_layout.addWidget(self.main_stack, 1)
        self._update_freshness_btn_state()

        # Splash / Loading Mask Overlay
        self.splash_overlay = SplashOverlay(config=self.config, parent=self)
        self.splash_overlay.dismissed.connect(self._on_splash_dismissed)
        self.splash_overlay.setGeometry(self.rect())
        self.splash_overlay.show()
        if hasattr(self, "mesh_map"):
            self.mesh_map.map_ready.connect(self.splash_overlay.on_map_ready)

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
            self.repeaters_view.reload_repeaters(),
            self.chat_widget.set_target(self.chat_widget.current_channel, self.chat_widget.current_dm)
        ))

    def _on_nav_view_changed(self, view_name: str):
        if view_name == "main":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(True)
            self.main_stack.setCurrentIndex(0)
            self.center_stack.setCurrentIndex(0)
        elif view_name == "floods":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.main_stack.setCurrentIndex(0)
            self.center_stack.setCurrentIndex(2)
            if hasattr(self, "heard_floods_view"):
                self.heard_floods_view.reload()
        elif view_name == "dms":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.dms_view.reload_contacts()
            self.main_stack.setCurrentIndex(1)
        elif view_name == "repeaters":
            if hasattr(self, "sidebar"):
                self.sidebar.setVisible(False)
            self.repeaters_view.reload_repeaters()
            self.main_stack.setCurrentIndex(2)

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
        elif layer_key == "age_fade":
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.node_freshness_fading = is_active
                try:
                    self.config.save()
                except Exception:
                    pass
            self.mesh_map.set_freshness_fading(is_active)
            self._update_freshness_btn_state()

    def _on_visualise_packet_path_info(self, path):
        """Highlights a selected packet path on the map and centers on the originator."""
        if not path:
            return
        if hasattr(self, "mesh_map"):
            self.mesh_map.preview_packet_path(path)
            if getattr(path, "coordinates", None) and len(path.coordinates) > 0:
                lat, lon = path.coordinates[0]
                self.mesh_map.center_on_node(
                    getattr(path, "sender_id", "") or "",
                    lat,
                    lon,
                    getattr(path, "sender_name", "") or ""
                )

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
            try:
                self.config.save()
            except Exception as e:
                logger.debug("Failed saving config on channel select: %s", e)

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

    def _on_contact_selected(self, contact_id: str):
        contact = self.storage.get_contact(contact_id) if self.storage else None
        is_rep = False
        if contact:
            is_rep = contact.is_repeater or "[rep]" in (contact.alias or "").lower()

        if is_rep and contact:
            self.repeater_console.set_repeater(contact)
            self.center_stack.setCurrentIndex(1)
            self.repeaters_view.set_active_repeater(contact)
        else:
            self.current_dm = contact_id
            self.chat_widget.set_target(self.current_channel, contact_id)
            self.composer.set_active_target(self.current_channel, contact_id)
            self.center_stack.setCurrentIndex(0)
            self.dms_view.select_contact(contact_id)

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
        self._update_companion_node_display()
        self._update_freshness_btn_state()
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
        if hasattr(self, "repeaters_view"):
            self.repeaters_view.reload_repeaters()
        self.chat_widget.reload_messages()

    def _open_settings(self):
        """Opens embedded settings view, hiding channels, chat and map."""
        if hasattr(self, "main_stack") and hasattr(self, "settings_view"):
            if self.main_stack.currentIndex() == 3:
                self._close_settings()
            else:
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(False)
                self._previous_view_index = self.main_stack.currentIndex()
                self.settings_view.reload()
                self.main_stack.setCurrentIndex(3)

    def _close_settings(self):
        """Returns to the previous view from the embedded settings view."""
        if hasattr(self, "main_stack"):
            prev_idx = getattr(self, "_previous_view_index", 0)
            if prev_idx == 3:
                prev_idx = 0
            if prev_idx == 0:
                is_main_chat = (not hasattr(self, "center_stack")) or (self.center_stack.currentIndex() == 0)
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(is_main_chat)
            else:
                if hasattr(self, "sidebar"):
                    self.sidebar.setVisible(False)
            self.main_stack.setCurrentIndex(prev_idx)

    def _on_reply_requested(self, reply_prefix: str):
        cur = self.composer.input_field.text()
        if not cur:
            self.composer.input_field.setText(reply_prefix)
        self.composer.focus()

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
            self.mesh_map.display_repeater_neighbors(repeater_contact, neighbors_data)

    def cleanup(self):
        """Releases WebEngine and async resources cleanly before exit."""
        if hasattr(self, "mesh_map") and hasattr(self.mesh_map, "cleanup"):
            self.mesh_map.cleanup()

    def closeEvent(self, event):
        """Creates a safety database backup on window close."""
        try:
            self.config.window_maximized = self.isMaximized()
            if not self.isMaximized():
                self.config.window_width = self.width()
                self.config.window_height = self.height()
            self.config.save()
        except Exception as e:
            logger.debug(f"Failed to persist window state on close: {e}")
        if hasattr(self, "storage") and self.storage:
            try:
                self.storage.backup_database(reason="window_close")
            except Exception as e:
                logger.debug(f"Backup on window close note: {e}")
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

