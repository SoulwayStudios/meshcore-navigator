"""Keyboard-First Power Composer with Autocomplete, Fast Switching, and Search."""

import logging
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QMenu
)

logger = logging.getLogger("meshcore_tray.composer")


class AutocompletePopup(QListWidget):
    """Floating overlay list for #channel, @user, and /switch autocomplete."""
    item_selected = pyqtSignal(str, str)  # (type, value)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("autocompleteList")
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()
        self.itemClicked.connect(self._on_item_clicked)

    def _on_item_clicked(self, item: QListWidgetItem):
        kind = item.data(Qt.ItemDataRole.UserRole)
        val = item.data(Qt.ItemDataRole.UserRole + 1)
        self.item_selected.emit(kind, val)
        self.hide()


class PowerComposer(QWidget):
    """Intelligent power input composer for mesh messaging, channel switching, and search."""

    # Signals
    send_message = pyqtSignal(str, object, str)  # (channel, recipient_id, text)
    switch_channel = pyqtSignal(str)             # (channel_name)
    join_channel = pyqtSignal(str)               # (channel_name)
    search_query = pyqtSignal(str)               # (search_text)
    search_cleared = pyqtSignal()

    def __init__(self, storage=None, config=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.active_channel = "Public"
        self.active_dm: Optional[str] = None
        self._is_searching = False
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("composerInput")
        self.input_field.setMinimumHeight(38)
        self.input_field.setPlaceholderText(
            "Type message... (#channel, @user, /join #channel, /switch, ? search)"
        )
        self.input_field.textChanged.connect(self._on_text_changed)

        self.emoji_btn = QPushButton("😀")
        self.emoji_btn.setFixedSize(38, 38)
        self.emoji_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.emoji_btn.setToolTip("Add Emoji")
        self.emoji_btn.setStyleSheet("""
            QPushButton {
                background-color: #2B2D31;
                border: 1px solid #383A40;
                border-radius: 6px;
                padding: 0px;
                font-size: 18px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #35373C;
                border-color: #5865F2;
            }
        """)
        self.emoji_btn.clicked.connect(self._show_emoji_menu)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setFixedWidth(80)
        self.send_button.setMinimumHeight(38)
        self.send_button.clicked.connect(self._handle_submit)

        layout.addWidget(self.input_field)
        layout.addWidget(self.emoji_btn)
        layout.addWidget(self.send_button)

        # Autocomplete popup
        self.popup = AutocompletePopup(self)
        self.popup.item_selected.connect(self._on_autocomplete_selected)

        # Install event filter on input field for keyboard navigation
        self.input_field.installEventFilter(self)
        self.apply_theme()

    def apply_theme(self, color_hex: Optional[str] = None, text_color_hex: Optional[str] = None):
        """Applies configured send button color and legible text color."""
        c = color_hex
        if not c and self.config and hasattr(self.config, "app_colors"):
            c = getattr(self.config.app_colors, "send_button_color", "#00FF7F")
        if not c:
            c = "#00FF7F"

        txt_col = text_color_hex
        if not txt_col and self.config and hasattr(self.config, "app_colors"):
            txt_col = getattr(self.config.app_colors, "send_button_text_color", None)
        if not txt_col:
            # Auto-calculate luminance contrast for maximum legibility on light/dark backgrounds
            col = QColor(c)
            lum = (0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()) / 255.0
            txt_col = "#000000" if lum > 0.45 else "#FFFFFF"

        c_hover = QColor(c).lighter(115).name()
        self.send_button.setStyleSheet(f"""
            QPushButton#primaryButton {{
                background-color: {c};
                color: {txt_col};
                border: 1px solid {c};
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton#primaryButton:hover {{
                background-color: {c_hover};
                border-color: {c_hover};
            }}
        """)

    def set_active_target(self, channel: str, dm_recipient: Optional[str] = None):
        self.active_channel = channel
        self.active_dm = dm_recipient
        if dm_recipient:
            self.input_field.setPlaceholderText(f"Message @{dm_recipient}... (#channel, @user, /join, /switch, ? search)")
        else:
            self.input_field.setPlaceholderText(f"Message #{channel}... (#channel, @user, /join, /switch, ? search)")

    def focus(self):
        self.input_field.setFocus()

    def _on_text_changed(self, text: str):
        # 1. Search Mode: Prefix '?'
        if text.startswith("?"):
            self._is_searching = True
            query = text[1:].strip()
            self.popup.hide()
            self.search_query.emit(query)
            return
        elif self._is_searching:
            self._is_searching = False
            self.search_cleared.emit()

        # 2. Slash Command / Channel Switcher & Joiner: Prefix '/'
        if text.startswith("/"):
            self._show_slash_suggestions(text[1:])
            return

        cursor_pos = self.input_field.cursorPosition()
        text_before_cursor = text[:cursor_pos]

        # 3. Channel Autocomplete: Prefix '#'
        if "#" in text_before_cursor:
            last_hash = text_before_cursor.rfind("#")
            if last_hash == 0 or text_before_cursor[last_hash - 1] in (" ", "(", "[", "{", ",", ":"):
                token = text_before_cursor[last_hash + 1:].lower()
                if " " not in token:
                    self._show_channel_autocomplete_suggestions(token)
                    return

        # 4. Contact / DM Autocomplete: Prefix '@'
        if "@" in text_before_cursor:
            last_at = text_before_cursor.rfind("@")
            if last_at == 0 or text_before_cursor[last_at - 1] in (" ", "(", "[", "{", ",", ":"):
                token = text_before_cursor[last_at + 1:].lower()
                if " " not in token:
                    self._show_contact_autocomplete_suggestions(token)
                    return

        self.popup.hide()

    def _show_slash_suggestions(self, command_text: str):
        cmd = command_text.strip().lower()
        self.popup.clear()

        # If user typed /join <channel> or /j <channel>
        if cmd.startswith("join") or cmd.startswith("j "):
            parts = command_text.strip().split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            target_display = target if target else "<channel_name>"
            item = QListWidgetItem(f"➕ Join MeshCore Channel #{target_display.lstrip('#')}")
            item.setData(Qt.ItemDataRole.UserRole, "join")
            item.setData(Qt.ItemDataRole.UserRole + 1, target if target else "")
            self.popup.addItem(item)
            self.popup.setCurrentRow(0)
            self._position_popup()
            return

        # Regular channel switch suggestions
        channels = []
        if self.storage:
            channels = self.storage.get_channels_by_recent_activity(cmd)
        else:
            channels = [type("C", (), {"name": "Public", "is_favorite": True})]

        for ch in channels:
            item = QListWidgetItem(f"🔀 Switch to #{ch.name.lstrip('#')}")
            item.setData(Qt.ItemDataRole.UserRole, "switch")
            item.setData(Qt.ItemDataRole.UserRole + 1, ch.name)
            self.popup.addItem(item)

        # Also offer /join suggestion
        if cmd:
            item = QListWidgetItem(f"➕ Join new channel #{cmd.lstrip('#')}")
            item.setData(Qt.ItemDataRole.UserRole, "join")
            item.setData(Qt.ItemDataRole.UserRole + 1, f"#{cmd.lstrip('#')}")
            self.popup.addItem(item)

        if self.popup.count() > 0:
            self.popup.setCurrentRow(0)
            self._position_popup()
        else:
            self.popup.hide()

    def _show_channel_autocomplete_suggestions(self, prefix: str):
        channels = []
        if self.storage:
            channels = self.storage.get_channels_by_recent_activity(prefix)
        else:
            channels = [type("C", (), {"name": "Public", "is_favorite": True})]

        self.popup.clear()
        if not channels:
            self.popup.hide()
            return

        for ch in channels:
            star = "⭐ " if ch.is_favorite else ""
            item = QListWidgetItem(f"{star}#{ch.name}")
            item.setData(Qt.ItemDataRole.UserRole, "channel")
            item.setData(Qt.ItemDataRole.UserRole + 1, ch.name)
            self.popup.addItem(item)

        self.popup.setCurrentRow(0)
        self._position_popup()

    def _show_contact_autocomplete_suggestions(self, prefix: str):
        contacts = []
        if self.storage:
            contacts = self.storage.get_contacts_by_recent_activity(prefix)
        else:
            contacts = [type("N", (), {"alias": "Alice", "node_id": "!8f3a", "is_favorite": True})]

        self.popup.clear()
        if not contacts:
            self.popup.hide()
            return

        for c in contacts:
            star = "⭐ " if c.is_favorite else ""
            display_name = f"{star}@{c.alias}" if c.alias == c.node_id else f"{star}@{c.alias} ({c.node_id})"
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, "contact")
            item.setData(Qt.ItemDataRole.UserRole + 1, c.alias)
            self.popup.addItem(item)

        self.popup.setCurrentRow(0)
        self._position_popup()

    def _position_popup(self):
        self.popup.setFixedWidth(max(280, self.input_field.width() - 80))
        self.popup.setFixedHeight(min(180, self.popup.count() * 34 + 10))
        global_pos = self.input_field.mapToGlobal(QPoint(0, -self.popup.height() - 4))
        self.popup.move(global_pos)
        self.popup.show()

    def _on_autocomplete_selected(self, kind: str, val: str):
        text = self.input_field.text()

        if kind == "switch":
            self.input_field.clear()
            self.switch_channel.emit(val)
            return

        if kind == "join":
            self.input_field.clear()
            self.join_channel.emit(val)
            return

        if kind == "channel":
            last_hash = text.rfind("#")
            if last_hash != -1:
                new_text = text[:last_hash] + f"#{val} "
                self.input_field.setText(new_text)
                self.input_field.setCursorPosition(len(new_text))

        elif kind == "contact":
            last_at = text.rfind("@")
            if last_at != -1:
                new_text = text[:last_at] + f"@{val} "
                self.input_field.setText(new_text)
                self.input_field.setCursorPosition(len(new_text))

    def _handle_submit(self):
        text = self.input_field.text().strip()
        if not text:
            return

        # Handle Search submit (just keep search active)
        if text.startswith("?"):
            return

        # Handle /join channel command
        cmd_lower = text.lower()
        if cmd_lower.startswith("/join") or cmd_lower.startswith("/j "):
            parts = text.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                chan_target = parts[1].strip()
                self.join_channel.emit(chan_target)
            self.input_field.clear()
            self.popup.hide()
            return

        # Handle explicit /dm or /msg command (/dm @user message or /msg @user message)
        if cmd_lower.startswith("/dm ") or cmd_lower.startswith("/msg "):
            parts = text.split(maxsplit=2)
            if len(parts) >= 3:
                target_user = parts[1].lstrip("@")
                dm_body = parts[2]
                self.send_message.emit("DM", target_user, dm_body)
                self.input_field.clear()
                self.popup.hide()
                return

        # Handle Slash channel switch
        if text.startswith("/"):
            if self.popup.isVisible() and self.popup.currentItem():
                kind = self.popup.currentItem().data(Qt.ItemDataRole.UserRole)
                val = self.popup.currentItem().data(Qt.ItemDataRole.UserRole + 1)
                if kind == "join":
                    self.join_channel.emit(val)
                else:
                    self.switch_channel.emit(val)
            else:
                chan = text[1:].strip()
                if chan:
                    self.switch_channel.emit(chan)
            self.input_field.clear()
            self.popup.hide()
            return

        # Handle Quick Channel Routing (#channel message)
        if text.startswith("#"):
            parts = text.split(" ", 1)
            target_chan = parts[0][1:]
            msg_body = parts[1] if len(parts) > 1 else ""
            if msg_body:
                self.send_message.emit(target_chan, None, msg_body)
                self.input_field.clear()
                self.popup.hide()
                return

        # Standard send to currently active conversation
        # Note: In-channel replies and mentions (@user ...) remain public in active_channel!
        if self.active_dm:
            self.send_message.emit("DM", self.active_dm, text)
        else:
            self.send_message.emit(self.active_channel, None, text)

        self.input_field.clear()
        self.popup.hide()

    def eventFilter(self, obj, event):
        """Keyboard navigation hook for Tab, Enter, Up, Down, Esc."""
        if obj == self.input_field and event.type() == event.Type.KeyPress:
            key = event.key()

            if self.popup.isVisible():
                if key == Qt.Key.Key_Down:
                    cur = self.popup.currentRow()
                    self.popup.setCurrentRow((cur + 1) % self.popup.count())
                    return True
                elif key == Qt.Key.Key_Up:
                    cur = self.popup.currentRow()
                    self.popup.setCurrentRow((cur - 1) % self.popup.count())
                    return True
                elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    item = self.popup.currentItem()
                    if item:
                        kind = item.data(Qt.ItemDataRole.UserRole)
                        val = item.data(Qt.ItemDataRole.UserRole + 1)
                        self._on_autocomplete_selected(kind, val)
                        self.popup.hide()
                        return True
                elif key == Qt.Key.Key_Escape:
                    self.popup.hide()
                    return True
            else:
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._handle_submit()
                    return True
                elif key == Qt.Key.Key_Escape:
                    if self.input_field.text().startswith("?"):
                        self.input_field.clear()
                        self.search_cleared.emit()
                        return True

        return super().eventFilter(obj, event)

    def _show_emoji_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #222327;
                color: #FFFFFF;
                border: 1px solid #414143;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 12px;
                font-size: 14px;
            }
            QMenu::item:selected {
                background-color: #2B303C;
            }
        """)
        emojis = ["👍", "⚡", "📡", "👋", "✅", "🔥", "🚨", "📻", "😊", "🎉", "🛰️", "⚠️"]
        for em in emojis:
            act = menu.addAction(em)
            act.triggered.connect(lambda _, e=em: self._insert_emoji(e))
        menu.exec(self.emoji_btn.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 4)))

    def _insert_emoji(self, emoji_char: str):
        self.input_field.insert(emoji_char)
        self.input_field.setFocus()
