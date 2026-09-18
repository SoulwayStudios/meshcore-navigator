"""Interactive Lightbox Modal for Satellite Imagery and Downlinked Earth Observation Scans."""

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QSize
from PyQt6.QtGui import QPixmap, QImage, QPainter, QAction, QKeySequence, QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QFileDialog,
    QApplication,
    QFrame,
)


class PanZoomImageLabel(QLabel):
    """Custom QLabel that supports mouse-drag panning."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._dragging = False
        self._last_mouse_pos = QPoint()
        self.scroll_area: Optional[QScrollArea] = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.scroll_area:
            delta = event.globalPosition().toPoint() - self._last_mouse_pos
            self._last_mouse_pos = event.globalPosition().toPoint()

            h_bar = self.scroll_area.horizontalScrollBar()
            v_bar = self.scroll_area.verticalScrollBar()
            if h_bar:
                h_bar.setValue(h_bar.value() - delta.x())
            if v_bar:
                v_bar.setValue(v_bar.value() - delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class SatelliteImageModal(QDialog):
    """High-resolution Lightbox Modal Dialog for inspecting downlinked satellite scans & spacecraft."""

    def __init__(
        self,
        image_path: str,
        title: str = "Downlinked Spacecraft Observation",
        subtitle: str = "Direct Downlink Transmission",
        metadata: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.image_path = image_path
        self.modal_title = title
        self.modal_subtitle = subtitle
        self.metadata = metadata or {}

        self.zoom_scale = 1.0
        self.min_zoom = 0.2
        self.max_zoom = 5.0
        self.orig_pixmap = QPixmap(image_path)

        self.setWindowTitle(f"{title} - Satellite Observation Lightbox")
        self.resize(1000, 720)
        self.setMinimumSize(640, 480)

        # Discord-like dark grey theme styling
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1F22;
                color: #DBDEE1;
            }
            QLabel {
                color: #DBDEE1;
            }
            QPushButton {
                background-color: #2B2D31;
                color: #F2F3F5;
                border: 1px solid #383A40;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #35373C;
                border-color: #5865F2;
            }
            QPushButton:pressed {
                background-color: #4752C4;
            }
        """)

        self._init_ui()
        self._fit_to_window()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # Top Header Bar
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(12)

        hdr_text_box = QVBoxLayout()
        hdr_text_box.setSpacing(2)

        self.lbl_title = QLabel(f"🛰️  {self.modal_title}")
        self.lbl_title.setStyleSheet("color: #F2F3F5; font-size: 16px; font-weight: 700;")
        hdr_text_box.addWidget(self.lbl_title)

        self.lbl_sub = QLabel(self.modal_subtitle)
        self.lbl_sub.setStyleSheet("color: #949BA4; font-size: 12px;")
        hdr_text_box.addWidget(self.lbl_sub)

        hdr_layout.addLayout(hdr_text_box, stretch=1)

        # Zoom Controls Pill Group
        zoom_bar = QHBoxLayout()
        zoom_bar.setSpacing(6)

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setToolTip("Zoom Out (Ctrl + Minus / Wheel Down)")
        self.btn_zoom_out.setFixedWidth(32)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        zoom_bar.addWidget(self.btn_zoom_out)

        self.lbl_zoom_pct = QLabel("100%")
        self.lbl_zoom_pct.setFixedWidth(52)
        self.lbl_zoom_pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_zoom_pct.setStyleSheet("""
            background-color: #2B2D31;
            color: #5865F2;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px;
            font-size: 11px;
            font-weight: 700;
            font-family: monospace;
        """)
        zoom_bar.addWidget(self.lbl_zoom_pct)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setToolTip("Zoom In (Ctrl + Plus / Wheel Up)")
        self.btn_zoom_in.setFixedWidth(32)
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        zoom_bar.addWidget(self.btn_zoom_in)

        self.btn_fit = QPushButton("⛶ Fit")
        self.btn_fit.setToolTip("Fit to Window")
        self.btn_fit.clicked.connect(self._fit_to_window)
        zoom_bar.addWidget(self.btn_fit)

        self.btn_native = QPushButton("1:1")
        self.btn_native.setToolTip("100% Native Resolution")
        self.btn_native.clicked.connect(self._reset_zoom)
        zoom_bar.addWidget(self.btn_native)

        hdr_layout.addLayout(zoom_bar)

        # Actions: Save / Copy / Close
        self.btn_save = QPushButton("💾 Save Image...")
        self.btn_save.setToolTip("Export image to disk")
        self.btn_save.clicked.connect(self._save_image)
        hdr_layout.addWidget(self.btn_save)

        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setToolTip("Copy image to clipboard")
        self.btn_copy.clicked.connect(self._copy_image)
        hdr_layout.addWidget(self.btn_copy)

        self.btn_close = QPushButton("✕ Close")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #383A40;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #ED4245;
            }
        """)
        self.btn_close.clicked.connect(self.accept)
        hdr_layout.addWidget(self.btn_close)

        main_layout.addLayout(hdr_layout)

        # Center Viewport (Scroll Area with Pan-Zoom Label)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #111214;
                border: 1px solid #2B2D31;
                border-radius: 6px;
            }
        """)

        self.img_lbl = PanZoomImageLabel()
        self.img_lbl.scroll_area = self.scroll_area
        self.scroll_area.setWidget(self.img_lbl)

        main_layout.addWidget(self.scroll_area, stretch=1)

        # Bottom Metadata Footer Bar
        footer = QFrame()
        footer.setStyleSheet("""
            QFrame {
                background-color: #2B2D31;
                border: 1px solid #383A40;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QLabel {
                font-size: 11px;
            }
        """)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        footer_layout.setSpacing(16)

        # Resolution pill
        w = self.orig_pixmap.width()
        h = self.orig_pixmap.height()
        lbl_res = QLabel(f"📐 Resolution: {w} × {h} px")
        lbl_res.setStyleSheet("color: #949BA4; font-weight: 600;")
        footer_layout.addWidget(lbl_res)

        # Sensor instrument
        sensor = self.metadata.get("sensor", "Optical / Radiometer Sensor")
        lbl_sensor = QLabel(f"🔬 Sensor: {sensor}")
        lbl_sensor.setStyleSheet("color: #23A55A; font-weight: 600;")
        footer_layout.addWidget(lbl_sensor)

        # Downlink Carrier / Modulation
        carrier = self.metadata.get("downlink", "137 MHz Band Downlink")
        lbl_carrier = QLabel(f"📡 Downlink: {carrier}")
        lbl_carrier.setStyleSheet("color: #5865F2; font-weight: 600;")
        footer_layout.addWidget(lbl_carrier)

        # Acquisition time
        acq = self.metadata.get("acquired", "Recent Orbital Pass")
        lbl_acq = QLabel(f"⏱️ Received: {acq}")
        lbl_acq.setStyleSheet("color: #FEE75C; font-weight: 600;")
        footer_layout.addWidget(lbl_acq)

        footer_layout.addStretch()

        lbl_hint = QLabel("💡 Tip: Click & drag to pan • Mouse wheel to zoom")
        lbl_hint.setStyleSheet("color: #949BA4; font-style: italic;")
        footer_layout.addWidget(lbl_hint)

        main_layout.addWidget(footer)

    def wheelEvent(self, event):
        """Zooms in or out on mouse wheel roll."""
        angle = event.angleDelta().y()
        if angle > 0:
            self._zoom_in()
        elif angle < 0:
            self._zoom_out()
        event.accept()

    def _zoom_in(self):
        new_zoom = min(self.max_zoom, self.zoom_scale * 1.25)
        self._apply_zoom(new_zoom)

    def _zoom_out(self):
        new_zoom = max(self.min_zoom, self.zoom_scale / 1.25)
        self._apply_zoom(new_zoom)

    def _reset_zoom(self):
        self._apply_zoom(1.0)

    def _fit_to_window(self):
        """Scales the image to fit entirely within the scroll area."""
        if self.orig_pixmap.isNull():
            return
        vw = max(200, self.scroll_area.viewport().width() - 20)
        vh = max(200, self.scroll_area.viewport().height() - 20)

        scale_w = vw / self.orig_pixmap.width()
        scale_h = vh / self.orig_pixmap.height()
        fit_scale = min(scale_w, scale_h, 1.5)
        self._apply_zoom(max(self.min_zoom, fit_scale))

    def _apply_zoom(self, scale: float):
        self.zoom_scale = scale
        self.lbl_zoom_pct.setText(f"{int(self.zoom_scale * 100)}%")

        if self.orig_pixmap.isNull():
            return

        target_w = max(10, int(self.orig_pixmap.width() * self.zoom_scale))
        target_h = max(10, int(self.orig_pixmap.height() * self.zoom_scale))

        scaled = self.orig_pixmap.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.img_lbl.setPixmap(scaled)
        self.img_lbl.resize(scaled.size())

    def _save_image(self):
        """Exports the full-resolution image to user disk."""
        ext = Path(self.image_path).suffix.lower() or ".jpg"
        filter_str = f"Image (*{ext});;All Files (*.*)"
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Downlinked Observation Image",
            Path(self.image_path).name,
            filter_str,
        )
        if dest:
            self.orig_pixmap.save(dest)

    def _copy_image(self):
        """Copies the image to the system clipboard."""
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(self.orig_pixmap)
            self.btn_copy.setText("✓ Copied!")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.btn_copy.setText("📋 Copy"))
