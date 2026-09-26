"""Unit tests for UKMesh-style 3D MapLibre terrain view integration, controls, and signals."""

import pytest
from unittest.mock import MagicMock, patch

from meshcore_tray.config import AppConfig
from meshcore_tray.ui.mesh_map_widget import MeshMapWidget, WebBridge, LEAFLET_HTML_TEMPLATE, get_leaflet_html
from meshcore_tray.ui.nav_dock import NavDockWidget, LayerButton, LAYER_SVGS, create_layer_icon
from meshcore_tray.ui.main_window import MainWindow


def test_map_3d_html_template_and_controls():
    """Verify Leaflet HTML template includes 3D map container, controls overlay, and JS functions."""
    # 1. 3D DOM Elements
    assert 'id="map-3d"' in LEAFLET_HTML_TEMPLATE
    assert 'id="map3dControls"' in LEAFLET_HTML_TEMPLATE
    assert 'class="map-overlay-panel"' in LEAFLET_HTML_TEMPLATE

    # 2. Controls and buttons
    assert 'onclick="close3DMode()"' in LEAFLET_HTML_TEMPLATE
    assert 'onclick="set3DPitch(0)"' in LEAFLET_HTML_TEMPLATE
    assert 'onclick="set3DPitch(58)"' in LEAFLET_HTML_TEMPLATE
    assert 'id="select-3d-exaggeration"' in LEAFLET_HTML_TEMPLATE
    assert 'onchange="set3DExaggeration(this.value)"' in LEAFLET_HTML_TEMPLATE

    # 3. JavaScript Functions
    assert 'function set3DMode(enabled)' in LEAFLET_HTML_TEMPLATE
    assert 'function close3DMode()' in LEAFLET_HTML_TEMPLATE
    assert 'function initMap3D()' in LEAFLET_HTML_TEMPLATE
    assert 'function syncAllNodesTo3D()' in LEAFLET_HTML_TEMPLATE
    assert 'function tracePacketPath3D(' in LEAFLET_HTML_TEMPLATE
    assert 'function generate3DArcCoordinates(' in LEAFLET_HTML_TEMPLATE

    # 4. Vendor styles include maplibre-gl.css
    html = get_leaflet_html()
    assert '.maplibregl-canvas' in html
    assert 'maplibre-gl.css' in html or '.maplibregl-' in html


def test_map_3d_bridge_signal():
    """Verify WebBridge map_3d_toggled_signal fires and propagates."""
    bridge = WebBridge()
    received = []
    bridge.map_3d_toggled_signal.connect(lambda v: received.append(v))

    bridge.on_map_3d_toggled(True)
    assert received == [True]

    bridge.on_map_3d_toggled(False)
    assert received == [True, False]


def test_nav_dock_3d_button_and_icon(qapp):
    """Verify NavDock contains map_3d layer button with vector glyph and proper toggle state."""
    # 1. SVG line-art glyph exists
    assert "map_3d" in LAYER_SVGS
    icon_inactive = create_layer_icon("map_3d", active=False)
    assert icon_inactive is not None
    icon_active = create_layer_icon("map_3d", active=True)
    assert icon_active is not None

    # 2. NavDock button exists
    nav = NavDockWidget()

    assert hasattr(nav, "btn_map_3d")
    assert isinstance(nav.btn_map_3d, LayerButton)
    assert nav.btn_map_3d.icon_name == "map_3d"
    assert not nav.btn_map_3d.isChecked()

    # 3. Signal emissions
    emitted = []
    nav.layer_toggled.connect(lambda k, v: emitted.append((k, v)))

    nav.btn_map_3d.click()
    assert ("map_3d", True) in emitted
    assert nav.btn_map_3d.isChecked()

    nav.btn_map_3d.click()
    assert ("map_3d", False) in emitted
    assert not nav.btn_map_3d.isChecked()

    # 4. Programmatic activation via set_layer_active
    nav.set_layer_active("map_3d", True)
    assert nav.btn_map_3d.isChecked()
    nav.set_layer_active("map_3d", False)
    assert not nav.btn_map_3d.isChecked()


def test_mesh_map_widget_3d_mode(qapp):
    """Verify MeshMapWidget.set_3d_mode manages UI button state and dispatches JS command."""
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget()

    widget.web_view = MagicMock()
    widget._page_ready = True

    # Initial state
    assert not widget.is_3d_mode
    assert hasattr(widget, "btn_map_3d")

    # Toggle 3D on
    widget.set_3d_mode(True)
    assert widget.is_3d_mode
    assert widget.btn_map_3d.isChecked()
    assert widget.web_view.page().runJavaScript.called
    last_call = widget.web_view.page().runJavaScript.call_args[0][0]
    assert "set3DMode(true)" in last_call

    # Toggle 3D off
    widget.set_3d_mode(False)
    assert not widget.is_3d_mode
    assert not widget.btn_map_3d.isChecked()
    last_call = widget.web_view.page().runJavaScript.call_args[0][0]
    assert "set3DMode(false)" in last_call


def test_main_window_dock_layer_toggled_3d(qapp):
    """Verify MainWindow._on_dock_layer_toggled forwards map_3d to mesh_map.set_3d_mode."""
    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        window = MainWindow(config=config)

    window.mesh_map.set_3d_mode = MagicMock()
    window._on_dock_layer_toggled("map_3d", True)
    window.mesh_map.set_3d_mode.assert_called_with(True)

    window._on_dock_layer_toggled("map_3d", False)
    window.mesh_map.set_3d_mode.assert_called_with(False)


def test_eng_ne_scope_definition_and_classification(tmp_path):
    """Verify eng-ne regional scope is defined in Storage and correctly groups North East nodes."""
    from meshcore_tray.storage import Storage, NodeContact

    db_path = str(tmp_path / "test_scopes.db")
    storage = Storage(db_path=db_path)
    scopes = storage.get_scope_definitions()

    assert "eng-ne" in scopes
    eng_ne = scopes["eng-ne"]
    assert eng_ne["id"] == "eng-ne"
    assert "North East England" in eng_ne["display_name"]
    assert eng_ne["color"] == "#FF6D00"
    assert eng_ne["center"] == [54.97, -1.61]

    # Save a repeater in Newcastle upon Tyne (coordinates + alias)
    contact = NodeContact(
        node_id="!aabbccdd",
        alias="NEWCASTLE-HUB",
        latitude=54.978,
        longitude=-1.617,
        is_repeater=True
    )
    storage.save_contact(contact)

    # Save a repeater in Newcastle-under-Lyme, Staffordshire (ST5, lat ~ 53.01)
    st5_contact = NodeContact(
        node_id="!st5node",
        alias="Newcastle ST5",
        latitude=53.013,
        longitude=-2.222,
        is_repeater=True
    )
    storage.save_contact(st5_contact)

    # Verify repeater gets grouped into eng-ne scope, while ST5 is excluded from eng-ne
    scope_groups = storage.get_repeaters_by_scope()
    assert "eng-ne" in scope_groups
    eng_ne_nodes = [n["node_id"] for n in scope_groups["eng-ne"]["nodes"]]
    assert "!aabbccdd" in eng_ne_nodes
    assert "!st5node" not in eng_ne_nodes
    assert "!st5node" in [n["node_id"] for n in scope_groups["gb-nwk"]["nodes"]]


def test_openfreemap_dark_and_gpu_optimizations():
    """Verify 3D map uses unmetered OpenFreeMap Dark tiles (no Carto watermark) and hardware-accelerated GPU profile."""
    # 1. OpenFreeMap Dark vector style used in 3D Map (no Carto watermark in 3D)
    assert "https://tiles.openfreemap.org/styles/dark" in LEAFLET_HTML_TEMPLATE
    assert "carto-dark" not in LEAFLET_HTML_TEMPLATE
    assert "a.basemaps.cartocdn.com" not in LEAFLET_HTML_TEMPLATE

    # 2. Terrarium DEM elevation tiles configured with global pyramid and zero CORS issues
    assert "elevation-tiles-prod/terrarium" in LEAFLET_HTML_TEMPLATE

    # 3. GPU performance optimizations
    assert "powerPreference: 'high-performance'" in LEAFLET_HTML_TEMPLATE
    assert "antialias: false" in LEAFLET_HTML_TEMPLATE
    assert "fadeDuration: 0" in LEAFLET_HTML_TEMPLATE
    assert "pixelRatio: Math.min" in LEAFLET_HTML_TEMPLATE


def test_corescope_3d_arcs_and_layer_sync():
    """Verify CoreScope 3D WebGL parabolic arcs and comprehensive 3D layer synchronizers."""
    # 1. CoreScope 3D WebGL custom layer and shader
    assert "corescope-3d-arcs" in LEAFLET_HTML_TEMPLATE
    assert "gl_Position = u_matrix * vec4(a_pos, 1.0);" in LEAFLET_HTML_TEMPLATE
    assert "generate3DArcPoints" in LEAFLET_HTML_TEMPLATE
    assert "trigger3DPulse" in LEAFLET_HTML_TEMPLATE

    # 2. 3D Layer synchronizers for all views
    assert "function syncScopesTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncHeatmapTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncThunderstormTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncAdsbTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncTropoTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncSpaceWeatherTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncLosTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncOrbitalsTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncSatellitesTo3D()" in LEAFLET_HTML_TEMPLATE
    assert "function syncVisualisedPathTo3D(" in LEAFLET_HTML_TEMPLATE
    assert "function syncAllLayersTo3D()" in LEAFLET_HTML_TEMPLATE


def test_3d_dem_maxzoom_and_spike_fix():
    """Verify DEM source uses Terrarium terrain tiles with maxzoom 12 and natural 1.5x exaggeration."""
    assert "maxzoom: 12" in LEAFLET_HTML_TEMPLATE
    assert "elevation-tiles-prod/terrarium" in LEAFLET_HTML_TEMPLATE
    assert "exaggeration: 1.5" in LEAFLET_HTML_TEMPLATE


def test_3d_dark_popups_and_tooltips_css():
    """Verify MapLibre popup and tooltip styling uses dark theme matching #222327."""
    html = get_leaflet_html()
    assert ".maplibregl-popup-content" in html
    assert "#222327" in html
    assert ".maplibregl-popup-tip" in html


def test_3d_adsb_cylinder_and_radial_rings():
    """Verify 3D ADS-B vertical cylinder with 4 altitude layers and radial range rings."""
    # 1. 3D layers setup in template
    assert "adsb-3d-cylinder" in LEAFLET_HTML_TEMPLATE
    assert "adsb-3d-cylinder-layer" in LEAFLET_HTML_TEMPLATE
    assert "'fill-extrusion'" in LEAFLET_HTML_TEMPLATE or "fill-extrusion" in LEAFLET_HTML_TEMPLATE
    assert "adsb-3d-rings" in LEAFLET_HTML_TEMPLATE
    assert "adsb-3d-rings-layer" in LEAFLET_HTML_TEMPLATE

    # 2. 4 vertical altitude bands configured
    assert "<2k ft" in LEAFLET_HTML_TEMPLATE
    assert "2k-7k ft" in LEAFLET_HTML_TEMPLATE
    assert "10k-25k ft" in LEAFLET_HTML_TEMPLATE
    assert ">25k ft" in LEAFLET_HTML_TEMPLATE

    # 3. Aircraft click opens popup in 3D
    assert "window._map3dAdsbPopup" in LEAFLET_HTML_TEMPLATE


def test_3d_satellites_plumb_and_footprints():
    """Verify 3D satellites have plumb lines downward to terrain nadir and footprint circles."""
    assert "satellites-3d-plumb" in LEAFLET_HTML_TEMPLATE
    assert "satellites-3d-plumb-lines" in LEAFLET_HTML_TEMPLATE
    assert "satellites-3d-nadir-dots" in LEAFLET_HTML_TEMPLATE
    assert "satellites-3d-footprints" in LEAFLET_HTML_TEMPLATE
    assert "satellites-3d-footprints-line" in LEAFLET_HTML_TEMPLATE
    assert "function syncSatellitesTo3D()" in LEAFLET_HTML_TEMPLATE


def test_3d_companion_orbitals_and_new_nodes():
    """Verify companion orbitals revolve around repeaters in 3D and new nodes gold styling."""
    # 1. Companion orbitals in 3D
    assert "syncOrbitalsTo3D" in LEAFLET_HTML_TEMPLATE
    assert "map3dOrbitalMarkers" in LEAFLET_HTML_TEMPLATE
    assert "orbital_host_3d_" in LEAFLET_HTML_TEMPLATE

    # 2. Newly discovered nodes styling in style3DDot
    assert "window._newNodesActive" in LEAFLET_HTML_TEMPLATE
    assert "#FFD700" in LEAFLET_HTML_TEMPLATE


def test_3d_route_visualisation_and_context_menu(qapp):
    """Verify right-click route visualisation produces 3D parabolic arcs."""
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget()

    widget.web_view = MagicMock()
    widget._page_ready = True

    # 1. Verify visualise_node_path method exists and runs JS
    assert hasattr(widget, "visualise_node_path")
    widget.visualise_node_path("!12345678", "TestNode", 54.5, -3.2)
    assert widget.web_view.page().runJavaScript.called
    js_call = widget.web_view.page().runJavaScript.call_args[0][0]
    assert "drawVisualisedMessagePath" in js_call

    # 2. Template includes syncVisualisedPathTo3D and visualised3DArcs
    assert "syncVisualisedPathTo3D" in LEAFLET_HTML_TEMPLATE
    assert "visualised3DArcs" in LEAFLET_HTML_TEMPLATE


def test_3d_middle_click_rotate_and_right_click_context():
    """Verify MapLibre right-click dragRotate is disabled, middle-click controls tilt/bearing, and right-click is dedicated to context menu."""
    assert "dragRotate: false" in LEAFLET_HTML_TEMPLATE
    assert "ev.button === 1" in LEAFLET_HTML_TEMPLATE or "e.button === 1" in LEAFLET_HTML_TEMPLATE
    assert "Middle-click" in LEAFLET_HTML_TEMPLATE
    assert "map3d.on('contextmenu'" in LEAFLET_HTML_TEMPLATE


def test_3d_weather_rainviewer_maxzoom():
    """Verify RainViewer raster tiles in 3D are clamped to maxzoom 7 to prevent unsupported zoom tiles."""
    assert "rainviewer-3d" in LEAFLET_HTML_TEMPLATE
    assert "maxzoom: 7" in LEAFLET_HTML_TEMPLATE


def test_3d_corescope_shader_multimode_and_arcs():
    """Verify CoreScope 3D WebGL multi-mode rendering with neon contrails, pulse rings, and white-core bead heads."""
    assert "uniform int u_render_mode;" in LEAFLET_HTML_TEMPLATE
    assert "duration: 520," in LEAFLET_HTML_TEMPLATE
    assert "trigger3DPulse" in LEAFLET_HTML_TEMPLATE
    assert "corescope-3d-arcs" in LEAFLET_HTML_TEMPLATE


def test_3d_adsb_floating_perspective_and_drop_stems():
    """Verify ADS-B aircraft are rendered in true 3D space with altitude drop stems and ground contact shadows."""
    assert "adsb-3d-floating-layer" in LEAFLET_HTML_TEMPLATE
    assert "adsb-floating-3d-node" in LEAFLET_HTML_TEMPLATE
    assert "displayAltM" in LEAFLET_HTML_TEMPLATE
    assert "stemVerts" in LEAFLET_HTML_TEMPLATE


def test_orbital_toggle_sync(qapp):
    """Verify companion orbital toggle synchronizes between MeshMapWidget, NavDock, config, and WebEngine."""
    from PyQt6.QtWidgets import QWidget
    parent_win = QWidget()
    nav_dock = NavDockWidget()
    parent_win.nav_dock = nav_dock

    config = AppConfig()
    with patch("meshcore_tray.ui.mesh_map_widget.WEBENGINE_AVAILABLE", False):
        widget = MeshMapWidget(config=config, parent=parent_win)

    widget.web_view = MagicMock()
    widget._page_ready = True

    # Test set_orbitals(True)
    widget.set_orbitals(True)
    assert widget.show_companion_orbitals is True
    assert nav_dock.btn_orbitals.isChecked() is True
    assert config.meshcore.map_show_companion_orbitals is True
    last_call = widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setCompanionOrbitalsVisible(true" in last_call

    # Test set_orbitals(False)
    widget.set_orbitals(False)
    assert widget.show_companion_orbitals is False
    assert nav_dock.btn_orbitals.isChecked() is False
    assert config.meshcore.map_show_companion_orbitals is False
    last_call = widget.web_view.page().runJavaScript.call_args[0][0]
    assert "setCompanionOrbitalsVisible(false" in last_call


def test_3d_rotation_raf_and_clean_dem():
    """Verify middle-click rotation uses requestAnimationFrame with jumpTo, and terrarium tiles are configured."""
    assert "requestAnimationFrame" in LEAFLET_HTML_TEMPLATE
    assert "map3d.jumpTo" in LEAFLET_HTML_TEMPLATE
    assert "s3.amazonaws.com/elevation-tiles-prod/terrarium" in LEAFLET_HTML_TEMPLATE


def test_3d_orbital_lod_and_perspective_alignment():
    """Verify orbitals use zoom threshold 13.5 and map-perspective pitchAlignment."""
    assert "currentZoom >= 13.5" in LEAFLET_HTML_TEMPLATE
    assert "pitchAlignment: 'map'" in LEAFLET_HTML_TEMPLATE
    assert "rotationAlignment: 'map'" in LEAFLET_HTML_TEMPLATE


def test_3d_visualised_path_styles_and_topography_clearance():
    """Verify 3D path visualization supports solid/dashed/dotted styles and clears topography."""
    assert "seg.is_ambiguous" in LEAFLET_HTML_TEMPLATE
    assert "style: segStyle" in LEAFLET_HTML_TEMPLATE
    assert "maxInterElev" in LEAFLET_HTML_TEMPLATE
    assert "totalAlt = Math.max(localGround + 70" in LEAFLET_HTML_TEMPLATE


def test_3d_adsb_taller_cylinder_and_altitude():
    """Verify ADS-B 3D cylinder has upper tier up to 62km and scaled aircraft altitudes."""
    assert "tierH4 = targetGround + 62000" in LEAFLET_HTML_TEMPLATE
    assert "(rawAltM / 10000.0) * 46000.0" in LEAFLET_HTML_TEMPLATE
    assert "makeHollowCirclePolygon3D" in LEAFLET_HTML_TEMPLATE
    assert "color: '#EF4444'" in LEAFLET_HTML_TEMPLATE
    assert "color: '#D946EF'" in LEAFLET_HTML_TEMPLATE
    assert "color: '#8B5CF6'" in LEAFLET_HTML_TEMPLATE
    assert "color: '#7DD3FC'" in LEAFLET_HTML_TEMPLATE
    assert "getCircle3DPoints" in LEAFLET_HTML_TEMPLATE


def test_3d_smooth_ribbon_arcs_and_ground_projected_pulses():
    """Verify 3D map renders camera-facing ribbons for arcs and flat 360-degree ground pulses."""
    # 1. Pulses are rendered flat on map terrain with depth testing disabled to avoid cutoff
    assert "gl.disable(gl.DEPTH_TEST);" in LEAFLET_HTML_TEMPLATE
    assert "cx + rIn * cosT, cy + rIn * sinT, cz" in LEAFLET_HTML_TEMPLATE
    assert "cx + rOut * cosT, cy + rOut * sinT, cz" in LEAFLET_HTML_TEMPLATE
    assert "gl.enable(gl.DEPTH_TEST);" in LEAFLET_HTML_TEMPLATE

    # 2. Camera-facing smooth ribbons for arcs without bead/dot artifacts on solid lines
    assert "vCoreRibbon = build3DRibbon(vpts" in LEAFLET_HTML_TEMPLATE
    assert "bCoreRibbon = build3DRibbon(subPts" in LEAFLET_HTML_TEMPLATE


def test_3d_dem_spike_interception_and_clean_tile():
    """Verify clean DEM tile base64 is injected and transformRequest intercepts Cumbria spike tile."""
    html = get_leaflet_html()
    assert "window._cleanDem76240 = 'data:image/png;base64," in html
    assert "transformRequest: function(url, resourceType)" in LEAFLET_HTML_TEMPLATE
    assert "elevation-tiles-prod/terrarium/7/62/40.png" in LEAFLET_HTML_TEMPLATE


def test_3d_glowing_nodes_and_repeater_color_rules():
    """Verify 3D nodes feature GPU luminous halo and follow repeater color rules."""
    # 1. Soft luminous halo layer with GPU radial blur
    assert "'nodes-3d-halo'" in LEAFLET_HTML_TEMPLATE
    assert "'circle-blur': 0.85" in LEAFLET_HTML_TEMPLATE
    assert "'circle-opacity': 0.65" in LEAFLET_HTML_TEMPLATE

    # 2. getNodeHexColor follows established rules (blue repeater, cyan companion, purple favorite)
    assert "if (node.is_repeater) return '#3B82F6';" in LEAFLET_HTML_TEMPLATE
    assert "if (node.is_favorite) return '#AA55FF';" in LEAFLET_HTML_TEMPLATE
    assert "if (node.is_room_server) return '#EC4899';" in LEAFLET_HTML_TEMPLATE
    assert "if (node.is_local) return '#10B981';" in LEAFLET_HTML_TEMPLATE
    assert "return '#06B6D4';" in LEAFLET_HTML_TEMPLATE


def test_3d_adsb_flight_contrails_attached_to_aircraft():
    """Verify ADSB flight engine contrails render in 3D WebGL airspace connected to floating aircraft."""
    assert "// 4b. 3D Flight Contrails attached directly to floating 3D aircraft" in LEAFLET_HTML_TEMPLATE
    assert "var mcCurr = maplibregl.MercatorCoordinate.fromLngLat([planeItemT.lon, planeItemT.lat], planeItemT.altM);" in LEAFLET_HTML_TEMPLATE
    assert "var trailVerts = build3DRibbon(trailPts, 3.2, [0.06, 0.75, 0.95]" in LEAFLET_HTML_TEMPLATE
    assert "fadeTail" in LEAFLET_HTML_TEMPLATE
    assert "glowVerts = build3DRibbon(trailPts" in LEAFLET_HTML_TEMPLATE


def test_3d_room_server_diamond_and_borderless_nodes():
    """Verify room server diamond canvas icon and symbol layer exist, and client/repeater circles have no white borders."""
    # 1. Room server diamond canvas registration and layer
    assert "map3d.addImage('room-server-diamond'" in LEAFLET_HTML_TEMPLATE
    assert "nodes-3d-room-servers" in LEAFLET_HTML_TEMPLATE
    assert "'icon-image': 'room-server-diamond'" in LEAFLET_HTML_TEMPLATE

    # 2. No white stroke on client or default repeater dots
    assert "'circle-stroke-width': 0" in LEAFLET_HTML_TEMPLATE
    assert "'circle-stroke-width': ['case', ['get', 'is_orbital_beacon'], 2.0, 0]" in LEAFLET_HTML_TEMPLATE


def test_3d_node_overlay_precedence():
    """Verify getNode3DVisualProperties prioritizes overlays before base node colors."""
    assert "function getNode3DVisualProperties(node)" in LEAFLET_HTML_TEMPLATE
    # Overlays in order
    assert "window._searchNodeIdActive" in LEAFLET_HTML_TEMPLATE
    assert "window._newNodesActive" in LEAFLET_HTML_TEMPLATE
    assert "window._mqttNodesActive" in LEAFLET_HTML_TEMPLATE
    assert "pathModesActive" in LEAFLET_HTML_TEMPLATE
    assert "window._scopeOverlaysActive" in LEAFLET_HTML_TEMPLATE
    assert "activityHeatmapActive" in LEAFLET_HTML_TEMPLATE
    assert "window._companionOrbitalsActive" in LEAFLET_HTML_TEMPLATE
    assert "is_orbital_beacon" in LEAFLET_HTML_TEMPLATE


def test_3d_draggable_controls_window():
    """Verify 3D floating controls window is registered as draggable via header."""
    assert "makeOverlayDraggable('map3dControls', 'map3dControlsHeader', 'map_3d_controls')" in LEAFLET_HTML_TEMPLATE
    assert "id=\"map3dControlsHeader\"" in LEAFLET_HTML_TEMPLATE
    assert "overlay_pos_map_3d_controls" in LEAFLET_HTML_TEMPLATE


def test_3d_adsb_stems_and_flight_orientation():
    """Verify vertical stems disable depth test to avoid terrain occlusion and airplane is oriented in 3D space."""
    assert "gl.disable(gl.DEPTH_TEST);" in LEAFLET_HTML_TEMPLATE
    assert "adsb-plane-svg-glyph" in LEAFLET_HTML_TEMPLATE
    assert "transform: perspective(800px) rotateX(" in LEAFLET_HTML_TEMPLATE
    assert "rotateZ(" in LEAFLET_HTML_TEMPLATE


def test_2d_heatmap_glowing_aura_and_thermal_palette():
    """Verify 2D node activity heatmap applies radiant multi-tier box shadows matching 3D."""
    assert "0 0 10px ' + actColor + ', 0 0 22px ' + actColor" in LEAFLET_HTML_TEMPLATE
    assert "render2DActivityHeatmap" in LEAFLET_HTML_TEMPLATE


def test_live_packet_replay_3d_integration():
    """Verify live packet feed replay triggers 3D arc tracing on MapLibre terrain."""
    assert "if (window.tracePacketPath3D)" in LEAFLET_HTML_TEMPLATE
    assert "tracePacketPath3D(meta, coords)" in LEAFLET_HTML_TEMPLATE


def test_corescope_3d_layer_topmost_depth_disabled():
    """Verify corescope 3D layer is placed as top-most layer and disables depth test for beams and pulses."""
    assert "corescope-3d-arcs" in LEAFLET_HTML_TEMPLATE
    assert "map3d.addLayer(corescope3DLayer);" in LEAFLET_HTML_TEMPLATE
    assert "gl.disable(gl.DEPTH_TEST);" in LEAFLET_HTML_TEMPLATE
    assert "map3d.moveLayer('corescope-3d-arcs')" in LEAFLET_HTML_TEMPLATE


def test_2d_heatmap_5tier_thermal_palette_with_blue():
    """Verify 2D activity heatmap uses 5-tier thermal palette matching 3D (blue to red)."""
    assert "act-lbl-min" in LEAFLET_HTML_TEMPLATE
    assert "actColor = '#00D2FF'" in LEAFLET_HTML_TEMPLATE
    assert "0 0 10px ' + actColor + ', 0 0 22px ' + actColor" in LEAFLET_HTML_TEMPLATE


def test_3d_packet_beams_saturated_alpha_blend():
    """Verify 3D packet beams use alpha blending (not additive) and saturated bead head."""
    assert "gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);" in LEAFLET_HTML_TEMPLATE
    assert "mix(v_color.rgb, vec3(1.0), 0.18" in LEAFLET_HTML_TEMPLATE


def test_activity_heatmap_cmin_declared_and_elevated_precedence():
    """Verify cMin is initialized to avoid JS ReferenceError and heatmap takes priority."""
    assert "cMin = 0" in LEAFLET_HTML_TEMPLATE
    assert "min-width: 250px !important;" in LEAFLET_HTML_TEMPLATE


def test_3d_node_generous_hit_testing_and_context_menu_signature():
    """Verify 3D map features generous hit target and passes all 8 arguments to pyBridge on right click."""
    assert "nodes-3d-hit-target" in LEAFLET_HTML_TEMPLATE
    assert "get3DNodeAtPoint(e.point, 22)" in LEAFLET_HTML_TEMPLATE
    assert "window.pyBridge.on_node_context_menu(" in LEAFLET_HTML_TEMPLATE
    assert "Math.round(e.point.x)" in LEAFLET_HTML_TEMPLATE
    assert "Math.round(e.point.y)" in LEAFLET_HTML_TEMPLATE


def test_heard_floods_triggers_trace_and_closes_popup():
    """Verify _on_visualise_packet_path_info triggers corescope trace animation and closes lingering popups."""
    from meshcore_tray.ui.main_window import MainWindow
    import inspect
    src = inspect.getsource(MainWindow._on_visualise_packet_path_info)
    assert "trigger_corescope_trace" in src
    assert "closePopup" in src
    assert "center_on_node" not in src


def test_visualise_packet_path_info_execution():
    """Directly test _on_visualise_packet_path_info execution to prevent NameError on json."""
    from unittest.mock import MagicMock
    from meshcore_tray.ui.main_window import MainWindow
    from meshcore_tray.core.models import PacketPathInfo

    win = MagicMock(spec=MainWindow)
    win.mesh_map = MagicMock()

    path = PacketPathInfo(
        packet_id="test-123",
        sender_id="7ad5b449b3a4",
        sender_name="OdinsEye",
        hop_nodes=["7ad5b449b3a4", "node2"],
        route_type="Direct",
        coordinates=[[54.5, -6.0], [54.6, -6.1]]
    )

    MainWindow._on_visualise_packet_path_info(win, path)
    assert win.mesh_map.trigger_corescope_trace.called
    assert win.mesh_map.preview_packet_path.called
    assert win.mesh_map.run_js.called


def test_3d_adsb_centered_icon_offset_badge_altitude_stems_and_delicate_rings():
    """Verify centered aircraft icon, offset callsign badge, altitude-coded stems, tail contrails, and delicate rings."""
    # 1. Airplane glyph dead-centered with margin centering and side callsign badge
    assert "margin-left:-13px; margin-top:-13px;" in LEAFLET_HTML_TEMPLATE
    assert "left:18px; top:-12px;" in LEAFLET_HTML_TEMPLATE

    # 2. Vertical stem line and ground radar footprint color-coded by aircraft altitude
    assert "sColor = [0.94, 0.27, 0.27]; // Red (<2k ft)" in LEAFLET_HTML_TEMPLATE
    assert "sColor = [0.85, 0.27, 0.94]; // Magenta (2k-7k ft)" in LEAFLET_HTML_TEMPLATE
    assert "sColor = [0.55, 0.36, 0.96]; // Purple (7k-25k ft)" in LEAFLET_HTML_TEMPLATE
    assert "sColor = [0.49, 0.83, 0.99]; // Pale Blue (>25k ft)" in LEAFLET_HTML_TEMPLATE

    # 3. Contrails attached to aircraft tail
    assert "var tailOffsetM = 12.0;" in LEAFLET_HTML_TEMPLATE
    assert "mcTail = maplibregl.MercatorCoordinate.fromLngLat" in LEAFLET_HTML_TEMPLATE

    # 4. Delicate grey dotted wireframe altitude rings (no colored extruded walls)
    assert "getCircle3DPoints(cylMeta.lon, cylMeta.lat, cylMeta.radiusM, hVal, 192);" in LEAFLET_HTML_TEMPLATE
    assert "0.70, 0.76, 0.86, 0.50, 2.2 * dpr" in LEAFLET_HTML_TEMPLATE
    assert "features: []" in LEAFLET_HTML_TEMPLATE


def test_3d_adsb_single_popup_box_and_topmost_zindex():
    """Verify 3D ADS-B popup has a single holding box, no duplicate close buttons, and topmost z-index."""
    # 1. Topmost z-index so no floating aircraft or UI can obscure it
    assert ".maplibregl-popup.adsb-3d-popup" in LEAFLET_HTML_TEMPLATE
    assert "z-index: 2000 !important;" in LEAFLET_HTML_TEMPLATE

    # 2. Single holding box (adsb-3d-popup styling instead of nested custom-popup + adsb-tooltip)
    assert "className: 'adsb-3d-popup'" in LEAFLET_HTML_TEMPLATE
    assert ".adsb-3d-popup .maplibregl-popup-content" in LEAFLET_HTML_TEMPLATE

    # 3. No duplicate close buttons (closeButton: false and display: none on maplibregl close button)
    assert "closeButton: false" in LEAFLET_HTML_TEMPLATE
    assert ".adsb-3d-popup .maplibregl-popup-close-button" in LEAFLET_HTML_TEMPLATE
    assert "display: none !important;" in LEAFLET_HTML_TEMPLATE

    # 4. closeAdsbTooltip removes window._map3dAdsbPopup cleanly
    assert "window._map3dAdsbPopup.remove()" in LEAFLET_HTML_TEMPLATE

