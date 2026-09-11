"""Splash Screen and Loading Mask Overlay for MESHCORE NAVIGATOR."""

import logging
from PyQt6.QtCore import Qt, QTimer, QUrl, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGraphicsOpacityEffect
)

from meshcore_tray import __version__, __app_name__, __author__

logger = logging.getLogger("meshcore_tray.splash")

COFFEE_URL = "https://buymeacoffee.com/m7ncy"


class SplashOverlay(QWidget):
    """Semi-transparent glassmorphic splash overlay with welcome card, versioning, author credit, and coffee link."""

    dismissed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.first_run = not getattr(self.config, "first_run_completed", False)
        self._map_is_ready = False
        self._min_timer_done = False
        self._is_dismissing = False

        self.setObjectName("splashOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._setup_ui()
        self._setup_timing()

    def _setup_ui(self):
        # Full overlay dark glass backdrop
        overlay_layout = QVBoxLayout(self)
        overlay_layout.setContentsMargins(0, 0, 0, 0)

        # Backdrop container spanning full window
        self.backdrop = QFrame(self)
        self.backdrop.setObjectName("splashBackdrop")
        self.backdrop.setStyleSheet("""
            QFrame#splashBackdrop {
                background-color: rgba(10, 12, 16, 0.94);
            }
        """)
        backdrop_layout = QVBoxLayout(self.backdrop)
        backdrop_layout.setContentsMargins(0, 0, 0, 0)
        backdrop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Centered Welcome Card
        self.card = QFrame()
        self.card.setObjectName("splashCard")
        self.card.setFixedWidth(560)
        self.card.setStyleSheet("""
            QFrame#splashCard {
                background-color: #1A1D24;
                border: 1.5px solid #38BDF8;
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
                border: none;
            }
        """)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(36, 30, 36, 30)
        card_layout.setSpacing(12)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Top Badge Row (App Icon + Version)
        badge_row = QHBoxLayout()
        badge_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("⚡")
        icon_lbl.setStyleSheet("""
            QLabel {
                font-size: 28px;
                background-color: #0C4A6E;
                color: #38BDF8;
                border: 1.5px solid #0284C7;
                border-radius: 24px;
                padding: 6px 12px;
            }
        """)
        badge_row.addWidget(icon_lbl)
        card_layout.addLayout(badge_row)

        # App Title
        title_lbl = QLabel(__app_name__)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet("font-size: 22px; font-weight: 900; letter-spacing: 2px; color: #FFFFFF;")
        card_layout.addWidget(title_lbl)

        # Version Pill Badge
        ver_lbl = QLabel(f"v{__version__} • Production Station")
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #38BDF8; letter-spacing: 1px;")
        card_layout.addWidget(ver_lbl)

        # Author Attribution
        author_lbl = QLabel(__author__)
        author_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #FBBF24;")
        card_layout.addWidget(author_lbl)

        # Description with clean two lines and ample height
        desc_lbl = QLabel(
            "MeshCore LoRa desktop client with real-time interactive mesh map,\n"
            "multi-hop packet path tracing, ADS-B aircraft overlays, and Divoom Pixoo 64 matrix."
        )
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        desc_lbl.setMinimumHeight(42)
        card_layout.addWidget(desc_lbl)

        card_layout.addSpacing(4)

        # Buy Me a Coffee Action Button
        coffee_btn = QPushButton("☕ Buy Me a Coffee")
        coffee_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        coffee_btn.setFixedHeight(38)
        coffee_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFDD00;
                color: #000000;
                border: 1px solid #E6C600;
                border-radius: 8px;
                padding: 6px 20px;
                font-size: 13px;
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
        coffee_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(COFFEE_URL)))
        card_layout.addWidget(coffee_btn)

        # Status & Loading text
        self.status_lbl = QLabel("Initializing RF Engine & Loading Map...")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("font-size: 11px; color: #64748B; font-style: italic;")
        card_layout.addWidget(self.status_lbl)

        # Action Button Row
        self.action_layout = QHBoxLayout()
        self.action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self.first_run:
            # First run: Show OK button requiring acknowledgement
            self.ok_btn = QPushButton("OK - Get Started")
            self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.ok_btn.setFixedHeight(38)
            self.ok_btn.setStyleSheet("""
                QPushButton {
                    background-color: #0284C7;
                    color: #FFFFFF;
                    border: 1px solid #38BDF8;
                    border-radius: 8px;
                    padding: 8px 32px;
                    font-size: 13px;
                    font-weight: 700;
                }
                QPushButton:hover {
                    background-color: #0369A1;
                    border-color: #7DD3FC;
                }
            """)
            self.ok_btn.clicked.connect(self._on_ok_clicked)
            self.action_layout.addWidget(self.ok_btn)
        else:
            # Subsequent run: automatic dismissal once loaded
            pass

        card_layout.addLayout(self.action_layout)

        backdrop_layout.addWidget(self.card)
        overlay_layout.addWidget(self.backdrop)

    def _setup_timing(self):
        # Ensure a minimum comfortable display time (1.8s) so splash doesn't jarringly flicker
        min_display_ms = 1800
        self._min_timer = QTimer(self)
        self._min_timer.setSingleShot(True)
        self._min_timer.timeout.connect(self._on_min_timer_done)
        self._min_timer.start(min_display_ms)

        # Fallback safety timeout (4.5s) to guarantee splash closes on slow systems
        if not self.first_run:
            self._fallback_timer = QTimer(self)
            self._fallback_timer.setSingleShot(True)
            self._fallback_timer.timeout.connect(self.dismiss)
            self._fallback_timer.start(4500)

    def on_map_ready(self):
        """Called when MeshMapWidget signals that Leaflet has finished loading."""
        self._map_is_ready = True
        self.status_lbl.setText("✓ Mesh Map Ready")
        if not self.first_run and self._min_timer_done:
            self.dismiss()

    def _on_min_timer_done(self):
        self._min_timer_done = True
        if not self.first_run and self._map_is_ready:
            self.dismiss()

    def _on_ok_clicked(self):
        """First run OK button clicked."""
        if hasattr(self.config, "first_run_completed"):
            self.config.first_run_completed = True
            try:
                self.config.save()
            except Exception as e:
                logger.debug(f"Save config first_run_completed note: {e}")
        self.dismiss()

    def dismiss(self, immediate: bool = False):
        """Smoothly fades out and deletes the splash overlay."""
        if self._is_dismissing:
            return
        self._is_dismissing = True

        if immediate:
            self._on_fade_finished()
            return

        self.fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fade_effect)
        self.anim = QPropertyAnimation(self.fade_effect, b"opacity")
        self.anim.setDuration(350)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._on_fade_finished)
        self.anim.start()

    def _on_fade_finished(self):
        self.hide()
        self.dismissed.emit()
        self.deleteLater()
