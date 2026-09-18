"""Repeater Console & Login Widget for MeshCore Repeaters."""

import html
import re
from datetime import datetime
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QTextBrowser, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextOption
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import NodeContact, MessageEnvelope
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon
from meshcore_tray.ui.link_parser import format_message_text_with_links


class RepeaterConsoleWidget(QWidget):
    """Repeater login and interactive command console displayed in the center view."""

    back_to_chat_requested = pyqtSignal()
    send_command_requested = pyqtSignal(str, str)  # (repeater_id, command_text)
    show_neighbors_on_map_requested = pyqtSignal(object, list)  # (NodeContact, list)

    def __init__(self, radio_driver=None, storage=None, parent=None):
        super().__init__(parent)
        self.radio_driver = radio_driver
        self.storage = storage
        self.current_contact: Optional[NodeContact] = None
        self.last_neighbors_data = []
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # 1. Top Header Bar
        header_bar = QFrame()
        header_bar.setStyleSheet("background-color: #161B22; border: 1px solid #30363D; border-radius: 8px; padding: 6px;")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(12, 8, 12, 8)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(54, 54)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setText("📡")
        self.icon_lbl.setStyleSheet("font-size: 36px; background: transparent; border: none;")
        header_layout.addWidget(self.icon_lbl)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        self.title_lbl = QLabel("Repeater Console")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #58A6FF;")
        self.sub_lbl = QLabel("Authentication & Command Interface")
        self.sub_lbl.setStyleSheet("font-size: 12px; color: #8B949E;")
        info_layout.addWidget(self.title_lbl)
        info_layout.addWidget(self.sub_lbl)
        header_layout.addLayout(info_layout)

        header_layout.addStretch()

        self.btn_edit_location = QPushButton("📍 Edit GPS Location")
        self.btn_edit_location.setStyleSheet(
            "background-color: #0284C7; color: #FFFFFF; border: 1px solid #0369A1; "
            "border-radius: 6px; padding: 6px 12px; font-weight: bold; font-size: 12px;"
        )
        self.btn_edit_location.clicked.connect(self._on_edit_location_clicked)
        header_layout.addWidget(self.btn_edit_location)

        self.telemetry_badge = QLabel("SNR: -- dB • RSSI: -- dBm")
        self.telemetry_badge.setStyleSheet(
            "background-color: #2B2F38; color: #9CA3AF; border: 1px solid #414143; "
            "border-radius: 6px; padding: 4px 10px; font-weight: bold; font-size: 11px;"
        )
        header_layout.addWidget(self.telemetry_badge)

        self.btn_back = QPushButton("🔙 Back to Chat")
        self.btn_back.setStyleSheet(
            "background-color: #2B2F38; color: #E5E7EB; border: 1px solid #414143; "
            "border-radius: 6px; padding: 6px 12px; font-weight: bold;"
        )
        self.btn_back.clicked.connect(self.back_to_chat_requested.emit)
        header_layout.addWidget(self.btn_back)

        main_layout.addWidget(header_bar)

        # Horizontal Action Toolbar directly under banner heading
        action_toolbar = QFrame()
        action_toolbar.setStyleSheet("background-color: #2B2D31; border: 1px solid #383A40; border-radius: 8px; padding: 4px;")
        at_layout = QHBoxLayout(action_toolbar)
        at_layout.setContentsMargins(8, 4, 8, 4)
        at_layout.setSpacing(8)

        btn_action_neighbors = QPushButton("📍 Show Neighbors on Map")
        btn_action_neighbors.setStyleSheet("""
            QPushButton {
                background-color: #1F3A5C; color: #58A6FF; border: 1px solid #388BFD;
                border-radius: 6px; padding: 6px 12px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #264B78; color: #FFFFFF; }
        """)
        btn_action_neighbors.clicked.connect(self._on_show_neighbors_on_map_clicked)
        at_layout.addWidget(btn_action_neighbors)

        btn_action_ping = QPushButton("⚡ Ping Repeater")
        btn_action_ping.setStyleSheet("""
            QPushButton {
                background-color: #2B2F38; color: #E5E7EB; border: 1px solid #414143;
                border-radius: 6px; padding: 6px 12px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #383B44; color: #FFFFFF; }
        """)
        btn_action_ping.clicked.connect(lambda: self._send_command("!info"))
        at_layout.addWidget(btn_action_ping)

        btn_action_telemetry = QPushButton("🔄 Query Telemetry")
        btn_action_telemetry.setStyleSheet("""
            QPushButton {
                background-color: #2B2F38; color: #E5E7EB; border: 1px solid #414143;
                border-radius: 6px; padding: 6px 12px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #383B44; color: #FFFFFF; }
        """)
        btn_action_telemetry.clicked.connect(lambda: self._send_command("!status"))
        at_layout.addWidget(btn_action_telemetry)

        btn_action_creds = QPushButton("🔑 Login / Credentials")
        btn_action_creds.setStyleSheet("""
            QPushButton {
                background-color: #238636; color: #FFFFFF; border: 1px solid #2EA043;
                border-radius: 6px; padding: 6px 12px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #2EA043; }
        """)
        btn_action_creds.clicked.connect(lambda: self.pwd_input.setFocus())
        at_layout.addWidget(btn_action_creds)

        at_layout.addStretch()
        main_layout.addWidget(action_toolbar)
        auth_card = QFrame()
        auth_card.setStyleSheet("background-color: #222327; border: 1px solid #414143; border-radius: 8px; padding: 10px;")
        auth_layout = QVBoxLayout(auth_card)
        auth_layout.setSpacing(8)

        auth_title = QLabel("🔐 <b>Repeater Login & Authentication</b>")
        auth_title.setStyleSheet("color: #E5E7EB; font-size: 13px;")
        auth_layout.addWidget(auth_title)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)

        self.pwd_input = QLineEdit()
        self.pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd_input.setPlaceholderText("Password (leave blank if none)...")
        self.pwd_input.setStyleSheet(
            "background-color: #1C1C1C; color: #E5E7EB; border: 1px solid #414143; "
            "border-radius: 6px; padding: 7px 10px; font-size: 13px;"
        )
        self.pwd_input.returnPressed.connect(self._on_login_clicked)
        auth_row.addWidget(self.pwd_input, 1)

        self.btn_login = QPushButton("🔑 Log In / Authenticate")
        self.btn_login.setStyleSheet(
            "background-color: #238636; color: #FFFFFF; border: 1px solid #2EA043; "
            "border-radius: 6px; padding: 7px 16px; font-weight: bold; font-size: 13px;"
        )
        self.btn_login.clicked.connect(self._on_login_clicked)
        auth_row.addWidget(self.btn_login)

        auth_layout.addLayout(auth_row)
        main_layout.addWidget(auth_card)

        # 3. Quick Action Commands Bar (2-Row Grid for smooth responsive resizing)
        cmd_card = QFrame()
        cmd_card.setStyleSheet("background-color: #222327; border: 1px solid #414143; border-radius: 8px; padding: 8px 12px;")
        cmd_layout = QVBoxLayout(cmd_card)
        cmd_layout.setSpacing(6)

        lbl_quick = QLabel("⚡ <b>Quick Commands:</b>")
        lbl_quick.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        cmd_layout.addWidget(lbl_quick)

        quick_grid = QGridLayout()
        quick_grid.setSpacing(6)

        quick_buttons = [
            ("📊 !status", "!status", 0, 0),
            ("🧭 !path", "!path", 0, 1),
            ("🔋 !bat", "!bat", 0, 2),
            ("🌐 !neighbors", "!neighbors", 1, 0),
            ("ℹ️ !info", "!info", 1, 1),
            ("🔄 !reboot", "!reboot", 1, 2),
        ]

        for text, cmd, r, c in quick_buttons:
            btn = QPushButton(text)
            btn.setStyleSheet(
                "background-color: #2B2F38; color: #E5E7EB; border: 1px solid #414143; "
                "border-radius: 5px; padding: 5px 8px; font-size: 11px; font-weight: 500;"
            )
            btn.clicked.connect(lambda _, c_text=cmd: self._send_command(c_text))
            quick_grid.addWidget(btn, r, c)

        cmd_layout.addLayout(quick_grid)

        self.btn_map_neighbors = QPushButton("🗺️ View Neighbours on Map")
        self.btn_map_neighbors.setStyleSheet("""
            QPushButton {
                background-color: #1F3A5C; color: #58A6FF; border: 1px solid #388BFD;
                border-radius: 5px; padding: 6px 12px; font-size: 11px; font-weight: bold;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: #264B78; color: #FFFFFF;
            }
        """)
        self.btn_map_neighbors.clicked.connect(self._on_show_neighbors_on_map_clicked)
        cmd_layout.addWidget(self.btn_map_neighbors)

        main_layout.addWidget(cmd_card)

        # 4. Interactive Live Terminal Output
        term_label = QLabel("📟 <b>Repeater Command Session & Responses</b>")
        term_label.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        main_layout.addWidget(term_label)

        self.term_box = QTextBrowser()
        self.term_box.setReadOnly(True)
        self.term_box.setOpenExternalLinks(True)
        self.term_box.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.term_box.setStyleSheet(
            "background-color: #1C1C1C; color: #39D353; font-family: 'Cascadia Code', 'Fira Code', 'DejaVu Sans Mono', monospace; "
            "font-size: 12px; border: 1px solid #414143; border-radius: 8px; padding: 10px;"
        )
        main_layout.addWidget(self.term_box, 1)

        # 5. CLI Command Input Line
        cli_row = QHBoxLayout()
        cli_row.setSpacing(8)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Type custom repeater command (e.g. !help, !set power 22)...")
        self.cmd_input.setStyleSheet(
            "background-color: #222327; color: #E5E7EB; border: 1px solid #414143; "
            "border-radius: 6px; padding: 8px 12px; font-family: monospace; font-size: 13px;"
        )
        self.cmd_input.returnPressed.connect(self._on_send_custom_command)
        cli_row.addWidget(self.cmd_input, 1)

        self.btn_send = QPushButton("Send Command ⚡")
        self.btn_send.setStyleSheet(
            "background-color: #3E4452; color: #FFFFFF; border: 1px solid #525A6C; "
            "border-radius: 6px; padding: 8px 18px; font-weight: bold; font-size: 13px;"
        )
        self.btn_send.clicked.connect(self._on_send_custom_command)
        cli_row.addWidget(self.btn_send)

        main_layout.addLayout(cli_row)

    def set_repeater(self, contact: NodeContact):
        """Switches active console target to the selected repeater."""
        self.current_contact = contact
        clean_alias = contact.alias.replace("[Rep]", "").replace("[rep]", "").strip()
        self.title_lbl.setText(f"📡 Repeater: @{clean_alias}")
        self.sub_lbl.setText(f"Node ID: {contact.node_id} • Status: Known Repeater")

        if hasattr(self, "icon_lbl"):
            avatar_icon = get_contact_avatar_icon(contact.node_id, contact.alias, is_repeater=True, size=54)
            if avatar_icon and not avatar_icon.isNull():
                self.icon_lbl.setPixmap(avatar_icon.pixmap(54, 54))
                self.icon_lbl.setStyleSheet("background: transparent; border: none; border-radius: 8px;")
            else:
                self.icon_lbl.setText("📡")
                self.icon_lbl.setStyleSheet("font-size: 36px; background: transparent; border: none;")

        snr_col = "#3FB950" if contact.snr_db >= 0 else "#D29922"
        self.telemetry_badge.setText(f"SNR: {contact.snr_db:+.1f} dB • RSSI: {contact.rssi_dbm:.1f} dBm")
        self.telemetry_badge.setStyleSheet(
            f"background-color: #161B22; color: {snr_col}; border: 1px solid {snr_col}; "
            "border-radius: 6px; padding: 4px 10px; font-weight: bold; font-size: 11px;"
        )

        self.pwd_input.clear()
        self.cmd_input.clear()
        self.term_box.clear()
        self.last_neighbors_data = []
        if hasattr(self, "btn_map_neighbors"):
            self.btn_map_neighbors.setText("🗺️ View Neighbours on Map")
        self._log_system(f"Connected console session to @{clean_alias} ({contact.node_id})")
        self._log_system("Enter password above or send commands directly.")

    def handle_incoming_message(self, msg: MessageEnvelope):
        """Displays incoming packet response from the repeater in the console."""
        if not self.current_contact:
            return

        cur_id = (self.current_contact.node_id or "").lower().lstrip("!")
        cur_alias = (self.current_contact.alias or "").lower()
        cur_pub = (self.current_contact.public_key or "").lower()
        msg_id = (msg.sender_id or "").lower().lstrip("!")
        msg_name = (msg.sender_name or "").lower()

        match = (
            (msg_id and (msg_id in cur_id or cur_id in msg_id or msg_id in cur_pub or cur_pub in msg_id))
            or (msg_name and (msg_name in cur_alias or cur_alias in msg_name))
            or (msg.metadata and msg.metadata.get("is_repeater_response", False))
        )
        if match:
            clean_text = html.unescape(msg.text)
            t_str = msg.timestamp[11:19] if (msg.timestamp and len(msg.timestamp) >= 19) else datetime.now().strftime("%H:%M:%S")

            # Update telemetry badge if SNR/RSSI present
            if msg.metadata and ("snr" in msg.metadata or "rssi" in msg.metadata):
                snr_val = msg.metadata.get("snr")
                rssi_val = msg.metadata.get("rssi")
                parts = []
                if snr_val is not None:
                    parts.append(f"SNR: {snr_val:+.1f} dB")
                if rssi_val is not None:
                    parts.append(f"RSSI: {rssi_val} dBm")
                if parts:
                    self.telemetry_badge.setText(" • ".join(parts))
                    self.telemetry_badge.setStyleSheet(
                        "background-color: #1B4728; color: #3FB950; border: 1px solid #2EA043; "
                        "border-radius: 6px; padding: 4px 10px; font-weight: bold; font-size: 11px;"
                    )

            snr_str = f" <span style='color: #6E7681;'>[{msg.metadata.get('snr', 0):+.1f}dB]</span>" if (msg.metadata and "snr" in msg.metadata) else ""
            lines = clean_text.splitlines()
            if lines:
                first_color = "#3FB950" if any(k in lines[0] for k in ["✅", "📊", "🌐", "ℹ️", "🔋", "🧭"]) else "#58A6FF"
                line0_html = format_message_text_with_links(lines[0], link_color="#58A6FF")
                self.term_box.append(f"<span style='color: #8B949E;'>[{t_str}]</span> <span style='color: #58A6FF; font-weight:bold;'>&lt;&lt;&lt;</span> <span style='color: {first_color}; font-weight: bold;'>{line0_html}</span>{snr_str}")
                for sub_line in lines[1:]:
                    sub_line_html = format_message_text_with_links(sub_line, link_color="#7EE787")
                    self.term_box.append(f"<span style='color: #8B949E;'>[{t_str}]</span> <span style='color: #30363D;'>&nbsp;&nbsp;&nbsp;</span> <span style='color: #7EE787;'>{sub_line_html}</span>")
            self.term_box.verticalScrollBar().setValue(self.term_box.verticalScrollBar().maximum())

            # Detect and parse neighbours telemetry
            if "neighbor" in clean_text.lower() or "neighbour" in clean_text.lower() or (msg.metadata and "neighbours" in msg.metadata):
                parsed = self._parse_neighbors(clean_text, msg.metadata)
                if parsed:
                    self.last_neighbors_data = parsed
                    if hasattr(self, "btn_map_neighbors"):
                        self.btn_map_neighbors.setText(f"🗺️ View {len(parsed)} Neighbours on Map")
                    self.show_neighbors_on_map_requested.emit(self.current_contact, parsed)

            # Detect and parse repeater GPS coordinates (e.g. from !status, !info, !get lat)
            lat_m = re.search(r'(?:lat(?:itude)?|position|loc(?:ation)?)\s*[:=]?\s*([+-]?\d+\.\d+)', clean_text, re.IGNORECASE)
            lon_m = re.search(r'(?:lon(?:gitude)?)\s*[:=]?\s*([+-]?\d+\.\d+)', clean_text, re.IGNORECASE)
            if lat_m and lon_m and self.current_contact:
                try:
                    c_lat = float(lat_m.group(1))
                    c_lon = float(lon_m.group(1))
                    if -90 <= c_lat <= 90 and -180 <= c_lon <= 180 and (c_lat != 0.0 or c_lon != 0.0):
                        self.current_contact.latitude = c_lat
                        self.current_contact.longitude = c_lon
                        if self.storage:
                            self.storage.save_contact(self.current_contact)
                        bus.emit(EventType.MAP_NODES_UPDATED, None)
                        self._log_system(f"✓ Updated repeater GPS coordinates on map: ({c_lat:.5f}, {c_lon:.5f})")
                except Exception:
                    pass

    def _on_edit_location_clicked(self):
        if not self.current_contact:
            return
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=self.radio_driver,
            parent=self.window(),
            initial_contact=self.current_contact
        )
        if dlg.exec():
            if self.storage:
                updated = self.storage.get_contact(self.current_contact.node_id)
                if updated:
                    self.current_contact = updated
            self._log_system("✓ Contact coordinates updated.")

    def _parse_neighbors(self, text: str, metadata: Optional[dict] = None) -> list:
        results = []
        if metadata and "neighbours" in metadata and isinstance(metadata["neighbours"], list):
            for n in metadata["neighbours"]:
                pk = str(n.get("pubkey") or n.get("node_id") or "").lstrip("!")
                s = n.get("snr", 0.0)
                sec = n.get("secs_ago", 0)
                time_str = n.get("time_ago") or (f"{sec//3600}h ago" if sec >= 3600 else f"{sec//60}m ago")
                results.append({
                    "node_id": pk,
                    "snr_str": f"{s:+.1f} dB" if isinstance(s, (int, float)) else str(s),
                    "time_str": time_str,
                    "snr_num": float(s) if isinstance(s, (int, float)) else 0.0,
                    "secs_ago": sec
                })
            if results:
                return results

        import re
        pat1 = re.compile(r"[•\*\-]?\s*!?([0-9a-fA-F]+)\s*\(\s*SNR:\s*([+-]?\d+(?:\.\d+)?)\s*dB,\s*([^\)]+)\)")
        pat2 = re.compile(r"[•\*\-]?\s*!?([0-9a-fA-F]+)\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*dB\s*\)")
        for line in text.splitlines():
            m = pat1.search(line)
            if m:
                node_id = m.group(1).strip()
                snr_val = float(m.group(2).strip())
                time_str = m.group(3).strip()
                results.append({
                    "node_id": node_id,
                    "snr_str": f"{snr_val:+.1f} dB",
                    "time_str": time_str,
                    "snr_num": snr_val,
                    "secs_ago": 0
                })
                continue
            m2 = pat2.search(line)
            if m2:
                node_id = m2.group(1).strip()
                snr_val = float(m2.group(2).strip())
                results.append({
                    "node_id": node_id,
                    "snr_str": f"{snr_val:+.1f} dB",
                    "time_str": "recently",
                    "snr_num": snr_val,
                    "secs_ago": 0
                })
        return results

    def _on_show_neighbors_on_map_clicked(self):
        if self.last_neighbors_data and self.current_contact:
            self.show_neighbors_on_map_requested.emit(self.current_contact, self.last_neighbors_data)
        else:
            self._send_command("!neighbors")

    def _on_login_clicked(self):
        pwd = self.pwd_input.text().strip()
        if pwd:
            self._send_command(f"!login {pwd}")
        else:
            self._send_command("!login")
        self.pwd_input.clear()

    def _on_send_custom_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        self._send_command(cmd)
        self.cmd_input.clear()

    def _send_command(self, cmd_text: str):
        if not self.current_contact:
            return
        t_str = datetime.now().strftime("%H:%M:%S")
        if cmd_text.lower().strip() == "!login":
            disp_cmd = "!login"
        elif cmd_text.lower().startswith("!login "):
            disp_cmd = "!login ********"
        else:
            disp_cmd = cmd_text
        self.term_box.append(f"<span style='color: #8B949E;'>[{t_str}]</span> <span style='color: #D29922; font-weight:bold;'>&gt;&gt;&gt;</span> <span style='color: #F0F6FC;'>{html.escape(disp_cmd)}</span>")
        self.term_box.verticalScrollBar().setValue(self.term_box.verticalScrollBar().maximum())

        # Emit signal to send message packet
        self.send_command_requested.emit(self.current_contact.node_id, cmd_text)

    def _log_system(self, text: str):
        t_str = datetime.now().strftime("%H:%M:%S")
        self.term_box.append(f"<span style='color: #6E7681;'>[{t_str}] [SYSTEM] {html.escape(text)}</span>")
