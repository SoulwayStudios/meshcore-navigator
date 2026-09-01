"""Live 64x64 Virtual Pixoo Matrix Preview Widget."""

import logging
import time
from typing import Optional
from PIL import Image
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QImage, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope

logger = logging.getLogger("meshcore_tray.pixoo_preview")


class MatrixCanvas(QWidget):
    """Draws 64x64 pixel grid with simulated LED dot styling and aspect scaling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(256, 256)
        self._current_image: Optional[QImage] = None

    def update_frame(self, pil_image: Image.Image):
        img_bytes = pil_image.tobytes("raw", "RGB")
        qimg = QImage(img_bytes, 64, 64, 64 * 3, QImage.Format.Format_RGB888)
        self._current_image = qimg
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        painter.fillRect(self.rect(), QColor("#05080E"))

        w = self.width()
        h = self.height()
        grid_size = min(w, h) - 16
        offset_x = (w - grid_size) // 2
        offset_y = (h - grid_size) // 2

        # Draw LED bezel border
        bezel_rect = QRectF(offset_x - 4, offset_y - 4, grid_size + 8, grid_size + 8)
        painter.setPen(QColor("#1F242C"))
        painter.setBrush(QColor("#080B10"))
        painter.drawRoundedRect(bezel_rect, 6, 6)

        if not self._current_image:
            return

        pixel_w = grid_size / 64.0
        pixel_h = grid_size / 64.0

        for y in range(64):
            for x in range(64):
                col = self._current_image.pixelColor(x, y)
                px = offset_x + x * pixel_w
                py = offset_y + y * pixel_h
                painter.fillRect(QRectF(px, py, pixel_w - 0.5, pixel_h - 0.5), col)


class PixooPreviewWidget(QWidget):
    """Panel containing the 64x64 matrix preview canvas and simulation test triggers."""

    def __init__(self, pixoo_service=None, parent=None):
        super().__init__(parent)
        self.pixoo_service = pixoo_service
        self._init_ui()

        # Connect event bus
        bus.subscribe(EventType.PIXOO_FRAME_READY, self._on_frame_ready)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Title
        title = QLabel("<b>PIXOO 64 LIVE MIRROR</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #00E5FF; font-size: 11px; letter-spacing: 1px;")
        layout.addWidget(title)

        # Canvas
        self.canvas = MatrixCanvas()
        layout.addWidget(self.canvas, 1)

        # Action Buttons
        btn_grid = QVBoxLayout()
        btn_grid.setSpacing(6)

        row1 = QHBoxLayout()
        self.btn_sim_alice = QPushButton("🧪 Alice Msg")
        self.btn_sim_alice.clicked.connect(self._sim_alice)
        self.btn_sim_bob = QPushButton("🧪 Bob Msg")
        self.btn_sim_bob.clicked.connect(self._sim_bob)
        row1.addWidget(self.btn_sim_alice)
        row1.addWidget(self.btn_sim_bob)

        row2 = QHBoxLayout()
        self.btn_sim_bounce = QPushButton("📜 Long Msg (Bounce)")
        self.btn_sim_bounce.clicked.connect(self._sim_bounce)
        self.btn_test_telem = QPushButton("📡 RF Telem")
        self.btn_test_telem.clicked.connect(self._test_telem)
        row2.addWidget(self.btn_sim_bounce)
        row2.addWidget(self.btn_test_telem)

        row3 = QHBoxLayout()
        self.btn_test_neigh = QPushButton("👥 Neighbours")
        self.btn_test_neigh.clicked.connect(self._test_neigh)
        row3.addWidget(self.btn_test_neigh)

        btn_grid.addLayout(row1)
        btn_grid.addLayout(row2)
        btn_grid.addLayout(row3)
        layout.addLayout(btn_grid)

    def _on_frame_ready(self, pil_frame: Image.Image):
        self.canvas.update_frame(pil_frame)

    def _sim_alice(self):
        msg = MessageEnvelope(
            id=f"sim-alice-{int(time.time()*1000)}",
            sender_name="Alice",
            channel="Public",
            text="Alice: Quick radio test on #Public.",
            is_favorite=True
        )
        bus.emit(EventType.MESSAGE_RECEIVED, msg)

    def _sim_bob(self):
        msg = MessageEnvelope(
            id=f"sim-bob-{int(time.time()*1000)}",
            sender_name="Bob_Node",
            channel="Public",
            text="Bob: Receiving Alice loud and clear.",
            is_favorite=False
        )
        bus.emit(EventType.MESSAGE_RECEIVED, msg)

    def _sim_bounce(self):
        msg = MessageEnvelope(
            id=f"sim-bounce-{int(time.time()*1000)}",
            sender_name="Charlie_Base",
            channel="Public",
            text="Long message transmission for vertical bounce test: Severe storm warning active across western sector. Maintain standby on 868.125MHz.",
            is_favorite=False
        )
        bus.emit(EventType.MESSAGE_RECEIVED, msg)

    def _test_telem(self):
        if self.pixoo_service:
            self.pixoo_service.test_telemetry()

    def _test_neigh(self):
        if self.pixoo_service:
            self.pixoo_service.test_neighbours()
