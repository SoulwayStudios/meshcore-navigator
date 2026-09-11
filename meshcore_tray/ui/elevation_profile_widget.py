"""Interactive Point-to-Point Topographic RF Elevation Profile Widget for MeshCore Navigator."""

import math
from typing import Dict, List, Optional
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

DARK_BG = QColor("#12151A")
PANEL_BG = QColor("#181B20")
GRID_COLOR = QColor("#2A303C")
TERRAIN_STROKE = QColor("#10B981")
TERRAIN_FILL_TOP = QColor(16, 185, 129, 45)
TERRAIN_FILL_BOT = QColor(18, 21, 26, 240)
LOS_CLEAR_COLOR = QColor("#38BDF8")
LOS_OBSTRUCTED_COLOR = QColor("#EF4444")
FRESNEL_COLOR = QColor("#FBBF24")
TEXT_MUTED = QColor("#9CA3AF")
TEXT_LIGHT = QColor("#F3F4F6")


class ElevationCanvasWidget(QWidget):
    """Custom QPainter canvas that draws the 2D elevation cross-section, direct LOS, and 1st Fresnel zone."""

    point_hovered = pyqtSignal(float, float, float)  # lat, lon, dist_m
    hover_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(140)
        self.profile_data: Optional[dict] = None
        self.hover_x: Optional[float] = None
        self.hover_point_info: Optional[dict] = None

    def set_profile_data(self, data: Optional[dict]):
        self.profile_data = data
        self.hover_x = None
        self.hover_point_info = None
        self.update()

    def mouseMoveEvent(self, event):
        if not self.profile_data or not self.profile_data.get("points"):
            super().mouseMoveEvent(event)
            return

        x = event.position().x()
        w = self.width()
        padding_l = 60.0
        padding_r = 40.0
        chart_w = w - padding_l - padding_r

        if x < padding_l or x > w - padding_r or chart_w <= 0:
            self.hover_x = None
            self.hover_point_info = None
            self.hover_cleared.emit()
            self.update()
            return

        frac = (x - padding_l) / chart_w
        points = self.profile_data["points"]
        idx = max(0, min(int(round(frac * (len(points) - 1))), len(points) - 1))
        pt = points[idx]

        self.hover_x = x
        self.hover_point_info = pt
        self.point_hovered.emit(pt["lat"], pt["lon"], pt["distance_m"])
        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.hover_point_info = None
        self.hover_cleared.emit()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = self.width()
        h = self.height()

        # 1. Background
        painter.fillRect(0, 0, w, h, PANEL_BG)

        if not self.profile_data or not self.profile_data.get("points"):
            painter.setPen(TEXT_MUTED)
            painter.setFont(QFont("Inter", 11))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Select two nodes or points on the map to view RF Path Elevation Profile")
            return

        points = self.profile_data["points"]
        if len(points) < 2:
            return

        padding_l = 65.0
        padding_r = 45.0
        padding_t = 30.0
        padding_b = 30.0

        chart_w = w - padding_l - padding_r
        chart_h = h - padding_t - padding_b
        if chart_w <= 10 or chart_h <= 10:
            return

        total_d_m = self.profile_data.get("total_distance_m")
        if not total_d_m:
            total_d_m = float(self.profile_data.get("total_distance_km", 1.0)) * 1000.0

        def get_eff_terrain(p: dict) -> float:
            return float(p.get("effective_terrain_asl", p.get("terrain_asl_m", 0.0)))

        def get_ground_elev(p: dict) -> float:
            return float(p.get("ground_elev_m", p.get("terrain_asl_m", 0.0)))

        def get_los(p: dict) -> float:
            return float(p.get("los_asl", p.get("optical_los_asl_m", 0.0)))

        def get_fresnel_top(p: dict) -> float:
            return float(p.get("fresnel_top_asl", get_los(p) + 15.0))

        def get_fresnel_bot(p: dict) -> float:
            return float(p.get("fresnel_bot_asl", p.get("fresnel_lower_asl_m", get_los(p) - 15.0)))

        def get_dist_m(p: dict) -> float:
            return float(p.get("distance_m", p.get("distance_km", 0.0) * 1000.0))

        # 2. Determine Y scale (Elevation min/max across terrain, LOS, and Fresnel)
        all_elevs = []
        for p in points:
            all_elevs.append(get_eff_terrain(p))
            all_elevs.append(get_los(p))
            all_elevs.append(get_fresnel_top(p))
            all_elevs.append(get_fresnel_bot(p))

        min_y = min(all_elevs) if all_elevs else 0.0
        max_y = max(all_elevs) if all_elevs else 100.0

        # Add 10% breathing room
        y_range = max(15.0, max_y - min_y)
        min_y = math.floor((min_y - y_range * 0.08) / 10.0) * 10.0
        max_y = math.ceil((max_y + y_range * 0.12) / 10.0) * 10.0
        y_span = max(1.0, max_y - min_y)

        def to_screen_x(dist_m: float) -> float:
            return padding_l + (dist_m / total_d_m) * chart_w

        def to_screen_y(elev_m: float) -> float:
            return padding_t + (1.0 - (elev_m - min_y) / y_span) * chart_h

        # 3. Draw Grid & Axes
        pen_grid = QPen(GRID_COLOR, 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_grid)
        painter.setFont(QFont("Inter", 9))

        # Y-axis horizontal grid lines (4-5 steps)
        y_step = 20.0 if y_span <= 120 else (50.0 if y_span <= 300 else 100.0)
        curr_y = math.ceil(min_y / y_step) * y_step
        while curr_y <= max_y:
            sy = to_screen_y(curr_y)
            painter.setPen(pen_grid)
            painter.drawLine(QPointF(padding_l, sy), QPointF(w - padding_r, sy))
            painter.setPen(TEXT_MUTED)
            painter.drawText(QRectF(0, sy - 9, padding_l - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{int(curr_y)}m")
            curr_y += y_step

        # X-axis vertical grid lines (distance km)
        total_km = total_d_m / 1000.0
        x_step_km = 5.0 if total_km > 20 else (2.0 if total_km > 8 else 1.0)
        curr_km = 0.0
        while curr_km <= total_km:
            sx = to_screen_x(curr_km * 1000.0)
            painter.setPen(pen_grid)
            painter.drawLine(QPointF(sx, padding_t), QPointF(sx, h - padding_b))
            painter.setPen(TEXT_MUTED)
            painter.drawText(QRectF(sx - 25, h - padding_b + 4, 50, 20), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, f"{int(curr_km)}km")
            curr_km += x_step_km

        # 4. Draw 1st Fresnel Zone (Dotted envelope)
        pen_fresnel = QPen(FRESNEL_COLOR, 1.5, Qt.PenStyle.DotLine)
        painter.setPen(pen_fresnel)
        path_fresnel_top = QPainterPath()
        path_fresnel_bot = QPainterPath()
        for i, p in enumerate(points):
            sx = to_screen_x(get_dist_m(p))
            sy_top = to_screen_y(get_fresnel_top(p))
            sy_bot = to_screen_y(get_fresnel_bot(p))
            if i == 0:
                path_fresnel_top.moveTo(sx, sy_top)
                path_fresnel_bot.moveTo(sx, sy_bot)
            else:
                path_fresnel_top.lineTo(sx, sy_top)
                path_fresnel_bot.lineTo(sx, sy_bot)
        painter.drawPath(path_fresnel_top)
        painter.drawPath(path_fresnel_bot)

        # 5. Draw Direct Line of Sight (LOS) Beam
        is_obstructed = (self.profile_data.get("status") == "OBSTRUCTED")
        los_color = LOS_OBSTRUCTED_COLOR if is_obstructed else LOS_CLEAR_COLOR
        pen_los = QPen(los_color, 2, Qt.PenStyle.SolidLine)
        painter.setPen(pen_los)

        tx_total = self.profile_data.get("tx_total_asl")
        if tx_total is None:
            tx_total = get_ground_elev(points[0]) + float(self.profile_data.get("tx_height_m", 8.0))
        rx_total = self.profile_data.get("rx_total_asl")
        if rx_total is None:
            rx_total = get_ground_elev(points[-1]) + float(self.profile_data.get("rx_height_m", 2.0))

        p_start_los = QPointF(to_screen_x(0), to_screen_y(tx_total))
        p_end_los = QPointF(to_screen_x(total_d_m), to_screen_y(rx_total))
        painter.drawLine(p_start_los, p_end_los)

        # 6. Draw Terrain Profile (Filled mountain silhouette with glowing crest)
        terrain_path = QPainterPath()
        terrain_path.moveTo(to_screen_x(0), h - padding_b)
        for p in points:
            sx = to_screen_x(get_dist_m(p))
            sy = to_screen_y(get_eff_terrain(p))
            terrain_path.lineTo(sx, sy)
        terrain_path.lineTo(to_screen_x(total_d_m), h - padding_b)
        terrain_path.closeSubpath()

        grad = QLinearGradient(0, padding_t, 0, h - padding_b)
        grad.setColorAt(0.0, TERRAIN_FILL_TOP)
        grad.setColorAt(1.0, TERRAIN_FILL_BOT)
        painter.fillPath(terrain_path, grad)

        # Draw terrain crest line
        pen_crest = QPen(TERRAIN_STROKE, 2)
        painter.setPen(pen_crest)
        crest_path = QPainterPath()
        for i, p in enumerate(points):
            sx = to_screen_x(get_dist_m(p))
            sy = to_screen_y(get_eff_terrain(p))
            if i == 0:
                crest_path.moveTo(sx, sy)
            else:
                crest_path.lineTo(sx, sy)
        painter.drawPath(crest_path)

        # 7. Draw Antenna Towers at Start and End
        def draw_tower(x: float, ground_asl: float, ant_asl: float, label: str, is_start: bool):
            sy_ground = to_screen_y(ground_asl)
            sy_ant = to_screen_y(ant_asl)
            # Mast pole
            painter.setPen(QPen(TEXT_LIGHT, 2))
            painter.drawLine(QPointF(x, sy_ground), QPointF(x, sy_ant))
            # Antenna tip beacon
            painter.setBrush(los_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(x, sy_ant), 4, 4)
            # Label
            painter.setPen(TEXT_LIGHT)
            painter.setFont(QFont("Inter", 8, QFont.Weight.Bold))
            if is_start:
                painter.drawText(QRectF(x + 4, sy_ant - 18, 200, 14), Qt.AlignmentFlag.AlignLeft, label)
            else:
                painter.drawText(QRectF(x - 220, sy_ant - 18, 216, 14), Qt.AlignmentFlag.AlignRight, label)

        draw_tower(to_screen_x(0), get_ground_elev(points[0]), tx_total, f"{self.profile_data.get('alias1', 'Tx')}", is_start=True)
        draw_tower(to_screen_x(total_d_m), get_ground_elev(points[-1]), rx_total, f"{self.profile_data.get('alias2', 'Rx')}", is_start=False)

        # 8. Draw Worst Obstacle Callout Marker if Obstructed
        worst = self.profile_data.get("worst_obstacle")
        if worst and is_obstructed:
            obs_dist = worst.get("distance_m", 0.0)
            obs_elev = worst.get("terrain_elev_m", 0.0) + worst.get("bulge_m", 0.0)
            ox = to_screen_x(obs_dist)
            oy = to_screen_y(obs_elev)
            painter.setBrush(LOS_OBSTRUCTED_COLOR)
            painter.setPen(QPen(TEXT_LIGHT, 1.5))
            painter.drawEllipse(QPointF(ox, oy), 6, 6)

            # Flag banner
            obs_m = worst.get("obstruction_m", "")
            obs_km = worst.get("distance_km", "")
            callout_text = f"OBSTRUCTED: +{obs_m}m @ {obs_km}km"
            painter.setFont(QFont("Inter", 9, QFont.Weight.Bold))
            painter.setPen(LOS_OBSTRUCTED_COLOR)
            painter.drawText(QRectF(ox - 130, oy - 26, 260, 18), Qt.AlignmentFlag.AlignCenter, callout_text)

        # 9. Draw Mouse Hover Guide & Telemetry Tooltip
        if self.hover_x is not None and self.hover_point_info is not None:
            pt = self.hover_point_info
            hx = self.hover_x
            pen_hover = QPen(TEXT_LIGHT, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_hover)
            painter.drawLine(QPointF(hx, padding_t), QPointF(hx, h - padding_b))

            hy_terrain = to_screen_y(get_eff_terrain(pt))
            painter.setBrush(TEXT_LIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(hx, hy_terrain), 3.5, 3.5)

            # Tooltip card
            d_km = pt.get('distance_km', round(get_dist_m(pt) / 1000.0, 2))
            g_elev = round(get_ground_elev(pt), 1)
            los_v = round(get_los(pt), 1)
            clr_v = round(pt.get('opt_clearance_m', pt.get('optical_clearance_m', los_v - get_eff_terrain(pt))), 1)
            f1_v = round(pt.get('f1_radius_m', 15.0), 1)
            tip_text = (
                f"{d_km} km | Elev: {g_elev}m | "
                f"LOS: {los_v}m | Clr: {clr_v}m | F1: {f1_v}m"
            )
            painter.setFont(QFont("Inter", 9, QFont.Weight.Bold))
            tw = painter.fontMetrics().horizontalAdvance(tip_text) + 16
            tip_x = max(padding_l, min(hx - tw / 2.0, w - padding_r - tw))
            tip_rect = QRectF(tip_x, padding_t + 4, tw, 22)
            painter.setBrush(QColor(24, 27, 32, 235))
            painter.setPen(QPen(QColor("#374151"), 1))
            painter.drawRoundedRect(tip_rect, 4, 4)
            painter.setPen(TEXT_LIGHT)
            painter.drawText(tip_rect, Qt.AlignmentFlag.AlignCenter, tip_text)


class ElevationProfileWidget(QFrame):
    """Dockable panel containing RF elevation profile canvas, antenna height controls, and LOS badge."""

    close_requested = pyqtSignal()
    heights_changed = pyqtSignal(float, float)  # tx_height_m, rx_height_m
    point_scrubbed = pyqtSignal(float, float)   # lat, lon (for Leaflet map crosshair)
    scrub_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("elevationProfileDock")
        self.setStyleSheet("""
            QFrame#elevationProfileDock {
                background-color: #15181D;
                border-top: 1px solid #282E39;
            }
            QLabel {
                color: #F3F4F6;
            }
            QDoubleSpinBox {
                background-color: #1F242C;
                color: #F3F4F6;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 2px 4px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #262B34;
                color: #D1D5DB;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #374151;
                color: #FFFFFF;
            }
        """)
        self._current_profile: Optional[dict] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 1. Header Toolbar
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(4, 0, 4, 0)
        hdr_layout.setSpacing(8)

        self.title_label = QLabel("🏔️ <b>RF Elevation Profile</b>")
        self.title_label.setStyleSheet("font-size: 12px; color: #38BDF8;")
        hdr_layout.addWidget(self.title_label)

        self.path_label = QLabel("Select nodes on map to profile")
        self.path_label.setStyleSheet("font-size: 11px; color: #9CA3AF;")
        hdr_layout.addWidget(self.path_label)

        hdr_layout.addStretch()

        # Antenna height controls
        hdr_layout.addWidget(QLabel("Tx Ant:"))
        self.spin_tx = QDoubleSpinBox()
        self.spin_tx.setRange(0.5, 100.0)
        self.spin_tx.setValue(8.0)
        self.spin_tx.setSingleStep(1.0)
        self.spin_tx.setSuffix(" m")
        self.spin_tx.valueChanged.connect(self._on_height_changed)
        hdr_layout.addWidget(self.spin_tx)

        hdr_layout.addWidget(QLabel("Rx Ant:"))
        self.spin_rx = QDoubleSpinBox()
        self.spin_rx.setRange(0.5, 100.0)
        self.spin_rx.setValue(2.0)
        self.spin_rx.setSingleStep(1.0)
        self.spin_rx.setSuffix(" m")
        self.spin_rx.valueChanged.connect(self._on_height_changed)
        hdr_layout.addWidget(self.spin_rx)

        # Status badge
        self.status_badge = QLabel("READY")
        self.status_badge.setStyleSheet("""
            background-color: #1F2937;
            color: #9CA3AF;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
        """)
        hdr_layout.addWidget(self.status_badge)

        # Close button
        btn_close = QPushButton("✕")
        btn_close.setToolTip("Close Elevation Profile")
        btn_close.setFixedWidth(26)
        btn_close.clicked.connect(self.close_requested.emit)
        hdr_layout.addWidget(btn_close)

        layout.addLayout(hdr_layout)

        # 2. Interactive Canvas
        self.canvas = ElevationCanvasWidget(self)
        self.canvas.point_hovered.connect(lambda lat, lon, d: self.point_scrubbed.emit(lat, lon))
        self.canvas.hover_cleared.connect(self.scrub_cleared.emit)
        layout.addWidget(self.canvas, 1)

    def set_profile(self, data: dict):
        self._current_profile = data
        self.canvas.set_profile_data(data)

        alias1 = data.get("alias1", "Point A")
        alias2 = data.get("alias2", "Point B")
        dist_km = data.get("total_distance_km", 0.0)
        bearing = data.get("bearing_deg", 0.0)

        self.path_label.setText(f"<b>{alias1}</b> → <b>{alias2}</b>: {dist_km} km ({bearing}°)")

        status = data.get("status", "READY")
        status_label = data.get("status_label", status)
        min_clr = data.get("min_optical_clearance_m", 0.0)
        min_fresnel = data.get("min_fresnel_clearance_m", 0.0)

        if status == "CLEAR":
            self.status_badge.setText(f"🟢 {status_label} (+{min_fresnel}m Fresnel)")
            self.status_badge.setStyleSheet("background-color: rgba(16, 185, 129, 0.2); color: #10B981; border: 1px solid #10B981; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;")
        elif status == "FRESNEL_INCURSION":
            self.status_badge.setText(f"🟡 {status_label} ({min_fresnel}m)")
            self.status_badge.setStyleSheet("background-color: rgba(245, 158, 11, 0.2); color: #F59E0B; border: 1px solid #F59E0B; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;")
        else:
            worst = data.get("worst_obstacle") or {}
            obs_m = worst.get("obstruction_m", abs(min_clr))
            obs_km = worst.get("distance_km", "")
            obs_info = f" (+{obs_m}m @ {obs_km}km)" if obs_km else ""
            self.status_badge.setText(f"🔴 {status_label}{obs_info}")
            self.status_badge.setStyleSheet("background-color: rgba(239, 68, 68, 0.2); color: #EF4444; border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;")

        # Update spinners if different
        self.spin_tx.blockSignals(True)
        self.spin_rx.blockSignals(True)
        self.spin_tx.setValue(float(data.get("tx_height_m", 8.0)))
        self.spin_rx.setValue(float(data.get("rx_height_m", 2.0)))
        self.spin_tx.blockSignals(False)
        self.spin_rx.blockSignals(False)

    def _on_height_changed(self):
        tx = float(self.spin_tx.value())
        rx = float(self.spin_rx.value())
        self.heights_changed.emit(tx, rx)
