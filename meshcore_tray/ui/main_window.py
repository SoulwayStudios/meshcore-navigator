"""Main Modal Window for MeshCore Pixoo System Tray."""

import logging
from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QFrame
)

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.ui.chat_widget import ChatWidget
from meshcore_tray.ui.composer import PowerComposer
from meshcore_tray.ui.sidebar import Sidebar
from meshcore_tray.ui.pixoo_preview import PixooPreviewWidget
from meshcore_tray.ui.settings_widget import SettingsDialog

logger = logging.getLogger("meshcore_tray.main_window")


class MainWindow(QMainWindow):
    """Primary application window with Sidebar, Chat, Power Composer, and Pixoo Mirror."""

    def __init__(self, config: AppConfig, storage=None, radio_driver=None, pixoo_service=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.storage = storage
        self.radio_driver = radio_driver
        self.pixoo_service = pixoo_service
        self.current_channel = "Public"
        self.current_dm: Optional[str] = None

        self.setWindowTitle("MeshCore & Pixoo 64 - Control Center")
        self.resize(1120, 690)
        self.setMinimumSize(880, 520)

        self._init_ui()
        self._setup_event_subscriptions()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # Top Control & Navigation Bar
        top_bar = QFrame()
        top_bar.setStyleSheet("background-color: #161B22; border: 1px solid #21262D; border-radius: 8px;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 6, 12, 6)
        top_layout.setSpacing(8)

        logo_lbl = QLabel("<b>⚡ MESHCORE</b> <span style='color:#00E5FF;'>PIXOO 64</span>")
        logo_lbl.setStyleSheet("font-size: 14px; letter-spacing: 0.5px;")
        top_layout.addWidget(logo_lbl)
        top_layout.addStretch()

        # Simulation Buttons (Alice, Bob, and Charlie Long Message)
        self.btn_sim_alice = QPushButton("🧪 Msg: Alice")
        self.btn_sim_alice.setToolTip("Simulate message from Alice on active channel")
        self.btn_sim_alice.clicked.connect(self._simulate_alice_message)
        top_layout.addWidget(self.btn_sim_alice)

        self.btn_sim_bob = QPushButton("🧪 Msg: Bob")
        self.btn_sim_bob.setToolTip("Simulate message from a 2nd user (Bob) to test grouping & alternating colors")
        self.btn_sim_bob.clicked.connect(self._simulate_bob_message)
        top_layout.addWidget(self.btn_sim_bob)

        self.btn_sim_long = QPushButton("📜 Long Msg (Bounce Test)")
        self.btn_sim_long.setToolTip("Simulate long multi-line message to test vertical bounce reading animation")
        self.btn_sim_long.clicked.connect(self._simulate_long_bounce_message)
        top_layout.addWidget(self.btn_sim_long)

        self.btn_settings = QPushButton("⚙️ Settings")
        self.btn_settings.setObjectName("accentButton")
        self.btn_settings.clicked.connect(self._open_settings)
        top_layout.addWidget(self.btn_settings)

        main_layout.addWidget(top_bar)

        # 3-Pane Splitter: Sidebar (Left) | Chat & Composer (Center) | Pixoo Preview (Right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. Left Sidebar
        self.sidebar = Sidebar(storage=self.storage, config=self.config)
        self.sidebar.channel_selected.connect(self._on_channel_selected)
        self.sidebar.contact_selected.connect(self._on_contact_selected)
        splitter.addWidget(self.sidebar)

        # 2. Center Chat & Composer
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)

        self.chat_widget = ChatWidget(storage=self.storage)
        center_layout.addWidget(self.chat_widget, 1)

        self.composer = PowerComposer(storage=self.storage, config=self.config)
        self.composer.send_message.connect(self._on_send_message)
        self.composer.switch_channel.connect(self._on_channel_selected)
        self.composer.search_query.connect(self._on_search_query)
        self.composer.search_cleared.connect(self._on_search_cleared)
        center_layout.addWidget(self.composer)

        splitter.addWidget(center_widget)

        # 3. Right Pixoo Preview Panel
        self.pixoo_panel = PixooPreviewWidget(pixoo_service=self.pixoo_service)
        splitter.addWidget(self.pixoo_panel)

        # Splitter proportion ratios (Sidebar 220px, Chat 540px, Pixoo 320px)
        splitter.setSizes([220, 540, 320])
        main_layout.addWidget(splitter, 1)

    def _setup_event_subscriptions(self):
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_bus_message_received)
        bus.subscribe(EventType.MESSAGE_SENT, self._on_bus_message_sent)
        bus.subscribe(EventType.CONNECTION_STATUS_CHANGED, self._on_connection_changed)
        bus.subscribe(EventType.SETTINGS_UPDATED, self._on_settings_updated)

    def _on_channel_selected(self, channel_name: str):
        self.current_channel = channel_name
        self.current_dm = None
        self.chat_widget.set_target(channel_name, None)
        self.composer.set_active_target(channel_name, None)
        if self.pixoo_service:
            self.pixoo_service.renderer.set_current_channel(channel_name)

    def _on_contact_selected(self, contact_id: str):
        self.current_dm = contact_id
        self.chat_widget.set_target(self.current_channel, contact_id)
        self.composer.set_active_target(self.current_channel, contact_id)

    def _on_send_message(self, channel: str, recipient_id: Optional[str], text: str):
        if recipient_id:
            if self.radio_driver:
                self.radio_driver.send_direct_message(recipient_id, text)
        else:
            if self.radio_driver:
                self.radio_driver.send_channel_message(channel, text)

    def _on_search_query(self, query: str):
        self.chat_widget.show_search_results(query)

    def _on_search_cleared(self):
        self.chat_widget.set_target(self.current_channel, self.current_dm)

    def _on_bus_message_received(self, msg: MessageEnvelope):
        self.chat_widget.add_message(msg)
        self.sidebar.reload()

    def _on_bus_message_sent(self, msg: MessageEnvelope):
        self.chat_widget.add_message(msg)
        self.sidebar.reload()

    def _on_connection_changed(self, status: dict):
        connected = status.get("connected", False)
        port = status.get("port", "")
        mode = status.get("mode", "serial")
        self.sidebar.update_connection_status(connected, port, mode)

    def _on_settings_updated(self, config: AppConfig):
        self.config = config
        self.sidebar.reload()
        self.chat_widget.reload_messages()

    def _open_settings(self):
        dlg = SettingsDialog(self.config, storage=self.storage, parent=self)
        dlg.exec()

    def _simulate_alice_message(self):
        """Simulate message from Alice."""
        sim_msg = MessageEnvelope(
            id=f"sim-alice-{id(self)}-{int(bus._loop.time() if bus._loop else 0)}",
            sender_name="Alice",
            sender_id="!8f3a",
            channel=self.current_channel,
            text=f"Alice here on #{self.current_channel}! Heltec node operational.",
            is_favorite=True,
            metadata={"snr": 10.5, "rssi": -78.0}
        )
        if self.storage:
            self.storage.save_message(sim_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, sim_msg)

    def _simulate_bob_message(self):
        """Simulate message from 2nd user (Bob)."""
        sim_msg = MessageEnvelope(
            id=f"sim-bob-{id(self)}-{int(bus._loop.time() if bus._loop else 0)}",
            sender_name="Bob_Node",
            sender_id="!9c21",
            channel=self.current_channel,
            text="Bob check-in: Signal 5/9 loud and clear across the valley.",
            is_favorite=False,
            metadata={"snr": 6.2, "rssi": -88.0}
        )
        if self.storage:
            self.storage.save_message(sim_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, sim_msg)

    def _simulate_long_bounce_message(self):
        """Simulate long multi-line message to demonstrate slow vertical bounce reading."""
        sim_msg = MessageEnvelope(
            id=f"sim-charlie-{id(self)}-{int(bus._loop.time() if bus._loop else 0)}",
            sender_name="Charlie_Base",
            sender_id="!10a4",
            channel=self.current_channel,
            text="Field report: Weather station update on mountain pass. Wind 25 knots gusting 40. Telemetry repeater active on frequency 868.125 MHz.",
            is_favorite=False,
            metadata={"snr": 8.0, "rssi": -82.0}
        )
        if self.storage:
            self.storage.save_message(sim_msg)
        bus.emit(EventType.MESSAGE_RECEIVED, sim_msg)
