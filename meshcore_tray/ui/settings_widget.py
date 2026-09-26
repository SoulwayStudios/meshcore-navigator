"""Settings Dialog & Configuration Widget for MeshCore Pixoo Tray."""

import logging
import os
from typing import Any, Dict, List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget,
    QScrollArea, QFrame, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QPushButton, QListWidget, QListWidgetItem,
    QColorDialog, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QApplication
)
from meshcore_tray import __version__, __coffee_url__
from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.core.version_checker import VersionChecker, ReleaseInfo, GITHUB_RELEASES_PAGE

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
        col = QColor(self.current_hex)
        lum = (0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()) / 255.0
        txt_col = "#000000" if lum > 0.55 else "#FFFFFF"
        self.setStyleSheet(
            f"background-color: {self.current_hex}; color: {txt_col}; "
            f"font-weight: bold; border: 1px solid rgba(255, 255, 255, 0.35); "
            f"border-radius: 6px; padding: 6px 14px; min-width: 85px;"
        )

    def set_color(self, hex_code: str):
        if hex_code:
            self.current_hex = hex_code
            self._update_swatch()

    def _open_picker(self):
        col = QColorDialog.getColor(QColor(self.current_hex), self, "Select Color")
        if col.isValid():
            self.current_hex = col.name()
            self._update_swatch()
            self.color_changed.emit(self.current_hex)



class SettingsCard(QFrame):
    """High-contrast outlined card container for settings sections."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.card_layout = QVBoxLayout(self)
        self.card_layout.setContentsMargins(16, 14, 16, 16)
        self.card_layout.setSpacing(10)

        title_lbl = QLabel(title.upper())
        title_lbl.setObjectName("cardTitle")
        self.card_layout.addWidget(title_lbl)

    def add_row(self, label_text: str, widget: QWidget, label_width: int = 150) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        self.card_layout.addLayout(row)
        return row

    def add_widget(self, widget: QWidget):
        self.card_layout.addWidget(widget)

    def add_layout(self, layout):
        self.card_layout.addLayout(layout)


class SettingsWidget(QWidget):
    """Complete Settings view for MeshCore, Pixoo 64, Colors, and Filters."""
    close_requested = pyqtSignal()

    def __init__(self, config: AppConfig, storage=None, radio_driver=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.storage = storage
        self.radio_driver = radio_driver
        self.setObjectName("settingsView")
        self.setWindowTitle("MeshCore Pixoo Tray - Settings")
        self.resize(880, 760)
        self.setStyleSheet("""
            SettingsWidget, SettingsDialog, QDialog, QWidget#settingsView {
                background-color: #1C1C1C;
                color: #E5E7EB;
            }
            QStackedWidget { background-color: #1C1C1C; border: none; }
            QStackedWidget > QWidget { background-color: #1C1C1C; }
            QScrollArea { background-color: #1C1C1C; border: none; }
            QScrollArea > QWidget > QWidget { background-color: #1C1C1C; }
            QWidget#settingsScrollViewport { background-color: #1C1C1C; }

            /* Scroll areas: transparent viewport so card backgrounds show */
            QScrollArea#settingsScroll {
                background-color: #1C1C1C;
                border: none;
            }

            /* Section cards: prominent, high-contrast containers */
            QFrame#settingsCard {
                background-color: #222327;
                border: 1.5px solid #414143;
                border-radius: 8px;
                margin-bottom: 8px;
            }
            QLabel#cardTitle {
                color: #60A5FA;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 0.6px;
                padding-bottom: 6px;
                border-bottom: 1px solid #414143;
                margin-bottom: 4px;
                background: transparent;
                background-color: transparent;
            }

            /* Labels: Soft muted gray for instant readability */
            QLabel {
                color: #9CA3AF;
                font-size: 12px;
                font-weight: 500;
                background: transparent;
                background-color: transparent;
            }

            /* Checkboxes and Radio buttons: transparent background */
            QCheckBox, QRadioButton {
                background: transparent;
                background-color: transparent;
                color: #E5E7EB;
            }

            QFrame#settingsCard QLabel,
            QFrame#settingsCard QCheckBox,
            QFrame#settingsCard QRadioButton {
                background: transparent;
                background-color: transparent;
            }

            /* Inputs: Dark recessed background with crisp outline borders */
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #1C1C1C;
                color: #FFFFFF;
                border: 1.5px solid #414143;
                border-radius: 6px;
                padding: 5px 10px;
                min-height: 28px;
                font-size: 13px;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
                border: 1.5px solid #60A5FA;
                background-color: #13161C;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1.5px solid #34D399;
                background-color: #0B0D10;
            }
            QComboBox QAbstractItemView {
                background-color: #222327;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 6px;
                selection-background-color: #3B82F6;
                selection-color: #FFFFFF;
                padding: 4px;
                outline: none;
            }

            /* Horizontal Sliders */
            QSlider::groove:horizontal {
                height: 6px;
                background: #2D313A;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #34D399;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid #34D399;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }

            /* Table Widget */
            QTableWidget {
                background-color: #1C1C1C;
                border: 1px solid #414143;
                border-radius: 6px;
                gridline-color: #2D313A;
            }
            QHeaderView::section {
                background-color: #222327;
                color: #E5E7EB;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #414143;
                font-weight: bold;
            }

            /* Sleek Modern Scrollbar */
            QScrollBar:vertical {
                background-color: #1C1C1C;
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background-color: #414143;
                min-height: 24px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #4B5565;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            /* Buttons */
            QPushButton {
                background-color: #262B36;
                color: #E5E7EB;
                border: 1px solid #4B5565;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #353D4B;
                color: #FFFFFF;
            }
            QPushButton#secondaryButton {
                background-color: #262B36;
                color: #E5E7EB;
                border: 1px solid #4B5565;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
            }
            QPushButton#secondaryButton:hover {
                background-color: #353D4B;
                color: #FFFFFF;
            }
            QPushButton#applyButton {
                background-color: #1F3A5C;
                color: #58A6FF;
                border: 1px solid #388BFD;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
            }
            QPushButton#applyButton:hover {
                background-color: #264B78;
                color: #FFFFFF;
            }
            QPushButton#actionButton {
                background-color: #1E3A5F;
                color: #60A5FA;
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: bold;
            }
            QPushButton#actionButton:hover {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            QPushButton#primaryButton {
                background-color: #238636;
                color: #FFFFFF;
                border: 1px solid #2EA043;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
            }
            QPushButton#primaryButton:hover {
                background-color: #2EA043;
            }
        """)
        self._init_ui()

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("settingsScroll")
        scroll.viewport().setObjectName("settingsScrollViewport")
        scroll.setWidget(widget)
        return scroll

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # Main horizontal split: Nav pane on left, stacked views on right
        body_layout = QHBoxLayout()
        body_layout.setSpacing(14)

        # Left Navigation List
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(210)
        self.nav_list.setObjectName("settingsNavList")
        self.nav_list.setStyleSheet("""
            #settingsNavList {
                background-color: #222327;
                border: 1px solid #414143;
                border-radius: 8px;
                padding: 6px;
                outline: none;
            }
            #settingsNavList::item {
                color: #9CA3AF;
                padding: 10px 14px;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
                margin-bottom: 4px;
            }
            #settingsNavList::item:hover {
                background-color: #2B303C;
                color: #E5E7EB;
            }
            #settingsNavList::item:selected {
                background-color: #414143;
                color: #FFFFFF;
                font-weight: bold;
                border-left: 3px solid #34D399;
            }
        """)

        categories = [
            ("📡 Radio & Node", 0),
            ("🖼️ Pixoo Integration", 1),
            ("🔀 Channels & Filters", 2),
            ("🎨 App UI Colors & Map Tiles", 3),
            ("🌈 Pixoo Matrix Colors", 4),
            ("🔔 Watched Words & Alerts", 5),
            ("🌐 Gateway & Telemetry", 6),
            ("ℹ️ About & Support", 7),
        ]
        for name, idx in categories:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.nav_list.addItem(item)

        # Right Stacked Content Pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._wrap_scroll(self._build_node_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_pixoo_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_channels_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_app_colors_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_colors_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_notifications_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_gateway_tab()))
        self.stack.addWidget(self._wrap_scroll(self._build_about_tab()))

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav_list.setCurrentRow(0)

        body_layout.addWidget(self.nav_list)
        body_layout.addWidget(self.stack, 1)
        main_layout.addLayout(body_layout, 1)

        # Bottom Action Bar with Cancel, Apply, and Save Changes
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet("border-top: 1px solid #414143; padding-top: 8px;")
        btn_bar = QHBoxLayout(bottom_bar)
        btn_bar.setContentsMargins(0, 4, 0, 0)

        self.btn_coffee = QPushButton("☕ Buy Me a Coffee")
        self.btn_coffee.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_coffee.setToolTip("Support development of MESHCORE NAVIGATOR (by Nicky Proniewicz - M7NCY)")
        self.btn_coffee.setStyleSheet("""
            QPushButton {
                background-color: #FFDD00;
                color: #000000;
                border: 1px solid #E6C600;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #FFE633;
                border-color: #FFDD00;
            }
            QPushButton:pressed {
                background-color: #E6C600;
            }
        """)
        self.btn_coffee.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(__coffee_url__)))
        btn_bar.addWidget(self.btn_coffee)

        self.apply_status_lbl = QLabel("")
        self.apply_status_lbl.setStyleSheet("color: #34D399; font-size: 12px; font-weight: bold;")
        btn_bar.addWidget(self.apply_status_lbl)
        btn_bar.addStretch()

        self.btn_cancel = QPushButton("← Back to Chat && Map")
        self.btn_cancel.setObjectName("secondaryButton")
        self.btn_cancel.setToolTip("Return to Chat and Mesh Map views")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("applyButton")
        self.btn_apply.clicked.connect(lambda: self._apply_settings(close_on_finish=False))

        self.btn_save = QPushButton("Save Changes")
        self.btn_save.setObjectName("primaryButton")
        self.btn_save.clicked.connect(lambda: self._apply_settings(close_on_finish=True))

        btn_bar.addWidget(self.btn_cancel)
        btn_bar.addWidget(self.btn_apply)
        btn_bar.addWidget(self.btn_save)
        main_layout.addWidget(bottom_bar)

        bus.subscribe(EventType.HARDWARE_CONTACTS_UPDATED, self._on_hardware_contacts_updated)

    # --- Tab 1: Node & Radio ---
    def _build_node_tab(self):
        self.tab_node = QWidget()
        layout = QVBoxLayout(self.tab_node)
        layout.setSpacing(12)

        # 1. Connection Card
        card_conn = SettingsCard("📡 Heltec V3 Hardware Connection")
        port_row = QHBoxLayout()
        lbl_port = QLabel("Serial Port:")
        lbl_port.setFixedWidth(130)
        port_row.addWidget(lbl_port)
        self.port_combo = QComboBox()
        self.port_combo.addItem("auto (Auto-detect)", "auto")
        self._refresh_serial_ports()
        port_row.addWidget(self.port_combo, 1)

        btn_refresh_ports = QPushButton("🔄 Scan")
        btn_refresh_ports.setObjectName("secondaryButton")
        btn_refresh_ports.clicked.connect(self._refresh_serial_ports)
        port_row.addWidget(btn_refresh_ports)
        card_conn.add_layout(port_row)

        bm_row = QHBoxLayout()
        lbl_baud = QLabel("Baud Rate:")
        lbl_baud.setFixedWidth(130)
        bm_row.addWidget(lbl_baud)
        self.baud_combo = QComboBox()
        for b in [115200, 57600, 38400, 19200, 9600, 230400]:
            self.baud_combo.addItem(str(b), b)
        idx = self.baud_combo.findData(self.config.meshcore.baudrate)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)
        bm_row.addWidget(self.baud_combo, 1)

        lbl_mode = QLabel("Mode:")
        lbl_mode.setFixedWidth(50)
        bm_row.addWidget(lbl_mode)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("USB Serial (COM / tty)", "serial")
        self.mode_combo.addItem("Bluetooth Low Energy (BLE)", "ble")
        self.mode_combo.addItem("Mock / Simulator Engine", "mock")
        m_idx = self.mode_combo.findData(self.config.meshcore.connection_type)
        if m_idx >= 0:
            self.mode_combo.setCurrentIndex(m_idx)
        bm_row.addWidget(self.mode_combo, 1)
        card_conn.add_layout(bm_row)
        layout.addWidget(card_conn)

        # 2. LoRa Radio RF Parameters & Regional Presets
        card_radio = SettingsCard("📻 LoRa Radio Frequency & Regional Presets")
        preset_row = QHBoxLayout()
        lbl_preset = QLabel("<b>Radio Preset:</b>")
        lbl_preset.setFixedWidth(130)
        preset_row.addWidget(lbl_preset)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("UK Narrow (869.618 MHz, 62.5 kHz, SF8, CR 4/5)", "uk_narrow")
        self.preset_combo.addItem("UK / EU Medium (869.525 MHz, 125.0 kHz, SF8, CR 4/5)", "uk_medium")
        self.preset_combo.addItem("EU 868 Standard (868.125 MHz, 250.0 kHz, SF7, CR 4/5)", "eu_868")
        self.preset_combo.addItem("EU 868 Long Fast (868.125 MHz, 250.0 kHz, SF11, CR 4/5)", "eu_long_fast")
        self.preset_combo.addItem("US 915 Standard (915.000 MHz, 250.0 kHz, SF7, CR 4/5)", "us_915")
        self.preset_combo.addItem("EU 433 Standard (433.175 MHz, 125.0 kHz, SF7, CR 4/5)", "eu_433")
        self.preset_combo.addItem("Custom / Manual Configuration", "custom")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        card_radio.add_layout(preset_row)

        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("Frequency (MHz):"), 0, 0)
        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(400.0, 1000.0)
        self.freq_spin.setDecimals(3)
        self.freq_spin.setSingleStep(0.025)
        self.freq_spin.setValue(self.config.meshcore.frequency_mhz)
        self.freq_spin.valueChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.freq_spin, 0, 1)

        grid.addWidget(QLabel("Bandwidth (kHz):"), 0, 2)
        self.bw_combo = QComboBox()
        for bw in [62.5, 125.0, 250.0, 500.0]:
            self.bw_combo.addItem(f"{bw} kHz", bw)
        b_idx = self.bw_combo.findData(self.config.meshcore.bandwidth_khz)
        if b_idx >= 0:
            self.bw_combo.setCurrentIndex(b_idx)
        self.bw_combo.currentIndexChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.bw_combo, 0, 3)

        grid.addWidget(QLabel("Spreading Factor:"), 1, 0)
        self.sf_combo = QComboBox()
        for sf in [7, 8, 9, 10, 11, 12]:
            self.sf_combo.addItem(f"SF{sf}", sf)
        s_idx = self.sf_combo.findData(self.config.meshcore.spreading_factor)
        if s_idx >= 0:
            self.sf_combo.setCurrentIndex(s_idx)
        self.sf_combo.currentIndexChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.sf_combo, 1, 1)

        grid.addWidget(QLabel("Coding Rate:"), 1, 2)
        self.cr_combo = QComboBox()
        for cr in ["4/5", "4/6", "4/7", "4/8"]:
            self.cr_combo.addItem(f"{cr} (CR {cr[-1]})", cr)
        c_idx = self.cr_combo.findData(self.config.meshcore.coding_rate)
        if c_idx >= 0:
            self.cr_combo.setCurrentIndex(c_idx)
        self.cr_combo.currentIndexChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.cr_combo, 1, 3)

        grid.addWidget(QLabel("TX Power (dBm):"), 2, 0)
        self.tx_spin = QSpinBox()
        self.tx_spin.setRange(1, 30)
        self.tx_spin.setValue(self.config.meshcore.tx_power_dbm)
        self.tx_spin.valueChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.tx_spin, 2, 1)

        grid.addWidget(QLabel("Byte Path Mode:"), 2, 2)
        self.path_mode_combo = QComboBox()
        self.path_mode_combo.addItem("1-Byte Path (Mode 0 - Standard UK)", 0)
        self.path_mode_combo.addItem("2-Byte Path (Mode 1 - Multibyte 2B)", 1)
        self.path_mode_combo.addItem("3-Byte Path (Mode 2 - Multibyte 3B)", 2)
        p_idx = self.path_mode_combo.findData(getattr(self.config.meshcore, "path_hash_mode", 0))
        if p_idx >= 0:
            self.path_mode_combo.setCurrentIndex(p_idx)
        self.path_mode_combo.currentIndexChanged.connect(self._on_custom_field_changed)
        grid.addWidget(self.path_mode_combo, 2, 3)

        btn_prog = QPushButton("⚡ Program Radio Node Now")
        btn_prog.setObjectName("actionButton")
        btn_prog.clicked.connect(self._apply_radio_to_hardware)
        grid.addWidget(btn_prog, 3, 0, 1, 4)

        card_radio.add_layout(grid)

        self.lbl_radio_status = QLabel("")
        self.lbl_radio_status.setStyleSheet("color: #3FB950; font-size: 11px;")
        card_radio.add_widget(self.lbl_radio_status)
        layout.addWidget(card_radio)

        # 3. MeshCore Protocol & Node Policies
        card_proto = SettingsCard("⚙️ MeshCore Protocol & Radio Policies")
        self.chk_autoadd = QCheckBox("Auto-add newly overheard node adverts to radio contact table")
        self.chk_autoadd.setChecked(getattr(self.config.meshcore, "autoadd_contacts", True))
        card_proto.add_row("Auto-Add Adverts:", self.chk_autoadd, 160)

        self.chk_auto_prune_hardware = QCheckBox("Automatically prune oldest non-favorite contacts from radio hardware flash (prevents table full)")
        self.chk_auto_prune_hardware.setChecked(getattr(self.config.meshcore, "auto_prune_hardware_contacts", True))
        card_proto.add_row("Auto-Prune Flash:", self.chk_auto_prune_hardware, 160)

        hw_widget = QWidget()
        hw_box = QHBoxLayout(hw_widget)
        hw_box.setContentsMargins(0, 0, 0, 0)
        hw_count = getattr(self.radio_driver, "hardware_contacts_count", 0) if self.radio_driver else 0
        hw_limit = getattr(self.config.meshcore, "hardware_contact_limit", 64)
        self.lbl_hw_capacity = QLabel(f"📻 Flash Table: {hw_count} / {hw_limit} slots used")
        self.lbl_hw_capacity.setStyleSheet("color: #FBBF24; font-size: 11px; font-weight: 600;")
        hw_box.addWidget(self.lbl_hw_capacity)

        self.btn_prune_hw_contacts = QPushButton("🧹 Prune Stale Contacts on Radio Flash")
        self.btn_prune_hw_contacts.setToolTip("Safely removes oldest non-favorite contacts from the Heltec V3's hardware memory so new contacts can be learned.\nIMPORTANT: Pruned contacts remain 100% saved in your application database and map!")
        self.btn_prune_hw_contacts.setStyleSheet("background-color: #2D3340; color: #F2F3F5; padding: 4px 10px; font-size: 11px; border-radius: 4px;")
        self.btn_prune_hw_contacts.clicked.connect(self._on_prune_hardware_clicked)
        hw_box.addWidget(self.btn_prune_hw_contacts)
        hw_box.addStretch()
        card_proto.add_row("Hardware Capacity:", hw_widget, 160)

        self.loc_policy_combo = QComboBox()
        self.loc_policy_combo.addItem("Precise Coordinates (Full GPS Broadcast)", 0)
        self.loc_policy_combo.addItem("Approximate / Low Precision Location", 1)
        self.loc_policy_combo.addItem("Private / Do Not Share Coordinates", 2)
        lp_idx = self.loc_policy_combo.findData(getattr(self.config.meshcore, "advert_loc_policy", 0))
        if lp_idx >= 0:
            self.loc_policy_combo.setCurrentIndex(lp_idx)
        card_proto.add_row("Advert Location Policy:", self.loc_policy_combo, 160)

        self.chk_multi_acks = QCheckBox("Enable redundant multi-ACK packet retries")
        self.chk_multi_acks.setChecked(getattr(self.config.meshcore, "multi_acks", False))
        card_proto.add_row("Multi-ACK Retries:", self.chk_multi_acks, 160)

        self.rx_delay_spin = QSpinBox()
        self.rx_delay_spin.setRange(0, 1000)
        self.rx_delay_spin.setSuffix(" ms")
        self.rx_delay_spin.setValue(getattr(self.config.meshcore, "rx_delay_ms", 0))
        card_proto.add_row("RX Tuning Delay:", self.rx_delay_spin, 160)

        layout.addWidget(card_proto)

        # 4. Node Identity & Home Station Coordinates
        card_id = SettingsCard("🆔 Node Identity & Home Station Location")
        self.alias_input = QLineEdit(self.config.meshcore.node_alias)
        card_id.add_row("Node Callsign / Alias:", self.alias_input, 140)

        self.node_id_input = QLineEdit(self.config.meshcore.node_id)
        card_id.add_row("Hex Node ID:", self.node_id_input, 140)

        coords_box = QHBoxLayout()
        coords_lbl = QLabel("Home GPS (Lat, Lon):")
        coords_lbl.setFixedWidth(140)
        coords_box.addWidget(coords_lbl)

        self.home_lat_input = QDoubleSpinBox()
        self.home_lat_input.setRange(-90.0, 90.0)
        self.home_lat_input.setDecimals(5)
        self.home_lat_input.setSingleStep(0.001)
        self.home_lat_input.setValue(self.config.meshcore.latitude if self.config.meshcore.latitude is not None else 54.65897)
        coords_box.addWidget(self.home_lat_input, 1)

        self.home_lon_input = QDoubleSpinBox()
        self.home_lon_input.setRange(-180.0, 180.0)
        self.home_lon_input.setDecimals(5)
        self.home_lon_input.setSingleStep(0.001)
        self.home_lon_input.setValue(self.config.meshcore.longitude if self.config.meshcore.longitude is not None else -3.4346)
        coords_box.addWidget(self.home_lon_input, 1)

        card_id.add_layout(coords_box)
        layout.addWidget(card_id)

        layout.addStretch()
        return self.tab_node

    def _on_preset_changed(self):
        preset_key = self.preset_combo.currentData()
        presets = {
            "uk_narrow": {"freq": 869.618, "bw": 62.5, "sf": 8, "cr": "4/5", "tx": 22},
            "uk_medium": {"freq": 869.525, "bw": 125.0, "sf": 8, "cr": "4/5", "tx": 27},
            "eu_868": {"freq": 868.125, "bw": 250.0, "sf": 7, "cr": "4/5", "tx": 14},
            "eu_long_fast": {"freq": 868.125, "bw": 250.0, "sf": 11, "cr": "4/5", "tx": 14},
            "us_915": {"freq": 915.000, "bw": 250.0, "sf": 7, "cr": "4/5", "tx": 20},
            "eu_433": {"freq": 433.175, "bw": 125.0, "sf": 7, "cr": "4/5", "tx": 10},
        }
        if preset_key in presets:
            p = presets[preset_key]
            self.freq_spin.blockSignals(True)
            self.bw_combo.blockSignals(True)
            self.sf_combo.blockSignals(True)
            self.cr_combo.blockSignals(True)
            self.tx_spin.blockSignals(True)

            self.freq_spin.setValue(p["freq"])
            b_idx = self.bw_combo.findData(p["bw"])
            if b_idx >= 0:
                self.bw_combo.setCurrentIndex(b_idx)
            s_idx = self.sf_combo.findData(p["sf"])
            if s_idx >= 0:
                self.sf_combo.setCurrentIndex(s_idx)
            c_idx = self.cr_combo.findData(p["cr"])
            if c_idx >= 0:
                self.cr_combo.setCurrentIndex(c_idx)
            self.tx_spin.setValue(p["tx"])

            self.freq_spin.blockSignals(False)
            self.bw_combo.blockSignals(False)
            self.sf_combo.blockSignals(False)
            self.cr_combo.blockSignals(False)
            self.tx_spin.blockSignals(False)

    def _on_custom_field_changed(self):
        current_preset = self.preset_combo.currentData()
        if current_preset != "custom":
            pass

    def _apply_radio_to_hardware(self):
        freq = self.freq_spin.value()
        bw = self.bw_combo.currentData() or 62.5
        sf = self.sf_combo.currentData() or 8
        cr = self.cr_combo.currentData() or "4/5"
        tx = self.tx_spin.value()
        preset = self.preset_combo.currentText()
        path_mode = self.path_mode_combo.currentData()
        if path_mode is None:
            path_mode = 0

        autoadd = self.chk_autoadd.isChecked()
        loc_policy = self.loc_policy_combo.currentData() or 0
        multi_acks = self.chk_multi_acks.isChecked()
        rx_dly = self.rx_delay_spin.value()

        self.config.meshcore.radio_preset = preset
        self.config.meshcore.frequency_mhz = freq
        self.config.meshcore.bandwidth_khz = bw
        self.config.meshcore.spreading_factor = sf
        self.config.meshcore.coding_rate = cr
        self.config.meshcore.tx_power_dbm = tx
        self.config.meshcore.path_hash_mode = path_mode
        self.config.meshcore.autoadd_contacts = autoadd
        if hasattr(self, "chk_auto_prune_hardware"):
            self.config.meshcore.auto_prune_hardware_contacts = self.chk_auto_prune_hardware.isChecked()
        self.config.meshcore.advert_loc_policy = loc_policy
        self.config.meshcore.multi_acks = multi_acks
        self.config.meshcore.rx_delay_ms = rx_dly

        if self.radio_driver:
            self.radio_driver.set_radio_params(freq, bw, sf, cr, tx, path_hash_mode=path_mode)
            self.radio_driver.set_autoadd_contacts(autoadd)
            self.radio_driver.set_advert_location_policy(loc_policy)
            self.radio_driver.set_multi_acks(multi_acks)
            self.radio_driver.set_tuning_params(rx_delay_ms=rx_dly)
            p_desc = f"{path_mode+1}B Path"
            self.lbl_radio_status.setText(f"✓ Transmitted ({freq} MHz, SF{sf}, {bw} kHz, {tx} dBm, {p_desc})")
            self.lbl_radio_status.setStyleSheet("color: #3FB950; font-weight: bold;")
        else:
            self.lbl_radio_status.setText("✓ Saved (Offline)")
            self.lbl_radio_status.setStyleSheet("color: #58A6FF; font-weight: bold;")

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

    def _on_hardware_contacts_updated(self, data: dict):
        if not hasattr(self, "lbl_hw_capacity"):
            return
        count = data.get("count", 0)
        limit = data.get("limit", 64)
        self.lbl_hw_capacity.setText(f"📻 Flash Table: {count} / {limit} slots used")

    def _on_prune_hardware_clicked(self):
        if not self.radio_driver or not self.radio_driver.is_connected():
            QMessageBox.information(self, "Radio Offline", "Cannot prune hardware contacts: radio is not connected.")
            return
        res = QMessageBox.question(
            self,
            "Prune Radio Hardware Contacts",
            "Are you sure you want to prune stale contacts from your physical Heltec V3's flash memory?\n\n"
            "• This frees up slots on the radio hardware so new contacts can be discovered over RF.\n"
            "• Favorites, repeaters, room servers, and contacts heard in the last 48h are protected.\n"
            "• IMPORTANT: All contacts remain 100% saved in your application database and on your map.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if res == QMessageBox.StandardButton.Yes:
            if hasattr(self.radio_driver, "prune_hardware_contacts_now"):
                self.radio_driver.prune_hardware_contacts_now()
                self.btn_prune_hw_contacts.setEnabled(False)
                self.btn_prune_hw_contacts.setText("🧹 Pruning Radio Flash...")
                QTimer.singleShot(4000, lambda: (
                    self.btn_prune_hw_contacts.setEnabled(True),
                    self.btn_prune_hw_contacts.setText("🧹 Prune Stale Contacts on Radio Flash")
                ))

    # --- Tab 2: Pixoo & Quiet Hours ---
    def _build_pixoo_tab(self):
        self.tab_pixoo = QWidget()
        layout = QVBoxLayout(self.tab_pixoo)
        layout.setSpacing(12)

        # Main Window Live Mirror Display Card
        card_display = SettingsCard("🖥️ Main Window Interface")
        self.chk_show_live_mirror = QCheckBox("Show Pixoo 64 Live Mirror panel on Main Screen")
        self.chk_show_live_mirror.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        self.chk_show_live_mirror.setChecked(getattr(self.config.pixoo, "show_live_mirror", False))
        card_display.add_widget(self.chk_show_live_mirror)

        desc_lbl = QLabel("When enabled, the 64x64 matrix live mirror preview panel appears on the right of the main window.")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        card_display.add_widget(desc_lbl)
        layout.addWidget(card_display)

        # Hardware Card
        card_hw = SettingsCard("📺 Pixoo 64 Hardware Setup & Brightness")
        self.ip_input = QLineEdit(self.config.pixoo.ip_address)
        card_hw.add_row("Device IP Address:", self.ip_input, 160)

        bright_row = QHBoxLayout()
        lbl_br = QLabel("Matrix Brightness:")
        lbl_br.setFixedWidth(160)
        bright_row.addWidget(lbl_br)
        self.bright_slider = QSlider(Qt.Orientation.Horizontal)
        self.bright_slider.setRange(0, 100)
        self.bright_slider.setValue(self.config.pixoo.brightness)
        self.lbl_bright = QLabel(f"{self.config.pixoo.brightness}%")
        self.lbl_bright.setFixedWidth(45)
        self.bright_slider.valueChanged.connect(lambda v: self.lbl_bright.setText(f"{v}%"))
        bright_row.addWidget(self.bright_slider, 1)
        bright_row.addWidget(self.lbl_bright)
        card_hw.add_layout(bright_row)
        layout.addWidget(card_hw)

        # Message Pacing Card
        card_pace = SettingsCard("⏱️ Message Timing & Pacing")
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Alert Flash (s):"))
        self.alert_dur_spin = QSpinBox()
        self.alert_dur_spin.setRange(3, 60)
        self.alert_dur_spin.setValue(self.config.pixoo.alert_duration_secs)
        dur_row.addWidget(self.alert_dur_spin, 1)

        dur_row.addWidget(QLabel("Flash Count:"))
        self.flash_count_spin = QSpinBox()
        self.flash_count_spin.setRange(1, 10)
        self.flash_count_spin.setValue(self.config.pixoo.flash_count)
        dur_row.addWidget(self.flash_count_spin, 1)

        dur_row.addWidget(QLabel("Page Hold (s):"))
        self.page_dur_spin = QSpinBox()
        self.page_dur_spin.setRange(5, 60)
        self.page_dur_spin.setValue(self.config.pixoo.page_duration_secs)
        dur_row.addWidget(self.page_dur_spin, 1)
        card_pace.add_layout(dur_row)
        layout.addWidget(card_pace)

        # Quiet Hours Card
        card_quiet = SettingsCard("🌙 Quiet Hours (Night Mode)")
        self.chk_quiet = QCheckBox("Enable Quiet Hours Schedule")
        self.chk_quiet.setChecked(self.config.quiet_hours.enabled)
        card_quiet.add_widget(self.chk_quiet)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Start Time (HH:MM):"))
        self.quiet_start = QLineEdit(self.config.quiet_hours.start_time)
        time_row.addWidget(self.quiet_start, 1)

        time_row.addWidget(QLabel("End Time (HH:MM):"))
        self.quiet_end = QLineEdit(self.config.quiet_hours.end_time)
        time_row.addWidget(self.quiet_end, 1)
        card_quiet.add_layout(time_row)

        action_row = QHBoxLayout()
        lbl_act = QLabel("Quiet Hours Mode:")
        lbl_act.setFixedWidth(140)
        action_row.addWidget(lbl_act)
        self.quiet_mode = QComboBox()
        self.quiet_mode.addItem("Mute Green Flash Strobe Only", "mute_flash")
        self.quiet_mode.addItem("Dim Screen Brightness (30%)", "dim")
        self.quiet_mode.addItem("Turn Off Pixoo Display (Blackout)", "blackout")
        q_idx = self.quiet_mode.findData(self.config.quiet_hours.action)
        if q_idx >= 0:
            self.quiet_mode.setCurrentIndex(q_idx)
        action_row.addWidget(self.quiet_mode, 1)
        card_quiet.add_layout(action_row)
        layout.addWidget(card_quiet)

        layout.addStretch()
        return self.tab_pixoo

    # --- Tab 3: Channels & Filters ---
    def _build_channels_tab(self):
        self.tab_channels = QWidget()
        layout = QVBoxLayout(self.tab_channels)
        layout.setSpacing(12)

        info_lbl = QLabel(
            "Customize which channels display on the Pixoo 64 screen. "
            "Unchecked channels (e.g. #test) will show in desktop chat but be silenced on the Pixoo."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #9CA3AF; margin-bottom: 2px;")
        layout.addWidget(info_lbl)

        card_chan = SettingsCard("🔀 Channel Matrix & Pixoo Display Filters")
        self.channel_table = QTableWidget()
        self.channel_table.setColumnCount(3)
        self.channel_table.setHorizontalHeaderLabels(["Channel Name", "📺 Show on Pixoo", "⭐ Favorite"])
        self.channel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        channels = self.storage.get_channels() if self.storage else []
        self.channel_table.setRowCount(len(channels))

        filter_map = self.config.pixoo.channel_filters
        for row, ch in enumerate(channels):
            clean_name = ch.name.lstrip("#")
            self.channel_table.setItem(row, 0, QTableWidgetItem(f"#{clean_name}"))

            pixoo_chk = QCheckBox()
            if clean_name in filter_map:
                is_enabled = bool(filter_map[clean_name])
            elif f"#{clean_name}" in filter_map:
                is_enabled = bool(filter_map[f"#{clean_name}"])
            elif ch.name in filter_map:
                is_enabled = bool(filter_map[ch.name])
            else:
                is_enabled = bool(ch.is_pixoo_enabled)

            pixoo_chk.setChecked(is_enabled)
            self.channel_table.setCellWidget(row, 1, pixoo_chk)

            fav_chk = QCheckBox()
            is_fav = (
                ch.is_favorite
                or (self.config and self.config.is_channel_favorite(clean_name))
            )
            fav_chk.setChecked(is_fav)
            self.channel_table.setCellWidget(row, 2, fav_chk)

        card_chan.add_widget(self.channel_table)
        layout.addWidget(card_chan)
        return self.tab_channels

    # --- Tab 4: App UI Colors ---
    def _build_app_colors_tab(self):
        self.tab_app_colors = QWidget()
        layout = QVBoxLayout(self.tab_app_colors)
        layout.setSpacing(12)

        info_lbl = QLabel(
            "Customize interface and map theme colors. Click any color swatch below to open the palette. "
            "Colors are saved and applied live immediately as you pick them."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #9CA3AF; margin-bottom: 2px;")
        layout.addWidget(info_lbl)

        # Theme Management & Persistence Card
        theme_card = SettingsCard("🎨 Theme Presets & Management")
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)

        self.btn_save_theme = QPushButton("💾 Save Theme")
        self.btn_save_theme.setObjectName("actionButton")
        self.btn_save_theme.setToolTip("Save current custom theme colors to configuration")
        self.btn_save_theme.clicked.connect(self._on_save_theme_clicked)
        theme_row.addWidget(self.btn_save_theme)

        self.btn_set_default_theme = QPushButton("⭐ Set Current Theme as Default")
        self.btn_set_default_theme.setObjectName("actionButton")
        self.btn_set_default_theme.setToolTip("Save current colors as your permanent default theme across app restarts")
        self.btn_set_default_theme.clicked.connect(self._on_set_default_theme_clicked)
        theme_row.addWidget(self.btn_set_default_theme)

        self.btn_reset_default_theme = QPushButton("↺ Reset to Default Theme")
        self.btn_reset_default_theme.setObjectName("secondaryButton")
        self.btn_reset_default_theme.setToolTip("Restore colors from your saved default theme (or factory defaults)")
        self.btn_reset_default_theme.clicked.connect(self._on_reset_default_theme_clicked)
        theme_row.addWidget(self.btn_reset_default_theme)

        theme_card.add_layout(theme_row)

        self.theme_status_lbl = QLabel("")
        self.theme_status_lbl.setStyleSheet("color: #34D399; font-weight: bold; font-size: 11px;")
        theme_card.add_widget(self.theme_status_lbl)
        layout.addWidget(theme_card)

        # Application Startup & Behavior Card
        startup_card = SettingsCard("🖥️ Application Startup & Behavior")
        self.chk_show_splash = QCheckBox("Show application splash screen on launch")
        self.chk_show_splash.setChecked(getattr(self.config, "show_splash_screen", True))
        self.chk_show_splash.setToolTip("When unchecked, the app launches directly to the map view without the full-screen splash cover.")
        startup_card.add_widget(self.chk_show_splash)

        self.chk_check_updates = QCheckBox("Check for application updates on launch")
        self.chk_check_updates.setChecked(getattr(self.config, "check_updates_on_startup", True))
        self.chk_check_updates.setToolTip("When enabled, the app checks GitHub releases asynchronously on launch and alerts you if a newer version is available.")
        startup_card.add_widget(self.chk_check_updates)
        layout.addWidget(startup_card)

        # Chat & Avatar Settings Card
        chat_card = SettingsCard("💬 Chat Settings")
        self.chk_show_chat_avatars = QCheckBox("Show user and repeater avatar icons next to chat messages")
        self.chk_show_chat_avatars.setChecked(getattr(self.config, "show_chat_avatars", True))
        self.chk_show_chat_avatars.setToolTip("When enabled, message bubbles in the chat stream display avatar icons.")
        chat_card.add_widget(self.chk_show_chat_avatars)

        self.combo_avatar_style = QComboBox()
        self.combo_avatar_style.addItem("🤖 Cyberpunk Radio Droids (Robots)", "droid")
        self.combo_avatar_style.addItem("🔤 Cyber Initials (Decorated 2-Letter Frame)", "letters")
        cur_style = getattr(self.config, "user_avatar_style", "droid")
        idx = self.combo_avatar_style.findData(cur_style)
        if idx >= 0:
            self.combo_avatar_style.setCurrentIndex(idx)
        self.combo_avatar_style.setToolTip("Select the avatar style for user contacts across chat, contacts list, and navigation. Repeaters always use tactical radar.")
        chat_card.add_row("👤 User Contact Avatar Style:", self.combo_avatar_style, 250)
        layout.addWidget(chat_card)

        # Card 1: Chat & Sidebar Accents
        c1 = SettingsCard("💬 Chat & Sidebar Accents")
        self.btn_col_fav_chan = ColorPickerButton(self.config.app_colors.favorite_channel_color)
        c1.add_row("⭐ Favorite Channel Color:", self.btn_col_fav_chan, 250)
        self.btn_col_fav_user = ColorPickerButton(self.config.app_colors.favorite_user_color)
        c1.add_row("⭐ Favorite User Color:", self.btn_col_fav_user, 250)
        self.btn_col_send = ColorPickerButton(self.config.app_colors.send_button_color)
        c1.add_row("✉️ Composer Send Button Color:", self.btn_col_send, 250)
        self.btn_col_send_txt = ColorPickerButton(getattr(self.config.app_colors, "send_button_text_color", "#000000"))
        c1.add_row("✉️ Send Button Text Color:", self.btn_col_send_txt, 250)
        self.btn_col_radio_conn = ColorPickerButton(getattr(self.config.app_colors, "radio_connected_color", "#00FF7F"))
        c1.add_row("🟢 Radio Connected Status Text Color:", self.btn_col_radio_conn, 250)
        self.btn_col_sync_status = ColorPickerButton(getattr(self.config.app_colors, "sync_status_color", "#00FF7F"))
        c1.add_row("✓ Sync Status ('Up to date') Color:", self.btn_col_sync_status, 250)
        self.btn_col_snr = ColorPickerButton(getattr(self.config.app_colors, "message_snr_color", "#AA55FF"))
        c1.add_row("📊 Message SNR Telemetry Text Color:", self.btn_col_snr, 250)
        self.btn_col_new_msg_bar = ColorPickerButton(getattr(self.config.app_colors, "new_messages_bar_color", "#00FF7F"))
        c1.add_row("🔴 'NEW MESSAGES' Bar Color:", self.btn_col_new_msg_bar, 250)
        layout.addWidget(c1)

        # Card 2: Mesh Map Node Markers & Size
        c2 = SettingsCard("🗺️ Mesh Map Node Markers & Size")
        row_dot = QHBoxLayout()
        lbl_d = QLabel("📏 Map Node Dot Size:")
        lbl_d.setFixedWidth(250)
        row_dot.addWidget(lbl_d)
        cur_dot_size = float(getattr(self.config.app_colors, "map_dot_size", 6.4))
        self.slider_dot_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_dot_size.setRange(10, 80)
        self.slider_dot_size.setValue(int(cur_dot_size * 10))
        self.lbl_dot_size_val = QLabel(f"{cur_dot_size:.1f}px")
        self.lbl_dot_size_val.setFixedWidth(45)
        self.slider_dot_size.valueChanged.connect(lambda v: self.lbl_dot_size_val.setText(f"{v / 10.0:.1f}px"))
        row_dot.addWidget(self.slider_dot_size, 1)
        row_dot.addWidget(self.lbl_dot_size_val)
        c2.add_layout(row_dot)

        self.btn_col_map_rep = ColorPickerButton(self.config.app_colors.map_repeater_color)
        c2.add_row("📡 Repeater Node Dot Color:", self.btn_col_map_rep, 250)
        self.btn_col_map_rep_hover = ColorPickerButton(getattr(self.config.app_colors, "map_repeater_hover_color", "#60A5FA"))
        c2.add_row("📡 Repeater Node Hover Color:", self.btn_col_map_rep_hover, 250)

        self.btn_col_map_comp = ColorPickerButton(self.config.app_colors.map_companion_color)
        c2.add_row("👤 Companion Node Dot Color:", self.btn_col_map_comp, 250)
        self.btn_col_map_comp_hover = ColorPickerButton(getattr(self.config.app_colors, "map_companion_hover_color", "#22D3EE"))
        c2.add_row("👤 Companion Node Hover Color:", self.btn_col_map_comp_hover, 250)

        self.btn_col_map_room = ColorPickerButton(getattr(self.config.app_colors, "map_room_server_color", "#A855F7"))
        c2.add_row("🏢 Room Server Diamond Color:", self.btn_col_map_room, 250)
        self.btn_col_map_room_hover = ColorPickerButton(getattr(self.config.app_colors, "map_room_server_hover_color", "#C084FC"))
        c2.add_row("🏢 Room Server Hover Color:", self.btn_col_map_room_hover, 250)

        self.btn_col_map_orbital_rep = ColorPickerButton(getattr(self.config.app_colors, "map_orbital_repeater_color", "#FFD335"))
        c2.add_row("🛰️ Orbital Repeater Ring Color:", self.btn_col_map_orbital_rep, 250)

        self.chk_freshness = QCheckBox("Dim Older Nodes by Age (<24h full bright, -10%/day down to 30%)")
        self.chk_freshness.setChecked(getattr(self.config.meshcore, "node_freshness_fading", True))
        c2.add_widget(self.chk_freshness)
        layout.addWidget(c2)

        # Card 2B: 2D & 3D Map Tiles & API Keys
        c_map_tiles = SettingsCard("🗺️ 2D & 3D Map Tiles & Provider API Keys")

        # --- 2D Basemap Section ---
        lbl_sec_2d = QLabel("<b>🗺️ 2D Basemap Layer & CARTO Dark API Key</b>")
        lbl_sec_2d.setStyleSheet("color: #60A5FA; font-size: 12px; margin-top: 2px;")
        c_map_tiles.add_widget(lbl_sec_2d)

        row_base = QHBoxLayout()
        lbl_base = QLabel("Default 2D Base Map:")
        lbl_base.setFixedWidth(250)
        row_base.addWidget(lbl_base)
        self.combo_map_base = QComboBox()
        self.combo_map_base.setStyleSheet("""
            QComboBox {
                background-color: #2B2F38;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                min-width: 220px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1E2024;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.combo_map_base.addItem("🌌 CoreScope Dark (Black Land / Grey Sea)", "corescope")
        self.combo_map_base.addItem("🗺️ Esri Dark Canvas", "canvas")
        self.combo_map_base.addItem("🏔️ OpenTopoMap Relief", "topo")
        cur_base = getattr(self.config, "map_base_layer", "canvas")
        idx_b = self.combo_map_base.findData(cur_base)
        if idx_b >= 0:
            self.combo_map_base.setCurrentIndex(idx_b)
        row_base.addWidget(self.combo_map_base, 1)
        c_map_tiles.add_layout(row_base)

        row_key = QHBoxLayout()
        lbl_k = QLabel("CARTO Basemaps API Key:")
        lbl_k.setFixedWidth(250)
        row_key.addWidget(lbl_k)
        self.txt_carto_key = QLineEdit(getattr(self.config, "carto_api_key", ""))
        self.txt_carto_key.setPlaceholderText("Paste free key from carto.com to remove watermark")
        self.txt_carto_key.setStyleSheet("""
            QLineEdit {
                background-color: #2B2F38;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #3B82F6;
            }
        """)
        row_key.addWidget(self.txt_carto_key, 1)

        btn_get_free_key = QPushButton("🌐 Get Free CARTO Key")
        btn_get_free_key.setToolTip("Open carto.com/basemaps/apikey in browser (free, no credit card required)")
        btn_get_free_key.setStyleSheet("""
            QPushButton {
                background-color: #1E3A8A;
                color: #93C5FD;
                border: 1px solid #3B82F6;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2563EB;
                color: #FFFFFF;
            }
        """)
        btn_get_free_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://carto.com/basemaps/apikey")))
        row_key.addWidget(btn_get_free_key)
        c_map_tiles.add_layout(row_key)

        lbl_carto_info = QLabel("💡 <i>CARTO Dark Matter tiles work without a key. Entering a free key removes the watermark and unlocks high-volume rate limits.</i>")
        lbl_carto_info.setWordWrap(True)
        lbl_carto_info.setStyleSheet("color: #9CA3AF; font-size: 11px; margin-bottom: 8px;")
        c_map_tiles.add_widget(lbl_carto_info)

        # --- 3D Globe & Airspace Section ---
        lbl_sec_3d = QLabel("<b>🌐 3D Airspace & Globe Map Tile Provider</b>")
        lbl_sec_3d.setStyleSheet("color: #34D399; font-size: 12px; margin-top: 6px; border-top: 1px solid #374151; padding-top: 8px;")
        c_map_tiles.add_widget(lbl_sec_3d)

        lbl_3d_info = QLabel(
            "✨ <b>No API key required by default!</b> The 3D Map runs out-of-the-box on open-source "
            "<b>OpenFreeMap Dark</b> vector tiles and <b>AWS Open Data Terrarium DEM</b> elevation without any account.<br>"
            "If you want to use custom vector tiles (e.g. MapTiler, Mapbox, or self-hosted styles), configure them below:"
        )
        lbl_3d_info.setWordWrap(True)
        lbl_3d_info.setStyleSheet("color: #9CA3AF; font-size: 11px; margin-bottom: 6px;")
        c_map_tiles.add_widget(lbl_3d_info)

        row_3d_style = QHBoxLayout()
        lbl_3d_style = QLabel("3D Map Style URL:")
        lbl_3d_style.setFixedWidth(250)
        row_3d_style.addWidget(lbl_3d_style)
        self.txt_map3d_custom_style_url = QLineEdit(getattr(self.config, "map3d_custom_style_url", ""))
        self.txt_map3d_custom_style_url.setPlaceholderText("Default: https://tiles.openfreemap.org/styles/dark")
        self.txt_map3d_custom_style_url.setStyleSheet("""
            QLineEdit {
                background-color: #2B2F38;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #34D399;
            }
        """)
        row_3d_style.addWidget(self.txt_map3d_custom_style_url, 1)
        c_map_tiles.add_layout(row_3d_style)

        row_3d_key = QHBoxLayout()
        lbl_3d_k = QLabel("3D Map API Key / Token:")
        lbl_3d_k.setFixedWidth(250)
        row_3d_key.addWidget(lbl_3d_k)
        self.txt_map3d_key = QLineEdit(getattr(self.config, "map3d_api_key", ""))
        self.txt_map3d_key.setPlaceholderText("Optional: MapTiler or Mapbox API key / access token")
        self.txt_map3d_key.setStyleSheet("""
            QLineEdit {
                background-color: #2B2F38;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #34D399;
            }
        """)
        row_3d_key.addWidget(self.txt_map3d_key, 1)

        btn_get_3d_key = QPushButton("🌐 Get MapTiler Key")
        btn_get_3d_key.setToolTip("Open cloud.maptiler.com/account/keys/ in browser (free account available)")
        btn_get_3d_key.setStyleSheet("""
            QPushButton {
                background-color: #064E3B;
                color: #A7F3D0;
                border: 1px solid #059669;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #059669;
                color: #FFFFFF;
            }
        """)
        btn_get_3d_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://cloud.maptiler.com/account/keys/")))
        row_3d_key.addWidget(btn_get_3d_key)
        c_map_tiles.add_layout(row_3d_key)

        # Quick Save button for map tiles
        row_save_tiles = QHBoxLayout()
        self.lbl_map_tiles_status = QLabel("")
        self.lbl_map_tiles_status.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
        row_save_tiles.addWidget(self.lbl_map_tiles_status, 1)

        self.btn_save_map_tiles = QPushButton("💾 Save & Apply Map Tiles")
        self.btn_save_map_tiles.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: 1px solid #3B82F6;
                border-radius: 4px;
                padding: 5px 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
        """)
        self.btn_save_map_tiles.clicked.connect(self._on_save_map_tiles_clicked)
        row_save_tiles.addWidget(self.btn_save_map_tiles)
        c_map_tiles.add_layout(row_save_tiles)

        layout.addWidget(c_map_tiles)

        # Card 3: Trajectory & Status Gradients
        c3 = SettingsCard("⚡ Map Trajectory & Status Gradients")
        self.btn_col_watch_start = ColorPickerButton(self.config.app_colors.map_watcher_line_start)
        c3.add_row("⚡ Watcher Packet Line (Origin Start):", self.btn_col_watch_start, 250)
        self.btn_col_watch_end = ColorPickerButton(self.config.app_colors.map_watcher_line_end)
        c3.add_row("⚡ Watcher Packet Line (Destination Fade):", self.btn_col_watch_end, 250)
        self.btn_col_msg_start = ColorPickerButton(self.config.app_colors.map_message_line_start)
        c3.add_row("📥 Received Message Route (Sender Start):", self.btn_col_msg_start, 250)
        self.btn_col_msg_end = ColorPickerButton(self.config.app_colors.map_message_line_end)
        c3.add_row("📥 Received Message Route (Receiver End):", self.btn_col_msg_end, 250)
        self.btn_col_watcher_status = ColorPickerButton(getattr(self.config.app_colors, "map_watcher_status_color", "#7EE787"))
        c3.add_row("⚡ Bottom Map Status ('FLOOD' text) Color:", self.btn_col_watcher_status, 250)
        layout.addWidget(c3)

        # Card 4: Route Visualisation Colors
        c_vis = SettingsCard("📍 Route Visualisation Colors")
        self.btn_col_visualised_path = ColorPickerButton(getattr(self.config.app_colors, "map_visualised_path_color", "#FF00FF"))
        c_vis.add_row("📍 Known Repeaters Route Line Color:", self.btn_col_visualised_path, 300)
        self.btn_col_visualised_heading = ColorPickerButton(getattr(self.config.app_colors, "map_visualised_heading_color", "#FF00FF"))
        c_vis.add_row("🏷️ Route Popup Headings & Accent Color:", self.btn_col_visualised_heading, 300)
        self.btn_col_phantom_path = ColorPickerButton(getattr(self.config.app_colors, "map_phantom_path_color", "#FFFF00"))
        c_vis.add_row("👻 Phantom Node Route Color:", self.btn_col_phantom_path, 300)
        self.btn_col_unknown_path = ColorPickerButton(getattr(self.config.app_colors, "map_unknown_path_color", "#EF4444"))
        c_vis.add_row("❓ Unknown Repeater Route Color:", self.btn_col_unknown_path, 300)
        self.btn_col_no_gps_path = ColorPickerButton(getattr(self.config.app_colors, "map_no_gps_path_color", "#000000"))
        c_vis.add_row("📡 Repeater with No GPS Route Color:", self.btn_col_no_gps_path, 300)
        layout.addWidget(c_vis)

        # Card 4B: ADS-B Flight Color Schemes
        c_adsb = SettingsCard("✈️ ADS-B Aircraft Flight Color Schemes")

        self.combo_adsb_mode = QComboBox()
        self.combo_adsb_mode.setStyleSheet("""
            QComboBox {
                background-color: #2B2F38;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                min-width: 170px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1E2024;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.combo_adsb_mode.addItems(["Altitude (Flight Level)", "Aircraft Type", "Distance from Station"])
        cur_mode = getattr(self.config.app_colors, "adsb_color_mode", "altitude")
        if cur_mode == "type":
            self.combo_adsb_mode.setCurrentIndex(1)
        elif cur_mode == "distance":
            self.combo_adsb_mode.setCurrentIndex(2)
        else:
            self.combo_adsb_mode.setCurrentIndex(0)
        self.combo_adsb_mode.currentIndexChanged.connect(self._on_adsb_mode_combo_changed)
        c_adsb.add_row("🎨 Active Flight Coloring Mode:", self.combo_adsb_mode, 300)

        # Altitude Scheme Colors
        self.btn_col_adsb_alt_ground = ColorPickerButton(getattr(self.config.app_colors, "adsb_alt_ground", "#FF00FF"))
        c_adsb.add_row("🏔️ Altitude: Lowest / Ground (< 2,000 ft):", self.btn_col_adsb_alt_ground, 300)
        self.btn_col_adsb_alt_low = ColorPickerButton(getattr(self.config.app_colors, "adsb_alt_low", "#FF0000"))
        c_adsb.add_row("🏔️ Altitude: Sub-7,000 ft (< 7k ft):", self.btn_col_adsb_alt_low, 300)
        self.btn_col_adsb_alt_mid = ColorPickerButton(getattr(self.config.app_colors, "adsb_alt_mid", "#0000FF"))
        c_adsb.add_row("🏔️ Altitude: Mid Flight Level (10k - 25k ft):", self.btn_col_adsb_alt_mid, 300)
        self.btn_col_adsb_alt_high = ColorPickerButton(getattr(self.config.app_colors, "adsb_alt_high", "#FFFFFF"))
        c_adsb.add_row("🏔️ Altitude: High Flight Level (> 25k ft):", self.btn_col_adsb_alt_high, 300)

        # Type Scheme Colors
        self.btn_col_adsb_type_airliner = ColorPickerButton(getattr(self.config.app_colors, "adsb_type_airliner", "#FFFFFF"))
        c_adsb.add_row("✈️ Aircraft Type: Commercial Airliner:", self.btn_col_adsb_type_airliner, 300)
        self.btn_col_adsb_type_light = ColorPickerButton(getattr(self.config.app_colors, "adsb_type_light", "#0000FF"))
        c_adsb.add_row("🛩️ Aircraft Type: Light Aircraft / Prop:", self.btn_col_adsb_type_light, 300)
        self.btn_col_adsb_type_military = ColorPickerButton(getattr(self.config.app_colors, "adsb_type_military", "#00FF00"))
        c_adsb.add_row("⚔️ Aircraft Type: Military Fast Jet / Transport:", self.btn_col_adsb_type_military, 300)
        self.btn_col_adsb_type_helicopter = ColorPickerButton(getattr(self.config.app_colors, "adsb_type_helicopter", "#FFFF00"))
        c_adsb.add_row("🚁 Aircraft Type: Helicopter / Rotorcraft:", self.btn_col_adsb_type_helicopter, 300)
        self.btn_col_adsb_type_glider = ColorPickerButton(getattr(self.config.app_colors, "adsb_type_glider", "#FF00FF"))
        c_adsb.add_row("🪂 Aircraft Type: Glider / Sailplane:", self.btn_col_adsb_type_glider, 300)

        # Distance Scheme Colors
        self.btn_col_adsb_dist_close = ColorPickerButton(getattr(self.config.app_colors, "adsb_dist_close", "#FF0000"))
        c_adsb.add_row("📏 Distance: Closest Range (0 - 25% Radius):", self.btn_col_adsb_dist_close, 300)
        self.btn_col_adsb_dist_mid_close = ColorPickerButton(getattr(self.config.app_colors, "adsb_dist_mid_close", "#FFA500"))
        c_adsb.add_row("📏 Distance: Mid-Close Range (25% - 50% Radius):", self.btn_col_adsb_dist_mid_close, 300)
        self.btn_col_adsb_dist_mid_far = ColorPickerButton(getattr(self.config.app_colors, "adsb_dist_mid_far", "#FFFF00"))
        c_adsb.add_row("📏 Distance: Mid-Far Range (50% - 75% Radius):", self.btn_col_adsb_dist_mid_far, 300)
        self.btn_col_adsb_dist_far = ColorPickerButton(getattr(self.config.app_colors, "adsb_dist_far", "#00FF00"))
        c_adsb.add_row("📏 Distance: Outer Boundary (75% - 100%+ Radius):", self.btn_col_adsb_dist_far, 300)

        layout.addWidget(c_adsb)

        # Connect all color pickers for automatic real-time save
        self._connect_app_color_pickers()

        # Card 5: Saved Hop Route Overrides
        layout.addWidget(self._build_hop_preferences_card())

        # Card 6: Marked Phantom Nodes
        layout.addWidget(self._build_phantom_nodes_card())

        layout.addStretch()
        return self.tab_app_colors

    def _connect_app_color_pickers(self):
        bindings = [
            (self.btn_col_fav_chan, "favorite_channel_color"),
            (self.btn_col_fav_user, "favorite_user_color"),
            (self.btn_col_send, "send_button_color"),
            (self.btn_col_send_txt, "send_button_text_color"),
            (self.btn_col_radio_conn, "radio_connected_color"),
            (self.btn_col_sync_status, "sync_status_color"),
            (self.btn_col_snr, "message_snr_color"),
            (self.btn_col_new_msg_bar, "new_messages_bar_color"),
            (self.btn_col_map_rep, "map_repeater_color"),
            (self.btn_col_map_rep_hover, "map_repeater_hover_color"),
            (self.btn_col_map_comp, "map_companion_color"),
            (self.btn_col_map_comp_hover, "map_companion_hover_color"),
            (self.btn_col_map_room, "map_room_server_color"),
            (self.btn_col_map_room_hover, "map_room_server_hover_color"),
            (self.btn_col_map_orbital_rep, "map_orbital_repeater_color"),
            (self.btn_col_watch_start, "map_watcher_line_start"),
            (self.btn_col_watch_end, "map_watcher_line_end"),
            (self.btn_col_msg_start, "map_message_line_start"),
            (self.btn_col_msg_end, "map_message_line_end"),
            (self.btn_col_watcher_status, "map_watcher_status_color"),
            (self.btn_col_visualised_path, "map_visualised_path_color"),
            (self.btn_col_visualised_heading, "map_visualised_heading_color"),
            (self.btn_col_phantom_path, "map_phantom_path_color"),
            (self.btn_col_unknown_path, "map_unknown_path_color"),
            (self.btn_col_no_gps_path, "map_no_gps_path_color"),
            (self.btn_col_adsb_alt_ground, "adsb_alt_ground"),
            (self.btn_col_adsb_alt_low, "adsb_alt_low"),
            (self.btn_col_adsb_alt_mid, "adsb_alt_mid"),
            (self.btn_col_adsb_alt_high, "adsb_alt_high"),
            (self.btn_col_adsb_type_airliner, "adsb_type_airliner"),
            (self.btn_col_adsb_type_light, "adsb_type_light"),
            (self.btn_col_adsb_type_military, "adsb_type_military"),
            (self.btn_col_adsb_type_helicopter, "adsb_type_helicopter"),
            (self.btn_col_adsb_type_glider, "adsb_type_glider"),
            (self.btn_col_adsb_dist_close, "adsb_dist_close"),
            (self.btn_col_adsb_dist_mid_close, "adsb_dist_mid_close"),
            (self.btn_col_adsb_dist_mid_far, "adsb_dist_mid_far"),
            (self.btn_col_adsb_dist_far, "adsb_dist_far"),
        ]
        for btn, attr in bindings:
            btn.color_changed.connect(lambda hex_val, b=btn, a=attr: self._on_app_color_changed(a, hex_val, b))

        self.slider_dot_size.valueChanged.connect(self._on_dot_size_slider_changed)
        self.chk_freshness.toggled.connect(self._on_freshness_chk_toggled)

    def _on_adsb_mode_combo_changed(self, idx: int):
        modes = ["altitude", "type", "distance"]
        if 0 <= idx < len(modes):
            self.config.app_colors.adsb_color_mode = modes[idx]
            try:
                self.config.save()
            except Exception:
                pass
            bus.emit(EventType.SETTINGS_UPDATED, self.config)

    def _on_app_color_changed(self, attr_name: str, hex_val: str, btn: Optional[ColorPickerButton] = None):
        if btn and btn.current_hex != hex_val:
            btn.current_hex = hex_val
            btn._update_swatch()
        if hasattr(self.config.app_colors, attr_name):
            setattr(self.config.app_colors, attr_name, hex_val)
            try:
                self.config.save()
            except Exception as e:
                logger.error(f"Failed to auto-save theme color {attr_name}: {e}")
            bus.emit(EventType.SETTINGS_UPDATED, self.config)
            friendly = attr_name.replace("map_", "").replace("_", " ").title()
            self.theme_status_lbl.setText(f"✓ Saved {friendly} ({hex_val.upper()}) & applied live!")
            QTimer.singleShot(2500, lambda: self.theme_status_lbl.setText(""))

    def _on_dot_size_slider_changed(self, val: int):
        dot_size = val / 10.0
        self.config.app_colors.map_dot_size = dot_size
        try:
            self.config.save()
        except Exception:
            pass
        bus.emit(EventType.SETTINGS_UPDATED, self.config)

    def _on_freshness_chk_toggled(self, checked: bool):
        self.config.meshcore.node_freshness_fading = checked
        try:
            self.config.save()
        except Exception:
            pass
        bus.emit(EventType.SETTINGS_UPDATED, self.config)

    def _sync_color_pickers_to_config(self):
        c = self.config.app_colors
        c.favorite_channel_color = self.btn_col_fav_chan.current_hex
        c.favorite_user_color = self.btn_col_fav_user.current_hex
        c.send_button_color = self.btn_col_send.current_hex
        c.send_button_text_color = self.btn_col_send_txt.current_hex
        c.radio_connected_color = self.btn_col_radio_conn.current_hex
        c.sync_status_color = self.btn_col_sync_status.current_hex
        c.message_snr_color = self.btn_col_snr.current_hex
        c.new_messages_bar_color = self.btn_col_new_msg_bar.current_hex
        c.map_dot_size = self.slider_dot_size.value() / 10.0
        c.map_repeater_color = self.btn_col_map_rep.current_hex
        c.map_repeater_hover_color = self.btn_col_map_rep_hover.current_hex
        c.map_companion_color = self.btn_col_map_comp.current_hex
        c.map_companion_hover_color = self.btn_col_map_comp_hover.current_hex
        c.map_room_server_color = self.btn_col_map_room.current_hex
        c.map_room_server_hover_color = self.btn_col_map_room_hover.current_hex
        c.map_orbital_repeater_color = self.btn_col_map_orbital_rep.current_hex
        c.map_watcher_line_start = self.btn_col_watch_start.current_hex
        c.map_watcher_line_end = self.btn_col_watch_end.current_hex
        c.map_message_line_start = self.btn_col_msg_start.current_hex
        c.map_message_line_end = self.btn_col_msg_end.current_hex
        c.map_watcher_status_color = self.btn_col_watcher_status.current_hex
        c.map_visualised_path_color = self.btn_col_visualised_path.current_hex
        c.map_visualised_heading_color = self.btn_col_visualised_heading.current_hex
        c.map_phantom_path_color = self.btn_col_phantom_path.current_hex
        c.map_unknown_path_color = self.btn_col_unknown_path.current_hex
        c.map_no_gps_path_color = self.btn_col_no_gps_path.current_hex
        c.adsb_alt_ground = self.btn_col_adsb_alt_ground.current_hex
        c.adsb_alt_low = self.btn_col_adsb_alt_low.current_hex
        c.adsb_alt_mid = self.btn_col_adsb_alt_mid.current_hex
        c.adsb_alt_high = self.btn_col_adsb_alt_high.current_hex
        c.adsb_type_airliner = self.btn_col_adsb_type_airliner.current_hex
        c.adsb_type_light = self.btn_col_adsb_type_light.current_hex
        c.adsb_type_military = self.btn_col_adsb_type_military.current_hex
        c.adsb_type_helicopter = self.btn_col_adsb_type_helicopter.current_hex
        c.adsb_type_glider = self.btn_col_adsb_type_glider.current_hex
        c.adsb_dist_close = self.btn_col_adsb_dist_close.current_hex
        c.adsb_dist_mid_close = self.btn_col_adsb_dist_mid_close.current_hex
        c.adsb_dist_mid_far = self.btn_col_adsb_dist_mid_far.current_hex
        c.adsb_dist_far = self.btn_col_adsb_dist_far.current_hex
        if hasattr(self, "combo_map_base"):
            self.config.map_base_layer = self.combo_map_base.currentData() or "canvas"
            self.config.meshcore.map_base_layer = self.config.map_base_layer
        if hasattr(self, "txt_carto_key"):
            self.config.carto_api_key = self.txt_carto_key.text().strip()
            self.config.meshcore.carto_api_key = self.config.carto_api_key
        if hasattr(self, "txt_map3d_key"):
            self.config.map3d_api_key = self.txt_map3d_key.text().strip()
            self.config.meshcore.map3d_api_key = self.config.map3d_api_key
        if hasattr(self, "txt_map3d_custom_style_url"):
            self.config.map3d_custom_style_url = self.txt_map3d_custom_style_url.text().strip()
            self.config.meshcore.map3d_custom_style_url = self.config.map3d_custom_style_url

    def _sync_config_to_color_pickers(self):
        c = self.config.app_colors
        self.btn_col_fav_chan.set_color(c.favorite_channel_color)
        self.btn_col_fav_user.set_color(c.favorite_user_color)
        self.btn_col_send.set_color(c.send_button_color)
        self.btn_col_send_txt.set_color(getattr(c, "send_button_text_color", "#000000"))
        self.btn_col_radio_conn.set_color(getattr(c, "radio_connected_color", "#00FF7F"))
        self.btn_col_sync_status.set_color(getattr(c, "sync_status_color", "#00FF7F"))
        self.btn_col_snr.set_color(getattr(c, "message_snr_color", "#AA55FF"))
        self.btn_col_new_msg_bar.set_color(getattr(c, "new_messages_bar_color", "#00FF7F"))
        cur_dot_size = float(getattr(c, "map_dot_size", 6.4))
        self.slider_dot_size.setValue(int(cur_dot_size * 10))
        self.lbl_dot_size_val.setText(f"{cur_dot_size:.1f}px")
        self.btn_col_map_rep.set_color(c.map_repeater_color)
        self.btn_col_map_rep_hover.set_color(getattr(c, "map_repeater_hover_color", "#FF55FF"))
        self.btn_col_map_comp.set_color(c.map_companion_color)
        self.btn_col_map_comp_hover.set_color(getattr(c, "map_companion_hover_color", "#00FFFF"))
        self.btn_col_map_room.set_color(getattr(c, "map_room_server_color", "#FF00FF"))
        self.btn_col_map_room_hover.set_color(getattr(c, "map_room_server_hover_color", "#FF55FF"))
        self.btn_col_map_orbital_rep.set_color(getattr(c, "map_orbital_repeater_color", "#FFD335"))
        self.btn_col_watch_start.set_color(c.map_watcher_line_start)
        self.btn_col_watch_end.set_color(c.map_watcher_line_end)
        self.btn_col_msg_start.set_color(c.map_message_line_start)
        self.btn_col_msg_end.set_color(c.map_message_line_end)
        self.btn_col_watcher_status.set_color(getattr(c, "map_watcher_status_color", "#7EE787"))
        self.btn_col_visualised_path.set_color(getattr(c, "map_visualised_path_color", "#FF00FF"))
        self.btn_col_visualised_heading.set_color(getattr(c, "map_visualised_heading_color", "#FF00FF"))
        self.btn_col_phantom_path.set_color(getattr(c, "map_phantom_path_color", "#FFFF00"))
        self.btn_col_unknown_path.set_color(getattr(c, "map_unknown_path_color", "#EF4444"))
        self.btn_col_no_gps_path.set_color(getattr(c, "map_no_gps_path_color", "#000000"))
        self.btn_col_adsb_alt_ground.set_color(getattr(c, "adsb_alt_ground", "#FF00FF"))
        self.btn_col_adsb_alt_low.set_color(getattr(c, "adsb_alt_low", "#FF0000"))
        self.btn_col_adsb_alt_mid.set_color(getattr(c, "adsb_alt_mid", "#0000FF"))
        self.btn_col_adsb_alt_high.set_color(getattr(c, "adsb_alt_high", "#FFFFFF"))
        self.btn_col_adsb_type_airliner.set_color(getattr(c, "adsb_type_airliner", "#FFFFFF"))
        self.btn_col_adsb_type_light.set_color(getattr(c, "adsb_type_light", "#0000FF"))
        self.btn_col_adsb_type_military.set_color(getattr(c, "adsb_type_military", "#00FF00"))
        self.btn_col_adsb_type_helicopter.set_color(getattr(c, "adsb_type_helicopter", "#FFFF00"))
        self.btn_col_adsb_type_glider.set_color(getattr(c, "adsb_type_glider", "#FF00FF"))
        self.btn_col_adsb_dist_close.set_color(getattr(c, "adsb_dist_close", "#FF0000"))
        self.btn_col_adsb_dist_mid_close.set_color(getattr(c, "adsb_dist_mid_close", "#FFA500"))
        self.btn_col_adsb_dist_mid_far.set_color(getattr(c, "adsb_dist_mid_far", "#FFFF00"))
        self.btn_col_adsb_dist_far.set_color(getattr(c, "adsb_dist_far", "#00FF00"))
        mode = getattr(c, "adsb_color_mode", "altitude")
        if mode == "type":
            self.combo_adsb_mode.setCurrentIndex(1)
        elif mode == "distance":
            self.combo_adsb_mode.setCurrentIndex(2)
        else:
            self.combo_adsb_mode.setCurrentIndex(0)
        if hasattr(self, "combo_map_base"):
            idx_b = self.combo_map_base.findData(getattr(self.config, "map_base_layer", "canvas"))
            if idx_b >= 0:
                self.combo_map_base.setCurrentIndex(idx_b)
        if hasattr(self, "txt_carto_key"):
            self.txt_carto_key.setText(getattr(self.config, "carto_api_key", ""))
        if hasattr(self, "txt_map3d_key"):
            self.txt_map3d_key.setText(getattr(self.config, "map3d_api_key", ""))
        if hasattr(self, "txt_map3d_custom_style_url"):
            self.txt_map3d_custom_style_url.setText(getattr(self.config, "map3d_custom_style_url", ""))

    def _on_save_map_tiles_clicked(self):
        """Dedicated save handler for 2D/3D map tiles and API keys."""
        btn = getattr(self, "btn_save_map_tiles", None)
        orig_txt = btn.text() if btn else ""
        if btn:
            btn.setEnabled(False)
            btn.setText("⏳ Saving...")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QApplication.processEvents()
        try:
            if hasattr(self, "combo_map_base"):
                self.config.map_base_layer = self.combo_map_base.currentData() or "canvas"
                self.config.meshcore.map_base_layer = self.config.map_base_layer
            if hasattr(self, "txt_carto_key"):
                self.config.carto_api_key = self.txt_carto_key.text().strip()
                self.config.meshcore.carto_api_key = self.config.carto_api_key
            if hasattr(self, "txt_map3d_key"):
                self.config.map3d_api_key = self.txt_map3d_key.text().strip()
                self.config.meshcore.map3d_api_key = self.config.map3d_api_key
            if hasattr(self, "txt_map3d_custom_style_url"):
                self.config.map3d_custom_style_url = self.txt_map3d_custom_style_url.text().strip()
                self.config.meshcore.map3d_custom_style_url = self.config.map3d_custom_style_url
            self.config.save()
            bus.emit(EventType.SETTINGS_UPDATED, self.config)
            if btn:
                btn.setText("✓ Saved!")
                btn.setStyleSheet("background-color: #059669; color: #FFFFFF; font-weight: bold; border-radius: 4px; padding: 5px 14px; font-size: 11px;")
            if hasattr(self, "lbl_map_tiles_status"):
                self.lbl_map_tiles_status.setText("✓ Map tile keys saved and active!")
                QTimer.singleShot(3500, lambda: self.lbl_map_tiles_status.setText(""))
            if btn:
                QTimer.singleShot(1800, lambda: (btn.setText(orig_txt), btn.setStyleSheet("background-color: #2563EB; color: #FFFFFF; border: 1px solid #3B82F6; border-radius: 4px; padding: 5px 14px; font-size: 11px; font-weight: bold;"), btn.setEnabled(True)))
        except Exception as e:
            logger.error(f"Failed to save map tile settings: {e}")
            if btn:
                btn.setText("❌ Error")
                QTimer.singleShot(2000, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))

    def _on_save_theme_clicked(self):
        btn = getattr(self, "btn_save_theme", None)
        orig_txt = btn.text() if btn else ""
        if btn:
            btn.setEnabled(False)
            btn.setText("⏳ Saving...")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QApplication.processEvents()
        try:
            self._sync_color_pickers_to_config()
            self.config.save()
            bus.emit(EventType.SETTINGS_UPDATED, self.config)
            if btn:
                btn.setText("✓ Saved!")
                btn.setStyleSheet("background-color: #059669; color: #FFFFFF; font-weight: bold; border-radius: 4px;")
            self.theme_status_lbl.setText("💾 Theme successfully saved to config.json and active!")
            if btn:
                QTimer.singleShot(1800, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))
            QTimer.singleShot(3000, lambda: self.theme_status_lbl.setText(""))
        except Exception as e:
            if btn:
                btn.setText("❌ Error")
                QTimer.singleShot(2000, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))

    def _on_set_default_theme_clicked(self):
        from dataclasses import asdict
        btn = getattr(self, "btn_set_default_theme", None)
        orig_txt = btn.text() if btn else ""
        if btn:
            btn.setEnabled(False)
            btn.setText("⏳ Saving...")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QApplication.processEvents()
        try:
            self._sync_color_pickers_to_config()
            self.config.default_app_colors = asdict(self.config.app_colors)
            self.config.save()
            bus.emit(EventType.SETTINGS_UPDATED, self.config)
            if btn:
                btn.setText("✓ Saved as Default!")
                btn.setStyleSheet("background-color: #059669; color: #FFFFFF; font-weight: bold; border-radius: 4px;")
            self.theme_status_lbl.setText("⭐ Current theme successfully saved as default!")
            if btn:
                QTimer.singleShot(1800, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))
            QTimer.singleShot(3000, lambda: self.theme_status_lbl.setText(""))
        except Exception as e:
            if btn:
                btn.setText("❌ Error")
                QTimer.singleShot(2000, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))

    def _on_reset_default_theme_clicked(self):
        from dataclasses import asdict
        from meshcore_tray.config import AppColors
        defaults = self.config.default_app_colors if self.config.default_app_colors else asdict(AppColors())
        self.config.app_colors = AppColors(**{k: v for k, v in defaults.items() if k in AppColors.__dataclass_fields__})
        self._sync_config_to_color_pickers()
        self.config.save()
        bus.emit(EventType.SETTINGS_UPDATED, self.config)
        self.theme_status_lbl.setText("↺ Restored default theme colors!")
        QTimer.singleShot(3000, lambda: self.theme_status_lbl.setText(""))


    def _build_hop_preferences_card(self) -> SettingsCard:
        self.card_hop_prefs = SettingsCard("💾 Saved Hop Route Overrides")
        self.hop_prefs_container = QVBoxLayout()
        self.card_hop_prefs.add_layout(self.hop_prefs_container)
        self._refresh_hop_preferences_ui()
        return self.card_hop_prefs

    def _refresh_hop_preferences_ui(self):
        while self.hop_prefs_container.count():
            item = self.hop_prefs_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            l = item.layout()
            if l:
                while l.count():
                    sub = l.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        desc = QLabel(
            "Manual repeater adjustments selected in the route popup are saved here and "
            "automatically applied to all future message and packet routing requests."
        )
        desc.setStyleSheet("color: #9CA3AF; font-size: 11px; margin-bottom: 4px;")
        desc.setWordWrap(True)
        self.hop_prefs_container.addWidget(desc)

        prefs = self.storage.get_all_hop_preferences() if self.storage else []
        if not prefs:
            lbl_empty = QLabel("<i>No custom hop overrides saved. (Click '[Use this]' in the route popup to save one).</i>")
            lbl_empty.setStyleSheet("color: #6B7280; font-size: 11px; margin: 4px 0;")
            self.hop_prefs_container.addWidget(lbl_empty)
            return

        for p in prefs:
            row = QHBoxLayout()
            h_pref = p["hop_prefix"]
            alias_display = p["alias"] or p["node_id"]
            nid_display = p["node_id"]
            lbl_item = QLabel(f"Prefix <code style='color: #60A5FA;'>{h_pref}</code> ➔ 📡 <b style='color: #34D399;'>{alias_display}</b> <span style='color: #9CA3AF; font-size: 10px;'>({nid_display})</span>")
            row.addWidget(lbl_item, 1)

            btn_rm = QPushButton("Remove")
            btn_rm.setStyleSheet("""
                QPushButton {
                    background-color: #374151;
                    color: #F87171;
                    border: 1px solid #4B5563;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #DC2626;
                    color: #FFFFFF;
                }
            """)
            btn_rm.clicked.connect(lambda _, pref=h_pref: self._on_delete_hop_preference(pref))
            row.addWidget(btn_rm)
            self.hop_prefs_container.addLayout(row)

        btn_clear_row = QHBoxLayout()
        btn_clear_row.addStretch()
        btn_clear_all = QPushButton("🧹 Clear All Saved Overrides")
        btn_clear_all.setStyleSheet("""
            QPushButton {
                background-color: #262A33;
                color: #D1D5DB;
                border: 1px solid #3E4451;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 10px;
                margin-top: 6px;
            }
            QPushButton:hover {
                background-color: #7F1D1D;
                color: #FCA5A5;
                border-color: #EF4444;
            }
        """)
        btn_clear_all.clicked.connect(self._on_clear_all_hop_preferences)
        btn_clear_row.addWidget(btn_clear_all)
        self.hop_prefs_container.addLayout(btn_clear_row)

    def _on_delete_hop_preference(self, hop_prefix: str):
        if self.storage:
            self.storage.delete_hop_preference(hop_prefix)
            self._refresh_hop_preferences_ui()

    def _on_clear_all_hop_preferences(self):
        if self.storage:
            self.storage.clear_all_hop_preferences()
            self._refresh_hop_preferences_ui()

    def _build_phantom_nodes_card(self) -> SettingsCard:
        self.card_phantom_nodes = SettingsCard("👻 Marked Phantom Nodes (Collision Discards)")
        self.phantom_nodes_container = QVBoxLayout()
        self.card_phantom_nodes.add_layout(self.phantom_nodes_container)
        self._refresh_phantom_nodes_ui()
        return self.card_phantom_nodes

    def _refresh_phantom_nodes_ui(self):
        while self.phantom_nodes_container.count():
            item = self.phantom_nodes_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            l = item.layout()
            if l:
                while l.count():
                    sub = l.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        desc = QLabel(
            "Nodes marked as 'phantom nodes' are excluded from map plotting. Any visualised route "
            "passing through these hops is drawn in yellow to indicate a phantom/unresolved hop."
        )
        desc.setStyleSheet("color: #9CA3AF; font-size: 11px; margin-bottom: 4px;")
        desc.setWordWrap(True)
        self.phantom_nodes_container.addWidget(desc)

        phantoms = self.storage.get_all_phantom_nodes() if self.storage else []
        if not phantoms and self.config and self.config.phantom_nodes:
            phantoms = [{"node_id": p, "alias": p, "created_at": ""} for p in self.config.phantom_nodes]

        if not phantoms:
            lbl_empty = QLabel("<i>No phantom nodes marked. (Click '👻 Mark as phantom node' in the route popup).</i>")
            lbl_empty.setStyleSheet("color: #6B7280; font-size: 11px; margin: 4px 0;")
            self.phantom_nodes_container.addWidget(lbl_empty)
            return

        for p in phantoms:
            row = QHBoxLayout()
            nid = p["node_id"]
            alias = p.get("alias") or nid
            lbl_item = QLabel(f"👻 <b style='color: #FACC15;'>{alias}</b> <span style='color: #9CA3AF; font-size: 10px;'>({nid})</span>")
            row.addWidget(lbl_item, 1)

            btn_rm = QPushButton("Unmark")
            btn_rm.setStyleSheet("""
                QPushButton {
                    background-color: #374151;
                    color: #FACC15;
                    border: 1px solid #CA8A04;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #CA8A04;
                    color: #000000;
                }
            """)
            btn_rm.clicked.connect(lambda _, n=nid, a=alias: self._on_delete_phantom_node(n, a))
            row.addWidget(btn_rm)
            self.phantom_nodes_container.addLayout(row)

        btn_clear_row = QHBoxLayout()
        btn_clear_row.addStretch()
        btn_clear_all = QPushButton("🧹 Clear All Phantom Nodes")
        btn_clear_all.setStyleSheet("""
            QPushButton {
                background-color: #262A33;
                color: #D1D5DB;
                border: 1px solid #3E4451;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 10px;
                margin-top: 6px;
            }
            QPushButton:hover {
                background-color: #7F1D1D;
                color: #FCA5A5;
                border-color: #EF4444;
            }
        """)
        btn_clear_all.clicked.connect(self._on_clear_all_phantom_nodes)
        btn_clear_row.addWidget(btn_clear_all)
        self.phantom_nodes_container.addLayout(btn_clear_row)

    def _on_delete_phantom_node(self, node_id: str, alias: str):
        if self.storage:
            self.storage.unmark_phantom_node(node_id)
            if alias:
                self.storage.unmark_phantom_node(alias)
        if self.config:
            self.config.unmark_phantom_node(node_id)
            if alias:
                self.config.unmark_phantom_node(alias)
        self._refresh_phantom_nodes_ui()

    def _on_clear_all_phantom_nodes(self):
        if self.storage:
            self.storage.clear_all_phantom_nodes()
        if self.config:
            self.config.phantom_nodes = []
            self.config.save()
        self._refresh_phantom_nodes_ui()

    # --- Tab 5: Pixoo Matrix Colors ---
    def _build_colors_tab(self):
        self.tab_colors = QWidget()
        layout = QVBoxLayout(self.tab_colors)
        layout.setSpacing(12)

        info_lbl = QLabel("Customize all 6 color tokens for the Pixoo 64 matrix display:")
        info_lbl.setStyleSheet("color: #9CA3AF; margin-bottom: 2px;")
        layout.addWidget(info_lbl)

        card = SettingsCard("🌈 Pixoo 64 Color Tokens")
        self.btn_col_channel = ColorPickerButton(self.config.pixoo_colors.channel_color)
        card.add_row("Channel Header Color:", self.btn_col_channel, 200)

        self.btn_col_alert = ColorPickerButton(self.config.pixoo_colors.alert_color)
        card.add_row("Alert Strobe Color:", self.btn_col_alert, 200)

        self.btn_col_msg = ColorPickerButton(self.config.pixoo_colors.message_color)
        card.add_row("Message Body Text Color:", self.btn_col_msg, 200)

        self.btn_col_bg = ColorPickerButton(self.config.pixoo_colors.background_color)
        card.add_row("Screen Background Color:", self.btn_col_bg, 200)

        self.btn_col_sender = ColorPickerButton(self.config.pixoo_colors.sender_color)
        card.add_row("Sender Name Color:", self.btn_col_sender, 200)

        self.btn_col_star = ColorPickerButton(self.config.pixoo_colors.favorite_star_color)
        card.add_row("Favorite Star (★) Color:", self.btn_col_star, 200)

        layout.addWidget(card)
        layout.addStretch()
        return self.tab_colors

    # --- Tab 6: Notifications & Watched Words ---
    def _build_notifications_tab(self):
        self.tab_notifications = QWidget()
        layout = QVBoxLayout(self.tab_notifications)
        layout.setSpacing(12)

        card_alerts = SettingsCard("🔔 Desktop & Mention Alerts")
        self.chk_desktop_notif = QCheckBox("Show Native Desktop Notifications")
        self.chk_desktop_notif.setChecked(self.config.notifications.desktop_notifications)
        card_alerts.add_widget(self.chk_desktop_notif)

        self.chk_mention_notif = QCheckBox("Alert when Node Callsign / Hex ID is mentioned")
        self.chk_mention_notif.setChecked(self.config.notifications.notify_on_node_mentions)
        card_alerts.add_widget(self.chk_mention_notif)
        layout.addWidget(card_alerts)

        card_kw = SettingsCard("🚨 Watched Keywords & Emergency Triggers")
        self.kw_list = QListWidget()
        self.kw_list.setStyleSheet("background-color: #1C1C1C; border: 1.5px solid #414143; border-radius: 6px;")
        for kw in self.config.notifications.watched_keywords:
            self.kw_list.addItem(kw)
        card_kw.add_widget(self.kw_list)

        add_row = QHBoxLayout()
        self.kw_input = QLineEdit()
        self.kw_input.setPlaceholderText("Enter keyword (e.g. storm, repeater, CQ)...")
        self.btn_add_kw = QPushButton("Add Word")
        self.btn_add_kw.setObjectName("secondaryButton")
        self.btn_add_kw.clicked.connect(self._add_keyword)

        self.btn_del_kw = QPushButton("Remove Selected")
        self.btn_del_kw.setObjectName("secondaryButton")
        self.btn_del_kw.clicked.connect(self._del_keyword)

        add_row.addWidget(self.kw_input, 1)
        add_row.addWidget(self.btn_add_kw)
        add_row.addWidget(self.btn_del_kw)
        card_kw.add_layout(add_row)
        layout.addWidget(card_kw)

        layout.addStretch()
        return self.tab_notifications

    def _add_keyword(self):
        text = self.kw_input.text().strip()
        if text:
            self.kw_list.addItem(text)
            self.kw_input.clear()

    def _del_keyword(self):
        item = self.kw_list.currentItem()
        if item:
            self.kw_list.takeItem(self.kw_list.row(item))

    # --- Tab 7: Gateway & Telemetry ---
    def _build_gateway_tab(self):
        self.tab_gateway = QWidget()
        layout = QVBoxLayout(self.tab_gateway)
        layout.setSpacing(12)

        card_rot = SettingsCard("📊 Telemetry & Neighbour Rotations")
        self.chk_telem_rot = QCheckBox("Rotate Node Battery & Telemetry onto Pixoo")
        self.chk_telem_rot.setChecked(self.config.telemetry.enabled)
        card_rot.add_widget(self.chk_telem_rot)

        t_row = QHBoxLayout()
        lbl_ti = QLabel("Telemetry Interval (mins):")
        lbl_ti.setFixedWidth(180)
        t_row.addWidget(lbl_ti)
        self.telem_interval_spin = QSpinBox()
        self.telem_interval_spin.setRange(1, 120)
        self.telem_interval_spin.setValue(self.config.telemetry.interval_mins)
        t_row.addWidget(self.telem_interval_spin, 1)
        card_rot.add_layout(t_row)

        self.chk_neigh_rot = QCheckBox("Rotate Heard Mesh Neighbours onto Pixoo")
        self.chk_neigh_rot.setChecked(self.config.neighbours.enabled)
        card_rot.add_widget(self.chk_neigh_rot)

        n_row = QHBoxLayout()
        lbl_ni = QLabel("Neighbours Interval (mins):")
        lbl_ni.setFixedWidth(180)
        n_row.addWidget(lbl_ni)
        self.neigh_interval_spin = QSpinBox()
        self.neigh_interval_spin.setRange(1, 120)
        self.neigh_interval_spin.setValue(self.config.neighbours.interval_mins)
        n_row.addWidget(self.neigh_interval_spin, 1)
        card_rot.add_layout(n_row)

        src_row = QHBoxLayout()
        lbl_src = QLabel("Neighbours Source:")
        lbl_src.setFixedWidth(180)
        src_row.addWidget(lbl_src)
        self.neigh_src_combo = QComboBox()
        self.neigh_src_combo.addItem("Local Radio Direct Neighbours", "local")
        self.neigh_src_combo.addItem("Query Target Repeater via LoRa", "repeater")
        src_idx = self.neigh_src_combo.findData(self.config.neighbours.source)
        if src_idx >= 0:
            self.neigh_src_combo.setCurrentIndex(src_idx)
        src_row.addWidget(self.neigh_src_combo, 1)
        card_rot.add_layout(src_row)

        target_row = QHBoxLayout()
        lbl_tr = QLabel("Target Repeater Node ID:")
        lbl_tr.setFixedWidth(180)
        target_row.addWidget(lbl_tr)
        self.target_rep_input = QLineEdit(self.config.neighbours.target_repeater_node_id)
        target_row.addWidget(self.target_rep_input, 1)
        card_rot.add_layout(target_row)
        layout.addWidget(card_rot)

        card_gate = SettingsCard("🌐 Local HTTP REST API Bridge")
        self.chk_gate = QCheckBox("Enable Local HTTP Bridge (for SDRs & External Scripts)")
        self.chk_gate.setChecked(self.config.gateway.http_bridge_enabled)
        card_gate.add_widget(self.chk_gate)

        port_row = QHBoxLayout()
        lbl_gp = QLabel("HTTP Bridge Port:")
        lbl_gp.setFixedWidth(180)
        port_row.addWidget(lbl_gp)
        self.gate_port_spin = QSpinBox()
        self.gate_port_spin.setRange(1024, 65535)
        self.gate_port_spin.setValue(self.config.gateway.http_port)
        port_row.addWidget(self.gate_port_spin, 1)
        card_gate.add_layout(port_row)
        layout.addWidget(card_gate)

        # Satellite Tracking Card
        card_sat = SettingsCard("🛰️ Satellite Tracking (CelesTrak & SGP4)")
        sat_cfg = getattr(self.config, "satellites", None)
        self.chk_sat_enabled = QCheckBox("Enable Satellite Tracking Layer")
        self.chk_sat_enabled.setChecked(sat_cfg.enabled if sat_cfg else False)
        card_sat.add_widget(self.chk_sat_enabled)

        self.chk_sat_footprints = QCheckBox("Show Line-of-Sight Horizon Footprints on Map")
        self.chk_sat_footprints.setChecked(sat_cfg.show_footprints if sat_cfg else True)
        card_sat.add_widget(self.chk_sat_footprints)

        self.chk_sat_tracks = QCheckBox("Show 90-minute Orbital Projection Ground Tracks")
        self.chk_sat_tracks.setChecked(sat_cfg.show_ground_tracks if sat_cfg else True)
        card_sat.add_widget(self.chk_sat_tracks)

        sat_int_row = QHBoxLayout()
        lbl_sint = QLabel("TLE Refresh Interval (hours):")
        lbl_sint.setFixedWidth(200)
        sat_int_row.addWidget(lbl_sint)
        self.sat_interval_spin = QSpinBox()
        self.sat_interval_spin.setRange(1, 168)
        self.sat_interval_spin.setValue(sat_cfg.update_interval_hours if sat_cfg else 24)
        sat_int_row.addWidget(self.sat_interval_spin, 1)
        card_sat.add_layout(sat_int_row)

        sat_el_row = QHBoxLayout()
        lbl_sel = QLabel("Minimum Pass Elevation (°):")
        lbl_sel.setFixedWidth(200)
        sat_el_row.addWidget(lbl_sel)
        self.sat_min_el_spin = QSpinBox()
        self.sat_min_el_spin.setRange(0, 89)
        self.sat_min_el_spin.setValue(sat_cfg.min_pass_elevation_deg if sat_cfg else 10)
        sat_el_row.addWidget(self.sat_min_el_spin, 1)
        card_sat.add_layout(sat_el_row)

        grp_box = QVBoxLayout()
        grp_lbl = QLabel("Tracked Satellite Groups:")
        grp_lbl.setStyleSheet("font-weight: bold; color: #E5E7EB; margin-top: 4px;")
        grp_box.addWidget(grp_lbl)

        active_grps = sat_cfg.active_groups if sat_cfg else ["stations", "amateur", "weather"]
        self.chk_sat_stations = QCheckBox("Space Stations (ISS, Tiangong CSS)")
        self.chk_sat_stations.setChecked("stations" in active_grps)
        grp_box.addWidget(self.chk_sat_stations)

        self.chk_sat_amateur = QCheckBox("Amateur Radio Repeaters & Transponders (SO-50, AO-91, RS-44)")
        self.chk_sat_amateur.setChecked("amateur" in active_grps)
        grp_box.addWidget(self.chk_sat_amateur)

        self.chk_sat_weather = QCheckBox("Weather & Earth Observation (NOAA 15/18/19, Meteor-M2)")
        self.chk_sat_weather.setChecked("weather" in active_grps)
        grp_box.addWidget(self.chk_sat_weather)

        self.chk_sat_cubesat = QCheckBox("Cubesats & LoRa (TinyGS, FOSSASAT)")
        self.chk_sat_cubesat.setChecked("cubesat" in active_grps)
        grp_box.addWidget(self.chk_sat_cubesat)
        card_sat.add_layout(grp_box)

        layout.addWidget(card_sat)

        # MQTT Broker & Feed Card (CoreScope / meshcoretomqtt Ingest)
        card_mqtt = SettingsCard("📡 MQTT Broker & CoreScope Feed")
        mqtt_cfg = getattr(self.config, "mqtt", None)

        self.chk_mqtt_enabled = QCheckBox("Enable MQTT Packet Ingest / Gateway")
        self.chk_mqtt_enabled.setChecked(mqtt_cfg.enabled if mqtt_cfg else False)
        card_mqtt.add_widget(self.chk_mqtt_enabled)

        m_preset_row = QHBoxLayout()
        lbl_mpre = QLabel("Public Community Presets:")
        lbl_mpre.setFixedWidth(180)
        m_preset_row.addWidget(lbl_mpre)
        self.combo_mqtt_preset = QComboBox()
        self.combo_mqtt_preset.addItems([
            "-- Select an Open MeshCore MQTT Stream --",
            "🇬🇧 UKMesh Network (wss://mqtt.ukmesh.com:443 - WebSockets/TLS)",
            "Lincomatic MeshCore (mqtt.lincomatic.com:8883 - TLS)",
            "🇬🇧 IPNet UK MeshCore Observer (mqtt.ipnt.uk:1883)",
            "🇬🇧 NorthMesh UK MeshCore Network (mqtt.northmesh.co.uk:1883)",
            "Local Bridge / meshcoretomqtt (localhost:1883)",
            "EMQX Public Sandbox (broker.emqx.io:1883)"
        ])

        # Restore saved preset if available, or find matching preset by broker_host
        saved_preset = getattr(mqtt_cfg, "preset_name", "") if mqtt_cfg else ""
        match_idx = -1
        if saved_preset:
            match_idx = self.combo_mqtt_preset.findText(saved_preset)
        if match_idx <= 0 and mqtt_cfg and mqtt_cfg.broker_host and mqtt_cfg.broker_host != "localhost":
            for i in range(1, self.combo_mqtt_preset.count()):
                if mqtt_cfg.broker_host in self.combo_mqtt_preset.itemText(i):
                    match_idx = i
                    break
        elif match_idx <= 0 and mqtt_cfg and mqtt_cfg.broker_host == "localhost":
            if mqtt_cfg.broker_port == 1883:
                for i in range(1, self.combo_mqtt_preset.count()):
                    if "localhost" in self.combo_mqtt_preset.itemText(i):
                        match_idx = i
                        break
        if match_idx > 0:
            self.combo_mqtt_preset.blockSignals(True)
            self.combo_mqtt_preset.setCurrentIndex(match_idx)
            self.combo_mqtt_preset.blockSignals(False)

        self.combo_mqtt_preset.currentIndexChanged.connect(self._on_mqtt_preset_selected)
        m_preset_row.addWidget(self.combo_mqtt_preset, 1)
        card_mqtt.add_layout(m_preset_row)

        m_host_row = QHBoxLayout()
        lbl_mh = QLabel("Broker Host:")
        lbl_mh.setFixedWidth(180)
        m_host_row.addWidget(lbl_mh)
        self.mqtt_host_input = QLineEdit(mqtt_cfg.broker_host if mqtt_cfg else "localhost")
        self.mqtt_host_input.setPlaceholderText("localhost or broker.emqx.io")
        m_host_row.addWidget(self.mqtt_host_input, 1)
        card_mqtt.add_layout(m_host_row)

        m_port_row = QHBoxLayout()
        lbl_mp = QLabel("Broker Port:")
        lbl_mp.setFixedWidth(180)
        m_port_row.addWidget(lbl_mp)
        self.mqtt_port_spin = QSpinBox()
        self.mqtt_port_spin.setRange(1, 65535)
        self.mqtt_port_spin.setValue(mqtt_cfg.broker_port if mqtt_cfg else 1883)
        m_port_row.addWidget(self.mqtt_port_spin, 1)
        card_mqtt.add_layout(m_port_row)

        m_trans_row = QHBoxLayout()
        lbl_mtrans = QLabel("Protocol / Transport:")
        lbl_mtrans.setFixedWidth(180)
        m_trans_row.addWidget(lbl_mtrans)
        self.combo_mqtt_transport = QComboBox()
        self.combo_mqtt_transport.addItem("Standard TCP (mqtt:// or ssl://)", "tcp")
        self.combo_mqtt_transport.addItem("WebSockets (ws:// or wss://)", "websockets")
        saved_transport = getattr(mqtt_cfg, "transport", "tcp") if mqtt_cfg else "tcp"
        t_idx = self.combo_mqtt_transport.findData(saved_transport)
        if t_idx >= 0:
            self.combo_mqtt_transport.setCurrentIndex(t_idx)
        m_trans_row.addWidget(self.combo_mqtt_transport, 1)

        lbl_ws_path = QLabel("WS Path:")
        self.mqtt_ws_path_input = QLineEdit(getattr(mqtt_cfg, "ws_path", "/mqtt") if mqtt_cfg else "/mqtt")
        self.mqtt_ws_path_input.setPlaceholderText("/mqtt")
        self.mqtt_ws_path_input.setFixedWidth(80)
        m_trans_row.addWidget(lbl_ws_path)
        m_trans_row.addWidget(self.mqtt_ws_path_input)
        card_mqtt.add_layout(m_trans_row)

        m_user_row = QHBoxLayout()
        lbl_mu = QLabel("Username (Optional):")
        lbl_mu.setFixedWidth(180)
        m_user_row.addWidget(lbl_mu)
        self.mqtt_user_input = QLineEdit(mqtt_cfg.username if mqtt_cfg else "")
        m_user_row.addWidget(self.mqtt_user_input, 1)
        card_mqtt.add_layout(m_user_row)

        m_pass_row = QHBoxLayout()
        lbl_mps = QLabel("Password (Optional):")
        lbl_mps.setFixedWidth(180)
        m_pass_row.addWidget(lbl_mps)
        self.mqtt_pass_input = QLineEdit(mqtt_cfg.password if mqtt_cfg else "")
        self.mqtt_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        m_pass_row.addWidget(self.mqtt_pass_input, 1)
        card_mqtt.add_layout(m_pass_row)

        self.chk_mqtt_tls = QCheckBox("Enable TLS / SSL Connection")
        self.chk_mqtt_tls.setChecked(mqtt_cfg.use_tls if mqtt_cfg else False)
        card_mqtt.add_widget(self.chk_mqtt_tls)

        m_top_row = QHBoxLayout()
        lbl_mt = QLabel("Subscribe Topics (comma-separated):")
        lbl_mt.setFixedWidth(220)
        m_top_row.addWidget(lbl_mt)
        topics_str = ", ".join(mqtt_cfg.subscribe_topics) if (mqtt_cfg and mqtt_cfg.subscribe_topics) else "meshcore/#, meshcore/uk/#"
        self.mqtt_topics_input = QLineEdit(topics_str)
        self.mqtt_topics_input.setPlaceholderText("meshcore/#, meshcore/uk/#, meshcoretomqtt/#")
        m_top_row.addWidget(self.mqtt_topics_input, 1)
        card_mqtt.add_layout(m_top_row)

        # Connection Test Row
        m_test_row = QHBoxLayout()
        self.btn_test_mqtt = QPushButton("🧪 Test Connection")
        self.btn_test_mqtt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test_mqtt.setStyleSheet(
            "QPushButton { background: #1E293B; border: 1px solid #38BDF8; color: #38BDF8; "
            "border-radius: 6px; padding: 6px 14px; font-weight: bold; font-size: 12px; } "
            "QPushButton:hover { background: #0284C7; color: #FFFFFF; } "
            "QPushButton:disabled { background: #334155; color: #64748B; border-color: #475569; }"
        )
        self.btn_test_mqtt.clicked.connect(self._test_mqtt_connection)
        m_test_row.addWidget(self.btn_test_mqtt)

        self.lbl_mqtt_test_status = QLabel("")
        self.lbl_mqtt_test_status.setStyleSheet("font-size: 11px; font-weight: bold;")
        m_test_row.addWidget(self.lbl_mqtt_test_status, 1)
        card_mqtt.add_layout(m_test_row)

        self.chk_mqtt_publish = QCheckBox("Publish Local Radio Packets to MQTT (Gateway Forwarding)")
        self.chk_mqtt_publish.setChecked(mqtt_cfg.publish_enabled if mqtt_cfg else False)
        card_mqtt.add_widget(self.chk_mqtt_publish)

        m_pub_row = QHBoxLayout()
        lbl_mpub = QLabel("Publish Topic:")
        lbl_mpub.setFixedWidth(180)
        m_pub_row.addWidget(lbl_mpub)
        self.mqtt_pub_topic_input = QLineEdit(mqtt_cfg.publish_topic if mqtt_cfg else "meshcore/packets")
        m_pub_row.addWidget(self.mqtt_pub_topic_input, 1)
        card_mqtt.add_layout(m_pub_row)

        # Dedicated Save Button for Gateway, Satellites & MQTT
        save_gate_box = QHBoxLayout()
        self.btn_save_gateway = QPushButton("💾 Save Gateway & MQTT Settings")
        self.btn_save_gateway.setObjectName("primaryButton")
        self.btn_save_gateway.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_gateway.clicked.connect(self._save_gateway_and_mqtt)
        save_gate_box.addWidget(self.btn_save_gateway)

        self.lbl_gateway_status = QLabel("")
        self.lbl_gateway_status.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
        save_gate_box.addWidget(self.lbl_gateway_status)
        save_gate_box.addStretch()
        card_mqtt.add_layout(save_gate_box)

        layout.addWidget(card_mqtt)

        layout.addStretch()
        return self.tab_gateway

    def _save_gateway_and_mqtt(self):
        """Dedicated save handler for Gateway, Satellites and MQTT settings."""
        btn = getattr(self, "btn_save_gateway", None)
        orig_txt = btn.text() if btn else ""
        if btn:
            btn.setEnabled(False)
            btn.setText("⏳ Saving...")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QApplication.processEvents()
        self._apply_settings(close_on_finish=False)
        if btn:
            btn.setText("✓ Saved & Active!")
            btn.setStyleSheet("background-color: #059669; color: #FFFFFF; font-weight: bold; border-radius: 6px; padding: 6px 16px;")
        self.lbl_gateway_status.setText("✓ Gateway, Satellite & MQTT settings saved & applied live!")
        if btn:
            QTimer.singleShot(2000, lambda: (btn.setText(orig_txt), btn.setStyleSheet(""), btn.setEnabled(True)))
        QTimer.singleShot(4000, lambda: self.lbl_gateway_status.setText(""))

    def _on_mqtt_preset_selected(self, index: int):
        """Pre-populates connection parameters for open community MeshCore MQTT brokers."""
        if index == 1:  # 🇬🇧 UKMesh Network (WebSockets/TLS)
            self.mqtt_host_input.setText("mqtt.ukmesh.com")
            self.mqtt_port_spin.setValue(443)
            self.mqtt_user_input.setText("soulway")
            self.mqtt_pass_input.setText("ZM3d2A94ZBu5btbK")
            self.chk_mqtt_tls.setChecked(True)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("websockets")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            if hasattr(self, "mqtt_ws_path_input"):
                self.mqtt_ws_path_input.setText("/mqtt")
            self.mqtt_topics_input.setText("public/+/+/packets, public/#")
            self.chk_mqtt_publish.setChecked(False)  # Permissions: read-only; publishing denied
        elif index == 2:  # Lincomatic MeshCore Community Broker
            self.mqtt_host_input.setText("mqtt.lincomatic.com")
            self.mqtt_port_spin.setValue(8883)
            self.mqtt_user_input.setText("")
            self.mqtt_pass_input.setText("")
            self.chk_mqtt_tls.setChecked(True)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("tcp")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            if hasattr(self, "mqtt_ws_path_input"):
                self.mqtt_ws_path_input.setText("/mqtt")
            self.mqtt_topics_input.setText("meshcore/#, meshcore/+/+/packets")
        elif index == 3:  # 🇬🇧 IPNet UK MeshCore Observer
            self.mqtt_host_input.setText("mqtt.ipnt.uk")
            self.mqtt_port_spin.setValue(1883)
            self.mqtt_user_input.setText("")
            self.mqtt_pass_input.setText("")
            self.chk_mqtt_tls.setChecked(False)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("tcp")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            self.mqtt_topics_input.setText("meshcore/uk/#, meshcore/#")
        elif index == 4:  # 🇬🇧 NorthMesh UK MeshCore Network
            self.mqtt_host_input.setText("mqtt.northmesh.co.uk")
            self.mqtt_port_spin.setValue(1883)
            self.mqtt_user_input.setText("")
            self.mqtt_pass_input.setText("")
            self.chk_mqtt_tls.setChecked(False)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("tcp")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            self.mqtt_topics_input.setText("meshcore/uk/#, meshcore/#")
        elif index == 5:  # Local Ingestor / meshcoretomqtt bridge
            self.mqtt_host_input.setText("localhost")
            self.mqtt_port_spin.setValue(1883)
            self.mqtt_user_input.setText("")
            self.mqtt_pass_input.setText("")
            self.chk_mqtt_tls.setChecked(False)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("tcp")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            self.mqtt_topics_input.setText("meshcore/#, meshcoretomqtt/#")
        elif index == 6:  # EMQX Public Sandbox
            self.mqtt_host_input.setText("broker.emqx.io")
            self.mqtt_port_spin.setValue(1883)
            self.mqtt_user_input.setText("")
            self.mqtt_pass_input.setText("")
            self.chk_mqtt_tls.setChecked(False)
            if hasattr(self, "combo_mqtt_transport"):
                t_idx = self.combo_mqtt_transport.findData("tcp")
                if t_idx >= 0:
                    self.combo_mqtt_transport.setCurrentIndex(t_idx)
            self.mqtt_topics_input.setText("meshcore/#")

    def _test_mqtt_connection(self):
        """Tests live connectivity to the specified MQTT broker in a background daemon thread."""
        host = self.mqtt_host_input.text().strip()
        port = self.mqtt_port_spin.value()
        user = self.mqtt_user_input.text().strip()
        passwd = self.mqtt_pass_input.text()
        use_tls = self.chk_mqtt_tls.isChecked()
        transport = self.combo_mqtt_transport.currentData() if hasattr(self, "combo_mqtt_transport") else "tcp"
        ws_path = self.mqtt_ws_path_input.text().strip() if hasattr(self, "mqtt_ws_path_input") else "/mqtt"
        topic = self.mqtt_topics_input.text().strip() or "meshcore/#"

        if not host:
            self.lbl_mqtt_test_status.setStyleSheet("color: #EF4444; font-size: 11px; font-weight: bold;")
            self.lbl_mqtt_test_status.setText("❌ Broker host cannot be empty.")
            return

        self.btn_test_mqtt.setEnabled(False)
        self.btn_test_mqtt.setText("⏳ Testing...")
        self.lbl_mqtt_test_status.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold;")
        self.lbl_mqtt_test_status.setText(f"Connecting to {host}:{port} via {str(transport).upper()}...")

        def run_test():
            import time
            from meshcore_tray.core.mqtt_service import MqttService
            res = MqttService.test_broker_connection(
                host=host,
                port=port,
                username=user,
                password=passwd,
                use_tls=use_tls,
                transport=transport or "tcp",
                ws_path=ws_path or "/mqtt",
                topic=topic,
                timeout_secs=6.0
            )

            def update_ui():
                self.btn_test_mqtt.setEnabled(True)
                self.btn_test_mqtt.setText("🧪 Test Connection")
                if res.get("success"):
                    lat = res.get("latency_ms", 0)
                    h = res.get("host", host)
                    p = res.get("port", port)
                    tr = res.get("transport", transport or "tcp").upper()
                    self.lbl_mqtt_test_status.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
                    self.lbl_mqtt_test_status.setText(f"✓ Connected to {h}:{p} ({tr}) • Latency: {lat}ms • Subscribed")
                else:
                    err = res.get("error", "Unknown error")
                    self.lbl_mqtt_test_status.setStyleSheet("color: #EF4444; font-size: 11px; font-weight: bold;")
                    self.lbl_mqtt_test_status.setText(f"❌ Connection failed: {err}")

            QTimer.singleShot(0, update_ui)

        import threading
        threading.Thread(target=run_test, daemon=True).start()


    # --- Tab 8: About & Support ---
    def _build_about_tab(self):
        self.tab_about = QWidget()
        layout = QVBoxLayout(self.tab_about)
        layout.setSpacing(14)

        card_about = SettingsCard("ℹ️ About MESHCORE NAVIGATOR")

        lbl_app = QLabel("⚡ MESHCORE NAVIGATOR")
        lbl_app.setStyleSheet("font-size: 20px; font-weight: 800; color: #38BDF8; margin-top: 4px;")
        card_about.add_widget(lbl_app)

        lbl_ver = QLabel(f"Version {__version__} • Early Alpha")
        lbl_ver.setStyleSheet("font-size: 13px; font-weight: 600; color: #94A3B8; margin-bottom: 6px;")
        card_about.add_widget(lbl_ver)

        lbl_desc = QLabel(
            "Advanced desktop station, real-time RF mesh map, and LED matrix integration "
            "engineered for MeshCore & Heltec V3 radios. Features multi-hop packet tracing, "
            "ADS-B aircraft overlays, tropospheric ducting forecasts, and thunderstorm tracking."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #D1D5DB; font-size: 13px; line-height: 1.4; margin-bottom: 8px;")
        card_about.add_widget(lbl_desc)

        lbl_author = QLabel("App by Nicky Proniewicz - M7NCY")
        lbl_author.setStyleSheet("font-size: 14px; font-weight: 700; color: #FBBF24; margin-bottom: 4px;")
        card_about.add_widget(lbl_author)

        lbl_license = QLabel("Free & Open Source Software under GNU General Public License v3.0 (GPL-3.0)")
        lbl_license.setStyleSheet("font-size: 12px; font-weight: 600; color: #10B981; margin-bottom: 12px;")
        card_about.add_widget(lbl_license)

        coffee_row = QHBoxLayout()
        btn_coffee_large = QPushButton("☕ Buy Me a Coffee")
        btn_coffee_large.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_coffee_large.setFixedHeight(40)
        btn_coffee_large.setStyleSheet("""
            QPushButton {
                background-color: #FFDD00;
                color: #000000;
                border: 1px solid #E6C600;
                border-radius: 8px;
                padding: 8px 24px;
                font-size: 14px;
                font-weight: 800;
            }
            QPushButton:hover {
                background-color: #FFE633;
                border-color: #FFDD00;
            }
            QPushButton:pressed {
                background-color: #E6C600;
            }
        """)
        btn_coffee_large.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(__coffee_url__)))
        coffee_row.addWidget(btn_coffee_large)

        btn_report_bug = QPushButton("🐛 Report Bug / Submit Issue")
        btn_report_bug.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_report_bug.setFixedHeight(40)
        btn_report_bug.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #38BDF8;
                border: 1px solid #0284C7;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369A1;
                color: #FFFFFF;
            }
        """)
        from meshcore_tray.ui.crash_dialog import open_bug_report_in_browser
        btn_report_bug.clicked.connect(lambda: open_bug_report_in_browser())
        coffee_row.addWidget(btn_report_bug)

        coffee_row.addStretch()
        card_about.add_layout(coffee_row)

        # Software Updates Card
        card_updates = SettingsCard("🚀 Software Updates & Releases")

        self.lbl_update_ver = QLabel(f"<b>Installed Version:</b> v{__version__}")
        self.lbl_update_ver.setStyleSheet("font-size: 13px; color: #E2E8F0;")
        card_updates.add_widget(self.lbl_update_ver)

        self.lbl_update_status = QLabel("Click 'Check for Updates' to query the latest GitHub releases.")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setStyleSheet("font-size: 12px; color: #94A3B8; margin-top: 4px; margin-bottom: 8px;")
        self.lbl_update_status.setOpenExternalLinks(True)
        card_updates.add_widget(self.lbl_update_status)

        updates_row = QHBoxLayout()
        self.btn_check_updates = QPushButton("🔍 Check for Updates")
        self.btn_check_updates.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_updates.setFixedHeight(36)
        self.btn_check_updates.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #38BDF8;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
            QPushButton:disabled {
                background-color: #334155;
                color: #64748B;
                border-color: #475569;
            }
        """)
        self.btn_check_updates.clicked.connect(self._on_check_updates_clicked)
        updates_row.addWidget(self.btn_check_updates)

        self.btn_all_releases = QPushButton("🌐 View GitHub Releases")
        self.btn_all_releases.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_all_releases.setFixedHeight(36)
        self.btn_all_releases.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #38BDF8;
                border: 1px solid #0284C7;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #0369A1;
                color: #FFFFFF;
            }
        """)
        self.btn_all_releases.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_RELEASES_PAGE)))
        updates_row.addWidget(self.btn_all_releases)
        updates_row.addStretch()
        card_updates.add_layout(updates_row)

        layout.addWidget(card_about)
        layout.addWidget(card_updates)
        layout.addStretch()
        return self.tab_about

    def _on_check_updates_clicked(self):
        """Triggers manual version check against GitHub releases API."""
        self.lbl_update_status.setText("Connecting to GitHub to check for updates...")
        self.btn_check_updates.setEnabled(False)
        checker = VersionChecker.get_instance()
        checker.check_finished.connect(self._on_manual_version_check_finished)
        checker.check_for_updates(force=True)

    def _on_manual_version_check_finished(self, is_newer: bool, info: Optional[Any], error_msg: str):
        self.btn_check_updates.setEnabled(True)
        try:
            VersionChecker.get_instance().check_finished.disconnect(self._on_manual_version_check_finished)
        except Exception:
            pass

        if is_newer and info:
            self.lbl_update_status.setText(
                f"🎉 <b>Update Available: v{info.version}</b> ({info.name})<br>"
                f"<a style='color: #38BDF8; text-decoration: underline;' href='{info.html_url}'>Click here to download the latest release on GitHub</a>"
            )
        elif error_msg:
            self.lbl_update_status.setText(f"⚠️ {error_msg}")
        else:
            self.lbl_update_status.setText(f"✓ You are running the latest version of MeshCore Navigator (v{__version__}).")

    # --- Save & Apply Handler ---
    def _apply_settings(self, close_on_finish: bool = True):
        """Applies all form settings, writes config.json to disk, emits bus event, and updates hardware."""
        target_btn = getattr(self, "btn_save" if close_on_finish else "btn_apply", None)
        orig_text = target_btn.text() if target_btn else ""
        orig_style = target_btn.styleSheet() if target_btn else ""
        if target_btn:
            target_btn.setEnabled(False)
            target_btn.setText("⏳ Saving..." if close_on_finish else "⏳ Applying...")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QApplication.processEvents()

        # 1. Update Node & Radio
        self.config.meshcore.serial_port = self.port_combo.currentData() or "auto"
        self.config.meshcore.baudrate = self.baud_combo.currentData() or 115200
        self.config.meshcore.connection_type = self.mode_combo.currentData() or "serial"
        new_alias = self.alias_input.text().strip()
        self.config.meshcore.node_alias = new_alias
        self.config.meshcore.node_id = self.node_id_input.text().strip()
        if hasattr(self, "home_lat_input") and hasattr(self, "home_lon_input"):
            self.config.meshcore.latitude = self.home_lat_input.value()
            self.config.meshcore.longitude = self.home_lon_input.value()

        freq = self.freq_spin.value()
        bw = self.bw_combo.currentData() or 62.5
        sf = self.sf_combo.currentData() or 8
        cr = self.cr_combo.currentData() or "4/5"
        tx = self.tx_spin.value()
        path_mode = self.path_mode_combo.currentData()
        if path_mode is None:
            path_mode = 0

        autoadd = self.chk_autoadd.isChecked()
        loc_policy = self.loc_policy_combo.currentData() or 0
        multi_acks = self.chk_multi_acks.isChecked()
        rx_dly = self.rx_delay_spin.value()

        self.config.meshcore.radio_preset = self.preset_combo.currentText()
        self.config.meshcore.frequency_mhz = freq
        self.config.meshcore.bandwidth_khz = bw
        self.config.meshcore.spreading_factor = sf
        self.config.meshcore.coding_rate = cr
        self.config.meshcore.tx_power_dbm = tx
        self.config.meshcore.path_hash_mode = path_mode
        self.config.meshcore.autoadd_contacts = autoadd
        if hasattr(self, "chk_auto_prune_hardware"):
            self.config.meshcore.auto_prune_hardware_contacts = self.chk_auto_prune_hardware.isChecked()
        self.config.meshcore.advert_loc_policy = loc_policy
        self.config.meshcore.multi_acks = multi_acks
        self.config.meshcore.rx_delay_ms = rx_dly

        if self.radio_driver:
            try:
                if new_alias:
                    self.radio_driver.set_node_name(new_alias)
                self.radio_driver.set_radio_params(freq, bw, sf, cr, tx, path_hash_mode=path_mode)
                self.radio_driver.set_autoadd_contacts(autoadd)
                self.radio_driver.set_advert_location_policy(loc_policy)
                self.radio_driver.set_multi_acks(multi_acks)
                self.radio_driver.set_tuning_params(rx_delay_ms=rx_dly)
            except Exception as e:
                logger.warning(f"Could not apply radio parameters to hardware: {e}")

        # 2. Update Pixoo
        self.config.pixoo.ip_address = self.ip_input.text().strip()
        self.config.pixoo.brightness = self.bright_slider.value()
        self.config.pixoo.alert_duration_secs = self.alert_dur_spin.value()
        self.config.pixoo.flash_count = self.flash_count_spin.value()
        self.config.pixoo.page_duration_secs = self.page_dur_spin.value()
        if hasattr(self, "chk_show_live_mirror"):
            self.config.pixoo.show_live_mirror = self.chk_show_live_mirror.isChecked()

        # Quiet hours
        self.config.quiet_hours.enabled = self.chk_quiet.isChecked()
        self.config.quiet_hours.start_time = self.quiet_start.text().strip()
        self.config.quiet_hours.end_time = self.quiet_end.text().strip()
        self.config.quiet_hours.action = self.quiet_mode.currentData() or "mute_flash"

        # 3. Update Channel Filters & Favorites
        new_filters: Dict[str, bool] = {}
        new_favorites = []
        batch_prefs = []
        if hasattr(self, "channel_table"):
            for row in range(self.channel_table.rowCount()):
                item = self.channel_table.item(row, 0)
                if not item:
                    continue
                chan_name = item.text().lstrip("#")
                pixoo_chk = self.channel_table.cellWidget(row, 1)
                fav_chk = self.channel_table.cellWidget(row, 2)

                is_pix = True
                if pixoo_chk and isinstance(pixoo_chk, QCheckBox):
                    is_pix = pixoo_chk.isChecked()
                new_filters[chan_name] = is_pix

                is_fav = False
                if fav_chk and isinstance(fav_chk, QCheckBox):
                    is_fav = fav_chk.isChecked()
                if is_fav:
                    new_favorites.append(chan_name)

                batch_prefs.append({
                    "name": chan_name,
                    "is_pixoo_enabled": is_pix,
                    "is_favorite": is_fav
                })

            if self.storage and batch_prefs:
                try:
                    self.storage.batch_update_channel_preferences(batch_prefs)
                except Exception as e:
                    logger.warning(f"Failed to batch update channel preferences: {e}")

            self.config.pixoo.channel_filters = new_filters
            self.config.favorite_channels = new_favorites
            self.config.favorites = list(new_favorites)
            try:
                bus.emit(EventType.CHANNELS_UPDATED, None)
                bus.emit(EventType.FAVORITES_UPDATED, None)
            except Exception as e:
                logger.warning(f"Failed to emit channel update events: {e}")

        # 4. Update App UI Colors & Map Settings
        if hasattr(self, "chk_freshness"):
            self.config.meshcore.node_freshness_fading = self.chk_freshness.isChecked()
        self._sync_color_pickers_to_config()

        # 5. Update Pixoo Matrix Colors
        if hasattr(self, "btn_col_channel"):
            self.config.pixoo_colors.channel_color = self.btn_col_channel.current_hex
        if hasattr(self, "btn_col_alert"):
            self.config.pixoo_colors.alert_color = self.btn_col_alert.current_hex
        if hasattr(self, "btn_col_msg"):
            self.config.pixoo_colors.message_color = self.btn_col_msg.current_hex
        if hasattr(self, "btn_col_bg"):
            self.config.pixoo_colors.background_color = self.btn_col_bg.current_hex
        if hasattr(self, "btn_col_sender"):
            self.config.pixoo_colors.sender_color = self.btn_col_sender.current_hex
        if hasattr(self, "btn_col_star"):
            self.config.pixoo_colors.favorite_star_color = self.btn_col_star.current_hex

        # 6. Update Notifications
        if hasattr(self, "chk_desktop_notif"):
            self.config.notifications.desktop_notifications = self.chk_desktop_notif.isChecked()
        if hasattr(self, "chk_mention_notif"):
            self.config.notifications.notify_on_node_mentions = self.chk_mention_notif.isChecked()
        if hasattr(self, "kw_list"):
            kw_list = []
            for i in range(self.kw_list.count()):
                kw_list.append(self.kw_list.item(i).text())
            self.config.notifications.watched_keywords = kw_list

        # 7. Update Rotations & Gateway
        if hasattr(self, "chk_telem_rot"):
            self.config.telemetry.enabled = self.chk_telem_rot.isChecked()
        if hasattr(self, "telem_interval_spin"):
            self.config.telemetry.interval_mins = self.telem_interval_spin.value()
        if hasattr(self, "chk_neigh_rot"):
            self.config.neighbours.enabled = self.chk_neigh_rot.isChecked()
        if hasattr(self, "neigh_interval_spin"):
            self.config.neighbours.interval_mins = self.neigh_interval_spin.value()
        if hasattr(self, "neigh_src_combo"):
            self.config.neighbours.source = self.neigh_src_combo.currentData() or "local"
        if hasattr(self, "target_rep_input"):
            self.config.neighbours.target_repeater_node_id = self.target_rep_input.text().strip()

        if hasattr(self, "chk_gate"):
            self.config.gateway.http_bridge_enabled = self.chk_gate.isChecked()
        if hasattr(self, "gate_port_spin"):
            self.config.gateway.http_port = self.gate_port_spin.value()

        if hasattr(self, "chk_sat_enabled"):
            self.config.satellites.enabled = self.chk_sat_enabled.isChecked()
            self.config.satellites.show_footprints = self.chk_sat_footprints.isChecked()
            self.config.satellites.show_ground_tracks = self.chk_sat_tracks.isChecked()
            self.config.satellites.update_interval_hours = self.sat_interval_spin.value()
            self.config.satellites.min_pass_elevation_deg = self.sat_min_el_spin.value()

            grps = []
            if self.chk_sat_stations.isChecked(): grps.append("stations")
            if self.chk_sat_amateur.isChecked(): grps.append("amateur")
            if self.chk_sat_weather.isChecked(): grps.append("weather")
            if self.chk_sat_cubesat.isChecked(): grps.append("cubesat")
            self.config.satellites.active_groups = grps or ["stations", "amateur"]

        if hasattr(self, "chk_mqtt_enabled"):
            self.config.mqtt.enabled = self.chk_mqtt_enabled.isChecked()
            self.config.mqtt.broker_host = self.mqtt_host_input.text().strip() or "localhost"
            self.config.mqtt.broker_port = self.mqtt_port_spin.value()
            self.config.mqtt.username = self.mqtt_user_input.text().strip()
            self.config.mqtt.password = self.mqtt_pass_input.text()
            self.config.mqtt.use_tls = self.chk_mqtt_tls.isChecked()
            if hasattr(self, "combo_mqtt_transport"):
                self.config.mqtt.transport = self.combo_mqtt_transport.currentData() or "tcp"
            if hasattr(self, "mqtt_ws_path_input"):
                self.config.mqtt.ws_path = self.mqtt_ws_path_input.text().strip() or "/mqtt"
            raw_topics = self.mqtt_topics_input.text().split(",")
            self.config.mqtt.subscribe_topics = [t.strip() for t in raw_topics if t.strip()]
            self.config.mqtt.publish_enabled = self.chk_mqtt_publish.isChecked()
            self.config.mqtt.publish_topic = self.mqtt_pub_topic_input.text().strip() or "meshcore/packets"
            if hasattr(self, "combo_mqtt_preset"):
                if self.combo_mqtt_preset.currentIndex() > 0:
                    self.config.mqtt.preset_name = self.combo_mqtt_preset.currentText()
                else:
                    self.config.mqtt.preset_name = ""

        if hasattr(self, "chk_show_splash"):
            self.config.show_splash_screen = self.chk_show_splash.isChecked()

        if hasattr(self, "chk_check_updates"):
            self.config.check_updates_on_startup = self.chk_check_updates.isChecked()

        if hasattr(self, "chk_show_chat_avatars"):
            self.config.show_chat_avatars = self.chk_show_chat_avatars.isChecked()

        if hasattr(self, "combo_avatar_style"):
            new_style = self.combo_avatar_style.currentData() or "droid"
            self.config.user_avatar_style = new_style
            try:
                from meshcore_tray.ui.avatar_generator import set_global_avatar_style
                set_global_avatar_style(new_style)
            except Exception as e:
                logger.warning(f"Failed to set global avatar style: {e}")

        # Save to disk
        try:
            self.config.save()
            logger.info("Successfully saved AppConfig to disk.")

            try:
                bus.emit(EventType.SETTINGS_UPDATED, self.config)
            except Exception as e:
                logger.warning(f"Failed to emit SETTINGS_UPDATED event: {e}")

            success_style = """
                QPushButton {
                    background-color: #059669;
                    color: #FFFFFF;
                    font-weight: bold;
                    border: 1px solid #10B981;
                    border-radius: 6px;
                    padding: 6px 16px;
                }
            """
            if close_on_finish:
                if hasattr(self, "btn_save"):
                    self.btn_save.setStyleSheet(success_style)
                    self.btn_save.setText("✓ Saved!")
                if hasattr(self, "apply_status_lbl"):
                    self.apply_status_lbl.setText("✓ Settings saved to disk and applied live!")
                QTimer.singleShot(450, self.accept)
            else:
                if hasattr(self, "btn_apply"):
                    self.btn_apply.setStyleSheet(success_style)
                    self.btn_apply.setText("✓ Applied!")
                if hasattr(self, "apply_status_lbl"):
                    self.apply_status_lbl.setText("✓ Saved to config.json & applied live!")
                    QTimer.singleShot(3000, lambda: self.apply_status_lbl.setText(""))

                def _restore_apply():
                    try:
                        if hasattr(self, "btn_apply"):
                            self.btn_apply.setText(orig_text)
                            self.btn_apply.setStyleSheet(orig_style)
                            self.btn_apply.setEnabled(True)
                    except Exception:
                        pass
                QTimer.singleShot(1800, _restore_apply)
        except Exception as err:
            logger.error(f"Failed to write config.json to disk: {err}", exc_info=True)
            if target_btn:
                target_btn.setStyleSheet("background-color: #DC2626; color: #FFFFFF;")
                target_btn.setText("❌ Error")
                QTimer.singleShot(2500, lambda: (target_btn.setText(orig_text), target_btn.setStyleSheet(orig_style), target_btn.setEnabled(True)))
            if hasattr(self, "apply_status_lbl"):
                self.apply_status_lbl.setText(f"❌ Error: {err}")

    def _load_settings_into_form(self):
        """Populates all UI input widgets with the latest values from self.config."""
        try:
            # 1. Radio & Node
            if hasattr(self, "port_combo"):
                idx = self.port_combo.findData(self.config.meshcore.serial_port)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
            if hasattr(self, "baud_combo"):
                idx = self.baud_combo.findData(self.config.meshcore.baudrate)
                if idx >= 0:
                    self.baud_combo.setCurrentIndex(idx)
            if hasattr(self, "mode_combo"):
                idx = self.mode_combo.findData(self.config.meshcore.connection_type)
                if idx >= 0:
                    self.mode_combo.setCurrentIndex(idx)
            if hasattr(self, "alias_input"):
                self.alias_input.setText(self.config.meshcore.node_alias or "")
            if hasattr(self, "node_id_input"):
                self.node_id_input.setText(self.config.meshcore.node_id or "")
            if hasattr(self, "home_lat_input") and hasattr(self, "home_lon_input"):
                self.home_lat_input.setValue(self.config.meshcore.latitude)
                self.home_lon_input.setValue(self.config.meshcore.longitude)
            if hasattr(self, "preset_combo"):
                idx = self.preset_combo.findText(self.config.meshcore.radio_preset)
                if idx >= 0:
                    self.preset_combo.setCurrentIndex(idx)
            if hasattr(self, "freq_spin"):
                self.freq_spin.setValue(self.config.meshcore.frequency_mhz)
            if hasattr(self, "bw_combo"):
                idx = self.bw_combo.findData(self.config.meshcore.bandwidth_khz)
                if idx >= 0:
                    self.bw_combo.setCurrentIndex(idx)
            if hasattr(self, "sf_combo"):
                idx = self.sf_combo.findData(self.config.meshcore.spreading_factor)
                if idx >= 0:
                    self.sf_combo.setCurrentIndex(idx)
            if hasattr(self, "cr_combo"):
                idx = self.cr_combo.findData(self.config.meshcore.coding_rate)
                if idx >= 0:
                    self.cr_combo.setCurrentIndex(idx)
            if hasattr(self, "tx_spin"):
                self.tx_spin.setValue(self.config.meshcore.tx_power_dbm)
            if hasattr(self, "path_mode_combo"):
                idx = self.path_mode_combo.findData(self.config.meshcore.path_hash_mode)
                if idx >= 0:
                    self.path_mode_combo.setCurrentIndex(idx)
            if hasattr(self, "chk_autoadd"):
                self.chk_autoadd.setChecked(self.config.meshcore.autoadd_contacts)
            if hasattr(self, "chk_auto_prune_hardware"):
                self.chk_auto_prune_hardware.setChecked(getattr(self.config.meshcore, "auto_prune_hardware_contacts", True))
            if hasattr(self, "loc_policy_combo"):
                idx = self.loc_policy_combo.findData(self.config.meshcore.advert_loc_policy)
                if idx >= 0:
                    self.loc_policy_combo.setCurrentIndex(idx)
            if hasattr(self, "chk_multi_acks"):
                self.chk_multi_acks.setChecked(self.config.meshcore.multi_acks)
            if hasattr(self, "rx_delay_spin"):
                self.rx_delay_spin.setValue(self.config.meshcore.rx_delay_ms)

            # 2. Pixoo
            if hasattr(self, "ip_input"):
                self.ip_input.setText(self.config.pixoo.ip_address)
            if hasattr(self, "bright_slider"):
                self.bright_slider.setValue(self.config.pixoo.brightness)
            if hasattr(self, "alert_dur_spin"):
                self.alert_dur_spin.setValue(self.config.pixoo.alert_duration_secs)
            if hasattr(self, "flash_count_spin"):
                self.flash_count_spin.setValue(self.config.pixoo.flash_count)
            if hasattr(self, "page_dur_spin"):
                self.page_dur_spin.setValue(self.config.pixoo.page_duration_secs)
            if hasattr(self, "chk_show_live_mirror"):
                self.chk_show_live_mirror.setChecked(self.config.pixoo.show_live_mirror)
            if hasattr(self, "chk_quiet"):
                self.chk_quiet.setChecked(self.config.quiet_hours.enabled)
            if hasattr(self, "quiet_start"):
                self.quiet_start.setText(self.config.quiet_hours.start_time)
            if hasattr(self, "quiet_end"):
                self.quiet_end.setText(self.config.quiet_hours.end_time)
            if hasattr(self, "quiet_mode"):
                idx = self.quiet_mode.findData(self.config.quiet_hours.action)
                if idx >= 0:
                    self.quiet_mode.setCurrentIndex(idx)

            # 3. App Colors & Map
            if hasattr(self, "chk_freshness"):
                self.chk_freshness.setChecked(self.config.meshcore.node_freshness_fading)
            if hasattr(self, "_sync_config_to_color_pickers"):
                self._sync_config_to_color_pickers()
            if hasattr(self, "combo_map_base"):
                cur_layer = getattr(self.config, "map_base_layer", "canvas")
                idx = self.combo_map_base.findData(cur_layer)
                if idx >= 0:
                    self.combo_map_base.setCurrentIndex(idx)
            if hasattr(self, "txt_carto_key"):
                self.txt_carto_key.setText(getattr(self.config, "carto_api_key", ""))
            if hasattr(self, "txt_map3d_key"):
                self.txt_map3d_key.setText(getattr(self.config, "map3d_api_key", ""))
            if hasattr(self, "txt_map3d_custom_style_url"):
                self.txt_map3d_custom_style_url.setText(getattr(self.config, "map3d_custom_style_url", ""))

            # 4. Pixoo Colors
            if hasattr(self, "btn_col_channel"):
                self.btn_col_channel.set_color(self.config.pixoo_colors.channel_color)
            if hasattr(self, "btn_col_alert"):
                self.btn_col_alert.set_color(self.config.pixoo_colors.alert_color)
            if hasattr(self, "btn_col_msg"):
                self.btn_col_msg.set_color(self.config.pixoo_colors.message_color)
            if hasattr(self, "btn_col_bg"):
                self.btn_col_bg.set_color(self.config.pixoo_colors.background_color)
            if hasattr(self, "btn_col_sender"):
                self.btn_col_sender.set_color(self.config.pixoo_colors.sender_color)
            if hasattr(self, "btn_col_star"):
                self.btn_col_star.set_color(self.config.pixoo_colors.favorite_star_color)

            # 5. Notifications
            if hasattr(self, "chk_desktop_notif"):
                self.chk_desktop_notif.setChecked(self.config.notifications.desktop_notifications)
            if hasattr(self, "chk_mention_notif"):
                self.chk_mention_notif.setChecked(self.config.notifications.notify_on_node_mentions)

            # 6. Rotations & Gateway
            if hasattr(self, "chk_telem_rot"):
                self.chk_telem_rot.setChecked(self.config.telemetry.enabled)
            if hasattr(self, "telem_interval_spin"):
                self.telem_interval_spin.setValue(self.config.telemetry.interval_mins)
            if hasattr(self, "chk_neigh_rot"):
                self.chk_neigh_rot.setChecked(self.config.neighbours.enabled)
            if hasattr(self, "neigh_interval_spin"):
                self.neigh_interval_spin.setValue(self.config.neighbours.interval_mins)
            if hasattr(self, "neigh_src_combo"):
                idx = self.neigh_src_combo.findData(self.config.neighbours.source)
                if idx >= 0:
                    self.neigh_src_combo.setCurrentIndex(idx)
            if hasattr(self, "target_rep_input"):
                self.target_rep_input.setText(self.config.neighbours.target_repeater_node_id)
            if hasattr(self, "chk_gate"):
                self.chk_gate.setChecked(self.config.gateway.http_bridge_enabled)
            if hasattr(self, "gate_port_spin"):
                self.gate_port_spin.setValue(self.config.gateway.http_port)

            # Satellites
            if hasattr(self, "chk_sat_enabled"):
                sat_cfg = getattr(self.config, "satellites", None)
                if sat_cfg:
                    self.chk_sat_enabled.setChecked(sat_cfg.enabled)
                    self.chk_sat_footprints.setChecked(sat_cfg.show_footprints)
                    self.chk_sat_tracks.setChecked(sat_cfg.show_ground_tracks)
                    self.sat_interval_spin.setValue(sat_cfg.update_interval_hours)
                    self.sat_min_el_spin.setValue(sat_cfg.min_pass_elevation_deg)
                    grps = sat_cfg.active_groups or []
                    self.chk_sat_stations.setChecked("stations" in grps)
                    self.chk_sat_amateur.setChecked("amateur" in grps)
                    self.chk_sat_weather.setChecked("weather" in grps)
                    self.chk_sat_cubesat.setChecked("cubesat" in grps)

            # MQTT
            if hasattr(self, "chk_mqtt_enabled"):
                mqtt_cfg = getattr(self.config, "mqtt", None)
                if mqtt_cfg:
                    self.chk_mqtt_enabled.setChecked(mqtt_cfg.enabled)
                    self.mqtt_host_input.setText(mqtt_cfg.broker_host or "localhost")
                    self.mqtt_port_spin.setValue(mqtt_cfg.broker_port or 1883)
                    self.mqtt_user_input.setText(mqtt_cfg.username or "")
                    self.mqtt_pass_input.setText(mqtt_cfg.password or "")
                    self.chk_mqtt_tls.setChecked(mqtt_cfg.use_tls)
                    if hasattr(self, "combo_mqtt_transport"):
                        t_idx = self.combo_mqtt_transport.findData(getattr(mqtt_cfg, "transport", "tcp"))
                        if t_idx >= 0:
                            self.combo_mqtt_transport.setCurrentIndex(t_idx)
                    if hasattr(self, "mqtt_ws_path_input"):
                        self.mqtt_ws_path_input.setText(getattr(mqtt_cfg, "ws_path", "/mqtt"))
                    self.mqtt_topics_input.setText(", ".join(mqtt_cfg.subscribe_topics or []))
                    self.chk_mqtt_publish.setChecked(mqtt_cfg.publish_enabled)
                    self.mqtt_pub_topic_input.setText(mqtt_cfg.publish_topic or "meshcore/packets")

            # General / Startup
            if hasattr(self, "chk_show_splash"):
                self.chk_show_splash.setChecked(getattr(self.config, "show_splash_screen", True))
            if hasattr(self, "chk_check_updates"):
                self.chk_check_updates.setChecked(getattr(self.config, "check_updates_on_startup", True))
            if hasattr(self, "chk_show_chat_avatars"):
                self.chk_show_chat_avatars.setChecked(getattr(self.config, "show_chat_avatars", True))
            if hasattr(self, "combo_avatar_style"):
                style = getattr(self.config, "user_avatar_style", "droid")
                idx = self.combo_avatar_style.findData(style)
                if idx >= 0:
                    self.combo_avatar_style.setCurrentIndex(idx)
        except Exception as e:
            logger.error(f"Error loading settings into form: {e}", exc_info=True)

    def _save_and_close(self):
        """Saves and closes the dialog/view."""
        self._apply_settings(close_on_finish=True)

    def result(self) -> int:
        """Compatibility method returning dialog result code (0 = rejected / active, 1 = accepted)."""
        return getattr(self, "_result", 0)

    def setResult(self, r: int):
        self._result = int(r)

    def accept(self):
        """Compatibility method: emits close_requested to switch back to chat & map."""
        self._result = 1
        self.close_requested.emit()

    def reject(self):
        """Compatibility method: emits close_requested without saving additional changes."""
        self._result = 0
        self.close_requested.emit()

    def exec(self):
        """Compatibility method for callers expecting a modal dialog."""
        self.show()

    def reload(self):
        """Refreshes dynamic settings tabs such as hop preferences, channel tables, and form inputs."""
        self._load_settings_into_form()
        if hasattr(self, "_refresh_hop_preferences_ui"):
            self._refresh_hop_preferences_ui()
        if hasattr(self, "_refresh_phantom_nodes_ui"):
            self._refresh_phantom_nodes_ui()


# Alias for backward compatibility
SettingsDialog = SettingsWidget

