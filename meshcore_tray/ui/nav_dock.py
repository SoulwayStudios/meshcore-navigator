"""Discord-Style Left Navigation & Map Layer Dock Widget for MeshCore Tray."""

import logging
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QByteArray, QSize
from PyQt6.QtGui import QCursor, QIcon, QPixmap, QPainter, QColor, QRadialGradient, QBrush
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QFrame, QMenu, QLabel, QToolTip
)

from meshcore_tray.config import AppConfig
from meshcore_tray.ui.avatar_generator import get_contact_avatar_icon

logger = logging.getLogger("meshcore_tray.nav_dock")

# Vector SVG line-art glyphs for map layers (white inactive, glowing green active)
LAYER_SVGS = {
    # Observer Traffic: Clean vector Eye
    "rf_links": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><circle cx="12" cy="12" r="3" stroke="{color}" stroke-width="2" fill="none"/>',
    "byte_paths": '<circle cx="5" cy="19" r="2" stroke="{color}" stroke-width="2" fill="none"/><circle cx="12" cy="5" r="2" stroke="{color}" stroke-width="2" fill="none"/><circle cx="19" cy="19" r="2" stroke="{color}" stroke-width="2" fill="none"/><path d="M6.5 17.5L10.5 7M13.5 7L17.5 17.5" stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/>',
    # Node Activity Heatmap: Clean vector Flame
    "heatmap": '<path d="M8.5 14.5A5.5 5.5 0 0 0 17.5 13c0-3.5-3-6-4.5-8.5C11.5 7 8.5 9.5 8.5 14.5z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M12 17.5a2.5 2.5 0 0 0 2.5-2.5c0-1.5-1.2-2.5-2.5-3.5-1.3 1-2.5 2-2.5 3.5a2.5 2.5 0 0 0 2.5 2.5z" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "orbitals": '<circle cx="12" cy="12" r="4" stroke="{color}" stroke-width="2" fill="none"/><ellipse cx="12" cy="12" rx="10" ry="4" stroke="{color}" stroke-width="1.8" stroke-linecap="round" transform="rotate(-30 12 12)" fill="none"/>',
    "tropo": '<path d="M2 20h20M5 16a10 10 0 0 1 14 0M8 12a6 6 0 0 1 8 0M11 8a2 2 0 0 1 2 0" stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/>',
    # Thunderstorm View: Sharp vector Cloud + Lightning
    "thunderstorm": '<path d="M19 16.9A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><polyline points="13 11 9 17 15 17 11 23" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    # ADSB View: Symmetrical top-down Airplane outline
    "adsb": '<path d="M12 2v8M12 10l9 4v2l-9-3v5l3 2v2l-3-1-3 1v-2l3-2v-5l-9 3v-2l9-4z" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "scopes": '<circle cx="12" cy="12" r="9" stroke="{color}" stroke-width="1.8" fill="none"/><circle cx="12" cy="12" r="5" stroke="{color}" stroke-width="1.5" fill="none"/><circle cx="12" cy="12" r="1.5" fill="{color}"/><line x1="12" y1="3" x2="12" y2="21" stroke="{color}" stroke-width="1.5"/><line x1="3" y1="12" x2="21" y2="12" stroke="{color}" stroke-width="1.5"/>',
    "rf_los": '<path d="M2 20L8.5 9l3.5 5.5 4-6.5 6 12H2z" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" fill="none"/><circle cx="16" cy="5" r="2" fill="{color}"/><line x1="3" y1="6" x2="14" y2="6" stroke="{color}" stroke-width="1.5" stroke-dasharray="2 2"/>',
    # Space Weather: Vertical undulating Aurora Borealis ribbons from mockup
    "space_weather": '<path d="M3 18h18" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/><path d="M4 18v-4M7 18v-7M10 18v-9M13 18v-11M16 18v-8M19 18v-5" stroke="{color}" stroke-width="2" stroke-linecap="round"/><path d="M3 14c3-3 6-5 10-5s5 2 8 4" stroke="{color}" stroke-width="1.5" stroke-linecap="round" fill="none"/>'
}

# Primary navigation action bar vector glyphs (white inactive, glowing emerald active)
PRIMARY_NAV_SVGS = {
    "app_icon": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    "main": '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "floods": '<path d="M2 12h3l3-7 4 14 3-7h7" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "dms": '<path d="M17 8h2a2 2 0 0 1 2 2v7l-3-2h-1" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M15 14H7l-4 3V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "rooms": '<path d="M12 2L2 12l10 10 10-10L12 2z" stroke="{color}" stroke-width="2" stroke-linejoin="round" fill="none"/><path d="M7 12h10M9 9h6M9 15h6" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>',
    "repeaters": '<path d="M12 18v4M9 22h6M12 18l3-11h-6l3 11z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M7.5 9.5a6.5 6.5 0 0 1 9 0M5 7a10 10 0 0 1 14 0" stroke="{color}" stroke-width="2" stroke-linecap="round" fill="none"/>',
}

CYCLE_SVGS = {
    "ALL": '<circle cx="12" cy="12" r="9" stroke="{color}" stroke-width="2" fill="none"/><path d="M3.6 9h16.8M3.6 15h16.8M12 3a15.3 15.3 0 0 1 4 9 15.3 15.3 0 0 1-4 9 15.3 15.3 0 0 1-4-9 15.3 15.3 0 0 1 4-9z" stroke="{color}" stroke-width="1.6" fill="none"/>',
    "CLIENTS": '<rect x="6" y="2" width="12" height="20" rx="2" stroke="{color}" stroke-width="2" fill="none"/><line x1="12" y1="18" x2="12.01" y2="18" stroke="{color}" stroke-width="2" stroke-linecap="round"/>',
    "REPEATERS": '<path d="M12 2v20M4 6l8 4 8-4M4 14l8 4 8-4M7 22l5-4 5 4" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "ROOMS": '<path d="M12 2L2 12l10 10 10-10L12 2z" stroke="{color}" stroke-width="2" stroke-linejoin="round" fill="none"/><circle cx="12" cy="12" r="2.5" fill="{color}"/>'
}


def create_layer_icon(icon_name: str, active: bool = False) -> Optional[QIcon]:
    """Renders crisp vector SVG icons: white when inactive, glowing neon emerald with radial halo when active."""
    inner_svg = LAYER_SVGS.get(icon_name)
    if not inner_svg:
        return None
    try:
        color = "#34D399" if active else "#FFFFFF"
        svg_str = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{inner_svg.format(color=color)}</svg>'
        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if active:
            # Subtle radial glowing emerald halo
            glow = QRadialGradient(16, 16, 13)
            glow.setColorAt(0.0, QColor(52, 211, 153, 90))
            glow.setColorAt(0.6, QColor(16, 185, 129, 35))
            glow.setColorAt(1.0, QColor(16, 185, 129, 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(2, 2, 28, 28)
        renderer.render(painter)
        painter.end()
        return QIcon(pix)
    except Exception as e:
        logger.warning(f"Failed rendering SVG icon for {icon_name}: {e}")
        return None


def create_cycle_icon(mode_name: str) -> Optional[QIcon]:
    """Renders crisp vector SVG icons for node type filtering."""
    inner_svg = CYCLE_SVGS.get(mode_name)
    if not inner_svg:
        return None
    try:
        svg_str = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{inner_svg.format(color="#34D399")}</svg>'
        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        return QIcon(pix)
    except Exception as e:
        logger.warning(f"Failed rendering cycle icon for {mode_name}: {e}")
        return None


def create_nav_icon(icon_name: str, active: bool = False) -> Optional[QIcon]:
    """Renders crisp vector SVG icons for primary navigation: white inactive, glowing emerald with radial halo active."""
    inner_svg = PRIMARY_NAV_SVGS.get(icon_name)
    if not inner_svg:
        return None
    try:
        color = "#34D399" if active else "#FFFFFF"
        svg_str = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{inner_svg.format(color=color)}</svg>'
        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if active:
            glow = QRadialGradient(16, 16, 13)
            glow.setColorAt(0.0, QColor(52, 211, 153, 90))
            glow.setColorAt(0.6, QColor(16, 185, 129, 35))
            glow.setColorAt(1.0, QColor(16, 185, 129, 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(2, 2, 28, 28)
        renderer.render(painter)
        painter.end()
        return QIcon(pix)
    except Exception as e:
        logger.warning(f"Failed rendering nav SVG icon for {icon_name}: {e}")
        return None


class DockButton(QPushButton):
    """Discord-style squircle button with crisp vector SVG icon that illuminates when active."""

    def __init__(self, key_or_text: str = "", tooltip: str = "", icon_name: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.raw_text = key_or_text
        self.icon_name = icon_name or self._resolve_icon_name(key_or_text)
        self.is_active = False
        self._icon_inactive = create_nav_icon(self.icon_name, active=False) if self.icon_name else None
        self._icon_active = create_nav_icon(self.icon_name, active=True) if self.icon_name else None
        self._update_style()

    def _resolve_icon_name(self, text: str) -> Optional[str]:
        mapping = {
            "main": "main",
            "💬": "main",
            "floods": "floods",
            "🌊": "floods",
            "dms": "dms",
            "👥": "dms",
            "rooms": "rooms",
            "🏢": "rooms",
            "repeaters": "repeaters",
            "📡": "repeaters",
            "app_icon": "app_icon",
            "⚡": "app_icon",
        }
        return mapping.get(text)

    def set_active(self, active: bool):
        self.is_active = active
        self._update_style()

    def _update_style(self):
        if self.is_active:
            if self._icon_active:
                self.setIcon(self._icon_active)
                self.setIconSize(QSize(24, 24))
                self.setText("")
            else:
                self.setText(self.raw_text)
            self.setStyleSheet("""
                DockButton {
                    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #064E3B, stop:1 #022C22);
                    color: #34D399;
                    border: 1.5px solid #10B981;
                    border-radius: 14px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 0px;
                    text-align: center;
                }
                DockButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #047857, stop:1 #064E3B);
                    border-color: #34D399;
                    color: #FFFFFF;
                }
            """)
        else:
            if self._icon_inactive:
                self.setIcon(self._icon_inactive)
                self.setIconSize(QSize(24, 24))
                self.setText("")
            else:
                self.setText(self.raw_text)
            self.setStyleSheet("""
                DockButton {
                    background-color: #24262B;
                    color: #FFFFFF;
                    border: 1px solid #33363E;
                    border-radius: 14px;
                    font-size: 18px;
                    padding: 0px;
                    text-align: center;
                }
                DockButton:hover {
                    background-color: #2F3239;
                    color: #FFFFFF;
                    border-color: #64748B;
                }
            """)


class LayerButton(QPushButton):
    """Compact toggle button for map layers with crisp white inactive icons and glowing emerald active icons."""

    def __init__(self, key_or_text: str = "", tooltip: str = "", icon_name: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(tooltip)

        self.raw_text = key_or_text
        self.icon_name = icon_name or self._resolve_icon_name(key_or_text)
        self._icon_inactive = create_layer_icon(self.icon_name, active=False) if self.icon_name else None
        self._icon_active = create_layer_icon(self.icon_name, active=True) if self.icon_name else None

        self.toggled.connect(self._on_toggled)
        self._update_style(False)

    def _resolve_icon_name(self, text: str) -> Optional[str]:
        mapping = {
            "⚡": "rf_links",
            "rf_links": "rf_links",
            "🛣️": "byte_paths",
            "byte_paths": "byte_paths",
            "🔥": "heatmap",
            "activity_heatmap": "heatmap",
            "heatmap": "heatmap",
            "🛰️": "orbitals",
            "orbitals": "orbitals",
            "📶": "tropo",
            "tropo": "tropo",
            "⛈️": "thunderstorm",
            "thunderstorm": "thunderstorm",
            "✈️": "adsb",
            "adsb": "adsb",
            "🌐": "scopes",
            "scopes": "scopes",
            "🏔️": "rf_los",
            "rf_los": "rf_los",
            "🌌": "space_weather",
            "space_weather": "space_weather",
        }
        return mapping.get(text)

    def _on_toggled(self, checked: bool):
        self._update_style(checked)

    def _update_style(self, checked: bool):
        if checked:
            if self._icon_active:
                self.setIcon(self._icon_active)
                self.setIconSize(QSize(24, 24))
                self.setText("")
            else:
                self.setText(self.raw_text)
            self.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #064E3B, stop:1 #022C22);
                    color: #34D399;
                    border: 1.5px solid #10B981;
                    border-radius: 14px;
                    font-size: 18px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #047857, stop:1 #064E3B);
                    border-color: #34D399;
                    color: #FFFFFF;
                }
            """)
        else:
            if self._icon_inactive:
                self.setIcon(self._icon_inactive)
                self.setIconSize(QSize(24, 24))
                self.setText("")
            else:
                self.setText(self.raw_text)
            self.setStyleSheet("""
                QPushButton {
                    background-color: #24262B;
                    color: #FFFFFF;
                    border: 1px solid #33363E;
                    border-radius: 14px;
                    font-size: 18px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #2F3239;
                    color: #FFFFFF;
                    border-color: #64748B;
                }
            """)


class CycleFilterButton(QPushButton):
    """4-state cycle button for filtering nodes (ALL, CLIENTS, REPEATERS, ROOMS)."""

    filter_cycled = pyqtSignal(str)  # ("ALL", "CLIENTS", "REPEATERS", "ROOMS")

    MODES = [
        ("ALL", "🌐", "Node Type: All Nodes"),
        ("CLIENTS", "📱", "Node Type: Clients Only"),
        ("REPEATERS", "📡", "Node Type: Repeaters Only"),
        ("ROOMS", "🏢", "Node Type: Room Servers Only")
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.current_idx = 0
        self.clicked.connect(self._on_clicked)
        self._update_display()

    def _on_clicked(self):
        self.current_idx = (self.current_idx + 1) % len(self.MODES)
        self._update_display()
        self.filter_cycled.emit(self.MODES[self.current_idx][0])

    def set_mode(self, mode_name: str):
        for idx, (m, _, _) in enumerate(self.MODES):
            if m == mode_name:
                self.current_idx = idx
                self._update_display()
                break

    def _update_display(self):
        mode, fallback_emoji, tip = self.MODES[self.current_idx]
        svg_icon = create_cycle_icon(mode)
        if svg_icon:
            self.setIcon(svg_icon)
            self.setIconSize(QSize(24, 24))
            self.setText("")
        else:
            self.setText(fallback_emoji)
        self.setToolTip(f"{tip} (Click to cycle)")
        self.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #064E3B, stop:1 #022C22);
                color: #34D399;
                border: 1.5px solid #10B981;
                border-radius: 14px;
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #047857, stop:1 #064E3B);
                border-color: #34D399;
                color: #FFFFFF;
            }
        """)


class MapLayerDockWidget(QWidget):
    """Vertical dock bar for Map Layer Overlays (48px wide), positioned beside the Leaflet map."""

    layer_toggled = pyqtSignal(str, bool)
    node_filter_changed = pyqtSignal(str)

    def __init__(self, config: Optional[AppConfig] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.setFixedWidth(48)
        self.setObjectName("mapLayerDock")
        self.setStyleSheet("""
            QWidget#mapLayerDock {
                background-color: #14161B;
                border-right: 1px solid #23262D;
            }
        """)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # 1. Node Type Cycle Filter
        self.btn_cycle_filter = CycleFilterButton(parent=self)
        self.btn_cycle_filter.filter_cycled.connect(self.node_filter_changed.emit)
        layout.addWidget(self.btn_cycle_filter)

        # 2. Observer / Watcher Traffic (Defaulted ON)
        self.btn_rf_links = LayerButton("rf_links", "Observer / Watcher Traffic", parent=self)
        self.btn_rf_links.setChecked(True)
        self.btn_rf_links.toggled.connect(lambda ch: self.layer_toggled.emit("rf_links", ch))
        layout.addWidget(self.btn_rf_links)

        # 3. Path Modes
        self.btn_byte_paths = LayerButton("byte_paths", "Path Modes", parent=self)
        self.btn_byte_paths.setChecked(False)
        self.btn_byte_paths.toggled.connect(lambda ch: self.layer_toggled.emit("byte_paths", ch))
        layout.addWidget(self.btn_byte_paths)

        # 4. Node Activity Heatmap
        self.btn_heatmap = LayerButton("activity_heatmap", "Node Activity Heatmap", parent=self)
        self.btn_heatmap.setChecked(False)
        self.btn_heatmap.toggled.connect(lambda ch: self.layer_toggled.emit("activity_heatmap", ch))
        layout.addWidget(self.btn_heatmap)

        # 5. Orbital View
        self.btn_orbitals = LayerButton("orbitals", "Orbital View", parent=self)
        self.btn_orbitals.setChecked(False)
        self.btn_orbitals.toggled.connect(lambda ch: self.layer_toggled.emit("orbitals", ch))
        layout.addWidget(self.btn_orbitals)

        # 6. Tropo Forecast View
        self.btn_tropo = LayerButton("tropo", "Tropo Forecast View", parent=self)
        self.btn_tropo.setChecked(False)
        self.btn_tropo.toggled.connect(lambda ch: self.layer_toggled.emit("tropo", ch))
        layout.addWidget(self.btn_tropo)

        # 7. Thunderstorm View
        self.btn_thunderstorm = LayerButton("thunderstorm", "Thunderstorm View", parent=self)
        self.btn_thunderstorm.setChecked(False)
        self.btn_thunderstorm.toggled.connect(lambda ch: self.layer_toggled.emit("thunderstorm", ch))
        layout.addWidget(self.btn_thunderstorm)

        # 8. ADSB View
        self.btn_adsb = LayerButton("adsb", "ADSB View", parent=self)
        self.btn_adsb.setChecked(False)
        self.btn_adsb.toggled.connect(lambda ch: self.layer_toggled.emit("adsb", ch))
        layout.addWidget(self.btn_adsb)

        # 9. Scope View
        self.btn_scopes = LayerButton("scopes", "Scope View", parent=self)
        self.btn_scopes.setChecked(False)
        self.btn_scopes.toggled.connect(lambda ch: self.layer_toggled.emit("scopes", ch))
        layout.addWidget(self.btn_scopes)

        # 10. RF Line of Sight & Topo Elevation View
        self.btn_rf_los = LayerButton("rf_los", "Line of Sight & Topo Elevation", parent=self)
        self.btn_rf_los.setChecked(False)
        self.btn_rf_los.toggled.connect(lambda ch: self.layer_toggled.emit("rf_los", ch))
        layout.addWidget(self.btn_rf_los)

        # 11. Space Weather & Aurora View
        self.btn_space_weather = LayerButton("space_weather", "Space Weather & Aurora View", parent=self)
        self.btn_space_weather.setChecked(False)
        self.btn_space_weather.toggled.connect(lambda ch: self.layer_toggled.emit("space_weather", ch))
        layout.addWidget(self.btn_space_weather)

        layout.addStretch()

    def set_layer_active(self, layer_key: str, is_active: bool):
        """Programmatically syncs dock layer toggle button states without re-emitting signals."""
        mapping = {
            "rf_links": self.btn_rf_links,
            "byte_paths": self.btn_byte_paths,
            "activity_heatmap": self.btn_heatmap,
            "orbitals": self.btn_orbitals,
            "tropo": self.btn_tropo,
            "thunderstorm": self.btn_thunderstorm,
            "adsb": self.btn_adsb,
            "scopes": self.btn_scopes,
            "rf_los": self.btn_rf_los,
            "space_weather": self.btn_space_weather,
        }
        btn = mapping.get(layer_key)
        if btn:
            btn.blockSignals(True)
            btn.setChecked(bool(is_active))
            btn.blockSignals(False)


class NavDockWidget(QWidget):
    """Vertical Discord-style navigation strip (60px wide) with primary views & favorite contacts."""

    view_changed = pyqtSignal(str)          # "main", "floods", "dms", "repeaters"
    settings_requested = pyqtSignal()
    broadcast_advert_requested = pyqtSignal(bool) # flood: True/False
    resync_requested = pyqtSignal()
    node_filter_changed = pyqtSignal(str)   # "ALL", "CLIENTS", "REPEATERS", "ROOMS"
    layer_toggled = pyqtSignal(str, bool)   # (layer_key, is_checked)
    radio_connect_requested = pyqtSignal()  # Trigger radio connect / reconnect
    contact_selected = pyqtSignal(str)      # Direct Message node_id from favorite avatar click

    def __init__(self, config: Optional[AppConfig] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.setFixedWidth(60)
        self.setObjectName("navDock")
        self.active_view = "main"

        # Cached hardware/node info for rich tooltip
        self.radio_connected = False
        self.radio_port = ""
        self.radio_mode = "serial"
        self.companion_alias = "Companion Node"
        self.sync_message = "Synchronized with Device"
        self.sync_stage = "idle"

        # Dedicated MapLayerDockWidget (placed beside the map in MainWindow)
        self.map_layers = MapLayerDockWidget(config=self.config)
        # Expose references for backwards compatibility with tests & external callers
        self.btn_cycle_filter = self.map_layers.btn_cycle_filter
        self.btn_rf_links = self.map_layers.btn_rf_links
        self.btn_byte_paths = self.map_layers.btn_byte_paths
        self.btn_heatmap = self.map_layers.btn_heatmap
        self.btn_orbitals = self.map_layers.btn_orbitals
        self.btn_tropo = self.map_layers.btn_tropo
        self.btn_thunderstorm = self.map_layers.btn_thunderstorm
        self.btn_adsb = self.map_layers.btn_adsb
        self.btn_scopes = self.map_layers.btn_scopes
        self.btn_rf_los = self.map_layers.btn_rf_los
        self.btn_space_weather = self.map_layers.btn_space_weather

        self.map_layers.layer_toggled.connect(self.layer_toggled.emit)
        self.map_layers.node_filter_changed.connect(self.node_filter_changed.emit)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 14)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # 1. Top App Icon (MeshCore Hexagon/Radio)
        self.app_icon_btn = QPushButton(parent=self)
        self.app_icon_btn.setFixedSize(44, 44)
        self.app_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.app_icon_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.app_icon_btn.customContextMenuRequested.connect(self._show_advert_menu)
        self.app_icon_btn.clicked.connect(self.settings_requested.emit)
        self._update_app_icon_style()
        self._update_app_icon_tooltip()
        layout.addWidget(self.app_icon_btn)

        # Thin Discord Divider
        layout.addWidget(self._create_divider())

        # 2. View 1: Main Chat & Map
        self.btn_main = DockButton("main", "Main Chat & Mesh Map (Channels, Chat, Map)", parent=self)
        self.btn_main.clicked.connect(lambda: self.switch_view("main"))
        layout.addWidget(self.btn_main)

        # 3. View 2: Heard RF Floods (Replaces chat with live packet stream)
        self.btn_floods = DockButton("floods", "Heard RF Floods (Replaces chat with live & historical packet streams)", parent=self)
        self.btn_floods.clicked.connect(lambda: self.switch_view("floods"))
        layout.addWidget(self.btn_floods)

        # 4. View 3: Direct Contacts & DMs
        self.btn_dms = DockButton("dms", "Direct Messages & Contacts (Favorites, Companions, DMs)", parent=self)
        self.btn_dms.clicked.connect(lambda: self.switch_view("dms"))
        layout.addWidget(self.btn_dms)

        # 5. View 4: Room Servers (Bulletin Boards, Chatrooms, Authentication)
        self.btn_rooms = DockButton("rooms", "Room Servers (Bulletin Boards, Chatrooms, Authentication)", parent=self)
        self.btn_rooms.clicked.connect(lambda: self.switch_view("rooms"))
        layout.addWidget(self.btn_rooms)

        # 6. View 5: Repeaters & Infrastructure
        self.btn_repeaters = DockButton("repeaters", "Repeaters & Infrastructure (Terminal, Credentials, Neighbors)", parent=self)
        self.btn_repeaters.clicked.connect(lambda: self.switch_view("repeaters"))
        layout.addWidget(self.btn_repeaters)

        # Favorite Contacts Section (at bottom of dock)
        self.fav_divider = self._create_divider()
        self.fav_divider.setVisible(False)
        layout.addWidget(self.fav_divider)

        self.fav_container = QWidget()
        self.fav_layout = QVBoxLayout(self.fav_container)
        self.fav_layout.setContentsMargins(0, 0, 0, 0)
        self.fav_layout.setSpacing(8)
        self.fav_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.fav_container)

        layout.addStretch()

        # Set default active button
        self.btn_main.set_active(True)

    def _is_repeater_contact(self, contact) -> bool:
        """Determines if a contact is a repeater node to select Tactical Radar vs Cyber Droid avatar."""
        alias = (getattr(contact, "alias", "") or "").upper()
        role = (getattr(contact, "role", "") or "").upper()
        type_name = (getattr(contact, "type", "") or "").upper()
        if getattr(contact, "is_repeater", False):
            return True
        if any(kw in role for kw in ("REPEATER", "ROUTER")):
            return True
        if any(kw in type_name for kw in ("REPEATER", "ROUTER")):
            return True
        if any(kw in alias for kw in ("[REP]", "[REPEATER]", "[ROUTER]", "[RTR]", "[GW]", "REPEATER", "ROUTER")):
            return True
        return False

    def update_favorite_contacts(self, favorites: list):
        """Populates the bottom of the nav dock with procedural avatar pills & rich hover tooltips."""
        while self.fav_layout.count():
            item = self.fav_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not favorites:
            self.fav_divider.setVisible(False)
            return

        self.fav_divider.setVisible(True)
        for contact in favorites[:6]:
            alias = getattr(contact, "alias", None) or getattr(contact, "node_id", "Unknown")
            node_id = getattr(contact, "node_id", "")
            is_rep = self._is_repeater_contact(contact)

            # Generate procedural avatar icon (Style B Tactical Radar for repeaters, Style C Cyberpunk Droid for users)
            avatar_icon = get_contact_avatar_icon(node_id, alias, is_repeater=is_rep, size=36)

            clean_alias = alias.strip().lstrip("!").lstrip("#")
            parts = clean_alias.split()
            initials = (parts[0][:1] + parts[1][:1]).upper() if len(parts) >= 2 else clean_alias[:2].upper()

            btn = QPushButton(parent=self)
            btn.setFixedSize(40, 40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("initials", initials)

            if avatar_icon and not avatar_icon.isNull():
                btn.setIcon(avatar_icon)
                btn.setIconSize(QSize(32, 32))
                btn.setText("")
            else:
                btn.setText(initials)

            role_desc = "📡 Repeater Node" if is_rep else "👤 User / Client Node"
            hw_model = getattr(contact, "hw_model", "") or getattr(contact, "hardware", "")
            hw_line = f"\n📟 Hardware: {hw_model}" if hw_model else ""

            # RF / Signal info if available
            snr = getattr(contact, "snr_db", 0.0)
            rssi = getattr(contact, "rssi_dbm", -100.0)
            rf_line = ""
            if snr != 0.0 or rssi != -100.0:
                rf_line = f"\n📶 Signal: {rssi:.0f} dBm (SNR {snr:+.1f} dB)"

            # Hop path if available
            hops = getattr(contact, "out_path_len", -1)
            hops_line = f"\n🔀 Path: {hops} hop{'s' if hops != 1 else ''}" if hops >= 0 else ""

            # Location if available
            lat = getattr(contact, "latitude", None)
            lon = getattr(contact, "longitude", None)
            loc_line = f"\n📍 Location: {lat:.4f}, {lon:.4f}" if (lat is not None and lon is not None) else ""

            last_seen = getattr(contact, "last_seen", None)
            seen_line = f"\n🕒 Last Seen: {last_seen}" if last_seen else ""

            tip = (
                f"👤 {alias}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ Role: {role_desc}\n"
                f"🔑 Node ID: {node_id}{hw_line}{rf_line}{hops_line}{loc_line}{seen_line}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💬 Click to open Direct Message"
            )
            btn.setToolTip(tip)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1A1D24;
                    border: 1.5px solid #2E323B;
                    border-radius: 14px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    border-color: #34D399;
                    background-color: #242832;
                    border-radius: 12px;
                }
            """)
            btn.clicked.connect(lambda _, nid=node_id: self.contact_selected.emit(nid))
            self.fav_layout.addWidget(btn)

    def _create_divider(self) -> QFrame:
        line = QFrame()
        line.setFixedSize(28, 2)
        line.setStyleSheet("background-color: #35363C; border-radius: 1px;")
        return line

    def _update_app_icon_style(self):
        active = self.radio_connected
        icon = create_nav_icon("app_icon", active=active)
        if icon:
            self.app_icon_btn.setIcon(icon)
            self.app_icon_btn.setIconSize(QSize(24, 24))
            self.app_icon_btn.setText("")
        else:
            self.app_icon_btn.setText("⚡")

        border_col = "#10B981" if active else "#ED4245"
        bg_stop0 = "#064E3B" if active else "#2B303C"
        bg_stop1 = "#022C22" if active else "#1C1C1E"
        self.app_icon_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {bg_stop0}, stop:1 {bg_stop1});
                color: #FFFFFF;
                border: 1.5px solid {border_col};
                border-radius: 14px;
                padding: 0px;
            }}
            QPushButton:hover {{
                border-color: #34D399;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #047857, stop:1 #064E3B);
            }}
        """)

    def _update_app_icon_tooltip(self):
        stat_emoji = "🟢" if self.radio_connected else "🔴"
        stat_text = "Radio Connected" if self.radio_connected else "Radio Disconnected"
        port_text = f"{self.radio_port} ({self.radio_mode})" if self.radio_port else "None"
        action_hint = "Right-click to Reconnect / Actions" if self.radio_connected else "Right-click to Connect"
        tip = (
            f"⚡ MeshCore\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{stat_emoji} Status: {stat_text}\n"
            f"📡 Companion: {self.companion_alias}\n"
            f"🔌 Port: {port_text}\n"
            f"🔄 Sync: {self.sync_message}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️ Click to Open Settings\n"
            f"🖱️ {action_hint}"
        )
        self.app_icon_btn.setToolTip(tip)

    def update_connection_status(self, connected: bool, port: str = "", mode: str = "serial"):
        self.radio_connected = connected
        self.radio_port = port
        self.radio_mode = mode
        self._update_app_icon_style()
        self._update_app_icon_tooltip()

    def update_companion_alias(self, alias: str):
        self.companion_alias = alias
        self._update_app_icon_tooltip()

    def update_sync_status(self, stage: str, message: str, is_synced: bool):
        self.sync_stage = stage
        self.sync_message = message
        self._update_app_icon_tooltip()

    def switch_view(self, view_name: str):
        if view_name == self.active_view:
            return
        self.active_view = view_name
        self.btn_main.set_active(view_name == "main")
        if hasattr(self, "btn_floods"):
            self.btn_floods.set_active(view_name == "floods")
        self.btn_dms.set_active(view_name == "dms")
        if hasattr(self, "btn_rooms"):
            self.btn_rooms.set_active(view_name == "rooms")
        self.btn_repeaters.set_active(view_name == "repeaters")
        self.view_changed.emit(view_name)

    def set_layer_active(self, layer_key: str, is_active: bool):
        """Programmatically syncs dock layer toggle button states without re-emitting signals."""
        self.map_layers.set_layer_active(layer_key, is_active)

    def _show_advert_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #222327;
                color: #E5E7EB;
                border: 1px solid #414143;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #2B303C;
                color: #38BDF8;
            }
            QMenu::item:disabled {
                color: #6B7280;
            }
        """)

        # Radio Status header
        status_icon = "🟢" if self.radio_connected else "🔴"
        status_text = "Radio: Connected" if self.radio_connected else "Radio: Disconnected"
        port_info = f" ({self.radio_port})" if self.radio_port else ""
        act_status = menu.addAction(f"{status_icon} {status_text}{port_info}")
        act_status.setEnabled(False)

        menu.addSeparator()

        if not self.radio_connected:
            act_connect = menu.addAction("🔌 Connect to Radio")
        else:
            act_connect = menu.addAction("🔄 Reconnect Radio")

        menu.addSeparator()
        act_zero = menu.addAction("📡 Broadcast Node (Advert Zero Hop)")
        act_flood = menu.addAction("🌊 Broadcast Node (Advert Flood Routed)")
        act_resync = menu.addAction("🔄 Re-sync Hardware State")
        menu.addSeparator()
        act_settings = menu.addAction("⚙️ Open Settings...")

        action = menu.exec(self.app_icon_btn.mapToGlobal(QPoint(self.app_icon_btn.width() + 4, 0)))
        if action == act_connect:
            self.radio_connect_requested.emit()
        elif action == act_zero:
            self.broadcast_advert_requested.emit(False)
        elif action == act_flood:
            self.broadcast_advert_requested.emit(True)
        elif action == act_resync:
            self.resync_requested.emit()
        elif action == act_settings:
            self.settings_requested.emit()
