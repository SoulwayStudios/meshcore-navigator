"""Contact Discovery and Manual Contact Management Dialog.

Provides:
1. Manual contact creation / editing with GPS coordinates, auto key generation,
   and smart Cumbria peak presets (Skiddaw, Lank Rigg, Blake Fell, etc.).
2. Contact Card / URI importer (meshcore://contact/...).
3. Discovered overheard mesh node scanner (packet path hops, RF adverts, message senders).
4. Hardware radio sync and flash push controls.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import secrets
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import NodeContact
from meshcore_tray.storage import Storage

logger = logging.getLogger("meshcore_tray.contact_dialog")

# Common Cumbria / UK peaks and repeater sites for instant 1-click coordinate population
CUMBRIA_PEAK_PRESETS = [
    ("Skiddaw Summit (931m, Cumbria)", 54.6514, -3.1484),
    ("Lank Rigg (541m, Cumbria)", 54.49467, -3.40394),
    ("Blake Fell (573m, Cumbria)", 54.5684, -3.4076),
    ("Dent Fell (352m, Cumbria)", 54.503603, -3.487967),
    ("Helvellyn (950m, Cumbria)", 54.5270, -3.0175),
    ("Scafell Pike (978m, Cumbria)", 54.4542, -3.2116),
    ("Great Gable (899m, Cumbria)", 54.4819, -3.2192),
    ("Southport (Lancashire)", 53.652142, -2.998592),
    ("Workington / Allotment Rep (Cumbria)", 54.63682, -3.53887),
    ("Whitehaven / Kells (Cumbria)", 54.545761, -3.5983),
]


class ContactDiscoveryDialog(QDialog):
    """Modern dark-themed dialog for manual contact creation and mesh discovery."""

    contact_saved = pyqtSignal(NodeContact)

    def __init__(
        self,
        storage: Optional[Storage] = None,
        driver: Optional[Any] = None,
        parent: Optional[QWidget] = None,
        initial_name: str = "",
        initial_lat: Optional[float] = None,
        initial_lon: Optional[float] = None,
        initial_contact: Optional[NodeContact] = None,
    ):
        super().__init__(parent)
        self.storage = storage
        self.driver = driver
        self.initial_contact = initial_contact
        if initial_contact:
            initial_name = initial_name or initial_contact.alias
            if initial_lat is None:
                initial_lat = initial_contact.latitude
            if initial_lon is None:
                initial_lon = initial_contact.longitude
        self.setWindowTitle("MeshCore Contact Manager & Discovery")
        self.resize(780, 600)
        self.setStyleSheet("""
            QDialog {
                background-color: #12151A;
                color: #E5E7EB;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #2D3340;
                border-radius: 8px;
                background-color: #181B22;
                padding: 12px;
            }
            QTabBar::tab {
                background-color: #1E222B;
                color: #9CA3AF;
                padding: 8px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #2D3340;
                color: #38BDF8;
                border-bottom: 2px solid #38BDF8;
            }
            QLineEdit, QComboBox {
                background-color: #222631;
                color: #F3F4F6;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #38BDF8;
            }
            QPushButton {
                background-color: #2D3340;
                color: #E5E7EB;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #374151;
                color: #FFFFFF;
            }
            QTableWidget {
                background-color: #181B22;
                color: #E5E7EB;
                border: 1px solid #2D3340;
                gridline-color: #2D3340;
                border-radius: 6px;
                selection-background-color: #2D3748;
            }
            QHeaderView::section {
                background-color: #1E222B;
                color: #9CA3AF;
                padding: 6px;
                border: 1px solid #2D3340;
                font-size: 11px;
                font-weight: 600;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Title
        title_lbl = QLabel("<b>📡 Contact Discovery & Management</b>")
        title_lbl.setStyleSheet("font-size: 16px; color: #38BDF8;")
        layout.addWidget(title_lbl)

        # Tab Widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Build Tabs
        self._init_tab_manual(initial_name, initial_lat, initial_lon)
        self._init_tab_discovered()
        self._init_tab_hardware()

        # Bottom Dialog Actions
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        bottom_bar.addWidget(self.btn_close)

        layout.addLayout(bottom_bar)

    def _init_tab_manual(
        self,
        initial_name: str = "",
        initial_lat: Optional[float] = None,
        initial_lon: Optional[float] = None,
    ):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # Top Card Import Box
        import_box = QGroupBox("Import Contact Card / URI")
        import_box.setStyleSheet("QGroupBox { font-weight: bold; color: #9CA3AF; }")
        imp_layout = QHBoxLayout(import_box)
        self.edit_import_uri = QLineEdit()
        self.edit_import_uri.setPlaceholderText("Paste meshcore://contact/... or base64 contact card")
        btn_do_import = QPushButton("📥 Import Card")
        btn_do_import.clicked.connect(self._import_contact_card)
        imp_layout.addWidget(self.edit_import_uri, 1)
        imp_layout.addWidget(btn_do_import)
        layout.addWidget(import_box)

        # Form Group
        form_group = QGroupBox("Contact Details")
        form_group.setStyleSheet("QGroupBox { font-weight: bold; color: #E5E7EB; }")
        form = QFormLayout(form_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # Name / Alias
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g. 🌐 MCC Skiddaw Mk3")
        form.addRow("<b>Node Name:</b>", self.edit_name)

        # Landmark Suggestion Banner (hidden until landmark detected)
        self.suggestion_banner = QFrame()
        self.suggestion_banner.setMinimumHeight(38)
        self.suggestion_banner.setStyleSheet("""
            QFrame {
                background-color: #0F2236;
                border: 1px solid #0284C7;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                color: #38BDF8;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        sugg_layout = QHBoxLayout(self.suggestion_banner)
        sugg_layout.setContentsMargins(10, 4, 8, 4)
        sugg_layout.setSpacing(8)
        self.lbl_suggestion = QLabel("")
        self.btn_apply_sugg = QPushButton("Apply Coordinates")
        self.btn_apply_sugg.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
        """)
        self.btn_apply_sugg.clicked.connect(self._apply_detected_preset)
        sugg_layout.addWidget(self.lbl_suggestion, 1)
        sugg_layout.addWidget(self.btn_apply_sugg)
        self.suggestion_banner.setVisible(False)
        form.addRow("", self.suggestion_banner)

        self.edit_name.textChanged.connect(self._on_name_changed)
        if initial_name:
            self.edit_name.setText(initial_name)

        # Node ID / Public Key
        key_layout = QHBoxLayout()
        self.edit_pubkey = QLineEdit()
        self.edit_pubkey.setPlaceholderText("64-hex char public key or 8-12 hex ID (Auto-generated if blank)")
        btn_gen_key = QPushButton("🎲 Auto Key")
        btn_gen_key.setToolTip("Generate a unique deterministic 32-byte public key")
        btn_gen_key.clicked.connect(self._generate_random_key)
        key_layout.addWidget(self.edit_pubkey, 1)
        key_layout.addWidget(btn_gen_key)
        form.addRow("<b>Public Key / ID:</b>", key_layout)

        # Role / Type
        role_layout = QHBoxLayout()
        self.radio_repeater = QRadioButton("🌐 Repeater (Type 2)")
        self.radio_repeater.setChecked(True)
        self.radio_client = QRadioButton("📱 Client / Companion (Type 1)")
        role_layout.addWidget(self.radio_repeater)
        role_layout.addWidget(self.radio_client)
        role_layout.addStretch()
        form.addRow("<b>Node Role:</b>", role_layout)

        # GPS Coordinates
        coords_layout = QHBoxLayout()
        self.edit_lat = QLineEdit()
        self.edit_lat.setPlaceholderText("Latitude (e.g. 54.6514)")
        if initial_lat is not None:
            self.edit_lat.setText(str(initial_lat))

        self.edit_lon = QLineEdit()
        self.edit_lon.setPlaceholderText("Longitude (e.g. -3.1484)")
        if initial_lon is not None:
            self.edit_lon.setText(str(initial_lon))

        self.combo_presets = QComboBox()
        self.combo_presets.addItem("📍 Select Cumbria Peak / Preset...")
        for name, lat, lon in CUMBRIA_PEAK_PRESETS:
            self.combo_presets.addItem(f"{name} ({lat}, {lon})", (lat, lon))
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)

        coords_layout.addWidget(self.edit_lat, 1)
        coords_layout.addWidget(self.edit_lon, 1)
        coords_layout.addWidget(self.combo_presets, 1)
        form.addRow("<b>GPS Location:</b>", coords_layout)

        # Path Mode
        self.combo_path_mode = QComboBox()
        self.combo_path_mode.addItem("Direct / Flood Route (Default)", -1)
        self.combo_path_mode.addItem("1-Byte Hash Mode (Direct & Routed)", 0)
        self.combo_path_mode.addItem("2-Byte Hash Mode (Extended)", 1)
        self.combo_path_mode.addItem("3-Byte Hash Mode (Long / Multi-Hop)", 2)
        form.addRow("<b>Routing Mode:</b>", self.combo_path_mode)

        # Favorite
        self.btn_fav = QPushButton("⭐ Mark as Favorite")
        self.btn_fav.setCheckable(True)
        self.btn_fav.setChecked(False)
        self.btn_fav.clicked.connect(self._toggle_favorite_ui)
        form.addRow("<b>Favorite:</b>", self.btn_fav)

        if self.initial_contact:
            if self.initial_contact.public_key:
                self.edit_pubkey.setText(self.initial_contact.public_key)
            elif self.initial_contact.node_id:
                self.edit_pubkey.setText(self.initial_contact.node_id)
            self.radio_repeater.setChecked(self.initial_contact.is_repeater)
            self.radio_client.setChecked(not self.initial_contact.is_repeater)
            self.btn_fav.setChecked(self.initial_contact.is_favorite)
            self._toggle_favorite_ui()
            pref_mode = getattr(self.initial_contact, "out_path_hash_mode", -1)
            if self.storage and hasattr(self.storage, "get_overheard_path_modes"):
                try:
                    ov_dict = self.storage.get_overheard_path_modes()
                    clean_id = (self.initial_contact.node_id or "").lstrip("!@").lower()
                    clean_al = (self.initial_contact.alias or "").lower()
                    clean_pk = (self.initial_contact.public_key or "").lower()
                    ov_entry = (
                        ov_dict.get(clean_id)
                        or ov_dict.get(clean_al)
                        or (ov_dict.get(clean_pk[:6]) if len(clean_pk) >= 6 else None)
                        or (ov_dict.get(clean_pk[:4]) if len(clean_pk) >= 4 else None)
                    )
                    if ov_entry and ov_entry.get("path_mode", -1) > pref_mode:
                        pref_mode = ov_entry["path_mode"]
                except Exception:
                    pass

            if pref_mode >= 0:
                for idx in range(self.combo_path_mode.count()):
                    if self.combo_path_mode.itemData(idx) == pref_mode:
                        self.combo_path_mode.setCurrentIndex(idx)
                        break

        layout.addWidget(form_group)

        # Bottom Save Button Bar
        save_bar = QHBoxLayout()
        save_bar.addStretch()
        self.btn_save = QPushButton("💾 Save to Contacts & Map")
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #38BDF8;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369A1;
                border-color: #7DD3FC;
            }
        """)
        self.btn_save.clicked.connect(self._save_manual_contact)
        save_bar.addWidget(self.btn_save)
        layout.addLayout(save_bar)

        self.tabs.addTab(tab, "➕ Add Contact")

        # Run name detection for initial name if passed
        if initial_name:
            self._on_name_changed(initial_name)

    def _init_tab_discovered(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # Header controls
        ctrl_bar = QHBoxLayout()
        ctrl_bar.addWidget(QLabel("<b>Overheard Mesh Traffic:</b>"))

        self.edit_disc_filter = QLineEdit()
        self.edit_disc_filter.setPlaceholderText("🔍 Filter discovered nodes...")
        self.edit_disc_filter.setStyleSheet("""
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
        self.edit_disc_filter.textChanged.connect(self._filter_discovered_table)
        ctrl_bar.addWidget(self.edit_disc_filter, 1)

        btn_rescan = QPushButton("🔄 Rescan Mesh")
        btn_rescan.clicked.connect(self.refresh_discovered_nodes)
        ctrl_bar.addWidget(btn_rescan)
        layout.addLayout(ctrl_bar)

        # Discovered Table
        self.disc_table = QTableWidget()
        self.disc_table.setColumnCount(6)
        self.disc_table.setHorizontalHeaderLabels([
            "Node ID / Hash", "Heard Name", "Source", "Heard Count", "Last Seen", "Action"
        ])
        header = self.disc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.disc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.disc_table.verticalHeader().setVisible(False)
        layout.addWidget(self.disc_table, 1)

        self.tabs.addTab(tab, "📡 Discovered Nodes")
        self.refresh_discovered_nodes()

    def _init_tab_hardware(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Radio Status Group
        hw_group = QGroupBox("MeshCore Radio Hardware Sync")
        hw_group.setStyleSheet("QGroupBox { font-weight: bold; color: #E5E7EB; }")
        hw_layout = QVBoxLayout(hw_group)
        hw_layout.setSpacing(10)

        is_connected = bool(self.driver and self.driver.is_connected())
        status_text = "🟢 Connected to MeshCore hardware radio" if is_connected else "⚪ Radio hardware offline"
        status_color = "#10B981" if is_connected else "#9CA3AF"

        self.lbl_hw_status = QLabel(f"<b>Status:</b> <span style='color: {status_color};'>{status_text}</span>")
        hw_layout.addWidget(self.lbl_hw_status)

        desc_lbl = QLabel(
            "MeshCore nodes maintain an on-board contact list in flash memory for local RF routing. "
            "You can synchronize contacts from the node flash or trigger hardware contact discovery."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        hw_layout.addWidget(desc_lbl)

        btn_bar = QHBoxLayout()
        btn_sync = QPushButton("📥 Pull All Contacts from Radio Flash")
        btn_sync.clicked.connect(self._sync_from_radio)
        btn_bar.addWidget(btn_sync)

        btn_autoadd = QPushButton("⚡ Enable Hardware Auto-Add Adverts")
        btn_autoadd.setToolTip("Programs radio firmware to automatically store overheard adverts to flash")
        btn_autoadd.clicked.connect(self._enable_hardware_autoadd)
        btn_bar.addWidget(btn_autoadd)

        btn_prune = QPushButton("🧹 Prune Stale Radio Contacts")
        btn_prune.setToolTip("Safely frees slots in Heltec V3 flash memory so new contacts can be received over RF.\nContacts remain permanently saved in your local app database and map.")
        btn_prune.clicked.connect(self._prune_hardware_contacts)
        btn_bar.addWidget(btn_prune)

        hw_layout.addLayout(btn_bar)
        layout.addWidget(hw_group)
        layout.addStretch()

        self.tabs.addTab(tab, "📻 Radio Hardware")

    def _prune_hardware_contacts(self):
        if not self.driver or not self.driver.is_connected():
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
            if hasattr(self.driver, "prune_hardware_contacts_now"):
                self.driver.prune_hardware_contacts_now()
                QMessageBox.information(
                    self,
                    "Pruning Started",
                    "Stale contact pruning initiated on Heltec V3 hardware flash.\nAll contacts remain permanently saved in your local app database."
                )

    # --- Manual Contact Logic ---

    def _on_name_changed(self, text: str):
        """Smart detection for Cumbria peaks/summits in the node name."""
        lower = text.lower()
        matched_preset = None
        for name, lat, lon in CUMBRIA_PEAK_PRESETS:
            keyword = name.split()[0].lower()
            if keyword in lower:
                matched_preset = (name, lat, lon)
                break

        if matched_preset:
            p_name, p_lat, p_lon = matched_preset
            self.lbl_suggestion.setText(f"🏔️ <b>Detected {p_name.split('(')[0].strip()}:</b> Suggest coordinates ({p_lat}, {p_lon})")
            self._current_detected_preset = (p_lat, p_lon)
            self.suggestion_banner.setVisible(True)
        else:
            self._current_detected_preset = None
            self.suggestion_banner.setVisible(False)

    def _apply_detected_preset(self):
        if hasattr(self, "_current_detected_preset") and self._current_detected_preset:
            lat, lon = self._current_detected_preset
            self.edit_lat.setText(str(lat))
            self.edit_lon.setText(str(lon))
            self.radio_repeater.setChecked(True)
            self.suggestion_banner.setVisible(False)

    def _on_preset_selected(self, index: int):
        data = self.combo_presets.currentData()
        if data and isinstance(data, tuple) and len(data) == 2:
            lat, lon = data
            self.edit_lat.setText(str(lat))
            self.edit_lon.setText(str(lon))

    def _generate_random_key(self):
        """Generates a valid 32-byte (64-char hex) public key."""
        key = secrets.token_hex(32)
        self.edit_pubkey.setText(key)

    def _toggle_favorite_ui(self):
        is_fav = self.btn_fav.isChecked()
        self.btn_fav.setText("⭐ Favorited" if is_fav else "⭐ Mark as Favorite")
        self.btn_fav.setStyleSheet(
            "background-color: #FBBF24; color: #1E293B; font-weight: bold;" if is_fav else ""
        )

    def _import_contact_card(self):
        """Parses meshcore://contact/... or base64/JSON contact cards."""
        raw = self.edit_import_uri.text().strip()
        if not raw:
            QMessageBox.warning(self, "Empty Card", "Please paste a contact URI or card string.")
            return

        try:
            # Format 1: JSON payload
            if raw.startswith("{") and raw.endswith("}"):
                data = json.loads(raw)
                self._populate_from_dict(data)
                QMessageBox.information(self, "Card Imported", f"Imported details for {data.get('adv_name', 'Contact')}")
                return

            # Format 2: meshcore://contact?name=...&pubkey=...&lat=...&lon=...
            if "meshcore://" in raw or "contact" in raw:
                import urllib.parse
                parsed = urllib.parse.urlparse(raw)
                qs = urllib.parse.parse_qs(parsed.query)
                name = qs.get("name", [""])[0] or qs.get("alias", [""])[0]
                pubkey = qs.get("pubkey", [""])[0] or qs.get("key", [""])[0]
                lat_str = qs.get("lat", [""])[0]
                lon_str = qs.get("lon", [""])[0]

                if name:
                    self.edit_name.setText(name)
                if pubkey:
                    self.edit_pubkey.setText(pubkey)
                if lat_str:
                    self.edit_lat.setText(lat_str)
                if lon_str:
                    self.edit_lon.setText(lon_str)
                QMessageBox.information(self, "Card Imported", f"Imported URI parameters for '{name or pubkey}'")
                return

            # Format 3: Raw 64-char hex public key
            clean_hex = raw.lstrip("!@").strip()
            if len(clean_hex) in (8, 12, 64) and all(c in "0123456789abcdefABCDEF" for c in clean_hex):
                self.edit_pubkey.setText(clean_hex)
                if not self.edit_name.text():
                    self.edit_name.setText(f"Node {clean_hex[:8]}")
                QMessageBox.information(self, "Key Imported", f"Set public key: {clean_hex[:12]}...")
                return

            QMessageBox.warning(self, "Unrecognized Format", "Could not parse contact format. Please check the input string.")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Error importing contact card: {e}")

    def _populate_from_dict(self, data: dict):
        name = data.get("adv_name") or data.get("alias") or ""
        pubkey = data.get("public_key") or data.get("node_id") or ""
        lat = data.get("adv_lat") or data.get("latitude")
        lon = data.get("adv_lon") or data.get("longitude")
        is_rep = bool(data.get("type") == 2 or data.get("is_repeater"))

        if name:
            self.edit_name.setText(str(name))
        if pubkey:
            self.edit_pubkey.setText(str(pubkey))
        if lat is not None:
            self.edit_lat.setText(str(lat))
        if lon is not None:
            self.edit_lon.setText(str(lon))
        self.radio_repeater.setChecked(is_rep)
        self.radio_client.setChecked(not is_rep)

    def _save_manual_contact(self):
        alias = self.edit_name.text().strip()
        if not alias:
            QMessageBox.warning(self, "Missing Name", "Please enter a node name or alias.")
            return

        pubkey = self.edit_pubkey.text().strip().lstrip("!@")
        if not pubkey:
            # Deterministically hash the alias so the node ID remains consistent across re-adds
            hash_key = hashlib.sha256(alias.encode("utf-8")).hexdigest()
            pubkey = hash_key

        node_id = pubkey[:12] if len(pubkey) >= 12 else pubkey

        # Coordinates
        lat, lon = None, None
        lat_text = self.edit_lat.text().strip()
        lon_text = self.edit_lon.text().strip()
        if lat_text and lon_text:
            try:
                lat = float(lat_text)
                lon = float(lon_text)
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    QMessageBox.warning(self, "Invalid Coordinates", "Latitude must be between -90 and 90, Longitude between -180 and 180.")
                    return
            except ValueError:
                QMessageBox.warning(self, "Invalid Coordinates", "Latitude and Longitude must be valid decimal numbers.")
                return

        is_rep = self.radio_repeater.isChecked()
        is_fav = self.btn_fav.isChecked()
        path_mode = int(self.combo_path_mode.currentData())

        contact = NodeContact(
            node_id=node_id,
            alias=alias,
            is_favorite=is_fav,
            last_seen=datetime.now(timezone.utc).isoformat(),
            public_key=pubkey,
            is_repeater=is_rep,
            latitude=lat,
            longitude=lon,
            out_path_len=-1 if path_mode == -1 else 0,
            out_path_hash_mode=path_mode if path_mode >= 0 else -1,
            out_path="",
        )

        # 1. Save to SQLite storage
        if self.storage:
            self.storage.save_contact(contact)
            bus.emit(EventType.MAP_NODES_UPDATED, None)
            bus.emit(EventType.NODE_DISCOVERED, contact)

        # 2. Push to radio hardware if driver is available
        if self.driver:
            self.driver.add_or_update_contact(contact)

        self.contact_saved.emit(contact)
        QMessageBox.information(
            self,
            "Contact Saved",
            f"✓ Added <b>{alias}</b> to contacts!<br>"
            f"Node ID: <code>{node_id}</code><br>"
            f"Role: {'🌐 Repeater' if is_rep else '📱 Client'}<br>"
            f"Location: {f'({lat:.4f}, {lon:.4f})' if lat is not None else 'No GPS'}"
        )
        self.accept()

    # --- Discovered Nodes Logic ---

    def refresh_discovered_nodes(self):
        """Scans packet paths, neighbours, and messages for unlinked nodes."""
        if not self.storage:
            return

        discovered = self.storage.get_discovered_nodes()
        self._all_discovered = discovered
        self._render_discovered_table(discovered)

    def _render_discovered_table(self, nodes: List[Dict[str, Any]]):
        self.disc_table.setRowCount(0)
        self.disc_table.setRowCount(len(nodes))

        for row, n in enumerate(nodes):
            nid = n.get("node_id", "")
            alias = n.get("alias", "")
            src = n.get("source", "")
            count = str(n.get("count", 1))
            ts = n.get("last_seen", "")
            if ts and len(ts) >= 16:
                ts = ts.replace("T", " ")[:16]

            item_id = QTableWidgetItem(nid)
            item_alias = QTableWidgetItem(alias)
            item_src = QTableWidgetItem(src)
            item_count = QTableWidgetItem(count)
            item_ts = QTableWidgetItem(ts)

            item_id.setFlags(item_id.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item_alias.setFlags(item_alias.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item_src.setFlags(item_src.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item_count.setFlags(item_count.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item_ts.setFlags(item_ts.flags() ^ Qt.ItemFlag.ItemIsEditable)

            self.disc_table.setItem(row, 0, item_id)
            self.disc_table.setItem(row, 1, item_alias)
            self.disc_table.setItem(row, 2, item_src)
            self.disc_table.setItem(row, 3, item_count)
            self.disc_table.setItem(row, 4, item_ts)

            # Action button
            btn_add = QPushButton("➕ Add")
            btn_add.setStyleSheet("""
                background-color: #0284C7; color: white; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;
            """)
            btn_add.clicked.connect(lambda _, node_dict=n: self._prefill_from_discovered(node_dict))
            self.disc_table.setCellWidget(row, 5, btn_add)

    def _filter_discovered_table(self, query: str):
        q = query.strip().lower()
        if not hasattr(self, "_all_discovered"):
            return
        if not q:
            self._render_discovered_table(self._all_discovered)
            return

        filtered = [
            n for n in self._all_discovered
            if q in n.get("node_id", "").lower()
            or q in n.get("alias", "").lower()
            or q in n.get("source", "").lower()
        ]
        self._render_discovered_table(filtered)

    def _prefill_from_discovered(self, node_dict: Dict[str, Any]):
        """Transfers a discovered overheard node into Tab 1 for manual review and editing."""
        nid = node_dict.get("node_id", "")
        alias = node_dict.get("alias", "")
        is_rep = bool(node_dict.get("is_repeater", True))
        lat = node_dict.get("latitude")
        lon = node_dict.get("longitude")

        self.edit_name.setText(alias)
        self.edit_pubkey.setText(nid)
        self.radio_repeater.setChecked(is_rep)
        self.radio_client.setChecked(not is_rep)

        if lat is not None:
            self.edit_lat.setText(str(lat))
        if lon is not None:
            self.edit_lon.setText(str(lon))

        self.tabs.setCurrentIndex(0)

    # --- Hardware Radio Sync Logic ---

    def _sync_from_radio(self):
        if not self.driver or not self.driver.is_connected():
            QMessageBox.warning(self, "Offline", "Radio hardware is not currently connected.")
            return

        success = self.driver.sync_contacts_from_device()
        if success:
            QMessageBox.information(self, "Syncing", "Requested contact synchronization from node flash memory.")
        else:
            QMessageBox.warning(self, "Sync Failed", "Could not trigger contact sync on radio.")

    def _enable_hardware_autoadd(self):
        if not self.driver or not self.driver.is_connected() or not self.driver.client:
            QMessageBox.warning(self, "Offline", "Radio hardware is not currently connected.")
            return

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self.driver.client.commands.set_autoadd_config(1))
            QMessageBox.information(
                self,
                "Auto-Add Enabled",
                "Enabled hardware auto-add mode (`set_autoadd_config(1)`). "
                "The radio firmware will now automatically register new adverts into node flash."
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed setting autoadd config: {e}")
