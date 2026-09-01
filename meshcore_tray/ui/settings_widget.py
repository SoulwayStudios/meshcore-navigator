"""Settings Dialog & Configuration Widget for MeshCore Pixoo Tray."""

import logging
from typing import Dict
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QLineEdit, QComboBox, QSpinBox, QSlider, QCheckBox,
    QPushButton, QGroupBox, QListWidget, QListWidgetItem, QColorDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver

logger = logging.getLogger("meshcore_tray.settings_widget")


class ColorPickerButton(QPushButton):
    """Button showing a color swatch that opens a QColorDialog."""
    color_changed = pyqtSignal(str)

    def __init__(self, initial_hex: str = "#00FFCC", parent=None):
        super().__init__(parent)
        self.current_hex = initial_hex
        self._update_swatch()
        self.clicked.connect(self._open_picker)

    def _update_swatch(self):
        self.setText(self.current_hex.upper())
        self.setStyleSheet(
            f"background-color: {self.current_hex}; color: #000000; "
            f"font-weight: bold; border: 1px solid #FFFFFF; border-radius: 6px; padding: 6px 12px;"
        )

    def _open_picker(self):
        col = QColorDialog.getColor(QColor(self.current_hex), self, "Select Color")
        if col.isValid():
            self.current_hex = col.name()
            self._update_swatch()
            self.color_changed.emit(self.current_hex)


class SettingsDialog(QDialog):
    """Complete Settings window for MeshCore, Pixoo 64, Colors, and Filters."""

    def __init__(self, config: AppConfig, storage=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.storage = storage
        self.setWindowTitle("MeshCore Pixoo Tray - Settings")
        self.resize(650, 520)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        self.tabs = QTabWidget()

        # Tab 1: Node & Radio
        self.tab_node = QWidget()
        self._build_node_tab()
        self.tabs.addTab(self.tab_node, "📡 Node & Radio")

        # Tab 2: Pixoo & Quiet Hours
        self.tab_pixoo = QWidget()
        self._build_pixoo_tab()
        self.tabs.addTab(self.tab_pixoo, "📺 Pixoo & Quiet Hours")

        # Tab 3: Channels & Filters
        self.tab_channels = QWidget()
        self._build_channels_tab()
        self.tabs.addTab(self.tab_channels, "🔀 Channels & Filters")

        # Tab 4: Color Themes
        self.tab_colors = QWidget()
        self._build_colors_tab()
        self.tabs.addTab(self.tab_colors, "🎨 Color Theme")

        # Tab 5: Notifications & Watched Words
        self.tab_notifications = QWidget()
        self._build_notifications_tab()
        self.tabs.addTab(self.tab_notifications, "🔔 Watched Words")

        # Tab 6: Rotations & Gateway
        self.tab_gateway = QWidget()
        self._build_gateway_tab()
        self.tabs.addTab(self.tab_gateway, "🌐 Rotations & Gateway")

        main_layout.addWidget(self.tabs)

        # Bottom Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.btn_save = QPushButton("Save & Apply")
        self.btn_save.setObjectName("primaryButton")
        self.btn_save.clicked.connect(self._save_and_close)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        btn_bar.addWidget(self.btn_cancel)
        btn_bar.addWidget(self.btn_save)
        main_layout.addLayout(btn_bar)

    # --- Tab 1: Node & Radio ---
    def _build_node_tab(self):
        layout = QVBoxLayout(self.tab_node)

        grp_conn = QGroupBox("Heltec V3 Hardware Connection")
        conn_layout = QVBoxLayout(grp_conn)

        # Serial Port
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Serial Port:"))
        self.port_combo = QComboBox()
        self.port_combo.addItem("auto (Auto-detect /dev/ttyUSB*)", "auto")
        self._refresh_serial_ports()
        port_row.addWidget(self.port_combo, 1)

        btn_refresh_ports = QPushButton("🔄 Scan")
        btn_refresh_ports.clicked.connect(self._refresh_serial_ports)
        port_row.addWidget(btn_refresh_ports)
        conn_layout.addLayout(port_row)

        # Baud Rate
        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Baud Rate:"))
        self.baud_combo = QComboBox()
        for b in [115200, 57600, 38400, 19200, 9600, 230400]:
            self.baud_combo.addItem(str(b), b)
        idx = self.baud_combo.findData(self.config.meshcore.baudrate)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)
        baud_row.addWidget(self.baud_combo, 1)
        conn_layout.addLayout(baud_row)

        # Connection Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Connection Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("USB Serial (/dev/ttyUSB*)", "serial")
        self.mode_combo.addItem("Bluetooth Low Energy (BLE)", "ble")
        self.mode_combo.addItem("Mock / Simulator Engine", "mock")
        m_idx = self.mode_combo.findData(self.config.meshcore.connection_type)
        if m_idx >= 0:
            self.mode_combo.setCurrentIndex(m_idx)
        mode_row.addWidget(self.mode_combo, 1)
        conn_layout.addLayout(mode_row)

        layout.addWidget(grp_conn)

        # Node Identifiers
        grp_node = QGroupBox("Node Identity")
        node_layout = QVBoxLayout(grp_node)

        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("Node Callsign / Alias:"))
        self.alias_input = QLineEdit(self.config.meshcore.node_alias)
        id_row.addWidget(self.alias_input)
        node_layout.addLayout(id_row)

        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("Hex Node ID:"))
        self.node_id_input = QLineEdit(self.config.meshcore.node_id)
        hex_row.addWidget(self.node_id_input)
        node_layout.addLayout(hex_row)

        layout.addWidget(grp_node)
        layout.addStretch()

    def _refresh_serial_ports(self):
        current = self.config.meshcore.serial_port
        self.port_combo.clear()
        self.port_combo.addItem("auto (Auto-detect)", "auto")
        ports = MeshCoreDriver.scan_serial_ports()
        for p in ports:
            self.port_combo.addItem(p, p)
        idx = self.port_combo.findData(current)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)

    # --- Tab 2: Pixoo & Quiet Hours ---
    def _build_pixoo_tab(self):
        layout = QVBoxLayout(self.tab_pixoo)

        # Device config
        grp_dev = QGroupBox("Divoom Pixoo 64 Hardware Setup")
        dev_layout = QVBoxLayout(grp_dev)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("Pixoo 64 IP Address:"))
        self.ip_input = QLineEdit(self.config.pixoo.ip_address)
        ip_row.addWidget(self.ip_input, 1)
        dev_layout.addLayout(ip_row)

        bright_row = QHBoxLayout()
        bright_row.addWidget(QLabel("Matrix Brightness:"))
        self.bright_slider = QSlider(Qt.Orientation.Horizontal)
        self.bright_slider.setRange(0, 100)
        self.bright_slider.setValue(self.config.pixoo.brightness)
        bright_row.addWidget(self.bright_slider, 1)
        dev_layout.addLayout(bright_row)

        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Alert Flash Duration (s):"))
        self.alert_dur_spin = QSpinBox()
        self.alert_dur_spin.setRange(3, 60)
        self.alert_dur_spin.setValue(self.config.pixoo.alert_duration_secs)
        dur_row.addWidget(self.alert_dur_spin)

        dur_row.addWidget(QLabel("Flash Cycles:"))
        self.flash_count_spin = QSpinBox()
        self.flash_count_spin.setRange(1, 15)
        self.flash_count_spin.setValue(self.config.pixoo.flash_count)
        dur_row.addWidget(self.flash_count_spin)

        dur_row.addWidget(QLabel("Channel Page Duration (s):"))
        self.page_dur_spin = QSpinBox()
        self.page_dur_spin.setRange(5, 300)
        self.page_dur_spin.setValue(getattr(self.config.pixoo, "page_duration_secs", 30))
        dur_row.addWidget(self.page_dur_spin)
        dev_layout.addLayout(dur_row)

        layout.addWidget(grp_dev)

        # Quiet Hours
        grp_quiet = QGroupBox("Quiet Hours")
        quiet_layout = QVBoxLayout(grp_quiet)

        self.chk_quiet = QCheckBox("Enable Quiet Hours")
        self.chk_quiet.setChecked(self.config.quiet_hours.enabled)
        quiet_layout.addWidget(self.chk_quiet)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Start Time (HH:MM):"))
        self.quiet_start = QLineEdit(self.config.quiet_hours.start_time)
        time_row.addWidget(self.quiet_start)

        time_row.addWidget(QLabel("End Time (HH:MM):"))
        self.quiet_end = QLineEdit(self.config.quiet_hours.end_time)
        time_row.addWidget(self.quiet_end)
        quiet_layout.addLayout(time_row)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Quiet Hours Mode:"))
        self.quiet_mode = QComboBox()
        self.quiet_mode.addItem("Mute Green Flash Strobe Only", "mute_flash")
        self.quiet_mode.addItem("Dim Screen Brightness (30%)", "dim")
        self.quiet_mode.addItem("Turn Off Pixoo Display (Blackout)", "blackout")
        q_idx = self.quiet_mode.findData(self.config.quiet_hours.action)
        if q_idx >= 0:
            self.quiet_mode.setCurrentIndex(q_idx)
        action_row.addWidget(self.quiet_mode, 1)
        quiet_layout.addLayout(action_row)

        layout.addWidget(grp_quiet)
        layout.addStretch()

    # --- Tab 3: Channels & Filters ---
    def _build_channels_tab(self):
        layout = QVBoxLayout(self.tab_channels)

        info_lbl = QLabel(
            "Customize which channels display on the Pixoo 64 screen. "
            "Unchecked channels (e.g. #test) will show in desktop chat but be silenced on the Pixoo."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #8B949E; margin-bottom: 8px;")
        layout.addWidget(info_lbl)

        self.channel_table = QTableWidget()
        self.channel_table.setColumnCount(3)
        self.channel_table.setHorizontalHeaderLabels(["Channel Name", "📺 Show on Pixoo", "⭐ Favorite"])
        self.channel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        # Load channels
        channels = self.storage.get_channels() if self.storage else []
        self.channel_table.setRowCount(len(channels))

        filter_map = self.config.pixoo.channel_filters
        for row, ch in enumerate(channels):
            # Name
            self.channel_table.setItem(row, 0, QTableWidgetItem(f"#{ch.name}"))

            # Pixoo filter
            pixoo_chk = QCheckBox()
            is_enabled = filter_map.get(ch.name, ch.is_pixoo_enabled)
            pixoo_chk.setChecked(is_enabled)
            self.channel_table.setCellWidget(row, 1, pixoo_chk)

            # Favorite
            fav_chk = QCheckBox()
            fav_chk.setChecked(ch.is_favorite or ch.name in self.config.favorites)
            self.channel_table.setCellWidget(row, 2, fav_chk)

        layout.addWidget(self.channel_table)

    # --- Tab 4: Color Themes ---
    def _build_colors_tab(self):
        layout = QVBoxLayout(self.tab_colors)

        info_lbl = QLabel("Customize all 6 color tokens for the Pixoo 64 matrix display:")
        info_lbl.setStyleSheet("color: #8B949E; margin-bottom: 8px;")
        layout.addWidget(info_lbl)

        grid = QVBoxLayout()
        grid.setSpacing(10)

        # Channel Name Color
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Channel Name Color (Top Header / Bottom Bar):"))
        self.btn_col_channel = ColorPickerButton(self.config.pixoo_colors.channel_color)
        row1.addWidget(self.btn_col_channel)
        grid.addLayout(row1)

        # Alert Flash Color
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Alert Flash Color (3-Row Border Strobe):"))
        self.btn_col_alert = ColorPickerButton(self.config.pixoo_colors.alert_color)
        row2.addWidget(self.btn_col_alert)
        grid.addLayout(row2)

        # Message Text Color
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Message Text Color (Vertical / Horizontal):"))
        self.btn_col_msg = ColorPickerButton(self.config.pixoo_colors.message_color)
        row3.addWidget(self.btn_col_msg)
        grid.addLayout(row3)

        # Background Color
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Background Color:"))
        self.btn_col_bg = ColorPickerButton(self.config.pixoo_colors.background_color)
        row4.addWidget(self.btn_col_bg)
        grid.addLayout(row4)

        # Sender Name Color
        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Sender Name Color:"))
        self.btn_col_sender = ColorPickerButton(self.config.pixoo_colors.sender_color)
        row5.addWidget(self.btn_col_sender)
        grid.addLayout(row5)

        # Favorite Star Color
        row6 = QHBoxLayout()
        row6.addWidget(QLabel("Favorite Star (★) Color:"))
        self.btn_col_star = ColorPickerButton(self.config.pixoo_colors.favorite_star_color)
        row6.addWidget(self.btn_col_star)
        grid.addLayout(row6)

        layout.addLayout(grid)
        layout.addStretch()

    # --- Tab 5: Notifications & Watched Words ---
    def _build_notifications_tab(self):
        layout = QVBoxLayout(self.tab_notifications)

        self.chk_desktop_notif = QCheckBox("Show Native Desktop Notifications")
        self.chk_desktop_notif.setChecked(self.config.notifications.desktop_notifications)
        layout.addWidget(self.chk_desktop_notif)

        self.chk_mention_notif = QCheckBox("Alert when Node Callsign / Hex ID is mentioned")
        self.chk_mention_notif.setChecked(self.config.notifications.notify_on_node_mentions)
        layout.addWidget(self.chk_mention_notif)

        # Watched Keywords List
        grp_kw = QGroupBox("Watched Keywords / Emergency Trigger Words")
        kw_layout = QVBoxLayout(grp_kw)

        self.kw_list = QListWidget()
        for kw in self.config.notifications.watched_keywords:
            self.kw_list.addItem(kw)
        kw_layout.addWidget(self.kw_list)

        add_row = QHBoxLayout()
        self.kw_input = QLineEdit()
        self.kw_input.setPlaceholderText("Enter keyword (e.g. storm, repeater, CQ)...")
        self.btn_add_kw = QPushButton("Add Word")
        self.btn_add_kw.clicked.connect(self._add_keyword)
        self.btn_del_kw = QPushButton("Remove Selected")
        self.btn_del_kw.clicked.connect(self._del_keyword)

        add_row.addWidget(self.kw_input, 1)
        add_row.addWidget(self.btn_add_kw)
        add_row.addWidget(self.btn_del_kw)
        kw_layout.addLayout(add_row)

        layout.addWidget(grp_kw)
        layout.addStretch()

    def _add_keyword(self):
        word = self.kw_input.text().strip()
        if word:
            self.kw_list.addItem(word)
            self.kw_input.clear()

    def _del_keyword(self):
        item = self.kw_list.currentItem()
        if item:
            self.kw_list.takeItem(self.kw_list.row(item))

    # --- Tab 6: Rotations & Gateway ---
    def _build_gateway_tab(self):
        layout = QVBoxLayout(self.tab_gateway)

        # Radio Telemetry Rotation
        grp_telem = QGroupBox("Periodic Radio Telemetry Dashboard")
        t_layout = QVBoxLayout(grp_telem)

        self.chk_telem_rot = QCheckBox("Enable Periodic Telemetry Rotation on Pixoo")
        self.chk_telem_rot.setChecked(self.config.telemetry.enabled)
        t_layout.addWidget(self.chk_telem_rot)

        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("Rotate Every (Minutes):"))
        self.telem_interval_spin = QSpinBox()
        self.telem_interval_spin.setRange(1, 120)
        self.telem_interval_spin.setValue(self.config.telemetry.interval_mins)
        t_row.addWidget(self.telem_interval_spin)
        t_layout.addLayout(t_row)

        layout.addWidget(grp_telem)

        # Nearest Neighbours Rotation
        grp_neigh = QGroupBox("Nearest Neighbours & SNR Screen")
        n_layout = QVBoxLayout(grp_neigh)

        self.chk_neigh_rot = QCheckBox("Enable Periodic Neighbours Rotation on Pixoo")
        self.chk_neigh_rot.setChecked(self.config.neighbours.enabled)
        n_layout.addWidget(self.chk_neigh_rot)

        n_row = QHBoxLayout()
        n_row.addWidget(QLabel("Rotate Every (Minutes):"))
        self.neigh_interval_spin = QSpinBox()
        self.neigh_interval_spin.setRange(1, 120)
        self.neigh_interval_spin.setValue(self.config.neighbours.interval_mins)
        n_row.addWidget(self.neigh_interval_spin)
        n_layout.addLayout(n_row)

        rep_row = QHBoxLayout()
        rep_row.addWidget(QLabel("Query Source:"))
        self.neigh_src_combo = QComboBox()
        self.neigh_src_combo.addItem("Local Node (Direct Heard Neighbours)", "local")
        self.neigh_src_combo.addItem("Owned / Trusted Repeater", "repeater")
        s_idx = self.neigh_src_combo.findData(self.config.neighbours.source)
        if s_idx >= 0:
            self.neigh_src_combo.setCurrentIndex(s_idx)
        rep_row.addWidget(self.neigh_src_combo, 1)
        n_layout.addLayout(rep_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Repeater Node ID:"))
        self.target_rep_input = QLineEdit(self.config.neighbours.target_repeater_node_id)
        target_row.addWidget(self.target_rep_input)
        n_layout.addLayout(target_row)

        layout.addWidget(grp_neigh)

        # Local Extensibility Gateway
        grp_gate = QGroupBox("Extensibility & Local REST API Bridge")
        g_layout = QVBoxLayout(grp_gate)

        self.chk_gate = QCheckBox("Enable Local HTTP Bridge (for SDRs & External Scripts)")
        self.chk_gate.setChecked(self.config.gateway.http_bridge_enabled)
        g_layout.addWidget(self.chk_gate)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("HTTP Bridge Port:"))
        self.gate_port_spin = QSpinBox()
        self.gate_port_spin.setRange(1024, 65535)
        self.gate_port_spin.setValue(self.config.gateway.http_port)
        port_row.addWidget(self.gate_port_spin)
        g_layout.addLayout(port_row)

        layout.addWidget(grp_gate)
        layout.addStretch()

    # --- Save & Apply Handler ---
    def _save_and_close(self):
        # 1. Update Node
        self.config.meshcore.serial_port = self.port_combo.currentData() or "auto"
        self.config.meshcore.baudrate = self.baud_combo.currentData() or 115200
        self.config.meshcore.connection_type = self.mode_combo.currentData() or "serial"
        self.config.meshcore.node_alias = self.alias_input.text().strip()
        self.config.meshcore.node_id = self.node_id_input.text().strip()

        # 2. Update Pixoo
        self.config.pixoo.ip_address = self.ip_input.text().strip()
        self.config.pixoo.brightness = self.bright_slider.value()
        self.config.pixoo.alert_duration_secs = self.alert_dur_spin.value()
        self.config.pixoo.flash_count = self.flash_count_spin.value()
        self.config.pixoo.page_duration_secs = self.page_dur_spin.value()

        # Quiet hours
        self.config.quiet_hours.enabled = self.chk_quiet.isChecked()
        self.config.quiet_hours.start_time = self.quiet_start.text().strip()
        self.config.quiet_hours.end_time = self.quiet_end.text().strip()
        self.config.quiet_hours.action = self.quiet_mode.currentData() or "mute_flash"

        # 3. Update Channel Filters & Favorites
        new_filters: Dict[str, bool] = {}
        new_favorites = []
        for row in range(self.channel_table.rowCount()):
            chan_name = self.channel_table.item(row, 0).text().lstrip("#")
            pixoo_chk = self.channel_table.cellWidget(row, 1)
            fav_chk = self.channel_table.cellWidget(row, 2)

            if pixoo_chk and isinstance(pixoo_chk, QCheckBox):
                new_filters[chan_name] = pixoo_chk.isChecked()
            if fav_chk and isinstance(fav_chk, QCheckBox):
                if fav_chk.isChecked():
                    new_favorites.append(chan_name)

        self.config.pixoo.channel_filters = new_filters
        self.config.favorites = new_favorites

        # 4. Update Colors
        self.config.pixoo_colors.channel_color = self.btn_col_channel.current_hex
        self.config.pixoo_colors.alert_color = self.btn_col_alert.current_hex
        self.config.pixoo_colors.message_color = self.btn_col_msg.current_hex
        self.config.pixoo_colors.background_color = self.btn_col_bg.current_hex
        self.config.pixoo_colors.sender_color = self.btn_col_sender.current_hex
        self.config.pixoo_colors.favorite_star_color = self.btn_col_star.current_hex

        # 5. Update Notifications
        self.config.notifications.desktop_notifications = self.chk_desktop_notif.isChecked()
        self.config.notifications.notify_on_node_mentions = self.chk_mention_notif.isChecked()
        kw_list = []
        for i in range(self.kw_list.count()):
            kw_list.append(self.kw_list.item(i).text())
        self.config.notifications.watched_keywords = kw_list

        # 6. Update Rotations & Gateway
        self.config.telemetry.enabled = self.chk_telem_rot.isChecked()
        self.config.telemetry.interval_mins = self.telem_interval_spin.value()
        self.config.neighbours.enabled = self.chk_neigh_rot.isChecked()
        self.config.neighbours.interval_mins = self.neigh_interval_spin.value()
        self.config.neighbours.source = self.neigh_src_combo.currentData() or "local"
        self.config.neighbours.target_repeater_node_id = self.target_rep_input.text().strip()

        self.config.gateway.http_bridge_enabled = self.chk_gate.isChecked()
        self.config.gateway.http_port = self.gate_port_spin.value()

        # Save to disk
        self.config.save()
        bus.emit(EventType.SETTINGS_UPDATED, self.config)
        self.accept()
