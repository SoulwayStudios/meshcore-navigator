"""Chat Stream & Search Results View Widget."""

import html
import logging
from datetime import datetime, timezone
import re
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QUrl
from PyQt6.QtGui import QTextOption, QKeySequence, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QPushButton, QSizePolicy, QMenu, QDialog, QFormLayout,
    QTextEdit, QMessageBox, QLineEdit
)
from meshcore_tray.core.models import MessageEnvelope, NodeContact
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon
from meshcore_tray.ui.link_parser import format_message_text_with_links, extract_urls

logger = logging.getLogger("meshcore_tray.chat_widget")


def insert_break_opportunities(text: str, max_chunk: int = 16) -> str:
    """Inserts zero-width spaces (\\u200b) as soft break points into long strings and tokens without whitespace.

    Allows Qt word wrap to break long paths (e.g. fefebc→fc2a45 or 80>17>95), URLs, hashes,
    and long unbroken words instead of letting them overflow the card or blow out the viewport.
    """
    if not text:
        return ""
    zwsp = "\u200b"
    if zwsp in text:
        return text

    lines = text.split("\n")
    processed_lines = []
    # Delimiters common in mesh routing paths, URLs, packets, and callsigns
    break_chars = set("→>/>\\_:-|,;?=&@+~.")

    for line in lines:
        tokens = re.split(r"(\s+)", line)
        processed_tokens = []
        for token in tokens:
            if not token or token.isspace():
                processed_tokens.append(token)
                continue

            char_buf = []
            chars_since_break = 0
            t_len = len(token)
            for i, ch in enumerate(token):
                char_buf.append(ch)
                if ch in break_chars and i < t_len - 1:
                    char_buf.append(zwsp)
                    chars_since_break = 0
                else:
                    chars_since_break += 1
                    if chars_since_break >= max_chunk and i < t_len - 1:
                        char_buf.append(zwsp)
                        chars_since_break = 0
            processed_tokens.append("".join(char_buf))
        processed_lines.append("".join(processed_tokens))

    return "\n".join(processed_lines)


class MessageBodyLabel(QLabel):
    """Word-wrapping QLabel for chat bubbles that renders clickable HTML links
    and safely breaks long unbroken strings to prevent oversized minimum size hints."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setOpenExternalLinks(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if text:
            self.setText(text)

    def setText(self, text: str):
        self._raw_text = text
        super().setText(format_message_text_with_links(text, insert_break_func=insert_break_opportunities))

    def raw_text(self) -> str:
        return getattr(self, "_raw_text", self.text().replace("\u200b", ""))

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(min(sh.width(), 60), sh.height())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            sel = self.selectedText().replace("\u200b", "")
            if sel:
                clip = QApplication.clipboard()
                if clip:
                    clip.setText(sel)
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        sel = self.selectedText().replace("\u200b", "")
        p = self.parent()
        while p and not isinstance(p, MessageBubble):
            p = p.parent()
        if p and hasattr(p, "_show_context_menu"):
            p._show_context_menu(p.mapFromGlobal(event.globalPos()), selected_text=sel)
        else:
            if sel:
                menu = QMenu(self)
                act = menu.addAction("📋 Copy Selection")
                act.triggered.connect(lambda: QApplication.clipboard().setText(sel))
                menu.exec(event.globalPos())
            else:
                super().contextMenuEvent(event)


class ChatContainerWidget(QWidget):
    """Container widget inside the message stream QScrollArea with bounded minimum size."""

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(min(sh.width(), 100), sh.height())


class NodeInfoDialog(QDialog):
    """Modal inspector displaying full node contact metadata, signal metrics, and packet details."""

    def __init__(self, msg: MessageEnvelope, storage=None, parent=None):
        super().__init__(parent)
        self.msg = msg
        self.storage = storage
        self.setWindowTitle(f"Node Info — @{msg.sender_name}")
        self.resize(440, 380)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(f"<b>📡 Node Information: @{html.escape(self.msg.sender_name)}</b>")
        title.setStyleSheet("font-size: 14px; color: #58A6FF;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)

        form.addRow("<b>Callsign / Alias:</b>", QLabel(self.msg.sender_name))
        form.addRow("<b>Node Hex ID:</b>", QLabel(self.msg.sender_id))

        contact = self.storage.get_contact(self.msg.sender_id) if self.storage else None
        if contact and contact.public_key:
            pub_lbl = MessageBodyLabel(contact.public_key)
            pub_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #8B949E;")
            form.addRow("<b>Public Key:</b>", pub_lbl)

        chan_str = f"#{self.msg.channel.lstrip('#')}" if not self.msg.is_direct_message else "Direct Message"
        form.addRow("<b>Channel Source:</b>", QLabel(chan_str))

        snr = self.msg.metadata.get("snr", 0.0) if self.msg.metadata else 0.0
        rssi = self.msg.metadata.get("rssi", -100.0) if self.msg.metadata else -100.0
        snr_color = "#3FB950" if snr >= 0 else "#D29922"
        form.addRow("<b>SNR / Signal Quality:</b>", QLabel(f"<span style='color:{snr_color}; font-weight:bold;'>{snr:+.1f} dB</span>"))
        form.addRow("<b>RSSI / Signal Level:</b>", QLabel(f"{rssi:.1f} dBm"))

        rep_status = "Repeater Node [Rep]" if (contact and contact.is_repeater) else "Standard Mesh Node"
        form.addRow("<b>Node Type:</b>", QLabel(rep_status))
        form.addRow("<b>Timestamp:</b>", QLabel(self.msg.timestamp))

        layout.addLayout(form)

        # Raw packet preview
        raw_text = html.unescape((self.msg.metadata.get("raw") if self.msg.metadata else None) or self.msg.text)
        lbl_raw = QLabel("<b>Raw Packet Payload:</b>")
        layout.addWidget(lbl_raw)

        raw_box = QTextEdit()
        raw_box.setReadOnly(True)
        raw_box.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        raw_box.setPlainText(raw_text)
        raw_box.setMaximumHeight(70)
        raw_box.setStyleSheet("background-color: #161B22; color: #8B949E; font-family: monospace; border: 1px solid #21262D; border-radius: 4px;")
        layout.addWidget(raw_box)

        btn_close = QPushButton("Close")
        btn_close.setObjectName("primaryButton")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


class UnreadDivider(QFrame):
    """Divider line indicating starting point of new/unread messages."""

    def __init__(self, color: str = "#F85149", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(8)

        line_left = QFrame()
        line_left.setFrameShape(QFrame.Shape.HLine)
        line_left.setStyleSheet(f"background-color: {color}; height: 1px; border: none;")

        lbl = QLabel("● NEW MESSAGES")
        lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lbl.setAutoFillBackground(False)
        lbl.setStyleSheet(f"background: transparent; border: none; color: {color}; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;")

        line_right = QFrame()
        line_right.setFrameShape(QFrame.Shape.HLine)
        line_right.setStyleSheet(f"background-color: {color}; height: 1px; border: none;")

        layout.addWidget(line_left, 1)
        layout.addWidget(lbl)
        layout.addWidget(line_right, 1)


class MessageBubble(QFrame):
    """Modern chat bubble card with right-click context menu (Favorite, Block, Delete, Reply, View Info)."""

    reply_requested = pyqtSignal(str)        # (reply_text_prefix)
    dm_requested = pyqtSignal(str)           # (contact_id)
    message_deleted = pyqtSignal(str)        # (msg_id)
    favorite_toggled = pyqtSignal(str, bool) # (sender_name, is_fav)
    user_blocked = pyqtSignal(str)           # (sender_name)
    visualise_path_requested = pyqtSignal(object) # (msg: MessageEnvelope)
    resend_requested = pyqtSignal(object)         # (msg: MessageEnvelope)

    def __init__(self, msg: MessageEnvelope, config=None, storage=None, parent=None):
        super().__init__(parent)
        self.setObjectName("messageBubble")
        self.msg = msg
        self.config = config
        self.storage = storage
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._init_ui()

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(min(sh.width(), 80), sh.height())

    def _init_ui(self):
        bubble_layout = QHBoxLayout(self)
        bubble_layout.setContentsMargins(10, 10, 10, 10)
        bubble_layout.setSpacing(10)

        # Content column for header + alerts + body
        content_box = QVBoxLayout()
        content_box.setContentsMargins(0, 0, 0, 0)
        content_box.setSpacing(4)

        # Header Row: Sender, Star, Channel Tag, Time
        header = QHBoxLayout()
        header.setSpacing(6)

        is_fav = self.msg.is_favorite or bool(self.config and self.config.is_user_favorite(self.msg.sender_id, self.msg.sender_name))
        star_str = "⭐ " if is_fav else ""
        fav_col = self.config.app_colors.favorite_user_color if (self.config and hasattr(self.config, "app_colors")) else "#FFD700"
        sender_lbl = QLabel(f"<b>{star_str}{html.escape(self.msg.sender_name)}</b>")
        sender_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        sender_lbl.setAutoFillBackground(False)
        sender_lbl.setStyleSheet(f"background: transparent; border: none; color: {fav_col};" if is_fav else "background: transparent; border: none; color: #E5E7EB;")

        chan_clean = f"#{self.msg.channel.lstrip('#')}" if not self.msg.is_direct_message else "DM"
        chan_tag = QLabel(chan_clean)
        chan_tag.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        chan_tag.setAutoFillBackground(False)
        chan_tag.setStyleSheet("background: transparent; border: none; color: #9CA3AF; font-size: 11px;")

        time_str = ""
        if self.msg.timestamp:
            try:
                dt = datetime.fromisoformat(str(self.msg.timestamp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                time_str = dt.astimezone().strftime("%H:%M")
            except Exception:
                time_str = self.msg.timestamp[11:16] if len(self.msg.timestamp) >= 16 else ""

        self.time_lbl = QLabel(time_str)
        self.time_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.time_lbl.setAutoFillBackground(False)
        self.time_lbl.setStyleSheet("background: transparent; border: none; color: #9CA3AF; font-size: 11px;")

        header.addWidget(sender_lbl)
        header.addWidget(chan_tag)
        header.addStretch()

        # Repeats heard indicator for outgoing messages
        if self.msg.is_outgoing:
            self.repeats_badge = QLabel()
            self.repeats_badge.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.set_repeats_heard(getattr(self.msg, "repeats_heard", 0))
            header.addWidget(self.repeats_badge)

        # Telemetry badge if available (SNR/RSSI)
        if self.msg.metadata and "snr" in self.msg.metadata:
            snr_val = self.msg.metadata["snr"]
            snr_col = getattr(self.config.app_colors, "message_snr_color", "#3FB950") if (self.config and hasattr(self.config, "app_colors")) else "#3FB950"
            telem_lbl = QLabel(f"SNR: {snr_val:+.1f}dB")
            telem_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            telem_lbl.setAutoFillBackground(False)
            telem_lbl.setStyleSheet(f"background: transparent; border: none; color: {snr_col}; font-size: 10px; font-weight: bold;")
            header.addWidget(telem_lbl)

        header.addWidget(self.time_lbl)
        content_box.addLayout(header)

        # Watched keyword / Mention warning badge
        if self.msg.is_mention or self.msg.matched_keywords:
            kw_str = ", ".join(self.msg.matched_keywords) if self.msg.matched_keywords else "Mention"
            alert_badge = QLabel(f"🚨 ALERT: {html.escape(kw_str)}")
            alert_badge.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            alert_badge.setStyleSheet(
                "background-color: #5C1D24; color: #FF7B72; border: 1px solid #F85149; "
                "border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: bold;"
            )
            content_box.addWidget(alert_badge)

        # Message Body
        clean_text = html.unescape(self.msg.text)
        self.body_lbl = MessageBodyLabel(clean_text)
        self.body_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.body_lbl.setStyleSheet("background: transparent; border: none; color: #F3F4F6; font-size: 13px; line-height: 1.4;")
        content_box.addWidget(self.body_lbl)

        # Avatar inside the bubble: 38x38px fills the vertical height of Line 1 (heading) + Line 2 (body)
        show_avatars = getattr(self.config, "show_chat_avatars", True) if self.config else True
        if show_avatars:
            sender_id = getattr(self.msg, "sender_id", "") or ""
            sender_name = getattr(self.msg, "sender_name", "") or ""
            is_rep = False
            if self.storage and sender_id:
                try:
                    c = self.storage.get_contact(sender_id)
                    if c:
                        is_rep = bool(
                            getattr(c, "is_repeater", False)
                            or any(kw in (c.alias or "").upper() for kw in ("[REP]", "[REPEATER]", "[ROUTER]", "[RTR]", "[GW]"))
                            or any(kw in (getattr(c, "role", "") or "").upper() for kw in ("REPEATER", "ROUTER"))
                        )
                except Exception:
                    pass
            if not is_rep:
                is_rep = any(kw in sender_name.upper() for kw in ("[REP]", "[REPEATER]", "[ROUTER]", "[RTR]", "[GW]"))

            avatar_icon = get_contact_avatar_icon(sender_id, sender_name, is_repeater=is_rep, size=38)
            if avatar_icon and not avatar_icon.isNull():
                self.avatar_lbl = QLabel()
                self.avatar_lbl.setFixedSize(38, 38)
                self.avatar_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                self.avatar_lbl.setAutoFillBackground(False)
                self.avatar_lbl.setPixmap(avatar_icon.pixmap(38, 38))
                self.avatar_lbl.setStyleSheet("background: transparent; border: none;")
                role_text = "Repeater" if is_rep else "User"
                self.avatar_lbl.setToolTip(f"👤 {sender_name}\n🔑 ID: {sender_id}\n🏷️ {role_text}\n💬 Click to open Direct Message")
                self.avatar_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                self.avatar_lbl.mousePressEvent = lambda e: self.dm_requested.emit(self.msg.sender_id)

        # Inside the bubble layout:
        # Outgoing: Content on left, Avatar on right
        # Incoming: Avatar on left, Content on right
        if self.msg.is_outgoing:
            bubble_layout.addLayout(content_box, 1)
            if hasattr(self, "avatar_lbl"):
                bubble_layout.addWidget(self.avatar_lbl, 0, Qt.AlignmentFlag.AlignTop)
        else:
            if hasattr(self, "avatar_lbl"):
                bubble_layout.addWidget(self.avatar_lbl, 0, Qt.AlignmentFlag.AlignTop)
            bubble_layout.addLayout(content_box, 1)

        # Styling depending on Outgoing vs Incoming
        if self.msg.is_outgoing:
            self.setStyleSheet("""
                MessageBubble {
                    background-color: #2D333F;
                    border: 1px solid #4B5363;
                    border-radius: 8px;
                    margin-left: 60px;
                }
                MessageBubble QLabel {
                    background: transparent;
                    background-color: transparent;
                    border: none;
                }
            """)
        else:
            self.setStyleSheet("""
                MessageBubble {
                    background-color: #222327;
                    border: 1px solid #414143;
                    border-radius: 8px;
                    margin-right: 60px;
                }
                MessageBubble QLabel {
                    background: transparent;
                    background-color: transparent;
                    border: none;
                }
            """)

    def set_repeats_heard(self, repeats: int):
        """Updates the repeats heard indicator badge."""
        self.msg.repeats_heard = repeats
        if not hasattr(self, "repeats_badge"):
            return
        if repeats == 0:
            self.repeats_badge.setText("🔁 0 repeats heard (Click to resend)")
            self.repeats_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self.repeats_badge.setStyleSheet(
                "background-color: #374151; color: #FBBF24; border: 1px solid #F59E0B; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
            self.repeats_badge.setToolTip("Sent over LoRa. No repeater rebroadcasts heard yet. Click to resend or press Enter in chat!")
            self.repeats_badge.mousePressEvent = lambda e: self.resend_requested.emit(self.msg)
        elif repeats == 1:
            self.repeats_badge.setCursor(Qt.CursorShape.ArrowCursor)
            self.repeats_badge.mousePressEvent = None
            self.repeats_badge.setText("🔁 1 repeat heard")
            self.repeats_badge.setStyleSheet(
                "background-color: #163828; color: #34D399; border: 1px solid #10B981; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
            self.repeats_badge.setToolTip("Heard 1 repeater rebroadcast. Message is getting out!")
        else:
            self.repeats_badge.setCursor(Qt.CursorShape.ArrowCursor)
            self.repeats_badge.mousePressEvent = None
            self.repeats_badge.setText(f"🔁 {repeats} repeats heard")
            self.repeats_badge.setStyleSheet(
                "background-color: #163828; color: #34D399; border: 1px solid #10B981; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
            self.repeats_badge.setToolTip(f"Heard {repeats} repeater rebroadcasts across the mesh!")

    def _show_context_menu(self, pos, selected_text: str = ""):
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #222327; border: 1px solid #414143; border-radius: 6px; padding: 4px;")

        if selected_text:
            act_copy = menu.addAction("📋 Copy Selection")
        else:
            act_copy = menu.addAction("📋 Copy Message")

        found_urls = extract_urls(selected_text or self.msg.text)
        url_actions = []
        if found_urls:
            if len(found_urls) == 1:
                u = found_urls[0]
                short_u = u if len(u) <= 30 else (u[:27] + "...")
                act_open_u = menu.addAction(f"🌐 Open Link ({short_u})")
                act_copy_u = menu.addAction(f"🔗 Copy Link Address")
                url_actions.append((act_open_u, "open", u))
                url_actions.append((act_copy_u, "copy", u))
            else:
                for u in found_urls[:5]:
                    short_u = u if len(u) <= 30 else (u[:27] + "...")
                    act_open_u = menu.addAction(f"🌐 Open {short_u}")
                    act_copy_u = menu.addAction(f"🔗 Copy {short_u}")
                    url_actions.append((act_open_u, "open", u))
                    url_actions.append((act_copy_u, "copy", u))

        act_resend = None
        if self.msg.is_outgoing and getattr(self.msg, "repeats_heard", 0) == 0:
            act_resend = menu.addAction("🔁 Resend Message (0 repeats)")

        act_reply = menu.addAction(f"💬 Reply to @{self.msg.sender_name}")
        act_dm = menu.addAction(f"✉️ Direct Message @{self.msg.sender_name}")
        act_path = menu.addAction("🗺️ Visualise Path")

        is_fav = bool(self.config and (self.msg.sender_name in self.config.favorites or self.msg.sender_id in self.config.favorites))
        act_fav = menu.addAction("⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites (Yellow Star)")
        act_info = menu.addAction("ℹ️ View Node Details")

        act_toggle_role = None
        c_for_role = self.storage.get_contact(self.msg.sender_id or self.msg.sender_name) if self.storage else None
        if c_for_role:
            role_label = "Mark as Companion (Client)" if c_for_role.is_repeater else "Mark as Repeater"
            act_toggle_role = menu.addAction(f"🔄 {role_label}")

        act_block = menu.addAction("🚫 Block User")
        menu.addSeparator()
        act_delete = menu.addAction("🗑️ Delete Message")

        chosen = menu.exec(self.mapToGlobal(pos))
        for act, action_type, u in url_actions:
            if chosen == act:
                if action_type == "open":
                    QDesktopServices.openUrl(QUrl(u))
                elif action_type == "copy":
                    clip = QApplication.clipboard()
                    if clip:
                        clip.setText(u)
                return

        if act_resend and chosen == act_resend:
            self.resend_requested.emit(self.msg)
        elif chosen == act_copy:
            text_to_copy = selected_text if selected_text else html.unescape(self.msg.text)
            clip = QApplication.clipboard()
            if clip:
                clip.setText(text_to_copy.replace("\u200b", ""))
        elif chosen == act_reply:
            self.reply_requested.emit(f"@{self.msg.sender_name} ")
        elif chosen == act_dm:
            self.dm_requested.emit(self.msg.sender_id or self.msg.sender_name)
        elif chosen == act_path:
            self.visualise_path_requested.emit(self.msg)
            bus.emit(EventType.VISUALISE_MESSAGE_PATH, self.msg)
        elif chosen == act_fav:
            new_state = not is_fav
            if self.config:
                if new_state:
                    if self.msg.sender_name not in self.config.favorite_users:
                        self.config.favorite_users.append(self.msg.sender_name)
                else:
                    self.config.favorite_users = [u for u in self.config.favorite_users if u != self.msg.sender_name and u != self.msg.sender_id]
                self.config.save()
            if self.storage:
                self.storage.set_contact_favorite(self.msg.sender_id or self.msg.sender_name, new_state)
            bus.emit(EventType.FAVORITES_UPDATED, self.msg.sender_name)
            self.favorite_toggled.emit(self.msg.sender_name, new_state)
        elif act_toggle_role and chosen == act_toggle_role:
            new_rep = not c_for_role.is_repeater
            if self.storage:
                self.storage.set_contact_repeater_status(c_for_role.node_id, new_rep)
                c_for_role.is_repeater = new_rep
                bus.emit(EventType.MAP_NODES_UPDATED, None)
                # Clear avatar cache so new role renders immediately
                from meshcore_tray.ui.avatar_generator import _AVATAR_CACHE
                _AVATAR_CACHE.clear()
        elif chosen == act_info:
            dlg = NodeInfoDialog(self.msg, storage=self.storage, parent=self)
            dlg.exec()
        elif chosen == act_block:
            reply = QMessageBox.question(
                self,
                "Block User",
                f"Are you sure you want to block all messages from @{self.msg.sender_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self.config:
                    if self.msg.sender_name not in self.config.blocked_users:
                        self.config.blocked_users.append(self.msg.sender_name)
                    if self.msg.sender_id and self.msg.sender_id not in self.config.blocked_users:
                        self.config.blocked_users.append(self.msg.sender_id)
                    self.config.save()
                self.user_blocked.emit(self.msg.sender_name)
        elif chosen == act_delete:
            if self.storage:
                self.storage.delete_message(self.msg.id)
            self.message_deleted.emit(self.msg.id)


class ChatWidget(QWidget):
    """Scrollable message stream with real-time update and search view."""

    jump_to_channel = pyqtSignal(str)
    reply_requested = pyqtSignal(str)
    dm_requested = pyqtSignal(str)
    visualise_path_requested = pyqtSignal(object) # (msg: MessageEnvelope)
    resend_requested = pyqtSignal(object)         # (msg: MessageEnvelope)

    def __init__(self, storage=None, config=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.current_channel = "Public"
        self.current_dm: Optional[str] = None
        self.is_searching = False
        self._active_first_unread_id: Optional[str] = None
        self._last_target_widget: Optional[QWidget] = None
        self._mark_read_timer: Optional[QTimer] = None
        self._bubbles: dict[str, MessageBubble] = {}
        bus.subscribe(EventType.SETTINGS_UPDATED, self._on_settings_updated)
        self._init_ui()

    def _on_settings_updated(self, config):
        old_config = self.config
        self.config = config
        old_sig = (
            getattr(old_config, "show_chat_avatars", True) if old_config else None,
            getattr(old_config, "user_avatar_style", "droid") if old_config else None,
            tuple(getattr(old_config, "blocked_users", [])) if old_config else None,
            getattr(old_config.app_colors, "new_messages_bar_color", None) if old_config and hasattr(old_config, "app_colors") else None,
            getattr(old_config.app_colors, "favorite_user_color", None) if old_config and hasattr(old_config, "app_colors") else None,
        )
        new_sig = (
            getattr(config, "show_chat_avatars", True) if config else None,
            getattr(config, "user_avatar_style", "droid") if config else None,
            tuple(getattr(config, "blocked_users", [])) if config else None,
            getattr(config.app_colors, "new_messages_bar_color", None) if config and hasattr(config, "app_colors") else None,
            getattr(config.app_colors, "favorite_user_color", None) if config and hasattr(config, "app_colors") else None,
        )
        if old_sig != new_sig:
            self.reload_messages()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header Title Bar
        self.header_bar = QFrame()
        self.header_bar.setStyleSheet("background-color: #222327; border-bottom: 1px solid #414143;")
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(16, 10, 16, 10)

        self.title_label = QLabel("#Public")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #E5E7EB;")
        self.sub_label = QLabel("Public Channel Broadcast")
        self.sub_label.setStyleSheet("color: #9CA3AF; font-size: 12px; margin-left: 10px;")

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.sub_label)
        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search messages & users...")
        self.search_input.setFixedWidth(200)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E1F22;
                color: #FFFFFF;
                border: 1px solid #3F4147;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #5865F2;
            }
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        header_layout.addWidget(self.search_input)

        main_layout.addWidget(self.header_bar)

        # Scroll Area for Messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.container = ChatContainerWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(12, 12, 12, 12)
        self.container_layout.setSpacing(10)
        self.container_layout.addStretch()

        self.scroll_area.setWidget(self.container)
        main_layout.addWidget(self.scroll_area)

    def showEvent(self, event):
        super().showEvent(event)
        # Apply scroll to first unread or bottom when widget appears
        if self._last_target_widget:
            self._scroll_to_widget(self._last_target_widget)
        else:
            self._scroll_to_bottom()

    def _on_search_text_changed(self, text: str):
        q = text.strip()
        if q:
            self.show_search_results(q)
        else:
            self.set_target(self.current_channel, self.current_dm)

    def set_target(self, channel: str, dm_recipient: Optional[str] = None):
        self.current_channel = channel
        self.current_dm = dm_recipient
        self.is_searching = False
        self._active_first_unread_id = None
        self._last_target_widget = None

        if dm_recipient:
            contact = self.storage.get_contact(dm_recipient) if self.storage else None
            alias = contact.alias if contact else dm_recipient
            is_fav = bool(contact and contact.is_favorite) or (self.config and self.config.is_user_favorite(dm_recipient, alias))
            star = "<span style='color: #FFD700;'>★ </span>" if is_fav else ""
            self.title_label.setText(f"{star}@{alias}")
            self.sub_label.setText("Direct Message")
        else:
            clean_chan = f"#{channel.lstrip('#')}"
            is_fav = bool(self.config and self.config.is_channel_favorite(channel))
            if self.storage:
                channels = self.storage.get_channels()
                ch = next((c for c in channels if c.name.lower().lstrip("#") == channel.lower().lstrip("#")), None)
                if ch and ch.is_favorite:
                    is_fav = True
            fav_col = getattr(self.config.app_colors, "favorite_channel_color", "#FF5555") if (self.config and hasattr(self.config, "app_colors")) else "#FF5555"
            star = f"<span style='color: {fav_col};'>★ </span>" if is_fav else ""
            self.title_label.setText(f"{star}{clean_chan}")
            self.sub_label.setText("Mesh Channel Stream")

        self.reload_messages()

    def reload_messages(self):
        """Fetches messages from SQLite, renders stream, and starts from the first unread message."""
        self._clear_container()

        messages = []
        target_key = f"dm:{self.current_dm}" if self.current_dm else f"chan:{self.current_channel.lstrip('#')}"

        if self.storage:
            if self.current_dm:
                messages = self.storage.get_messages(contact_id=self.current_dm)
            else:
                unread_cnt = self.storage.get_channel_unread_count(self.current_channel)
                fetch_limit = min(max(60, unread_cnt + 20), 150)
                messages = self.storage.get_messages(channel=self.current_channel, limit=fetch_limit)

        # Filter blocked users
        if self.config and self.config.blocked_users:
            messages = [
                m for m in messages
                if m.sender_name not in self.config.blocked_users and m.sender_id not in self.config.blocked_users
            ]

        # Filter legacy echo messages (incoming messages where sender matches our own node alias)
        local_alias = (self.config.meshcore.node_alias if self.config else "").strip().lower()
        if local_alias and local_alias not in ("heltec-v3", "local node", "local", ""):
            messages = [
                m for m in messages
                if not (not m.is_outgoing and m.sender_name.strip().lower() == local_alias)
            ]

        if not messages:
            chan_display = f"#{self.current_channel.lstrip('#')}" if not self.current_dm else f"@{self.current_dm}"
            empty_lbl = QLabel(f"No messages in {chan_display} yet. Say hello!")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #6E7681; font-size: 13px; margin-top: 40px;")
            self.container_layout.addWidget(empty_lbl)
            self.container_layout.addStretch()
            self._last_target_widget = None
            return

        # Determine the first unread message
        last_read_info = self.storage.get_last_read(target_key) if self.storage else None
        first_unread_msg = None

        # If we have an active session unread marker, use it so unread divider stays stable while reading
        if self._active_first_unread_id:
            for m in messages:
                if m.id == self._active_first_unread_id:
                    first_unread_msg = m
                    break

        if not first_unread_msg and last_read_info:
            last_read_id = last_read_info.get("last_read_msg_id")
            last_read_ts = last_read_info.get("last_read_timestamp", "")

            # Look for index of last read ID
            idx_found = -1
            for idx, m in enumerate(messages):
                if m.id == last_read_id:
                    idx_found = idx
                    break

            if idx_found != -1:
                if idx_found < len(messages) - 1:
                    first_unread_msg = messages[idx_found + 1]
            elif last_read_ts:
                for m in messages:
                    if m.timestamp > last_read_ts:
                        first_unread_msg = m
                        break

        if first_unread_msg and not self._active_first_unread_id:
            self._active_first_unread_id = first_unread_msg.id

        first_unread_widget = None

        self.container.setUpdatesEnabled(False)
        try:
            for msg in messages:
                if msg is first_unread_msg:
                    bar_col = getattr(self.config.app_colors, "new_messages_bar_color", "#F85149") if (self.config and hasattr(self.config, "app_colors")) else "#F85149"
                    divider = UnreadDivider(color=bar_col)
                    self.container_layout.addWidget(divider)
                    first_unread_widget = divider

                bubble = self._create_bubble(msg)
                self.container_layout.addWidget(bubble)

                if msg is first_unread_msg and not first_unread_widget:
                    first_unread_widget = bubble

            self.container_layout.addStretch()
            self._last_target_widget = first_unread_widget
        finally:
            self.container.setUpdatesEnabled(True)

        # Defer marking as read so opening/refreshing doesn't prematurely erase unread position
        if self.storage and messages:
            self._schedule_mark_read(target_key, messages[-1].id, messages[-1].timestamp)

        if first_unread_widget:
            self._scroll_to_widget(first_unread_widget)
        else:
            self._scroll_to_bottom()

    def _schedule_mark_read(self, target_key: str, msg_id: str, timestamp: str):
        """Marks channel as read after 3.5 seconds of active dwell time."""
        if self._mark_read_timer and self._mark_read_timer.isActive():
            self._mark_read_timer.stop()
        self._mark_read_timer = QTimer(self)
        self._mark_read_timer.setSingleShot(True)
        self._mark_read_timer.timeout.connect(lambda: self._do_mark_read(target_key, msg_id, timestamp))
        self._mark_read_timer.start(3500)

    def _do_mark_read(self, target_key: str, msg_id: str, timestamp: str):
        if self.storage:
            self.storage.mark_as_read(target_key, msg_id, timestamp)

    def mark_current_as_read(self):
        """Immediately commits read state of the active channel or DM."""
        if self._mark_read_timer and self._mark_read_timer.isActive():
            self._mark_read_timer.stop()
        target_key = f"dm:{self.current_dm}" if self.current_dm else f"chan:{self.current_channel.lstrip('#')}"
        if self.storage:
            if not self.current_dm:
                self.storage.mark_channel_as_read(self.current_channel)
            else:
                messages = self.storage.get_messages(contact_id=self.current_dm, limit=1)
                if messages:
                    self._do_mark_read(target_key, messages[-1].id, messages[-1].timestamp)

    def add_message(self, msg: MessageEnvelope):
        """Dynamically appends a new message if it matches active view."""
        if self.is_searching:
            return

        # Filter blocked users
        if self.config and self.config.blocked_users:
            if msg.sender_name in self.config.blocked_users or msg.sender_id in self.config.blocked_users:
                return

        # Ignore incoming echo messages where sender matches our own node alias
        if not msg.is_outgoing and self.config and getattr(self.config, "meshcore", None):
            local_alias = (self.config.meshcore.node_alias or "").strip().lower()
            if local_alias and local_alias not in ("heltec-v3", "local node", "local", ""):
                if msg.sender_name.strip().lower() == local_alias:
                    return

        is_match = False
        if self.current_dm and msg.is_direct_message:
            if msg.sender_id == self.current_dm or msg.recipient_id == self.current_dm:
                is_match = True
        elif not self.current_dm and not msg.is_direct_message:
            if msg.channel.lstrip("#").lower() == self.current_channel.lstrip("#").lower():
                is_match = True

        if is_match:
            # Remove stretch at bottom, add bubble, re-add stretch
            count = self.container_layout.count()
            if count > 0:
                item = self.container_layout.takeAt(count - 1)
                del item

            bubble = self._create_bubble(msg)
            self.container_layout.addWidget(bubble)
            self.container_layout.addStretch()

            # Schedule mark as read
            target_key = f"dm:{self.current_dm}" if self.current_dm else f"chan:{self.current_channel.lstrip('#')}"
            if self.storage:
                self._schedule_mark_read(target_key, msg.id, msg.timestamp)

            self._scroll_to_bottom()

    def show_search_results(self, query: str):
        """Renders search results across all channels."""
        self.is_searching = True
        self.title_label.setText(f"🔍 Search: \"{query}\"")
        self.sub_label.setText("All Channels & DMs")
        self._clear_container()

        if not self.storage or not query.strip():
            return

        results = self.storage.search_messages(query)
        if not results:
            no_res = QLabel(f"No messages found matching \"{query}\"")
            no_res.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_res.setStyleSheet("color: #8B949E; margin-top: 40px;")
            self.container_layout.addWidget(no_res)
        else:
            for msg in results:
                bubble = self._create_bubble(msg)
                self.container_layout.addWidget(bubble)

        self.container_layout.addStretch()

    def _create_bubble(self, msg: MessageEnvelope) -> MessageBubble:
        bubble = MessageBubble(msg, config=self.config, storage=self.storage)
        bubble.reply_requested.connect(self.reply_requested)
        bubble.dm_requested.connect(self.dm_requested)
        bubble.message_deleted.connect(lambda _: self.reload_messages())
        bubble.favorite_toggled.connect(lambda _n, _f: self.reload_messages())
        bubble.user_blocked.connect(lambda _: self.reload_messages())
        bubble.visualise_path_requested.connect(self.visualise_path_requested.emit)
        bubble.resend_requested.connect(self.resend_requested.emit)
        if not hasattr(self, "_bubbles"):
            self._bubbles = {}
        self._bubbles[msg.id] = bubble
        return bubble

    def _clear_container(self):
        self._bubbles = {}
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def update_message(self, msg: MessageEnvelope):
        """Updates an existing message bubble in place (e.g. when repeats heard updates)."""
        if hasattr(self, "_bubbles") and msg.id in self._bubbles:
            self._bubbles[msg.id].set_repeats_heard(getattr(msg, "repeats_heard", 0))

    def _scroll_to_widget(self, widget: QWidget):
        if not widget or not self.scroll_area:
            return
        self.container.adjustSize()
        target_y = widget.geometry().top()
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(max(0, target_y - 20))
        # Staggered updates to ensure geometry is finalized by Qt
        QTimer.singleShot(40, lambda: self._do_scroll_to_widget(widget))
        QTimer.singleShot(120, lambda: self._do_scroll_to_widget(widget))
        QTimer.singleShot(250, lambda: self._do_scroll_to_widget(widget))

    def _do_scroll_to_widget(self, widget: QWidget):
        if widget and self.scroll_area:
            target_y = widget.geometry().top()
            self.scroll_area.verticalScrollBar().setValue(max(0, target_y - 20))

    def _scroll_to_bottom(self):
        if not self.scroll_area:
            return
        self.container.adjustSize()
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())
        # Staggered timers to ensure scroll reaches actual bottom as layout settles
        QTimer.singleShot(40, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
        QTimer.singleShot(120, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
        QTimer.singleShot(250, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
