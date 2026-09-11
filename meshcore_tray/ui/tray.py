"""System Tray Icon for Linux Desktop with Notifications."""

import logging
from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QAction
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope

logger = logging.getLogger("meshcore_tray.tray")


def create_tray_icon_pixmap(color_hex: str = "#00FF66", badge_count: int = 0) -> QPixmap:
    """Generates dynamic tray icon with radio waves and optional unread badge."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Base Radio Circle
    painter.setBrush(QBrush(QColor("#161B22")))
    painter.setPen(QColor("#30363D"))
    painter.drawEllipse(4, 4, 56, 56)

    # Signal Arc Waves
    painter.setPen(QColor(color_hex))
    painter.drawArc(14, 14, 36, 36, 45 * 16, 90 * 16)
    painter.drawArc(20, 20, 24, 24, 45 * 16, 90 * 16)

    # Center Node Dot
    painter.setBrush(QBrush(QColor(color_hex)))
    painter.drawEllipse(27, 27, 10, 10)

    # Unread badge
    if badge_count > 0:
        painter.setBrush(QBrush(QColor("#FF4444")))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawEllipse(40, 4, 20, 20)

    painter.end()
    return pixmap


class SystemTray(QSystemTrayIcon):
    """Linux system tray icon handling modal toggling, notifications, and menu."""

    def __init__(self, main_window=None, config=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = config
        self.unread_count = 0

        self.setIcon(QIcon(create_tray_icon_pixmap("#00FF66")))
        self._init_menu()

        # Connect signals
        self.activated.connect(self._on_tray_activated)
        bus.subscribe(EventType.NOTIFY_USER, self._on_notify_user)
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_message_received)

    def _init_menu(self):
        menu = QMenu()

        action_show = QAction("Open MeshCore Map Mixer...", self)
        action_show.triggered.connect(self._toggle_window)
        menu.addAction(action_show)

        action_quiet = QAction("Toggle Quiet Hours", self)
        action_quiet.triggered.connect(self._toggle_quiet_hours)
        menu.addAction(action_quiet)

        action_settings = QAction("Settings...", self)
        action_settings.triggered.connect(self._open_settings)
        menu.addAction(action_settings)

        action_bug = QAction("🐛 Report Bug / Issue...", self)
        from meshcore_tray.ui.crash_dialog import open_bug_report_in_browser
        action_bug.triggered.connect(lambda: open_bug_report_in_browser())
        menu.addAction(action_bug)

        menu.addSeparator()

        action_quit = QAction("Quit MESHCORE NAVIGATOR", self)
        action_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(action_quit)

        self.setContextMenu(menu)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window()

    def _toggle_window(self):
        if not self.main_window:
            return
        if self.main_window.isVisible():
            self.main_window.hide()
        else:
            self.main_window.showNormal()
            self.main_window.activateWindow()
            self.unread_count = 0
            self.setIcon(QIcon(create_tray_icon_pixmap("#00FF66", 0)))

    def _toggle_quiet_hours(self):
        if self.config:
            self.config.quiet_hours.enabled = not self.config.quiet_hours.enabled
            self.config.save()
            state_str = "ENABLED" if self.config.quiet_hours.enabled else "DISABLED"
            self.showMessage("Quiet Hours", f"Quiet Hours {state_str}", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _open_settings(self):
        if self.main_window:
            self.main_window._open_settings()

    def _on_notify_user(self, data: dict):
        """Dispatches high-priority desktop balloon notification for mentions/keywords."""
        if not self.config or not self.config.notifications.desktop_notifications:
            return

        title = data.get("title", "MeshCore Alert")
        body = data.get("body", "New mesh packet received")
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Warning, 5000)
        self.unread_count += 1
        self.setIcon(QIcon(create_tray_icon_pixmap("#FF4444", self.unread_count)))

    def _on_message_received(self, msg: MessageEnvelope):
        if not self.main_window or not self.main_window.isVisible():
            self.unread_count += 1
            self.setIcon(QIcon(create_tray_icon_pixmap("#00FF66", self.unread_count)))
