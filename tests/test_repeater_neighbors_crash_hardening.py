import os
import json
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from meshcore_tray.config import AppConfig
from meshcore_tray.core.models import NodeContact
from meshcore_tray.storage import Storage
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, get_leaflet_html


def test_draw_repeater_neighbors_js_template():
    """Verifies that the generated Leaflet HTML includes safe bounds logic and escaping."""
    html = get_leaflet_html()
    assert "function drawRepeaterNeighbors(data)" in html
    assert "applyBounds" in html
    assert "isFinite" in html
    assert "escapeHtml(n.snr_str)" in html
    assert "escapeHtml(n.time_str)" in html
    assert "escapeHtml(rep ? (rep.alias || rep.id) : 'Repeater')" in html


def test_repeater_neighbors_dict_and_single_point():
    """Verifies display_repeater_neighbors handles dict input and single host coordinate without errors."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            map_widget = MeshMapWidget(config=config, storage=storage)

        mock_web_view = MagicMock()
        mock_js_calls = []
        mock_web_view.page().runJavaScript = lambda js: mock_js_calls.append(js)
        map_widget.web_view = mock_web_view
        map_widget._page_ready = True

        # 1. Repeater passed as dict (e.g. from telemetry or network packet)
        rep_dict = {
            "node_id": "DD2DF7A117C6",
            "alias": 'Repeater "North" <Beta>',
            "latitude": 54.6588,
            "longitude": -3.4344
        }
        neighbors_data = [
            {"node_id": "unknown_01", "snr_str": "+3.0 dB", "time_str": "< 1m ago"},
            {"node_id": "unknown_02", "snr": -12.5, "time_ago": "< 30s ago"}
        ]

        map_widget.display_repeater_neighbors(rep_dict, neighbors_data)

        assert len(mock_js_calls) > 0
        last_js = mock_js_calls[-1]
        assert "drawRepeaterNeighbors" in last_js
        assert "DD2DF7A117C6" in last_js

        # Parse the JSON payload passed to drawRepeaterNeighbors
        js_arg = last_js[len("drawRepeaterNeighbors("):-2]
        payload = json.loads(js_arg)
        assert payload["repeater"]["coord"] == [54.6588, -3.4344]
        assert payload["repeater"]["alias"] == 'Repeater "North" <Beta>'
        assert len(payload["neighbors"]) == 2
        assert payload["neighbors"][0]["time_str"] == "< 1m ago"
        assert payload["neighbors"][1]["snr_str"] == "-12.5 dB"

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_repeater_neighbors_pending_payload_flushed():
    """Verifies that if neighbors arrive before map is ready, payload is safely queued and flushed on load."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
            map_widget = MeshMapWidget(config=config, storage=storage)

        map_widget._page_ready = False
        mock_web_view = MagicMock()
        mock_js_calls = []
        mock_web_view.page().runJavaScript = lambda js: mock_js_calls.append(js)
        map_widget.web_view = mock_web_view

        rep = NodeContact(node_id="rep_test", alias="Test Rep", latitude=55.0, longitude=-3.0, is_repeater=True)
        map_widget.display_repeater_neighbors(rep, [{"node_id": "n1", "snr": 5.0}])

        # Should be queued in _pending_neighbors_payload, not executed yet
        assert len(mock_js_calls) == 0
        assert map_widget._pending_neighbors_payload is not None

        # Trigger _on_map_loaded
        map_widget._on_map_loaded(True)
        assert map_widget._page_ready is True
        assert map_widget._pending_neighbors_payload is None
        assert len(mock_js_calls) > 0
        assert any("drawRepeaterNeighbors" in c for c in mock_js_calls)

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_watchdog_hidden_tab_guard():
    """Verifies that the watchdog does not accumulate timeouts when map is hidden (e.g. in repeaters tab)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        storage = Storage(db_path=db_path)
        config = AppConfig()

        with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", True):
            map_widget = MeshMapWidget(config=config, storage=storage)

        map_widget._page_ready = True
        mock_web_view = MagicMock()
        map_widget.web_view = mock_web_view

        # Hide widget
        map_widget.hide()
        assert map_widget.isVisible() is False

        # Call watchdog check multiple times
        map_widget._watchdog_unanswered = 4
        map_widget._check_renderer_watchdog()

        # Unanswered counter should have been reset because widget is hidden
        assert map_widget._watchdog_unanswered == 0

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
