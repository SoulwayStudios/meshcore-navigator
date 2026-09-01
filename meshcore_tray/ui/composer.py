"""Keyboard-First Power Composer with Autocomplete, Fast Switching, and Search."""

import logging
from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem
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
    search_query = pyqtSignal(str)               # (search_text)
    search_cleared = pyqtSignal()

    def __init__(self, storage=None, config=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.active_channel = "Public"
        self.active_dm: Optional[str] = None
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("composerInput")
        self.input_field.setPlaceholderText(
            "Type message... (#channel, @user, /switch, ? search)"
        )
        self.input_field.textChanged.connect(self._on_text_changed)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setFixedWidth(80)
        self.send_button.clicked.connect(self._handle_submit)

        layout.addWidget(self.input_field)
        layout.addWidget(self.send_button)

        # Autocomplete popup
        self.popup = AutocompletePopup(self)
        self.popup.item_selected.connect(self._on_autocomplete_selected)

        # Install event filter on input field for keyboard navigation
        self.input_field.installEventFilter(self)

    def set_active_target(self, channel: str, dm_recipient: Optional[str] = None):
        self.active_channel = channel
        self.active_dm = dm_recipient
        if dm_recipient:
            self.input_field.setPlaceholderText(f"Message @{dm_recipient}... (#channel, @user, /switch, ? search)")
        else:
            self.input_field.setPlaceholderText(f"Message #{channel}... (#channel, @user, /switch, ? search)")

    def focus(self):
        self.input_field.setFocus()

    def _on_text_changed(self, text: str):
        # 1. Search Mode: Prefix '?'
        if text.startswith("?"):
            query = text[1:].strip()
            self.popup.hide()
            self.search_query.emit(query)
            return
        else:
            self.search_cleared.emit()

        # 2. Slash Command / Channel Switcher: Prefix '/'
        if text.startswith("/"):
            prefix = text[1:].strip().lower()
            self._show_channel_switch_suggestions(prefix)
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

    def _show_channel_switch_suggestions(self, prefix: str):
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
            item = QListWidgetItem(f"🔀 Switch to #{ch.name}")
            item.setData(Qt.ItemDataRole.UserRole, "switch")
            item.setData(Qt.ItemDataRole.UserRole + 1, ch.name)
            self.popup.addItem(item)

        self.popup.setCurrentRow(0)
        self._position_popup()

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

        # Handle Slash channel switch
        if text.startswith("/"):
            if self.popup.isVisible() and self.popup.currentItem():
                val = self.popup.currentItem().data(Qt.ItemDataRole.UserRole + 1)
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

        # Handle Quick DM Routing (@user message)
        if text.startswith("@"):
            parts = text.split(" ", 1)
            target_user = parts[0][1:]
            msg_body = parts[1] if len(parts) > 1 else ""
            if msg_body:
                self.send_message.emit("DM", target_user, msg_body)
                self.input_field.clear()
                self.popup.hide()
                return

        # Standard send to currently active conversation
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
