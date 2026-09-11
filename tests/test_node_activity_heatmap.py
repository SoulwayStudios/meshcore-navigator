"""Tests for Node Activity Heatmap (1h / 6h / 24h traffic volume) and Repeater Heatmap Coloring."""

import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from meshcore_tray.config import AppConfig
from meshcore_tray.storage import Storage
from meshcore_tray.core.models import MessageEnvelope, PacketPathInfo, NodeContact
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, get_leaflet_html
from meshcore_tray.ui.nav_dock import NavDockWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_meshcore_heatmap.db"
    storage = Storage(str(db_file))
    return storage


def test_storage_activity_counts_timeframes(temp_storage):
    """Verifies storage.get_node_activity_counts filters messages and packet paths by 1h, 6h, and 24h."""
    now = datetime.now(timezone.utc)

    # 1. Add contact
    temp_storage.save_contact(NodeContact(
        node_id="!node_alpha",
        alias="AlphaRepeater",
        is_repeater=True,
        latitude=51.5,
        longitude=-0.12
    ))
    temp_storage.save_contact(NodeContact(
        node_id="!node_beta",
        alias="BetaRepeater",
        is_repeater=True,
        latitude=51.6,
        longitude=-0.15
    ))

    # 2. Message 30 mins ago: sender=!node_alpha, hops=["BetaRepeater"]
    m1 = MessageEnvelope(
        id="msg-1",
        source_driver="meshcore",
        sender_id="!node_alpha",
        sender_name="AlphaRepeater",
        channel="Public",
        text="Hello 30m ago",
        timestamp=(now - timedelta(minutes=30)).isoformat(),
        metadata={"hops": ["BetaRepeater"]}
    )
    temp_storage.save_message(m1)

    # 3. Message 3 hours ago: sender=!node_alpha
    m2 = MessageEnvelope(
        id="msg-2",
        source_driver="meshcore",
        sender_id="!node_alpha",
        sender_name="AlphaRepeater",
        channel="Public",
        text="Hello 3h ago",
        timestamp=(now - timedelta(hours=3)).isoformat(),
        metadata={}
    )
    temp_storage.save_message(m2)

    # 4. Message 12 hours ago: sender=!node_beta
    m3 = MessageEnvelope(
        id="msg-3",
        source_driver="meshcore",
        sender_id="!node_beta",
        sender_name="BetaRepeater",
        channel="Public",
        text="Hello 12h ago",
        timestamp=(now - timedelta(hours=12)).isoformat(),
        metadata={}
    )
    temp_storage.save_message(m3)

    # 5. Message 36 hours ago (outside 24h): sender=!node_alpha
    m4 = MessageEnvelope(
        id="msg-4",
        source_driver="meshcore",
        sender_id="!node_alpha",
        sender_name="AlphaRepeater",
        channel="Public",
        text="Hello 36h ago",
        timestamp=(now - timedelta(hours=36)).isoformat(),
        metadata={}
    )
    temp_storage.save_message(m4)

    # 6. Packet Path 45 mins ago: sender=!node_beta, hop_nodes=["AlphaRepeater"]
    p1 = PacketPathInfo(
        packet_id="hash-1",
        sender_id="!node_beta",
        sender_name="BetaRepeater",
        route_type="FLOOD",
        hop_nodes=["AlphaRepeater"],
        coordinates=[[51.6, -0.15], [51.5, -0.12]],
        timestamp=(now - timedelta(minutes=45)).isoformat()
    )
    temp_storage.save_packet_path(p1)

    # Test 1 Hour Timeframe:
    # Within 1h: m1 (sender AlphaRepeater, hop BetaRepeater) and p1 (sender BetaRepeater, hop AlphaRepeater)
    # Both nodes should have 2 counts
    counts_1h = temp_storage.get_node_activity_counts(1)
    assert counts_1h.get("!node_alpha", 0) == 2
    assert counts_1h.get("AlphaRepeater", 0) == 2
    assert counts_1h.get("!node_beta", 0) == 2
    assert counts_1h.get("BetaRepeater", 0) == 2

    # Test 6 Hours Timeframe:
    # Includes m1, m2 (Alpha +1), p1
    # Alpha = 2 (from 1h) + 1 (from m2) = 3
    # Beta = 2
    counts_6h = temp_storage.get_node_activity_counts(6)
    assert counts_6h.get("!node_alpha", 0) == 3
    assert counts_6h.get("AlphaRepeater", 0) == 3
    assert counts_6h.get("!node_beta", 0) == 2
    assert counts_6h.get("BetaRepeater", 0) == 2

    # Test 24 Hours Timeframe:
    # Includes m1, m2, m3 (Beta +1), p1 (m4 excluded because 36h > 24h)
    # Alpha = 3
    # Beta = 3
    counts_24h = temp_storage.get_node_activity_counts(24)
    assert counts_24h.get("!node_alpha", 0) == 3
    assert counts_24h.get("!node_beta", 0) == 3


def test_leaflet_html_contains_heatmap_overlay():
    """Verifies that the generated Leaflet HTML includes the heatmap UI bar, buttons, and styles."""
    html = get_leaflet_html()
    assert 'id="activity-heatmap-bar"' in html
    assert 'id="act-btn-1h"' in html
    assert 'id="act-btn-6h"' in html
    assert 'id="act-btn-24h"' in html
    assert '1 hour' in html
    assert '6 hours' in html
    assert '24 hours' in html
    assert 'setActivityHeatmap' in html
    assert 'setActivityTimeframe' in html
    assert 'applyNodeMarkerStyling' in html
    # Color specifications
    assert '#10B981' in html  # Low (Green)
    assert '#FACC15' in html  # Medium (Yellow)
    assert '#FB923C' in html  # High (Orange)
    assert '#EF4444' in html  # Very High (Red)


def test_mesh_map_activity_heatmap_toggle(qapp, temp_storage):
    """Verifies MeshMapWidget.set_activity_heatmap updates state, watcher status, and syncs buttons."""
    from unittest.mock import patch
    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        map_widget = MeshMapWidget(storage=temp_storage, config=config)

    assert not map_widget.activity_heatmap_active

    # Enable heatmap
    map_widget.set_activity_heatmap(True, timeframe_hours=6)
    assert map_widget.activity_heatmap_active is True
    assert map_widget.activity_timeframe_hours == 6
    assert "Activity Heatmap" in map_widget.watcher_status.text()
    assert "6h" in map_widget.watcher_status.text()

    # Change timeframe
    map_widget.set_activity_heatmap(True, timeframe_hours=24)
    assert map_widget.activity_timeframe_hours == 24
    assert "24h" in map_widget.watcher_status.text()

    # Disable heatmap
    map_widget.set_activity_heatmap(False)
    assert map_widget.activity_heatmap_active is False
    assert "Watcher" in map_widget.watcher_status.text()


def test_nav_dock_heatmap_button(qapp):
    """Verifies NavDockWidget has the heatmap layer toggle button and emits the correct signal."""
    dock = NavDockWidget()
    assert hasattr(dock, "btn_heatmap")
    assert dock.btn_heatmap.toolTip() == "Node Activity Heatmap"

    emitted_layers = []
    dock.layer_toggled.connect(lambda key, val: emitted_layers.append((key, val)))

    dock.btn_heatmap.setChecked(True)
    assert ("activity_heatmap", True) in emitted_layers

    dock.btn_heatmap.setChecked(False)
    assert ("activity_heatmap", False) in emitted_layers

    # Test programmatic set_layer_active
    dock.set_layer_active("activity_heatmap", True)
    assert dock.btn_heatmap.isChecked() is True
    # Should not re-emit when programmatic
    assert len(emitted_layers) == 2
