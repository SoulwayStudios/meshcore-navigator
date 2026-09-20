"""Network Activity Timeline Widget for MeshCore Navigator (CoreScope VCR Sparkline)."""

import time
from datetime import datetime, timezone
from typing import List, Optional
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPen,
    QBrush,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QToolTip,
)

DARK_BG = QColor("#111214")
BORDER_COLOR = QColor("#383A40")
BAR_COLOR = QColor(170, 85, 255, 190)       # App signature purple (#AA55FF) histogram
BAR_HOVER_COLOR = QColor(192, 132, 252, 240) # Vibrant lavender hover (#C084FC)
PLAYHEAD_COLOR = QColor("#EF4444")          # Coral red scrubber playhead
PLAYHEAD_GLOW = QColor(239, 68, 68, 75)
TEXT_MUTED = QColor("#94A3B8")


class ActivityTimelineCanvas(QWidget):
    """Draws 100-bucket density histogram sparkline and scrub playhead matching CoreScope."""

    time_scrubbed = pyqtSignal(float)   # timestamp ms
    scrub_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.timestamps: List[float] = []   # list of epoch ms
        self.scope_hours: int = 24
        self.is_scrubbing: bool = False
        self.playhead_pct: Optional[float] = None  # 0.0 to 1.0
        self.buckets: int = 100
        self.counts: List[int] = [0] * 100
        self.max_count: int = 0
        self.start_ts: float = time.time() * 1000.0 - (24 * 3600 * 1000.0)

    def set_data(self, timestamps: List[float], scope_hours: int):
        self.timestamps = sorted(timestamps)
        self.scope_hours = max(1, scope_hours)
        self._recompute_buckets()
        self.update()

    def add_timestamp(self, ts_ms: Optional[float] = None):
        if ts_ms is None:
            ts_ms = time.time() * 1000.0
        self.timestamps.append(ts_ms)
        self._recompute_buckets()
        self.update()

    def _recompute_buckets(self):
        now_ms = time.time() * 1000.0
        scope_ms = self.scope_hours * 3600.0 * 1000.0
        self.start_ts = now_ms - scope_ms

        self.counts = [0] * self.buckets
        self.max_count = 0

        for ts in self.timestamps:
            if ts >= self.start_ts and ts <= now_ms:
                b_idx = int(((ts - self.start_ts) / scope_ms) * self.buckets)
                if 0 <= b_idx < self.buckets:
                    self.counts[b_idx] += 1
                    if self.counts[b_idx] > self.max_count:
                        self.max_count = self.counts[b_idx]

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_scrubbing = True
            self._handle_mouse(event.position().x())

    def mouseMoveEvent(self, event):
        self._handle_mouse(event.position().x(), show_tooltip=True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_scrubbing = False

    def leaveEvent(self, event):
        if not self.is_scrubbing:
            self.playhead_pct = None
            self.update()

    def _handle_mouse(self, x: float, show_tooltip: bool = False):
        w = max(1.0, float(self.width()))
        pct = max(0.0, min(1.0, x / w))
        self.playhead_pct = pct

        scope_ms = self.scope_hours * 3600.0 * 1000.0
        scrub_ts = self.start_ts + (pct * scope_ms)
        self.time_scrubbed.emit(scrub_ts)

        if show_tooltip:
            dt = datetime.fromtimestamp(scrub_ts / 1000.0, tz=timezone.utc).astimezone()
            t_str = dt.strftime("%I:%M:%S %p")
            b_idx = min(self.buckets - 1, max(0, int(pct * self.buckets)))
            pkt_cnt = self.counts[b_idx] if b_idx < len(self.counts) else 0
            QToolTip.showText(
                QCursor.pos(),
                f"🕒 {t_str}\n📦 {pkt_cnt} packets in bucket",
                self
            )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w = self.width()
        h = self.height()

        # Recessed background frame
        painter.fillRect(0, 0, w, h, DARK_BG)
        painter.setPen(QPen(BORDER_COLOR, 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Draw histogram density bars
        if self.max_count > 0:
            bar_w = w / float(self.buckets)
            bar_color = BAR_COLOR
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bar_color))

            for i, count in enumerate(self.counts):
                if count == 0:
                    continue
                bar_h = max(2.0, (float(count) / float(self.max_count)) * (h - 4.0))
                bx = i * bar_w
                by = (h - 2.0) - bar_h
                painter.fillRect(QRectF(bx, by, max(1.0, bar_w - 0.8), bar_h), bar_color)

        # Draw red playhead scrubber line
        if self.playhead_pct is not None:
            px = self.playhead_pct * w
            # Glow
            painter.setPen(QPen(PLAYHEAD_GLOW, 4))
            painter.drawLine(int(px), 1, int(px), h - 2)
            # Core line
            painter.setPen(QPen(PLAYHEAD_COLOR, 2))
            painter.drawLine(int(px), 1, int(px), h - 2)
        else:
            # Default to current time at right edge
            painter.setPen(QPen(PLAYHEAD_COLOR, 2))
            painter.drawLine(w - 2, 1, w - 2, h - 2)

        painter.end()


class NetworkActivityTimelineWidget(QFrame):
    """Split-view bottom dock displaying network packet volume over time."""

    scope_changed = pyqtSignal(int)
    close_requested = pyqtSignal()

    def __init__(self, storage=None, config=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.current_scope_hours = 24
        if self.config and hasattr(self.config, "meshcore") and hasattr(self.config.meshcore, "map_timeline_scope_hours"):
            self.current_scope_hours = self.config.meshcore.map_timeline_scope_hours or 24

        self.setObjectName("networkActivityTimelineDock")
        self.setFixedHeight(46)
        self.setStyleSheet("""
            QFrame#networkActivityTimelineDock {
                background-color: #1E1F22;
                border-top: 1px solid #383A40;
            }
            QPushButton.map-ctrl-btn {
                background-color: #2B2D31;
                color: #DBDEE1;
                border: 1px solid #383A40;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.map-ctrl-btn:hover {
                background-color: #35373C;
                color: #FFFFFF;
                border-color: #4E5058;
            }
            QPushButton.map-ctrl-btn:checked {
                background-color: #38BDF8;
                color: #0F172A;
                border-color: #38BDF8;
                font-weight: 700;
            }
            QPushButton.map-ctrl-btn:checked:hover {
                background-color: #7DD3FC;
                color: #0F172A;
            }
            QPushButton.scope-btn {
                background-color: #2B2D31;
                color: #94A3B8;
                border: 1px solid #383A40;
                border-radius: 4px;
                padding: 3px 9px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.scope-btn:hover {
                background-color: #35373C;
                color: #F1F5F9;
                border-color: #4E5058;
            }
            QPushButton.scope-btn.active {
                background-color: #582299;
                color: #FFFFFF;
                border: 1px solid #AA55FF;
                font-weight: 700;
            }
            QPushButton.timeline-close-btn {
                background: transparent;
                color: #94A3B8;
                border: none;
                font-size: 13px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }
            QPushButton.timeline-close-btn:hover {
                background-color: #ED4245;
                color: #FFFFFF;
            }
        """)

        self._init_ui()
        self.refresh_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(6)

        # 0. Left Map Action Buttons Container (Re-center, Reset Layers, Age Fade, Topo/Canvas)
        self.map_controls_layout = QHBoxLayout()
        self.map_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.map_controls_layout.setSpacing(4)
        layout.addLayout(self.map_controls_layout)

        # Subtle vertical separator line between map options and timeline scopes
        self.controls_divider = QFrame()
        self.controls_divider.setFrameShape(QFrame.Shape.VLine)
        self.controls_divider.setFrameShadow(QFrame.Shadow.Plain)
        self.controls_divider.setStyleSheet("color: #383A40; background-color: #383A40; width: 1px; margin: 4px 2px;")
        layout.addWidget(self.controls_divider)

        # 1. Scope Buttons: 1h, 6h, 12h, 24h
        self.scope_btns = {}
        for hours, label in [(1, "1h"), (6, "6h"), (12, "12h"), (24, "24h")]:
            btn = QPushButton(label)
            btn.setProperty("class", "scope-btn")
            btn.setProperty("scope_hours", hours)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if hours == self.current_scope_hours:
                btn.setProperty("active", True)
                btn.setStyleSheet("background-color: #582299; color: #FFFFFF; border: 1px solid #AA55FF; font-weight: 700;")
            btn.clicked.connect(lambda _, h=hours: self.set_scope(h))
            self.scope_btns[hours] = btn
            layout.addWidget(btn)

        # 2. Activity Canvas Sparkline
        self.canvas = ActivityTimelineCanvas(self)
        layout.addWidget(self.canvas, 1)

        # 3. Stats Badge
        self.lbl_stats = QLabel("0 pkts")
        self.lbl_stats.setStyleSheet("background-color: #111214; color: #AA55FF; border: 1px solid #383A40; border-radius: 4px; font-size: 10.5px; font-family: monospace; font-weight: 700; padding: 2px 6px;")
        layout.addWidget(self.lbl_stats)

        # 4. Close button (Hidden as timeline is now a permanent standard dock)
        self.btn_close = QPushButton("✕")
        self.btn_close.setProperty("class", "timeline-close-btn")
        self.btn_close.setToolTip("Close Network Activity Timeline")
        self.btn_close.clicked.connect(self.close_requested.emit)
        self.btn_close.hide()
        layout.addWidget(self.btn_close)

    def set_map_controls(self, btn_center=None, btn_reset_layers=None, btn_age_fade=None, btn_base_map=None):
        """Populates the leading map action buttons before the 1h scope button."""
        for btn in (btn_center, btn_reset_layers, btn_age_fade, btn_base_map):
            if btn:
                btn.setProperty("class", "map-ctrl-btn")
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2B2D31;
                        color: #DBDEE1;
                        border: 1px solid #383A40;
                        border-radius: 4px;
                        padding: 3px 8px;
                        font-size: 11px;
                        font-weight: 600;
                        min-height: 20px;
                    }
                    QPushButton:hover {
                        background-color: #35373C;
                        color: #FFFFFF;
                        border-color: #4E5058;
                    }
                    QPushButton:checked {
                        background-color: #38BDF8;
                        color: #0F172A;
                        border-color: #38BDF8;
                        font-weight: 700;
                    }
                    QPushButton:checked:hover {
                        background-color: #7DD3FC;
                        color: #0F172A;
                    }
                """)
                self.map_controls_layout.addWidget(btn)

    def set_scope(self, hours: int):
        self.current_scope_hours = hours
        if self.config and hasattr(self.config, "meshcore") and hasattr(self.config.meshcore, "map_timeline_scope_hours"):
            self.config.meshcore.map_timeline_scope_hours = hours
            try:
                self.config.save()
            except Exception:
                pass

        for h, btn in self.scope_btns.items():
            if h == hours:
                btn.setStyleSheet("background-color: #582299; color: #FFFFFF; border: 1px solid #AA55FF; font-weight: 700;")
            else:
                btn.setStyleSheet("")

        self.scope_changed.emit(hours)
        self.refresh_data()

    def record_packet(self, ts_ms: Optional[float] = None):
        """Records an incoming packet in real-time."""
        self.canvas.add_timestamp(ts_ms)
        total = len(self.canvas.timestamps)
        self.lbl_stats.setText(f"{total} pkts")

    def refresh_data(self):
        """Fetches historical timestamps from storage within current scope."""
        if not self.storage or not hasattr(self.storage, "get_packet_timeline_timestamps"):
            return
        try:
            timestamps = self.storage.get_packet_timeline_timestamps(hours=self.current_scope_hours)
            self.canvas.set_data(timestamps, self.current_scope_hours)
            self.lbl_stats.setText(f"{len(timestamps)} pkts")
        except Exception as e:
            self.lbl_stats.setText("0 pkts")

    @property
    def btn_1h(self) -> QPushButton:
        return self.scope_btns[1]

    @property
    def btn_6h(self) -> QPushButton:
        return self.scope_btns[6]

    @property
    def btn_12h(self) -> QPushButton:
        return self.scope_btns[12]

    @property
    def btn_24h(self) -> QPushButton:
        return self.scope_btns[24]
