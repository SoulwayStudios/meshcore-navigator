"""Unit and integration tests for Unified Floating Overlays, Search Node IDs, and Relative Heatmap."""

import json
import pytest
from PyQt6.QtCore import Qt, QPoint, QPointF
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from meshcore_tray.config import AppConfig
from meshcore_tray.ui.mesh_map_widget import (
    MeshMapWidget,
    DraggableOverlayFrame,
    LEAFLET_HTML_TEMPLATE,
    get_leaflet_html,
)
from meshcore_tray.ui.nav_dock import NavDockWidget


class TestUnifiedFloatingOverlaysAndSearchNodeIds:
    @pytest.fixture(autouse=True)
    def setup_app(self, qapp):
        self.qapp = qapp

    def test_search_node_id_nav_dock_button_and_icon(self):
        dock = NavDockWidget()
        assert hasattr(dock.map_layers, "btn_search_node_id")
        assert dock.map_layers.btn_search_node_id.raw_text == "search_node_id"
        assert dock.map_layers.btn_search_node_id.icon_name == "search_node_id"
        assert not dock.map_layers.btn_search_node_id.isChecked()

        dock.map_layers.set_layer_active("search_node_id", True)
        assert dock.map_layers.btn_search_node_id.isChecked()

        dock.map_layers.set_layer_active("search_node_id", False)
        assert not dock.map_layers.btn_search_node_id.isChecked()

    def test_mesh_map_search_node_id_toggle_and_signal(self):
        widget = MeshMapWidget(storage=None, config=AppConfig())
        assert hasattr(widget, "show_search_node_id")
        assert widget.show_search_node_id is False

        emitted = []
        widget.search_node_id_toggled.connect(lambda val: emitted.append(val))

        widget.set_search_node_id(True)
        assert widget.show_search_node_id is True
        assert emitted == [True]
        assert "Search Node IDs" in widget.watcher_status.text()

        widget.set_search_node_id(False)
        assert widget.show_search_node_id is False
        assert emitted == [True, False]

    def test_los_controls_draggable_overlay_frame_and_safe_zone(self):
        widget = MeshMapWidget(storage=None, config=AppConfig())
        assert hasattr(widget, "los_controls")
        assert isinstance(widget.los_controls, DraggableOverlayFrame)
        assert hasattr(widget, "btn_close_los")
        assert hasattr(widget, "btn_toggle_los")
        assert hasattr(widget, "btn_los_ground")
        assert hasattr(widget, "btn_los_rooftop")
        assert hasattr(widget, "btn_los_mast")
        assert hasattr(widget, "combo_los_radius")
        assert hasattr(widget, "btn_profile_path")
        assert hasattr(widget, "btn_clear_los")

        # Test initial positioning with safe zone
        widget.resize(1000, 600)
        widget.show()
        self.qapp.processEvents()

        widget._reposition_floating_controls()
        pos_x = widget.los_controls.x()
        assert pos_x >= 0 and widget.los_controls.y() >= 0

        # Test user dragging sets _user_moved flag
        widget.los_controls.move(500, 200)
        widget.los_controls._user_moved = True

        # Calling _reposition_floating_controls must not reset position
        widget._reposition_floating_controls()
        assert widget.los_controls.x() == 500
        assert widget.los_controls.y() == 200

        # Test close button
        widget.set_los_view_active(True)
        assert not widget.los_controls.isHidden()
        widget.btn_close_los.click()
        assert widget.los_controls.isHidden()

    def test_draggable_overlay_frame_top_left_unconstrained(self):
        parent_frame = DraggableOverlayFrame()
        parent_frame.resize(800, 600)
        child_overlay = DraggableOverlayFrame(parent=parent_frame)
        child_overlay.resize(200, 150)
        child_overlay.move(450, 100)

        # Simulate left mouse press on overlay frame
        press_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(460, 110),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        child_overlay.mousePressEvent(press_event)
        assert child_overlay._dragging is True

        # Simulate drag into top-left region (e.g. 50, 10)
        move_event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(10, 10),
            QPointF(60, 20),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        child_overlay.mouseMoveEvent(move_event)
        # Y position must NOT be clamped to 58px anymore since ghost controls are removed
        assert child_overlay.x() == 50
        assert child_overlay.y() == 10
        assert child_overlay._user_moved is True

        # Release mouse
        release_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(10, 10),
            QPointF(60, 20),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        child_overlay.mouseReleaseEvent(release_event)
        assert child_overlay._dragging is False

    def test_lightning_proximity_alert_signal_and_bridge(self):
        widget = MeshMapWidget(storage=None, config=AppConfig())
        alerts = []
        widget.lightning_proximity_alert.connect(lambda dist, brg: alerts.append((dist, brg)))

        widget.bridge.lightning_proximity_alert_signal.emit(12.4, 185)
        assert len(alerts) == 1
        assert alerts[0] == (12.4, 185)
        assert "LIGHTNING ALERT" in widget.watcher_status.text()
        assert "12.4 mi" in widget.watcher_status.text()
        assert "185°" in widget.watcher_status.text()

    def test_clipboard_copy_bridge_signal(self):
        widget = MeshMapWidget(storage=None, config=AppConfig())
        copied = []
        widget.bridge.copy_clipboard_signal.connect(lambda text: copied.append(text))

        widget.bridge.on_copy_clipboard("!e97eb412")
        assert "!e97eb412" in copied

    def test_html_template_contains_all_unified_features(self):
        html = get_leaflet_html()

        # 1. Dark custom webkit scrollbars
        assert "::-webkit-scrollbar" in html
        assert "::-webkit-scrollbar-track" in html
        assert "::-webkit-scrollbar-thumb" in html
        assert "#1E1F22" in html
        assert "#383A40" in html

        # 2. Search Node IDs View
        assert 'id="search-node-id-panel"' in html
        assert 'id="search-node-input"' in html
        assert "searchNodeIds" in html
        assert "setSearchNodeIdVisible" in html
        assert "closeSearchNodeIdPanel" in html

        # 3. Dynamic Node Activity Relative % and Scaling
        assert 'id="activity-heatmap-bar"' in html
        assert 'id="act-chk-relative"' in html
        assert 'id="act-chk-scaling"' in html
        assert "toggleActivityRelative" in html
        assert "toggleActivityScaling" in html
        assert "act-lbl-vhigh" in html
        assert "act-lbl-low" in html
        assert "window._actRelative" in html
        assert "window._actScaling" in html

        # 4. Thunderstorms steppers & proximity
        assert 'id="thunderstorm-panel"' in html
        assert "stepThunderstormRadar" in html
        assert 'id="thunder-proximity-badge"' in html
        assert 'id="thunder-nearest-val"' in html
        assert "jumpToNearestStrike" in html

        # 5. Scopes prune & highlight
        assert 'id="scope-filter-bar"' in html
        assert "toggleScopePrune" in html
        assert "toggleScopeHighlight" in html

        # 6. Repeater popup copy button & monospace ID
        assert "copyNodeIdClipboard" in html
        assert "node-popup-id-row" in html
        assert "(Repeater)" in html

        # 7. Safe zone drag logic (formerly restricted top-left; removed after moving action controls)
        assert "newLeft < 415 && newTop < 55" not in html
        assert "newTop = 58" not in html

    def test_search_node_id_strict_prefix_matching(self):
        """Verifies that searchNodeIds enforces strict prefix matching and rejects substring matches in the middle."""
        html = get_leaflet_html()

        # Strict prefix check: (nid.indexOf(cleanQuery) === 0)
        assert "(nid.indexOf(cleanQuery) === 0)" in html
        assert "isPrefix" in html
        # Must not match middle/end substrings in searchNodeIds
        assert "nid.indexOf(cleanQuery) !== -1" not in html
