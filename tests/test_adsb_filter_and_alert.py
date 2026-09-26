import json
import pytest
from unittest.mock import MagicMock
from meshcore_tray.config import AppConfig, MeshcoreConfig
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, WebBridge, get_leaflet_html


def test_adsb_config_defaults():
    """Verifies that MeshcoreConfig contains default ADS-B filter and alert preferences."""
    config = MeshcoreConfig()
    assert hasattr(config, "adsb_filter_categories")
    assert "military" in config.adsb_filter_categories
    assert "airliner" in config.adsb_filter_categories
    assert config.adsb_alert_enabled is True
    assert config.adsb_alert_categories == ["military"]
    assert config.adsb_alert_radius_mi == 10.0


def test_leaflet_html_contains_adsb_filter_and_alert_ui():
    """Verifies that the generated Leaflet HTML contains the filter checkboxes and proximity alert UI."""
    html = get_leaflet_html()
    # Display filter card & checkboxes
    assert "adsb-flt-airliner" in html
    assert "adsb-flt-light" in html
    assert "adsb-flt-military" in html
    assert "adsb-flt-helicopter" in html
    assert "adsb-flt-glider" in html
    assert "adsb-flt-general" in html
    assert "updateAdsbFilters()" in html
    assert "setAllAdsbFilters(" in html

    # Proximity alert card & checkboxes
    assert "adsb-alert-card" in html
    assert "adsb-alert-enabled" in html
    assert "adsb-alert-military" in html
    assert "updateAdsbAlertConfig()" in html
    assert "PROXIMITY ALERT" in html


def test_web_bridge_proximity_alert_and_filters_signals():
    """Verifies that WebBridge dispatches proximity alert and filter change signals."""
    bridge = WebBridge()

    alert_results = []
    bridge.adsb_proximity_alert_signal.connect(lambda hex_c, flt, dist, cat: alert_results.append((hex_c, flt, dist, cat)))

    bridge.on_adsb_proximity_alert("43c123", "RAF-TYPHOON", 4.5, "military")
    assert len(alert_results) == 1
    assert alert_results[0] == ("43c123", "RAF-TYPHOON", 4.5, "military")

    filter_results = []
    bridge.adsb_filters_changed_signal.connect(lambda data: filter_results.append(data))
    bridge.on_adsb_filters_changed('{"military": true, "airliner": false}')
    assert len(filter_results) == 1
    assert '"airliner": false' in filter_results[0]


def test_mesh_map_persists_filter_and_alert_settings():
    """Verifies that MeshMapWidget saves filter and proximity alert preferences to config."""
    config = AppConfig()
    widget = MeshMapWidget(config=config)

    # Filter changed
    filters = json.dumps({"airliner": False, "military": True, "light": True, "helicopter": False})
    widget._on_bridge_adsb_filters_changed(filters)
    assert "airliner" not in config.meshcore.adsb_filter_categories
    assert "military" in config.meshcore.adsb_filter_categories

    # Alert config changed
    alert_cfg = json.dumps({
        "enabled": True,
        "radiusMi": 10.0,
        "categories": {"military": True, "helicopter": True, "airliner": False}
    })
    widget._on_bridge_adsb_alert_config_changed(alert_cfg)
    assert config.meshcore.adsb_alert_enabled is True
    assert "military" in config.meshcore.adsb_alert_categories
    assert "helicopter" in config.meshcore.adsb_alert_categories
    assert "airliner" not in config.meshcore.adsb_alert_categories
    assert config.meshcore.adsb_alert_radius_mi == 10.0
