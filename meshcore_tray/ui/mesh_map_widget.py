"""Interactive Mesh Map & Packet Path Watcher Widget for PyQt6."""

from datetime import datetime
import html
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from meshcore_tray.config import AppConfig

import math
import time
from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot, QTimer, QPoint, QEvent
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar,
    QPushButton, QSplitter, QVBoxLayout, QWidget, QApplication
)

logger = logging.getLogger("meshcore_tray.mesh_map")

# Check WebEngine availability
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtWebChannel import QWebChannel
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    logger.warning("PyQt6.QtWebEngineWidgets not available; MeshMapWidget will use fallback canvas.")

if WEBENGINE_AVAILABLE:
    class LoggingWebEnginePage(QWebEnginePage):
        """Custom QWebEnginePage capturing all browser/JS console messages, warnings, and errors."""
        def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
            lvl_map = {
                QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: (logging.INFO, "JS-INFO"),
                QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: (logging.WARNING, "JS-WARN"),
                QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: (logging.ERROR, "JS-ERROR"),
            }
            lvl, tag = lvl_map.get(level, (logging.INFO, "JS-LOG"))
            src = sourceID.split("/")[-1] if sourceID else "inline"
            logger.log(lvl, f"[{tag}] {message} ({src}:{lineNumber})")
else:
    class LoggingWebEnginePage:
        pass

from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.models import MessageEnvelope, NodeContact, PacketPathInfo, is_valid_coordinate, is_plausible_rf_coordinate, is_room_server_contact
from meshcore_tray.core.tropo_service import TropoForecastService
from meshcore_tray.core.adsb_service import ADSBService
from meshcore_tray.core.thunderstorm_service import ThunderstormService
from meshcore_tray.core.elevation_service import ElevationService
from meshcore_tray.core.viewshed_service import ViewshedService
from meshcore_tray.core.space_weather_service import SpaceWeatherService
from meshcore_tray.core.satellite_service import SatelliteService
from meshcore_tray.ui.elevation_profile_widget import ElevationProfileWidget
from meshcore_tray.ui.activity_timeline_widget import NetworkActivityTimelineWidget

STATIC_VENDOR_DIR = Path(__file__).parent / "static" / "vendor"
_CACHED_LEAFLET_HTML: Optional[str] = None


def _load_vendor_asset(filename: str) -> str:
    path = STATIC_VENDOR_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_vendor_js(filename: str) -> str:
    return _load_vendor_asset(filename)


def get_leaflet_html() -> str:
    global _CACHED_LEAFLET_HTML
    if _CACHED_LEAFLET_HTML is None:
        vendor_css = (
            f"<style>\n{_load_vendor_asset('leaflet.css')}\n</style>\n"
            f"<style>\n{_load_vendor_asset('maplibre-gl.css')}\n</style>\n"
        )
        clean_dem_b64 = _load_vendor_asset("dem_7_62_40_clean.b64").strip()
        clean_dem_script = f"<script>window._cleanDem76240 = 'data:image/png;base64,{clean_dem_b64}';</script>\n" if clean_dem_b64 else ""
        vendor_js = (
            f"{clean_dem_script}"
            f"<script>{_load_vendor_asset('leaflet.js')}</script>\n"
            f"<script>{_load_vendor_asset('d3.v4.min.js')}</script>\n"
            f"<script>{_load_vendor_asset('d3-contour.min.js')}</script>\n"
            f"<script>{_load_vendor_asset('satellite.min.js')}</script>\n"
        )
        html = LEAFLET_HTML_TEMPLATE.replace("<!-- __VENDOR_STYLES__ -->", vendor_css)
        html = html.replace("<!-- __VENDOR_SCRIPTS__ -->", vendor_js)
        _CACHED_LEAFLET_HTML = html
    return _CACHED_LEAFLET_HTML


LEAFLET_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MeshCore Watcher Map</title>
    <!-- __VENDOR_STYLES__ -->
    <!-- __VENDOR_SCRIPTS__ -->
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        html, body, #map, #map-3d {
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
            background-color: #12151a;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #c9d1d9;
        }
        #map-3d {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: #0d1117;
            display: none;
            z-index: 10;
        }
        .maplibregl-canvas {
            outline: none;
        }
        .btn-3d-preset:hover {
            filter: brightness(1.2);
        }
        .leaflet-container {
            background-color: #12151a;
        }
        /* Marker container with a generous 20px interaction hitbox */
        .node-marker-wrap {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            background: transparent !important;
            border: none !important;
            cursor: pointer !important;
        }
        .node-marker-wrap .node-dot {
            border-radius: 50%;
            pointer-events: none;
            transition: transform 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
        }

        :root {
            --repeater-color: #3B82F6;
            --repeater-hover-color: #60A5FA;
            --companion-color: #06B6D4;
            --companion-hover-color: #22D3EE;
            --favorite-color: #FFD700;
            --dot-size-repeater: 3px;
            --dot-size-companion: 2.5px;
            --dot-size-favorite: 3.2px;
            --dot-size-local: 4px;
            --visualised-path-color: #FF00FF;
            --visualised-heading-color: #FF00FF;
            --orbital-repeater-color: #FFD335;
            --room-server-color: #A855F7;
            --room-server-hover-color: #C084FC;
            --dot-size-room: 9px;
        }

        @keyframes pathPulse {
            0% { transform: scale(0.9); opacity: 0.8; }
            50% { transform: scale(1.3); opacity: 1.0; }
            100% { transform: scale(0.9); opacity: 0.8; }
        }
        .repeater-path-highlight {
            border-radius: 50%;
            animation: pathPulse 1.6s infinite ease-in-out;
            pointer-events: auto !important;
            cursor: pointer;
        }

        /* Dark Monochromatic Elevation Layer (Pure Grayscale relief matching mockup) */
        .dark-topo-tiles {
            filter: grayscale(100%) invert(100%) brightness(70%) contrast(140%) !important;
            background-color: #12151a !important;
        }

        /* In-Map Non-blocking Loading HUD */
        .map-loading-hud {
            position: absolute;
            top: 14px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 2000;
            background: rgba(18, 21, 26, 0.90);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(16, 185, 129, 0.45);
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.55);
            color: #E5E7EB;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 12px;
            font-weight: 500;
            padding: 6px 14px;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
            pointer-events: none;
            transition: opacity 0.25s ease, transform 0.25s ease;
        }
        .map-loading-hud.hidden {
            opacity: 0;
            transform: translate(-50%, -10px);
            pointer-events: none;
        }
        .loading-spinner {
            width: 12px;
            height: 12px;
            border: 2px solid rgba(16, 185, 129, 0.3);
            border-top-color: #10B981;
            border-radius: 50%;
            animation: hudSpin 0.8s linear infinite;
        }
        @keyframes hudSpin {
            to { transform: rotate(360deg); }
        }

        @keyframes losBeaconPulse {
            0% { transform: scale(0.9); opacity: 0.9; }
            50% { transform: scale(2.2); opacity: 0.0; }
            100% { transform: scale(0.9); opacity: 0.0; }
        }

        .switch-hop-btn {
            background-color: #374151;
            color: #E5E7EB;
            border: 1px solid #4B5563;
            border-radius: 3px;
            padding: 1px 6px;
            font-size: 10px;
            cursor: pointer;
            margin-left: 6px;
            transition: all 0.15s ease;
        }
        .switch-hop-btn:hover {
            background-color: #4B5563;
            color: #FFFFFF;
        }

        .phantom-node-btn {
            background-color: #2D2415;
            color: #FACC15;
            border: 1px solid #CA8A04;
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 9px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s ease, color 0.15s ease;
        }
        .phantom-node-btn:hover {
            background-color: #CA8A04;
            color: #000000;
        }
        .phantom-node-btn.is-phantom {
            background-color: #374151;
            color: #D1D5DB;
            border-color: #4B5563;
        }
        .phantom-node-btn.is-phantom:hover {
            background-color: #4B5563;
            color: #FFFFFF;
        }

        /* Global Discord-Dark Scrollbar for all overlay panels & lists */
        ::-webkit-scrollbar {
            width: 5px;
            height: 5px;
        }
        ::-webkit-scrollbar-track {
            background: #1E1F22;
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb {
            background: #383A40;
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #4E5058;
        }

        /* Shared Unified Floating Overlay Panels Style (Satellite Tracker Theme) */
        .map-overlay-panel {
            background: rgba(30, 31, 34, 0.96) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid #383A40 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
            user-select: none;
            transition: box-shadow 0.2s ease, border-color 0.2s ease;
            box-sizing: border-box;
            overflow: hidden;
        }
        .map-overlay-panel:hover, .map-overlay-panel.active-drag {
            border-color: #5865F2 !important;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.8), 0 0 12px rgba(88, 101, 242, 0.25) !important;
        }
        .map-overlay-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 6px 10px 4px 10px;
            cursor: move;
            border-bottom: 1px solid #383A40;
            background: rgba(255, 255, 255, 0.02);
            border-top-left-radius: 7px;
            border-top-right-radius: 7px;
            gap: 8px;
            user-select: none;
            flex-shrink: 0;
        }
        .map-overlay-header:active {
            cursor: grabbing;
        }
        .map-drag-handle-grip {
            opacity: 0.55;
            font-size: 11px;
            cursor: move;
            letter-spacing: -1px;
            margin-right: 4px;
            color: #94A3B8;
        }
        .map-drag-handle-grip:hover {
            opacity: 0.95;
            color: #5865F2;
        }

        .floating-route-panel {
            position: absolute;
            top: 60px;
            left: 12px;
            width: 290px;
            max-width: calc(100% - 24px);
            max-height: calc(100% - 24px);
            z-index: 1000;
            display: none;
            flex-direction: column;
            overflow: hidden;
        }
        .floating-route-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: rgba(255, 255, 255, 0.04);
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            cursor: move;
            user-select: none;
        }
        .floating-route-title {
            font-size: 12px;
            font-weight: 700;
            color: var(--visualised-heading-color, #FF00FF);
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .floating-route-close {
            background: transparent;
            border: none;
            color: #9CA3AF;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            line-height: 1;
            padding: 2px 6px;
            border-radius: 4px;
            transition: background 0.15s ease, color 0.15s ease;
        }
        .floating-route-close:hover {
            color: #FFFFFF;
            background: rgba(255, 255, 255, 0.12);
        }
        .floating-route-body {
            padding: 10px 12px;
            overflow-y: auto;
            flex: 1;
            color: #E5E7EB;
        }
        .hop-candidates-dropdown {
            margin: 3px 0 6px 14px;
        }
        .hop-candidates-summary {
            cursor: pointer;
            color: #9CA3AF;
            font-size: 10px;
            font-weight: 500;
            user-select: none;
            outline: none;
            list-style: none;
            display: flex;
            align-items: center;
            gap: 4px;
            padding: 2px 5px;
            border-radius: 4px;
            width: fit-content;
            transition: background 0.15s ease, color 0.15s ease;
        }
        .hop-candidates-summary::-webkit-details-marker {
            display: none;
        }
        .hop-candidates-summary:hover {
            color: #D1D5DB;
            background: rgba(255, 255, 255, 0.08);
        }
        .hop-candidates-box {
            background: #121418;
            border: 1px solid #2E333D;
            border-radius: 4px;
            padding: 6px 8px;
            margin-top: 4px;
        }
        @keyframes cyanCandidatePulse {
            0% {
                transform: scale(0.85);
                box-shadow: 0 0 0 0 rgba(0, 255, 255, 0.85);
                opacity: 1;
            }
            70% {
                transform: scale(1.6);
                box-shadow: 0 0 0 16px rgba(0, 255, 255, 0);
                opacity: 0.3;
            }
            100% {
                transform: scale(0.85);
                box-shadow: 0 0 0 0 rgba(0, 255, 255, 0);
                opacity: 1;
            }
        }
        @keyframes whiteNeighborPulse {
            0% {
                transform: scale(0.8);
                box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.95);
                opacity: 1;
            }
            70% {
                transform: scale(1.85);
                box-shadow: 0 0 0 18px rgba(255, 255, 255, 0);
                opacity: 0.35;
            }
            100% {
                transform: scale(0.8);
                box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);
                opacity: 1;
            }
        }
        @keyframes hostRepeaterPulse {
            0% {
                transform: scale(0.85);
                box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.9);
                opacity: 1;
            }
            70% {
                transform: scale(1.6);
                box-shadow: 0 0 0 16px rgba(56, 189, 248, 0);
                opacity: 0.3;
            }
            100% {
                transform: scale(0.85);
                box-shadow: 0 0 0 0 rgba(56, 189, 248, 0);
                opacity: 1;
            }
        }
        .hop-candidate-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin: 2px 0;
            font-size: 9.5px;
            color: #D1D5DB;
            padding: 2px 6px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.15s ease, color 0.15s ease;
        }
        .hop-candidate-row:hover {
            background: rgba(0, 255, 255, 0.12);
            color: #00FFFF;
        }
        .hop-item-row {
            transition: background 0.15s ease, color 0.15s ease;
            border-radius: 4px;
        }
        .hop-item-row.has-gps {
            cursor: pointer;
        }
        .hop-item-row.has-gps:hover,
        .active-candidate-row:hover {
            background: rgba(0, 255, 255, 0.12) !important;
        }

        @keyframes flowTowardsHome {
            from {
                stroke-dashoffset: 0;
            }
            to {
                stroke-dashoffset: -40;
            }
        }
        .animated-path-flow {
            animation: flowTowardsHome 1.6s linear infinite !important;
            pointer-events: none !important;
        }
        .path-hit-corridor {
            pointer-events: stroke !important;
            cursor: pointer;
        }

        /* Pure vibrant orange for regular repeaters with luminous glow */
        .node-dot-repeater {
            width: var(--dot-size-repeater);
            height: var(--dot-size-repeater);
            background: var(--repeater-color);
            box-shadow: 0 0 6px var(--repeater-color);
        }

        /* Companion Orbitals: glowing hollow ring beacon for repeaters with docked satellites */
        .node-dot.orbital-ring-repeater {
            width: 14px !important;
            height: 14px !important;
            background: transparent !important;
            background-color: transparent !important;
            border: 2.5px solid var(--orbital-repeater-color, #FFD335) !important;
            box-shadow: 0 0 8px var(--orbital-repeater-color, #FFD335) !important;
            box-sizing: border-box !important;
            border-radius: 50% !important;
            transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease !important;
            pointer-events: auto !important;
            cursor: pointer !important;
        }
        .node-marker-wrap:hover .node-dot.orbital-ring-repeater {
            transform: scale(1.15) !important;
            border-color: #FFFFFF !important;
            box-shadow: 0 0 14px var(--orbital-repeater-color, #FFD335) !important;
        }

        /* If the orbital repeater is favorited (e.g. M7NCY West Yagi), show the ring in the purple favourite color! */
        .node-dot.orbital-ring-repeater.orbital-ring-repeater-fav,
        .node-dot-favorite.orbital-ring-repeater {
            border-color: var(--favorite-color, #AA55FF) !important;
            box-shadow: 0 0 8px var(--favorite-color, #AA55FF) !important;
        }
        .node-marker-wrap:hover .node-dot.orbital-ring-repeater.orbital-ring-repeater-fav,
        .node-marker-wrap:hover .node-dot-favorite.orbital-ring-repeater {
            border-color: #FFFFFF !important;
            box-shadow: 0 0 14px var(--favorite-color, #AA55FF) !important;
        }

        @keyframes orbitalSpin {
            from {
                transform: rotate(0deg);
            }
            to {
                transform: rotate(360deg);
            }
        }
        @keyframes orbitalCounterSpin {
            from {
                transform: rotate(0deg);
            }
            to {
                transform: rotate(-360deg);
            }
        }

        .orbital-sat-group {
            transform-origin: 60px 60px;
            animation: orbitalSpin 32s linear infinite;
        }

        .orbital-sat-node {
            transform-origin: center;
            transform-box: fill-box;
            animation: orbitalCounterSpin 32s linear infinite;
        }

        .orbital-paused .orbital-sat-group,
        .orbital-paused .orbital-sat-node {
            animation-play-state: paused !important;
        }

        .orbital-host-container {
            width: 120px !important;
            height: 120px !important;
            pointer-events: none !important;
        }

        .orbital-host-inner {
            width: 120px;
            height: 120px;
            position: relative;
            pointer-events: none;
        }

        .orbital-tactical-tooltip {
            position: fixed;
            z-index: 10000;
            pointer-events: none;
            background: rgba(13, 17, 23, 0.95);
            border: 1px solid rgba(56, 189, 248, 0.75);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.75), 0 0 12px rgba(56, 189, 248, 0.25);
            border-radius: 6px;
            padding: 7px 11px;
            color: #F3F4F6;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 11px;
            line-height: 1.35;
            white-space: nowrap;
            transform: translate(-50%, -100%);
            display: none;
            backdrop-filter: blur(6px);
        }

        /* Companion nodes: micro green pinpoint with luminous glow */
        .node-dot-companion {
            width: var(--dot-size-companion);
            height: var(--dot-size-companion);
            background: var(--companion-color);
            box-shadow: 0 0 6px var(--companion-color);
        }

        /* Favorite nodes & repeaters: bright pure golden yellow with luminous glow */
        .node-dot-favorite {
            width: var(--dot-size-favorite);
            height: var(--dot-size-favorite);
            background: var(--favorite-color);
            box-shadow: 0 0 6px var(--favorite-color);
        }

        /* Local node: micro slate-cyan pinpoint with luminous glow */
        .node-dot-local {
            width: var(--dot-size-local);
            height: var(--dot-size-local);
            background: #38BDF8;
            box-shadow: 0 0 6px #38BDF8;
        }

        /* Room server nodes: square rotated 45deg (diamond) with luminous magenta glow and crisp white border */
        .node-marker-wrap-room {
            z-index: 12000 !important;
        }
        .node-dot-room {
            width: var(--dot-size-room, 9px);
            height: var(--dot-size-room, 9px);
            border-radius: 0% !important;
            transform: rotate(45deg);
            background: var(--room-server-color, #D946EF);
            box-shadow: 0 0 10px var(--room-server-color, #D946EF), 0 0 4px #FFFFFF;
            border: 1.5px solid #FFFFFF !important;
        }

        /* Phantom repeater nodes: black dot with slate-gray border and dark shadow */
        .node-dot-phantom {
            width: var(--dot-size-repeater);
            height: var(--dot-size-repeater);
            background: #000000 !important;
            border: 1.5px solid #4B5563 !important;
            box-shadow: 0 0 6px rgba(0, 0, 0, 0.9) !important;
        }

        /* MQTT Discovered nodes: orange pinpoint with vibrant orange glow */
        .node-dot-mqtt {
            background: #F97316 !important;
            background-color: #F97316 !important;
            border: 1.5px solid #FFFFFF !important;
            box-shadow: 0 0 14px rgba(249, 115, 22, 0.95), 0 0 4px #FFFFFF !important;
        }

        /* Hover animations when mouse enters the 20px hitbox */
        .node-marker-wrap:hover .node-dot {
            transform: scale(3.0);
            opacity: 1.0 !important;
        }
        .node-marker-wrap:hover .node-dot-repeater {
            background: var(--repeater-hover-color) !important;
            box-shadow: 0 0 10px var(--repeater-hover-color);
        }
        .node-marker-wrap:hover .node-dot-phantom {
            transform: scale(2.8) !important;
            background: #000000 !important;
            border: 2px solid #9CA3AF !important;
            box-shadow: 0 0 12px rgba(0, 0, 0, 1.0) !important;
        }
        .node-marker-wrap:hover .node-dot-companion {
            background: var(--companion-hover-color) !important;
            box-shadow: 0 0 10px var(--companion-hover-color);
        }
        .node-marker-wrap:hover .node-dot-favorite {
            background: #FFEE33;
            box-shadow: 0 0 12px #FFD700;
        }
        .node-marker-wrap:hover .node-dot-local {
            background: #7DD3FC;
            box-shadow: 0 0 10px #38BDF8;
        }
        .node-marker-wrap:hover .node-dot-room {
            transform: rotate(45deg) scale(2.6) !important;
            background: var(--room-server-hover-color, #F0ABFC) !important;
            box-shadow: 0 0 16px var(--room-server-hover-color, #F0ABFC), 0 0 6px #FFFFFF;
        }

        /* Tactical Radar Blip & Sender Badge */
        .radar-blip-wrap {
            background: transparent !important;
            border: none !important;
            pointer-events: none !important;
            overflow: visible !important;
        }
        .radar-blip-container {
            position: relative;
            width: 20px;
            height: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            pointer-events: none;
        }

        /* Sleek expanding radar ring */
        @keyframes radar-ping-ring {
            0% {
                transform: scale(0.3);
                opacity: 1.0;
            }
            60% {
                transform: scale(1.6);
                opacity: 0.7;
            }
            100% {
                transform: scale(2.4);
                opacity: 0.0;
            }
        }
        .radar-ping-ring {
            position: absolute;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            border: 1.5px solid currentColor;
            background: rgba(59, 130, 246, 0.15);
            animation: radar-ping-ring 1.0s ease-out infinite;
            pointer-events: none;
            box-sizing: border-box;
        }

        /* Crisp central white ping dot */
        .radar-center-dot {
            position: absolute;
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: #FFFFFF;
            box-shadow: 0 0 5px 1px #FFFFFF;
            pointer-events: none;
        }

        /* Sender Name Badge floating cleanly above the blip */
        @keyframes radar-badge-fade {
            0% {
                opacity: 0;
                transform: translate(-50%, 4px);
            }
            15% {
                opacity: 1;
                transform: translate(-50%, -2px);
            }
            85% {
                opacity: 1;
                transform: translate(-50%, -2px);
            }
            100% {
                opacity: 0;
                transform: translate(-50%, -6px);
            }
        }
        .radar-sender-badge {
            position: absolute;
            bottom: 16px;
            left: 50%;
            transform: translate(-50%, -2px);
            background: rgba(24, 26, 31, 0.95);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.7);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.3px;
            white-space: nowrap;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.8), 0 0 6px rgba(255, 255, 255, 0.3);
            animation: radar-badge-fade 3.0s ease-in-out forwards;
            pointer-events: none;
            z-index: 3000;
        }
        /* Clean dark hover tooltips */
        .leaflet-tooltip.node-tooltip {
            background-color: #222327 !important;
            color: #E5E7EB !important;
            border: 1px solid #414143 !important;
            border-radius: 4px !important;
            font-size: 11px !important;
            font-weight: 500 !important;
            padding: 2px 6px !important;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.7) !important;
            white-space: nowrap !important;
        }
        .leaflet-tooltip-top.node-tooltip:before {
            border-top-color: #414143 !important;
        }
        /* Clean dark click popups */
        .leaflet-popup-content-wrapper,
        .custom-popup .leaflet-popup-content-wrapper {
            background-color: #222327 !important;
            color: #E5E7EB !important;
            border: 1px solid #414143 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7) !important;
        }
        .leaflet-popup-tip,
        .custom-popup .leaflet-popup-tip {
            background-color: #222327 !important;
            border: 1px solid #414143 !important;
        }
        .leaflet-popup-content,
        .custom-popup .leaflet-popup-content {
            color: #E5E7EB !important;
            margin: 10px 14px !important;
            line-height: 1.4 !important;
            min-width: 250px !important;
            max-width: 320px !important;
            width: 270px !important;
            box-sizing: border-box !important;
        }
        .custom-popup {
            min-width: 250px !important;
        }
        .custom-popup .leaflet-popup-content-wrapper {
            min-width: 250px !important;
        }
        .leaflet-container a.leaflet-popup-close-button {
            color: #9CA3AF !important;
            padding: 6px 8px 0 0 !important;
        }
        .leaflet-container a.leaflet-popup-close-button:hover {
            color: #FFFFFF !important;
        }
        /* MapLibre GL 3D dark popups and tooltips */
        .maplibregl-popup-content,
        .custom-popup .maplibregl-popup-content {
            background-color: #222327 !important;
            color: #E5E7EB !important;
            border: 1px solid #414143 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7) !important;
            padding: 10px 14px !important;
            line-height: 1.4 !important;
            min-width: 250px !important;
            max-width: 320px !important;
            width: 270px !important;
            box-sizing: border-box !important;
        }
        .maplibregl-popup-tip,
        .custom-popup .maplibregl-popup-tip {
            border-top-color: #222327 !important;
            border-bottom-color: #222327 !important;
            border-left-color: #222327 !important;
            border-right-color: #222327 !important;
        }
        .maplibregl-popup-close-button {
            color: #9CA3AF !important;
            padding: 6px 8px !important;
            font-size: 16px !important;
            background: transparent !important;
            border: none !important;
            cursor: pointer !important;
        }
        .maplibregl-popup-close-button:hover {
            color: #FFFFFF !important;
        }
        /* 3D ADS-B Popup styling (single holding box, topmost z-index) */
        .maplibregl-popup.adsb-3d-popup {
            z-index: 2000 !important;
            pointer-events: auto !important;
        }
        .adsb-3d-popup .maplibregl-popup-content {
            background: rgba(26, 28, 32, 0.96) !important;
            border: 1px solid #41444C !important;
            border-radius: 8px !important;
            color: #FFFFFF !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.85) !important;
            font-size: 10px !important;
            padding: 10px 12px !important;
            line-height: 1.35 !important;
            width: auto !important;
            min-width: 220px !important;
            max-width: 340px !important;
            box-sizing: border-box !important;
            pointer-events: auto !important;
        }
        .adsb-3d-popup .maplibregl-popup-tip {
            border-top-color: rgba(26, 28, 32, 0.96) !important;
            border-bottom-color: rgba(26, 28, 32, 0.96) !important;
            border-left-color: rgba(26, 28, 32, 0.96) !important;
            border-right-color: rgba(26, 28, 32, 0.96) !important;
        }
        .adsb-3d-popup .maplibregl-popup-close-button {
            display: none !important;
        }
        .popup-title {
            font-weight: bold;
            font-size: 13px;
            color: #F3F4F6 !important;
            margin-bottom: 4px;
        }
        .popup-stat {
            font-size: 11px;
            color: #9CA3AF !important;
            margin: 2px 0;
        }
        .popup-btn {
            background: #3E4452 !important;
            color: #FFFFFF !important;
            border: 1px solid #525A6C !important;
            border-radius: 4px !important;
            padding: 5px 8px !important;
            font-size: 11px !important;
            font-weight: 600 !important;
            cursor: pointer !important;
            margin-top: 6px !important;
            width: 100% !important;
            transition: background 0.15s ease !important;
        }
        .popup-btn:hover {
            background: #4B5363 !important;
        }
        /* Styled Zoom Controls in-line with dark UI */
        .leaflet-bar {
            border: 1px solid #414143 !important;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5) !important;
            border-radius: 6px !important;
            overflow: hidden !important;
        }
        .leaflet-bar a {
            background-color: #222327 !important;
            color: #E5E7EB !important;
            border-bottom: 1px solid #414143 !important;
            transition: background 0.15s ease !important;
        }
        .leaflet-bar a:hover {
            background-color: #353A45 !important;
            color: #FFFFFF !important;
        }
        .leaflet-bar a:last-child {
            border-bottom: none !important;
        }
        /* Floating Path Mode Legend - Portrait Card */
        .path-mode-legend {
            position: absolute;
            bottom: 24px;
            left: 12px;
            width: 250px;
            padding: 8px 10px 6px 10px;
            font-size: 11px;
            color: #E5E7EB;
            z-index: 1000;
            display: none;
            pointer-events: auto;
            user-select: none;
            flex-direction: column;
            gap: 6px;
        }
        .path-mode-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 5px;
            padding: 5px 8px;
            gap: 8px;
            transition: border-color 0.15s ease, background 0.15s ease;
        }
        .path-mode-row:hover {
            border-color: #4E5058;
            background: #313338;
        }
        .path-mode-left {
            display: flex;
            align-items: center;
            gap: 7px;
            min-width: 0;
        }
        .path-legend-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        .path-mode-info {
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        .path-mode-name {
            font-size: 10px;
            font-weight: 700;
            color: #F2F3F5;
            white-space: nowrap;
        }
        .path-mode-desc {
            font-size: 8px;
            color: #949BA4;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .path-stat-badge {
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 8px;
            padding: 1px 6px;
            font-size: 9px;
            font-weight: 600;
            color: #DBDEE1;
            flex-shrink: 0;
            min-width: 18px;
            text-align: center;
        }
        .path-options-bar {
            display: flex;
            flex-direction: column;
            gap: 4px;
            padding-top: 4px;
            border-top: 1px solid #383A40;
            margin-top: 2px;
        }
        .path-chk-label {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 9.5px;
            color: #B5BAC1;
            cursor: pointer;
        }

        /* Floating Tropo Legend Panel - Portrait Card */
        .tropo-legend-panel {
            position: absolute;
            bottom: 24px;
            right: 12px;
            width: 250px;
            padding: 8px 10px 6px 10px;
            font-size: 11px;
            color: #E5E7EB;
            z-index: 1000;
            display: none;
            user-select: none;
            flex-direction: column;
            gap: 6px;
        }
        .tropo-legend-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 4px;
            flex-shrink: 0;
        }
        .tropo-legend-title {
            font-weight: 700;
            font-size: 11px;
            color: #F2F3F5;
            display: flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
        }
        .tropo-stepper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 2px 4px;
            gap: 6px;
            width: 100%;
            box-sizing: border-box;
        }
        .tropo-step-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            cursor: pointer;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 3px;
            line-height: 1;
        }
        .tropo-step-btn:hover {
            background: #2B2D31;
            color: #FFFFFF;
        }
        .tropo-time-label {
            font-size: 10px;
            font-weight: 600;
            color: #F2F3F5;
            white-space: nowrap;
        }
        .tropo-legend-close {
            background: transparent;
            border: none;
            color: #949BA4;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
            line-height: 1;
            padding: 0 2px;
        }
        .tropo-legend-close:hover {
            color: #FFFFFF;
        }
        .tropo-tier-list {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        .tropo-tier-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 3px 6px;
            font-size: 9.5px;
        }
        .tropo-tier-left {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .tropo-tier-dot {
            width: 8px;
            height: 8px;
            border-radius: 2px;
            flex-shrink: 0;
        }
        .tropo-tier-name {
            color: #F2F3F5;
            font-weight: 600;
        }
        .tropo-tier-range {
            color: #949BA4;
            font-size: 8.5px;
            font-family: monospace;
        }
        .tropo-legend-footer {
            font-size: 8.5px;
            color: #64748B;
            text-align: right;
            border-top: 1px solid #383A40;
            padding-top: 4px;
            margin-top: 2px;
        }

        /* Floating Scope Filter Bar - Portrait Card */
        .scope-filter-bar {
            position: absolute;
            top: 60px;
            left: 12px;
            z-index: 1000;
            padding: 8px 10px 6px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            width: 220px;
            max-height: calc(100% - 120px);
            box-sizing: border-box;
        }
        .scope-filter-title {
            font-size: 11px;
            font-weight: 700;
            color: #F2F3F5;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .scope-options-bar {
            display: flex;
            flex-direction: column;
            gap: 4px;
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 6px;
        }
        .scope-chk-label {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 9.5px;
            color: #B5BAC1;
            cursor: pointer;
        }
        .scope-pills-container {
            display: flex;
            flex-direction: column;
            gap: 3px;
            overflow-y: auto;
            max-height: 240px;
            padding-right: 2px;
        }
        .scope-pill {
            background: #2B2D31;
            border: 1px solid #383A40;
            color: #DBDEE1;
            font-size: 10px;
            font-weight: 600;
            padding: 3px 6px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.12s ease, border-color 0.12s ease;
            white-space: nowrap;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
        }
        .scope-pill:hover {
            background: #35373C;
            color: #FFFFFF;
            border-color: #4E5058;
        }
        .scope-pill.active {
            background: #35373C;
            border-color: #5865F2;
            color: #FFFFFF;
        }
        .scope-pill-name {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .scope-pill-count {
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 8px;
            padding: 1px 5px;
            font-size: 9px;
            color: #949BA4;
            flex-shrink: 0;
            margin-left: auto;
        }
        .scope-polygon-label {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            color: #FFFFFF;
            font-size: 12px;
            font-weight: 700;
            text-shadow: 0 0 6px #000, 0 0 10px #000;
            pointer-events: none;
        }
        .scope-btn {
            background: #2B2D31;
            color: #DBDEE1;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 3px 7px;
            font-size: 10px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }
        .scope-btn:hover {
            background: #35373C;
            color: #FFFFFF;
            border-color: #4E5058;
        }

        /* Floating Activity Heatmap - Portrait Card */
        .activity-heatmap-bar {
            position: absolute;
            bottom: 24px;
            left: 12px;
            z-index: 1000;
            width: 250px;
            padding: 8px 10px 6px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-sizing: border-box;
        }
        .activity-bar-title {
            font-size: 11px;
            font-weight: 700;
            color: #F2F3F5;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .activity-btn-group {
            display: flex;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
            width: 100%;
            box-sizing: border-box;
        }
        .activity-tf-btn {
            flex: 1;
            background: transparent;
            color: #949BA4;
            border: none;
            border-radius: 4px;
            padding: 3px 2px;
            font-size: 9px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            transition: all 0.15s ease;
        }
        .activity-tf-btn:hover {
            color: #DBDEE1;
            background: #2B2D31;
        }
        .activity-tf-btn.active {
            background: #35373C;
            color: #FFFFFF;
            font-weight: 700;
        }
        .act-options-bar {
            display: flex;
            flex-direction: column;
            gap: 4px;
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 6px;
        }
        .act-chk-label {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 9.5px;
            color: #B5BAC1;
            cursor: pointer;
        }
        .act-peak-card {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 9px;
        }
        .activity-legend {
            display: flex;
            flex-direction: column;
            gap: 2px;
            background: #1E1F22;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 6px;
        }
        .act-leg-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 9.5px;
            color: #DBDEE1;
            padding: 1px 0;
        }
        .act-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 5px;
        }
        .activity-bar-close {
            background: transparent;
            border: none;
            color: #949BA4;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            padding: 0 2px;
            line-height: 1;
        }
        .activity-bar-close:hover {
            color: #FFFFFF;
        }
        .node-activity-heat-aura {
            filter: blur(8px);
            pointer-events: none;
        }

        /* Floating New Nodes Discovery Panel - Discord Grey Theme */
        .new-nodes-panel {
            position: absolute;
            bottom: 24px;
            left: 12px;
            z-index: 1000;
            width: 275px;
            padding: 8px 10px 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-sizing: border-box;
            background: rgba(30, 31, 34, 0.96) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid #383A40 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
        }
        .new-nodes-panel:hover, .new-nodes-panel.active-drag {
            border-color: #FFD700 !important;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.8), 0 0 12px rgba(255, 215, 0, 0.25) !important;
        }
        .new-nodes-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 4px;
            margin: -2px -2px 2px -2px;
            flex-shrink: 0;
            cursor: move;
        }
        .new-nodes-title {
            font-size: 11px;
            font-weight: 700;
            color: #FFD700;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 5px;
            text-transform: uppercase;
        }
        .new-nodes-close-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            font-size: 15px;
            font-weight: bold;
            line-height: 1;
            cursor: pointer;
            padding: 0 4px;
            border-radius: 4px;
            transition: all 0.15s ease;
        }
        .new-nodes-close-btn:hover {
            color: #FFFFFF;
            background: #ED4245;
        }
        .new-nodes-btn-group {
            display: flex;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
            width: 100%;
            box-sizing: border-box;
        }
        .new-nodes-tf-btn {
            flex: 1;
            background: transparent;
            color: #949BA4;
            border: none;
            border-radius: 4px;
            padding: 4px 2px;
            font-size: 9.5px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            transition: all 0.15s ease;
        }
        .new-nodes-tf-btn:hover {
            color: #DBDEE1;
            background: #2B2D31;
        }
        .new-nodes-tf-btn.active {
            background: #2B2D31;
            border: 1px solid #FFD700;
            color: #FFD700;
            font-weight: 700;
            box-shadow: 0 0 8px rgba(255, 215, 0, 0.3);
        }
        .new-nodes-stats-card {
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 6px 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 9.5px;
        }
        .new-nodes-action-btn {
            background: #2B2D31;
            border: 1px solid #383A40;
            color: #DBDEE1;
            border-radius: 4px;
            padding: 4px 6px;
            font-size: 9.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            width: 100%;
            text-align: center;
        }
        .new-nodes-action-btn:hover {
            background: #35373C;
            border-color: #FFD700;
            color: #FFFFFF;
        }

        /* Floating MQTT Discovered Nodes Panel - Discord Grey / Orange Accent Theme */
        .mqtt-nodes-panel {
            position: absolute;
            bottom: 24px;
            left: 12px;
            z-index: 1000;
            width: 275px;
            padding: 8px 10px 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-sizing: border-box;
            background: rgba(30, 31, 34, 0.96) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid #383A40 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
        }
        .mqtt-nodes-panel:hover, .mqtt-nodes-panel.active-drag {
            border-color: #F97316 !important;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.8), 0 0 12px rgba(249, 115, 22, 0.25) !important;
        }
        .mqtt-nodes-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 4px;
            margin: -2px -2px 2px -2px;
            flex-shrink: 0;
            cursor: move;
        }
        .mqtt-nodes-title {
            font-size: 11px;
            font-weight: 700;
            color: #FB923C;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 5px;
            text-transform: uppercase;
        }
        .mqtt-nodes-close-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            font-size: 15px;
            font-weight: bold;
            line-height: 1;
            cursor: pointer;
            padding: 0 4px;
            border-radius: 4px;
            transition: all 0.15s ease;
        }
        .mqtt-nodes-close-btn:hover {
            color: #FFFFFF;
            background: #ED4245;
        }
        .mqtt-nodes-stats-card {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            background: rgba(17, 18, 20, 0.85);
            padding: 6px 8px;
            border-radius: 5px;
            border: 1px solid #2B2D31;
        }
        .mqtt-nodes-desc {
            font-size: 10px;
            color: #9CA3AF;
            line-height: 1.3;
        }

        /* Floating Thunderstorm & Radar Panel - Portrait Card */
        .thunderstorm-panel {
            position: absolute;
            top: 12px;
            right: 12px;
            z-index: 1000;
            width: 270px;
            padding: 8px 10px 6px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-sizing: border-box;
        }
        .thunderstorm-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 4px;
            flex-shrink: 0;
        }
        .thunderstorm-title {
            font-size: 11px;
            font-weight: 700;
            color: #F2F3F5;
            display: flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
        }
        .thunder-stepper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 2px 4px;
            gap: 6px;
            width: 100%;
            box-sizing: border-box;
        }
        .thunder-step-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            cursor: pointer;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 3px;
            line-height: 1;
        }
        .thunder-step-btn:hover {
            background: #2B2D31;
            color: #FFFFFF;
        }
        .thunder-time-label {
            font-size: 9.5px;
            font-weight: 600;
            color: #F2F3F5;
            white-space: nowrap;
        }
        .thunder-nearest-card {
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 5px;
            padding: 6px 8px;
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        .thunder-jump-btn {
            background: #5865F2;
            border: none;
            border-radius: 4px;
            color: #FFFFFF;
            font-size: 9.5px;
            font-weight: 600;
            padding: 3px 6px;
            cursor: pointer;
            margin-top: 2px;
            transition: background 0.15s;
        }
        .thunder-jump-btn:hover {
            background: #4752C4;
        }
        .thunder-proximity-badge {
            background: rgba(239, 68, 68, 0.2);
            border: 1px solid #EF4444;
            color: #FCA5A5;
            font-size: 9px;
            font-weight: 700;
            border-radius: 4px;
            padding: 3px 6px;
            text-align: center;
            animation: pulse-border 1.5s infinite;
        }
        @keyframes pulse-border {
            0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
            70% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
            100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .thunder-stat-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4px;
        }
        .thunder-stat-box {
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 6px;
            text-align: center;
        }
        .thunder-stat-label {
            font-size: 8px;
            color: #949BA4;
            text-transform: uppercase;
        }
        .thunder-stat-val {
            font-size: 11px;
            font-weight: 700;
            color: #F2F3F5;
        }
        .thunderstorm-close-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            line-height: 1;
            padding: 0 2px;
        }
        .thunderstorm-close-btn:hover {
            color: #FFFFFF;
        }

        /* Floating Search Node IDs Panel - Portrait Card */
        .search-node-id-panel {
            position: absolute;
            top: 60px;
            left: 12px;
            z-index: 1000;
            width: 275px;
            padding: 8px 10px 6px 10px;
            display: none;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            box-sizing: border-box;
        }
        .search-node-input {
            width: 100%;
            box-sizing: border-box;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            color: #F2F3F5;
            font-family: monospace;
            font-size: 11px;
            padding: 5px 8px;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
        }
        .search-node-input:focus {
            border-color: #23A55A;
            box-shadow: 0 0 6px rgba(35, 165, 90, 0.4);
        }
        .search-node-status {
            font-size: 9px;
            color: #949BA4;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 2px;
        }
        .search-node-results {
            display: flex;
            flex-direction: column;
            gap: 3px;
            max-height: 220px;
            overflow-y: auto;
            padding-right: 2px;
        }
        .search-node-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 4px 7px;
            cursor: pointer;
            transition: all 0.12s ease;
        }
        .search-node-item:hover {
            background: #35373C;
            border-color: #23A55A;
        }
        .search-node-id-mono {
            font-family: monospace;
            font-weight: 700;
            color: #23A55A;
            font-size: 11px;
        }
        .search-node-meta {
            font-size: 8.5px;
            color: #949BA4;
        }

        /* Node Popup Green Node ID & Copy Button */
        .node-popup-id-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 6px;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 3px 6px;
        }
        .node-popup-id-mono {
            font-family: monospace;
            font-size: 11px;
            font-weight: 700;
            color: #23A55A;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .node-popup-copy-btn {
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 3px;
            color: #DBDEE1;
            font-size: 9px;
            font-weight: 600;
            padding: 1px 5px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .node-popup-copy-btn:hover {
            background: #383A40;
            color: #FFFFFF;
            border-color: #23A55A;
        }

        /* Floating Space Weather & Aurora Panel */
        .aurora-legend-panel {
            position: absolute;
            top: 60px;
            left: 175px;
            z-index: 1000;
            border: 1px solid rgba(168, 85, 247, 0.5) !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65), 0 0 15px rgba(168, 85, 247, 0.2) !important;
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            user-select: none;
            width: 275px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: #E2E8F0;
        }
        .aurora-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(168, 85, 247, 0.3);
            padding-bottom: 6px;
        }
        .aurora-title {
            font-size: 11px;
            font-weight: 700;
            color: #C084FC;
            display: flex;
            align-items: center;
            gap: 6px;
            letter-spacing: 0.5px;
        }
        .aurora-refresh-btn, .aurora-close-btn {
            background: transparent;
            border: none;
            color: #94A3B8;
            font-size: 13px;
            font-weight: bold;
            cursor: pointer;
            padding: 2px 4px;
            border-radius: 4px;
            line-height: 1;
        }
        .aurora-refresh-btn:hover {
            color: #C084FC;
            background: rgba(168, 85, 247, 0.2);
        }
        .aurora-close-btn:hover {
            color: #FFFFFF;
            background: rgba(239, 68, 68, 0.2);
        }
        .aurora-kpi-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 6px;
        }
        .aurora-kpi-card {
            background: rgba(30, 41, 59, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            padding: 5px 6px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }
        .aurora-kpi-label {
            font-size: 8px;
            font-weight: 600;
            color: #94A3B8;
            margin-bottom: 2px;
            letter-spacing: 0.3px;
        }
        .aurora-kpi-val {
            font-size: 11.5px;
            font-weight: 700;
            color: #F8FAFC;
        }
        .aurora-kpi-sub {
            font-size: 8.5px;
            color: #CBD5E1;
            margin-top: 1px;
            white-space: nowrap;
        }
        .aurora-scale-section {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        .aurora-scale-bar {
            display: flex;
            width: 100%;
            height: 14px;
            border-radius: 3px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .aurora-scale-bar span {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 8.5px;
            font-weight: 600;
            color: #FFFFFF;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
        }
        .aurora-footer {
            display: flex;
            flex-direction: column;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            padding-top: 6px;
        }

        /* Animated Lightning Strike Markers */
        .lightning-marker-wrap {
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .lightning-flash-dot {
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: radial-gradient(circle, #FFFFFF 25%, #38BDF8 65%, rgba(56, 189, 248, 0) 100%);
            box-shadow: 0 0 10px #38BDF8, 0 0 20px #FFFFFF;
            animation: lightning-strike 1.6s ease-out infinite;
        }
        @keyframes lightning-strike {
            0% { transform: scale(0.3); opacity: 1.0; }
            40% { transform: scale(1.6); opacity: 0.9; }
            100% { transform: scale(1.0); opacity: 0.75; }
        }

        /* Floating ADS-B Live Flight Panel */
        .adsb-panel {
            position: absolute;
            top: 12px;
            right: 12px;
            z-index: 1000;
            padding: 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            width: 252px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .adsb-mode-bar {
            display: flex;
            background: #1C1C1E;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
        }
        .adsb-mode-pill {
            flex: 1;
            background: transparent;
            border: none;
            border-radius: 4px;
            color: #9CA3AF;
            font-size: 9px;
            font-weight: 600;
            padding: 3px 2px;
            cursor: pointer;
            text-align: center;
            transition: all 0.15s ease;
        }
        .adsb-mode-pill:hover {
            color: #FFFFFF;
            background: rgba(255, 255, 255, 0.06);
        }
        .adsb-mode-pill.active {
            background: #2D3748;
            color: #38BDF8;
            font-weight: 700;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
        }
        .adsb-photo-box {
            margin-top: 5px;
            border-top: 1px solid #374151;
            padding-top: 4px;
            text-align: center;
        }
        .adsb-photo-img {
            max-width: 100%;
            height: auto;
            max-height: 120px;
            border-radius: 4px;
            border: 1px solid #4B5563;
            display: block;
            margin: 0 auto;
        }
        .adsb-photo-meta {
            font-size: 8px;
            color: #9CA3AF;
            margin-top: 3px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .adsb-photo-link {
            color: #38BDF8;
            text-decoration: none;
        }
        .adsb-photo-link:hover {
            text-decoration: underline;
        }
        .adsb-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #414143;
            padding-bottom: 4px;
        }
        .adsb-title {
            font-size: 11px;
            font-weight: 700;
            color: #E5E7EB;
            display: flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .adsb-close-btn {
            background: transparent;
            border: none;
            color: #9CA3AF;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            padding: 0 2px;
            line-height: 1;
        }
        .adsb-close-btn:hover {
            color: #FFFFFF;
        }
        .adsb-target-box {
            display: flex;
            flex-direction: column;
            gap: 2px;
            background: #2B2F38;
            border: 1px solid #414143;
            border-radius: 6px;
            padding: 4px 7px;
        }
        .adsb-target-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 10px;
        }
        .adsb-target-name {
            color: #38BDF8;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 140px;
        }
        .adsb-radius-badge {
            background: #1C1C1E;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 1px 4px;
            font-size: 9px;
            color: #9CA3AF;
        }
        .adsb-reset-btn {
            background: #353A45;
            color: #E5E7EB;
            border: 1px solid #414143;
            border-radius: 4px;
            font-size: 9px;
            font-weight: 600;
            padding: 2px 6px;
            cursor: pointer;
            margin-top: 2px;
            text-align: center;
            transition: background 0.15s ease;
        }
        .adsb-reset-btn:hover {
            background: #464C5A;
            color: #FFFFFF;
        }
        .adsb-legend-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 9px;
            color: #9CA3AF;
            padding-top: 2px;
        }
        .adsb-legend-item {
            display: flex;
            align-items: center;
            gap: 3px;
        }
        .adsb-legend-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
        }
        .adsb-legend-distress-dot {
            background: #EF4444 !important;
            animation: adsb-distress-glow 2.2s infinite ease-in-out;
        }
        @keyframes adsb-distress-glow {
            0%, 100% {
                opacity: 0.35;
                box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
            }
            50% {
                opacity: 1;
                box-shadow: 0 0 8px 2px rgba(239, 68, 68, 0.85);
            }
        }
        .adsb-filter-card {
            background: rgba(26, 28, 32, 0.70);
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 5px 7px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .adsb-sub-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .adsb-sub-title {
            font-size: 8.5px;
            font-weight: 700;
            color: #9CA3AF;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }
        .adsb-sub-actions {
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 8.5px;
        }
        .adsb-link-btn {
            color: #38BDF8;
            text-decoration: none;
            cursor: pointer;
            font-weight: 600;
        }
        .adsb-link-btn:hover {
            text-decoration: underline;
            color: #7DD3FC;
        }
        .adsb-checkbox-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 3px 6px;
        }
        .adsb-chk-label {
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 9px;
            color: #E2E8F0;
            cursor: pointer;
            user-select: none;
        }
        .adsb-chk-label input[type="checkbox"] {
            margin: 0;
            cursor: pointer;
            accent-color: #38BDF8;
            width: 12px;
            height: 12px;
        }
        .adsb-switch-label {
            display: flex;
            align-items: center;
            gap: 4px;
            cursor: pointer;
            user-select: none;
        }
        .adsb-switch-label input[type="checkbox"] {
            margin: 0;
            cursor: pointer;
            accent-color: #10B981;
            width: 12px;
            height: 12px;
        }
        .adsb-switch-text {
            font-size: 8.5px;
            font-weight: 600;
            color: #34D399;
        }
        .adsb-plane-container {
            position: relative;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .adsb-distress-echo-ring {
            position: absolute;
            top: 50%;
            left: 50%;
            width: 24px;
            height: 24px;
            margin-top: -12px;
            margin-left: -12px;
            border-radius: 50%;
            border: 2px solid #EF4444;
            background: rgba(239, 68, 68, 0.15);
            box-sizing: border-box;
            pointer-events: none;
            animation: adsb-echo-pulse 2.4s infinite cubic-bezier(0.2, 0.6, 0.35, 1);
            z-index: 1;
        }
        .adsb-distress-echo-ring-2 {
            animation-delay: 1.2s;
        }
        @keyframes adsb-echo-pulse {
            0% {
                transform: scale(0.6);
                opacity: 0.95;
            }
            70% {
                transform: scale(3.2);
                opacity: 0.25;
            }
            100% {
                transform: scale(4.4);
                opacity: 0;
            }
        }
        .adsb-distress-tag {
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            margin-top: 3px;
            display: inline-flex;
            align-items: center;
            gap: 3px;
            background: rgba(220, 38, 38, 0.95);
            border: 1px solid #F87171;
            border-radius: 3px;
            color: #FFFFFF;
            font-size: 8px;
            font-weight: 700;
            letter-spacing: 0.3px;
            padding: 1px 4px;
            white-space: nowrap;
            box-shadow: 0 1px 4px rgba(0,0,0,0.8), 0 0 6px rgba(239, 68, 68, 0.5);
            pointer-events: none;
            z-index: 10;
        }
        .adsb-distress-beacon-dot {
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: #FFFFFF;
            animation: adsb-distress-glow 2.2s infinite ease-in-out;
            display: inline-block;
        }
        .adsb-distress-banner {
            display: flex;
            align-items: center;
            gap: 5px;
            background: rgba(185, 28, 28, 0.35);
            border: 1px solid #EF4444;
            border-radius: 4px;
            padding: 3px 6px;
            margin-bottom: 5px;
            font-size: 9.5px;
            font-weight: 600;
            color: #FCA5A5;
        }
        .adsb-plane-marker {
            cursor: pointer;
            position: relative;
            transform-origin: center center;
            transition: transform 0.2s ease, filter 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            pointer-events: auto !important;
            z-index: 2;
        }
        /* Enlarged invisible hit target ensuring clicks on or near the plane always register */
        .adsb-plane-marker::before {
            content: '';
            position: absolute;
            top: -8px;
            left: -8px;
            right: -8px;
            bottom: -8px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.001);
            pointer-events: auto !important;
        }
        .adsb-plane-marker:hover {
            filter: drop-shadow(0 0 6px #38BDF8) drop-shadow(0 0 2px #FFFFFF) !important;
        }
        .adsb-plane-marker.pinned {
            filter: drop-shadow(0 0 8px #38BDF8) drop-shadow(0 0 2px #38BDF8) !important;
        }
        .adsb-icon-wrap {
            background: transparent !important;
            border: none !important;
            overflow: visible !important;
            pointer-events: auto !important;
        }
        .adsb-radar-center-wrap {
            background: transparent !important;
            border: none !important;
            overflow: visible !important;
            pointer-events: none !important;
        }
        .adsb-radar-center-beacon {
            position: relative;
            width: 28px;
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .adsb-radar-center-dot {
            font-size: 16px;
            filter: drop-shadow(0 0 4px rgba(0, 0, 0, 0.9));
            z-index: 2;
        }
        .adsb-radar-center-pulse {
            position: absolute;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            border: 1.5px solid #38BDF8;
            background: rgba(56, 189, 248, 0.15);
            animation: adsb-radar-pulse 2.5s infinite ease-out;
            pointer-events: none;
            z-index: 1;
        }
        @keyframes adsb-radar-pulse {
            0% { transform: scale(0.3); opacity: 0.95; }
            100% { transform: scale(2.4); opacity: 0; }
        }
        .adsb-range-ring-label {
            background: transparent !important;
            border: none !important;
            pointer-events: none !important;
            white-space: nowrap;
        }
        .adsb-cluster-bar {
            display: flex;
            align-items: center;
            gap: 4px;
            padding: 3px 6px;
            background: #111827;
            border-radius: 4px;
            margin-bottom: 5px;
            border: 1px solid #374151;
            font-size: 9px;
            flex-wrap: wrap;
        }
        .adsb-cluster-pill {
            padding: 1px 5px;
            background: #1F2937;
            border: 1px solid #4B5563;
            border-radius: 3px;
            color: #D1D5DB;
            cursor: pointer;
            transition: background 0.15s, color 0.15s, border-color 0.15s;
        }
        .adsb-cluster-pill:hover {
            background: #374151;
            color: #FFFFFF;
            border-color: #38BDF8;
        }
        .adsb-cluster-pill.active {
            background: #0284C7;
            color: #FFFFFF;
            border-color: #38BDF8;
            font-weight: 600;
        }
        .adsb-tooltip {
            background: rgba(26, 28, 32, 0.96) !important;
            border: 1px solid #41444C !important;
            border-radius: 6px !important;
            color: #FFFFFF !important;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.65) !important;
            font-size: 10px !important;
            padding: 6px 9px !important;
            pointer-events: auto !important;
            user-select: text;
        }
        /* Hover bridge: transparent invisible zone extending downwards so moving mouse from marker to tooltip doesn't lose hover */
        .adsb-tooltip::after {
            content: '';
            position: absolute;
            bottom: -14px;
            left: -10px;
            right: -10px;
            height: 18px;
            background: transparent;
            pointer-events: auto !important;
        }
        .adsb-tooltip:before {
            border-top-color: #41444C !important;
        }
        .adsb-photo-placeholder {
            margin-top: 5px;
            margin-bottom: 2px;
            padding: 8px 6px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            background: rgba(31, 41, 55, 0.45);
            border-radius: 4px;
            border: 1px dashed #4B5563;
            box-sizing: border-box;
            min-height: 48px;
        }
        .adsb-photo-placeholder-icon {
            font-size: 15px;
            line-height: 1;
            margin-bottom: 3px;
            opacity: 0.55;
        }
        .adsb-photo-placeholder-text {
            font-size: 9.5px;
            color: #9CA3AF;
            font-weight: 500;
            letter-spacing: 0.2px;
        }
        .adsb-tip-close {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 15px;
            height: 15px;
            border-radius: 3px;
            background: rgba(255, 255, 255, 0.08);
            color: #9CA3AF;
            font-size: 10px;
            line-height: 1;
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
            margin-left: 8px;
            padding: 0;
            border: none;
        }
        .adsb-tip-close:hover {
            background: rgba(239, 68, 68, 0.5);
            color: #FFFFFF;
        }
        .adsb-cat-badge {
            display: inline-block;
            padding: 1px 5px;
            border-radius: 3px;
            font-size: 9px;
            font-weight: 600;
            background: #1F2937;
            border: 1px solid #374151;
            color: #38BDF8;
            margin-right: 3px;
        }
        .adsb-tip-link {
            color: #38BDF8;
            text-decoration: underline;
            font-size: 10px;
            display: inline-block;
            cursor: pointer;
        }
        .adsb-tip-link:hover {
            color: #7DD3FC;
        }
        .adsb-link-btn {
            display: block;
            padding: 4px 8px;
            background: #252830;
            border: 1px solid #3F4450;
            border-radius: 4px;
            color: #38BDF8;
            font-size: 11px;
            text-decoration: none;
            font-weight: 500;
            text-align: center;
            transition: background 0.15s, border-color 0.15s;
            cursor: pointer;
        }
        .adsb-link-btn:hover {
            background: #1D4ED8;
            border-color: #60A5FA;
            color: #FFFFFF;
        }
        @keyframes radar-sweep-spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        .adsb-radar-sweeper {
            transform-origin: 100px 100px;
            animation: radar-sweep-spin 5s linear infinite;
        }

        /* Floating Satellite Tracking Panel */
        .sat-panel {
            position: absolute;
            top: 12px;
            right: 12px;
            z-index: 1000;
            padding: 8px 10px 4px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            user-select: none;
            width: 280px;
            min-height: 200px;
            max-height: 90vh;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow: hidden;
            box-sizing: border-box;
            background: rgba(30, 31, 34, 0.96) !important;
            border: 1px solid #383A40 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
        }
        .sat-content-body {
            overflow: hidden;
            flex: 1;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding-right: 2px;
        }
        .sat-resize-handle {
            height: 10px;
            width: 100%;
            cursor: ns-resize;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-top: 2px;
            padding-bottom: 2px;
            flex-shrink: 0;
        }
        .sat-resize-handle:hover .sat-resize-grip-line {
            background-color: #5865F2;
        }
        .sat-resize-grip-line {
            width: 36px;
            height: 3px;
            border-radius: 1.5px;
            background-color: #4E5058;
            transition: background-color 0.15s ease;
        }
        .sat-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 4px;
            flex-shrink: 0;
        }
        .sat-title {
            font-size: 11px;
            font-weight: 700;
            color: #F2F3F5;
            display: flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .sat-close-btn {
            background: transparent;
            border: none;
            color: #949BA4;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            padding: 0 2px;
            line-height: 1;
        }
        .sat-close-btn:hover {
            color: #FFFFFF;
        }
        .sat-refresh-btn {
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 4px;
            color: #DBDEE1;
            font-size: 11px;
            cursor: pointer;
            padding: 2px 6px;
            line-height: 1;
        }
        .sat-refresh-btn:hover {
            background: #35373C;
            color: #FFFFFF;
        }
        .sat-group-bar {
            display: flex;
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
            flex-shrink: 0;
        }
        .sat-group-pill {
            flex: 1;
            background: transparent;
            border: none;
            border-radius: 4px;
            color: #949BA4;
            font-size: 9px;
            font-weight: 600;
            padding: 3px 2px;
            cursor: pointer;
            text-align: center;
            transition: all 0.15s ease;
        }
        .sat-group-pill:hover {
            color: #DBDEE1;
            background: #2B2D31;
        }
        .sat-group-pill.active {
            background: #35373C;
            color: #FFFFFF;
            font-weight: 700;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
        }
        .sat-search-input {
            background: #111214;
            border: 1px solid #383A40;
            border-radius: 4px;
            color: #DBDEE1;
            font-size: 10px;
            padding: 4px 6px;
            outline: none;
            width: 100%;
            box-sizing: border-box;
            flex-shrink: 0;
        }
        .sat-search-input:focus {
            border-color: #5865F2;
        }
        .sat-quick-list {
            display: flex;
            flex-direction: column;
            gap: 2px;
            flex: 1 1 auto;
            min-height: 90px;
            overflow-y: auto;
            background: #1E1F22;
            border: 1px solid #383A40;
            border-radius: 4px;
            padding: 2px;
        }
        .sat-item-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 3px 6px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 10px;
            color: #DBDEE1;
            transition: background 0.12s;
        }
        .sat-item-row:hover {
            background: #35373C;
            color: #FFFFFF;
        }
        .sat-item-row.selected {
            background: #35373C;
            border-left: 2.5px solid #5865F2;
            color: #FFFFFF;
            font-weight: 600;
        }
        .sat-in-view-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #23A55A;
            box-shadow: 0 0 6px #23A55A;
            margin-right: 4px;
            display: inline-block;
        }
        .sat-below-horizon-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #4E5058;
            margin-right: 4px;
            display: inline-block;
        }
        .sat-telemetry-card {
            background: #2B2D31;
            border: 1px solid #383A40;
            border-radius: 6px;
            padding: 6px 8px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 10px;
            flex-shrink: 0;
            max-height: 220px;
            overflow-y: auto;
        }
        .sat-telem-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #383A40;
            padding-bottom: 3px;
        }
        .sat-telem-name {
            font-weight: 700;
            color: #F2F3F5;
            font-size: 11px;
        }
        .sat-pass-badge {
            font-size: 9px;
            padding: 1px 5px;
            border-radius: 3px;
            font-weight: 700;
            text-transform: uppercase;
        }
        .sat-pass-badge.in-view {
            background: rgba(35, 165, 90, 0.2);
            border: 1px solid #23A55A;
            color: #23A55A;
            box-shadow: 0 0 6px rgba(35, 165, 90, 0.4);
        }
        .sat-pass-badge.below {
            background: rgba(78, 80, 88, 0.25);
            border: 1px solid #4E5058;
            color: #949BA4;
        }
        .sat-grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4px 8px;
            font-size: 9.5px;
        }
        .sat-metric-label {
            color: #949BA4;
            font-size: 8.5px;
            text-transform: uppercase;
        }
        .sat-metric-val {
            color: #F2F3F5;
            font-weight: 600;
            font-family: monospace;
        }
        .sat-freq-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 9px;
            margin-top: 2px;
        }
        .sat-freq-table th {
            text-align: left;
            color: #949BA4;
            border-bottom: 1px solid #383A40;
            padding: 2px 0;
            font-size: 8px;
            text-transform: uppercase;
        }
        .sat-freq-table td {
            padding: 2px 0;
            color: #DBDEE1;
        }
        .sat-doppler-pos {
            color: #23A55A;
            font-family: monospace;
            font-weight: 600;
        }
        .sat-doppler-neg {
            color: #F23F43;
            font-family: monospace;
            font-weight: 600;
        }
        .sat-options-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 9px;
            color: #949BA4;
            padding-top: 4px;
            border-top: 1px solid #383A40;
            margin-top: auto;
            flex-shrink: 0;
        }
        .sat-marker {
            cursor: pointer;
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .sat-marker-icon {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            box-shadow: 0 0 8px rgba(0, 0, 0, 0.8);
            transition: transform 0.15s;
        }
        .sat-marker:hover .sat-marker-icon, .sat-marker.selected .sat-marker-icon {
            transform: scale(1.25);
        }
        .sat-marker-stations .sat-marker-icon {
            background: #0284C7;
            border: 1.5px solid #38BDF8;
            box-shadow: 0 0 10px #38BDF8;
        }
        .sat-marker-amateur .sat-marker-icon {
            background: #059669;
            border: 1.5px solid #34D399;
            box-shadow: 0 0 10px #34D399;
        }
        .sat-marker-weather .sat-marker-icon {
            background: #D97706;
            border: 1.5px solid #FBBF24;
            box-shadow: 0 0 10px #FBBF24;
        }
        .sat-marker-cubesat .sat-marker-icon {
            background: #7C3AED;
            border: 1.5px solid #A855F7;
            box-shadow: 0 0 10px #A855F7;
        }
        .sat-marker-label {
            font-size: 8.5px;
            font-weight: 700;
            color: #FFFFFF;
            text-shadow: 0 1px 3px #000000, 0 0 4px #000000;
            white-space: nowrap;
            margin-top: 1px;
            pointer-events: none;
        }

        /* --- CoreScope Live Packet HUD & Legend Overlays --- */
        .live-overlay {
            position: absolute;
            z-index: 1000;
            background: rgba(30, 31, 34, 0.96) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border-radius: 8px !important;
            border: 1px solid #383A40 !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
            color: #DBDEE1;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow: hidden;
            transition: transform 0.2s ease, opacity 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .live-overlay:hover {
            border-color: #5865F2 !important;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.8), 0 0 12px rgba(88, 101, 242, 0.25) !important;
        }
        .live-overlay[data-position="bl"] {
            bottom: 16px;
            left: 14px;
            top: auto;
            right: auto;
        }
        .live-overlay[data-position="tl"] {
            top: 14px;
            left: 14px;
            bottom: auto;
            right: auto;
        }
        .live-overlay[data-position="tr"] {
            top: 14px;
            right: 14px;
            bottom: auto;
            left: auto;
        }
        .live-overlay[data-position="br"] {
            bottom: 16px;
            right: 14px;
            top: auto;
            left: auto;
        }
        .live-packet-hud {
            width: 390px;
            max-width: calc(100vw - 40px);
            display: flex;
            flex-direction: column;
        }
        .live-hud-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 7px 10px;
            background: #2B2D31;
            border-bottom: 1px solid #383A40;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
            color: #F2F3F5;
            cursor: grab;
            user-select: none;
            -webkit-user-select: none;
        }
        .live-hud-header:active {
            cursor: grabbing;
        }
        .live-hud-header-left, .live-hud-header-right {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .panel-corner-btn, .live-hud-btn {
            background: #1E1F22;
            border: 1px solid #383A40;
            color: #949BA4;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            padding: 2px 6px;
            line-height: 14px;
            transition: all 0.15s ease;
        }
        .panel-corner-btn:hover, .live-hud-btn:hover {
            background: #35373C;
            color: #FFFFFF;
            border-color: #4E5058;
        }
        .live-hud-close:hover {
            background: #ED4245 !important;
            color: #FFFFFF !important;
            border-color: #ED4245 !important;
        }
        .live-hud-content {
            background: #111214;
            max-height: min(460px, calc(100vh - 120px));
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            padding: 6px 8px;
            gap: 4px;
            /* Top fade removed so the newest incoming packets at the top remain 100% crisp and legible */
            mask-image: none;
            -webkit-mask-image: none;
        }
        .live-feed-empty {
            padding: 16px;
            text-align: center;
            font-size: 11px;
            color: #949BA4;
            font-style: italic;
        }
        .live-feed-item {
            color: #DBDEE1;
            background: #1E1F22;
            border: 1px solid #2B2D31;
            font-size: 11.5px;
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            padding: 5px 8px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            gap: 7px;
            transition: background 0.15s ease, transform 0.15s ease, border-color 0.15s ease;
            overflow: hidden;
            cursor: pointer;
            border-left: 3px solid transparent;
            white-space: nowrap;
            flex-shrink: 0;
            min-height: 28px;
            line-height: 1.4;
            box-sizing: border-box;
        }
        .live-feed-item:hover {
            background: #2B2D31;
            border-color: #383A40;
            color: #FFFFFF;
        }
        .feed-icon {
            font-size: 13px;
            flex-shrink: 0;
            line-height: 1;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .feed-src {
            border-radius: 3px;
            padding: 1px 4px;
            font-size: 9px;
            font-weight: 700;
            line-height: 1.2;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            letter-spacing: 0.3px;
        }
        .feed-src-rf {
            background: rgba(16, 185, 129, 0.18);
            color: #10B981;
            border: 1px solid #059669;
        }
        .feed-src-mqtt {
            background: rgba(245, 158, 11, 0.18);
            color: #F59E0B;
            border: 1px solid #D97706;
        }
        .feed-type {
            font-weight: 700;
            font-size: 10px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            flex-shrink: 0;
            line-height: 1.2;
        }
        .feed-hops {
            font-size: 9.5px;
            font-weight: 600;
            color: #CBD5E1;
            background: rgba(255, 255, 255, 0.1);
            padding: 1px 4px;
            border-radius: 3px;
            flex-shrink: 0;
            line-height: 1.2;
            display: inline-flex;
            align-items: center;
            margin-right: 4px;
            gap: 2px;
        }
        .feed-text {
            color: #CBD5E1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex: 1;
            min-width: 0;
            font-size: 11px;
            line-height: 1.35;
        }
        .feed-time {
            font-size: 10px;
            color: #64748B;
            flex-shrink: 0;
            margin-left: auto;
            padding-left: 6px;
            line-height: 1.35;
        }

        /* Legend styling (Screenshot 1) */
        .live-legend {
            width: 260px;
            padding: 0;
            background: rgba(30, 31, 34, 0.96) !important;
            border: 1px solid #383A40 !important;
            border-radius: 8px !important;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.65) !important;
        }
        .live-legend-content {
            background: #111214;
            padding: 8px 10px 10px 10px;
        }
        .legend-section-title {
            font-size: 9.5px;
            font-weight: 700;
            color: #949BA4;
            letter-spacing: 0.8px;
            margin-bottom: 6px;
        }
        .legend-list {
            list-style: none;
            margin: 0;
            padding: 0;
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        .legend-list li {
            display: flex;
            align-items: center;
            font-size: 11px;
            gap: 7px;
            color: #E2E8F0;
        }
        .legend-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
            box-shadow: 0 0 6px currentColor;
        }
        .legend-name {
            font-weight: 600;
            color: #F8FAFC;
        }
        .legend-desc {
            color: #94A3B8;
            font-size: 10.5px;
        }
    </style>
</head>
<body>
    <div id="map"></div>
    <div id="map-3d" style="display: none; position: absolute; top: 0; left: 0; width: 100%; height: 100%;"></div>

    <!-- 3D Map Perspective Controls Overlay -->
    <div id="map3dControls" class="map-overlay-panel" style="display: none; position: absolute; top: 12px; right: 12px; z-index: 10000; background: rgba(18, 21, 26, 0.92); border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.6); backdrop-filter: blur(8px); min-width: 220px; flex-direction: column;">
        <div id="map3dControlsHeader" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; cursor: grab; user-select: none;" title="Drag to move panel">
            <span style="font-weight: 700; font-size: 12px; color: #58a6ff; display: flex; align-items: center; gap: 6px; pointer-events: none;">
                <span style="opacity: 0.6; font-size: 10px;">⠿</span> <span>🏔️</span> 3D Terrain Perspective
            </span>
            <button onclick="close3DMode()" style="background: none; border: none; color: #8b949e; cursor: pointer; font-size: 14px; padding: 2px 6px;">✕</button>
        </div>
        <div style="font-size: 10.5px; color: #8b949e; line-height: 1.4; margin-bottom: 8px;">
            Hold <b>Middle-click</b> to tilt camera & orbit in 3D. <b>Right-click</b> for Node/Map Menu.
        </div>
        <div style="display: flex; gap: 6px; margin-bottom: 8px;">
            <button id="btn-3d-pitch-58" onclick="set3DPitch(58)" class="btn-3d-preset active" style="flex: 1; padding: 4px 6px; font-size: 10px; font-weight: 600; border-radius: 4px; border: 1px solid #00D2FF; background: #0C4A6E; color: #38BDF8; cursor: pointer;">📐 58° Pitch</button>
            <button id="btn-3d-pitch-0" onclick="set3DPitch(0)" class="btn-3d-preset" style="flex: 1; padding: 4px 6px; font-size: 10px; font-weight: 600; border-radius: 4px; border: 1px solid #30363d; background: #161b22; color: #8b949e; cursor: pointer;">🧭 Top-down</button>
        </div>
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: #c9d1d9;">
            <span>Terrain Relief:</span>
            <select id="select-3d-exaggeration" onchange="set3DExaggeration(this.value)" style="background: #161b22; border: 1px solid #30363d; color: #58a6ff; font-size: 10px; border-radius: 4px; padding: 2px 6px;">
                <option value="1.0">1.0x (Real)</option>
                <option value="2.0">2.0x (Standard)</option>
                <option value="2.5" selected>2.5x (UKMesh)</option>
                <option value="3.5">3.5x (Dramatic)</option>
            </select>
        </div>
    </div>

    <!-- Floating Live Packet Feed HUD Overlay -->
    <div id="livePacketHud" class="live-overlay live-packet-hud" data-position="bl" style="display: none;">
        <div class="live-hud-header" id="liveHudDragHandle">
            <div class="live-hud-header-left">
                <button class="panel-corner-btn" onclick="cycleHudCorner()" title="Move HUD to next corner" aria-label="Move HUD to next corner">◫</button>
                <span class="live-hud-title">⚡ LIVE PACKET FEED</span>
            </div>
            <div class="live-hud-header-right">
                <button class="live-hud-btn" onclick="toggleMapLegend()" title="Toggle Types & Roles Legend">🎨 Legend</button>
                <button class="live-hud-btn live-hud-close" onclick="setPacketHudVisible(false)" title="Close HUD">✕</button>
            </div>
        </div>
        <div class="live-hud-content" id="liveHudContent">
            <div class="live-feed-empty" id="liveHudEmpty">Waiting for live packets…</div>
        </div>
    </div>

    <!-- Floating Map Legend Overlay (Screenshot 1) -->
    <div id="liveLegend" class="live-overlay live-legend" data-position="br" style="display: none;">
        <div class="live-hud-header" id="liveLegendDragHandle">
            <span class="live-hud-title">MAP LEGEND</span>
            <button class="live-hud-btn live-hud-close" onclick="toggleMapLegend(false)" title="Close Legend">✕</button>
        </div>
        <div class="live-legend-content">
            <div class="legend-section-title">PACKET TYPES</div>
            <ul class="legend-list">
                <li><span class="legend-dot" style="background:#22C55E"></span> <span class="legend-name">Advert</span> <span class="legend-desc">— Node advertisement</span></li>
                <li><span class="legend-dot" style="background:#3B82F6"></span> <span class="legend-name">Message</span> <span class="legend-desc">— Group text</span></li>
                <li><span class="legend-dot" style="background:#F59E0B"></span> <span class="legend-name">Direct</span> <span class="legend-desc">— Direct message</span></li>
                <li><span class="legend-dot" style="background:#A855F7"></span> <span class="legend-name">Request</span> <span class="legend-desc">— Data request</span></li>
                <li><span class="legend-dot" style="background:#EC4899"></span> <span class="legend-name">Trace</span> <span class="legend-desc">— Route trace</span></li>
            </ul>
            <div class="legend-section-title" style="margin-top:10px;">NODE ROLES</div>
            <ul class="legend-list">
                <li><span class="legend-dot" style="background:#3B82F6"></span> <span class="legend-name">Repeater</span></li>
                <li><span class="legend-dot" style="background:#06B6D4"></span> <span class="legend-name">Companion</span></li>
                <li><span class="legend-dot" style="background:#A855F7"></span> <span class="legend-name">Room</span></li>
                <li><span class="legend-dot" style="background:#F97316"></span> <span class="legend-name">MQTT Ingest</span> <span class="legend-desc">— (In MQTT View)</span></li>
            </ul>
            <div class="legend-section-title" style="margin-top:10px;">HOP VERIFICATION</div>
            <ul class="legend-list">
                <li><span style="display:inline-block;width:22px;height:2px;background:#34D399;margin-right:6px;vertical-align:middle;"></span> <span class="legend-name">Solid</span> <span class="legend-desc">— Verified RF Hop</span></li>
                <li><span style="display:inline-block;width:22px;height:0;border-top:2px dashed #FACC15;margin-right:6px;vertical-align:middle;"></span> <span class="legend-name">Dashed</span> <span class="legend-desc">— Ambiguous Hop (Collision)</span></li>
                <li><span style="display:inline-block;width:22px;height:0;border-top:2px dotted #94A3B8;margin-right:6px;vertical-align:middle;"></span> <span class="legend-name">Dotted</span> <span class="legend-desc">— Inferred Step (No GPS/MQTT)</span></li>
            </ul>
        </div>
    </div>
    <div id="map-loading-hud" class="map-loading-hud hidden">
        <span class="loading-spinner"></span>
        <span id="loading-hud-text">Initializing MeshCore Map &amp; RF Services...</span>
    </div>
    <div id="adsb-panel" class="adsb-panel map-overlay-panel" style="display: none;">
        <div class="adsb-header map-overlay-header" id="adsb-drag-handle">
            <span class="adsb-title"><span class="map-drag-handle-grip">⠿</span>✈️ ADS-B FLIGHTS <span id="adsb-count-badge" class="scope-pill-count">0</span></span>
            <button class="adsb-close-btn" onclick="if (window.pyBridge && window.pyBridge.on_adsb_toggled) window.pyBridge.on_adsb_toggled(false)" title="Close ADS-B Layer">×</button>
        </div>
        <div class="adsb-mode-bar">
            <button id="adsb-mode-alt" class="adsb-mode-pill active" onclick="setAdsbColorMode('altitude')">🏔️ Altitude</button>
            <button id="adsb-mode-type" class="adsb-mode-pill" onclick="setAdsbColorMode('type')">✈️ Type</button>
            <button id="adsb-mode-dist" class="adsb-mode-pill" onclick="setAdsbColorMode('distance')">📏 Distance</button>
        </div>
        <div class="adsb-target-box">
            <div class="adsb-target-row">
                <span>🎯 <span id="adsb-target-label" class="adsb-target-name">Local Node</span></span>
                <span id="adsb-radius-label" class="adsb-radius-badge">50 NM</span>
            </div>
            <button id="adsb-reset-btn" class="adsb-reset-btn" onclick="if (window.pyBridge && window.pyBridge.on_reset_adsb_target) window.pyBridge.on_reset_adsb_target()" style="display: none;">↺ Reset to Local Node</button>
        </div>

        <!-- Aircraft Type Display Filter Checkboxes -->
        <div class="adsb-filter-card">
            <div class="adsb-sub-header">
                <span class="adsb-sub-title">FILTER TYPES</span>
                <span class="adsb-sub-actions">
                    <a href="javascript:void(0)" onclick="setAllAdsbFilters(true)" class="adsb-link-btn">All</a>
                    <span style="color:#4B5563;">•</span>
                    <a href="javascript:void(0)" onclick="setAllAdsbFilters(false)" class="adsb-link-btn">None</a>
                </span>
            </div>
            <div class="adsb-checkbox-grid">
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-airliner" checked onchange="updateAdsbFilters()"> ✈️ Airliner</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-light" checked onchange="updateAdsbFilters()"> 🛩️ Light</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-military" checked onchange="updateAdsbFilters()"> ⚔️ Military</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-helicopter" checked onchange="updateAdsbFilters()"> 🚁 Helicopter</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-glider" checked onchange="updateAdsbFilters()"> 🪂 Glider</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-flt-general" checked onchange="updateAdsbFilters()"> ✈️ General</label>
            </div>
        </div>

        <!-- Proximity Alert & Auto-Popup (<10 miles) -->
        <div class="adsb-filter-card adsb-alert-card">
            <div class="adsb-sub-header">
                <span class="adsb-sub-title">🔔 PROXIMITY ALERT (&lt;10 MI)</span>
                <label class="adsb-switch-label" title="Enable automatic popup and desktop notification for nearby watched aircraft">
                    <input type="checkbox" id="adsb-alert-enabled" checked onchange="updateAdsbAlertConfig()">
                    <span class="adsb-switch-text">Active</span>
                </label>
            </div>
            <div class="adsb-checkbox-grid">
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-military" checked onchange="updateAdsbAlertConfig()"> ⚔️ Military</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-helicopter" onchange="updateAdsbAlertConfig()"> 🚁 Helicopter</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-light" onchange="updateAdsbAlertConfig()"> 🛩️ Light</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-airliner" onchange="updateAdsbAlertConfig()"> ✈️ Airliner</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-glider" onchange="updateAdsbAlertConfig()"> 🪂 Glider</label>
                <label class="adsb-chk-label"><input type="checkbox" id="adsb-alert-general" onchange="updateAdsbAlertConfig()"> ✈️ General</label>
            </div>
        </div>

        <div id="adsb-legend-container"></div>
    </div>
    <!-- Floating Satellite Tracker Panel -->
    <div id="satellite-panel" class="sat-panel map-overlay-panel" style="display: none;">
        <div class="sat-header map-overlay-header" id="satellite-drag-handle">
            <span class="sat-title"><span class="map-drag-handle-grip">⠿</span>🛰️ SATELLITE TRACKER <span id="sat-count-badge" class="scope-pill-count">0</span></span>
            <div style="display: flex; align-items: center; gap: 4px;">
                <button class="sat-refresh-btn" onclick="if (window.pyBridge && window.pyBridge.on_satellite_refresh) window.pyBridge.on_satellite_refresh()" title="Sync CelesTrak TLEs">↺</button>
                <button class="sat-close-btn" onclick="if (window.pyBridge && window.pyBridge.on_satellite_toggled) window.pyBridge.on_satellite_toggled(false)" title="Close Satellite Layer">×</button>
            </div>
        </div>
        <div class="sat-content-body">
            <div class="sat-group-bar">
                <button id="sat-grp-all" class="sat-group-pill active" onclick="setSatelliteGroupFilter('all')">All</button>
                <button id="sat-grp-stations" class="sat-group-pill" onclick="setSatelliteGroupFilter('stations')">Stations</button>
                <button id="sat-grp-amateur" class="sat-group-pill" onclick="setSatelliteGroupFilter('amateur')">Amateur</button>
                <button id="sat-grp-weather" class="sat-group-pill" onclick="setSatelliteGroupFilter('weather')">Weather</button>
                <button id="sat-grp-cubesat" class="sat-group-pill" onclick="setSatelliteGroupFilter('cubesat')">Cubesats</button>
            </div>
            <input type="text" id="sat-search-input" class="sat-search-input" placeholder="Filter satellite or NORAD ID..." oninput="filterSatellites(this.value)" />
            <div id="sat-quick-list" class="sat-quick-list"></div>
            <div id="sat-telemetry-box" class="sat-telemetry-card" style="display: none;">
                <div class="sat-telem-header">
                    <span id="sat-telem-name" class="sat-telem-name">--</span>
                    <span id="sat-pass-badge" class="sat-pass-badge below">BELOW HORIZON</span>
                </div>
                <div class="sat-grid-2">
                    <div>
                        <div class="sat-metric-label">Sub-Sat Lat/Lon</div>
                        <div id="sat-telem-latlon" class="sat-metric-val">--°, --°</div>
                    </div>
                    <div>
                        <div class="sat-metric-label">Altitude / Speed</div>
                        <div id="sat-telem-alt-spd" class="sat-metric-val">-- km • -- km/s</div>
                    </div>
                    <div>
                        <div class="sat-metric-label">Azimuth / Elevation</div>
                        <div id="sat-telem-az-el" class="sat-metric-val">--° • --°</div>
                    </div>
                    <div>
                        <div class="sat-metric-label">Slant Range</div>
                        <div id="sat-telem-range" class="sat-metric-val">-- km</div>
                    </div>
                </div>
                <div id="sat-freqs-container"></div>
                <div style="display: flex; justify-content: flex-end; margin-top: 2px;">
                    <button class="adsb-reset-btn" onclick="centerOnSelectedSatellite()" style="margin: 0; font-size: 8.5px; padding: 2px 6px;">🎯 Center on Satellite</button>
                </div>
            </div>
            <div class="sat-options-bar">
                <label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">
                    <input type="checkbox" id="sat-chk-footprint" checked onchange="toggleSatFootprint(this.checked)" /> Horizon Footprint
                </label>
                <label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">
                    <input type="checkbox" id="sat-chk-track" checked onchange="toggleSatGroundTrack(this.checked)" /> 90m Orbit Track
                </label>
            </div>
        </div>
        <div id="satellite-resize-handle" class="sat-resize-handle" title="Drag to resize vertically">
            <div class="sat-resize-grip-line"></div>
        </div>
    </div>
    <div id="scope-filter-bar" class="scope-filter-bar map-overlay-panel" style="display: none;">
        <div class="map-overlay-header" id="scope-filter-drag-handle" style="margin: -8px -10px 4px -10px; padding: 4px 8px;">
            <div class="scope-filter-title" style="border-bottom:none; margin:0; padding:0; display:flex; align-items:center; gap:4px;"><span class="map-drag-handle-grip">⠿</span>🌐 Scopes</div>
            <button class="tropo-legend-close" onclick="document.getElementById('scope-filter-bar').style.display='none'" title="Hide Scopes" style="font-size:12px; padding:0 2px;">×</button>
        </div>
        <div class="scope-options-bar">
            <label class="scope-chk-label">
                <input type="checkbox" id="scope-chk-prune" onchange="toggleScopePrune(this.checked)" /> 🧹 Prune Empty (0 nodes)
            </label>
            <label class="scope-chk-label">
                <input type="checkbox" id="scope-chk-highlight" onchange="toggleScopeHighlight(this.checked)" /> ✨ Highlight Scopes
            </label>
        </div>
        <div id="scope-pills-container" class="scope-pills-container"></div>
    </div>
    <div id="orbital-tactical-tooltip" class="orbital-tactical-tooltip"></div>
    <div id="path-mode-legend" class="path-mode-legend map-overlay-panel" style="display: none;">
        <div class="map-overlay-header" id="path-mode-drag-handle" style="margin: -8px -10px 4px -10px; padding: 4px 8px; cursor: move;">
            <span style="font-size: 11px; font-weight: 700; color: #F2F3F5; display: flex; align-items: center; gap: 4px;"><span class="map-drag-handle-grip">⠿</span>🧭 PATH MODES</span>
            <button class="tropo-legend-close" onclick="document.getElementById('path-mode-legend').style.display='none'" title="Close Path Modes" style="font-size: 12px; padding: 0 2px;">×</button>
        </div>
        <div class="path-mode-row">
            <div class="path-mode-left">
                <span class="path-legend-dot" style="background: #EF4444; box-shadow: 0 0 6px #EF4444;"></span>
                <div class="path-mode-info">
                    <span class="path-mode-name">1-Byte Path</span>
                    <span class="path-mode-desc">Direct next-hop single byte ID</span>
                </div>
            </div>
            <span id="path-count-1byte" class="path-stat-badge">--</span>
        </div>
        <div class="path-mode-row">
            <div class="path-mode-left">
                <span class="path-legend-dot" style="background: #00D2FF; box-shadow: 0 0 6px #00D2FF;"></span>
                <div class="path-mode-info">
                    <span class="path-mode-name">2-Byte Path</span>
                    <span class="path-mode-desc">Extended dual-byte address hash</span>
                </div>
            </div>
            <span id="path-count-2byte" class="path-stat-badge">--</span>
        </div>
        <div class="path-mode-row">
            <div class="path-mode-left">
                <span class="path-legend-dot" style="background: #00FF7F; box-shadow: 0 0 6px #00FF7F;"></span>
                <div class="path-mode-info">
                    <span class="path-mode-name">3-Byte Multibyte Path</span>
                    <span class="path-mode-desc">Multi-hop 3-byte routing identifier</span>
                </div>
            </div>
            <span id="path-count-3byte" class="path-stat-badge">--</span>
        </div>
        <div class="path-mode-row">
            <div class="path-mode-left">
                <span class="path-legend-dot" style="background: #6B7280;"></span>
                <div class="path-mode-info">
                    <span class="path-mode-name">Direct / Flood</span>
                    <span class="path-mode-desc">Broadcast or unrouted packet</span>
                </div>
            </div>
            <span id="path-count-direct" class="path-stat-badge">--</span>
        </div>
        <div class="path-options-bar">
            <label class="path-chk-label">
                <input type="checkbox" id="chk-path-vectors" checked onchange="togglePathVectors(this.checked)" /> Show Link Direction Vectors
            </label>
            <label class="path-chk-label">
                <input type="checkbox" id="chk-path-multihop" onchange="togglePathMultihop(this.checked)" /> Multi-hop Paths Only
            </label>
        </div>
    </div>
    <div id="tropo-legend-panel" class="tropo-legend-panel map-overlay-panel">
        <div class="tropo-legend-header map-overlay-header" id="tropo-drag-handle">
            <span class="tropo-legend-title"><span class="map-drag-handle-grip">⠿</span>📡 TROPO FORECAST</span>
            <button class="tropo-legend-close" onclick="if (window.pyBridge && window.pyBridge.on_tropo_toggled) window.pyBridge.on_tropo_toggled(false)" title="Close Tropo Overlay">×</button>
        </div>
        <div class="tropo-stepper">
            <button class="tropo-step-btn" onclick="if (window.pyBridge && window.pyBridge.on_tropo_stepped) window.pyBridge.on_tropo_stepped(-3)" title="Previous 3h">◀</button>
            <span id="tropo-time-label" class="tropo-time-label">--:-- UTC</span>
            <button class="tropo-step-btn" onclick="if (window.pyBridge && window.pyBridge.on_tropo_stepped) window.pyBridge.on_tropo_stepped(3)" title="Next 3h">▶</button>
        </div>
        <div class="tropo-scale-bar" style="height: 4px; border-radius: 2px; margin: 4px 0 6px 0; background: linear-gradient(to right, #C084FC, #8B5CF6, #10B981, #FBBF24, #EF4444, #FFFFFF);"></div>
        <div class="tropo-tier-list">
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #FFFFFF; box-shadow: 0 0 6px #FFFFFF;"></span>
                    <span class="tropo-tier-name">Extreme</span>
                </div>
                <span class="tropo-tier-range">>110</span>
            </div>
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #EF4444; box-shadow: 0 0 5px #EF4444;"></span>
                    <span class="tropo-tier-name">Strong</span>
                </div>
                <span class="tropo-tier-range">90 - 110</span>
            </div>
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #FBBF24; box-shadow: 0 0 5px #FBBF24;"></span>
                    <span class="tropo-tier-name">Good</span>
                </div>
                <span class="tropo-tier-range">70 - 90</span>
            </div>
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #10B981; box-shadow: 0 0 5px #10B981;"></span>
                    <span class="tropo-tier-name">Moderate</span>
                </div>
                <span class="tropo-tier-range">60 - 70</span>
            </div>
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #8B5CF6;"></span>
                    <span class="tropo-tier-name">Fair</span>
                </div>
                <span class="tropo-tier-range">55 - 60</span>
            </div>
            <div class="tropo-tier-item">
                <div class="tropo-tier-left">
                    <span class="tropo-tier-dot" style="background: #C084FC;"></span>
                    <span class="tropo-tier-name">Marginal</span>
                </div>
                <span class="tropo-tier-range">43 - 55</span>
            </div>
        </div>
        <div class="tropo-legend-footer">
            <span>NOAA GFS Refractivity Index • F5LEN</span>
        </div>
    </div>
    <div id="visualised-floating-panel" class="floating-route-panel map-overlay-panel">
        <div class="floating-route-header map-overlay-header" id="floating-route-drag-handle">
            <div class="floating-route-title">
                <span class="map-drag-handle-grip">⠿</span>
                <span id="floating-route-title-text">📍 Visualised Route</span>
            </div>
            <button class="floating-route-close" id="floating-route-close-btn" title="Close Route (Esc)">×</button>
        </div>
        <div class="floating-route-body" id="floating-route-body-content"></div>
    </div>
    <!-- Floating Node Activity Heatmap Bar -->
    <div id="activity-heatmap-bar" class="activity-heatmap-bar map-overlay-panel" style="display: none;">
        <div class="map-overlay-header" id="activity-heatmap-drag-handle" style="margin: -8px -10px 4px -10px; padding: 4px 8px;">
            <div class="activity-bar-title"><span class="map-drag-handle-grip">⠿</span>🔥 NODE ACTIVITY</div>
            <button class="activity-bar-close" onclick="closeActivityHeatmap()" title="Close Activity Heatmap">×</button>
        </div>
        <div class="activity-btn-group">
            <button id="act-btn-1h" class="activity-tf-btn active" onclick="setActivityTimeframe(1)">1 hour</button>
            <button id="act-btn-6h" class="activity-tf-btn" onclick="setActivityTimeframe(6)">6 hours</button>
            <button id="act-btn-24h" class="activity-tf-btn" onclick="setActivityTimeframe(24)">24 hours</button>
        </div>
        <div class="act-options-bar">
            <label class="act-chk-label">
                <input type="checkbox" id="act-chk-relative" checked onchange="toggleActivityRelative(this.checked)" /> Relative % to peak node
            </label>
            <label class="act-chk-label">
                <input type="checkbox" id="act-chk-scaling" checked onchange="toggleActivityScaling(this.checked)" /> Scale node size by traffic
            </label>
        </div>
        <div class="act-peak-card">
            <span style="color: #949BA4;">Peak: <span id="act-peak-node-val" style="font-family: monospace; color: #23A55A; font-weight: 700;">--</span></span>
            <span style="color: #949BA4;">Traffic: <span id="act-peak-packets-val" style="color: #F2F3F5; font-weight: 700;">-- pkts</span></span>
        </div>
        <div class="activity-legend">
            <div class="act-leg-item"><span><span class="act-dot" style="background: #EF4444; box-shadow: 0 0 5px #EF4444;"></span><span id="act-lbl-vhigh">> 75% (Peak)</span></span><span id="act-count-vhigh" style="color: #949BA4; font-size: 8.5px;">--</span></div>
            <div class="act-leg-item"><span><span class="act-dot" style="background: #FB923C;"></span><span id="act-lbl-high">50 - 75% (High)</span></span><span id="act-count-high" style="color: #949BA4; font-size: 8.5px;">--</span></div>
            <div class="act-leg-item"><span><span class="act-dot" style="background: #FACC15;"></span><span id="act-lbl-med">25 - 50% (Med)</span></span><span id="act-count-med" style="color: #949BA4; font-size: 8.5px;">--</span></div>
            <div class="act-leg-item"><span><span class="act-dot" style="background: #10B981;"></span><span id="act-lbl-low">10 - 25% (Low)</span></span><span id="act-count-low" style="color: #949BA4; font-size: 8.5px;">--</span></div>
            <div class="act-leg-item"><span><span class="act-dot" style="background: #00D2FF; box-shadow: 0 0 4px #00D2FF;"></span><span id="act-lbl-min">< 10% (Base)</span></span><span id="act-count-min" style="color: #949BA4; font-size: 8.5px;">--</span></div>
        </div>
    </div>
    <!-- Floating New Nodes Discovery Panel -->
    <div id="new-nodes-panel" class="new-nodes-panel map-overlay-panel" style="display: none;">
        <div class="map-overlay-header new-nodes-header" id="new-nodes-drag-handle">
            <div class="new-nodes-title"><span class="map-drag-handle-grip">⠿</span>👋 NEW NODES DISCOVERY</div>
            <button class="new-nodes-close-btn" onclick="closeNewNodes()" title="Close New Nodes View">×</button>
        </div>
        <div class="new-nodes-btn-group">
            <button id="nn-btn-24h" class="new-nodes-tf-btn" onclick="setNewNodesTimeframe(24)">1 day</button>
            <button id="nn-btn-72h" class="new-nodes-tf-btn active" onclick="setNewNodesTimeframe(72)">3 days</button>
            <button id="nn-btn-168h" class="new-nodes-tf-btn" onclick="setNewNodesTimeframe(168)">1 week</button>
            <button id="nn-btn-336h" class="new-nodes-tf-btn" onclick="setNewNodesTimeframe(336)">2 weeks</button>
        </div>
        <div class="new-nodes-stats-card">
            <span style="color: #949BA4;">Discovered: <span id="nn-discovered-count" style="font-family: monospace; color: #FFD700; font-weight: 700;">0 nodes</span></span>
            <span style="color: #949BA4;">Total Plotted: <span id="nn-total-count" style="color: #F2F3F5; font-weight: 700;">0</span></span>
        </div>
        <button class="new-nodes-action-btn" onclick="markAllNodesKnown()" title="Mark all currently discovered nodes as known and start new discovery from now">✓ Mark All Known (Start From Now)</button>
    </div>
    <!-- Floating MQTT Ingest Nodes Panel -->
    <div id="mqtt-nodes-panel" class="mqtt-nodes-panel map-overlay-panel" style="display: none;">
        <div class="map-overlay-header mqtt-nodes-header" id="mqtt-nodes-drag-handle">
            <div class="mqtt-nodes-title"><span class="map-drag-handle-grip">⠿</span>🌐 MQTT DISCOVERED NODES</div>
            <button class="mqtt-nodes-close-btn" onclick="closeMqttNodes()" title="Close MQTT Nodes View">×</button>
        </div>
        <div class="mqtt-nodes-stats-card">
            <span style="color: #949BA4;">MQTT Ingest: <span id="mqtt-discovered-count" style="font-family: monospace; color: #FB923C; font-weight: 700;">0 nodes</span></span>
            <span style="color: #949BA4;">Total Plotted: <span id="mqtt-total-count" style="color: #F2F3F5; font-weight: 700;">0</span></span>
        </div>
        <div class="mqtt-nodes-desc">
            Highlighting nodes discovered via MQTT broker feeds in solid orange. RF-only nodes are dimmed.
        </div>
    </div>
    <!-- Floating Thunderstorm & Radar Panel -->
    <div id="thunderstorm-panel" class="thunderstorm-panel map-overlay-panel" style="display: none;">
        <div class="thunderstorm-header map-overlay-header" id="thunderstorm-drag-handle">
            <span class="thunderstorm-title"><span class="map-drag-handle-grip">⠿</span>🌩️ THUNDERSTORMS <span id="strike-count-badge" class="scope-pill-count">0 strikes</span></span>
            <button class="thunderstorm-close-btn" onclick="if (window.pyBridge && window.pyBridge.on_thunderstorm_toggled) window.pyBridge.on_thunderstorm_toggled(false)" title="Close Thunderstorm Layer">×</button>
        </div>
        <div class="thunder-stepper">
            <button class="thunder-step-btn" onclick="stepThunderstormRadar(-1)" title="Previous radar frame">◀</button>
            <span id="thunder-radar-label" class="thunder-time-label">Radar: Live Feed</span>
            <button class="thunder-step-btn" onclick="stepThunderstormRadar(1)" title="Next radar frame">▶</button>
        </div>
        <div id="thunder-proximity-badge" class="thunder-proximity-badge" style="display: none;">
            ⚠️ Proximity Alert: Strike within 25 mi
        </div>
        <div class="thunder-nearest-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 9px; font-weight: 700; color: #DBDEE1;">⚡ Nearest Strike</span>
                <span id="thunder-nearest-time" style="font-size: 8.5px; color: #949BA4;">-- ago</span>
            </div>
            <div id="thunder-nearest-val" style="font-size: 11px; font-weight: 700; color: #FBBF24;">-- mi (Bearing --°)</div>
            <button class="thunder-jump-btn" onclick="jumpToNearestStrike()">🎯 Jump to Nearest Strike</button>
        </div>
        <div class="thunder-stat-grid">
            <div class="thunder-stat-box">
                <div class="thunder-stat-label">Strike Rate</div>
                <div id="thunder-stat-rate" class="thunder-stat-val">0 / min</div>
            </div>
            <div class="thunder-stat-box">
                <div class="thunder-stat-label">Distance</div>
                <div id="thunder-stat-dist" class="thunder-stat-val">-- mi</div>
            </div>
        </div>
        <div class="tropo-legend-footer">
            <span>RainViewer Radar • Blitzortung Live Feed</span>
        </div>
    </div>
    <!-- Floating Search Node IDs Panel -->
    <div id="search-node-id-panel" class="search-node-id-panel map-overlay-panel" style="display: none;">
        <div class="map-overlay-header" id="search-node-drag-handle" style="margin: -8px -10px 4px -10px; padding: 4px 8px;">
            <span style="font-size: 11px; font-weight: 700; color: #23A55A; display: flex; align-items: center; gap: 4px;"><span class="map-drag-handle-grip">⠿</span>🔍 SEARCH NODE IDS</span>
            <button class="tropo-legend-close" onclick="closeSearchNodeIdPanel()" title="Close Search Panel" style="font-size: 12px; padding: 0 2px;">×</button>
        </div>
        <input type="text" id="search-node-input" class="search-node-input" placeholder="Type Node ID or prefix (e.g. !a1b2, 4f)..." oninput="searchNodeIds(this.value)" autocomplete="off" spellcheck="false" />
        <div class="search-node-status">
            <span id="search-node-status-text">Showing all nodes</span>
            <span id="search-node-count-badge" class="scope-pill-count">0</span>
        </div>
        <div id="search-node-results" class="search-node-results"></div>
    </div>
    <!-- Floating Space Weather & Aurora Panel -->
    <div id="aurora-legend-panel" class="aurora-legend-panel map-overlay-panel" style="display: none;">
        <div class="aurora-header map-overlay-header" id="aurora-drag-handle">
            <div class="aurora-title">
                <span class="map-drag-handle-grip">⠿</span>
                <span>🌌</span>
                <span>SPACE WEATHER &amp; AURORA</span>
            </div>
            <div style="display: flex; align-items: center; gap: 4px;">
                <button class="aurora-refresh-btn" onclick="if (window.pyBridge && window.pyBridge.on_space_weather_refresh) window.pyBridge.on_space_weather_refresh()" title="Refresh NOAA Feeds">↺</button>
                <button class="aurora-close-btn" onclick="if (window.pyBridge && window.pyBridge.on_space_weather_toggled) window.pyBridge.on_space_weather_toggled(false)" title="Close Layer">×</button>
            </div>
        </div>
        <div class="aurora-kpi-grid">
            <div class="aurora-kpi-card" id="aurora-kp-card">
                <div class="aurora-kpi-label">GEOMAGNETIC</div>
                <div class="aurora-kpi-val" id="aurora-kp-val">--</div>
                <div class="aurora-kpi-sub" id="aurora-kp-sub">--</div>
            </div>
            <div class="aurora-kpi-card">
                <div class="aurora-kpi-label">SOLAR WIND</div>
                <div class="aurora-kpi-val" id="aurora-wind-val">-- km/s</div>
                <div class="aurora-kpi-sub" id="aurora-bz-sub">Bz: -- nT</div>
            </div>
            <div class="aurora-kpi-card">
                <div class="aurora-kpi-label">SOLAR FLUX</div>
                <div class="aurora-kpi-val" id="aurora-sfi-val">-- sfu</div>
                <div class="aurora-kpi-sub" id="aurora-scales-sub">R0 S0 G0</div>
            </div>
        </div>
        <div class="aurora-scale-section">
            <div style="display: flex; justify-content: space-between; font-size: 9px; color: #94A3B8; margin-bottom: 2px;">
                <span>OVATION Aurora Probability</span>
                <span id="aurora-max-prob">Peak: --%</span>
            </div>
            <div class="aurora-scale-bar">
                <span style="background: rgba(34, 197, 94, 0.4);" title="5-15% Faint">5%</span>
                <span style="background: rgba(74, 222, 128, 0.6);" title="15-30% Visible">15%</span>
                <span style="background: rgba(56, 189, 248, 0.7);" title="30-50% Moderate">30%</span>
                <span style="background: rgba(192, 132, 252, 0.8);" title="50-75% Strong">50%</span>
                <span style="background: rgba(244, 63, 94, 0.9);" title="75%+ Overhead">75%</span>
            </div>
        </div>
        <div class="aurora-footer">
            <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; font-size: 9.5px; color: #94A3B8;">
                <span>Layer Opacity</span>
                <input type="range" id="aurora-opacity-range" min="10" max="100" value="60" style="width: 105px; height: 4px; accent-color: #A855F7; cursor: pointer;" oninput="updateAuroraOpacity(this.value)">
                <span id="aurora-opacity-label">60%</span>
            </div>
            <div id="aurora-time-label" style="font-size: 8.5px; color: #64748B; text-align: right; width: 100%; margin-top: 4px;">
                NOAA SWPC
            </div>
        </div>
    </div>
    <script>
        window.addEventListener('error', function(e) {
            console.error('[Leaflet Window Error] ' + (e.message || e) + ' at ' + (e.filename || '') + ':' + (e.lineno || ''));
        });
        window.addEventListener('unhandledrejection', function(e) {
            console.error('[Leaflet Unhandled Rejection] ' + (e.reason ? (e.reason.message || e.reason) : 'unknown'));
        });

        var map = L.map('map', {
            zoomControl: false,
            attributionControl: false,
            worldCopyJump: true,
            preferCanvas: true
        }).setView([54.5, -3.0], 8);

        // Debounced ResizeObserver & window resize to prevent compositor lockups and DOM thrashing during window maximize/resize
        var _mapResizeDebounceTimer = null;
        function debouncedMapInvalidate() {
            if (_mapResizeDebounceTimer) clearTimeout(_mapResizeDebounceTimer);
            _mapResizeDebounceTimer = setTimeout(function() {
                if (typeof map !== 'undefined' && map) {
                    map.invalidateSize(false);
                }
            }, 120);
        }

        if (typeof ResizeObserver !== 'undefined') {
            var _mapResizeObserver = new ResizeObserver(function() {
                debouncedMapInvalidate();
            });
            var _mapEl = document.getElementById('map');
            if (_mapEl) _mapResizeObserver.observe(_mapEl);
        }
        window.addEventListener('resize', function() {
            debouncedMapInvalidate();
        });

        L.control.zoom({ position: 'topright' }).addTo(map);

        // --- CoreScope Canvas Animation Engine (Radar Pulses & Particle Beams) ---
        var TYPE_COLORS = {
            'ADVERT': '#22C55E', 'GRP_TXT': '#3B82F6', 'TXT_MSG': '#F59E0B', 'ACK': '#6B7280',
            'REQ': '#A855F7', 'RESPONSE': '#06B6D4', 'TRACE': '#EC4899', 'PATH': '#14B8A6',
            'ANON_REQ': '#F43F5E', 'GRP_DATA': '#8B5CF6', 'MULTIPART': '#0D9488',
            'CONTROL': '#B45309', 'RAW_CUSTOM': '#C026D3', 'FLOOD': '#3B82F6', 'DIRECT': '#F59E0B',
            'UNKNOWN': '#6B7280'
        };

        map.createPane('animationsPane');
        map.getPane('animationsPane').style.zIndex = 650;
        map.getPane('animationsPane').style.pointerEvents = 'none';

        if (!map.getPane('roomServerPane')) {
            map.createPane('roomServerPane');
            map.getPane('roomServerPane').style.zIndex = 620; // Explicit layer above standard markerPane (600)
        }

        var animCanvas = document.createElement('canvas');
        animCanvas.id = 'meshcoreAnimCanvas';
        animCanvas.style.cssText = 'position:absolute; pointer-events:none; top:0; left:0;';
        map.getPane('animationsPane').appendChild(animCanvas);
        var animCtx = animCanvas.getContext('2d');
        var canvasTopLeft = { x: 0, y: 0 };
        var activePulses = [];
        var activeAnimations = [];
        var isCanvasAnimating = false;
        var _lastAnimTime = 0;

        function updateAnimCanvas() {
            if (!animCanvas || !map) return;
            var size = map.getSize();
            if (!size || size.x === 0 || size.y === 0) return;
            var padX = Math.round(size.x * 0.2);
            var padY = Math.round(size.y * 0.2);
            var w = size.x + padX * 2;
            var h = size.y + padY * 2;
            var dpr = Math.min(window.devicePixelRatio || 1, 1.5);
            animCanvas.width = w * dpr;
            animCanvas.height = h * dpr;
            animCanvas.style.width = w + 'px';
            animCanvas.style.height = h + 'px';
            animCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

            var pixelBounds = map.getPixelBounds();
            var min = pixelBounds.min.subtract([padX, padY]);
            canvasTopLeft = min.subtract(map.getPixelOrigin());
            L.DomUtil.setPosition(animCanvas, canvasTopLeft);
        }

        map.on('move', updateAnimCanvas);
        map.on('zoom', updateAnimCanvas);
        map.on('resize', updateAnimCanvas);
        map.on('viewreset', updateAnimCanvas);
        setTimeout(updateAnimCanvas, 150);

        // Base Layer 1: Esri World Dark Gray Canvas Base
        var canvasBaseLayer = L.tileLayer('https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 16,
            updateWhenIdle: true,
            updateWhenZooming: false,
            keepBuffer: 2,
            attribution: 'Esri Canvas'
        });
        canvasBaseLayer.on('tileerror', function(err) {
            console.warn('[Leaflet Base Tile Error] Canvas: ' + (err.tile ? err.tile.src : 'unknown'));
        });

        // Base Layer 2: OpenTopoMap Topographic Relief (with dark inversion & relief contrast)
        var topoBaseLayer = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            maxZoom: 17,
            subdomains: ['a', 'b', 'c'],
            className: 'dark-topo-tiles',
            updateWhenIdle: true,
            updateWhenZooming: false,
            keepBuffer: 2,
            attribution: 'OpenTopoMap (CC-BY-SA)'
        });
        topoBaseLayer.on('tileerror', function(err) {
            console.warn('[Leaflet Base Tile Error] Topo: ' + (err.tile ? err.tile.src : 'unknown'));
        });

        // Base Layer 3: CoreScope Dark (Carto Dark Matter — pitch black landmass with dark grey ocean/sea)
        var cartoApiKey = '';
        function getCartoTileUrl(key) {
            var k = (key !== undefined && key !== null && String(key).trim() !== '') ? String(key).trim() : cartoApiKey;
            var base = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
            return k ? (base + '?key=' + encodeURIComponent(k)) : base;
        }

        var corescopeBaseLayer = L.tileLayer(getCartoTileUrl(), {
            maxZoom: 19,
            subdomains: ['a', 'b', 'c', 'd'],
            updateWhenIdle: true,
            updateWhenZooming: false,
            keepBuffer: 2,
            attribution: '© OpenStreetMap contributors © CARTO'
        });
        corescopeBaseLayer.on('tileerror', function(err) {
            console.warn('[Leaflet Base Tile Error] CoreScope: ' + (err.tile ? err.tile.src : 'unknown'));
        });

        function setCartoApiKey(key) {
            cartoApiKey = (key ? String(key).trim() : '');
            if (corescopeBaseLayer && corescopeBaseLayer.setUrl) {
                corescopeBaseLayer.setUrl(getCartoTileUrl(cartoApiKey));
            }
        }
        window.setCartoApiKey = setCartoApiKey;

        // Dedicated pane for Reference labels at zIndex 380 so town/city names stay legible above tropo colors
        map.createPane('labelsPane');
        map.getPane('labelsPane').style.zIndex = 380;
        map.getPane('labelsPane').style.pointerEvents = 'none';

        // Reference labels layer (Esri Dark Gray Reference) for Canvas and Topo layers
        var esriRefLayer = L.tileLayer('https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 16,
            opacity: 0.85,
            updateWhenIdle: true,
            updateWhenZooming: false,
            keepBuffer: 2,
            pane: 'labelsPane'
        });

        // Default to Canvas base layer initially
        canvasBaseLayer.addTo(map);
        esriRefLayer.addTo(map);
        var currentBaseLayerType = 'canvas';

        function setBaseMapLayer(type) {
            type = (type || 'canvas').toLowerCase();
            if (map.hasLayer(canvasBaseLayer)) map.removeLayer(canvasBaseLayer);
            if (map.hasLayer(topoBaseLayer)) map.removeLayer(topoBaseLayer);
            if (map.hasLayer(corescopeBaseLayer)) map.removeLayer(corescopeBaseLayer);

            if (type === 'corescope' || type === 'carto') {
                map.addLayer(corescopeBaseLayer);
                if (map.hasLayer(esriRefLayer)) map.removeLayer(esriRefLayer);
                currentBaseLayerType = 'corescope';
            } else if (type === 'topo') {
                map.addLayer(topoBaseLayer);
                if (!map.hasLayer(esriRefLayer)) map.addLayer(esriRefLayer);
                currentBaseLayerType = 'topo';
            } else {
                map.addLayer(canvasBaseLayer);
                if (!map.hasLayer(esriRefLayer)) map.addLayer(esriRefLayer);
                currentBaseLayerType = 'canvas';
            }
        }
        window.setBaseMapLayer = setBaseMapLayer;

        function showLoadingHud(text) {
            var hud = document.getElementById('map-loading-hud');
            var txt = document.getElementById('loading-hud-text');
            if (hud) {
                if (txt && text) txt.textContent = text;
                hud.classList.remove('hidden');
            }
        }
        window.showLoadingHud = showLoadingHud;

        function hideLoadingHud() {
            var hud = document.getElementById('map-loading-hud');
            if (hud) hud.classList.add('hidden');
        }
        window.hideLoadingHud = hideLoadingHud;

        // Dedicated pane for Tropo Forecast at zIndex 350 (strictly beneath markers at 600, routes at 400)
        map.createPane('tropoPane');
        map.getPane('tropoPane').style.zIndex = 350;
        map.getPane('tropoPane').style.pointerEvents = 'none';

        // Dedicated pane for ADS-B Radar Overlay at zIndex 410 (pointer-events strictly none)
        map.createPane('adsbRadarPane');
        map.getPane('adsbRadarPane').style.zIndex = 410;
        map.getPane('adsbRadarPane').style.pointerEvents = 'none';

        // Dedicated pane for ADS-B Flight Trails at zIndex 420 (pointer-events strictly none)
        map.createPane('adsbTrailsPane');
        map.getPane('adsbTrailsPane').style.zIndex = 420;
        map.getPane('adsbTrailsPane').style.pointerEvents = 'none';

        // Dedicated pane for ADS-B Interactive Aircraft Markers at zIndex 620 (strictly above node markers at 600, routes at 400)
        map.createPane('adsbMarkersPane');
        map.getPane('adsbMarkersPane').style.zIndex = 620;

        map.createPane('adsbPane');
        map.getPane('adsbPane').style.zIndex = 620;

        var adsbTrailsGroup = L.layerGroup([], { pane: 'adsbTrailsPane' }).addTo(map);
        var adsbLayerGroup = L.layerGroup([], { pane: 'adsbMarkersPane' }).addTo(map);
        var adsbRadarRingsGroup = L.layerGroup([], { pane: 'adsbRadarPane' }).addTo(map);
        var adsbRangeCircle = null;
        var adsbRadarOverlay = null;
        var adsbRadarRadius = null;
        var adsbCenterMarker = null;
        var adsbLastTargetCoord = null;
        var adsbActive = false;
        var aircraftHistory = {};
        var aircraftTrails = {};
        var aircraftMarkers = {};
        var currentAircraftData = {};
        var _aircraftContainerPoints = {};
        var pinnedTooltipHex = null;
        var _activeHoverHex = null;
        var _adsbHoverCloseTimer = null;

        // Dedicated pane for Thunderstorm Precipitation Radar at zIndex 360 (above tropo at 350, below routes at 400)
        map.createPane('thunderstormPane');
        map.getPane('thunderstormPane').style.zIndex = 360;
        map.getPane('thunderstormPane').style.pointerEvents = 'none';

        // Dedicated pane for Lightning Strikes at zIndex 580 (above trails at 420, below node markers at 600)
        map.createPane('lightningPane');
        map.getPane('lightningPane').style.zIndex = 580;
        map.getPane('lightningPane').style.pointerEvents = 'none';

        // Dedicated pane for Aurora Borealis / Australis at zIndex 355 (above tropo at 350, below radar at 360)
        map.createPane('auroraPane');
        map.getPane('auroraPane').style.zIndex = 355;
        map.getPane('auroraPane').style.pointerEvents = 'none';

        // Dedicated pane for LOS / Viewshed Coverage Overlay at zIndex 370 (above tropo/radar, below routes at 400)
        map.createPane('losPane');
        map.getPane('losPane').style.zIndex = 370;
        map.getPane('losPane').style.pointerEvents = 'none';

        // Dedicated pane for Point-to-Point Path & Fresnel Profile Line at zIndex 590 (above routes at 400, below node markers at 600)
        map.createPane('p2pPane');
        map.getPane('p2pPane').style.zIndex = 590;

        var thunderstormActive = false;
        var rainViewerRadarLayer = null;
        var lightningStrikesGroup = L.layerGroup([], { pane: 'lightningPane' }).addTo(map);
        var blitzortungWs = null;
        var lightningStrikesList = [];

        // Activity Heatmap State
        var activityHeatmapActive = false;
        var activityTimeframeHours = 1;
        var activityHeatmapData = {};
        var activityHeatmapLayerGroup = L.layerGroup().addTo(map);

        // Preview Path Layer (for on-hover flood path visualization)
        var previewPathLayer = null;

        var markers = {};
        var rfLinks = [];
        var activePaths = [];
        var pyBridge = null;
        var mapColors = {
            repeater: '#3B82F6',
            companion: '#06B6D4',
            favorite: '#FFD700',
            watcherStart: '#3B82F6',
            watcherEnd: '#1D4ED8',
            messageStart: '#3B82F6',
            messageEnd: '#1D4ED8',
            visualisedPath: '#FF00FF',
            visualisedHeading: '#FF00FF',
            orbitalRepeater: '#FFD335',
            roomServer: '#A855F7'
        };
        var visualisedPathLayer = null;
        var visualisedSvgRenderer = L.svg({ padding: 0.5 });
        var visualisedHighlightMarkers = [];
        var neighborsOverlayLayer = null;
        var orbitalLayerGroup = L.layerGroup().addTo(map);
        window._companionOrbitalsActive = false;
        window._dockedCompanionsData = {};
        window._pausedOrbitals = {};
        var ORBITAL_ZOOM_THRESHOLD = 14;

        var scopeLayerGroup = L.layerGroup().addTo(map);
        window._scopeOverlaysActive = false;
        window._scopeData = {};
        window._scopeNodeMap = {};
        window._activeScopeFilter = 'all';

        function computeConvexHull(points) {
            if (!points || points.length < 3) return points ? points.slice() : [];
            var pts = points.slice().sort(function(a, b) {
                return a[0] === b[0] ? a[1] - b[1] : a[0] - b[0];
            });
            function cross(o, a, b) {
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
            }
            var lower = [];
            for (var i = 0; i < pts.length; i++) {
                while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], pts[i]) <= 0) {
                    lower.pop();
                }
                lower.push(pts[i]);
            }
            var upper = [];
            for (var i = pts.length - 1; i >= 0; i--) {
                while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], pts[i]) <= 0) {
                    upper.pop();
                }
                upper.push(pts[i]);
            }
            upper.pop();
            lower.pop();
            return lower.concat(upper);
        }

        function expandPolygonOutward(hull, bufferDeg) {
            if (!hull || hull.length < 3) return hull;
            var cLat = 0, cLon = 0;
            for (var i = 0; i < hull.length; i++) {
                cLat += hull[i][0];
                cLon += hull[i][1];
            }
            cLat /= hull.length;
            cLon /= hull.length;
            var expanded = [];
            for (var i = 0; i < hull.length; i++) {
                var dLat = hull[i][0] - cLat;
                var dLon = hull[i][1] - cLon;
                var dist = Math.sqrt(dLat * dLat + dLon * dLon) || 1.0;
                expanded.push([
                    hull[i][0] + (dLat / dist) * bufferDeg,
                    hull[i][1] + (dLon / dist) * bufferDeg
                ]);
            }
            return expanded;
        }

        window._scopePruneEmpty = false;
        window._scopeHighlight = false;

        function toggleScopePrune(checked) {
            window._scopePruneEmpty = !!checked;
            renderScopeFilterPills();
        }
        window.toggleScopePrune = toggleScopePrune;

        function toggleScopeHighlight(checked) {
            window._scopeHighlight = !!checked;
            renderScopeOverlays();
            refreshMarkersForScope();
        }
        window.toggleScopeHighlight = toggleScopeHighlight;

        function renderScopeFilterPills() {
            var container = document.getElementById('scope-pills-container');
            if (!container) return;
            container.innerHTML = '';
            var allCount = 0;
            for (var k in window._scopeData) {
                allCount += (window._scopeData[k].nodes || []).length;
            }

            var allPill = document.createElement('div');
            allPill.className = 'scope-pill' + (window._activeScopeFilter === 'all' ? ' active' : '');
            allPill.innerHTML = '<span class="scope-pill-name"><span style="color:#9CA3AF; font-weight:bold; margin-right:4px;">🌐</span>All</span><span class="scope-pill-count">' + allCount + '</span>';
            allPill.onclick = function() {
                window._activeScopeFilter = 'all';
                renderScopeOverlays();
                renderScopeFilterPills();
                refreshMarkersForScope();
            };
            container.appendChild(allPill);

            for (var k in window._scopeData) {
                var sc = window._scopeData[k];
                var cnt = (sc.nodes || []).length;
                if (window._scopePruneEmpty && cnt === 0) {
                    continue;
                }
                (function(scopeKey, count) {
                    var pill = document.createElement('div');
                    var isActive = (window._activeScopeFilter === scopeKey);
                    pill.className = 'scope-pill' + (isActive ? ' active' : '');
                    if (isActive) {
                        pill.style.borderColor = sc.color || '#60687A';
                    }
                    var dotColor = sc.color || '#9CA3AF';
                    pill.innerHTML = '<span class="scope-pill-name"><span style="color:' + dotColor + '; font-weight:bold; margin-right:4px;">#</span>' + escapeHtml(scopeKey) + '</span><span class="scope-pill-count">' + count + '</span>';
                    pill.onclick = function() {
                        window._activeScopeFilter = (window._activeScopeFilter === scopeKey ? 'all' : scopeKey);
                        renderScopeOverlays();
                        renderScopeFilterPills();
                        refreshMarkersForScope();
                    };
                    container.appendChild(pill);
                })(k, cnt);
            }
        }

        function refreshMarkersForScope() {
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }

        function renderScopeOverlays() {
            if (!scopeLayerGroup) return;
            scopeLayerGroup.clearLayers();
            if (!window._scopeOverlaysActive) return;

            window._scopeNodeMap = {};

            for (var sKey in window._scopeData) {
                var sInfo = window._scopeData[sKey];
                var sColor = sInfo.color || '#00E5FF';
                var sNodes = sInfo.nodes || [];

                for (var i = 0; i < sNodes.length; i++) {
                    var n = sNodes[i];
                    window._scopeNodeMap[n.node_id] = {
                        scope_name: sKey,
                        color: sColor,
                        allowed_regions: n.allowed_regions || [],
                        is_gateway: !!n.is_gateway
                    };
                    if (n.alias) {
                        window._scopeNodeMap[n.alias] = window._scopeNodeMap[n.node_id];
                    }
                }

                var isSelected = (window._activeScopeFilter === 'all' || window._activeScopeFilter === sKey);
                if (!isSelected && window._activeScopeFilter !== 'all') {
                    continue;
                }

                var pts = [];
                for (var i = 0; i < sNodes.length; i++) {
                    if (sNodes[i].latitude && sNodes[i].longitude) {
                        pts.push([sNodes[i].latitude, sNodes[i].longitude]);
                    }
                }

                if (pts.length === 0) continue;

                var polyFillOp = isSelected ? (window._scopeHighlight ? 0.30 : 0.12) : 0.03;
                var polyWeight = isSelected ? (window._scopeHighlight ? 3.5 : 2) : 1;

                if (pts.length === 1) {
                    var circle = L.circle(pts[0], {
                        radius: 20000,
                        color: sColor,
                        fillColor: sColor,
                        fillOpacity: polyFillOp,
                        weight: polyWeight,
                        dashArray: '5, 4'
                    }).addTo(scopeLayerGroup);
                    circle.bindTooltip('<span style="color:' + sColor + '; font-weight: bold; font-size: 11px;">#' + sKey + ' (' + pts.length + ')</span>', {
                        permanent: true,
                        direction: 'center',
                        className: 'scope-polygon-label'
                    });
                } else if (pts.length === 2) {
                    var midLat = (pts[0][0] + pts[1][0]) / 2;
                    var midLon = (pts[0][1] + pts[1][1]) / 2;
                    var hullPts = [
                        [pts[0][0] + 0.12, pts[0][1] - 0.12],
                        [pts[0][0] + 0.12, pts[0][1] + 0.12],
                        [pts[1][0] + 0.12, pts[1][1] + 0.12],
                        [pts[1][0] - 0.12, pts[1][1] + 0.12],
                        [pts[1][0] - 0.12, pts[1][1] - 0.12],
                        [pts[0][0] - 0.12, pts[0][1] - 0.12]
                    ];
                    var hull = computeConvexHull(hullPts);
                    var poly = L.polygon(hull, {
                        color: sColor,
                        fillColor: sColor,
                        fillOpacity: polyFillOp,
                        weight: polyWeight,
                        dashArray: '6, 4'
                    }).addTo(scopeLayerGroup);
                    poly.bindTooltip('<span style="color:' + sColor + '; font-weight: bold; font-size: 12px;">#' + sKey + ' (' + pts.length + ')</span>', {
                        permanent: true,
                        direction: 'center',
                        className: 'scope-polygon-label'
                    });
                } else {
                    var rawHull = computeConvexHull(pts);
                    var buffered = expandPolygonOutward(rawHull, 0.14);
                    var poly = L.polygon(buffered, {
                        color: sColor,
                        fillColor: sColor,
                        fillOpacity: polyFillOp,
                        weight: polyWeight,
                        dashArray: '6, 4'
                    }).addTo(scopeLayerGroup);

                    poly.bindTooltip('<span style="color:' + sColor + '; font-weight: bold; font-size: 12px;">#' + sKey + ' (' + pts.length + ')</span>', {
                        permanent: true,
                        direction: 'center',
                        className: 'scope-polygon-label'
                    });
                }
            }
            if (window._is3DActive && typeof syncScopesTo3D === 'function') {
                syncScopesTo3D();
            }
        }

        function setScopeOverlaysVisible(visible, scopeData) {
            window._scopeOverlaysActive = !!visible;
            var bar = document.getElementById('scope-filter-bar');
            if (bar) {
                bar.style.display = window._scopeOverlaysActive ? 'flex' : 'none';
            }
            if (scopeData !== undefined && scopeData !== null) {
                window._scopeData = scopeData;
            }
            renderScopeOverlays();
            renderScopeFilterPills();
            refreshMarkersForScope();
            if (window._is3DActive && typeof syncScopesTo3D === 'function') {
                syncScopesTo3D();
            }
        }

        map.on('zoomend', function() {
            if (window._companionOrbitalsActive) {
                renderCompanionOrbitals();
            }
        });

        function hexToRgb(hex) {
            if (!hex) return [255, 255, 255];
            var c = hex.replace('#', '');
            if (c.length === 3) c = c[0]+c[0]+c[1]+c[1]+c[2]+c[2];
            var num = parseInt(c, 16);
            if (isNaN(num)) return [255, 255, 255];
            return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
        }

        var freshnessFading = false;
        var pathModesActive = false;

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .split('&').join('&amp;')
                .split('<').join('&lt;')
                .split('>').join('&gt;')
                .split('"').join('&quot;')
                .split("'").join('&#39;');
        }

        function escapeJsString(str) {
            if (!str) return '';
            return encodeURIComponent(String(str));
        }

        window._searchNodeIdActive = false;
        window._searchNodeQuery = '';
        window._actRelative = true;
        window._actScaling = true;
        window._actMaxTraffic = 0;
        window._actPeakNode = '--';
        window._showPathVectors = true;
        window._pathMultihopOnly = false;

        function setSearchNodeIdVisible(visible) {
            window._searchNodeIdActive = !!visible;
            var panel = document.getElementById('search-node-id-panel');
            if (panel) {
                panel.style.display = window._searchNodeIdActive ? 'flex' : 'none';
                if (window._searchNodeIdActive) {
                    bringOverlayToFront(panel);
                    var inp = document.getElementById('search-node-input');
                    if (inp) {
                        inp.focus();
                        searchNodeIds(inp.value);
                    }
                } else {
                    window._searchNodeQuery = '';
                    var inp = document.getElementById('search-node-input');
                    if (inp) inp.value = '';
                    refreshMarkersForSearch();
                }
            }
        }
        window.setSearchNodeIdVisible = setSearchNodeIdVisible;

        function closeSearchNodeIdPanel() {
            setSearchNodeIdVisible(false);
            if (window.pyBridge && window.pyBridge.on_search_node_id_toggled) {
                window.pyBridge.on_search_node_id_toggled(false);
            }
        }
        window.closeSearchNodeIdPanel = closeSearchNodeIdPanel;

        function searchNodeIds(query) {
            window._searchNodeQuery = (query || '').trim().toLowerCase();
            var cleanQuery = window._searchNodeQuery.replace(/^[!@]+/, '');
            var resultsContainer = document.getElementById('search-node-results');
            var statusText = document.getElementById('search-node-status-text');
            var countBadge = document.getElementById('search-node-count-badge');

            var matches = [];
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    var nd = m._nodeData;
                    if (!cleanQuery) {
                        matches.push({ marker: m, data: nd });
                    } else {
                        var nid = (nd.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                        var rawNid = (nd.node_id || '').toLowerCase();
                        var nalias = (nd.alias || '').toLowerCase().replace(/^[!@]+/, '');
                        var rawAlias = (nd.alias || '').toLowerCase();

                        var isPrefix = (nid.indexOf(cleanQuery) === 0) ||
                                       (rawNid.indexOf(window._searchNodeQuery) === 0) ||
                                       (nalias.indexOf(cleanQuery) === 0) ||
                                       (rawAlias.indexOf(window._searchNodeQuery) === 0);

                        if (isPrefix) {
                            matches.push({ marker: m, data: nd, isPrefix: true });
                        }
                    }
                }
            }

            if (cleanQuery) {
                matches.sort(function(a, b) {
                    var aId = (a.data.node_id || '').replace(/^[!@]+/, '');
                    var bId = (b.data.node_id || '').replace(/^[!@]+/, '');
                    return aId.localeCompare(bId);
                });
            }

            if (countBadge) countBadge.innerText = matches.length;
            if (statusText) {
                if (!cleanQuery) {
                    statusText.innerText = 'Showing all ' + matches.length + ' nodes';
                } else {
                    statusText.innerText = matches.length + ' match' + (matches.length === 1 ? '' : 'es') + ' found';
                }
            }

            if (resultsContainer) {
                resultsContainer.innerHTML = '';
                var displayList = matches.slice(0, 35);
                for (var i = 0; i < displayList.length; i++) {
                    (function(item) {
                        var card = document.createElement('div');
                        card.className = 'search-node-item';
                        var idStr = item.data.node_id || '--';
                        var nameStr = item.data.alias || item.data.name || 'Unnamed';
                        var repBadge = item.data.is_repeater ? ' <span style="color:#A78BFA; font-size:8.5px; font-weight:600;">(Repeater)</span>' : '';
                        card.innerHTML = '<div><div class="search-node-id-mono">' + escapeHtml(idStr) + repBadge + '</div><div class="search-node-meta">' + escapeHtml(nameStr) + '</div></div><span style="color:#949BA4; font-size:11px;">➔</span>';
                        card.onclick = function() {
                            if (item.marker && item.marker.getLatLng) {
                                map.setView(item.marker.getLatLng(), Math.max(map.getZoom(), 12));
                                if (item.marker.openPopup) {
                                    item.marker.openPopup();
                                }
                            }
                        };
                        resultsContainer.appendChild(card);
                    })(displayList[i]);
                }
            }

            refreshMarkersForSearch();
        }
        window.searchNodeIds = searchNodeIds;

        function refreshMarkersForSearch() {
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }

        function applyNodeMarkerStyling(marker, node) {
            if (!marker || !node) return;
            var el = marker.getElement();
            if (!el) return;
            var dot = el.querySelector('.node-dot');
            if (!dot) return;

            if (node.is_phantom) {
                dot.style.setProperty('background-color', '#000000', 'important');
                dot.style.setProperty('border', '1.5px solid #4B5563', 'important');
                dot.style.setProperty('box-shadow', '0 0 6px rgba(0, 0, 0, 0.9)', 'important');
                dot.style.opacity = '0.92';
                dot.style.transform = '';
                return;
            }

            var isLocal = !!node.is_local;
            var isRoom = !!node.is_room_server;
            var isPhantom = !!node.is_phantom;
            if (isRoom) {
                marker.setZIndexOffset(12000);
                if (el) el.style.zIndex = '12000';
            } else {
                if (el) el.style.zIndex = '';
            }
            var opacity = (!isLocal && freshnessFading) ? calculateFreshnessOpacity(node.last_seen) : 1.0;
            dot.style.opacity = opacity.toFixed(2);
            dot.style.transform = isRoom ? 'rotate(45deg)' : '';

            // Clean any inline border styles so nodes fall back cleanly to CSS rules
            dot.style.removeProperty('border');
            dot.style.removeProperty('border-color');
            dot.style.removeProperty('border-width');
            dot.style.removeProperty('border-style');

            // 1. Search Node IDs View
            if (window._searchNodeIdActive) {
                var rawQ = (window._searchNodeQuery || '').trim().toLowerCase();
                var cleanQ = rawQ.replace(/^[!@]+/, '');
                var nid = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                var rawNid = (node.node_id || '').toLowerCase();
                var nalias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                var rawAlias = (node.alias || '').toLowerCase();

                var isMatch = (!cleanQ) || (
                    nid.indexOf(cleanQ) === 0 ||
                    rawNid.indexOf(rawQ) === 0 ||
                    nalias.indexOf(cleanQ) === 0 ||
                    rawAlias.indexOf(rawQ) === 0
                );

                if (isMatch) {
                    dot.style.opacity = '1.0';
                    dot.style.setProperty('background-color', '#23A55A', 'important');
                    dot.style.setProperty('border-color', '#FFFFFF', 'important');
                    dot.style.setProperty('box-shadow', '0 0 12px rgba(35, 165, 90, 0.95), 0 0 4px #FFFFFF', 'important');
                    dot.style.transform = cleanQ ? 'scale(1.35)' : '';
                    el.style.zIndex = '99999';
                } else {
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                    dot.style.removeProperty('border');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('border-width');
                    dot.style.removeProperty('border-style');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    dot.style.transform = 'scale(0.85)';
                }
                return;
            }

            // 2. New Nodes Discovery View (Gold Highlight)
            if (window._newNodesActive) {
                var tfHours = window._newNodesTimeframeHours || 72;
                var maxAgeMs = tfHours * 3600 * 1000;
                var firstSeenStr = node.first_seen || '';
                var isNew = false;
                var baselineCutoff = 1704153600000; // 2024-01-02T00:00:00Z
                if (firstSeenStr) {
                    try {
                        var parsed = Date.parse(firstSeenStr);
                        if (!isNaN(parsed) && parsed > baselineCutoff) {
                            var ageMs = Date.now() - parsed;
                            if (ageMs >= 0 && ageMs <= maxAgeMs) {
                                isNew = true;
                            }
                        }
                    } catch(e) {}
                }

                if (isNew) {
                    dot.style.opacity = '1.0';
                    dot.style.setProperty('background-color', '#FFD700', 'important');
                    dot.style.setProperty('border', '2px solid #FFFFFF', 'important');
                    dot.style.setProperty('box-shadow', '0 0 16px rgba(255, 215, 0, 0.95), 0 0 6px #FFFFFF', 'important');
                    dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(1.4)';
                    el.style.zIndex = '11000';
                } else {
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                    dot.style.removeProperty('border');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('border-width');
                    dot.style.removeProperty('border-style');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(0.85)';
                    el.style.zIndex = '';
                }
                return;
            }

            // 3. MQTT Discovered Nodes View (Orange Highlight Mode)
            if (window._mqttNodesActive) {
                var src = (node.source || '').toLowerCase().trim();
                var isMqtt = (src === 'mqtt' || !!node.is_mqtt);
                if (isMqtt && !isLocal && !isPhantom) {
                    dot.style.opacity = '1.0';
                    dot.style.setProperty('background', '#F97316', 'important');
                    dot.style.setProperty('background-color', '#F97316', 'important');
                    dot.style.setProperty('border', '1.5px solid #FFFFFF', 'important');
                    dot.style.setProperty('border-color', '#FFFFFF', 'important');
                    dot.style.setProperty('box-shadow', '0 0 16px rgba(249, 115, 22, 0.95), 0 0 6px #FFFFFF', 'important');
                    dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(1.4)';
                    el.style.zIndex = '11000';
                } else {
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background', '#4E5058', 'important');
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                    dot.style.removeProperty('border');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('border-width');
                    dot.style.removeProperty('border-style');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(0.85)';
                    el.style.zIndex = '';
                }
                return;
            }

            // 4. Node Activity Heatmap (Dedicated traffic analysis mode - takes priority over Path Modes and Scopes)
            if (activityHeatmapActive && !isLocal) {
                if (node.is_repeater || node.is_room_server) {
                    var cleanId = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                    var cleanAlias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                    var actMap = activityHeatmapData || {};
                    var count = (actMap[cleanId] !== undefined) ? actMap[cleanId] : (actMap[cleanAlias] || 0);

                    if (count <= 0) {
                        dot.style.opacity = '0.20';
                        dot.style.setProperty('background-color', '#4B5563', 'important');
                        dot.style.removeProperty('border');
                        dot.style.removeProperty('border-color');
                        dot.style.removeProperty('border-width');
                        dot.style.removeProperty('border-style');
                        dot.style.setProperty('border', 'none', 'important');
                        dot.style.setProperty('box-shadow', 'none', 'important');
                        if (window._actScaling) {
                            dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(0.70)';
                        } else if (isRoom) {
                            dot.style.transform = 'rotate(45deg)';
                        }
                        el.style.zIndex = '';
                    } else {
                        dot.style.opacity = '1.0';
                        var actColor, actBorder;
                        var maxT = window._actMaxTraffic || 1;
                        var ratio = maxT > 0 ? (count / maxT) : 0;

                        if (window._actRelative) {
                            if (ratio >= 0.75) {
                                actColor = '#EF4444'; // Red (Peak)
                                actBorder = '#DC2626';
                            } else if (ratio >= 0.50) {
                                actColor = '#FB923C'; // Orange (High)
                                actBorder = '#EA580C';
                            } else if (ratio >= 0.25) {
                                actColor = '#FACC15'; // Yellow (Medium)
                                actBorder = '#CA8A04';
                            } else if (ratio >= 0.10) {
                                actColor = '#10B981'; // Green (Low-Medium)
                                actBorder = '#059669';
                            } else {
                                actColor = '#00D2FF'; // Blue (Base / Low traffic matching 3D map)
                                actBorder = '#0284C7';
                            }
                        } else {
                            if (count > 10) {
                                actColor = '#EF4444'; // Red (Peak)
                                actBorder = '#DC2626';
                            } else if (count > 5) {
                                actColor = '#FB923C'; // Orange (High)
                                actBorder = '#EA580C';
                            } else if (count > 2) {
                                actColor = '#FACC15'; // Yellow (Medium)
                                actBorder = '#CA8A04';
                            } else if (count > 1) {
                                actColor = '#10B981'; // Green (Low-Medium)
                                actBorder = '#059669';
                            } else {
                                actColor = '#00D2FF'; // Blue (1 pkt Low / Base)
                                actBorder = '#0284C7';
                            }
                        }
                        dot.style.setProperty('background-color', actColor, 'important');
                        dot.style.removeProperty('border');
                        dot.style.removeProperty('border-color');
                        dot.style.removeProperty('border-width');
                        dot.style.removeProperty('border-style');
                        dot.style.setProperty('border', 'none', 'important');
                        dot.style.setProperty('box-shadow', '0 0 10px ' + actColor + ', 0 0 22px ' + actColor + ', 0 0 38px ' + actColor + 'aa', 'important');

                        if (window._actScaling) {
                            var effectiveRatio = window._actRelative ? ratio : Math.min(1.0, count / 15.0);
                            var scaleFactor = 1.05 + (Math.pow(effectiveRatio, 0.55) * 1.75); // scales up to 2.80x
                            dot.style.transform = (isRoom ? 'rotate(45deg) ' : '') + 'scale(' + scaleFactor.toFixed(2) + ')';
                            el.style.zIndex = Math.floor(1000 + effectiveRatio * 5000).toString();
                        } else if (isRoom) {
                            dot.style.transform = 'rotate(45deg)';
                        }
                    }
                } else {
                    // Regular client nodes in heatmap mode: dim to subtle background
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background-color', '#4B5563', 'important');
                    dot.style.removeProperty('border');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('border-width');
                    dot.style.removeProperty('border-style');
                    dot.style.setProperty('border', 'none', 'important');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    dot.style.transform = 'scale(0.70)';
                    el.style.zIndex = '';
                }
                return;
            }

            // 5. Path Modes
            if (pathModesActive && !isLocal) {
                var pLen = (node.out_path_len !== undefined && node.out_path_len !== null) ? Number(node.out_path_len) : -1;
                var pMode = (node.out_path_hash_mode !== undefined && node.out_path_hash_mode !== null) ? Number(node.out_path_hash_mode) : -1;

                if (window._pathMultihopOnly && (pMode <= 0 && pLen <= 0)) {
                    dot.style.opacity = '0.20';
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                    dot.style.setProperty('border-color', '#2B2D31', 'important');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    return;
                }

                if (pMode >= 0) {
                    if (pMode === 0) {
                        dot.style.setProperty('background-color', '#EF4444', 'important');
                        dot.style.setProperty('border-color', '#B91C1C', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(239, 68, 68, 0.7)', 'important');
                    } else if (pMode === 1) {
                        dot.style.setProperty('background-color', '#00D2FF', 'important');
                        dot.style.setProperty('border-color', '#0284C7', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(0, 210, 255, 0.7)', 'important');
                    } else {
                        dot.style.setProperty('background-color', '#00FF7F', 'important');
                        dot.style.setProperty('border-color', '#047857', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(0, 255, 127, 0.7)', 'important');
                    }
                } else if (pLen > 0) {
                    dot.style.setProperty('background-color', '#EF4444', 'important');
                    dot.style.setProperty('border-color', '#B91C1C', 'important');
                    dot.style.setProperty('box-shadow', '0 0 8px rgba(239, 68, 68, 0.7)', 'important');
                } else {
                    dot.style.setProperty('background-color', '#6B7280', 'important');
                    dot.style.setProperty('border-color', '#4B5563', 'important');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                }
            // 6. Scopes
            } else if (window._scopeOverlaysActive && !isLocal && node.is_repeater) {
                var scMeta = (window._scopeNodeMap && (window._scopeNodeMap[node.node_id] || (node.alias && window._scopeNodeMap[node.alias]))) ? (window._scopeNodeMap[node.node_id] || window._scopeNodeMap[node.alias]) : null;
                if (scMeta) {
                    var isFiltered = (window._activeScopeFilter !== 'all' && window._activeScopeFilter !== scMeta.scope_name);
                    if (isFiltered) {
                        dot.style.opacity = '0.15';
                        dot.style.removeProperty('background-color');
                        dot.style.removeProperty('border');
                        dot.style.removeProperty('border-color');
                        dot.style.removeProperty('border-width');
                        dot.style.removeProperty('border-style');
                        dot.style.removeProperty('box-shadow');
                    } else {
                        var scCol = scMeta.color || '#00E5FF';
                        dot.style.opacity = '1.0';
                        dot.style.setProperty('background-color', scCol, 'important');
                        dot.style.setProperty('border-color', scCol, 'important');
                        dot.style.setProperty('box-shadow', (window._scopeHighlight ? '0 0 14px ' : '0 0 10px ') + scCol, 'important');
                        if (window._scopeHighlight) {
                            dot.style.transform = 'scale(1.25)';
                        }
                    }
                } else {
                    dot.style.removeProperty('background-color');
                    dot.style.removeProperty('border');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('border-width');
                    dot.style.removeProperty('border-style');
                    dot.style.removeProperty('box-shadow');
                    dot.style.opacity = window._scopeHighlight ? '0.20' : ((window._activeScopeFilter !== 'all') ? '0.15' : '0.4');
                }
            } else {
                dot.style.opacity = (!isLocal && freshnessFading) ? calculateFreshnessOpacity(node.last_seen) : '1.0';
                dot.style.removeProperty('background');
                dot.style.removeProperty('background-color');
                dot.style.removeProperty('border');
                dot.style.removeProperty('border-color');
                dot.style.removeProperty('border-width');
                dot.style.removeProperty('border-style');
                dot.style.removeProperty('box-shadow');
                dot.style.transform = isRoom ? 'rotate(45deg)' : '';
                el.style.zIndex = isRoom ? '12000' : '';
            }
        }

        function updatePathModeStats() {
            var c1 = 0, c2 = 0, c3 = 0, cDirect = 0;
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData && !m._nodeData.is_local) {
                    var nd = m._nodeData;
                    var pMode = (nd.out_path_hash_mode !== undefined && nd.out_path_hash_mode !== null) ? Number(nd.out_path_hash_mode) : -1;
                    var pLen = (nd.out_path_len !== undefined && nd.out_path_len !== null) ? Number(nd.out_path_len) : -1;
                    if (pMode === 0) c1++;
                    else if (pMode === 1) c2++;
                    else if (pMode >= 2) c3++;
                    else if (pLen > 0) c1++;
                    else cDirect++;
                }
            }
            var el1 = document.getElementById('path-count-1byte');
            var el2 = document.getElementById('path-count-2byte');
            var el3 = document.getElementById('path-count-3byte');
            var elD = document.getElementById('path-count-direct');
            if (el1) el1.innerText = c1;
            if (el2) el2.innerText = c2;
            if (el3) el3.innerText = c3;
            if (elD) elD.innerText = cDirect;
        }

        function togglePathVectors(checked) {
            window._showPathVectors = !!checked;
        }
        window.togglePathVectors = togglePathVectors;

        function togglePathMultihop(checked) {
            window._pathMultihopOnly = !!checked;
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) applyNodeMarkerStyling(m, m._nodeData);
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }
        window.togglePathMultihop = togglePathMultihop;

        function setPathModesVisible(visible) {
            pathModesActive = !!visible;
            var legend = document.getElementById('path-mode-legend');
            if (legend) {
                legend.style.display = pathModesActive ? 'flex' : 'none';
            }
            updatePathModeStats();
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }

        function setFreshnessFading(enabled) {
            freshnessFading = !!enabled;
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
        }

        function calculateFreshnessOpacity(lastSeenIso) {
            if (!freshnessFading) return 1.0;
            if (!lastSeenIso) return 0.30;
            var lastSeenMs = Date.parse(lastSeenIso);
            if (isNaN(lastSeenMs)) return 0.30;
            var nowMs = Date.now();
            var ageHours = Math.max(0, nowMs - lastSeenMs) / (1000 * 60 * 60);
            if (ageHours <= 24.0) {
                return 1.0;
            }
            var extraDays = (ageHours - 24.0) / 24.0;
            var op = 1.0 - (extraDays * 0.10);
            return Math.max(0.30, Math.min(1.0, op));
        }

        function formatLastHeard(isoStr) {
            if (!isoStr) return 'Never';
            var ms = Date.parse(isoStr);
            if (isNaN(ms)) return 'Unknown';
            var now = Date.now();
            var diffSec = Math.floor(Math.max(0, now - ms) / 1000);
            if (diffSec < 60) return 'Just now';
            var diffMin = Math.floor(diffSec / 60);
            if (diffMin < 60) return diffMin + 'm ago';
            var diffHrs = Math.floor(diffMin / 60);
            if (diffHrs < 24) {
                var remMin = diffMin % 60;
                return remMin > 0 ? (diffHrs + 'h ' + remMin + 'm ago') : (diffHrs + 'h ago');
            }
            var diffDays = Math.floor(diffHrs / 24);
            if (diffDays === 1) return 'Yesterday';
            if (diffDays < 7) return diffDays + 'd ago';
            var d = new Date(ms);
            return d.toLocaleDateString();
        }

        function formatPathInfo(node) {
            if (node.is_local) return 'Home Station';
            var pLen = (node.out_path_len !== undefined && node.out_path_len !== null) ? Number(node.out_path_len) : -1;
            var pMode = (node.out_path_hash_mode !== undefined && node.out_path_hash_mode !== null) ? Number(node.out_path_hash_mode) : -1;
            var isOverheard = (node.out_path_src === 'overheard' || node.out_path_src === 'message' || node.out_path_src === 'packet_path');
            var srcTag = isOverheard ? ' <span style="color:#9CA3AF; font-size:10px; font-weight:normal;">(Heard RF)</span>' : '';
            var hopStr = pLen > 0 ? (pLen + (pLen === 1 ? ' hop' : ' hops')) : (pLen === 0 ? 'Direct RF' : 'Flood');

            if (pMode === 0) {
                return '<span style="color:#EF4444; font-weight:600;">🔴 1-Byte Path (' + hopStr + ')</span>' + srcTag;
            } else if (pMode === 1) {
                return '<span style="color:#00D2FF; font-weight:600;">🔵 2-Byte Path (' + hopStr + ')</span>' + srcTag;
            } else if (pMode === 2) {
                return '<span style="color:#00FF7F; font-weight:600;">🟢 3-Byte Path (' + hopStr + ')</span>' + srcTag;
            } else if (pLen > 0) {
                return '<span style="color:#9CA3AF;">Routed (' + hopStr + ')</span>' + srcTag;
            } else if (pLen === 0) {
                return '<span style="color:#9CA3AF;">Direct (0 intermediate hops)</span>';
            } else {
                return '<span style="color:#9CA3AF;">Direct / Flood</span>';
            }
        }

        function setMapColors(theme) {
            if (!theme) return;
            mapColors = Object.assign(mapColors, theme);
            if (mapColors.repeater) document.documentElement.style.setProperty('--repeater-color', mapColors.repeater);
            if (mapColors.repeaterHover) document.documentElement.style.setProperty('--repeater-hover-color', mapColors.repeaterHover);
            if (mapColors.companion) document.documentElement.style.setProperty('--companion-color', mapColors.companion);
            if (mapColors.companionHover) document.documentElement.style.setProperty('--companion-hover-color', mapColors.companionHover);
            if (mapColors.favorite) document.documentElement.style.setProperty('--favorite-color', mapColors.favorite);
            if (mapColors.roomServer) document.documentElement.style.setProperty('--room-server-color', mapColors.roomServer);
            if (mapColors.roomServerHover) document.documentElement.style.setProperty('--room-server-hover-color', mapColors.roomServerHover);
            if (mapColors.dotSize) {
                var s = parseFloat(mapColors.dotSize);
                document.documentElement.style.setProperty('--dot-size-repeater', (s * 1.0).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-companion', (s * 0.85).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-favorite', (s * 1.1).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-local', (s * 1.25).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-room', (s * 1.1).toFixed(1) + 'px');
            }
            if (mapColors.visualisedPath) {
                document.documentElement.style.setProperty('--visualised-path-color', mapColors.visualisedPath);
            }
            if (mapColors.visualisedHeading) {
                document.documentElement.style.setProperty('--visualised-heading-color', mapColors.visualisedHeading);
            }
            if (mapColors.phantomPath) {
                document.documentElement.style.setProperty('--phantom-path-color', mapColors.phantomPath);
            }
            if (mapColors.unknownPath) {
                document.documentElement.style.setProperty('--unknown-path-color', mapColors.unknownPath);
            }
            if (mapColors.noGpsPath) {
                document.documentElement.style.setProperty('--no-gps-path-color', mapColors.noGpsPath);
            }
            if (mapColors.orbitalRepeater) {
                document.documentElement.style.setProperty('--orbital-repeater-color', mapColors.orbitalRepeater);
                if (window._companionOrbitalsActive) {
                    renderCompanionOrbitals();
                }
            }
            if (window._activeVisualisedMeta && typeof rebuildSegmentsFromMeta === 'function' && typeof drawVisualisedMessagePath === 'function') {
                if (mapColors.visualisedPath) window._activeVisualisedMeta.color = mapColors.visualisedPath;
                if (mapColors.visualisedHeading) window._activeVisualisedMeta.heading_color = mapColors.visualisedHeading;
                if (mapColors.phantomPath) window._activeVisualisedMeta.phantom_color = mapColors.phantomPath;
                if (mapColors.unknownPath) window._activeVisualisedMeta.unknown_color = mapColors.unknownPath;
                if (mapColors.noGpsPath) window._activeVisualisedMeta.no_gps_color = mapColors.noGpsPath;
                window._activeVisualisedMeta.segments = rebuildSegmentsFromMeta(window._activeVisualisedMeta);
                drawVisualisedMessagePath(window._activeVisualisedMeta);
            }
        }

        var _moveEndDebounceTimer = null;
        map.on('moveend', function() {
            if (_moveEndDebounceTimer) clearTimeout(_moveEndDebounceTimer);
            _moveEndDebounceTimer = setTimeout(function() {
                if (typeof map === 'undefined' || !map) return;
                var center = map.getCenter().wrap();
                var zoom = map.getZoom();
                if (pyBridge && pyBridge.on_map_moved) {
                    pyBridge.on_map_moved(center.lat, center.lng, zoom);
                }
            }, 150);
        });

        if (typeof QWebChannel !== "undefined") {
            new QWebChannel(qt.webChannelTransport, function (channel) {
                pyBridge = channel.objects.pyBridge;
                window.pyBridge = pyBridge;
            });
        }

        var currentTropoLayer = null;
        var tropoActive = false;

        function clearTropoLayer() {
            if (currentTropoLayer !== null) {
                map.removeLayer(currentTropoLayer);
                currentTropoLayer = null;
            }
            tropoActive = false;
            window._lastTropoGeojson = null;
            var panel = document.getElementById('tropo-legend-panel');
            if (panel) panel.style.display = 'none';
            if (window._is3DActive && typeof syncTropoTo3D === 'function') {
                syncTropoTo3D();
            }
        }
        window.clearTropoLayer = clearTropoLayer;

        window.onTropoDataReady = function(payload) {
            if (!payload || !payload.grid || !payload.grid.b64_floats) return;
            clearTropoLayer();
            try {
                var grid = payload.grid;
                var w = grid.w;
                var h = grid.h;
                if (!w || !h) return;

                var binaryString = atob(grid.b64_floats);
                var len = binaryString.length;
                var bytes = new Uint8Array(len);
                for (var i = 0; i < len; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                }
                var vals = new Float32Array(bytes.buffer);

                var lon_min = grid.lon_min || -20.0;
                var lat_max = grid.lat_max || 65.0;
                var res = grid.res || 0.25;

                // Generate vector contour MultiPolygons for tropospheric ducting tiers
                // (< 43 N-units is baseline normal and remains 100% transparent)
                var rawContours = d3.contours().size([w, h]).thresholds([43, 55, 60, 70, 90, 110])(vals);

                var features = [];
                for (var i = 0; i < rawContours.length; i++) {
                    var c = rawContours[i];
                    if (!c.coordinates || c.coordinates.length === 0) continue;
                    var newCoords = c.coordinates.map(function(ringList) {
                        return ringList.map(function(ring) {
                            return ring.map(function(pt) {
                                var lon = lon_min + pt[0] * res;
                                var lat = lat_max - pt[1] * res;
                                return [Number(lon.toFixed(3)), Number(lat.toFixed(3))];
                            });
                        });
                    });
                    features.push({
                        type: "Feature",
                        properties: { value: c.value },
                        geometry: {
                            type: "MultiPolygon",
                            coordinates: newCoords
                        }
                    });
                }

                var geojson = {
                    type: "FeatureCollection",
                    features: features
                };

                function getContourStyle(feature) {
                    var val = feature.properties.value;
                    var fillColor = '#BA68C8';
                    var strokeColor = '#9C27B0';
                    var fillOpacity = 0.22;

                    if (val >= 110) {
                        fillColor = '#FFFFFF';
                        strokeColor = '#FFFFFF';
                        fillOpacity = 0.65;
                    } else if (val >= 90) {
                        fillColor = '#EF4444';
                        strokeColor = '#DC2626';
                        fillOpacity = 0.55;
                    } else if (val >= 70) {
                        fillColor = '#FBBF24';
                        strokeColor = '#D97706';
                        fillOpacity = 0.45;
                    } else if (val >= 60) {
                        fillColor = '#10B981';
                        strokeColor = '#059669';
                        fillOpacity = 0.40;
                    } else if (val >= 55) {
                        fillColor = '#9333EA';
                        strokeColor = '#7E22CE';
                        fillOpacity = 0.32;
                    } else {
                        // 43 - 55: Marginal
                        fillColor = '#BA68C8';
                        strokeColor = '#9C27B0';
                        fillOpacity = 0.22;
                    }

                    return {
                        fillColor: fillColor,
                        fillOpacity: fillOpacity,
                        color: strokeColor,
                        weight: 1.2,
                        opacity: 0.75,
                        smoothFactor: 1.0,
                        pane: 'tropoPane'
                    };
                }

                for (var ti = 0; ti < features.length; ti++) {
                    var st = getContourStyle(features[ti]);
                    features[ti].properties.fillColor = st.fillColor;
                    features[ti].properties.strokeColor = st.color;
                    features[ti].properties.fillOpacity = st.fillOpacity;
                }

                currentTropoLayer = L.geoJSON(geojson, {
                    style: getContourStyle,
                    pane: 'tropoPane'
                }).addTo(map);

                tropoActive = true;
                window._lastTropoGeojson = geojson;
                if (window._is3DActive && typeof syncTropoTo3D === 'function') {
                    syncTropoTo3D();
                }

                var panel = document.getElementById('tropo-legend-panel');
                var timeLbl = document.getElementById('tropo-time-label');
                if (timeLbl && payload.label) {
                    timeLbl.textContent = payload.label;
                }
                if (panel) panel.style.display = 'block';
            } catch (err) {
                console.error("Error rendering Tropo vector contours:", err);
            }
        };

        function onNodeClicked(nodeId) {
            if (pyBridge && pyBridge.on_node_clicked) {
                pyBridge.on_node_clicked(nodeId);
            }
        }

        function onTrackAdsbClicked(btn) {
            if (window.pyBridge && window.pyBridge.on_set_adsb_target && btn) {
                var nid = decodeURIComponent(btn.dataset.nid || '');
                var alias = decodeURIComponent(btn.dataset.alias || '');
                var lat = parseFloat(btn.dataset.lat);
                var lon = parseFloat(btn.dataset.lon);
                window.pyBridge.on_set_adsb_target(nid, alias, lat, lon);
            }
        }
        window.onTrackAdsbClicked = onTrackAdsbClicked;

        function onToggleScopePicker(safeNid) {
            var el = document.getElementById('scope-picker-' + safeNid);
            if (el) {
                el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
            }
        }
        window.onToggleScopePicker = onToggleScopePicker;

        function onApplyScopePicker(safeNid) {
            var sel = document.getElementById('scope-select-' + safeNid);
            if (sel) {
                onSetNodeScope(safeNid, sel.value);
            }
        }
        window.onApplyScopePicker = onApplyScopePicker;

        function onSetNodeScope(safeNid, scopeName) {
            var cleanId = decodeURIComponent(safeNid);
            if (window.pyBridge && window.pyBridge.on_node_scope_changed) {
                window.pyBridge.on_node_scope_changed(cleanId, scopeName);
            }
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData && (m._nodeData.node_id === cleanId || m._nodeData.alias === cleanId)) {
                    m._nodeData.scope_name = scopeName;
                    if (scopeName === 'none' || scopeName === 'excluded') {
                        if (window._scopeNodeMap) {
                            delete window._scopeNodeMap[m._nodeData.node_id];
                            if (m._nodeData.alias) delete window._scopeNodeMap[m._nodeData.alias];
                        }
                    }
                    applyNodeMarkerStyling(m, m._nodeData);
                    if (m.isPopupOpen()) {
                        m.setPopupContent(buildNodePopupContent(m._nodeData));
                    }
                    break;
                }
            }
        }
        window.onSetNodeScope = onSetNodeScope;

        window.onTogglePhantomMarker = function(btn) {
            if (!btn) return;
            var nid = decodeURIComponent(btn.getAttribute('data-nid') || '');
            var alias = decodeURIComponent(btn.getAttribute('data-alias') || '');
            var isPhant = btn.getAttribute('data-phantom') === '1';
            if (window.pyBridge && window.pyBridge.on_phantom_node_toggled) {
                window.pyBridge.on_phantom_node_toggled(nid, alias, !isPhant);
            }
        };

        window.onDeleteNodeClicked = function(btn) {
            if (!btn) return;
            var nid = decodeURIComponent(btn.getAttribute('data-nid') || '');
            var alias = decodeURIComponent(btn.getAttribute('data-alias') || nid || 'Node');
            if (!nid) return;
            if (confirm("Are you sure you want to permanently delete node '" + alias + "' (" + nid + ")?\\n\\nThis will remove the node, direct message history, neighbour links, and room credentials.")) {
                if (window.pyBridge && window.pyBridge.on_delete_node) {
                    window.pyBridge.on_delete_node(nid);
                }
                if (window.deleteNodeMarker) {
                    window.deleteNodeMarker(nid);
                }
            }
        };

        function deleteNodeMarker(nid) {
            if (!nid) return;
            map.closePopup();
            var target = nid.toLowerCase().replace(/^[!@]+/, '');
            for (var id in markers) {
                var cleanId = id.toLowerCase().replace(/^[!@]+/, '');
                var m = markers[id];
                var mNodeId = (m && m._nodeData && m._nodeData.node_id) ? m._nodeData.node_id.toLowerCase().replace(/^[!@]+/, '') : '';
                if (cleanId === target || mNodeId === target) {
                    map.removeLayer(m);
                    delete markers[id];
                }
            }
        }
        window.deleteNodeMarker = deleteNodeMarker;

        function buildNodePopupContent(node) {
            var isLocal = !!node.is_local;
            var isRep = !!node.is_repeater;
            var isRoom = !!node.is_room_server;
            var isFav = !!node.is_favorite;
            var isPhantom = !!node.is_phantom;
            var favLabel = isFav ? 'Favorite ' : '';
            var typeLabel = isPhantom ? 'Phantom Node' : (isLocal ? 'Local Companion' : (isRoom ? 'Room Server' : (isRep ? favLabel + 'Repeater' : favLabel + 'Companion Node')));
            var starHtml = isFav ? '<span style="color: #FFD700;">★ </span>' : '';
            var safeAlias = escapeHtml(node.alias || node.node_id || 'Node');
            var safeNodeId = escapeHtml(node.node_id);
            var lastHeardStr = isLocal ? 'Active now (Local node)' : formatLastHeard(node.last_seen);
            var pathStr = formatPathInfo(node);
            var snrVal = (node.snr !== undefined && node.snr !== null) ? Number(node.snr) : null;
            var snrHtml = (snrVal !== null && snrVal !== 0) ? ('<div class="popup-stat">SNR: ' + (snrVal > 0 ? '+' : '') + snrVal.toFixed(1) + ' dB</div>') : '';
            var rssiVal = (node.rssi !== undefined && node.rssi !== null) ? Number(node.rssi) : null;
            var rssiHtml = (rssiVal !== null && rssiVal !== -100) ? ('<div class="popup-stat">RSSI: ' + rssiVal + ' dBm</div>') : '';

            var dockedOrbitalsHtml = '';
            if (window._companionOrbitalsActive && window._dockedCompanionsData && isRep) {
                var dMap = window._dockedCompanionsData;
                var dList = dMap[node.node_id] || dMap[node.alias];
                if (!dList && node.alias) {
                    dList = dMap['@' + node.alias] || dMap[node.alias.replace(/^@/, '')];
                }
                if (dList && dList.length > 0) {
                    var uniqueDocked = [];
                    var seenIds = {};
                    for (var d = 0; d < dList.length; d++) {
                        var dNode = dList[d];
                        if (!dNode || !dNode.node_id || seenIds[dNode.node_id]) continue;
                        seenIds[dNode.node_id] = true;
                        uniqueDocked.push(dNode);
                    }

                    if (uniqueDocked.length > 0) {
                        var sectionTitleColor = isFav ? 'var(--favorite-color, #AA55FF)' : 'var(--orbital-repeater-color, #FFD335)';
                        dockedOrbitalsHtml = '<div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.15);">' +
                            '<div style="color: ' + sectionTitleColor + '; font-weight: bold; font-size: 11px; margin-bottom: 6px; display: flex; align-items: center; justify-content: space-between;">' +
                                '<span>🛰️ Docked Companions (' + uniqueDocked.length + ')</span>' +
                                '<span style="font-size: 9px; color: #9CA3AF; font-weight: normal;">No GPS Anchor</span>' +
                            '</div>' +
                            '<div style="max-height: 130px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 2px;">';

                        for (var i = 0; i < uniqueDocked.length; i++) {
                            var sat = uniqueDocked[i];
                            var sAlias = escapeHtml(sat.alias || sat.node_id || 'Companion');
                            var sTime = formatLastHeard(sat.last_heard);
                            var sSnr = (sat.snr !== null && sat.snr !== undefined && sat.snr !== 0) ? ((Number(sat.snr) > 0 ? '+' : '') + Number(sat.snr).toFixed(1) + ' dB') : '';
                            var isUnk = !!sat.is_unknown_first_hop;
                            var dotCol = isUnk ? '#EF4444' : '#00FFFF';
                            var unkBadge = isUnk ? ' <span style="color:#EF4444; font-size:9px; font-weight:bold;">[1st hop unconfirmed]</span>' : '';
                            var safeSatId = encodeURIComponent(sat.node_id);

                            dockedOrbitalsHtml += '<div style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 4px; padding: 4px 6px; display: flex; align-items: center; justify-content: space-between; font-size: 10px; cursor: pointer;" ' +
                                'data-sat-id="' + safeSatId + '" ' +
                                'onclick="onNodeClicked(decodeURIComponent(this.dataset.satId))" ' +
                                'title="Click to direct message ' + sAlias + '">' +
                                '<div style="display: flex; align-items: center; gap: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px;">' +
                                    '<span style="width: 6px; height: 6px; min-width: 6px; border-radius: 50%; background: ' + dotCol + '; box-shadow: 0 0 4px ' + dotCol + '; display: inline-block;"></span>' +
                                    '<span style="font-weight: 600; color: #F3F4F6;">' + sAlias + '</span>' +
                                    unkBadge +
                                '</div>' +
                                '<div style="color: #9CA3AF; font-size: 9px; display: flex; gap: 5px; white-space: nowrap; margin-left: 4px;">' +
                                    (sSnr ? '<span style="color: #10B981;">' + sSnr + '</span>' : '') +
                                    '<span>' + sTime + '</span>' +
                                '</div>' +
                            '</div>';
                        }

                        dockedOrbitalsHtml += '</div></div>';
                    }
                }
            }

            var scopeInfoHtml = '';
            if (isRep) {
                var rawScName = (node.scope_name || '').toLowerCase();
                var isExcluded = (rawScName === 'none' || rawScName === 'excluded' || rawScName === 'hide' || rawScName === 'hidden');
                var scItem = null;
                if (!isExcluded) {
                    scItem = (window._scopeNodeMap && (window._scopeNodeMap[node.node_id] || (node.alias && window._scopeNodeMap[node.alias]))) || null;
                    if (!scItem && rawScName) {
                        var sDef = (window._scopeData && window._scopeData[rawScName]) || {};
                        scItem = {
                            scope_name: rawScName,
                            color: sDef.color || '#00E5FF',
                            allowed_regions: node.allowed_regions || [],
                            is_gateway: false
                        };
                    }
                }

                var currentScopeVal = isExcluded ? 'none' : (scItem ? scItem.scope_name : '');
                var safeNid = encodeURIComponent(node.node_id);

                var scopeOptionsHtml = '<div style="display: flex; gap: 4px; align-items: center; margin-top: 5px;">' +
                    '<select id="scope-select-' + safeNid + '" style="background: #111827; color: #F3F4F6; border: 1px solid #4B5563; border-radius: 3px; font-size: 10px; padding: 2px 4px; flex: 1;">' +
                        '<option value="" ' + (currentScopeVal === '' ? 'selected' : '') + '>Auto (Default heuristic)</option>' +
                        '<option value="none" ' + (isExcluded ? 'selected' : '') + '>🚫 None (Exclude from scopes)</option>' +
                        '<option value="gb-cum" ' + (currentScopeVal === 'gb-cum' ? 'selected' : '') + '>#gb-cum (Cumbria)</option>' +
                        '<option value="gb-nwk" ' + (currentScopeVal === 'gb-nwk' ? 'selected' : '') + '>#gb-nwk (North West)</option>' +
                        '<option value="cax" ' + (currentScopeVal === 'cax' ? 'selected' : '') + '>#cax (Carlisle)</option>' +
                        '<option value="gb-nth" ' + (currentScopeVal === 'gb-nth' ? 'selected' : '') + '>#gb-nth (Northern England)</option>' +
                        '<option value="eng-ne" ' + (currentScopeVal === 'eng-ne' ? 'selected' : '') + '>#eng-ne (North East England)</option>' +
                        '<option value="sco" ' + (currentScopeVal === 'sco' ? 'selected' : '') + '>#sco (Scotland)</option>' +
                        '<option value="iom" ' + (currentScopeVal === 'iom' ? 'selected' : '') + '>#iom (Isle of Man)</option>' +
                        '<option value="ioi" ' + (currentScopeVal === 'ioi' ? 'selected' : '') + '>#ioi (Island of Ireland)</option>' +
                    '</select>' +
                    '<button class="scope-btn apply-scope-btn" data-nid="' + safeNid + '" onclick="onApplyScopePicker(this.dataset.nid)">Save</button>' +
                '</div>';

                if (isExcluded) {
                    scopeInfoHtml = '<div style="margin-top: 6px; padding: 5px 7px; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; font-size: 10px;">' +
                        '<div style="display: flex; align-items: center; justify-content: space-between;">' +
                            '<span style="color: #F87171; font-weight: bold;">🌐 Scope: 🚫 Excluded from Scopes</span>' +
                        '</div>' +
                        '<div style="margin-top: 4px; display: flex; gap: 4px;">' +
                            '<button class="scope-btn restore-scope-btn" data-nid="' + safeNid + '" data-scope="" onclick="onSetNodeScope(this.dataset.nid, this.dataset.scope)">↩️ Restore Auto Scope</button>' +
                            '<button class="scope-btn" data-nid="' + safeNid + '" onclick="onToggleScopePicker(this.dataset.nid)">⚙️ Change...</button>' +
                        '</div>' +
                        '<div id="scope-picker-' + safeNid + '" style="display: none;">' +
                            scopeOptionsHtml +
                        '</div>' +
                    '</div>';
                } else if (scItem) {
                    var scCol = scItem.color || '#00E5FF';
                    var allowList = (scItem.allowed_regions && scItem.allowed_regions.length > 0) ? scItem.allowed_regions.join(', ') : scItem.scope_name;
                    var gwBadge = scItem.is_gateway ? ' <span style="background: rgba(255,215,0,0.2); color: #FFD700; padding: 1px 4px; border-radius: 3px; font-size: 9px;">Gateway</span>' : '';
                    scopeInfoHtml = '<div style="margin-top: 6px; padding: 5px 7px; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 4px; font-size: 10px;">' +
                        '<div style="display: flex; align-items: center; justify-content: space-between;">' +
                            '<span>🌐 <b>Primary Scope:</b> <span style="color:' + scCol + '; font-weight: bold;">#' + escapeHtml(scItem.scope_name) + '</span></span>' +
                            gwBadge +
                        '</div>' +
                        '<div style="color: #9CA3AF; margin-top: 2px; font-size: 9px;">Allowed: ' + escapeHtml(allowList) + '</div>' +
                        '<div style="margin-top: 5px; display: flex; gap: 4px;">' +
                            '<button class="scope-btn exclude-scope-btn" data-nid="' + safeNid + '" data-scope="none" onclick="onSetNodeScope(this.dataset.nid, this.dataset.scope)">🚫 Remove from Scope</button>' +
                            '<button class="scope-btn" data-nid="' + safeNid + '" onclick="onToggleScopePicker(this.dataset.nid)">⚙️ Change...</button>' +
                        '</div>' +
                        '<div id="scope-picker-' + safeNid + '" style="display: none;">' +
                            scopeOptionsHtml +
                        '</div>' +
                    '</div>';
                } else {
                    scopeInfoHtml = '<div style="margin-top: 6px; padding: 5px 7px; background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 4px; font-size: 10px;">' +
                        '<div style="display: flex; align-items: center; justify-content: space-between;">' +
                            '<span style="color: #9CA3AF;">🌐 <b>Scope:</b> None (Unscoped)</span>' +
                            '<button class="scope-btn" data-nid="' + safeNid + '" onclick="onToggleScopePicker(this.dataset.nid)">➕ Set Scope...</button>' +
                        '</div>' +
                        '<div id="scope-picker-' + safeNid + '" style="display: none;">' +
                            scopeOptionsHtml +
                        '</div>' +
                    '</div>';
                }
            }

            var iconPrefix = isPhantom ? '👻 ' : (isLocal ? '👤 ' : (isRoom ? '🏢 ' : (isRep ? '📡 ' : '👤 ')));
            var actionBtnLabel = isRoom ? 'Open Room Server' : (isRep ? 'Open Repeater Console' : 'Direct Message');

            var phantomBtnHtml = '';
            if (isRep || isPhantom) {
                var pLabel = isPhantom ? '👻 Unmark Phantom Node' : '👻 Mark as Phantom Node';
                var pStyle = isPhantom ? 'background-color: #374151; color: #F3F4F6; border: 1px solid #9CA3AF;' : 'background-color: #181A20; color: #E5E7EB; border: 1px solid #4B5563;';
                phantomBtnHtml = '<button class="popup-btn" style="margin-top: 5px; ' + pStyle + '" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || '') + '" data-phantom="' + (isPhantom ? '1' : '0') + '" onclick="onTogglePhantomMarker(this)">' + pLabel + '</button>';
            }

        function copyNodeIdClipboard(rawText, btn) {
            var text = (rawText || '').trim();
            if (!text && btn) {
                text = (btn.getAttribute('data-node-id') || (btn.dataset ? btn.dataset.nodeId : '') || '').trim();
            }
            if (text && text.indexOf('%') !== -1) {
                try { text = decodeURIComponent(text); } catch(e) {}
            }
            if (!text) return;

            // 1. Native Qt clipboard via Python bridge
            try {
                if (window.pyBridge && window.pyBridge.on_copy_clipboard) {
                    window.pyBridge.on_copy_clipboard(text);
                }
            } catch(e) {
                console.warn('[Clipboard] pyBridge copy exception:', e);
            }

            // 2. Synchronous execCommand copy via off-screen textarea
            try {
                var ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.left = "-9999px";
                ta.style.top = "-9999px";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
            } catch(e) {}

            // 3. Standard Async Clipboard API
            if (navigator.clipboard && navigator.clipboard.writeText) {
                try {
                    navigator.clipboard.writeText(text).catch(function() {});
                } catch(e) {}
            }

            // 4. Visual button feedback
            if (btn) {
                var orig = btn.innerText;
                btn.innerText = '✓ Copied';
                btn.style.color = '#10B981';
                btn.style.borderColor = '#10B981';
                setTimeout(function() {
                    btn.innerText = orig;
                    btn.style.color = '';
                    btn.style.borderColor = '';
                }, 1500);
            }
        }
        window.copyNodeIdClipboard = copyNodeIdClipboard;

            var repLabel = isRep ? ' <span style="color:#A78BFA; font-size:9.5px; font-weight:700;">(Repeater)</span>' : (' (' + typeLabel + ')');
            var idHtml = '<div class="node-popup-id-row">' +
                '<span class="node-popup-id-mono">ID: ' + safeNodeId + repLabel + '</span>' +
                '<button class="node-popup-copy-btn" data-node-id="' + escapeHtml(node.node_id || '') + '" onclick="copyNodeIdClipboard(this.dataset.nodeId, this)" title="Copy Node ID">⎘ Copy</button>' +
                '</div>';

            var isMqtt = (node.source === 'mqtt' || !!node.is_mqtt);
            var sourceHtml = isMqtt ?
                '<div class="popup-stat" style="color: #FB923C; font-weight: 600;">Source: 🌐 MQTT Ingest</div>' :
                '<div class="popup-stat" style="color: #60A5FA;">Source: 📻 Direct LoRa RF</div>';

            return '<div class="custom-popup">' +
                '<div class="popup-title">' + starHtml + iconPrefix + safeAlias + '</div>' +
                idHtml +
                '<div class="popup-stat">Last heard: ' + lastHeardStr + '</div>' +
                '<div class="popup-stat">Routing: ' + pathStr + '</div>' +
                snrHtml + rssiHtml +
                '<div class="popup-stat">Coords: ' + Number(node.lat).toFixed(4) + ', ' + Number(node.lon).toFixed(4) + '</div>' +
                sourceHtml +
                scopeInfoHtml +
                dockedOrbitalsHtml +
                '<button class="popup-btn" data-node-id="' + encodeURIComponent(node.node_id) + '" onclick="onNodeClicked(decodeURIComponent(this.dataset.nodeId))">' + actionBtnLabel + '</button>' +
                '<div style="margin-top: 5px; display: flex; gap: 4px;">' +
                    '<button class="popup-btn" style="flex: 1; margin-top: 0; background-color: #1E293B; color: #38BDF8; border: 1px solid #38BDF8;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onProfileNodeClicked(this)">🏔️ Path Profile</button>' +
                    '<button class="popup-btn" style="flex: 1; margin-top: 0; background-color: #064E3B; color: #34D399; border: 1px solid #10B981;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onCalcViewshedClicked(this)">🟢 LOS Viewshed</button>' +
                '</div>' +
                '<button class="popup-btn" style="margin-top: 5px; background-color: #24262B; color: #38BDF8; border: 1px solid #38BDF8;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onTrackAdsbClicked(this)">✈️ Track ADS-B Around Node</button>' +
                phantomBtnHtml +
                '<button class="popup-btn popup-btn-delete" style="margin-top: 5px; background-color: #7F1D1D; color: #FCA5A5; border: 1px solid #DC2626; font-weight: 600;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" onclick="onDeleteNodeClicked(this)">🗑️ Delete Node</button>' +
                '</div>';
        }

        function setNodes(nodesList) {
            var seen = {};
            var latLngs = [];

            nodesList.forEach(function(node) {
                try {
                    if (!node || typeof node.lat !== 'number' || typeof node.lon !== 'number' || isNaN(node.lat) || isNaN(node.lon)) return;
                    if (node.lat < -85.0 || node.lat > 85.0 || node.lon < -180.0 || node.lon > 180.0) return;

                    var isPhantom = !!node.is_phantom;
                    var isLocal = !!node.is_local;
                    var isRep = !!node.is_repeater;
                    var isRoom = !!node.is_room_server;
                    var isFav = !!node.is_favorite;
                    var isMqtt = (node.source === 'mqtt' || !!node.is_mqtt);

                    var dotClass = 'node-dot ';
                    if (isPhantom) {
                        dotClass += 'node-dot-phantom';
                    } else if (isLocal) {
                        dotClass += 'node-dot-local';
                    } else if (isRoom) {
                        dotClass += 'node-dot-room';
                    } else if (isFav) {
                        dotClass += 'node-dot-favorite';
                        if (isRep) dotClass += ' node-dot-repeater';
                    } else if (isRep) {
                        dotClass += 'node-dot-repeater';
                    } else {
                        dotClass += 'node-dot-companion';
                    }

                    var opacity = (!isLocal && freshnessFading) ? calculateFreshnessOpacity(node.last_seen) : 1.0;
                    var initPathStyle = '';
                    if (pathModesActive && !isLocal) {
                        var pLen = (node.out_path_len !== undefined && node.out_path_len !== null) ? Number(node.out_path_len) : -1;
                        var pMode = (node.out_path_hash_mode !== undefined && node.out_path_hash_mode !== null) ? Number(node.out_path_hash_mode) : -1;
                        if (pMode >= 0) {
                            if (pMode === 0) initPathStyle = 'background-color:#EF4444 !important; border-color:#B91C1C !important; box-shadow:0 0 8px rgba(239, 68, 68, 0.7) !important;';
                            else if (pMode === 1) initPathStyle = 'background-color:#00D2FF !important; border-color:#0284C7 !important; box-shadow:0 0 8px rgba(0, 210, 255, 0.7) !important;';
                            else initPathStyle = 'background-color:#00FF7F !important; border-color:#047857 !important; box-shadow:0 0 8px rgba(0, 255, 127, 0.7) !important;';
                        } else if (pLen > 0) {
                            initPathStyle = 'background-color:#EF4444 !important; border-color:#B91C1C !important; box-shadow:0 0 8px rgba(239, 68, 68, 0.7) !important;';
                        } else {
                            initPathStyle = 'background-color:#64748B !important; border-color:#334155 !important; box-shadow:0 0 4px rgba(100, 116, 139, 0.5) !important;';
                        }
                    }

                    var styleAttr = ' style="opacity: ' + opacity.toFixed(2) + ';' + initPathStyle + '"';

                    var starPrefix = isFav ? '⭐ ' : '';
                    var phantomPrefix = isPhantom ? '<span style="color: #9CA3AF; font-size: 10px;">👻 [Phantom] </span>' : '';
                    var lastHeardStr = isLocal ? 'Active now (Local node)' : formatLastHeard(node.last_seen);
                    var pathStr = formatPathInfo(node);
                    var rawAlias = node.alias || node.node_id || 'Node';
                    var safeAlias = escapeHtml(rawAlias);

                    var actInfo = '';
                    if (activityHeatmapActive && (isRep || isRoom)) {
                        var cleanId = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                        var cleanAlias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                        var cAct = (activityHeatmapData[cleanId] !== undefined) ? activityHeatmapData[cleanId] : (activityHeatmapData[cleanAlias] || 0);
                        var actLabel = cAct <= 0 ? 'Inactive' : (cAct <= 2 ? 'Low' : (cAct <= 5 ? 'Medium' : (cAct <= 10 ? 'High' : 'Very High')));
                        var actBadgeCol = cAct <= 0 ? '#9CA3AF' : (cAct <= 2 ? '#10B981' : (cAct <= 5 ? '#FACC15' : (cAct <= 10 ? '#FB923C' : '#EF4444')));
                        actInfo = '<div style="font-size: 10px; color: ' + actBadgeCol + '; font-weight: bold; margin-top: 2px;">🔥 Activity (' + activityTimeframeHours + 'h): ' + cAct + ' msgs (' + actLabel + ')</div>';
                    }

                    var newBadge = '';
                    if (window._newNodesActive) {
                        var tfH = window._newNodesTimeframeHours || 72;
                        var fsStr = node.first_seen || '';
                        var baselineCutoff = 1704153600000; // 2024-01-02T00:00:00Z
                        if (fsStr) {
                            try {
                                var pTs = Date.parse(fsStr);
                                if (!isNaN(pTs) && pTs > baselineCutoff && (Date.now() - pTs) <= (tfH * 3600 * 1000)) {
                                    newBadge = '<div style="font-size: 10px; color: #FFD700; font-weight: bold; margin-top: 2px;">👋 Newly Discovered Node</div>';
                                }
                            } catch(e) {}
                        }
                    }

                    var mqttBadge = '';
                    if (isMqtt && !isLocal) {
                        mqttBadge = '<div style="font-size: 10px; color: #FB923C; font-weight: bold; margin-top: 2px;">🌐 Discovered via MQTT</div>';
                    }

                    var tipContent = '<div style="text-align: center; line-height: 1.35;">' +
                        '<div>' + phantomPrefix + starPrefix + '<b>' + safeAlias + '</b></div>' +
                        '<div style="font-size: 10px; color: #9CA3AF; margin-top: 2px;">Last heard: ' + lastHeardStr + '</div>' +
                        '<div style="font-size: 10px; margin-top: 2px;">' + pathStr + '</div>' +
                        actInfo +
                        newBadge +
                        mqttBadge +
                        '</div>';

                    seen[node.node_id] = true;
                    var marker = markers[node.node_id];
                    var zOffset = isRoom ? 12000 : (isLocal ? 6000 : (isFav ? 2000 : 0));
                    var paneName = isRoom ? 'roomServerPane' : 'markerPane';
                    var wrapClass = 'node-marker-wrap' + (isRoom ? ' node-marker-wrap-room' : '');

                    if (marker) {
                        var curLL = marker.getLatLng();
                        if (Math.abs(curLL.lat - node.lat) > 0.00001 || Math.abs(curLL.lng - node.lon) > 0.00001) {
                            marker.setLatLng([node.lat, node.lon]);
                        }
                        marker._nodeData = node;
                        var icon = L.divIcon({
                            className: wrapClass,
                            html: '<div class="' + dotClass + '"' + styleAttr + '></div>',
                            iconSize: [20, 20],
                            iconAnchor: [10, 10]
                        });
                        marker.setIcon(icon);
                        marker.setZIndexOffset(zOffset);
                        applyNodeMarkerStyling(marker, node);
                        marker.setTooltipContent(tipContent);
                    } else {
                        var icon = L.divIcon({
                            className: wrapClass,
                            html: '<div class="' + dotClass + '"' + styleAttr + '></div>',
                            iconSize: [20, 20],
                            iconAnchor: [10, 10]
                        });
                        marker = L.marker([node.lat, node.lon], {
                            icon: icon,
                            pane: paneName,
                            zIndexOffset: zOffset
                        }).addTo(map);
                        marker._nodeData = node;
                        applyNodeMarkerStyling(marker, node);
                        marker.bindTooltip(tipContent, {
                            direction: 'top',
                            offset: [0, -6],
                            className: 'node-tooltip'
                        });
                        marker.bindPopup(function() {
                            return buildNodePopupContent(marker._nodeData);
                        }, { className: 'custom-popup', maxWidth: 320 });

                        // Right-click context menu on node marker
                        marker.on('contextmenu', function(ev) {
                            if (ev && ev.originalEvent) {
                                ev.originalEvent.preventDefault();
                                ev.originalEvent.stopPropagation();
                            }
                            if (window.pyBridge && window.pyBridge.on_node_context_menu) {
                                var nd = marker._nodeData || node;
                                window.pyBridge.on_node_context_menu(
                                    nd.node_id,
                                    nd.alias || nd.node_id || '',
                                    !!nd.is_repeater,
                                    !!nd.is_phantom,
                                    ev.latlng.lat,
                                    ev.latlng.lng,
                                    Math.round(ev.containerPoint.x),
                                    Math.round(ev.containerPoint.y)
                                );
                            }
                        });

                        if (isRep) {
                            marker.on('click', function(ev) {
                                if (window._companionOrbitalsActive) {
                                    var dMap = window._dockedCompanionsData || {};
                                    var dList = dMap[node.node_id] || dMap[node.alias];
                                    if (!dList && node.alias) {
                                        dList = dMap['@' + node.alias] || dMap[node.alias.replace(/^@/, '')];
                                    }
                                    if (dList && dList.length > 0) {
                                        if (map.getZoom() < ORBITAL_ZOOM_THRESHOLD) {
                                            marker.closePopup();
                                            setTimeout(function() { marker.closePopup(); }, 30);
                                            map.flyTo([node.lat, node.lon], ORBITAL_ZOOM_THRESHOLD, { duration: 0.8 });
                                            if (ev && ev.originalEvent) ev.originalEvent.stopPropagation();
                                            return;
                                        }
                                    }
                                }
                            });
                        }
                        markers[node.node_id] = marker;
                    }
                    latLngs.push([node.lat, node.lon]);
                } catch (nodeErr) {
                    console.error('Error rendering node marker:', node, nodeErr);
                }
            });

            for (var id in markers) {
                if (!seen[id]) {
                    map.removeLayer(markers[id]);
                    delete markers[id];
                }
            }

            // Only fit bounds on first-ever load if user has not set/restored a custom viewport
            if (!window._initialViewSet && latLngs.length > 0) {
                map.fitBounds(latLngs, { padding: [25, 25], maxZoom: 12 });
                window._initialViewSet = true;
            }

            renderCompanionOrbitals();
            if (window._newNodesActive) {
                updateNewNodesStats();
            }
            if (window._mqttNodesActive && window.updateMqttNodesStats) {
                updateMqttNodesStats();
            }
            if (window.syncAllNodesTo3D && map3d) {
                window.syncAllNodesTo3D();
            }
        }

        function setCompanionOrbitalsVisible(active, dockedData) {
            window._companionOrbitalsActive = !!active;
            if (dockedData !== undefined && dockedData !== null) {
                window._dockedCompanionsData = dockedData;
            }
            renderCompanionOrbitals();
            if (window._is3DActive && typeof syncOrbitalsTo3D === 'function') {
                syncOrbitalsTo3D();
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }

        function toggleOrbitalPause(safeRepId, ev) {
            if (ev) {
                if (ev.stopPropagation) ev.stopPropagation();
                if (ev.originalEvent && ev.originalEvent.stopPropagation) ev.originalEvent.stopPropagation();
            }
            if (!window._pausedOrbitals) window._pausedOrbitals = {};
            window._pausedOrbitals[safeRepId] = !window._pausedOrbitals[safeRepId];
            var isPaused = window._pausedOrbitals[safeRepId];
            var host = document.getElementById('orbital_host_' + safeRepId);
            if (host) {
                if (isPaused) {
                    host.classList.add('orbital-paused');
                } else {
                    host.classList.remove('orbital-paused');
                }
            }
        }

        function onOrbitalSatClicked(el, ev) {
            if (ev) {
                if (ev.stopPropagation) ev.stopPropagation();
                if (ev.originalEvent && ev.originalEvent.stopPropagation) ev.originalEvent.stopPropagation();
            }
            var rawId = decodeURIComponent(el.getAttribute('data-node-id') || '');
            if (rawId) onNodeClicked(rawId);
        }

        function renderCompanionOrbitals() {
            if (!orbitalLayerGroup) return;
            orbitalLayerGroup.clearLayers();

            // Reset orbital ring styling on all markers
            for (var nid in markers) {
                var m = markers[nid];
                var el = m.getElement();
                if (el) {
                    var dot = el.querySelector('.node-dot');
                    if (dot) {
                        dot.classList.remove('orbital-ring-repeater');
                        dot.classList.remove('orbital-ring-repeater-fav');
                    }
                }
            }

            if (!window._companionOrbitalsActive || !window._dockedCompanionsData) {
                return;
            }

            var dockedMap = window._dockedCompanionsData;
            var currentZoom = map.getZoom();
            var showSatellites = (currentZoom >= ORBITAL_ZOOM_THRESHOLD);

            for (var id in markers) {
                var marker = markers[id];
                var node = marker._nodeData;
                if (!node || !node.is_repeater) continue;

                // Find docked nodes for this repeater by node_id or alias
                var dockedList = dockedMap[node.node_id] || dockedMap[node.alias];
                if (!dockedList && node.alias) {
                    dockedList = dockedMap['@' + node.alias] || dockedMap[node.alias.replace(/^@/, '')];
                }
                if (!dockedList || dockedList.length === 0) continue;

                // Deduplicate docked nodes by node_id
                var uniqueDocked = [];
                var seenIds = {};
                for (var d = 0; d < dockedList.length; d++) {
                    var dNode = dockedList[d];
                    if (!dNode || !dNode.node_id || seenIds[dNode.node_id]) continue;
                    seenIds[dNode.node_id] = true;
                    uniqueDocked.push(dNode);
                }
                if (uniqueDocked.length === 0) continue;

                // When orbital mode is active, always mark the repeater as the orbital ring beacon (purple if favorited)
                var el = marker.getElement();
                if (el) {
                    var dot = el.querySelector('.node-dot');
                    if (dot) {
                        dot.classList.add('orbital-ring-repeater');
                        if (node.is_favorite) {
                            dot.classList.add('orbital-ring-repeater-fav');
                        }
                    }
                }

                // If zoomed out (currentZoom < 14), ONLY show the ring. Do not draw orbit circles or satellites!
                if (!showSatellites) {
                    continue;
                }

                var safeRepId = escapeJsString(node.node_id).replace(/[^a-zA-Z0-9_-]/g, '_');
                var repAliasStr = escapeHtml(node.alias || node.node_id || 'Repeater');
                var isFav = !!node.is_favorite;
                var repColor = isFav ? 'var(--favorite-color, #AA55FF)' : 'var(--orbital-repeater-color, #FFD335)';
                var repLabel = (isFav ? '★ ' : '') + repAliasStr;

                var N = uniqueDocked.length;
                var hasOverflow = N > 5;
                var visibleCount = hasOverflow ? 5 : N;
                var totalPositions = hasOverflow ? 6 : visibleCount;
                var isPaused = !!(window._pausedOrbitals && window._pausedOrbitals[safeRepId]);

                // Calculate deterministic pseudo-random starting angle offset for this repeater
                var repHash = 0;
                var idStr = String(node.node_id || node.alias || 'rep');
                for (var h = 0; h < idStr.length; h++) {
                    repHash = (repHash * 31 + idStr.charCodeAt(h)) & 0xFFFFFF;
                }
                var startAngleOffset = ((repHash % 360) * Math.PI) / 180;

                var svgDefs = ['<defs>'];
                var svgTrails = [];
                var svgNodes = [];

                for (var i = 0; i < totalPositions; i++) {
                    var angle = -Math.PI / 2 + startAngleOffset + (i * 2 * Math.PI / totalPositions);
                    var x = (60 + 46 * Math.cos(angle)).toFixed(1);
                    var y = (60 + 46 * Math.sin(angle)).toFixed(1);

                    if (i < visibleCount) {
                        var sat = uniqueDocked[i];
                        var rawAlias = sat.alias || sat.node_id || 'Companion';
                        var safeAlias = escapeHtml(rawAlias);
                        var lastHeardStr = formatLastHeard(sat.last_heard);
                        var chanStr = sat.channel ? escapeHtml(sat.channel) : 'Public';
                        var repNameStr = escapeHtml(node.alias || node.node_id || 'Repeater');
                        var snrStr = (sat.snr !== null && sat.snr !== undefined && sat.snr !== 0) ? ((Number(sat.snr) > 0 ? '+' : '') + Number(sat.snr).toFixed(1) + ' dB') : 'N/A';
                        var isUnknownFirst = !!sat.is_unknown_first_hop;
                        var satColor = isUnknownFirst ? '#EF4444' : '#00FFFF';
                        var satBorder = isUnknownFirst ? '#991B1B' : '#047857';

                        // Glowing trailing comet tail trailing behind the node along the circular orbit
                        var trailAngle = angle - 0.58;
                        var xTail = (60 + 46 * Math.cos(trailAngle)).toFixed(1);
                        var yTail = (60 + 46 * Math.sin(trailAngle)).toFixed(1);
                        var gradId = 'trail_grad_' + safeRepId + '_' + i;

                        svgDefs.push(
                            '<linearGradient id="' + gradId + '" x1="' + xTail + '" y1="' + yTail + '" x2="' + x + '" y2="' + y + '" gradientUnits="userSpaceOnUse">' +
                            '<stop offset="0%" stop-color="' + satColor + '" stop-opacity="0" />' +
                            '<stop offset="30%" stop-color="' + satColor + '" stop-opacity="0.25" />' +
                            '<stop offset="70%" stop-color="' + satColor + '" stop-opacity="0.65" />' +
                            '<stop offset="100%" stop-color="' + satColor + '" stop-opacity="0.95" />' +
                            '</linearGradient>'
                        );

                        svgTrails.push(
                            '<path d="M ' + xTail + ' ' + yTail + ' A 46 46 0 0 1 ' + x + ' ' + y + '" ' +
                            'fill="none" stroke="url(#' + gradId + ')" stroke-width="3.8" stroke-linecap="round" pointer-events="none" ' +
                            'style="filter: drop-shadow(0 0 4px ' + satColor + ');" />'
                        );

                        svgNodes.push(
                            '<g class="orbital-sat-node" style="pointer-events:all; cursor:pointer;" ' +
                            'data-alias="' + safeAlias + '" ' +
                            'data-time="' + lastHeardStr + '" ' +
                            'data-channel="' + chanStr + '" ' +
                            'data-repeater="' + repNameStr + '" ' +
                            'data-snr="' + snrStr + '" ' +
                            'data-node-id="' + encodeURIComponent(sat.node_id) + '" ' +
                            'data-unknown-first="' + (isUnknownFirst ? 'true' : 'false') + '" ' +
                            'data-first-hop="' + escapeHtml(sat.first_hop_alias || '') + '" ' +
                            'data-color="' + satColor + '" ' +
                            'onmouseenter="orbitalSatHover(this, event)" ' +
                            'onmouseleave="orbitalSatLeave(this)" ' +
                            'onclick="onOrbitalSatClicked(this, event)">' +
                            '<circle cx="' + x + '" cy="' + y + '" r="12" fill="transparent" />' +
                            '<circle class="sat-dot" cx="' + x + '" cy="' + y + '" r="4.25" fill="' + satColor + '" stroke="' + satBorder + '" stroke-width="1.1" style="filter:drop-shadow(0 0 6px ' + satColor + '); transition:r 0.15s ease, fill 0.15s ease;" />' +
                            '</g>'
                        );
                    } else if (i === 5 && hasOverflow) {
                        var overflowCount = N - 5;
                        var extraNames = uniqueDocked.slice(5).map(function(s) { return escapeHtml(s.alias || s.node_id); });
                        var joinedNames = extraNames.join('|||');
                        var repNameStr = escapeHtml(node.alias || node.node_id || 'Repeater');

                        svgNodes.push(
                            '<g class="orbital-overflow-badge orbital-sat-node" style="pointer-events:all; cursor:pointer;" ' +
                            'data-count="' + overflowCount + '" ' +
                            'data-names="' + joinedNames + '" ' +
                            'data-repeater="' + repNameStr + '" ' +
                            'onmouseenter="orbitalOverflowHover(this, event)" ' +
                            'onmouseleave="orbitalOverflowLeave(this)">' +
                            '<rect x="' + (Number(x) - 13).toFixed(1) + '" y="' + (Number(y) - 8).toFixed(1) + '" width="26" height="16" rx="8" fill="#111827" stroke="' + repColor + '" stroke-width="1.2" style="filter:drop-shadow(0 0 5px rgba(255, 211, 53, 0.45));" />' +
                            '<text x="' + x + '" y="' + (Number(y) + 3.5).toFixed(1) + '" text-anchor="middle" fill="' + repColor + '" font-size="9" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-weight="bold">+' + overflowCount + '</text>' +
                            '</g>'
                        );
                    }
                }

                svgDefs.push('</defs>');

                var svgParts = [
                    '<svg width="120" height="120" viewBox="0 0 120 120" style="overflow:visible; pointer-events:none;">',
                    svgDefs.join(''),
                    // Pale grey dotted orbit circle (radius 46px, centered at 60,60)
                    '<circle cx="60" cy="60" r="46" fill="none" stroke="rgba(255, 255, 255, 0.40)" stroke-width="1.2" stroke-dasharray="3, 4" pointer-events="none" />',
                    // Repeater alias label underneath repeater ring
                    '<text x="60" y="80" text-anchor="middle" fill="' + repColor + '" font-size="9" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-weight="bold" style="text-shadow: 0 1px 3px rgba(0,0,0,0.95); pointer-events:none;">' + repLabel + '</text>',
                    // Rotating satellites container
                    '<g class="orbital-sat-group">',
                    svgTrails.join(''),
                    svgNodes.join(''),
                    '</g>',
                    '</svg>'
                ];

                var orbIcon = L.divIcon({
                    className: 'orbital-host-container',
                    html: '<div id="orbital_host_' + safeRepId + '" class="orbital-host-inner' + (isPaused ? ' orbital-paused' : '') + '">' + svgParts.join('') + '</div>',
                    iconSize: [120, 120],
                    iconAnchor: [60, 60]
                });

                var orbMarker = L.marker([node.lat, node.lon], {
                    icon: orbIcon,
                    interactive: false,
                    zIndexOffset: 300
                });
                orbitalLayerGroup.addLayer(orbMarker);
            }
        }

        function orbitalSatHover(el, ev) {
            var alias = el.getAttribute('data-alias') || 'Companion';
            var lastHeard = el.getAttribute('data-time') || 'recently';
            var channel = el.getAttribute('data-channel') || 'Public';
            var repeater = el.getAttribute('data-repeater') || 'Repeater';
            var snr = el.getAttribute('data-snr') || 'N/A';
            var isUnknownFirst = (el.getAttribute('data-unknown-first') === 'true');
            var defaultColor = el.getAttribute('data-color') || (isUnknownFirst ? '#EF4444' : '#00FFFF');

            var dot = el.querySelector('.sat-dot');
            if (dot) {
                dot.setAttribute('r', '5.5');
                dot.setAttribute('fill', '#FFFFFF');
            }

            var noticeHtml = '';
            if (isUnknownFirst) {
                noticeHtml = '<div style="color:#F87171; font-size:10px; margin-top:2px; font-weight:600;">⚠️ Assigned here: Initial repeater unknown</div>';
            }

            showOrbitalTooltip(ev,
                '<div style="font-size:12px; font-weight:bold; color:' + defaultColor + '; margin-bottom:3px;">👤 ' + alias + ' <span style="font-size:10px; color:#9CA3AF; font-weight:normal;">(No GPS)</span></div>' +
                '<div style="color:#9CA3AF; font-size:10.5px; margin-bottom:2px;">Last heard: <span style="color:#E5E7EB;">' + lastHeard + '</span> on <span style="color:#38BDF8;">' + channel + '</span></div>' +
                '<div style="color:#9CA3AF; font-size:10.5px;">Relayed via: <span style="color:var(--orbital-repeater-color, #FFD700); font-weight:bold;">@' + repeater.replace(/^@/, '') + '</span> <span style="color:#10B981;">(SNR: ' + snr + ')</span></div>' +
                noticeHtml +
                '<div style="margin-top:4px; font-size:9.5px; color:#38BDF8; font-weight:bold; border-top:1px solid #374151; padding-top:3px;">💬 Click to Direct Message</div>'
            );
        }

        function orbitalSatLeave(el) {
            var dot = el.querySelector('.sat-dot');
            if (dot) {
                var defaultColor = el.getAttribute('data-color') || '#00FFFF';
                dot.setAttribute('r', '4.25');
                dot.setAttribute('fill', defaultColor);
            }
            hideOrbitalTooltip();
        }

        function orbitalOverflowHover(el, ev) {
            var count = el.getAttribute('data-count') || '0';
            var repeater = el.getAttribute('data-repeater') || 'Repeater';
            var namesJoined = el.getAttribute('data-names') || '';
            var names = namesJoined ? namesJoined.split('|||') : [];
            var listHtml = names.slice(0, 10).map(function(n) {
                return '<div style="color:#E5E7EB; font-size:10px; margin-top:1px;">• ' + n + '</div>';
            }).join('');
            if (names.length > 10) {
                listHtml += '<div style="color:#9CA3AF; font-size:9px; margin-top:2px;">...and ' + (names.length - 10) + ' more</div>';
            }
            showOrbitalTooltip(ev,
                '<div style="font-size:11px; font-weight:bold; color:var(--orbital-repeater-color, #FFD700); margin-bottom:2px;">🛰️ +' + count + ' More Docked Companions</div>' +
                '<div style="color:#9CA3AF; font-size:10px; margin-bottom:4px;">Relayed via @' + repeater.replace(/^@/, '') + '</div>' +
                '<div style="border-top:1px solid #374151; padding-top:3px;">' + listHtml + '</div>'
            );
        }

        function orbitalOverflowLeave(el) {
            hideOrbitalTooltip();
        }

        function showOrbitalTooltip(ev, html) {
            var tip = document.getElementById('orbital-tactical-tooltip');
            if (!tip) return;
            tip.innerHTML = html;
            tip.style.display = 'block';
            positionOrbitalTooltip(ev);
        }

        function positionOrbitalTooltip(ev) {
            var tip = document.getElementById('orbital-tactical-tooltip');
            if (!tip || tip.style.display === 'none' || !ev) return;
            var x = ev.clientX;
            var y = ev.clientY;
            tip.style.left = x + 'px';
            tip.style.top = (y - 10) + 'px';
        }

        function hideOrbitalTooltip() {
            var tip = document.getElementById('orbital-tactical-tooltip');
            if (tip) tip.style.display = 'none';
        }

        window.addEventListener('mousemove', function(e) {
            var tip = document.getElementById('orbital-tactical-tooltip');
            if (tip && tip.style.display === 'block') {
                positionOrbitalTooltip(e);
            }
        });
        map.on('movestart zoomstart', hideOrbitalTooltip);

        function setRfLinks(linksList) {
            rfLinks.forEach(function(l) { map.removeLayer(l); });
            rfLinks = [];

            linksList.forEach(function(link) {
                if (!link.coords || link.coords.length < 2) return;
                var color = link.snr >= 10 ? '#2ea043' : (link.snr >= 0 ? '#d29922' : '#f85149');
                var line = L.polyline(link.coords, {
                    color: color,
                    weight: 1,
                    opacity: 0.25,
                    dashArray: '3, 6'
                }).addTo(map);
                line.bindTooltip('RF Link: ' + (link.snr ? (link.snr > 0 ? '+' : '') + link.snr.toFixed(1) + ' dB' : ''), { sticky: true });
                rfLinks.push(line);
            });
        }

        var _recentNodePings = {};
        function pulseOriginNode(coord, senderId, senderName, color) {
            if (!coord || coord.length < 2) return;
            var pingKey = (senderId || '') + ':' + coord[0].toFixed(4) + ',' + coord[1].toFixed(4);
            var now = performance.now();
            if (_recentNodePings[pingKey] && (now - _recentNodePings[pingKey] < 1200)) {
                return;
            }
            _recentNodePings[pingKey] = now;

            var displayName = senderName || '';
            var originMarker = senderId && markers[senderId] ? markers[senderId] : null;
            if (!originMarker) {
                for (var id in markers) {
                    var mPos = markers[id].getLatLng();
                    if (Math.abs(mPos.lat - coord[0]) < 0.001 && Math.abs(mPos.lng - coord[1]) < 0.001) {
                        originMarker = markers[id];
                        break;
                    }
                }
            }

            if (!displayName && originMarker && originMarker.getTooltip()) {
                displayName = originMarker.getTooltip().getContent();
            }
            if (!displayName) {
                displayName = senderId ? ('@' + senderId) : 'Transmission';
            }

            // Clean up any HTML tags from tooltip
            var cleanName = displayName.replace(/<[^>]*>/g, '').trim();

            var pingColor = color || '#3B82F6';
            var ringStyle = 'border-color: ' + pingColor + '; background: ' + (pingColor.startsWith('#') ? (pingColor + '25') : 'rgba(59, 130, 246, 0.15)') + ';';
            var dotStyle = 'background: ' + pingColor + '; box-shadow: 0 0 8px ' + pingColor + ';';
            var badgeStyle = 'border-color: ' + (pingColor.startsWith('#') ? (pingColor + '55') : 'rgba(59, 130, 246, 0.35)') + ';';

            // Add sleek tactical radar blip + floating sender badge matching packet type
            var blipIcon = L.divIcon({
                className: 'radar-blip-wrap',
                html: '<div class="radar-blip-container">' +
                          '<div class="radar-ping-ring" style="' + ringStyle + '"></div>' +
                          '<div class="radar-center-dot" style="' + dotStyle + '"></div>' +
                          '<div class="radar-sender-badge" style="' + badgeStyle + '">' + cleanName + '</div>' +
                      '</div>',
                iconSize: [20, 20],
                iconAnchor: [10, 10]
            });
            var blipMarker = L.marker(coord, { icon: blipIcon, zIndexOffset: 3000 }).addTo(map);

            // Stop blip and remove after 3 seconds
            setTimeout(function() {
                try {
                    map.removeLayer(blipMarker);
                } catch(e) {}
            }, 3000);
        }

        function renderCanvasAnimations(now) {
            if (!animCtx) return;
            if (activePulses.length === 0 && activeAnimations.length === 0) {
                isCanvasAnimating = false;
                animCtx.clearRect(0, 0, animCanvas.clientWidth, animCanvas.clientHeight);
                return;
            }

            var dt = Math.min(32, now - (_lastAnimTime || now));
            _lastAnimTime = now;
            var dtSec = dt / 1000.0;

            animCtx.clearRect(0, 0, animCanvas.clientWidth, animCanvas.clientHeight);
            var W = animCanvas.clientWidth;
            var H = animCanvas.clientHeight;

            // 1. Render Pulses (Expanding Radar Rings)
            for (var i = activePulses.length - 1; i >= 0; i--) {
                var p = activePulses[i];
                p.r += 62 * dtSec;
                p.op -= 1.15 * dtSec;
                p.hl_r += 24 * dtSec;
                p.hl_op -= 1.35 * dtSec;

                if (p.op <= 0 || (now - p.startTime > 5000)) {
                    activePulses.splice(i, 1);
                    continue;
                }

                var layerPt = map.latLngToLayerPoint(p.pos);
                var px = layerPt.x - canvasTopLeft.x;
                var py = layerPt.y - canvasTopLeft.y;

                if (px >= -p.r && px <= W + p.r && py >= -p.r && py <= H + p.r) {
                    // Inner expanding pulse ring
                    animCtx.beginPath();
                    animCtx.arc(px, py, p.r, 0, Math.PI * 2);
                    animCtx.lineWidth = Math.max(0.4, 3.0 - p.r * 0.04);
                    animCtx.strokeStyle = p.color;
                    animCtx.globalAlpha = Math.max(0, p.op);
                    animCtx.stroke();

                    // Outer tactical highlight ring
                    if (p.hl_op > 0) {
                        animCtx.beginPath();
                        animCtx.arc(px, py, p.hl_r, 0, Math.PI * 2);
                        animCtx.lineWidth = p.hl_op > 0.4 ? 3 : 2;
                        animCtx.strokeStyle = p.color;
                        animCtx.globalAlpha = Math.max(0, p.hl_op);
                        animCtx.stroke();
                    }
                }
            }
            animCtx.globalAlpha = 1.0;

            // 2. Render Traveling Particle Beams (CoreScope Contrail + Dot)
            for (var j = activeAnimations.length - 1; j >= 0; j--) {
                var anim = activeAnimations[j];
                anim.progress += dt / (anim.duration || 550);
                var t = Math.min(1.0, anim.progress);

                var fromPt = map.latLngToLayerPoint(anim.from);
                var toPt = map.latLngToLayerPoint(anim.to);
                var fx = fromPt.x - canvasTopLeft.x;
                var fy = fromPt.y - canvasTopLeft.y;
                var tx = toPt.x - canvasTopLeft.x;
                var ty = toPt.y - canvasTopLeft.y;

                var curX = fx + (tx - fx) * t;
                var curY = fy + (ty - fy) * t;

                // Contrail glow
                animCtx.beginPath();
                animCtx.moveTo(fx, fy);
                animCtx.lineTo(curX, curY);
                animCtx.strokeStyle = anim.color;
                animCtx.lineWidth = 6;
                animCtx.globalAlpha = (anim.opacity || 0.9) * 0.25;
                animCtx.lineCap = 'round';
                animCtx.stroke();

                // Core transmission line
                animCtx.beginPath();
                animCtx.moveTo(fx, fy);
                animCtx.lineTo(curX, curY);
                if (anim.isInferred) {
                    animCtx.setLineDash([2, 6]);
                    animCtx.lineWidth = 2.0;
                } else if (anim.isDashed) {
                    animCtx.setLineDash([6, 8]);
                    animCtx.lineWidth = 2.2;
                } else {
                    animCtx.lineWidth = 2.5;
                }
                animCtx.strokeStyle = anim.color;
                animCtx.globalAlpha = anim.opacity || 0.9;
                animCtx.stroke();
                animCtx.setLineDash([]);

                // Leading glowing particle dot
                animCtx.beginPath();
                animCtx.arc(curX, curY, 3.8, 0, Math.PI * 2);
                animCtx.fillStyle = '#FFFFFF';
                animCtx.fill();
                animCtx.lineWidth = 1.8;
                animCtx.strokeStyle = anim.color;
                animCtx.stroke();
                animCtx.globalAlpha = 1.0;

                if (t >= 1.0) {
                    activeAnimations.splice(j, 1);
                    triggerCanvasPulse(anim.to, anim.color);
                    if (anim.onComplete) {
                        anim.onComplete();
                    }
                }
            }

            if (activePulses.length > 0 || activeAnimations.length > 0) {
                requestAnimationFrame(renderCanvasAnimations);
            } else {
                isCanvasAnimating = false;
                animCtx.clearRect(0, 0, animCanvas.clientWidth, animCanvas.clientHeight);
            }
        }

        var _recentPulses = {};
        function triggerCanvasPulse(coord, color) {
            if (!coord || !map) return;
            var pKey = (coord[0] !== undefined && coord[1] !== undefined) ? (coord[0].toFixed(4) + ',' + coord[1].toFixed(4)) : '';
            var now = performance.now();
            if (pKey && _recentPulses[pKey] && (now - _recentPulses[pKey] < 800)) {
                return;
            }
            if (pKey) _recentPulses[pKey] = now;
            activePulses.push({
                pos: coord,
                color: color || '#3B82F6',
                r: 3,
                op: 0.95,
                hl_r: 10,
                hl_op: 0.95,
                startTime: performance.now()
            });
            if (!isCanvasAnimating) {
                isCanvasAnimating = true;
                _lastAnimTime = performance.now();
                requestAnimationFrame(renderCanvasAnimations);
            }
        }
        window.triggerCanvasPulse = triggerCanvasPulse;

        var _recentPathSignatures = {};
        function drawPacketPath(coords, meta) {
            if (!coords || coords.length < 2) return;
            meta = meta || {};

            var pathSig = (meta.packet_id || '') + ':' + (meta.channel || '') + ':' + (meta.text || '') + ':' + coords[0].join(',') + '->' + coords[coords.length - 1].join(',');
            var now = performance.now();
            if (pathSig.length > 5 && _recentPathSignatures[pathSig] && (now - _recentPathSignatures[pathSig] < 2000)) {
                return;
            }
            _recentPathSignatures[pathSig] = now;

            if (window._is3DActive && typeof tracePacketPath3D === 'function') {
                tracePacketPath3D(meta, coords);
            }

            var pType = (meta.payload_type || meta.route_type || 'FLOOD').toUpperCase();
            var beamColor = TYPE_COLORS[pType] || (meta.color && meta.color !== 'orange' && meta.color !== 'green' ? meta.color : (meta.is_incoming ? '#10B981' : '#3B82F6'));

            // Tactical origin radar pulse with matching packet type color
            triggerCanvasPulse(coords[0], beamColor);
            pulseOriginNode(coords[0], meta.sender_id, meta.sender_name, beamColor);

            // Sequential CoreScope particle beam execution across hops
            function runHopAnimation(hopIdx) {
                if (hopIdx >= coords.length - 1) {
                    triggerCanvasPulse(coords[coords.length - 1], beamColor);
                    pulseOriginNode(coords[coords.length - 1], meta.recipient_id, meta.recipient_name, beamColor);
                    return;
                }

                var hMeta = (meta.hop_metas && meta.hop_metas[hopIdx]) ? meta.hop_metas[hopIdx] : {};
                var isHopAmbiguous = Boolean(hMeta.is_ambiguous || meta.is_speculative || meta.is_ghost);
                var isHopInferred = Boolean(hMeta.is_inferred);

                activeAnimations.push({
                    from: coords[hopIdx],
                    to: coords[hopIdx + 1],
                    progress: 0,
                    duration: 520,
                    color: beamColor,
                    isDashed: isHopAmbiguous,
                    isInferred: isHopInferred,
                    onComplete: function() {
                        runHopAnimation(hopIdx + 1);
                    }
                });
                if (!isCanvasAnimating) {
                    isCanvasAnimating = true;
                    _lastAnimTime = performance.now();
                    requestAnimationFrame(renderCanvasAnimations);
                }
            }
            runHopAnimation(0);
        }
        window.triggerCoreScopeTrace = drawPacketPath;

        // --- CoreScope Live Packet HUD Controls ---
        window.setPacketHudVisible = function(visible) {
            var el = document.getElementById('livePacketHud');
            if (el) el.style.display = visible ? 'flex' : 'none';
            if (window.pyBridge && window.pyBridge.on_packet_hud_toggled) {
                window.pyBridge.on_packet_hud_toggled(Boolean(visible));
            }
        };

        window.toggleMapLegend = function(force) {
            var el = document.getElementById('liveLegend');
            if (!el) return;
            var show = (force !== undefined) ? Boolean(force) : (el.style.display === 'none');
            el.style.display = show ? 'block' : 'none';
            if (window.pyBridge && window.pyBridge.on_map_legend_toggled) {
                window.pyBridge.on_map_legend_toggled(Boolean(show));
            }
        };

        window.cycleHudCorner = function() {
            var el = document.getElementById('livePacketHud');
            if (!el) return;
            var corners = ['bl', 'tl', 'tr', 'br'];
            var curr = el.getAttribute('data-position') || 'bl';
            var next = corners[(corners.indexOf(curr) + 1) % corners.length];
            el.style.left = '';
            el.style.top = '';
            el.style.right = '';
            el.style.bottom = '';
            el.setAttribute('data-position', next);
            try { sessionStorage.removeItem('overlay_pos_live_packet_hud'); } catch(e) {}
        };

        var _recentHudEntries = {};

        window.addPacketToHud = function(meta, coords) {
            var feed = document.getElementById('liveHudContent');
            if (!feed) return;
            var empty = document.getElementById('liveHudEmpty');
            if (empty) empty.style.display = 'none';

            meta = meta || {};
            var pType = (meta.payload_type || meta.route_type || 'FLOOD').toUpperCase();
            var color = TYPE_COLORS[pType] || '#3B82F6';

            var pSource = (meta.source || 'radio').toLowerCase();
            var isRadio = (pSource !== 'mqtt');

            // Unique event signature - specific to message payload, packet ID, node advert, or raw hex
            var eventKey = '';
            if (meta.channel && meta.text && meta.text.trim()) {
                eventKey = 'msg:' + meta.channel.toLowerCase().replace('#', '') + ':' + meta.text.trim();
            } else if (meta.text && meta.text.trim()) {
                eventKey = 'msg::' + meta.text.trim();
            } else if (meta.raw_hex && meta.raw_hex.length >= 8) {
                eventKey = 'hex:' + meta.raw_hex.toUpperCase();
            } else if (meta.sender_id && pType === 'ADVERT') {
                eventKey = 'adv:' + meta.sender_id.toLowerCase().replace('!', '');
            } else if (meta.packet_id && !meta.packet_id.startsWith('path-') && !meta.packet_id.startsWith('mqtt-')) {
                eventKey = 'pkt:' + meta.packet_id;
            }

            var now = performance.now();

            if (eventKey) {
                var cached = _recentHudEntries[eventKey];
                // Tight 3.0-second deduplication window so valid subsequent messages are never dropped or removed
                var isRecent = cached && (now - cached.time < 3000);

                if (isRecent) {
                    if (isRadio && cached.source === 'mqtt') {
                        // Radio heard the same event within 3 seconds of MQTT:
                        // Remove the existing MQTT DOM entry so Radio takes priority and replaces it!
                        var existingItem = feed.querySelector('.live-feed-item[data-event-key="' + CSS.escape(eventKey) + '"][data-source="mqtt"]');
                        if (existingItem) {
                            existingItem.remove();
                        }
                    } else if (!isRadio && cached.source === 'radio') {
                        // MQTT received an event that local radio ALREADY heard!
                        // Drop MQTT immediately so RF retains absolute priority!
                        return;
                    } else {
                        // Duplicate event within window! Drop incoming so only 1 entry exists per event
                        return;
                    }
                }

                _recentHudEntries[eventKey] = { time: now, source: isRadio ? 'radio' : 'mqtt' };
            }

            var icon = '📦';
            if (pType.indexOf('GRP_TXT') !== -1 || pType.indexOf('MESSAGE') !== -1 || pType.indexOf('CHAN') !== -1) {
                icon = '💬';
            } else if (pType.indexOf('TXT_MSG') !== -1 || pType.indexOf('DIRECT') !== -1) {
                icon = '💬';
            } else if (pType.indexOf('RESPONSE') !== -1) {
                icon = '📡';
            } else if (pType.indexOf('PATH') !== -1) {
                icon = '🛣️';
            } else if (pType.indexOf('TRACE') !== -1) {
                icon = '🔍';
            } else if (pType.indexOf('ADVERT') !== -1) {
                icon = '🟢';
            }

            var hops = meta.hops || 1;
            var ambCount = meta.ambiguous_hops || 0;
            var infCount = meta.inferred_hops || 0;
            var hopTitle = hops + ' Hop' + (hops === 1 ? '' : 's');
            var hopBadgeExtra = '';
            if (ambCount > 0 && infCount > 0) {
                hopTitle += ' (' + ambCount + ' ambiguous, ' + infCount + ' inferred)';
                hopBadgeExtra = ' <span style="font-size:8px;color:#FACC15;font-weight:bold;" title="' + hopTitle + '">⤍</span>';
            } else if (ambCount > 0) {
                hopTitle += ' (' + ambCount + ' ambiguous hash collision' + (ambCount === 1 ? '' : 's') + ')';
                hopBadgeExtra = ' <span style="font-size:8px;color:#FACC15;font-weight:bold;" title="' + hopTitle + '">⤍</span>';
            } else if (infCount > 0) {
                hopTitle += ' (' + infCount + ' inferred unmapped step' + (infCount === 1 ? '' : 's') + ')';
                hopBadgeExtra = ' <span style="font-size:8px;color:#94A3B8;font-weight:bold;" title="' + hopTitle + '">⋯</span>';
            }
            var hopHtml = '<span class="feed-hops" title="' + hopTitle + '">' + hops + '➔' + hopBadgeExtra + '</span>';

            var text = meta.text || meta.sender_name || meta.sender_id || '';
            if (meta.channel) {
                text = '[' + meta.channel + '] ' + text;
            }
            if (/^[0-9a-fA-F]{2,4}$/.test(text.trim())) {
                text = 'Node [' + text.trim().toUpperCase() + ']';
            }
            if (text.length > 34) {
                text = text.substring(0, 34) + '…';
            }

            var timeStr = (new Date()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

            var srcHtml = '';
            if (!isRadio) {
                srcHtml = '<span class="feed-src feed-src-mqtt" title="Ingested from MQTT Broker">MQTT</span>';
            } else {
                srcHtml = '<span class="feed-src feed-src-rf" title="LoRa Physical Radio Reception">RF</span>';
            }

            var item = document.createElement('div');
            item.className = 'live-feed-item';
            if (eventKey) {
                item.setAttribute('data-event-key', eventKey);
            }
            item.setAttribute('data-source', isRadio ? 'radio' : 'mqtt');
            item.style.borderLeftColor = color;
            item.innerHTML = 
                '<span class="feed-icon" style="color:' + color + '">' + icon + '</span>' +
                srcHtml +
                '<span class="feed-type" style="color:' + color + '">' + escapeHtml(pType) + '</span>' +
                hopHtml +
                '<span class="feed-text">' + escapeHtml(text) + '</span>' +
                '<span class="feed-time">' + timeStr + '</span>';

            item.onclick = function() {
                if (coords && coords.length >= 2) {
                    drawPacketPath(coords, meta);
                    if (window.tracePacketPath3D) {
                        window.tracePacketPath3D(meta, coords);
                    }
                }
            };

            feed.prepend(item);

            // Cap at 30 items
            var items = feed.querySelectorAll('.live-feed-item');
            if (items.length > 30) {
                for (var i = 30; i < items.length; i++) {
                    items[i].remove();
                }
            }
        };

        window._activeVisualisedCoords = null;
        window._activeVisualisedMeta = null;
        window._visualisedPathActive = false;
        window._isSwitchingHop = false;

        window.candidatePreviewMarker = null;

        window.previewHopCandidate = function(lat, lon, alias, iconChar) {
            window.clearCandidatePreview();
            if (lat == null || lon == null || isNaN(lat) || isNaN(lon)) return;
            var iconBadge = iconChar || '📡';
            var previewHtml = '<div class="cyan-candidate-pulse-wrap" style="position: relative; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; pointer-events: none;">' +
                '<div style="position: absolute; width: 32px; height: 32px; border-radius: 50%; border: 2px solid #00FFFF; background: rgba(0, 255, 255, 0.25); animation: cyanCandidatePulse 1.1s infinite ease-out;"></div>' +
                '<div style="position: relative; width: 10px; height: 10px; border-radius: 50%; background: #00FFFF; box-shadow: 0 0 12px #00FFFF;"></div>' +
                '<div style="position: absolute; top: -22px; white-space: nowrap; background: rgba(15, 20, 28, 0.95); color: #00FFFF; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px; border: 1px solid #00FFFF; box-shadow: 0 2px 8px rgba(0,0,0,0.6);">' + iconBadge + ' ' + alias + '</div>' +
                '</div>';
            var icon = L.divIcon({
                className: 'cyan-preview-icon',
                html: previewHtml,
                iconSize: [32, 32],
                iconAnchor: [16, 16]
            });
            window.candidatePreviewMarker = L.marker([lat, lon], { icon: icon, zIndexOffset: 5000 }).addTo(map);
        };

        window.clearCandidatePreview = function() {
            if (window.candidatePreviewMarker) {
                try { map.removeLayer(window.candidatePreviewMarker); } catch(e) {}
                window.candidatePreviewMarker = null;
            }
        };

        function clearVisualisedPath() {
            window._visualisedPathActive = false;
            window._activeVisualisedSegments = null;
            window._activeVisualisedCoords = null;
            window._activeVisualisedMeta = null;
            window.clearCandidatePreview();
            var panel = document.getElementById('visualised-floating-panel');
            if (panel) panel.style.display = 'none';
            if (visualisedPathLayer) {
                try {
                    map.removeLayer(visualisedPathLayer);
                } catch(e) {}
                visualisedPathLayer = null;
            }
            if (visualisedHighlightMarkers && visualisedHighlightMarkers.length > 0) {
                for (var i = 0; i < visualisedHighlightMarkers.length; i++) {
                    try {
                        map.removeLayer(visualisedHighlightMarkers[i]);
                    } catch(e) {}
                }
                visualisedHighlightMarkers = [];
            }
            if (visualised3DArcs) {
                visualised3DArcs = [];
                if (map3d) map3d.triggerRepaint();
            }
            map.closePopup();
        }

        function closeVisualisedPanel() {
            clearVisualisedPath();
            if (pyBridge && pyBridge.on_visualised_path_closed) {
                pyBridge.on_visualised_path_closed();
            }
        }

        function clearRepeaterNeighbors() {
            if (neighborsOverlayLayer) {
                try {
                    map.removeLayer(neighborsOverlayLayer);
                } catch(e) {}
                neighborsOverlayLayer = null;
            }
            var banner = document.getElementById('neighbors-overlay-banner');
            if (banner) banner.remove();
            if (pyBridge && pyBridge.on_repeater_neighbors_cleared) {
                pyBridge.on_repeater_neighbors_cleared();
            }
        }

        function drawRepeaterNeighbors(data) {
            if (!data) return;
            clearRepeaterNeighbors();

            neighborsOverlayLayer = L.layerGroup().addTo(map);

            var rep = data.repeater;
            var neighbors = data.neighbors || [];
            var allBounds = [];

            // 1. Host Repeater Marker
            if (rep && rep.coord && rep.coord.length === 2 && rep.coord[0] != null && rep.coord[1] != null) {
                allBounds.push(rep.coord);
                var repHaloHtml = '<div style="position:relative; width:44px; height:44px; display:flex; align-items:center; justify-content:center; pointer-events:none;">' +
                    '<div style="position:absolute; width:44px; height:44px; border-radius:50%; border:2.5px solid #38BDF8; background:rgba(56,189,248,0.2); animation:hostRepeaterPulse 1.8s infinite ease-out;"></div>' +
                    '<div style="width:14px; height:14px; border-radius:50%; background:#38BDF8; border:2px solid #FFFFFF; box-shadow:0 0 10px #38BDF8;"></div>' +
                    '</div>';
                var repHalo = L.marker(rep.coord, {
                    icon: L.divIcon({
                        className: 'host-repeater-halo-wrap',
                        html: repHaloHtml,
                        iconSize: [44, 44],
                        iconAnchor: [22, 22]
                    }),
                    zIndexOffset: 1500
                }).addTo(neighborsOverlayLayer);
                repHalo.bindTooltip('<b>📡 ' + escapeHtml(rep.alias || rep.id) + '</b><br><span style="color:#38BDF8;">Host Repeater Station</span>', {
                    permanent: false,
                    direction: 'top',
                    className: 'leaflet-tooltip'
                });
            }

            var plottedCount = 0;
            // 2. Plot Each Neighbour
            for (var i = 0; i < neighbors.length; i++) {
                var n = neighbors[i];
                if (!n.coord || n.coord.length < 2 || n.coord[0] == null || n.coord[1] == null) continue;
                plottedCount++;
                allBounds.push(n.coord);

                // A. White Pulse Beacon
                var pulseHtml = '<div style="position:relative; width:38px; height:38px; display:flex; align-items:center; justify-content:center; pointer-events:auto;">' +
                    '<div style="position:absolute; width:38px; height:38px; border-radius:50%; border:2px solid #FFFFFF; background:rgba(255,255,255,0.35); animation:whiteNeighborPulse 1.4s infinite ease-out;"></div>' +
                    '<div style="width:12px; height:12px; border-radius:50%; background:#FFFFFF; border:2px solid #1C1C1C; box-shadow:0 0 12px #FFFFFF;"></div>' +
                    '</div>';
                var pulseIcon = L.divIcon({
                    className: 'white-neighbor-pulse-wrap',
                    html: pulseHtml,
                    iconSize: [38, 38],
                    iconAnchor: [19, 19]
                });
                var nMarker = L.marker(n.coord, { icon: pulseIcon, zIndexOffset: 1200 }).addTo(neighborsOverlayLayer);
                nMarker.bindTooltip('<b>📡 ' + escapeHtml(n.alias || n.node_id) + '</b><br><span style="color:#D1D5DB;">SNR: ' + escapeHtml(n.snr_str) + ' • Seen: ' + escapeHtml(n.time_str) + '</span>', {
                    permanent: false,
                    direction: 'top',
                    className: 'leaflet-tooltip'
                });

                // B. White Lines & Midpoint Box (if host repeater has coordinates)
                if (rep && rep.coord && rep.coord.length === 2 && rep.coord[0] != null && rep.coord[1] != null) {
                    // Outer subtle glow
                    L.polyline([rep.coord, n.coord], {
                        color: '#FFFFFF',
                        weight: 6,
                        opacity: 0.35,
                        lineCap: 'round',
                        lineJoin: 'round',
                        interactive: false
                    }).addTo(neighborsOverlayLayer);

                    // Solid clean white line
                    L.polyline([rep.coord, n.coord], {
                        color: '#FFFFFF',
                        weight: 2.5,
                        opacity: 0.95,
                        dashArray: '5, 7',
                        lineCap: 'round',
                        lineJoin: 'round',
                        interactive: false
                    }).addTo(neighborsOverlayLayer);

                    // C. Midpoint Box with SNR and Time Ago
                    var midLat = (rep.coord[0] + n.coord[0]) / 2.0;
                    var midLon = (rep.coord[1] + n.coord[1]) / 2.0;

                    var badgeHtml = '<div style="background:#1C1C1C; border:1.5px solid #FFFFFF; border-radius:6px; padding:3px 8px; color:#FFFFFF; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:11px; font-weight:bold; white-space:nowrap; box-shadow:0 2px 10px rgba(0,0,0,0.85); text-align:center; display:flex; flex-direction:column; gap:2px; pointer-events:auto;">' +
                        '<div><span style="color:#38BDF8;">SNR:</span> ' + escapeHtml(n.snr_str) + '</div>' +
                        '<div style="font-size:10px; color:#9CA3AF; font-weight:normal;">' + escapeHtml(n.time_str) + '</div>' +
                        '</div>';
                    var badgeIcon = L.divIcon({
                        className: 'neighbor-mid-badge-wrap',
                        html: badgeHtml,
                        iconSize: [85, 36],
                        iconAnchor: [42, 18]
                    });
                    L.marker([midLat, midLon], { icon: badgeIcon, zIndexOffset: 1400 }).addTo(neighborsOverlayLayer);
                }
            }

            // 3. Fit bounds safely so all neighbours and repeater are visible
            function applyBounds() {
                if (!map || allBounds.length === 0) return;
                try {
                    map.invalidateSize(false);
                } catch(e) {}

                if (allBounds.length === 1) {
                    var singleTarget = allBounds[0];
                    if (singleTarget && isFinite(singleTarget[0]) && isFinite(singleTarget[1])) {
                        try {
                            map.setView(singleTarget, Math.min(map.getZoom() || 12, 13));
                        } catch(e) {}
                    }
                    return;
                }

                var validBounds = allBounds.filter(function(c) {
                    return Array.isArray(c) && c.length >= 2 && isFinite(c[0]) && isFinite(c[1]);
                });
                if (validBounds.length === 0) return;
                if (validBounds.length === 1) {
                    try { map.setView(validBounds[0], Math.min(map.getZoom() || 12, 13)); } catch(e) {}
                    return;
                }

                var sz = map.getSize();
                if (sz && sz.x > 140 && sz.y > 140) {
                    try {
                        map.fitBounds(validBounds, { padding: [50, 50], maxZoom: 13 });
                    } catch(e) {
                        try { map.setView(validBounds[0], 12); } catch(e2) {}
                    }
                } else {
                    setTimeout(function() {
                        try {
                            map.invalidateSize(false);
                            map.fitBounds(validBounds, { padding: [50, 50], maxZoom: 13 });
                        } catch(e) {
                            try { map.setView(validBounds[0], 12); } catch(e2) {}
                        }
                    }, 120);
                }
            }
            applyBounds();

            // 4. Floating Banner
            var banner = document.getElementById('neighbors-overlay-banner');
            if (banner) banner.remove();

            var repTitle = escapeHtml(rep ? (rep.alias || rep.id) : 'Repeater');
            var bannerHtml = '<div id="neighbors-overlay-banner" style="position:absolute; top:48px; left:12px; z-index:1000; background:#222327; border:1px solid #414143; border-radius:6px; padding:6px 12px; color:#E5E7EB; font-size:12px; font-weight:bold; display:flex; align-items:center; gap:10px; box-shadow:0 4px 12px rgba(0,0,0,0.6);">' +
                '<span>🌐 Neighbours: <span style="color:#38BDF8;">@' + repTitle + '</span> (' + plottedCount + ' mapped with GPS)</span>' +
                '<button onclick="clearRepeaterNeighbors()" style="background:#2B2F38; color:#EF4444; border:1px solid #414143; border-radius:4px; padding:2px 8px; font-size:11px; cursor:pointer; font-weight:bold;">✖ Clear</button>' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', bannerHtml);
        }

        var _overlayZIndexCounter = 1050;

        function bringOverlayToFront(panel) {
            if (!panel) return;
            _overlayZIndexCounter += 1;
            panel.style.zIndex = _overlayZIndexCounter;
        }

        function makeOverlayDraggable(panelId, handleId, storageKey) {
            var panel = document.getElementById(panelId);
            var handle = document.getElementById(handleId) || panel;
            if (!panel || !handle) return;

            if (typeof L !== 'undefined' && L.DomEvent) {
                L.DomEvent.disableClickPropagation(panel);
                L.DomEvent.disableScrollPropagation(panel);
            }

            panel.addEventListener('mousedown', function() {
                bringOverlayToFront(panel);
            });

            // Restore position from sessionStorage if present and valid
            if (storageKey) {
                try {
                    var saved = sessionStorage.getItem('overlay_pos_' + storageKey);
                    if (saved) {
                        var pos = JSON.parse(saved);
                        if (typeof pos.left === 'number' && typeof pos.top === 'number') {
                            panel.removeAttribute('data-position');
                            var mapEl = document.getElementById('map');
                            var containerW = (mapEl ? mapEl.clientWidth : window.innerWidth);
                            var containerH = (mapEl ? mapEl.clientHeight : window.innerHeight);
                            var clampLeft = Math.max(0, Math.min(Math.max(0, containerW - 100), pos.left));
                            var clampTop = Math.max(0, Math.min(Math.max(0, containerH - 60), pos.top));
                            panel.style.left = clampLeft + 'px';
                            panel.style.top = clampTop + 'px';
                            panel.style.right = 'auto';
                            panel.style.bottom = 'auto';
                        }
                    }
                } catch(e) {}
            }

            var isDragging = false;
            var startX = 0, startY = 0;
            var origLeft = 0, origTop = 0;

            handle.addEventListener('mousedown', function(e) {
                if (e.button !== 0) return;
                if (e.target && e.target.closest && (
                    e.target.closest('button') || 
                    e.target.closest('input') || 
                    e.target.closest('select') || 
                    e.target.closest('a') || 
                    e.target.closest('label') ||
                    e.target.closest('.floating-route-close') ||
                    e.target.closest('.tropo-legend-close') ||
                    e.target.closest('.activity-bar-close') ||
                    e.target.closest('.new-nodes-close-btn') ||
                    e.target.closest('.thunderstorm-close-btn') ||
                    e.target.closest('.aurora-close-btn') ||
                    e.target.closest('.adsb-close-btn') ||
                    e.target.closest('.live-hud-btn') ||
                    e.target.closest('.panel-corner-btn')
                )) {
                    return;
                }

                bringOverlayToFront(panel);
                isDragging = true;
                panel.classList.add('active-drag');
                panel.removeAttribute('data-position');
                startX = e.clientX;
                startY = e.clientY;

                var rect = panel.getBoundingClientRect();
                var parentRect = panel.offsetParent ? panel.offsetParent.getBoundingClientRect() : { left: 0, top: 0 };
                origLeft = rect.left - parentRect.left;
                origTop = rect.top - parentRect.top;

                panel.style.left = origLeft + 'px';
                panel.style.top = origTop + 'px';
                panel.style.right = 'auto';
                panel.style.bottom = 'auto';

                document.body.style.userSelect = 'none';
                e.preventDefault();
            });

            document.addEventListener('mousemove', function(e) {
                if (!isDragging) return;
                var dx = e.clientX - startX;
                var dy = e.clientY - startY;
                var mapEl = document.getElementById('map');
                var containerW = (mapEl && mapEl.offsetWidth > 0 ? mapEl.clientWidth : window.innerWidth);
                var containerH = (mapEl && mapEl.offsetHeight > 0 ? mapEl.clientHeight : window.innerHeight);

                var maxW = Math.max(0, containerW - panel.offsetWidth);
                var maxH = Math.max(0, containerH - panel.offsetHeight);
                var newLeft = Math.max(0, Math.min(maxW, origLeft + dx));
                var newTop = Math.max(0, Math.min(maxH, origTop + dy));

                panel.style.left = newLeft + 'px';
                panel.style.top = newTop + 'px';
                panel.style.right = 'auto';
                panel.style.bottom = 'auto';
            });

            document.addEventListener('mouseup', function() {
                if (isDragging) {
                    isDragging = false;
                    panel.classList.remove('active-drag');
                    document.body.style.userSelect = '';
                    if (storageKey) {
                        try {
                            sessionStorage.setItem('overlay_pos_' + storageKey, JSON.stringify({
                                left: parseInt(panel.style.left, 10),
                                top: parseInt(panel.style.top, 10)
                            }));
                        } catch(e) {}
                    }
                }
            });
        }

        function makeOverlayVerticallyResizable(panelId, handleId, storageKey) {
            var panel = document.getElementById(panelId);
            var handle = document.getElementById(handleId);
            if (!panel || !handle) return;

            if (typeof L !== 'undefined' && L.DomEvent) {
                L.DomEvent.disableClickPropagation(handle);
                L.DomEvent.disableScrollPropagation(handle);
            }

            if (storageKey) {
                try {
                    var savedH = sessionStorage.getItem('overlay_height_' + storageKey);
                    if (savedH) {
                        var h = parseInt(savedH, 10);
                        if (!isNaN(h) && h >= 180 && h <= window.innerHeight - 20) {
                            panel.style.height = h + 'px';
                        }
                    }
                } catch(e) {}
            }

            var isResizing = false;
            var startY = 0;
            var startH = 0;

            handle.addEventListener('mousedown', function(e) {
                if (e.button !== 0) return;
                e.preventDefault();
                e.stopPropagation();
                isResizing = true;
                startY = e.clientY;
                startH = panel.offsetHeight;
                document.body.style.userSelect = 'none';
                document.body.style.cursor = 'ns-resize';
            });

            document.addEventListener('mousemove', function(e) {
                if (!isResizing) return;
                var dy = e.clientY - startY;
                var minH = 200;
                var maxH = window.innerHeight - 40;
                var newH = Math.max(minH, Math.min(maxH, startH + dy));
                panel.style.height = newH + 'px';
            });

            document.addEventListener('mouseup', function() {
                if (isResizing) {
                    isResizing = false;
                    document.body.style.userSelect = '';
                    document.body.style.cursor = '';
                    if (storageKey) {
                        try {
                            sessionStorage.setItem('overlay_height_' + storageKey, panel.offsetHeight.toString());
                        } catch(e) {}
                    }
                }
            });
        }

        function initAllDraggableOverlays() {
            // Visualised Route
            var vClose = document.getElementById('floating-route-close-btn');
            if (vClose) {
                vClose.addEventListener('click', function(e) {
                    e.stopPropagation();
                    closeVisualisedPanel();
                });
            }
            makeOverlayDraggable('visualised-floating-panel', 'floating-route-drag-handle', 'visualised_route');

            // ADS-B Live Flights
            makeOverlayDraggable('adsb-panel', 'adsb-drag-handle', 'adsb');

            // Thunderstorms & Radar
            makeOverlayDraggable('thunderstorm-panel', 'thunderstorm-drag-handle', 'thunderstorm');

            // Space Weather & Aurora
            makeOverlayDraggable('aurora-legend-panel', 'aurora-drag-handle', 'aurora');

            // Scopes Bar
            makeOverlayDraggable('scope-filter-bar', 'scope-filter-drag-handle', 'scopes');

            // Node Activity Heatmap
            makeOverlayDraggable('activity-heatmap-bar', 'activity-heatmap-drag-handle', 'activity_heatmap');

            // Tropo Ducting Forecast
            makeOverlayDraggable('tropo-legend-panel', 'tropo-drag-handle', 'tropo');

            // Path Mode Legend
            makeOverlayDraggable('path-mode-legend', 'path-mode-drag-handle', 'path_mode_legend');

            // Satellite Tracker
            makeOverlayDraggable('satellite-panel', 'satellite-drag-handle', 'satellites');
            makeOverlayVerticallyResizable('satellite-panel', 'satellite-resize-handle', 'satellites');

            // Search Node IDs
            makeOverlayDraggable('search-node-id-panel', 'search-node-drag-handle', 'search_node_id');

            // New Nodes Discovery
            makeOverlayDraggable('new-nodes-panel', 'new-nodes-drag-handle', 'new_nodes');

            // MQTT Discovered Nodes
            makeOverlayDraggable('mqtt-nodes-panel', 'mqtt-nodes-drag-handle', 'mqtt_nodes');

            // Live Packet Feed HUD & Map Legend (CoreScope Live Overlays)
            makeOverlayDraggable('livePacketHud', 'liveHudDragHandle', 'live_packet_hud');
            makeOverlayDraggable('liveLegend', 'liveLegendDragHandle', 'live_legend');

            // 3D Perspective Controls Floating Window
            makeOverlayDraggable('map3dControls', 'map3dControlsHeader', 'map_3d_controls');
        }
        setTimeout(initAllDraggableOverlays, 100);

        function rebuildSegmentsFromMeta(meta) {
            var nodeChain = [];
            if (meta.sender_coord) {
                nodeChain.push({ name: meta.sender_name, coords: meta.sender_coord, is_known: true });
            } else {
                nodeChain.push({ name: meta.sender_name, coords: null, is_known: false });
            }
            if (meta.repeaters) {
                for (var r = 0; r < meta.repeaters.length; r++) {
                    var rp = meta.repeaters[r];
                    var isPhant = !!rp.is_phantom;
                    var c = (!isPhant && rp.lat != null && rp.lon != null) ? [rp.lat, rp.lon] : null;
                    nodeChain.push({ name: rp.alias || rp.name, coords: c, is_known: !!rp.is_known, is_phantom: isPhant });
                }
            }
            if (meta.home_coord) {
                nodeChain.push({ name: meta.home_name, coords: meta.home_coord, is_known: true });
            } else if (meta.home_name) {
                nodeChain.push({ name: meta.home_name, coords: null, is_known: false });
            }
            var segs = [];
            var lastIdx = null;
            var pathCol = meta.color || (mapColors && mapColors.visualisedPath) || '#FF00FF';
            var phantomCol = meta.phantom_color || (mapColors && mapColors.phantomPath) || '#FFFF00';
            var unknownCol = meta.unknown_color || (mapColors && mapColors.unknownPath) || '#EF4444';
            var noGpsCol = meta.no_gps_color || (mapColors && mapColors.noGpsPath) || '#000000';
            for (var i = 0; i < nodeChain.length; i++) {
                if (nodeChain[i].coords) {
                    if (lastIdx !== null) {
                        var hasNoGps = false;
                        var hasUnk = false;
                        var hasPhantom = false;
                        var missingNames = [];
                        for (var k = lastIdx + 1; k < i; k++) {
                            if (!nodeChain[k].coords) {
                                missingNames.push(nodeChain[k].name);
                                if (nodeChain[k].is_phantom) {
                                    hasPhantom = true;
                                } else if (!nodeChain[k].is_known) {
                                    hasUnk = true;
                                } else {
                                    hasNoGps = true;
                                }
                            }
                        }
                        var sCol = pathCol;
                        if (hasPhantom) {
                            sCol = phantomCol;
                        } else if (hasNoGps) {
                            sCol = noGpsCol;
                        } else if (hasUnk) {
                            sCol = unknownCol;
                        }
                        segs.push({
                            coords: [nodeChain[lastIdx].coords, nodeChain[i].coords],
                            color: sCol,
                            is_unknown: hasUnk,
                            is_no_gps: hasNoGps,
                            is_phantom: hasPhantom,
                            missing_names: missingNames,
                            from_name: nodeChain[lastIdx].name,
                            to_name: nodeChain[i].name
                        });
                    }
                    lastIdx = i;
                }
            }
            return segs;
        }

        window.togglePhantomNode = function(hopIdx, targetNodeId, targetAlias) {
            if (!window._activeVisualisedMeta || !window._activeVisualisedMeta.repeaters) return;
            var rep = window._activeVisualisedMeta.repeaters[hopIdx];
            if (!rep) return;
            window._isSwitchingHop = true;
            window.clearCandidatePreview();
            var willBePhantom = !rep.is_phantom;
            rep.is_phantom = willBePhantom;
            if (willBePhantom) {
                rep.orig_lat = (rep.lat != null) ? rep.lat : rep.orig_lat;
                rep.orig_lon = (rep.lon != null) ? rep.lon : rep.orig_lon;
                rep.lat = null;
                rep.lon = null;
            } else {
                rep.lat = (rep.orig_lat != null) ? rep.orig_lat : rep.lat;
                rep.lon = (rep.orig_lon != null) ? rep.orig_lon : rep.lon;
            }

            if (rep.candidates) {
                for (var c = 0; c < rep.candidates.length; c++) {
                    if (rep.candidates[c].node_id === targetNodeId || rep.candidates[c].alias === targetAlias) {
                        rep.candidates[c].is_phantom = willBePhantom;
                    }
                }
            }

            if (pyBridge && pyBridge.on_phantom_node_toggled) {
                pyBridge.on_phantom_node_toggled(targetNodeId, targetAlias || rep.name || '', willBePhantom);
            }

            var newSegments = rebuildSegmentsFromMeta(window._activeVisualisedMeta);
            drawVisualisedMessagePath(newSegments, window._activeVisualisedMeta, true);
            setTimeout(function() { window._isSwitchingHop = false; }, 350);
        };

        window.switchHopCandidate = function(hopIdx, targetNodeId) {
            if (!window._activeVisualisedMeta || !window._activeVisualisedMeta.repeaters) return;
            var rep = window._activeVisualisedMeta.repeaters[hopIdx];
            if (!rep || !rep.candidates) return;
            window._isSwitchingHop = true;
            window.clearCandidatePreview();
            for (var i = 0; i < rep.candidates.length; i++) {
                var cand = rep.candidates[i];
                if (cand.node_id === targetNodeId) {
                    rep.name = cand.alias;
                    rep.alias = cand.alias;
                    rep.node_id = cand.node_id;
                    rep.lat = cand.lat;
                    rep.lon = cand.lon;
                    rep.snr = cand.snr;
                    rep.is_known = (cand.lat != null && cand.lon != null);
                    for (var j = 0; j < rep.candidates.length; j++) {
                        var isMatch = (rep.candidates[j].node_id === targetNodeId);
                        rep.candidates[j].is_selected = isMatch;
                        if (isMatch) {
                            rep.candidates[j].is_saved_preference = true;
                        }
                    }
                    break;
                }
            }

            // Persist choice to Python backend via WebChannel for all future routing requests
            if (pyBridge && pyBridge.on_hop_candidate_selected) {
                var msgId = (window._activeVisualisedMeta && window._activeVisualisedMeta.msg_id) || '';
                var hopPrefix = rep.hash || '';
                pyBridge.on_hop_candidate_selected(hopPrefix, targetNodeId, msgId);
            }

            var newSegments = rebuildSegmentsFromMeta(window._activeVisualisedMeta);
            drawVisualisedMessagePath(newSegments, window._activeVisualisedMeta, true);
            setTimeout(function() { window._isSwitchingHop = false; }, 350);
        };

        function drawVisualisedMessagePath(segmentsOrCoords, meta, isSwitching) {
            if (!isSwitching) {
                clearVisualisedPath();
            } else if (visualisedPathLayer) {
                try { map.removeLayer(visualisedPathLayer); } catch(e) {}
                if (visualisedHighlightMarkers && visualisedHighlightMarkers.length > 0) {
                    for (var i = 0; i < visualisedHighlightMarkers.length; i++) {
                        try { map.removeLayer(visualisedHighlightMarkers[i]); } catch(e) {}
                    }
                    visualisedHighlightMarkers = [];
                }
            }
            window.clearCandidatePreview();
            if (!segmentsOrCoords || segmentsOrCoords.length === 0) return;

            window._visualisedPathActive = true;
            window._activeVisualisedMeta = meta;

            visualisedPathLayer = L.layerGroup().addTo(map);
            var pathColor = (meta && meta.color) || mapColors.visualisedPath || '#FF00FF';
            var headingColor = (meta && meta.heading_color) || mapColors.visualisedHeading || '#FF00FF';

            // Standardize segments array
            var segments = [];
            if (segmentsOrCoords[0] && segmentsOrCoords[0].coords) {
                segments = segmentsOrCoords;
            } else if (Array.isArray(segmentsOrCoords[0]) && typeof segmentsOrCoords[0][0] === 'number') {
                for (var si = 0; si < segmentsOrCoords.length - 1; si++) {
                    segments.push({
                        coords: [segmentsOrCoords[si], segmentsOrCoords[si + 1]],
                        color: pathColor,
                        is_unknown: false
                    });
                }
            } else {
                segments = segmentsOrCoords;
            }
            window._activeVisualisedSegments = segments;
            if (window._is3DActive && typeof syncVisualisedPathTo3D === 'function') {
                syncVisualisedPathTo3D(segments, meta);
            }

            // Draw each transmission line segment (Magenta for known hops, Red for paths through unknown repeaters)
            for (var s = 0; s < segments.length; s++) {
                var seg = segments[s];
                if (!seg.coords || seg.coords.length < 2) continue;
                var phantomCol = (meta && meta.phantom_color) || (mapColors && mapColors.phantomPath) || '#FFFF00';
                var unknownCol = (meta && meta.unknown_color) || (mapColors && mapColors.unknownPath) || '#EF4444';
                var noGpsCol = (meta && meta.no_gps_color) || (mapColors && mapColors.noGpsPath) || '#000000';
                var segCol = seg.color || (seg.is_phantom ? phantomCol : (seg.is_no_gps ? noGpsCol : (seg.is_unknown ? unknownCol : pathColor)));

                var isPhantom = !!seg.is_phantom;
                var isNoGps = !isPhantom && !!seg.is_no_gps;
                var isUnk = !isPhantom && !isNoGps && !!seg.is_unknown;
                var isAmbiguous = !isPhantom && !isNoGps && !isUnk && !!seg.is_ambiguous;
                var isInferred = isUnk || isNoGps || isPhantom || !!seg.is_inferred;

                var segDash = '8, 12';
                if (isInferred) {
                    segDash = '2, 6'; // CoreScope fine-dotted for inferred unmapped / dropped steps
                } else if (isAmbiguous) {
                    segDash = '6, 6'; // CoreScope dashed for ambiguous hash collision hops
                } else {
                    segDash = '8, 12'; // Confirmed solid / flowing RF hop
                }

                // Outer subtle glow / contrast halo (white backing for black line on dark map, matching color otherwise)
                var isBlack = (segCol && (segCol.toLowerCase() === '#000000' || segCol.toLowerCase() === '#000' || segCol.toLowerCase() === 'black'));
                var glowCol = isBlack ? '#FFFFFF' : (isAmbiguous ? '#FACC15' : segCol);
                var glowOpacity = isPhantom ? 0.35 : (isNoGps ? 0.35 : (isUnk ? 0.35 : (isAmbiguous ? 0.40 : 0.25)));
                var glowWidth = isBlack ? 5.5 : (isAmbiguous ? 8 : 7);

                var glowPoly = L.polyline(seg.coords, {
                    renderer: visualisedSvgRenderer,
                    color: glowCol,
                    weight: glowWidth,
                    opacity: glowOpacity,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(visualisedPathLayer);

                // Dotted animated line flowing in packet transmission direction (period = 20px)
                var poly = L.polyline(seg.coords, {
                    renderer: visualisedSvgRenderer,
                    color: segCol,
                    weight: 3.5,
                    dashArray: segDash,
                    className: 'animated-path-flow',
                    opacity: 0.95,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(visualisedPathLayer);

                // Broad transparent hit-area (28px wide corridor) for effortless hovering anywhere near the line
                var hitPoly = L.polyline(seg.coords, {
                    renderer: visualisedSvgRenderer,
                    weight: 28,
                    opacity: 0.0001,
                    color: '#000000',
                    className: 'path-hit-corridor',
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: true
                }).addTo(visualisedPathLayer);

                var tipText = '';
                if (isPhantom) {
                    var repNameInfo = (seg.missing_names && seg.missing_names.length > 0) ? (' (' + seg.missing_names.join(', ') + ')') : '';
                    tipText = '👻 Path connects through phantom node' + repNameInfo + ': ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
                } else if (isNoGps) {
                    var repNameInfo = (seg.missing_names && seg.missing_names.length > 0) ? (' (' + seg.missing_names.join(', ') + ')') : '';
                    tipText = '⚠️ Inferred Trajectory: intermediate repeater has unknown location' + repNameInfo + ': ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
                } else if (isUnk) {
                    tipText = '⚠️ Inferred Trajectory: connects through unknown repeater (' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node') + ')';
                } else if (isAmbiguous) {
                    var candCount = (seg.candidates_count && seg.candidates_count > 1) ? (' (' + seg.candidates_count + ' candidates)') : '';
                    tipText = '⚠️ Ambiguous Hop (Hash Collision' + candCount + '): ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node') + ' [Heuristic inference]';
                } else {
                    tipText = '✓ Verified RF Hop: ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
                }

                hitPoly.bindTooltip(tipText, { sticky: true, className: 'node-tooltip' });

                (function(gPoly, noGps, unk, phant) {
                    hitPoly.on('mouseover', function() {
                        gPoly.setStyle({ opacity: noGps ? 0.8 : 0.75, weight: noGps ? 8 : 10 });
                    });
                    hitPoly.on('mouseout', function() {
                        gPoly.setStyle({ opacity: noGps ? 0.35 : ((unk || phant) ? 0.35 : 0.25), weight: noGps ? 5.5 : 7 });
                    });
                })(glowPoly, isNoGps, isUnk, isPhantom);
            }

            // Build repeater list HTML
            var repeaters = (meta && meta.repeaters) || [];
            var repListHtml = '';
            if (repeaters.length === 0) {
                repListHtml = '<div style="font-size: 11px; color: #9CA3AF; font-style: italic; margin-top: 4px;">Direct transmission (0 repeaters heard)</div>';
            } else {
                for (var r = 0; r < repeaters.length; r++) {
                    var rep = repeaters[r];
                    var isKnown = rep.is_known;
                    var isPhantom = !!rep.is_phantom;
                    var repName = isKnown ? (rep.name || rep.alias) : (rep.name || '?? Unknown');
                    var icon = isPhantom ? '👻' : (isKnown ? '📡' : '❓');
                    var textColor = isPhantom ? '#FACC15' : (isKnown ? '#34D399' : '#F87171');
                    var phantomBadge = isPhantom ? ' <span style="color: #FACC15; font-size: 8.5px; font-weight: bold; background: rgba(234, 179, 8, 0.2); border: 1px solid #CA8A04; border-radius: 3px; padding: 1px 4px; margin-left: 4px;">[Phantom]</span>' : '';
                    var snrStr = (rep.snr != null && rep.snr !== '') ? ' <span style="color: #9CA3AF; font-size: 10px;">(' + (rep.snr > 0 ? '+' : '') + Number(rep.snr).toFixed(1) + ' dB)</span>' : '';

                    var repHoverAttr = (!isPhantom && rep.lat != null && rep.lon != null)
                        ? (' onmouseenter="window.previewHopCandidate(' + rep.lat + ', ' + rep.lon + ', \\'' + (repName || '').replace(/'/g, "\\\\'") + '\\')" onmouseleave="window.clearCandidatePreview()"')
                        : '';
                    var repClass = 'hop-item-row' + ((!isPhantom && rep.lat != null && rep.lon != null) ? ' has-gps' : '');

                    repListHtml += '<div class="' + repClass + '" ' + repHoverAttr + ' style="font-size: 11px; margin: 3px 0 2px 0; padding: 2px 6px; border-radius: 4px; display: flex; align-items: center; justify-content: space-between;">' +
                        '<div><b style="color: ' + headingColor + ';">' + (r + 1) + '.</b> ' + icon + ' <b style="color: ' + textColor + ';">' + repName + '</b>' + phantomBadge + snrStr + '</div>' +
                        ((isKnown && !isPhantom) ? '<span style="color: #60A5FA; font-size: 9px; opacity: 0.7;">📍</span>' : (isPhantom ? '<span style="color: #FACC15; font-size: 9px;">👻</span>' : '')) +
                        '</div>';

                    var hasCandidates = (rep.candidates && rep.candidates.length > 1);
                    var summaryTitle = hasCandidates
                        ? ('<span style="color: ' + headingColor + '; font-size: 9px;">▾</span> Alternate repeaters (' + (rep.candidates.length - 1) + ')')
                        : ('<span style="color: ' + headingColor + '; font-size: 9px;">▾</span> Node options');

                    var phantomBtnLabel = isPhantom ? '👻 Unmark phantom node' : '👻 Mark as phantom node';
                    var phantomBtnClass = 'phantom-node-btn' + (isPhantom ? ' is-phantom' : '');
                    var phantomNodeId = rep.node_id || ('!' + (rep.hash || ''));
                    var phantomAlias = (rep.name || rep.alias || '').replace(/'/g, "\\\\'");

                    repListHtml += '<details class="hop-candidates-dropdown">' +
                        '<summary class="hop-candidates-summary">' + summaryTitle + '</summary>' +
                        '<div class="hop-candidates-box">';

                    if (hasCandidates) {
                        var refNote = rep.ref_name ? (' (relative to ' + rep.ref_name + ')') : '';
                        repListHtml += '<div style="color: #9CA3AF; font-size: 9px; margin-bottom: 3px;">Prefix <code>' + rep.hash + '</code> candidates' + refNote + ' (click to switch route):</div>';

                        for (var c = 0; c < rep.candidates.length; c++) {
                            var cand = rep.candidates[c];
                            var isSel = (cand.node_id === rep.node_id || cand.alias === rep.name || cand.is_selected);
                            var distText = cand.dist_km != null ? (cand.dist_km + ' km') : 'no GPS';
                            var candPhant = !!cand.is_phantom;
                            if (isSel) {
                                var activeHoverAttr = (!candPhant && cand.lat != null && cand.lon != null)
                                    ? (' onmouseenter="window.previewHopCandidate(' + cand.lat + ', ' + cand.lon + ', \\'' + (cand.alias || '').replace(/'/g, "\\\\'") + '\\')" onmouseleave="window.clearCandidatePreview()"')
                                    : '';
                                var savedTag = cand.is_saved_preference ? ' <span style="color: #60A5FA; font-size: 8.5px; font-weight: normal;">• Saved 💾</span>' : '';
                                var pTag = isPhantom ? ' <span style="color: #FACC15; font-size: 8.5px; font-weight: bold;">[Phantom]</span>' : '';
                                repListHtml += '<div class="hop-candidate-row active-candidate-row" ' + activeHoverAttr + ' style="color: ' + (isPhantom ? '#FACC15' : '#34D399') + '; font-weight: 600; font-size: 9.5px; margin: 2px 0; padding: 2px 6px; border-radius: 4px;">' +
                                    '✓ ' + cand.alias + ' (' + distText + ') [Active' + savedTag + ']' + pTag +
                                    '</div>';
                            } else {
                                var hoverAttr = (!candPhant && cand.lat != null && cand.lon != null)
                                    ? (' onmouseenter="window.previewHopCandidate(' + cand.lat + ', ' + cand.lon + ', \\'' + (cand.alias || '').replace(/'/g, "\\\\'") + '\\')" onmouseleave="window.clearCandidatePreview()"')
                                    : '';
                                var candTag = candPhant ? ' <span style="color: #FACC15; font-size: 8.5px;">[Phantom]</span>' : '';
                                repListHtml += '<div class="hop-candidate-row" ' + hoverAttr + '>' +
                                    '<span>• ' + cand.alias + ' (' + distText + ')' + candTag + '</span>' +
                                    '<button class="switch-hop-btn" onclick="window.switchHopCandidate(' + r + ', \\'' + cand.node_id + '\\')">Use this</button>' +
                                    '</div>';
                            }
                        }
                    } else {
                        repListHtml += '<div style="color: #9CA3AF; font-size: 9px; margin-bottom: 3px;">Node: <b>' + repName + '</b>' + (rep.hash ? ' (<code>' + rep.hash + '</code>)' : '') + '</div>';
                    }

                    // Always provide the "Mark as phantom node" button in the dropdown
                    repListHtml += '<div style="margin-top: 4px; padding-top: 4px; border-top: 1px solid #414143; display: flex; align-items: center; justify-content: space-between;">' +
                        '<span style="font-size: 9px; color: #9CA3AF;">Collision / Faraway:</span>' +
                        '<button class="' + phantomBtnClass + '" onclick="window.togglePhantomNode(' + r + ', \\'' + phantomNodeId + '\\', \\'' + phantomAlias + '\\')">' + phantomBtnLabel + '</button>' +
                        '</div>';

                    repListHtml += '</div></details>';
                }
            }

            var senderText = (meta && meta.sender_name) ? meta.sender_name : 'Unknown';
            var chanText = (meta && meta.channel) ? ' on #' + meta.channel.replace(/^#/, '') : '';
            var routeText = (meta && meta.route_type) ? ' • ' + meta.route_type : '';
            var timeText = (meta && meta.time) ? ' • ' + meta.time : '';
            var homeNodeName = (meta && meta.home_name) ? meta.home_name : 'M7NCY';

            // Place Origin (Start Point) marker where the message originated
            var startPos = (meta && meta.sender_coord) ? meta.sender_coord : null;
            if (!startPos && segments.length > 0) {
                var firstSeg = segments[0];
                if (firstSeg.coords && firstSeg.coords.length > 0) {
                    startPos = firstSeg.coords[0];
                }
            }
            if (startPos) {
                var startHtml = '<div class="origin-path-highlight" style="' +
                    'display: flex; flex-direction: column; align-items: center; cursor: pointer; pointer-events: auto;">' +
                    '<div style="background: rgba(14, 165, 233, 0.95); border: 1.5px solid #38BDF8; color: #FFFFFF; font-size: 9px; font-weight: 800; padding: 1px 6px; border-radius: 8px; box-shadow: 0 0 10px rgba(56, 189, 248, 0.7); white-space: nowrap; margin-bottom: 2px; display: flex; align-items: center; gap: 3px; letter-spacing: 0.5px;">' +
                    '<span>📍</span><span>ORIGIN</span>' +
                    '</div>' +
                    '<div style="width: 22px; height: 22px; border-radius: 50%; background: rgba(56, 189, 248, 0.25); border: 2px solid #38BDF8; box-shadow: 0 0 14px #38BDF8; display: flex; align-items: center; justify-content: center;">' +
                    '<div style="width: 8px; height: 8px; border-radius: 50%; background: #38BDF8;"></div>' +
                    '</div>' +
                    '</div>';

                var startIcon = L.divIcon({
                    className: 'start-highlight-icon',
                    html: startHtml,
                    iconSize: [60, 44],
                    iconAnchor: [30, 36]
                });

                var startMarker = L.marker(startPos, { icon: startIcon, zIndexOffset: 1400 }).addTo(visualisedPathLayer);
                visualisedHighlightMarkers.push(startMarker);
                startMarker.bindTooltip(escapeHtml(senderText || 'Origin') + ' (Message Origin / Start Point)', { direction: 'top', className: 'node-tooltip' });
            }

            // Markers on map for repeaters (skip phantom nodes)
            for (var k = 0; k < repeaters.length; k++) {
                var rp = repeaters[k];
                if (rp.is_phantom) continue;
                if (rp.lat != null && rp.lon != null) {
                    var repPos = [rp.lat, rp.lon];
                    var markerColor = pathColor;

                    var highlightHtml = '<div class="repeater-path-highlight" style="' +
                        'width: 26px; height: 26px; border-radius: 50%; ' +
                        'background: rgba(255, 0, 255, 0.2); ' +
                        'border: 2px solid ' + markerColor + '; ' +
                        'box-shadow: 0 0 12px ' + markerColor + '; ' +
                        'display: flex; align-items: center; justify-content: center;">' +
                        '<div style="width: 8px; height: 8px; border-radius: 50%; background: ' + markerColor + ';"></div>' +
                        '</div>';

                    var repIcon = L.divIcon({
                        className: 'repeater-highlight-icon',
                        html: highlightHtml,
                        iconSize: [26, 26],
                        iconAnchor: [13, 13]
                    });

                    var hMarker = L.marker(repPos, { icon: repIcon, zIndexOffset: 1000 }).addTo(visualisedPathLayer);
                    visualisedHighlightMarkers.push(hMarker);
                    hMarker.bindTooltip(escapeHtml(rp.name || rp.alias) + ' (Hop #' + (k + 1) + ')', { direction: 'top', className: 'node-tooltip' });
                }
            }

            // Place Home Station marker at final destination
            var homePos = (meta && meta.home_coord) ? meta.home_coord : null;
            if (!homePos && segments.length > 0) {
                var lastSeg = segments[segments.length - 1];
                if (lastSeg.coords && lastSeg.coords.length > 1) {
                    homePos = lastSeg.coords[1];
                }
            }
            if (homePos) {
                var homeHtml = '<div style="width: 28px; height: 28px; border-radius: 50%; ' +
                    'background: rgba(52, 211, 153, 0.25); border: 2px solid #34D399; ' +
                    'box-shadow: 0 0 14px #34D399; display: flex; align-items: center; justify-content: center; ' +
                    'font-size: 15px; cursor: pointer;">🏠</div>';

                var homeIcon = L.divIcon({
                    className: 'home-highlight-icon',
                    html: homeHtml,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14]
                });

                var homeMarker = L.marker(homePos, { icon: homeIcon, zIndexOffset: 1200 }).addTo(visualisedPathLayer);
                visualisedHighlightMarkers.push(homeMarker);
                homeMarker.bindTooltip(escapeHtml(homeNodeName) + ' (Home Station)', { direction: 'top', className: 'node-tooltip' });
            }

            // Populate Floating Draggable Panel
            var senderHoverAttr = (startPos)
                ? (' onmouseenter="window.previewHopCandidate(' + startPos[0] + ', ' + startPos[1] + ', \\'' + (senderText || '').replace(/'/g, "\\\\'") + '\\', \\'📍\\')" onmouseleave="window.clearCandidatePreview()"')
                : '';
            var homeHoverAttr = (homePos)
                ? (' onmouseenter="window.previewHopCandidate(' + homePos[0] + ', ' + homePos[1] + ', \\'' + (homeNodeName || '').replace(/'/g, "\\\\'") + '\\', \\'🏠\\')" onmouseleave="window.clearCandidatePreview()"')
                : '';

            var bodyHtml = '<div style="margin-bottom: 8px;">' +
                '<div class="hop-item-row has-gps" ' + homeHoverAttr + ' style="font-weight: 700; color: #34D399; font-size: 13px; margin-bottom: 2px; padding: 2px 4px; border-radius: 4px;">🏠 ' + homeNodeName + ' (Home Station)</div>' +
                '<div class="hop-item-row' + (startPos ? ' has-gps' : '') + '" ' + senderHoverAttr + ' style="font-size: 10px; color: #9CA3AF; padding: 2px 4px; border-radius: 4px;"><b>From:</b> ' + (startPos ? '📍 ' : '') + senderText + chanText + ' • ' + Math.max(1, (meta && meta.repeaters ? meta.repeaters.length : 1)) + ' hop(s)' + routeText + timeText + '</div>' +
                '</div>' +
                '<div style="background: #1C1C1C; border: 1px solid #414143; border-radius: 6px; padding: 6px 8px;">' +
                '<div style="font-size: 11px; font-weight: 600; color: ' + headingColor + '; margin-bottom: 5px;">Repeaters in Path (' + repeaters.length + '):</div>' +
                repListHtml +
                '</div>';

            var panel = document.getElementById('visualised-floating-panel');
            var bodyEl = document.getElementById('floating-route-body-content');
            var titleEl = document.getElementById('floating-route-title-text');
            if (panel && bodyEl) {
                if (titleEl) titleEl.style.color = headingColor;
                bodyEl.innerHTML = bodyHtml;
                panel.style.display = 'flex';
            }
        }

        function centerOnAll() {
            var all = [];
            for (var id in markers) {
                var pos = markers[id].getLatLng();
                if (pos && typeof pos.lat === 'number' && typeof pos.lng === 'number' && !isNaN(pos.lat) && !isNaN(pos.lng) && pos.lat >= -85.0 && pos.lat <= 85.0 && pos.lng >= -180.0 && pos.lng <= 180.0) {
                    all.push(pos);
                }
            }
            if (all.length > 0) {
                map.fitBounds(all, { padding: [25, 25], maxZoom: 12 });
            }
        }

        function setAdsbVisible(visible) {
            adsbActive = !!visible;
            var panel = document.getElementById('adsb-panel');
            if (panel) {
                panel.style.display = adsbActive ? 'flex' : 'none';
                if (adsbActive) renderAdsbLegend();
            }
            if (!adsbActive) {
                if (adsbLayerGroup) adsbLayerGroup.clearLayers();
                if (adsbTrailsGroup) adsbTrailsGroup.clearLayers();
                if (adsbRadarRingsGroup) adsbRadarRingsGroup.clearLayers();
                if (adsbCenterMarker) {
                    try {
                        if (adsbRadarRingsGroup) adsbRadarRingsGroup.removeLayer(adsbCenterMarker);
                        map.removeLayer(adsbCenterMarker);
                    } catch (e) {}
                    adsbCenterMarker = null;
                }
                aircraftMarkers = {};
                pinnedTooltipHex = null;
                _activeHoverHex = null;
                aircraftTrails = {};
                aircraftHistory = {};
                currentAircraftData = {};
                _aircraftContainerPoints = {};
                adsbLastTargetCoord = null;
                if (adsbRadarOverlay) {
                    try { map.removeLayer(adsbRadarOverlay); } catch (e) {}
                    adsbRadarOverlay = null;
                    adsbRadarRadius = null;
                }
                if (adsbRangeCircle) {
                    try { map.removeLayer(adsbRangeCircle); } catch (e) {}
                    adsbRangeCircle = null;
                }
            }
            if (window._is3DActive && typeof syncAdsbTo3D === 'function') {
                syncAdsbTo3D();
            }
        }
        window.setAdsbVisible = setAdsbVisible;

        window.clearAdsbForRetarget = function() {
            if (_adsbHoverCloseTimer) {
                clearTimeout(_adsbHoverCloseTimer);
                _adsbHoverCloseTimer = null;
            }
            if (map && map.closeTooltip) {
                try { map.closeTooltip(); } catch (e) {}
            }
            if (adsbLayerGroup) adsbLayerGroup.clearLayers();
            if (adsbTrailsGroup) adsbTrailsGroup.clearLayers();
            if (adsbRadarRingsGroup) adsbRadarRingsGroup.clearLayers();
            if (adsbCenterMarker) {
                try {
                    if (adsbRadarRingsGroup) adsbRadarRingsGroup.removeLayer(adsbCenterMarker);
                    map.removeLayer(adsbCenterMarker);
                } catch (e) {}
                adsbCenterMarker = null;
            }
            aircraftMarkers = {};
            pinnedTooltipHex = null;
            _activeHoverHex = null;
            aircraftTrails = {};
            aircraftHistory = {};
            currentAircraftData = {};
            _aircraftContainerPoints = {};
            adsbLastTargetCoord = null;
            if (window._is3DActive && typeof syncAdsbTo3D === 'function') {
                syncAdsbTo3D();
            }
        };

        function updateActivityStats() {
            var maxT = 0;
            var peakN = '--';
            var actMap = activityHeatmapData || {};
            for (var k in actMap) {
                var v = actMap[k];
                if (typeof v === 'number' && v > maxT) {
                    maxT = v;
                    peakN = k;
                }
            }
            window._actMaxTraffic = maxT;
            window._actPeakNode = peakN;

            var peakNodeEl = document.getElementById('act-peak-node-val');
            var peakPacketsEl = document.getElementById('act-peak-packets-val');
            if (peakNodeEl) peakNodeEl.innerText = peakN;
            if (peakPacketsEl) peakPacketsEl.innerText = maxT + ' pkts';

            var cVHigh = 0, cHigh = 0, cMed = 0, cLow = 0, cMin = 0;
            for (var k in actMap) {
                var cnt = actMap[k];
                if (typeof cnt !== 'number' || cnt <= 0) continue;
                var ratio = maxT > 0 ? (cnt / maxT) : 0;
                if (window._actRelative) {
                    if (ratio >= 0.75) cVHigh++;
                    else if (ratio >= 0.50) cHigh++;
                    else if (ratio >= 0.25) cMed++;
                    else if (ratio >= 0.10) cLow++;
                    else cMin++;
                } else {
                    if (cnt > 10) cVHigh++;
                    else if (cnt > 5) cHigh++;
                    else if (cnt > 2) cMed++;
                    else if (cnt > 1) cLow++;
                    else cMin++;
                }
            }
            var elVHigh = document.getElementById('act-count-vhigh');
            var elHigh = document.getElementById('act-count-high');
            var elMed = document.getElementById('act-count-med');
            var elLow = document.getElementById('act-count-low');
            var elMin = document.getElementById('act-count-min');
            if (elVHigh) elVHigh.innerText = cVHigh;
            if (elHigh) elHigh.innerText = cHigh;
            if (elMed) elMed.innerText = cMed;
            if (elLow) elLow.innerText = cLow;
            if (elMin) elMin.innerText = cMin;

            var lblVHigh = document.getElementById('act-lbl-vhigh');
            var lblHigh = document.getElementById('act-lbl-high');
            var lblMed = document.getElementById('act-lbl-med');
            var lblLow = document.getElementById('act-lbl-low');
            var lblMin = document.getElementById('act-lbl-min');
            if (window._actRelative) {
                if (lblVHigh) lblVHigh.innerText = '> 75% (Peak)';
                if (lblHigh) lblHigh.innerText = '50 - 75% (High)';
                if (lblMed) lblMed.innerText = '25 - 50% (Med)';
                if (lblLow) lblLow.innerText = '10 - 25% (Low)';
                if (lblMin) lblMin.innerText = '< 10% (Base)';
            } else {
                if (lblVHigh) lblVHigh.innerText = '> 10 pkts (Peak)';
                if (lblHigh) lblHigh.innerText = '6 - 10 pkts (High)';
                if (lblMed) lblMed.innerText = '3 - 5 pkts (Med)';
                if (lblLow) lblLow.innerText = '2 pkts (Low)';
                if (lblMin) lblMin.innerText = '1 pkt (Base)';
            }
        }

        function render2DActivityHeatmap() {
            var oldCanvas = document.getElementById('activityHeatmapCanvas');
            if (oldCanvas) oldCanvas.remove();
            if (activityHeatmapLayerGroup) {
                activityHeatmapLayerGroup.clearLayers();
            }
        }

        function toggleActivityRelative(checked) {
            window._actRelative = !!checked;
            updateActivityStats();
            render2DActivityHeatmap();
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) applyNodeMarkerStyling(m, m._nodeData);
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }
        window.toggleActivityRelative = toggleActivityRelative;

        function toggleActivityScaling(checked) {
            window._actScaling = !!checked;
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) applyNodeMarkerStyling(m, m._nodeData);
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }
        window.toggleActivityScaling = toggleActivityScaling;

        function setActivityHeatmap(enabled, timeframeHours, data) {
            activityHeatmapActive = !!enabled;
            if (timeframeHours) activityTimeframeHours = Number(timeframeHours);
            if (data) activityHeatmapData = data;

            var bar = document.getElementById('activity-heatmap-bar');
            if (bar) {
                bar.style.display = activityHeatmapActive ? 'flex' : 'none';
            }

            var btns = ['act-btn-1h', 'act-btn-6h', 'act-btn-24h'];
            for (var b = 0; b < btns.length; b++) {
                var el = document.getElementById(btns[b]);
                if (el) el.classList.remove('active');
            }
            var targetBtn = document.getElementById('act-btn-' + activityTimeframeHours + 'h');
            if (targetBtn) targetBtn.classList.add('active');

            updateActivityStats();
            render2DActivityHeatmap();

            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncHeatmapTo3D === 'function') {
                syncHeatmapTo3D();
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }
        window.setActivityHeatmap = setActivityHeatmap;

        function setActivityTimeframe(hours) {
            activityTimeframeHours = hours;
            var btns = ['act-btn-1h', 'act-btn-6h', 'act-btn-24h'];
            for (var b = 0; b < btns.length; b++) {
                var el = document.getElementById(btns[b]);
                if (el) el.classList.remove('active');
            }
            var targetBtn = document.getElementById('act-btn-' + hours + 'h');
            if (targetBtn) targetBtn.classList.add('active');

            if (pyBridge && pyBridge.on_activity_timeframe_changed) {
                pyBridge.on_activity_timeframe_changed(hours);
            }
        }
        window.setActivityTimeframe = setActivityTimeframe;

        function closeActivityHeatmap() {
            setActivityHeatmap(false);
            if (pyBridge && pyBridge.on_activity_heatmap_toggled) {
                pyBridge.on_activity_heatmap_toggled(false);
            }
        }
        window.closeActivityHeatmap = closeActivityHeatmap;

        window._newNodesActive = false;
        window._newNodesTimeframeHours = 72; // default 3 days

        function updateNewNodesStats() {
            var tfHours = window._newNodesTimeframeHours || 72;
            var newCount = 0;
            var totalCount = 0;
            var now = Date.now();
            var maxAgeMs = tfHours * 3600 * 1000;
            var baselineCutoff = 1704153600000; // 2024-01-02T00:00:00Z

            for (var id in markers) {
                var m = markers[id];
                if (!m || !m._nodeData) continue;
                totalCount++;
                var fs = m._nodeData.first_seen || '';
                if (fs) {
                    try {
                        var parsed = Date.parse(fs);
                        if (!isNaN(parsed) && parsed > baselineCutoff) {
                            var ageMs = now - parsed;
                            if (ageMs >= 0 && ageMs <= maxAgeMs) {
                                newCount++;
                            }
                        }
                    } catch(e) {}
                }
            }

            var discEl = document.getElementById('nn-discovered-count');
            if (discEl) discEl.textContent = newCount + ' nodes';
            var totEl = document.getElementById('nn-total-count');
            if (totEl) totEl.textContent = totalCount;
        }

        function setNewNodes(enabled, timeframeHours) {
            window._newNodesActive = !!enabled;
            if (timeframeHours) window._newNodesTimeframeHours = Number(timeframeHours);

            var panel = document.getElementById('new-nodes-panel');
            if (panel) {
                panel.style.display = window._newNodesActive ? 'flex' : 'none';
                if (window._newNodesActive) {
                    bringOverlayToFront(panel);
                }
            }

            var btns = ['nn-btn-24h', 'nn-btn-72h', 'nn-btn-168h', 'nn-btn-336h'];
            for (var b = 0; b < btns.length; b++) {
                var el = document.getElementById(btns[b]);
                if (el) el.classList.remove('active');
            }
            var targetBtn = document.getElementById('nn-btn-' + window._newNodesTimeframeHours + 'h');
            if (targetBtn) targetBtn.classList.add('active');

            updateNewNodesStats();

            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }
        }
        window.setNewNodes = setNewNodes;

        function setNewNodesTimeframe(hours) {
            window._newNodesTimeframeHours = Number(hours);
            var btns = ['nn-btn-24h', 'nn-btn-72h', 'nn-btn-168h', 'nn-btn-336h'];
            for (var b = 0; b < btns.length; b++) {
                var el = document.getElementById(btns[b]);
                if (el) el.classList.remove('active');
            }
            var targetBtn = document.getElementById('nn-btn-' + hours + 'h');
            if (targetBtn) targetBtn.classList.add('active');

            updateNewNodesStats();

            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
            }
            if (window._is3DActive && typeof syncAllNodesTo3D === 'function') {
                syncAllNodesTo3D();
            }

            if (pyBridge && pyBridge.on_new_nodes_timeframe_changed) {
                pyBridge.on_new_nodes_timeframe_changed(Number(hours));
            }
        }
        window.setNewNodesTimeframe = setNewNodesTimeframe;

        function markAllNodesKnown() {
            if (window.pyBridge && window.pyBridge.mark_all_nodes_known) {
                window.pyBridge.mark_all_nodes_known();
            }
        }
        window.markAllNodesKnown = markAllNodesKnown;

        function closeNewNodes() {
            setNewNodes(false);
            if (pyBridge && pyBridge.on_new_nodes_toggled) {
                pyBridge.on_new_nodes_toggled(false);
            }
        }
        window.closeNewNodes = closeNewNodes;

        window._mqttNodesActive = false;

        function setMqttNodes(enabled) {
            window._mqttNodesActive = !!enabled;
            var panel = document.getElementById('mqtt-nodes-panel');
            if (panel) {
                panel.style.display = window._mqttNodesActive ? 'flex' : 'none';
                if (window._mqttNodesActive) {
                    bringOverlayToFront(panel);
                }
            }
            updateMqttNodesStats();
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    try {
                        applyNodeMarkerStyling(m, m._nodeData);
                    } catch(e) {
                        console.error("Error styling marker " + id + " for MQTT mode:", e);
                    }
                }
            }
            if (window.syncAllNodesTo3D && map3d) {
                window.syncAllNodesTo3D();
            }
        }
        window.setMqttNodes = setMqttNodes;

        function closeMqttNodes() {
            setMqttNodes(false);
            if (window.pyBridge && window.pyBridge.on_mqtt_nodes_toggled) {
                window.pyBridge.on_mqtt_nodes_toggled(false);
            }
        }
        window.closeMqttNodes = closeMqttNodes;

        function updateMqttNodesStats() {
            var mqttCount = 0;
            var totalCount = 0;
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    totalCount++;
                    var nd = m._nodeData;
                    var src = (nd.source || '').toLowerCase().trim();
                    if ((src === 'mqtt' || !!nd.is_mqtt) && !nd.is_local && !nd.is_phantom) {
                        mqttCount++;
                    }
                }
            }
            var elCount = document.getElementById('mqtt-discovered-count');
            var elTotal = document.getElementById('mqtt-total-count');
            if (elCount) elCount.innerText = mqttCount + (mqttCount === 1 ? ' node' : ' nodes');
            if (elTotal) elTotal.innerText = totalCount;
        }
        window.updateMqttNodesStats = updateMqttNodesStats;

        window._is3DActive = false;
        var map3d = null;
        var map3dInitialized = false;
        var map3dMarkers = {};
        var map3dArcsCount = 0;

        function set3DMode(enabled) {
            window._is3DActive = !!enabled;
            var el3D = document.getElementById('map-3d');
            var el2D = document.getElementById('map');
            var panel = document.getElementById('map3dControls');

            if (window._is3DActive) {
                if (el2D) el2D.style.display = 'none';
                if (el3D) el3D.style.display = 'block';
                if (panel) {
                    panel.style.display = 'flex';
                    try {
                        var savedPos = sessionStorage.getItem('overlay_pos_map_3d_controls');
                        if (savedPos) {
                            var p = JSON.parse(savedPos);
                            if (p && typeof p.left === 'number' && typeof p.top === 'number') {
                                panel.style.left = p.left + 'px';
                                panel.style.top = p.top + 'px';
                                panel.style.right = 'auto';
                                panel.style.bottom = 'auto';
                            }
                        }
                    } catch(e) {}
                }

                if (!map3dInitialized) {
                    initMap3D();
                } else if (map3d) {
                    map3d.resize();
                    var c = map.getCenter();
                    var z = map.getZoom();
                    map3d.jumpTo({
                        center: [c.lng, c.lat],
                        zoom: Math.max(3, z - 0.5),
                        pitch: 58,
                        bearing: 0
                    });
                    syncAllLayersTo3D();
                }
            } else {
                active3DBeams = [];
                active3DPulses = [];
                if (panel) panel.style.display = 'none';
                if (el3D) el3D.style.display = 'none';
                if (el2D) {
                    el2D.style.display = 'block';
                    if (map3d) {
                        try {
                            var c3d = map3d.getCenter();
                            var z3d = map3d.getZoom();
                            map.setView([c3d.lat, c3d.lng], Math.round(z3d + 0.5));
                        } catch(e) {}
                    }
                    map.invalidateSize();
                }
            }
        }
        window.set3DMode = set3DMode;

        function close3DMode() {
            set3DMode(false);
            if (window.pyBridge && window.pyBridge.on_map_3d_toggled) {
                window.pyBridge.on_map_3d_toggled(false);
            }
        }
        window.close3DMode = close3DMode;

        function set3DPitch(pitchVal) {
            if (!map3d) return;
            map3d.easeTo({ pitch: pitchVal, duration: 600 });
            var b58 = document.getElementById('btn-3d-pitch-58');
            var b0 = document.getElementById('btn-3d-pitch-0');
            if (b58 && b0) {
                if (pitchVal > 20) {
                    b58.style.borderColor = '#00D2FF';
                    b58.style.background = '#0C4A6E';
                    b58.style.color = '#38BDF8';
                    b0.style.borderColor = '#30363d';
                    b0.style.background = '#161b22';
                    b0.style.color = '#8b949e';
                } else {
                    b0.style.borderColor = '#00D2FF';
                    b0.style.background = '#0C4A6E';
                    b0.style.color = '#38BDF8';
                    b58.style.borderColor = '#30363d';
                    b58.style.background = '#161b22';
                    b58.style.color = '#8b949e';
                }
            }
        }
        window.set3DPitch = set3DPitch;

        function set3DExaggeration(val) {
            if (!map3d) return;
            try {
                map3d.setTerrain({ source: 'terrain-dem', exaggeration: parseFloat(val) || 2.5 });
            } catch(e) {
                console.error('[3D Map] Failed setting exaggeration:', e);
            }
        }
        window.set3DExaggeration = set3DExaggeration;

        var corescope3DProgram = null;
        var active3DArcs = [];
        var active3DBeams = [];
        var active3DPulses = [];
        var active3DPlanes = [];
        var visualised3DArcs = [];
        var map3dAdsbMarkers = {};
        var map3dSatMarkers = {};
        var map3dOrbitalMarkers = {};

        var corescope3DLayer = {
            id: 'corescope-3d-arcs',
            type: 'custom',
            renderingMode: '3d',
            onAdd: function(map, gl) {
                var vShaderSource = 'attribute vec3 a_pos;' +
                    'attribute vec4 a_color;' +
                    'attribute float a_size;' +
                    'uniform mat4 u_matrix;' +
                    'varying vec4 v_color;' +
                    'void main() {' +
                    '    v_color = a_color;' +
                    '    gl_Position = u_matrix * vec4(a_pos, 1.0);' +
                    '    gl_PointSize = a_size;' +
                    '}';
                var fShaderSource = 'precision mediump float;' +
                    'varying vec4 v_color;' +
                    'uniform int u_render_mode;' +
                    'void main() {' +
                    '    if (u_render_mode == 0) {' +
                    '        gl_FragColor = v_color;' +
                    '    } else if (u_render_mode == 1) {' +
                    '        vec2 coord = gl_PointCoord - vec2(0.5);' +
                    '        float d = length(coord);' +
                    '        if (d > 0.5) discard;' +
                    '        float alpha = smoothstep(0.5, 0.05, d) * v_color.a;' +
                    '        gl_FragColor = vec4(v_color.rgb, alpha);' +
                    '    } else if (u_render_mode == 2) {' +
                    '        vec2 coord = gl_PointCoord - vec2(0.5);' +
                    '        float d = length(coord);' +
                    '        if (d > 0.5) discard;' +
                    '        float alpha = smoothstep(0.5, 0.05, d) * v_color.a;' +
                    '        vec3 col = mix(v_color.rgb, vec3(1.0), 0.18 * (1.0 - smoothstep(0.0, 0.25, d)));' +
                    '        gl_FragColor = vec4(col, alpha);' +
                    '    } else if (u_render_mode == 3) {' +
                    '        vec2 coord = gl_PointCoord - vec2(0.5);' +
                    '        float d = length(coord);' +
                    '        if (d > 0.5 || d < 0.20) discard;' +
                    '        float ring = smoothstep(0.12, 0.0, abs(d - 0.42));' +
                    '        gl_FragColor = vec4(v_color.rgb, ring * v_color.a);' +
                    '    } else {' +
                    '        gl_FragColor = v_color;' +
                    '    }' +
                    '}';
                function compileShader(type, src) {
                    var s = gl.createShader(type);
                    gl.shaderSource(s, src);
                    gl.compileShader(s);
                    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
                        console.error('[3D Layer] Shader compile error:', gl.getShaderInfoLog(s));
                    }
                    return s;
                }
                var vs = compileShader(gl.VERTEX_SHADER, vShaderSource);
                var fs = compileShader(gl.FRAGMENT_SHADER, fShaderSource);
                var prog = gl.createProgram();
                gl.attachShader(prog, vs);
                gl.attachShader(prog, fs);
                gl.linkProgram(prog);
                corescope3DProgram = {
                    program: prog,
                    aPos: gl.getAttribLocation(prog, 'a_pos'),
                    aColor: gl.getAttribLocation(prog, 'a_color'),
                    aSize: gl.getAttribLocation(prog, 'a_size'),
                    uMatrix: gl.getUniformLocation(prog, 'u_matrix'),
                    uRenderMode: gl.getUniformLocation(prog, 'u_render_mode'),
                    buffer: gl.createBuffer()
                };
            },
            render: function(gl, matrix) {
                if (!corescope3DProgram || !map3d) return;
                var p = corescope3DProgram;
                var now = performance.now();

                gl.useProgram(p.program);
                gl.uniformMatrix4fv(p.uMatrix, false, matrix);
                gl.bindBuffer(gl.ARRAY_BUFFER, p.buffer);

                gl.enableVertexAttribArray(p.aPos);
                gl.enableVertexAttribArray(p.aColor);
                gl.enableVertexAttribArray(p.aSize);

                gl.enable(gl.BLEND);
                gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
                gl.depthMask(false);

                var curZoom = map3d.getZoom ? map3d.getZoom() : 8;
                var camOpt = map3d.getFreeCameraOptions ? map3d.getFreeCameraOptions() : null;
                var camPos = camOpt && camOpt.position ? camOpt.position : null;

                // Helper: camera-facing 3D ribbon quad strip
                function build3DRibbon(pts, widthPx, color, alpha, zoom, camPos, fadeTail) {
                    if (!pts || pts.length < 2) return [];
                    var mercPerPx = 1.0 / (512.0 * Math.pow(2, zoom));
                    var pitch = (map3d.getPitch ? map3d.getPitch() : 50) * Math.PI / 180.0;
                    var pitchCorr = 1.0 / Math.max(0.35, Math.cos(pitch));
                    var halfW = (widthPx * 0.5 * pitchCorr) * mercPerPx;

                    var verts = [];
                    var nPts = pts.length;
                    for (var i = 0; i < nPts; i++) {
                        var p = pts[i];
                        var tx = 0, ty = 0;
                        if (i === 0) {
                            tx = pts[1][0] - p[0];
                            ty = pts[1][1] - p[1];
                        } else if (i === nPts - 1) {
                            tx = p[0] - pts[nPts - 2][0];
                            ty = p[1] - pts[nPts - 2][1];
                        } else {
                            tx = pts[i + 1][0] - pts[i - 1][0];
                            ty = pts[i + 1][1] - pts[i - 1][1];
                        }
                        var tLen = Math.sqrt(tx * tx + ty * ty);
                        var nx = 0, ny = 1;
                        if (tLen > 1e-9) {
                            nx = -ty / tLen;
                            ny = tx / tLen;
                        }

                        var ptAlpha = alpha;
                        var ptHalfW = halfW;
                        if (fadeTail && nPts > 1) {
                            var tProg = i / (nPts - 1); // 0.0 at oldest breadcrumb, 1.0 at aircraft tail
                            var tFade = Math.pow(tProg, 1.3); // Smooth curve dissipating into distance
                            ptAlpha = alpha * tFade;
                            ptHalfW = halfW * (0.20 + 0.80 * tProg);
                        }

                        verts.push(
                            p[0] - nx * ptHalfW, p[1] - ny * ptHalfW, p[2],
                            color[0], color[1], color[2], ptAlpha,
                            1.0
                        );
                        verts.push(
                            p[0] + nx * ptHalfW, p[1] + ny * ptHalfW, p[2],
                            color[0], color[1], color[2], ptAlpha,
                            1.0
                        );
                    }
                    return verts;
                }

                // Helper: camera-facing dashed 3D ribbon triangles
                function build3DDashes(pts, widthPx, color, alpha, zoom, camPos, dashSegs, gapSegs) {
                    if (!pts || pts.length < 2) return [];
                    dashSegs = dashSegs || 3;
                    gapSegs = gapSegs || 2;
                    var period = dashSegs + gapSegs;

                    var mercPerPx = 1.0 / (512.0 * Math.pow(2, zoom));
                    var pitch = (map3d.getPitch ? map3d.getPitch() : 50) * Math.PI / 180.0;
                    var pitchCorr = 1.0 / Math.max(0.35, Math.cos(pitch));
                    var halfW = (widthPx * 0.5 * pitchCorr) * mercPerPx;

                    var nPts = pts.length;
                    var leftPts = [];
                    var rightPts = [];
                    for (var i = 0; i < nPts; i++) {
                        var p = pts[i];
                        var tx = 0, ty = 0;
                        if (i === 0) {
                            tx = pts[1][0] - p[0];
                            ty = pts[1][1] - p[1];
                        } else if (i === nPts - 1) {
                            tx = p[0] - pts[nPts - 2][0];
                            ty = p[1] - pts[nPts - 2][1];
                        } else {
                            tx = pts[i + 1][0] - pts[i - 1][0];
                            ty = pts[i + 1][1] - pts[i - 1][1];
                        }
                        var tLen = Math.sqrt(tx * tx + ty * ty);
                        var nx = 0, ny = 1;
                        if (tLen > 1e-9) {
                            nx = -ty / tLen;
                            ny = tx / tLen;
                        }

                        leftPts.push([p[0] - nx * halfW, p[1] - ny * halfW, p[2]]);
                        rightPts.push([p[0] + nx * halfW, p[1] + ny * halfW, p[2]]);
                    }

                    var triVerts = [];
                    for (var k = 0; k < nPts - 1; k++) {
                        if ((k % period) < dashSegs) {
                            var l0 = leftPts[k], r0 = rightPts[k];
                            var l1 = leftPts[k + 1], r1 = rightPts[k + 1];
                            triVerts.push(l0[0], l0[1], l0[2], color[0], color[1], color[2], alpha, 1.0);
                            triVerts.push(r0[0], r0[1], r0[2], color[0], color[1], color[2], alpha, 1.0);
                            triVerts.push(l1[0], l1[1], l1[2], color[0], color[1], color[2], alpha, 1.0);

                            triVerts.push(r0[0], r0[1], r0[2], color[0], color[1], color[2], alpha, 1.0);
                            triVerts.push(r1[0], r1[1], r1[2], color[0], color[1], color[2], alpha, 1.0);
                            triVerts.push(l1[0], l1[1], l1[2], color[0], color[1], color[2], alpha, 1.0);
                        }
                    }
                    return triVerts;
                }

                // 1. Lingering completed parabolic arcs
                var hasActiveFading = false;
                gl.uniform1i(p.uRenderMode, 0);

                for (var i = active3DArcs.length - 1; i >= 0; i--) {
                    var arc = active3DArcs[i];
                    var age = now - arc.createdAt;
                    if (age >= arc.ttl) {
                        active3DArcs.splice(i, 1);
                        continue;
                    }
                    hasActiveFading = true;
                    var alpha = Math.max(0, 1.0 - (age / arc.ttl)) * 0.92;
                    var pts = arc.points;
                    if (!pts || pts.length < 2) continue;

                    var ribVerts = build3DRibbon(pts, 2.5, arc.color, alpha, curZoom, camPos);
                    if (ribVerts.length > 0) {
                        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(ribVerts), gl.DYNAMIC_DRAW);
                        gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                        gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                        gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                        gl.drawArrays(gl.TRIANGLE_STRIP, 0, ribVerts.length / 8);
                    }
                }

                // 1b. Persistent route visualisation 3D arcs (Route Visualisation)
                if (visualised3DArcs && visualised3DArcs.length > 0) {
                    for (var vi = 0; vi < visualised3DArcs.length; vi++) {
                        var varc = visualised3DArcs[vi];
                        var vpts = varc.points;
                        if (!vpts || vpts.length < 2) continue;
                        var vStyle = varc.style || 'solid';

                        if (vStyle === 'solid') {
                            gl.uniform1i(p.uRenderMode, 0);

                            // Luminous neon glow ribbon along solid visualized arc (continuous ribbon)
                            var vGlowRibbon = build3DRibbon(vpts, 7.0, varc.color, 0.35, curZoom, camPos);
                            if (vGlowRibbon.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vGlowRibbon), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLE_STRIP, 0, vGlowRibbon.length / 8);
                            }

                            // Razor-sharp solid core transmission ribbon (prominent & vivid saturated color)
                            var vCoreRibbon = build3DRibbon(vpts, 2.6, varc.color, 0.95, curZoom, camPos);
                            if (vCoreRibbon.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vCoreRibbon), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLE_STRIP, 0, vCoreRibbon.length / 8);
                            }
                        } else if (vStyle === 'dashed') {
                            gl.uniform1i(p.uRenderMode, 0);

                            // Glow dashed ribbon
                            var vDashGlow = build3DDashes(vpts, 7.0, varc.color, 0.35, curZoom, camPos, 3, 2);
                            if (vDashGlow.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vDashGlow), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLES, 0, vDashGlow.length / 8);
                            }

                            // Core dashed ribbon
                            var vDashCore = build3DDashes(vpts, 2.6, varc.color, 0.95, curZoom, camPos, 3, 2);
                            if (vDashCore.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vDashCore), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLES, 0, vDashCore.length / 8);
                            }
                        } else if (vStyle === 'dotted') {
                            // Dotted: neat, fine circular points spaced evenly along the arc
                            gl.uniform1i(p.uRenderMode, 1);
                            var vDotData = [];
                            var dotStep = Math.max(1, Math.round(vpts.length / 48));
                            for (var vtk = 0; vtk < vpts.length; vtk += dotStep) {
                                vDotData.push(vpts[vtk][0], vpts[vtk][1], vpts[vtk][2], varc.color[0], varc.color[1], varc.color[2], 0.95, 3.5 * (window.devicePixelRatio || 1));
                            }
                            if (vDotData.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vDotData), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.POINTS, 0, vDotData.length / 8);
                            }
                        }
                    }
                }

                // 2. Currently traveling CoreScope particle beams & white-core bead head
                // Disable depth testing so packet lines & pulses ALWAYS render as top-most layer above all nodes & terrain
                gl.disable(gl.DEPTH_TEST);
                for (var b = active3DBeams.length - 1; b >= 0; b--) {
                    var beam = active3DBeams[b];
                    var elapsed = now - beam.startTime;
                    var progress = Math.min(1.0, elapsed / beam.duration);

                    var totalPts = beam.curve.length;
                    var curIdx = Math.floor(progress * (totalPts - 1));
                    var subPts = beam.curve.slice(0, Math.max(2, curIdx + 1));
                    var bStyle = beam.style || 'solid';

                    if (bStyle === 'solid') {
                        gl.uniform1i(p.uRenderMode, 0);

                        // Subtle glowing neon trail ribbon behind traveling packet (NO dots)
                        // Subtle glowing neon trail ribbon behind traveling packet (NO dots)
                        var bGlowRibbon = build3DRibbon(subPts, 5.0, beam.color, 0.30, curZoom, camPos);
                        if (bGlowRibbon.length > 0) {
                            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(bGlowRibbon), gl.DYNAMIC_DRAW);
                            gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                            gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                            gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                            gl.drawArrays(gl.TRIANGLE_STRIP, 0, bGlowRibbon.length / 8);
                        }

                        // Sharp saturated beam core transmission ribbon
                        var bCoreRibbon = build3DRibbon(subPts, 2.2, beam.color, 0.95, curZoom, camPos);
                        if (bCoreRibbon.length > 0) {
                            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(bCoreRibbon), gl.DYNAMIC_DRAW);
                            gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                            gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                            gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                            gl.drawArrays(gl.TRIANGLE_STRIP, 0, bCoreRibbon.length / 8);
                        }
                    } else if (bStyle === 'dashed') {
                        gl.uniform1i(p.uRenderMode, 0);
                        var bDashCore = build3DDashes(subPts, 2.2, beam.color, 0.95, curZoom, camPos, 3, 2);
                        if (bDashCore.length > 0) {
                            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(bDashCore), gl.DYNAMIC_DRAW);
                            gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                            gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                            gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                            gl.drawArrays(gl.TRIANGLES, 0, bDashCore.length / 8);
                        }
                    } else if (bStyle === 'dotted') {
                        gl.uniform1i(p.uRenderMode, 1);
                        var bDotData = [];
                        var bDotStep = Math.max(1, Math.round(subPts.length / 32));
                        for (var btk = 0; btk < subPts.length; btk += bDotStep) {
                            bDotData.push(subPts[btk][0], subPts[btk][1], subPts[btk][2], beam.color[0], beam.color[1], beam.color[2], 0.95, 3.5 * (window.devicePixelRatio || 1));
                        }
                        if (bDotData.length > 0) {
                            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(bDotData), gl.DYNAMIC_DRAW);
                            gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                            gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                            gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                            gl.drawArrays(gl.POINTS, 0, bDotData.length / 8);
                        }
                    }

                    // Traveling CoreScope saturated neon bead head
                    var headPt = beam.curve[curIdx];
                    if (headPt) {
                        gl.uniform1i(p.uRenderMode, 2);
                        var beadData = [
                            headPt[0], headPt[1], headPt[2],
                            beam.color[0], beam.color[1], beam.color[2], 1.0,
                            9.0 * (window.devicePixelRatio || 1)
                        ];
                        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(beadData), gl.DYNAMIC_DRAW);
                        gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                        gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                        gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                        gl.drawArrays(gl.POINTS, 0, 1);
                    }

                    if (progress >= 1.0) {
                        active3DBeams.splice(b, 1);
                        if (beam.onComplete) beam.onComplete();
                    }
                }

                // 3. Ground ripple pulses at hops (CoreScope hollow expanding radar rings flat on map terrain)
                // Disable depth test during pulse rendering so foreground terrain never cuts off the bottom half into an arch
                gl.disable(gl.DEPTH_TEST);
                gl.uniform1i(p.uRenderMode, 0);
                for (var pl = active3DPulses.length - 1; pl >= 0; pl--) {
                    var pulse = active3DPulses[pl];
                    var pElapsed = now - pulse.startTime;
                    var pProg = pElapsed / pulse.duration;
                    if (pProg >= 1.0) {
                        active3DPulses.splice(pl, 1);
                        continue;
                    }
                    var pAlpha = Math.max(0, 1.0 - pProg) * 0.95;
                    var mercPerPx = 1.0 / (512.0 * Math.pow(2, curZoom));
                    var rOut = (12.0 + pProg * 48.0) * mercPerPx;
                    var rIn = Math.max(0, rOut - (4.0 + (1.0 - pProg) * 2.5) * mercPerPx);
                    var cx = pulse.pos[0], cy = pulse.pos[1], cz = pulse.pos[2];
                    var col = pulse.color;

                    var ringVerts = [];
                    var segments = 36;
                    for (var seg = 0; seg <= segments; seg++) {
                        var theta = (seg / segments) * 2.0 * Math.PI;
                        var cosT = Math.cos(theta);
                        var sinT = Math.sin(theta);
                        ringVerts.push(
                            cx + rIn * cosT, cy + rIn * sinT, cz,
                            col[0], col[1], col[2], pAlpha * 0.20,
                            1.0
                        );
                        ringVerts.push(
                            cx + rOut * cosT, cy + rOut * sinT, cz,
                            col[0], col[1], col[2], pAlpha * 0.95,
                            1.0
                        );
                    }
                    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(ringVerts), gl.DYNAMIC_DRAW);
                    gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                    gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                    gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                    gl.drawArrays(gl.TRIANGLE_STRIP, 0, ringVerts.length / 8);
                }
                gl.enable(gl.DEPTH_TEST);

                // 3b. ADS-B 3D Cylindrical Airspace Altitude Divider Rings (Fine, delicate grey dotted rings)
                if (window._lastAdsbCylinderMeta && typeof getCircle3DPoints === 'function') {
                    var cylMeta = window._lastAdsbCylinderMeta;
                    var cHeights = cylMeta.heights || [];
                    var dotData = [];
                    var dpr = (window.devicePixelRatio || 1);

                    for (var chi = 0; chi < cHeights.length; chi++) {
                        var hVal = cHeights[chi];
                        var ringPts = getCircle3DPoints(cylMeta.lon, cylMeta.lat, cylMeta.radiusM, hVal, 192);
                        for (var rpi = 0; rpi < ringPts.length; rpi++) {
                            var rpt = ringPts[rpi];
                            // Delicate soft neutral slate grey dot
                            dotData.push(rpt[0], rpt[1], rpt[2], 0.70, 0.76, 0.86, 0.50, 2.2 * dpr);
                        }
                    }

                    if (dotData.length > 0) {
                        gl.disable(gl.DEPTH_TEST);
                        gl.uniform1i(p.uRenderMode, 1); // Anti-aliased circular points
                        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(dotData), gl.DYNAMIC_DRAW);
                        gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                        gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                        gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                        gl.drawArrays(gl.POINTS, 0, dotData.length / 8);
                        gl.enable(gl.DEPTH_TEST);
                    }
                }

                // 4. ADS-B 3D Aircraft Vertical Drop Lines, Ground Footprint Shadows, & Screen Projection
                if (typeof active3DPlanes !== 'undefined' && active3DPlanes && active3DPlanes.length > 0) {
                    var cCanvas = map3d.getCanvas();
                    var cW = cCanvas ? cCanvas.clientWidth : 800;
                    var cH = cCanvas ? cCanvas.clientHeight : 600;
                    var curPitch = (map3d.getPitch ? map3d.getPitch() : 0);
                    var curBearing = (map3d.getBearing ? map3d.getBearing() : 0);

                    var stemVerts = [];
                    var shadowVerts = [];

                    for (var pi = 0; pi < active3DPlanes.length; pi++) {
                        var planeItem = active3DPlanes[pi];
                        var mcG = maplibregl.MercatorCoordinate.fromLngLat([planeItem.lon, planeItem.lat], planeItem.groundElev);
                        var mcA = maplibregl.MercatorCoordinate.fromLngLat([planeItem.lon, planeItem.lat], planeItem.altM);

                        // Color-code vertical stem line and ground radar footprint based on aircraft altitude
                        var altFtVal = planeItem.altFt || 1000;
                        var sColor = [0.49, 0.83, 0.99]; // Pale Blue (>25k ft)
                        if (altFtVal < 2000) {
                            sColor = [0.94, 0.27, 0.27]; // Red (<2k ft)
                        } else if (altFtVal < 7000) {
                            sColor = [0.85, 0.27, 0.94]; // Magenta (2k-7k ft)
                        } else if (altFtVal < 25000) {
                            sColor = [0.55, 0.36, 0.96]; // Purple (7k-25k ft)
                        }

                        // Vertical stem line from terrain directly to aircraft altitude
                        stemVerts.push(mcG.x, mcG.y, mcG.z, sColor[0], sColor[1], sColor[2], 0.70, 1.0);
                        stemVerts.push(mcA.x, mcA.y, mcA.z, sColor[0], sColor[1], sColor[2], 0.95, 1.0);

                        // Ground contact shadow ring
                        shadowVerts.push(mcG.x, mcG.y, mcG.z, sColor[0], sColor[1], sColor[2], 0.90, 12.0 * (window.devicePixelRatio || 1));

                        // Project 3D coordinate to screen pixel
                        var pW = matrix[3]*mcA.x + matrix[7]*mcA.y + matrix[11]*mcA.z + matrix[15];
                        var planeDom = document.getElementById('adsb-plane-3d-' + planeItem.hex);
                        if (planeDom) {
                            if (pW > 0.0001) {
                                var cX = matrix[0]*mcA.x + matrix[4]*mcA.y + matrix[8]*mcA.z + matrix[12];
                                var cY = matrix[1]*mcA.x + matrix[5]*mcA.y + matrix[9]*mcA.z + matrix[13];
                                var px = (cX / pW * 0.5 + 0.5) * cW;
                                var py = (-cY / pW * 0.5 + 0.5) * cH;
                                if (px >= -80 && px <= cW + 80 && py >= -80 && py <= cH + 80) {
                                    planeDom.style.display = 'block';
                                    planeDom.style.left = Math.round(px) + 'px';
                                    planeDom.style.top = Math.round(py) + 'px';
                                    var relTrack = (planeItem.track || 0) - curBearing;
                                    var glyph = planeDom.querySelector('.adsb-plane-svg-glyph');
                                    if (glyph) {
                                        glyph.style.transform = 'perspective(800px) rotateX(' + curPitch.toFixed(1) + 'deg) rotateZ(' + relTrack.toFixed(1) + 'deg)';
                                    }
                                } else {
                                    planeDom.style.display = 'none';
                                }
                            } else {
                                planeDom.style.display = 'none';
                            }
                        }
                    }

                    // Render vertical stems and ground contact rings with DEPTH_TEST disabled so terrain elevation doesn't clip them
                    gl.disable(gl.DEPTH_TEST);

                    if (stemVerts.length > 0) {
                        gl.uniform1i(p.uRenderMode, 0);
                        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(stemVerts), gl.DYNAMIC_DRAW);
                        gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                        gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                        gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                        gl.lineWidth(2.0);
                        gl.drawArrays(gl.LINES, 0, stemVerts.length / 8);
                    }

                    if (shadowVerts.length > 0) {
                        gl.uniform1i(p.uRenderMode, 3);
                        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(shadowVerts), gl.DYNAMIC_DRAW);
                        gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                        gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                        gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                        gl.drawArrays(gl.POINTS, 0, shadowVerts.length / 8);
                    }

                    // 4b. 3D Flight Contrails attached directly to floating 3D aircraft
                    gl.uniform1i(p.uRenderMode, 0);
                    for (var piTrail = 0; piTrail < active3DPlanes.length; piTrail++) {
                        var planeItemT = active3DPlanes[piTrail];
                        var hist = aircraftHistory[planeItemT.hex];
                        if (!hist || hist.length < 2) continue;

                        var trailPts = [];
                        var lastX = null, lastY = null;
                        for (var hi = 0; hi < hist.length; hi++) {
                            var hPt = hist[hi];
                            var hAltFt = hPt.alt_baro || planeItemT.altFt || 1000;
                            var hRawAltM = Math.max(60, hAltFt * 0.3048);
                            var hDisplayAltM = planeItemT.groundElev + Math.max(300, (hRawAltM / 10000.0) * 46000.0);
                            var mcH = maplibregl.MercatorCoordinate.fromLngLat([hPt.lon, hPt.lat], hDisplayAltM);
                            if (lastX !== null) {
                                var dSq = (mcH.x - lastX)*(mcH.x - lastX) + (mcH.y - lastY)*(mcH.y - lastY);
                                if (dSq < 4e-12) continue; // skip points closer than 2e-6
                            }
                            lastX = mcH.x;
                            lastY = mcH.y;
                            trailPts.push([mcH.x, mcH.y, mcH.z]);
                        }

                        var mcCurr = maplibregl.MercatorCoordinate.fromLngLat([planeItemT.lon, planeItemT.lat], planeItemT.altM);
                        // Attach trail directly to the rear tail of the aircraft
                        var trackRad = (planeItemT.track || 0) * Math.PI / 180.0;
                        var tailOffsetM = 12.0;
                        var cosLat = Math.max(0.1, Math.cos(planeItemT.lat * Math.PI / 180.0));
                        var tailDLat = (tailOffsetM / 111320.0) * (-Math.cos(trackRad));
                        var tailDLon = (tailOffsetM / (111320.0 * cosLat)) * (-Math.sin(trackRad));
                        var mcTail = maplibregl.MercatorCoordinate.fromLngLat([planeItemT.lon + tailDLon, planeItemT.lat + tailDLat], planeItemT.altM);
                        trailPts.push([mcTail.x, mcTail.y, mcTail.z]);

                        if (trailPts.length >= 2) {
                            // Soft vapor halo ribbon that fades out smoothly along the history
                            var glowVerts = build3DRibbon(trailPts, 6.2, [0.10, 0.80, 0.98], 0.28, curZoom, camPos, true);
                            if (glowVerts.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(glowVerts), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLE_STRIP, 0, glowVerts.length / 8);
                            }

                            // Crisp contrail core ribbon with tail dissipation fade
                            var trailVerts = build3DRibbon(trailPts, 3.2, [0.06, 0.75, 0.95], 0.80, curZoom, camPos, true);
                            if (trailVerts.length > 0) {
                                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(trailVerts), gl.DYNAMIC_DRAW);
                                gl.vertexAttribPointer(p.aPos, 3, gl.FLOAT, false, 32, 0);
                                gl.vertexAttribPointer(p.aColor, 4, gl.FLOAT, false, 32, 12);
                                gl.vertexAttribPointer(p.aSize, 1, gl.FLOAT, false, 32, 28);
                                gl.drawArrays(gl.TRIANGLE_STRIP, 0, trailVerts.length / 8);
                            }
                        }
                    }
                    gl.enable(gl.DEPTH_TEST);
                }

                gl.depthMask(true);
                gl.disable(gl.BLEND);

                // Repaint while animations, fading arcs, or active planes are present
                if (active3DBeams.length > 0 || active3DPulses.length > 0 || hasActiveFading || (typeof active3DPlanes !== 'undefined' && active3DPlanes && active3DPlanes.length > 0)) {
                    map3d.triggerRepaint();
                }
            }
        };

        function initMap3D() {
            if (map3dInitialized) return;
            map3dInitialized = true;

            var curCenter = map.getCenter();
            var curZoom = map.getZoom();

            try {
                map3d = new maplibregl.Map({
                    container: 'map-3d',
                    style: 'https://tiles.openfreemap.org/styles/dark',
                    center: [curCenter.lng, curCenter.lat],
                    zoom: Math.max(3, curZoom - 0.5),
                    pitch: 58,
                    bearing: 0,
                    maxPitch: 85,
                    pixelRatio: Math.min(window.devicePixelRatio || 1, 1.25),
                    fadeDuration: 0,
                    dragRotate: false,
                    canvasContextAttributes: {
                        antialias: false,
                        powerPreference: 'high-performance'
                    },
                    transformRequest: function(url, resourceType) {
                        if (url && (url.indexOf('elevation-tiles-prod/terrarium/7/62/40.png') !== -1 || url.indexOf('/7/62/40.png') !== -1)) {
                            if (window._cleanDem76240) {
                                return { url: window._cleanDem76240 };
                            }
                        }
                        return { url: url };
                    }
                });

                // Disable built-in right-click rotation; mouse middle-click (button 1) rotates and pitches map smoothly at 60fps
                var isMiddleDragging = false;
                var lastMouseX = 0;
                var lastMouseY = 0;
                var curTargetBearing = 0;
                var curTargetPitch = 58;
                var rafRotateId = null;

                var container3D = document.getElementById('map-3d');
                if (container3D) {
                    container3D.addEventListener('mousedown', function(e) {
                        if (e.button === 1) { // Middle click
                            e.preventDefault();
                            e.stopPropagation();
                            isMiddleDragging = true;
                            lastMouseX = e.clientX;
                            lastMouseY = e.clientY;
                            if (map3d) {
                                curTargetBearing = map3d.getBearing();
                                curTargetPitch = map3d.getPitch();
                            }
                            document.body.style.cursor = 'grab';
                        }
                    });
                }

                window.addEventListener('mousemove', function(e) {
                    if (!isMiddleDragging || !map3d) return;
                    e.preventDefault();
                    var dx = e.clientX - lastMouseX;
                    var dy = e.clientY - lastMouseY;
                    lastMouseX = e.clientX;
                    lastMouseY = e.clientY;

                    curTargetBearing = (curTargetBearing + dx * 0.45) % 360;
                    curTargetPitch = Math.max(0, Math.min(85, curTargetPitch - dy * 0.40));

                    if (!rafRotateId) {
                        rafRotateId = requestAnimationFrame(function() {
                            rafRotateId = null;
                            if (map3d && isMiddleDragging) {
                                map3d.jumpTo({ bearing: curTargetBearing, pitch: curTargetPitch });
                            }
                        });
                    }
                });

                window.addEventListener('mouseup', function(e) {
                    if (e.button === 1 && isMiddleDragging) {
                        isMiddleDragging = false;
                        if (rafRotateId) {
                            cancelAnimationFrame(rafRotateId);
                            rafRotateId = null;
                        }
                        document.body.style.cursor = '';
                    }
                });

                // Ensure 3D Floating Window Options (#map3dControls) is draggable
                if (typeof makeOverlayDraggable === 'function') {
                    makeOverlayDraggable('map3dControls', 'map3dControlsHeader', 'map_3d_controls');
                }

                // Zoom listeners to re-evaluate orbital companion visibility & LOD on zoom completion
                map3d.on('zoomend', function() {
                    if (window._companionOrbitalsActive && typeof syncOrbitalsTo3D === 'function') {
                        syncOrbitalsTo3D();
                    }
                });

                map3d.addControl(new maplibregl.NavigationControl({
                    visualizePitch: true,
                    showCompass: true,
                    showZoom: true
                }), 'bottom-right');

                map3d.on('load', function() {
                    console.log('[3D Map] MapLibre OpenFreeMap dark style loaded successfully');

                    // 1. Add AWS Terrarium DEM (full global pyramid zoom 0-15, complete low-zoom coverage, zero CORS tile errors)
                    try {
                        map3d.addSource('terrain-dem', {
                            type: 'raster-dem',
                            tiles: [
                                'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
                            ],
                            encoding: 'terrarium',
                            tileSize: 256,
                            maxzoom: 12
                        });
                        map3d.setTerrain({
                            source: 'terrain-dem',
                            exaggeration: 1.5
                        });
                        map3d.setSky({
                            'sky-color': '#0d1117',
                            'sky-horizon-blend': 0.5,
                            'horizon-color': '#161b22',
                            'horizon-fog-blend': 0.5,
                            'fog-color': '#0d1117',
                            'fog-ground-blend': 0.5
                        });
                        map3d.addLayer({
                            id: 'hillshade-layer',
                            type: 'hillshade',
                            source: 'terrain-dem',
                            minzoom: 4,
                            paint: {
                                'hillshade-exaggeration': 0.85,
                                'hillshade-shadow-color': '#000000',
                                'hillshade-highlight-color': '#ffffff',
                                'hillshade-accent-color': '#1f2937'
                            }
                        });
                    } catch(te) {
                        console.error('[3D Map] Error configuring terrain DEM:', te);
                    }

                    // 2. Initialize layer sources & custom WebGL layers
                    try {
                        // Scopes
                        map3d.addSource('scopes-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'scopes-3d-fill',
                            type: 'fill',
                            source: 'scopes-3d',
                            paint: {
                                'fill-color': ['get', 'color'],
                                'fill-opacity': ['get', 'fillOpacity']
                            }
                        });
                        map3d.addLayer({
                            id: 'scopes-3d-line',
                            type: 'line',
                            source: 'scopes-3d',
                            paint: {
                                'line-color': ['get', 'color'],
                                'line-width': ['get', 'weight']
                            }
                        });

                        // Heatmap
                        map3d.addSource('heatmap-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'heatmap-3d-layer',
                            type: 'heatmap',
                            source: 'heatmap-3d',
                            paint: {
                                'heatmap-weight': ['get', 'weight'],
                                'heatmap-intensity': 1.6,
                                'heatmap-radius': 28,
                                'heatmap-opacity': 0.70
                            }
                        });

                        // Lightning
                        map3d.addSource('lightning-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'lightning-3d-layer',
                            type: 'circle',
                            source: 'lightning-3d',
                            paint: {
                                'circle-color': ['get', 'color'],
                                'circle-radius': 5,
                                'circle-stroke-width': 1,
                                'circle-stroke-color': '#ffffff'
                            }
                        });

                        // ADS-B Trails
                        map3d.addSource('adsb-3d-trails', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'adsb-3d-trails-layer',
                            type: 'line',
                            source: 'adsb-3d-trails',
                            paint: {
                                'line-color': '#06B6D4',
                                'line-width': 2.0,
                                'line-opacity': 0.8
                            }
                        });

                        // ADS-B 3D Vertical Multi-Tier Hologram Cylinder HUD
                        map3d.addSource('adsb-3d-cylinder', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'adsb-3d-cylinder-layer',
                            type: 'fill-extrusion',
                            source: 'adsb-3d-cylinder',
                            paint: {
                                'fill-extrusion-color': ['get', 'color'],
                                'fill-extrusion-height': ['get', 'height'],
                                'fill-extrusion-base': ['get', 'base_height'],
                                'fill-extrusion-opacity': 0.22
                            }
                        });

                        // ADS-B 3D Radial Range Rings (10, 25, 50 NM)
                        map3d.addSource('adsb-3d-rings', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'adsb-3d-rings-layer',
                            type: 'line',
                            source: 'adsb-3d-rings',
                            paint: {
                                'line-color': ['get', 'color'],
                                'line-width': ['get', 'width'],
                                'line-opacity': 0.65,
                                'line-dasharray': [3, 2]
                            }
                        });

                        // Satellites 3D Plumb Lines & Nadir Dots
                        map3d.addSource('satellites-3d-plumb', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'satellites-3d-plumb-lines',
                            type: 'line',
                            source: 'satellites-3d-plumb',
                            filter: ['==', '$type', 'LineString'],
                            paint: {
                                'line-color': ['case', ['get', 'selected'], '#38BDF8', '#0EA5E9'],
                                'line-width': ['case', ['get', 'selected'], 2.5, 1.4],
                                'line-opacity': 0.85,
                                'line-dasharray': [3, 2]
                            }
                        });
                        map3d.addLayer({
                            id: 'satellites-3d-nadir-dots',
                            type: 'circle',
                            source: 'satellites-3d-plumb',
                            filter: ['==', '$type', 'Point'],
                            paint: {
                                'circle-color': ['case', ['get', 'selected'], '#38BDF8', '#06B6D4'],
                                'circle-radius': ['case', ['get', 'selected'], 6, 3.5],
                                'circle-stroke-width': 1.2,
                                'circle-stroke-color': '#FFFFFF'
                            }
                        });

                        // Satellites 3D Footprint Ring
                        map3d.addSource('satellites-3d-footprints', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'satellites-3d-footprints-line',
                            type: 'line',
                            source: 'satellites-3d-footprints',
                            paint: {
                                'line-color': '#0EA5E9',
                                'line-width': 1.8,
                                'line-opacity': 0.65,
                                'line-dasharray': [4, 3]
                            }
                        });

                        // Tropo
                        map3d.addSource('tropo-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'tropo-3d-fill',
                            type: 'fill',
                            source: 'tropo-3d',
                            paint: {
                                'fill-color': ['get', 'fillColor'],
                                'fill-opacity': ['get', 'fillOpacity']
                            }
                        });
                        map3d.addLayer({
                            id: 'tropo-3d-line',
                            type: 'line',
                            source: 'tropo-3d',
                            paint: {
                                'line-color': ['get', 'strokeColor'],
                                'line-width': 1.0
                            }
                        });

                        // Aurora
                        map3d.addSource('aurora-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
                        map3d.addLayer({
                            id: 'aurora-3d-fill',
                            type: 'fill',
                            source: 'aurora-3d',
                            paint: {
                                'fill-color': ['get', 'fill'],
                                'fill-opacity': 0.45
                            }
                        });

                        // Register Room Server Diamond canvas image icon with soft magenta glow
                        function createRoomDiamondIcon() {
                            try {
                                var dCanvas = document.createElement('canvas');
                                dCanvas.width = 40;
                                dCanvas.height = 40;
                                var dctx = dCanvas.getContext('2d');
                                dctx.translate(20, 20);
                                dctx.rotate(45 * Math.PI / 180);
                                dctx.shadowColor = '#D946EF';
                                dctx.shadowBlur = 8;
                                dctx.fillStyle = '#D946EF';
                                dctx.fillRect(-10, -10, 20, 20);
                                dctx.shadowBlur = 0;
                                dctx.fillStyle = '#FFFFFF';
                                dctx.fillRect(-4, -4, 8, 8);
                                var img = new Image();
                                img.onload = function() {
                                    if (map3d && (!map3d.hasImage || !map3d.hasImage('room-server-diamond'))) {
                                        map3d.addImage('room-server-diamond', img);
                                    }
                                };
                                img.src = dCanvas.toDataURL();
                            } catch(e) {
                                console.warn('[3D Map] Failed creating room server diamond icon:', e);
                            }
                        }
                        createRoomDiamondIcon();
                        map3d.on('styleimagemissing', function(e) {
                            if (e && e.id === 'room-server-diamond') createRoomDiamondIcon();
                        });

                        // High-Performance GPU Nodes Layer Source & Layers (Single WebGL Draw Call)
                        map3d.addSource('nodes-3d', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

                        // Dedicated generous hit-target layer for effortless clicking and context menus (24px radius)
                        map3d.addLayer({
                            id: 'nodes-3d-hit-target',
                            type: 'circle',
                            source: 'nodes-3d',
                            paint: {
                                'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 14.0, 12, 20.0, 16, 28.0],
                                'circle-opacity': 0.001
                            }
                        });

                        // 0. Soft Luminous Glowing Halo for all nodes (authentic GPU aura)
                        map3d.addLayer({
                            id: 'nodes-3d-halo',
                            type: 'circle',
                            source: 'nodes-3d',
                            paint: {
                                'circle-color': ['get', 'color'],
                                'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 4.5, 12, 8.0, 16, 13.0],
                                'circle-blur': 0.85,
                                'circle-opacity': 0.65
                            }
                        });

                        // 1. Companion & Client nodes (delicate glowing pinpoint without harsh stroke)
                        map3d.addLayer({
                            id: 'nodes-3d-clients',
                            type: 'circle',
                            source: 'nodes-3d',
                            filter: ['all', ['!=', ['get', 'is_repeater'], true], ['!=', ['get', 'is_room_server'], true]],
                            paint: {
                                'circle-color': ['get', 'color'],
                                'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 2.0, 12, 3.4, 16, 5.2],
                                'circle-stroke-width': 0,
                                'circle-opacity': ['coalesce', ['get', 'opacity'], 0.95]
                            }
                        });

                        // 2. Repeaters (clean pinpoint dot; stroke only for active orbital beacon ring)
                        map3d.addLayer({
                            id: 'nodes-3d-repeaters',
                            type: 'circle',
                            source: 'nodes-3d',
                            filter: ['all', ['==', ['get', 'is_repeater'], true], ['!=', ['get', 'is_room_server'], true]],
                            paint: {
                                'circle-color': ['get', 'color'],
                                'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 2.8, 12, 4.5, 16, 7.2],
                                'circle-stroke-width': ['case', ['get', 'is_orbital_beacon'], 2.0, 0],
                                'circle-stroke-color': ['case', ['get', 'is_favorite'], '#AA55FF', '#FFD335'],
                                'circle-opacity': ['coalesce', ['get', 'opacity'], 1.0]
                            }
                        });

                        // 2b. Room Servers (prominent glowing diamond shape larger than repeaters)
                        if (map3d.hasImage && map3d.hasImage('room-server-diamond')) {
                            map3d.addLayer({
                                id: 'nodes-3d-room-servers',
                                type: 'symbol',
                                source: 'nodes-3d',
                                filter: ['==', ['get', 'is_room_server'], true],
                                layout: {
                                    'icon-image': 'room-server-diamond',
                                    'icon-size': ['interpolate', ['linear'], ['zoom'], 6, 0.45, 12, 0.70, 16, 1.0],
                                    'icon-allow-overlap': true,
                                    'icon-ignore-placement': true
                                }
                            });
                        } else {
                            map3d.addLayer({
                                id: 'nodes-3d-room-servers',
                                type: 'circle',
                                source: 'nodes-3d',
                                filter: ['==', ['get', 'is_room_server'], true],
                                paint: {
                                    'circle-color': '#D946EF',
                                    'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 3.5, 12, 6.0, 16, 9.0],
                                    'circle-stroke-width': 1.5,
                                    'circle-stroke-color': '#FFFFFF'
                                }
                            });
                        }

                        // 3. Repeater Aliases at higher zoom
                        map3d.addLayer({
                            id: 'nodes-3d-labels',
                            type: 'symbol',
                            source: 'nodes-3d',
                            minzoom: 10.5,
                            filter: ['==', ['get', 'is_repeater'], true],
                            layout: {
                                'text-field': ['get', 'alias'],
                                'text-size': 10,
                                'text-offset': [0, 1.2],
                                'text-anchor': 'top',
                                'text-optional': true
                            },
                            paint: {
                                'text-color': '#E5E7EB',
                                'text-halo-color': '#0d1117',
                                'text-halo-width': 1.5
                            }
                        });

                        // CoreScope 3D Arcs Custom WebGL Layer (Top-most layer above all nodes & terrain)
                        map3d.addLayer(corescope3DLayer);

                        function get3DNodeAtPoint(point, radiusPx) {
                            radiusPx = radiusPx || 18;
                            var bbox = [[point.x - radiusPx, point.y - radiusPx], [point.x + radiusPx, point.y + radiusPx]];
                            var feats = map3d.queryRenderedFeatures(bbox, {
                                layers: ['nodes-3d-hit-target', 'nodes-3d-repeaters', 'nodes-3d-room-servers', 'nodes-3d-clients', 'nodes-3d-halo']
                            });
                            if (feats && feats.length > 0) {
                                var p = feats[0].properties;
                                var nodeData = p;
                                if (p && p.node_json) {
                                    try { nodeData = JSON.parse(p.node_json); } catch(err) {}
                                }
                                return { feature: feats[0], properties: p, nodeData: nodeData };
                            }
                            return null;
                        }

                        function on3DNodeClick(e) {
                            var hit = null;
                            if (e.point) {
                                hit = get3DNodeAtPoint(e.point, 20);
                            } else if (e.features && e.features[0]) {
                                var p = e.features[0].properties;
                                var nd = p;
                                if (p && p.node_json) {
                                    try { nd = JSON.parse(p.node_json); } catch(ex){}
                                }
                                hit = { nodeData: nd, properties: p };
                            }
                            if (!hit || !hit.nodeData) return;
                            var nodeData = hit.nodeData;
                            if (window._map3dNodePopup) {
                                try { window._map3dNodePopup.remove(); } catch(ex){}
                            }
                            var coords = (nodeData.lon != null && nodeData.lat != null) ?
                                [Number(nodeData.lon), Number(nodeData.lat)] : e.lngLat;
                            window._map3dNodePopup = new maplibregl.Popup({ offset: 12, className: 'custom-popup' })
                                .setLngLat(coords)
                                .setHTML(buildNodePopupContent(nodeData))
                                .addTo(map3d);
                        }

                        map3d.on('click', on3DNodeClick);
                        map3d.on('click', 'nodes-3d-hit-target', on3DNodeClick);
                        map3d.on('click', 'nodes-3d-clients', on3DNodeClick);
                        map3d.on('click', 'nodes-3d-repeaters', on3DNodeClick);
                        map3d.on('click', 'nodes-3d-room-servers', on3DNodeClick);
                        map3d.on('click', 'nodes-3d-halo', on3DNodeClick);

                        function handle3DContextMenu(e) {
                            if (e.originalEvent) {
                                e.originalEvent.preventDefault();
                                e.originalEvent.stopPropagation();
                            }
                            var hit = get3DNodeAtPoint(e.point, 22);
                            if (hit && hit.nodeData) {
                                var nd = hit.nodeData;
                                if (window.pyBridge && window.pyBridge.on_node_context_menu) {
                                    window.pyBridge.on_node_context_menu(
                                        nd.node_id,
                                        nd.alias || nd.node_id || '',
                                        !!(nd.is_repeater || hit.properties.is_repeater),
                                        !!(nd.is_phantom || hit.properties.is_phantom),
                                        e.lngLat.lat,
                                        e.lngLat.lng,
                                        Math.round(e.point.x),
                                        Math.round(e.point.y)
                                    );
                                }
                                return;
                            }
                            if (window.pyBridge && window.pyBridge.on_map_context_menu) {
                                window.pyBridge.on_map_context_menu(e.lngLat.lat, e.lngLat.lng, Math.round(e.point.x), Math.round(e.point.y));
                            }
                        }

                        map3d.on('contextmenu', handle3DContextMenu);
                        map3d.on('contextmenu', 'nodes-3d-hit-target', handle3DContextMenu);
                        map3d.on('contextmenu', 'nodes-3d-clients', handle3DContextMenu);
                        map3d.on('contextmenu', 'nodes-3d-room-servers', handle3DContextMenu);
                        map3d.on('contextmenu', 'nodes-3d-repeaters', handle3DContextMenu);

                        map3d.on('mousemove', function(e) {
                            var hit = get3DNodeAtPoint(e.point, 18);
                            map3d.getCanvas().style.cursor = hit ? 'pointer' : '';
                        });
                    } catch(le) {
                        console.error('[3D Map] Error initializing 3D layers:', le);
                    }

                    syncAllLayersTo3D();
                });
            } catch(e) {
                console.error('[3D Map] Failed initializing MapLibre GL:', e);
            }
        }

        var _3dNodesDataMap = {};
        var _3dNodesRaf = null;

        function getNode3DVisualProperties(node) {
            if (!node) return { color: '#06B6D4', opacity: 0.95, radius_scale: 1.0, is_orbital_beacon: false };
            var isLocal = !!node.is_local;
            var isPhantom = !!node.is_phantom;
            var isRoom = !!node.is_room_server;
            var isRep = !!node.is_repeater;
            var isFav = !!node.is_favorite;

            if (isLocal) {
                return { color: '#10B981', opacity: 1.0, radius_scale: 1.25, is_orbital_beacon: false };
            }

            // 1. Search Node ID
            if (window._searchNodeIdActive) {
                var q = (window._searchNodeQuery || '').trim().toLowerCase().replace(/^[!@]+/, '');
                var nid = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                var als = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                var matched = q ? (nid.indexOf(q) !== -1 || als.indexOf(q) !== -1) : true;
                if (matched) {
                    return { color: '#23A55A', opacity: 1.0, radius_scale: 1.4, is_orbital_beacon: false };
                } else {
                    return { color: '#4E5058', opacity: 0.18, radius_scale: 0.85, is_orbital_beacon: false };
                }
            }

            // 2. New Nodes Discovery
            if (window._newNodesActive) {
                var tfHours = window._newNodesTimeframeHours || 72;
                var maxAgeMs = tfHours * 3600 * 1000;
                var firstSeenStr = node.first_seen || '';
                var isNew = false;
                if (firstSeenStr) {
                    var parsed = Date.parse(firstSeenStr);
                    if (!isNaN(parsed) && parsed > 1704153600000 && (Date.now() - parsed) <= maxAgeMs) {
                        isNew = true;
                    }
                }
                if (isNew) {
                    return { color: '#FFD700', opacity: 1.0, radius_scale: 1.4, is_orbital_beacon: false };
                } else {
                    return { color: '#4E5058', opacity: 0.18, radius_scale: 0.85, is_orbital_beacon: false };
                }
            }

            // 3. MQTT Discovered Nodes
            if (window._mqttNodesActive) {
                var src = (node.source || '').toLowerCase().trim();
                var isMqtt = (src === 'mqtt' || !!node.is_mqtt);
                if (isMqtt && !isPhantom) {
                    return { color: '#F97316', opacity: 1.0, radius_scale: 1.4, is_orbital_beacon: false };
                } else {
                    return { color: '#4E5058', opacity: 0.18, radius_scale: 0.85, is_orbital_beacon: false };
                }
            }

            // 4. Node Activity Heatmap (Dedicated traffic analysis mode - takes priority over Path Modes and Scopes)
            if (activityHeatmapActive) {
                if (isRep || isRoom) {
                    var cleanId = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                    var cleanAlias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                    var actMap = activityHeatmapData || {};
                    var count = (actMap[cleanId] !== undefined) ? actMap[cleanId] : (actMap[cleanAlias] || 0);
                    if (count <= 0) {
                        return { color: '#4B5563', opacity: 0.20, radius_scale: 0.70, is_orbital_beacon: false };
                    } else {
                        var maxT = window._actMaxTraffic || 1;
                        var ratio = maxT > 0 ? (count / maxT) : 0;
                        var actColor = '#00D2FF';
                        if (window._actRelative) {
                            if (ratio >= 0.75) actColor = '#EF4444';
                            else if (ratio >= 0.50) actColor = '#FB923C';
                            else if (ratio >= 0.25) actColor = '#FACC15';
                            else if (ratio >= 0.10) actColor = '#10B981';
                            else actColor = '#00D2FF';
                        } else {
                            if (count > 10) actColor = '#EF4444';
                            else if (count > 5) actColor = '#FB923C';
                            else if (count > 2) actColor = '#FACC15';
                            else if (count > 1) actColor = '#10B981';
                            else actColor = '#00D2FF';
                        }
                        return { color: actColor, opacity: 1.0, radius_scale: 1.15 + (ratio * 0.50), is_orbital_beacon: false };
                    }
                } else {
                    return { color: '#4B5563', opacity: 0.18, radius_scale: 0.70, is_orbital_beacon: false };
                }
            }

            // 5. Path Modes
            if (pathModesActive) {
                var pLen = (node.out_path_len !== undefined && node.out_path_len !== null) ? Number(node.out_path_len) : -1;
                var pMode = (node.out_path_hash_mode !== undefined && node.out_path_hash_mode !== null) ? Number(node.out_path_hash_mode) : -1;
                if (window._pathMultihopOnly && (pMode <= 0 && pLen <= 0)) {
                    return { color: '#4E5058', opacity: 0.20, radius_scale: 0.80, is_orbital_beacon: false };
                }
                if (pMode >= 0) {
                    if (pMode === 0) return { color: '#EF4444', opacity: 1.0, radius_scale: 1.20, is_orbital_beacon: false };
                    if (pMode === 1) return { color: '#00D2FF', opacity: 1.0, radius_scale: 1.20, is_orbital_beacon: false };
                    return { color: '#00FF7F', opacity: 1.0, radius_scale: 1.20, is_orbital_beacon: false };
                } else if (pLen > 0) {
                    return { color: '#EF4444', opacity: 1.0, radius_scale: 1.20, is_orbital_beacon: false };
                } else {
                    return { color: '#6B7280', opacity: 0.70, radius_scale: 0.90, is_orbital_beacon: false };
                }
            }

            // 6. Scopes
            if (window._scopeOverlaysActive && isRep) {
                var scMeta = (window._scopeNodeMap && (window._scopeNodeMap[node.node_id] || (node.alias && window._scopeNodeMap[node.alias]))) ? (window._scopeNodeMap[node.node_id] || window._scopeNodeMap[node.alias]) : null;
                if (scMeta) {
                    var isFiltered = (window._activeScopeFilter !== 'all' && window._activeScopeFilter !== scMeta.scope_name);
                    if (isFiltered) {
                        return { color: scMeta.color || '#00E5FF', opacity: 0.15, radius_scale: 0.80, is_orbital_beacon: false };
                    } else {
                        return { color: scMeta.color || '#00E5FF', opacity: 1.0, radius_scale: (window._scopeHighlight ? 1.35 : 1.15), is_orbital_beacon: false };
                    }
                } else {
                    return { color: '#4E5058', opacity: (window._scopeHighlight ? 0.20 : 0.35), radius_scale: 0.80, is_orbital_beacon: false };
                }
            }

            // 7. Companion Orbitals
            var hasDocked = false;
            if (window._companionOrbitalsActive && isRep && window._dockedCompanionsData) {
                var dList = window._dockedCompanionsData[node.node_id] || window._dockedCompanionsData[node.alias];
                if (!dList && node.alias) {
                    dList = window._dockedCompanionsData['@' + node.alias] || window._dockedCompanionsData[node.alias.replace(/^@/, '')];
                }
                if (dList && dList.length > 0) hasDocked = true;
            }
            if (hasDocked) {
                return {
                    color: isFav ? '#AA55FF' : '#FFD335',
                    opacity: 1.0,
                    radius_scale: 1.30,
                    is_orbital_beacon: true
                };
            }

            // 8. Base colors
            if (isPhantom) return { color: '#64748B', opacity: 0.85, radius_scale: 0.90, is_orbital_beacon: false };
            if (isRoom) return { color: '#D946EF', opacity: 1.0, radius_scale: 1.35, is_orbital_beacon: false };
            if (isFav) return { color: '#AA55FF', opacity: 1.0, radius_scale: 1.15, is_orbital_beacon: false };
            if (isRep) return { color: '#3B82F6', opacity: 1.0, radius_scale: 1.10, is_orbital_beacon: false };
            return { color: '#06B6D4', opacity: 0.95, radius_scale: 1.0, is_orbital_beacon: false };
        }
        window.getNode3DVisualProperties = getNode3DVisualProperties;

        function getNodeHexColor(node) {
            if (!node) return '#06B6D4';
            if (node.is_phantom) return '#64748B';
            if (node.is_local) return '#10B981';
            if (node.is_room_server) return '#EC4899';
            if (node.is_favorite) return '#AA55FF';
            if (node.is_repeater) return '#3B82F6';
            if (window._newNodesActive) {
                var tfHours = window._newNodesTimeframeHours || 72;
                var maxAgeMs = tfHours * 3600 * 1000;
                var firstSeenStr = node.first_seen || '';
                if (firstSeenStr) {
                    var parsedFs = Date.parse(firstSeenStr);
                    if (!isNaN(parsedFs) && parsedFs > 1704153600000 && (Date.now() - parsedFs) <= maxAgeMs) {
                        return '#FBBF24';
                    }
                }
            }
            var src = (node.source || '').toLowerCase().trim();
            if (src === 'mqtt' || !!node.is_mqtt) return '#FB923C';
            return '#06B6D4';
        }

        function flushNodesTo3D() {
            if (!map3d || !map3d.getSource('nodes-3d')) return;
            var features = [];
            for (var nid in _3dNodesDataMap) {
                var node = _3dNodesDataMap[nid];
                if (!node || node.lat == null || node.lon == null) continue;
                var vProps = getNode3DVisualProperties(node);
                features.push({
                    type: 'Feature',
                    properties: {
                        node_id: node.node_id,
                        alias: node.alias || node.node_id,
                        is_repeater: !!node.is_repeater,
                        is_favorite: !!node.is_favorite,
                        is_room_server: !!node.is_room_server,
                        is_phantom: !!node.is_phantom,
                        is_local: !!node.is_local,
                        is_orbital_beacon: !!vProps.is_orbital_beacon,
                        color: vProps.color,
                        opacity: vProps.opacity,
                        radius_scale: vProps.radius_scale,
                        node_json: JSON.stringify(node)
                    },
                    geometry: {
                        type: 'Point',
                        coordinates: [Number(node.lon), Number(node.lat)]
                    }
                });
            }
            map3d.getSource('nodes-3d').setData({
                type: 'FeatureCollection',
                features: features
            });
        }

        function syncAllNodesTo3D() {
            if (!map3d) return;
            _3dNodesDataMap = {};
            for (var nid in markers) {
                var m = markers[nid];
                if (m && m._nodeData) {
                    _3dNodesDataMap[nid] = m._nodeData;
                }
            }
            flushNodesTo3D();
        }
        window.syncAllNodesTo3D = syncAllNodesTo3D;

        function addOrUpdate3DNode(node) {
            if (!map3d || !node || node.lat == null || node.lon == null) return;
            _3dNodesDataMap[node.node_id] = node;
            if (!_3dNodesRaf) {
                _3dNodesRaf = requestAnimationFrame(function() {
                    _3dNodesRaf = null;
                    flushNodesTo3D();
                });
            }
        }
        window.addOrUpdate3DNode = addOrUpdate3DNode;

        function style3DDot(dot, node) {
            var isPhantom = !!node.is_phantom;
            var isLocal = !!node.is_local;
            var isRoom = !!node.is_room_server;
            var isFav = !!node.is_favorite;
            var isRep = !!node.is_repeater;
            var src = (node.source || '').toLowerCase().trim();
            var isMqtt = (src === 'mqtt' || !!node.is_mqtt);

            var dotClass = 'node-dot ';
            if (isPhantom) dotClass += 'node-dot-phantom';
            else if (isLocal) dotClass += 'node-dot-local';
            else if (isRoom) dotClass += 'node-dot-room';
            else if (isFav) dotClass += 'node-dot-favorite' + (isRep ? ' node-dot-repeater' : '');
            else if (isRep) dotClass += 'node-dot-repeater';
            else dotClass += 'node-dot-companion';

            if (window._companionOrbitalsActive && isRep) {
                dotClass += ' orbital-ring-repeater';
                if (isFav) dotClass += ' orbital-ring-repeater-fav';
            }

            if (window._newNodesActive) {
                var tfHours = window._newNodesTimeframeHours || 72;
                var maxAgeMs = tfHours * 3600 * 1000;
                var firstSeenStr = node.first_seen || '';
                var isNew = false;
                var baselineCutoff = 1704153600000;
                if (firstSeenStr) {
                    try {
                        var parsed = Date.parse(firstSeenStr);
                        if (!isNaN(parsed) && parsed > baselineCutoff) {
                            var ageMs = Date.now() - parsed;
                            if (ageMs >= 0 && ageMs <= maxAgeMs) {
                                isNew = true;
                            }
                        }
                    } catch(e) {}
                }
                if (isNew) {
                    dot.style.opacity = '1.0';
                    dot.style.setProperty('background', '#FFD700', 'important');
                    dot.style.setProperty('background-color', '#FFD700', 'important');
                    dot.style.setProperty('border', '2px solid #FFFFFF', 'important');
                    dot.style.setProperty('box-shadow', '0 0 16px rgba(255, 215, 0, 0.95), 0 0 6px #FFFFFF', 'important');
                    dot.style.transform = 'scale(1.4)';
                } else {
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background', '#4E5058', 'important');
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                    dot.style.removeProperty('border');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                    dot.style.transform = 'scale(0.85)';
                }
                dot.className = dotClass;
                return;
            }

            if (window._mqttNodesActive) {
                if (isMqtt && !isLocal && !isPhantom) {
                    dotClass += ' node-3d-mqtt';
                    dot.style.setProperty('background', '#F97316', 'important');
                    dot.style.setProperty('background-color', '#F97316', 'important');
                    dot.style.setProperty('border', '1.5px solid #FFFFFF', 'important');
                    dot.style.setProperty('box-shadow', '0 0 16px rgba(249, 115, 22, 0.95), 0 0 6px #FFFFFF', 'important');
                    dot.style.opacity = '1.0';
                } else {
                    dot.style.opacity = '0.18';
                    dot.style.setProperty('background', '#4E5058', 'important');
                    dot.style.setProperty('background-color', '#4E5058', 'important');
                }
            } else {
                dot.style.removeProperty('background');
                dot.style.removeProperty('background-color');
                dot.style.removeProperty('border');
                dot.style.removeProperty('box-shadow');
                dot.style.opacity = '1.0';
            }
            dot.className = dotClass;
        }

        function generate3DArcPoints(lng1, lat1, lng2, lat2, steps) {
            steps = steps || 64;
            var dLng = (lng2 - lng1) * Math.cos((lat1 + lat2) * Math.PI / 360);
            var dLat = lat2 - lat1;
            var distKm = Math.sqrt(dLng * dLng + dLat * dLat) * 111.0;

            var terrainExag = 2.5;
            if (map3d && map3d.getTerrain) {
                var terr = map3d.getTerrain();
                if (terr && typeof terr.exaggeration === 'number') {
                    terrainExag = terr.exaggeration;
                }
            }

            var elev1 = 0, elev2 = 0;
            if (map3d && map3d.queryTerrainElevation) {
                try {
                    elev1 = Math.max(0, map3d.queryTerrainElevation([lng1, lat1]) || 0);
                    elev2 = Math.max(0, map3d.queryTerrainElevation([lng2, lat2]) || 0);
                } catch(e) {}
            }
            var renderedElev1 = elev1 * terrainExag;
            var renderedElev2 = elev2 * terrainExag;

            // Sample intermediate points to find maximum rendered terrain elevation along the trajectory
            var maxInterElev = Math.max(renderedElev1, renderedElev2);
            var sampleSteps = Math.min(24, steps);
            for (var s = 1; s < sampleSteps; s++) {
                var st = s / sampleSteps;
                var slng = lng1 + (lng2 - lng1) * st;
                var slat = lat1 + (lat2 - lat1) * st;
                if (map3d && map3d.queryTerrainElevation) {
                    try {
                        var sElev = Math.max(0, map3d.queryTerrainElevation([slng, slat]) || 0) * terrainExag;
                        if (sElev > maxInterElev) maxInterElev = sElev;
                    } catch(e) {}
                }
            }

            // Apex altitude must clear the highest mountain peak between endpoints + tropospheric curve
            var apexClearance = Math.min(3500, Math.max(120, distKm * 40));
            var peakApexAlt = maxInterElev + apexClearance;

            var points = [];
            for (var i = 0; i <= steps; i++) {
                var t = i / steps;
                var curLng = lng1 + (lng2 - lng1) * t;
                var curLat = lat1 + (lat2 - lat1) * t;
                var localGround = 0;
                if (map3d && map3d.queryTerrainElevation) {
                    try {
                        localGround = Math.max(0, map3d.queryTerrainElevation([curLng, curLat]) || 0) * terrainExag;
                    } catch(e) {}
                }

                // Smooth linear baseline between endpoints
                var linearBase = renderedElev1 * (1.0 - t) + renderedElev2 * t;
                // Parabolic arc dome
                var parabola = 4.0 * t * (1.0 - t);
                // Total altitude guaranteed to clear both the parabolic curve AND local mountain ridges by at least 70m
                var arcAlt = linearBase + (peakApexAlt - Math.min(renderedElev1, renderedElev2)) * parabola;
                var totalAlt = Math.max(localGround + 70, arcAlt);

                var mc = maplibregl.MercatorCoordinate.fromLngLat([curLng, curLat], totalAlt);
                points.push([mc.x, mc.y, mc.z]);
            }
            return points;
        }
        window.generate3DArcPoints = generate3DArcPoints;

        function generate3DArcCoordinates(start, end, steps) {
            // Backward-compatible signature accepting [lng, lat]
            var lng1 = start[0], lat1 = start[1];
            var lng2 = end[0], lat2 = end[1];
            return generate3DArcPoints(lng1, lat1, lng2, lat2, steps);
        }
        window.generate3DArcCoordinates = generate3DArcCoordinates;

        function trigger3DPulse(lng, lat, colorRgb) {
            if (!map3d) return;
            if (map3d.getLayer && map3d.getLayer('corescope-3d-arcs')) {
                try { map3d.moveLayer('corescope-3d-arcs'); } catch(e) {}
            }
            var terrainExag = 2.5;
            if (map3d && map3d.getTerrain) {
                var terr = map3d.getTerrain();
                if (terr && typeof terr.exaggeration === 'number') {
                    terrainExag = terr.exaggeration;
                }
            }
            var elev = 0;
            if (map3d.queryTerrainElevation) {
                try { elev = Math.max(0, map3d.queryTerrainElevation([lng, lat]) || 0); } catch(e) {}
            }
            var mc = maplibregl.MercatorCoordinate.fromLngLat([lng, lat], elev * terrainExag + 10);
            active3DPulses.push({
                pos: [mc.x, mc.y, mc.z],
                lng: lng,
                lat: lat,
                elev: elev * terrainExag + 10,
                color: colorRgb || [0.2, 0.8, 1.0],
                startTime: performance.now(),
                duration: 950
            });
            if (map3d) map3d.triggerRepaint();
        }

        function tracePacketPath3D(meta, coords) {
            // Flexible argument order support: (meta, coords) or (coords, meta)
            if (Array.isArray(meta) && (!coords || !Array.isArray(coords))) {
                var tmp = meta;
                meta = coords || {};
                coords = tmp;
            }
            if (!map3d || !coords || coords.length < 2) return;
            meta = meta || {};
            if (map3d.getLayer && map3d.getLayer('corescope-3d-arcs')) {
                try { map3d.moveLayer('corescope-3d-arcs'); } catch(e) {}
            }

            var pType = (meta.payload_type || meta.route_type || 'FLOOD').toUpperCase();
            var isMqtt = meta && (meta.source === 'mqtt' || (meta.packet_id && meta.packet_id.indexOf('mqtt-') === 0));

            // Legend color mapping:
            // green is advert, blue is group text message, gold is direct, purple is data request, red is route trace
            var colorHex = '#3B82F6'; // Default Blue (Group text message)
            if (isMqtt) {
                colorHex = '#F97316'; // Ingested MQTT
            } else if (pType === 'ADVERT') {
                colorHex = '#22C55E'; // Green (Advert)
            } else if (pType === 'GRP_TXT' || pType === 'FLOOD') {
                colorHex = '#3B82F6'; // Blue (Group text)
            } else if (pType === 'TXT_MSG' || pType === 'DIRECT') {
                colorHex = '#F59E0B'; // Gold (Direct text)
            } else if (pType === 'REQ' || pType === 'GRP_DATA' || pType === 'ANON_REQ') {
                colorHex = '#A855F7'; // Purple (Data request)
            } else if (pType === 'TRACE' || pType === 'PATH') {
                colorHex = '#EF4444'; // Red (Route trace)
            } else if (TYPE_COLORS[pType]) {
                colorHex = TYPE_COLORS[pType];
            } else if (meta.color) {
                colorHex = meta.color;
            }

            // Default packet-level style fallback:
            // solid is verified rf hop, dashes is ambiguous hop (collision), dotted is inferred step (no gps / mqtt)
            var lineStyle = 'solid';
            if (meta.is_ambiguous) {
                lineStyle = 'dashed';
            } else if (meta.is_inferred || meta.is_no_gps || meta.is_unknown || meta.is_phantom || isMqtt) {
                lineStyle = 'dotted';
            }

            var rgb = hexToRgb(colorHex);
            var normColor = [rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0];

            // Tactical origin radar pulse on terrain
            trigger3DPulse(coords[0][1], coords[0][0], normColor);

            // Sequential CoreScope hop animation across 3D terrain:
            // Shows trail ONLY between the last node and the next node.
            // When a node receives a message, it pulses with hollow radar ring.
            function runHop3D(hopIdx) {
                if (hopIdx >= coords.length - 1) {
                    return;
                }

                var p1 = coords[hopIdx];
                var p2 = coords[hopIdx + 1];
                var curvePts = generate3DArcPoints(p1[1], p1[0], p2[1], p2[0], 64);

                // Resolve style per hop segment if hop_metas is available:
                var hMeta = (meta.hop_metas && meta.hop_metas[hopIdx]) ? meta.hop_metas[hopIdx] : meta;
                var hopStyle = 'solid';
                if (hMeta.is_ambiguous) {
                    hopStyle = 'dashed';
                } else if (hMeta.is_inferred || hMeta.is_no_gps || hMeta.is_unknown || hMeta.is_phantom) {
                    hopStyle = 'dotted';
                } else if (isMqtt && coords.length <= 2) {
                    hopStyle = 'dotted';
                } else {
                    hopStyle = lineStyle;
                }

                active3DBeams.push({
                    curve: curvePts,
                    color: normColor,
                    style: hopStyle,
                    startTime: performance.now(),
                    duration: 520,
                    hopIdx: hopIdx,
                    onComplete: function() {
                        // Receiving node triggers radar pulse effect upon receiving packet
                        trigger3DPulse(p2[1], p2[0], normColor);
                        // Progress to next hop (trail only between last and next node, no lingering past trails)
                        runHop3D(hopIdx + 1);
                    }
                });
                if (map3d) map3d.triggerRepaint();
            }

            runHop3D(0);
        }
        window.tracePacketPath3D = tracePacketPath3D;

        // --- 3D Map View Layer Synchronizers ---
        function syncScopesTo3D() {
            if (!map3d || !map3d.getSource('scopes-3d')) return;
            if (!window._scopeOverlaysActive || !window._scopeData) {
                map3d.getSource('scopes-3d').setData({ type: 'FeatureCollection', features: [] });
                return;
            }

            var features = [];
            for (var sKey in window._scopeData) {
                var sInfo = window._scopeData[sKey];
                var sColor = sInfo.color || '#00E5FF';
                var sNodes = sInfo.nodes || [];

                var isSelected = (window._activeScopeFilter === 'all' || window._activeScopeFilter === sKey);
                if (!isSelected && window._activeScopeFilter !== 'all') continue;

                var pts = [];
                for (var i = 0; i < sNodes.length; i++) {
                    if (sNodes[i].latitude && sNodes[i].longitude) {
                        pts.push([sNodes[i].latitude, sNodes[i].longitude]);
                    }
                }
                if (pts.length === 0) continue;

                var fillOp = isSelected ? (window._scopeHighlight ? 0.35 : 0.15) : 0.04;
                var weight = isSelected ? (window._scopeHighlight ? 3.5 : 2.0) : 1.0;

                if (pts.length === 1) {
                    var cLat = pts[0][0], cLon = pts[0][1];
                    var ring = [];
                    var dDegLat = 20.0 / 111.0;
                    var dDegLon = 20.0 / (111.0 * Math.cos(cLat * Math.PI / 180.0));
                    for (var a = 0; a <= 36; a++) {
                        var rad = (a * 10) * Math.PI / 180.0;
                        ring.push([cLon + dDegLon * Math.cos(rad), cLat + dDegLat * Math.sin(rad)]);
                    }
                    features.push({
                        type: 'Feature',
                        properties: { scope: sKey, color: sColor, fillOpacity: fillOp, weight: weight },
                        geometry: { type: 'Polygon', coordinates: [ring] }
                    });
                } else if (pts.length === 2) {
                    var lat1 = pts[0][0], lon1 = pts[0][1];
                    var lat2 = pts[1][0], lon2 = pts[1][1];
                    var dLat = (lat2 - lat1), dLon = (lon2 - lon1);
                    var len = Math.sqrt(dLat*dLat + dLon*dLon) || 1;
                    var nLat = -dLon / len * 0.12;
                    var nLon = dLat / len * 0.12;
                    var ring = [
                        [lon1 + nLon, lat1 + nLat],
                        [lon2 + nLon, lat2 + nLat],
                        [lon2 - nLon, lat2 - nLat],
                        [lon1 - nLon, lat1 - nLat],
                        [lon1 + nLon, lat1 + nLat]
                    ];
                    features.push({
                        type: 'Feature',
                        properties: { scope: sKey, color: sColor, fillOpacity: fillOp, weight: weight },
                        geometry: { type: 'Polygon', coordinates: [ring] }
                    });
                } else {
                    var hullPts = computeConvexHull(pts);
                    if (hullPts.length >= 3) {
                        var exp = expandPolygonOutward(hullPts, 0.08);
                        var ring = exp.map(function(p) { return [p[1], p[0]]; });
                        ring.push(ring[0]);
                        features.push({
                            type: 'Feature',
                            properties: { scope: sKey, color: sColor, fillOpacity: fillOp, weight: weight },
                            geometry: { type: 'Polygon', coordinates: [ring] }
                        });
                    }
                }
            }

            map3d.getSource('scopes-3d').setData({
                type: 'FeatureCollection',
                features: features
            });
        }
        window.syncScopesTo3D = syncScopesTo3D;

        function syncHeatmapTo3D() {
            if (!map3d || !map3d.getSource('heatmap-3d')) return;
            if (!activityHeatmapActive) {
                map3d.getSource('heatmap-3d').setData({ type: 'FeatureCollection', features: [] });
                return;
            }
            var features = [];
            var maxVal = window._actMaxTraffic || 1;
            var actMap = activityHeatmapData || {};
            for (var nid in markers) {
                var m = markers[nid];
                if (!m || !m._nodeData || m._nodeData.lat == null || m._nodeData.lon == null) continue;
                var nd = m._nodeData;
                var cleanId = (nd.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                var cleanAlias = (nd.alias || '').toLowerCase().replace(/^[!@]+/, '');
                var act = (actMap[cleanId] !== undefined) ? actMap[cleanId] : (actMap[cleanAlias] || 0);
                if (act <= 0) continue;
                var normWeight = Math.min(1.0, Math.max(0.15, act / maxVal));
                features.push({
                    type: 'Feature',
                    properties: { weight: normWeight },
                    geometry: {
                        type: 'Point',
                        coordinates: [nd.lon, nd.lat]
                    }
                });
            }
            map3d.getSource('heatmap-3d').setData({
                type: 'FeatureCollection',
                features: features
            });
        }
        window.syncHeatmapTo3D = syncHeatmapTo3D;

        function syncThunderstormTo3D() {
            if (!map3d) return;
            // 1. Radar raster layer
            try {
                if (!thunderstormActive || !window._radarMeta) {
                    if (map3d.getLayer('rainviewer-3d-layer')) map3d.removeLayer('rainviewer-3d-layer');
                    if (map3d.getSource('rainviewer-3d')) map3d.removeSource('rainviewer-3d');
                } else {
                    var frames = window._radarMeta.frames || [];
                    var framePath = window._radarMeta.path || '';
                    if (frames.length > 0 && window._radarFrameIdx >= 0 && window._radarFrameIdx < frames.length) {
                        framePath = frames[window._radarFrameIdx].path;
                    }
                    if (framePath) {
                        var tileUrl = window._radarMeta.host + framePath + '/256/{z}/{x}/{y}/2/1_1.png';
                        if (map3d.getSource('rainviewer-3d')) {
                            if (map3d.getLayer('rainviewer-3d-layer')) map3d.removeLayer('rainviewer-3d-layer');
                            map3d.removeSource('rainviewer-3d');
                        }
                        map3d.addSource('rainviewer-3d', {
                            type: 'raster',
                            tiles: [tileUrl],
                            tileSize: 256,
                            maxzoom: 7
                        });
                        map3d.addLayer({
                            id: 'rainviewer-3d-layer',
                            type: 'raster',
                            source: 'rainviewer-3d',
                            paint: { 'raster-opacity': 0.65 }
                        });
                    }
                }
            } catch(e) {
                console.error('[3D Map] syncThunderstormTo3D radar error:', e);
            }

            // 2. Lightning strikes
            try {
                if (!map3d.getSource('lightning-3d')) return;
                if (!thunderstormActive || !lightningStrikesList || lightningStrikesList.length === 0) {
                    map3d.getSource('lightning-3d').setData({ type: 'FeatureCollection', features: [] });
                    return;
                }
                var now = Date.now();
                var lFeatures = [];
                for (var i = 0; i < lightningStrikesList.length; i++) {
                    var s = lightningStrikesList[i];
                    var ageSec = (now - s.time) / 1000.0;
                    if (ageSec > 1200) continue;
                    var lColor = ageSec < 120 ? '#FDE047' : (ageSec < 600 ? '#FB923C' : '#EF4444');
                    lFeatures.push({
                        type: 'Feature',
                        properties: { color: lColor, age: ageSec },
                        geometry: {
                            type: 'Point',
                            coordinates: [s.lon, s.lat]
                        }
                    });
                }
                map3d.getSource('lightning-3d').setData({
                    type: 'FeatureCollection',
                    features: lFeatures
                });
            } catch(e) {
                console.error('[3D Map] syncThunderstormTo3D lightning error:', e);
            }
        }
        window.syncThunderstormTo3D = syncThunderstormTo3D;

        function makeCirclePolygon3D(cLon, cLat, radiusMeters, steps) {
            steps = steps || 48;
            var ring = [];
            var dLat = radiusMeters / 111320.0;
            var dLon = radiusMeters / (111320.0 * Math.cos(cLat * Math.PI / 180.0));
            for (var i = 0; i <= steps; i++) {
                var rad = (i * 2.0 * Math.PI) / steps;
                ring.push([cLon + dLon * Math.sin(rad), cLat + dLat * Math.cos(rad)]);
            }
            return [ring];
        }

        function makeHollowCirclePolygon3D(cLon, cLat, radiusMeters, wallThicknessMeters, steps) {
            steps = steps || 64;
            wallThicknessMeters = wallThicknessMeters || Math.max(800, radiusMeters * 0.015);
            var innerRadius = Math.max(10, radiusMeters - wallThicknessMeters);
            var outerRing = [];
            var innerRing = [];
            var dLatOut = radiusMeters / 111320.0;
            var dLonOut = radiusMeters / (111320.0 * Math.cos(cLat * Math.PI / 180.0));
            var dLatIn = innerRadius / 111320.0;
            var dLonIn = innerRadius / (111320.0 * Math.cos(cLat * Math.PI / 180.0));
            for (var i = 0; i <= steps; i++) {
                var rad = (i * 2.0 * Math.PI) / steps;
                var sinR = Math.sin(rad);
                var cosR = Math.cos(rad);
                outerRing.push([cLon + dLonOut * sinR, cLat + dLatOut * cosR]);
                innerRing.push([cLon + dLonIn * sinR, cLat + dLatIn * cosR]);
            }
            // GeoJSON Polygon with hole: outer ring followed by inner ring in reverse winding
            return [outerRing, innerRing.reverse()];
        }

        function getCircle3DPoints(cLon, cLat, radiusMeters, altM, steps) {
            steps = steps || 64;
            var pts = [];
            var dLat = radiusMeters / 111320.0;
            var dLon = radiusMeters / (111320.0 * Math.cos(cLat * Math.PI / 180.0));
            for (var i = 0; i <= steps; i++) {
                var rad = (i * 2.0 * Math.PI) / steps;
                var lon = cLon + dLon * Math.sin(rad);
                var lat = cLat + dLat * Math.cos(rad);
                var mc = maplibregl.MercatorCoordinate.fromLngLat([lon, lat], altM);
                pts.push([mc.x, mc.y, mc.z]);
            }
            return pts;
        }

        function syncAdsbTo3D() {
            if (!map3d) return;
            var floatLayer = document.getElementById('adsb-3d-floating-layer');
            if (!adsbActive) {
                if (map3d.getSource('adsb-3d-trails')) {
                    map3d.getSource('adsb-3d-trails').setData({ type: 'FeatureCollection', features: [] });
                }
                if (map3d.getSource('adsb-3d-cylinder')) {
                    map3d.getSource('adsb-3d-cylinder').setData({ type: 'FeatureCollection', features: [] });
                }
                if (map3d.getSource('adsb-3d-rings')) {
                    map3d.getSource('adsb-3d-rings').setData({ type: 'FeatureCollection', features: [] });
                }
                for (var hx in map3dAdsbMarkers) {
                    map3dAdsbMarkers[hx].remove();
                }
                map3dAdsbMarkers = {};
                if (floatLayer) floatLayer.innerHTML = '';
                active3DPlanes = [];
                window._lastAdsbCylinderMeta = null;
                if (window._map3dAdsbPopup) {
                    window._map3dAdsbPopup.remove();
                    window._map3dAdsbPopup = null;
                }
                if (window._map3dAdsbCenterMarker) {
                    window._map3dAdsbCenterMarker.remove();
                    window._map3dAdsbCenterMarker = null;
                }
                return;
            }

            // 1. Flight Trails in 3D (Rendered directly in WebGL airspace via corescope3DLayer attached to aircraft)
            if (map3d.getSource('adsb-3d-trails')) {
                map3d.getSource('adsb-3d-trails').setData({
                    type: 'FeatureCollection',
                    features: []
                });
            }

            // 2. Multi-tier Vertical 3D Airspace Cylinder & Range Rings
            var target = window._lastAdsbTarget;
            var terrainExag = 2.5;
            if (map3d && map3d.getTerrain) {
                var terr = map3d.getTerrain();
                if (terr && typeof terr.exaggeration === 'number') {
                    terrainExag = terr.exaggeration;
                }
            }

            if (target && typeof target.lat === 'number' && typeof target.lon === 'number') {
                var maxNm = target.radius_nm || 50;
                var cylRadiusM = maxNm * 1852.0;
                var cylHollowPoly = makeHollowCirclePolygon3D(target.lon, target.lat, cylRadiusM, 1500, 64);

                var targetGround = 0;
                if (map3d.queryTerrainElevation) {
                    try { targetGround = Math.max(0, map3d.queryTerrainElevation([target.lon, target.lat]) || 0) * terrainExag; } catch(e) {}
                }

                var tierH0 = targetGround;
                var tierH1 = targetGround + 3000;   // 2k ft divider
                var tierH2 = targetGround + 10500;  // 7k ft divider
                var tierH3 = targetGround + 36000;  // 25k ft divider
                var tierH4 = targetGround + 62000;  // 25k+ ft top rim

                // Remove colored extruded cylinder walls totally, keep metadata for delicate grey dotted altitude wireframe rings
                window._lastAdsbCylinderMeta = {
                    lon: target.lon,
                    lat: target.lat,
                    radiusM: cylRadiusM,
                    heights: [tierH0, tierH1, tierH2, tierH3],
                    tiers: [
                        { tier: '<2k ft', color: '#EF4444' },
                        { tier: '2k-7k ft', color: '#D946EF' },
                        { tier: '10k-25k ft', color: '#8B5CF6' },
                        { tier: '>25k ft', color: '#7DD3FC' }
                    ]
                };

                if (map3d.getSource('adsb-3d-cylinder')) {
                    map3d.getSource('adsb-3d-cylinder').setData({
                        type: 'FeatureCollection',
                        features: []
                    });
                }

                var ringsNm = [10, 25, 50];
                var ringFeatures = [];
                var hasMaxRing = false;
                for (var rIdx = 0; rIdx < ringsNm.length; rIdx++) {
                    var dNm = ringsNm[rIdx];
                    if (dNm <= maxNm) {
                        var isMax = Math.abs(dNm - maxNm) < 0.1;
                        if (isMax) hasMaxRing = true;
                        var rPoly = makeCirclePolygon3D(target.lon, target.lat, dNm * 1852.0, 64);
                        ringFeatures.push({
                            type: 'Feature',
                            properties: {
                                color: isMax ? '#94A3B8' : '#64748B',
                                width: isMax ? 1.4 : 1.0
                            },
                            geometry: { type: 'LineString', coordinates: rPoly[0] }
                        });
                    }
                }
                if (!hasMaxRing) {
                    var maxPoly = makeCirclePolygon3D(target.lon, target.lat, maxNm * 1852.0, 64);
                    ringFeatures.push({
                        type: 'Feature',
                        properties: {
                            color: '#94A3B8',
                            width: 1.4
                        },
                        geometry: { type: 'LineString', coordinates: maxPoly[0] }
                    });
                }

                if (map3d.getSource('adsb-3d-rings')) {
                    map3d.getSource('adsb-3d-rings').setData({
                        type: 'FeatureCollection',
                        features: ringFeatures
                    });
                }

                if (!window._map3dAdsbCenterMarker) {
                    var cEl = document.createElement('div');
                    cEl.className = 'adsb-radar-center-wrap';
                    cEl.innerHTML = '<div class="adsb-radar-center-beacon"><div class="adsb-radar-center-pulse"></div><div class="adsb-radar-center-dot">📡</div></div>';
                    window._map3dAdsbCenterMarker = new maplibregl.Marker({ element: cEl, anchor: 'center' })
                        .setLngLat([target.lon, target.lat])
                        .addTo(map3d);
                } else {
                    window._map3dAdsbCenterMarker.setLngLat([target.lon, target.lat]);
                }
            }

            // 3. Aircraft Displayed in 3D Space (Vertical Drop Lines, Ground Footprints, & Altitude Screen Projections)
            active3DPlanes = [];
            var activeHexes = {};
            if (!floatLayer) {
                var c3d = document.getElementById('map-3d');
                if (c3d) {
                    floatLayer = document.createElement('div');
                    floatLayer.id = 'adsb-3d-floating-layer';
                    floatLayer.style.position = 'absolute';
                    floatLayer.style.top = '0';
                    floatLayer.style.left = '0';
                    floatLayer.style.width = '100%';
                    floatLayer.style.height = '100%';
                    floatLayer.style.pointerEvents = 'none';
                    floatLayer.style.overflow = 'hidden';
                    floatLayer.style.zIndex = '5';
                    c3d.appendChild(floatLayer);
                }
            }

            for (var mHex in aircraftMarkers) {
                var marker2d = aircraftMarkers[mHex];
                var ac = (marker2d && marker2d._planeData) ? marker2d._planeData : (currentAircraftData ? currentAircraftData[mHex] : null);
                if (!ac || ac.lat == null || ac.lon == null) continue;
                if (typeof window.isAircraftCategoryVisible === 'function' && !window.isAircraftCategoryVisible(ac.category || 'general')) {
                    var old3dEl = document.getElementById('adsb-plane-3d-' + mHex);
                    if (old3dEl) old3dEl.style.display = 'none';
                    continue;
                }
                activeHexes[mHex] = true;

                var rawElev = 0;
                if (map3d.queryTerrainElevation) {
                    try { rawElev = Math.max(0, map3d.queryTerrainElevation([ac.lon, ac.lat]) || 0); } catch(e) {}
                }
                var groundElev = rawElev * terrainExag;
                var altFt = ac.alt_baro || ac.alt_geom || 1000;
                var rawAltM = Math.max(60, altFt * 0.3048);
                // Scaling 3D altitude above terrain so aircraft float with distinct vertical separation in 3D perspective
                var displayAltM = groundElev + Math.max(300, (rawAltM / 10000.0) * 46000.0);

                var planeColor = (typeof getAircraftColor === 'function') ? getAircraftColor(ac) : '#06B6D4';
                var track = ac.track || 0;
                var callsign = ac.flight || ac.hex || 'AC';
                var altFormatted = (altFt >= 10000 ? 'FL' + Math.round(altFt / 100) : altFt.toLocaleString() + 'ft');

                active3DPlanes.push({
                    hex: mHex,
                    lon: ac.lon,
                    lat: ac.lat,
                    altM: displayAltM,
                    groundElev: groundElev,
                    altFt: altFt,
                    callsign: callsign,
                    track: track,
                    color: planeColor,
                    planeData: ac
                });

                // Update or create DOM element inside floating layer
                var el = document.getElementById('adsb-plane-3d-' + mHex);
                if (!el && floatLayer) {
                    el = document.createElement('div');
                    el.id = 'adsb-plane-3d-' + mHex;
                    el.className = 'adsb-floating-3d-node';
                    el.style.position = 'absolute';
                    el.style.pointerEvents = 'auto';
                    el.style.cursor = 'pointer';
                    el.style.display = 'none';
                    el.style.transformOrigin = 'center center';
                    el.style.zIndex = '10';

                    (function(curHex) {
                        el.addEventListener('click', function(ev) {
                            ev.stopPropagation();
                            window.openAircraftTooltip(curHex, aircraftMarkers[curHex], true);
                        });
                    })(mHex);

                    floatLayer.appendChild(el);
                }

                if (el) {
                    var curPitch = (map3d && map3d.getPitch) ? map3d.getPitch() : 0;
                    var curBearing = (map3d && map3d.getBearing) ? map3d.getBearing() : 0;
                    var relTrack = track - curBearing;
                    el.innerHTML = '<div style="position:relative; width:0; height:0; pointer-events:none;">' +
                        '<div class="adsb-plane-svg-glyph" style="position:absolute; left:0; top:0; margin-left:-13px; margin-top:-13px; transform: perspective(800px) rotateX(' + curPitch.toFixed(1) + 'deg) rotateZ(' + relTrack.toFixed(1) + 'deg); transform-style:preserve-3d; display:inline-block; pointer-events:auto; filter:drop-shadow(0 0 6px ' + planeColor + ');">' +
                        '<svg width="26" height="26" viewBox="0 0 24 24" style="display:block;">' +
                        '<path fill="' + planeColor + '" stroke="#0F172A" stroke-width="0.8" d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>' +
                        '</svg></div>' +
                        '<div style="position:absolute; left:18px; top:-12px; background:rgba(15,23,42,0.92); border:1px solid ' + planeColor + '; border-radius:3px; padding:1px 5px; font-size:9px; color:#F8FAFC; white-space:nowrap; font-weight:bold; box-shadow:0 2px 8px rgba(0,0,0,0.85); pointer-events:auto;">' +
                        callsign.trim() + ' <span style="color:#38BDF8; font-weight:normal;">' + altFormatted + '</span>' +
                        '</div></div>';
                }
            }

            // Remove retired planes from floating layer
            if (floatLayer) {
                var children = floatLayer.querySelectorAll('.adsb-floating-3d-node');
                for (var ci = 0; ci < children.length; ci++) {
                    var ch = children[ci];
                    var chHex = ch.id.replace('adsb-plane-3d-', '');
                    if (!activeHexes[chHex]) {
                        ch.remove();
                    }
                }
            }

            if (map3d) map3d.triggerRepaint();
        }
        window.syncAdsbTo3D = syncAdsbTo3D;

        function syncTropoTo3D() {
            if (!map3d || !map3d.getSource('tropo-3d')) return;
            if (!window._lastTropoGeojson) {
                map3d.getSource('tropo-3d').setData({ type: 'FeatureCollection', features: [] });
                return;
            }
            map3d.getSource('tropo-3d').setData(window._lastTropoGeojson);
        }
        window.syncTropoTo3D = syncTropoTo3D;

        function syncSpaceWeatherTo3D() {
            if (!map3d || !map3d.getSource('aurora-3d')) return;
            if (!auroraActive || !window._lastAuroraGeojson) {
                map3d.getSource('aurora-3d').setData({ type: 'FeatureCollection', features: [] });
                return;
            }
            map3d.getSource('aurora-3d').setData(window._lastAuroraGeojson);
            if (map3d.getLayer('aurora-3d-fill')) {
                map3d.setPaintProperty('aurora-3d-fill', 'fill-opacity', currentAuroraOpacity * 0.65);
            }
        }
        window.syncSpaceWeatherTo3D = syncSpaceWeatherTo3D;

        function syncLosTo3D() {
            if (!map3d) return;
            try {
                if (map3d.getLayer('viewshed-3d-fill')) map3d.removeLayer('viewshed-3d-fill');
                if (map3d.getSource('viewshed-3d')) map3d.removeSource('viewshed-3d');
                if (map3d.getLayer('viewshed-img-3d-layer')) map3d.removeLayer('viewshed-img-3d-layer');
                if (map3d.getSource('viewshed-img-3d')) map3d.removeSource('viewshed-img-3d');

                if (!window._lastViewshedPayload) return;
                var payload = window._lastViewshedPayload;

                if (payload.image_data_url && payload.bounds) {
                    var b = payload.bounds;
                    var s = b[0][0], w = b[0][1], n = b[1][0], e = b[1][1];
                    map3d.addSource('viewshed-img-3d', {
                        type: 'image',
                        url: payload.image_data_url,
                        coordinates: [
                            [w, n],
                            [e, n],
                            [e, s],
                            [w, s]
                        ]
                    });
                    map3d.addLayer({
                        id: 'viewshed-img-3d-layer',
                        type: 'raster',
                        source: 'viewshed-img-3d',
                        paint: { 'raster-opacity': 0.78 }
                    });
                } else if (payload.geojson) {
                    map3d.addSource('viewshed-3d', { type: 'geojson', data: payload.geojson });
                    map3d.addLayer({
                        id: 'viewshed-3d-fill',
                        type: 'fill',
                        source: 'viewshed-3d',
                        paint: { 'fill-color': '#10B981', 'fill-opacity': 0.45 }
                    });
                }
            } catch(e) {
                console.error('[3D Map] syncLosTo3D error:', e);
            }
        }
        window.syncLosTo3D = syncLosTo3D;

        function syncOrbitalsTo3D() {
            if (!map3d) return;
            if (!window._companionOrbitalsActive || !window._dockedCompanionsData) {
                for (var oid in map3dOrbitalMarkers) {
                    map3dOrbitalMarkers[oid].remove();
                }
                map3dOrbitalMarkers = {};
                for (var mid in map3dMarkers) {
                    var mObj = map3dMarkers[mid];
                    var mEl = mObj ? mObj.getElement() : null;
                    var mDot = mEl ? mEl.querySelector('.node-dot') : null;
                    if (mDot) {
                        mDot.classList.remove('orbital-ring-repeater');
                        mDot.classList.remove('orbital-ring-repeater-fav');
                    }
                }
                return;
            }

            var dockedMap = window._dockedCompanionsData;
            var currentZoom = map3d.getZoom ? map3d.getZoom() : 10;
            var showSatellites = (currentZoom >= 9.5); // threshold formerly currentZoom >= 13.5
            var activeOrbIds = {};

            for (var id in markers) {
                var marker = markers[id];
                var node = marker ? marker._nodeData : null;
                if (!node || !node.is_repeater || node.lat == null || node.lon == null) continue;

                var dockedList = dockedMap[node.node_id] || dockedMap[node.alias];
                if (!dockedList && node.alias) {
                    dockedList = dockedMap['@' + node.alias] || dockedMap[node.alias.replace(/^@/, '')];
                }
                if (!dockedList || dockedList.length === 0) continue;

                var uniqueDocked = [];
                var seenIds = {};
                for (var d = 0; d < dockedList.length; d++) {
                    var dNode = dockedList[d];
                    if (!dNode || !dNode.node_id || seenIds[dNode.node_id]) continue;
                    seenIds[dNode.node_id] = true;
                    uniqueDocked.push(dNode);
                }
                if (uniqueDocked.length === 0) continue;

                var safeRepId = escapeJsString(node.node_id).replace(/[^a-zA-Z0-9_-]/g, '_');
                activeOrbIds[safeRepId] = true;

                // Update 3D node marker dot ring styling
                if (map3dMarkers[node.node_id]) {
                    var repEl = map3dMarkers[node.node_id].getElement();
                    var repDot = repEl ? repEl.querySelector('.node-dot') : null;
                    if (repDot) {
                        repDot.classList.add('orbital-ring-repeater');
                        if (node.is_favorite) repDot.classList.add('orbital-ring-repeater-fav');
                    }
                }

                if (!showSatellites) {
                    if (map3dOrbitalMarkers[safeRepId]) {
                        map3dOrbitalMarkers[safeRepId].remove();
                        delete map3dOrbitalMarkers[safeRepId];
                    }
                    continue;
                }

                if (map3dOrbitalMarkers[safeRepId]) {
                    map3dOrbitalMarkers[safeRepId].setLngLat([node.lon, node.lat]);
                    continue;
                }

                var repAliasStr = escapeHtml(node.alias || node.node_id || 'Repeater');
                var isFav = !!node.is_favorite;
                var repColor = isFav ? 'var(--favorite-color, #AA55FF)' : 'var(--orbital-repeater-color, #FFD335)';
                var repLabel = (isFav ? '★ ' : '') + repAliasStr;

                var N = uniqueDocked.length;
                var hasOverflow = N > 5;
                var visibleCount = hasOverflow ? 5 : N;
                var totalPositions = hasOverflow ? 6 : visibleCount;
                var isPaused = !!(window._pausedOrbitals && window._pausedOrbitals[safeRepId]);

                var repHash = 0;
                var idStr = String(node.node_id || node.alias || 'rep');
                for (var h = 0; h < idStr.length; h++) {
                    repHash = (repHash * 31 + idStr.charCodeAt(h)) & 0xFFFFFF;
                }
                var startAngleOffset = ((repHash % 360) * Math.PI) / 180;

                var svgDefs = ['<defs>'];
                var svgTrails = [];
                var svgNodes = [];

                for (var oi = 0; oi < totalPositions; oi++) {
                    var angle = -Math.PI / 2 + startAngleOffset + (oi * 2 * Math.PI / totalPositions);
                    var x = (60 + 46 * Math.cos(angle)).toFixed(1);
                    var y = (60 + 46 * Math.sin(angle)).toFixed(1);

                    if (oi < visibleCount) {
                        var sat = uniqueDocked[oi];
                        var rawAlias = sat.alias || sat.node_id || 'Companion';
                        var safeAlias = escapeHtml(rawAlias);
                        var lastHeardStr = (typeof formatLastHeard === 'function') ? formatLastHeard(sat.last_heard) : 'Recently';
                        var chanStr = sat.channel ? escapeHtml(sat.channel) : 'Public';
                        var repNameStr = escapeHtml(node.alias || node.node_id || 'Repeater');
                        var snrStr = (sat.snr !== null && sat.snr !== undefined && sat.snr !== 0) ? ((Number(sat.snr) > 0 ? '+' : '') + Number(sat.snr).toFixed(1) + ' dB') : 'N/A';
                        var isUnknownFirst = !!sat.is_unknown_first_hop;
                        var satColor = isUnknownFirst ? '#EF4444' : '#00FFFF';
                        var satBorder = isUnknownFirst ? '#991B1B' : '#047857';

                        var trailAngle = angle - 0.58;
                        var xTail = (60 + 46 * Math.cos(trailAngle)).toFixed(1);
                        var yTail = (60 + 46 * Math.sin(trailAngle)).toFixed(1);
                        var gradId = 'trail_grad_3d_' + safeRepId + '_' + oi;

                        svgDefs.push(
                            '<linearGradient id="' + gradId + '" x1="' + xTail + '" y1="' + yTail + '" x2="' + x + '" y2="' + y + '" gradientUnits="userSpaceOnUse">' +
                            '<stop offset="0%" stop-color="' + satColor + '" stop-opacity="0" />' +
                            '<stop offset="30%" stop-color="' + satColor + '" stop-opacity="0.25" />' +
                            '<stop offset="70%" stop-color="' + satColor + '" stop-opacity="0.65" />' +
                            '<stop offset="100%" stop-color="' + satColor + '" stop-opacity="0.95" />' +
                            '</linearGradient>'
                        );

                        svgTrails.push(
                            '<path d="M ' + xTail + ' ' + yTail + ' A 46 46 0 0 1 ' + x + ' ' + y + '" ' +
                            'fill="none" stroke="url(#' + gradId + ')" stroke-width="3.8" stroke-linecap="round" pointer-events="none" ' +
                            'style="filter: drop-shadow(0 0 4px ' + satColor + ');" />'
                        );

                        svgNodes.push(
                            '<g class="orbital-sat-node" style="pointer-events:all; cursor:pointer;" ' +
                            'data-alias="' + safeAlias + '" ' +
                            'data-time="' + lastHeardStr + '" ' +
                            'data-channel="' + chanStr + '" ' +
                            'data-repeater="' + repNameStr + '" ' +
                            'data-snr="' + snrStr + '" ' +
                            'data-node-id="' + encodeURIComponent(sat.node_id) + '" ' +
                            'data-unknown-first="' + (isUnknownFirst ? 'true' : 'false') + '" ' +
                            'data-first-hop="' + escapeHtml(sat.first_hop_alias || '') + '" ' +
                            'data-color="' + satColor + '" ' +
                            'onmouseenter="orbitalSatHover(this, event)" ' +
                            'onmouseleave="orbitalSatLeave(this)" ' +
                            'onclick="onOrbitalSatClicked(this, event)">' +
                            '<circle cx="' + x + '" cy="' + y + '" r="12" fill="transparent" />' +
                            '<circle class="sat-dot" cx="' + x + '" cy="' + y + '" r="4.25" fill="' + satColor + '" stroke="' + satBorder + '" stroke-width="1.1" style="filter:drop-shadow(0 0 6px ' + satColor + '); transition:r 0.15s ease, fill 0.15s ease;" />' +
                            '</g>'
                        );
                    } else if (oi === 5 && hasOverflow) {
                        var overflowCount = N - 5;
                        var extraNames = uniqueDocked.slice(5).map(function(s) { return escapeHtml(s.alias || s.node_id); });
                        var joinedNames = extraNames.join('|||');
                        var repNameStr = escapeHtml(node.alias || node.node_id || 'Repeater');

                        svgNodes.push(
                            '<g class="orbital-overflow-badge orbital-sat-node" style="pointer-events:all; cursor:pointer;" ' +
                            'data-count="' + overflowCount + '" ' +
                            'data-names="' + joinedNames + '" ' +
                            'data-repeater="' + repNameStr + '" ' +
                            'onmouseenter="orbitalOverflowHover(this, event)" ' +
                            'onmouseleave="orbitalOverflowLeave(this)">' +
                            '<rect x="' + (Number(x) - 13).toFixed(1) + '" y="' + (Number(y) - 8).toFixed(1) + '" width="26" height="16" rx="8" fill="#111827" stroke="' + repColor + '" stroke-width="1.2" />' +
                            '<text x="' + x + '" y="' + (Number(y) + 3.5).toFixed(1) + '" text-anchor="middle" fill="' + repColor + '" font-size="9" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-weight="bold">+' + overflowCount + '</text>' +
                            '</g>'
                        );
                    }
                }
                svgDefs.push('</defs>');

                var svgParts = [
                    '<svg width="120" height="120" viewBox="0 0 120 120" style="overflow:visible; pointer-events:none;">',
                    svgDefs.join(''),
                    '<circle cx="60" cy="60" r="46" fill="none" stroke="rgba(255, 255, 255, 0.40)" stroke-width="1.2" stroke-dasharray="3, 4" pointer-events="none" />',
                    '<text x="60" y="80" text-anchor="middle" fill="' + repColor + '" font-size="9" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-weight="bold" style="text-shadow: 0 1px 3px rgba(0,0,0,0.95); pointer-events:none;">' + repLabel + '</text>',
                    '<g class="orbital-sat-group">',
                    svgTrails.join(''),
                    svgNodes.join(''),
                    '</g>',
                    '</svg>'
                ];

                var wrap = document.createElement('div');
                wrap.className = 'orbital-host-container';
                wrap.style.width = '120px';
                wrap.style.height = '120px';
                wrap.style.pointerEvents = 'none';
                wrap.innerHTML = '<div id="orbital_host_3d_' + safeRepId + '" class="orbital-host-inner' + (isPaused ? ' orbital-paused' : '') + '">' + svgParts.join('') + '</div>';

                var m3Orb = new maplibregl.Marker({
                    element: wrap,
                    anchor: 'center',
                    pitchAlignment: 'map',
                    rotationAlignment: 'map'
                })
                    .setLngLat([node.lon, node.lat])
                    .addTo(map3d);
                map3dOrbitalMarkers[safeRepId] = m3Orb;
            }

            for (var oldSafeId in map3dOrbitalMarkers) {
                if (!activeOrbIds[oldSafeId]) {
                    map3dOrbitalMarkers[oldSafeId].remove();
                    delete map3dOrbitalMarkers[oldSafeId];
                }
            }
        }
        window.syncOrbitalsTo3D = syncOrbitalsTo3D;

        function syncSatellitesTo3D() {
            if (!map3d) return;
            if (!satellitesActive || typeof satellite === 'undefined' || !satellitesParsed || satellitesParsed.length === 0) {
                if (map3d.getSource('satellites-3d-plumb')) {
                    map3d.getSource('satellites-3d-plumb').setData({ type: 'FeatureCollection', features: [] });
                }
                if (map3d.getSource('satellites-3d-footprints')) {
                    map3d.getSource('satellites-3d-footprints').setData({ type: 'FeatureCollection', features: [] });
                }
                for (var sid in map3dSatMarkers) {
                    map3dSatMarkers[sid].remove();
                }
                map3dSatMarkers = {};
                return;
            }

            var plumbFeatures = [];
            var footprintFeatures = [];
            var activeSatIds = {};

            for (var si = 0; si < satellitesParsed.length; si++) {
                var sat = satellitesParsed[si];
                if (sat.lat == null || sat.lon == null) continue;

                var matchesGroup = currentSatGroup === 'all' || sat.group_name === currentSatGroup;
                var matchesSearch = !satSearchQuery || sat.name.toLowerCase().indexOf(satSearchQuery) !== -1 || sat.norad_id.indexOf(satSearchQuery) !== -1;
                if (!matchesGroup || !matchesSearch) continue;

                var sNoradId = sat.norad_id;
                activeSatIds[sNoradId] = true;
                var isSelected = (selectedNoradId === sNoradId);

                var groundElev = 0;
                if (map3d.queryTerrainElevation) {
                    try { groundElev = Math.max(0, map3d.queryTerrainElevation([sat.lon, sat.lat]) || 0); } catch(e) {}
                }
                var altVisualM = Math.max(15000, Math.min(100000, (sat.alt_km || 500) * 80.0));

                plumbFeatures.push({
                    type: 'Feature',
                    properties: {
                        id: sNoradId,
                        selected: isSelected,
                        type: 'line'
                    },
                    geometry: {
                        type: 'LineString',
                        coordinates: [
                            [sat.lon, sat.lat, groundElev],
                            [sat.lon, sat.lat, groundElev + altVisualM]
                        ]
                    }
                });

                plumbFeatures.push({
                    type: 'Feature',
                    properties: {
                        id: sNoradId,
                        selected: isSelected,
                        type: 'point'
                    },
                    geometry: {
                        type: 'Point',
                        coordinates: [sat.lon, sat.lat, groundElev]
                    }
                });

                if (isSelected && sat.alt_km > 0) {
                    var earthR = 6371.0;
                    var theta = Math.acos(earthR / (earthR + sat.alt_km));
                    var footRadiusM = theta * earthR * 1000.0;
                    var footPoly = makeCirclePolygon3D(sat.lon, sat.lat, footRadiusM, 48);
                    footprintFeatures.push({
                        type: 'Feature',
                        properties: { id: sNoradId },
                        geometry: { type: 'LineString', coordinates: footPoly[0] }
                    });
                }

                if (map3dSatMarkers[sNoradId]) {
                    map3dSatMarkers[sNoradId].setLngLat([sat.lon, sat.lat]);
                    var sEl = map3dSatMarkers[sNoradId].getElement();
                    if (sEl) {
                        if (isSelected) sEl.classList.add('selected');
                        else sEl.classList.remove('selected');
                    }
                } else {
                    var sEl = document.createElement('div');
                    sEl.className = 'satellite-marker-wrap' + (isSelected ? ' selected' : '');
                    sEl.style.cursor = 'pointer';
                    sEl.innerHTML = '<div class="sat-marker-body" style="background:rgba(15,23,42,0.92); border:1.5px solid #38BDF8; border-radius:12px; padding:2px 7px; display:flex; align-items:center; gap:4px; box-shadow:0 0 10px rgba(56,189,248,0.6); font-family:system-ui,-apple-system,sans-serif; white-space:nowrap;">' +
                        '<span style="font-size:12px;">🛰️</span>' +
                        '<span style="font-size:9.5px; font-weight:700; color:#38BDF8;">' + escapeHtml(sat.name) + '</span>' +
                        (sat.in_view ? '<span style="font-size:8px; color:#10B981; font-weight:bold;">▲' + Math.round(sat.elevation_deg) + '°</span>' : '') +
                        '</div>';

                    (function(curSat) {
                        sEl.addEventListener('click', function(ev) {
                            ev.stopPropagation();
                            selectSatellite(curSat.norad_id);
                        });
                    })(sat);

                    var satM3 = new maplibregl.Marker({ element: sEl, anchor: 'bottom' })
                        .setLngLat([sat.lon, sat.lat])
                        .addTo(map3d);
                    map3dSatMarkers[sNoradId] = satM3;
                }
            }

            for (var oldSid in map3dSatMarkers) {
                if (!activeSatIds[oldSid]) {
                    map3dSatMarkers[oldSid].remove();
                    delete map3dSatMarkers[oldSid];
                }
            }

            if (map3d.getSource('satellites-3d-plumb')) {
                map3d.getSource('satellites-3d-plumb').setData({
                    type: 'FeatureCollection',
                    features: plumbFeatures
                });
            }
            if (map3d.getSource('satellites-3d-footprints')) {
                map3d.getSource('satellites-3d-footprints').setData({
                    type: 'FeatureCollection',
                    features: footprintFeatures
                });
            }
        }
        window.syncSatellitesTo3D = syncSatellitesTo3D;

        function syncVisualisedPathTo3D(segments, meta) {
            visualised3DArcs = [];
            if (!segments || segments.length === 0) {
                if (map3d) map3d.triggerRepaint();
                return;
            }
            meta = meta || {};
            var pType = (meta.payload_type || meta.route_type || 'FLOOD').toUpperCase();
            var isMqtt = meta && (meta.source === 'mqtt' || (meta.packet_id && meta.packet_id.indexOf('mqtt-') === 0));

            // Default color from legend:
            // green is advert, blue is group text message, gold is direct, purple is data request, red is route trace
            var baseColorHex = '#3B82F6';
            if (isMqtt) {
                baseColorHex = '#F97316';
            } else if (pType === 'ADVERT') {
                baseColorHex = '#22C55E';
            } else if (pType === 'GRP_TXT' || pType === 'FLOOD') {
                baseColorHex = '#3B82F6';
            } else if (pType === 'TXT_MSG' || pType === 'DIRECT') {
                baseColorHex = '#F59E0B';
            } else if (pType === 'REQ' || pType === 'GRP_DATA' || pType === 'ANON_REQ') {
                baseColorHex = '#A855F7';
            } else if (pType === 'TRACE' || pType === 'PATH') {
                baseColorHex = '#EF4444';
            } else if (meta.color) {
                baseColorHex = meta.color;
            }

            for (var si = 0; si < segments.length; si++) {
                var seg = segments[si];
                if (!seg.coords || seg.coords.length < 2) continue;
                var p1 = seg.coords[0];
                var p2 = seg.coords[1];
                // generate3DArcPoints guarantees clearance over all sampled mountain summits
                var pts = generate3DArcPoints(p1[1], p1[0], p2[1], p2[0], 64);

                var segHex = seg.color || baseColorHex;
                if (seg.is_phantom) {
                    segHex = '#FFD700'; // Gold phantom node
                } else if (seg.is_unknown) {
                    segHex = '#EF4444'; // Red unknown
                } else if (seg.is_no_gps) {
                    segHex = '#9CA3AF'; // Gray/no GPS
                }
                var segRgb = hexToRgb(segHex);
                var segNorm = [segRgb[0] / 255.0, segRgb[1] / 255.0, segRgb[2] / 255.0];

                // Line style:
                // solid is verified rf hop, dashes is ambiguous hop (collision), dotted is inferred step (no gps / mqtt)
                var segStyle = 'solid';
                if (seg.is_ambiguous) {
                    segStyle = 'dashed';
                } else if (seg.is_inferred || seg.is_no_gps || seg.is_unknown || seg.is_phantom || (isMqtt && segments.length <= 1)) {
                    segStyle = 'dotted';
                }

                visualised3DArcs.push({
                    points: pts,
                    color: segNorm,
                    style: segStyle
                });
            }
            if (map3d) map3d.triggerRepaint();
        }
        window.syncVisualisedPathTo3D = syncVisualisedPathTo3D;

        function syncAllLayersTo3D() {
            if (!map3d) return;
            syncAllNodesTo3D();
            syncScopesTo3D();
            syncHeatmapTo3D();
            syncThunderstormTo3D();
            syncAdsbTo3D();
            syncTropoTo3D();
            syncSpaceWeatherTo3D();
            syncLosTo3D();
            syncOrbitalsTo3D();
            syncSatellitesTo3D();
            if (window._activeVisualisedSegments) {
                syncVisualisedPathTo3D(window._activeVisualisedSegments, window._activeVisualisedMeta);
            }
        }
        window.syncAllLayersTo3D = syncAllLayersTo3D;

        var wsServerIdx = 0;
        var blitzServers = ['wss://ws7.blitzortung.org', 'wss://ws1.blitzortung.org', 'wss://ws8.blitzortung.org'];
        window._radarMeta = null;
        window._radarFrameIdx = 0;
        window._nearestStrike = null;
        window._lastLightningAlertTime = 0;

        function setThunderstormVisible(visible, radarMeta) {
            thunderstormActive = !!visible;
            var panel = document.getElementById('thunderstorm-panel');
            if (panel) {
                panel.style.display = thunderstormActive ? 'flex' : 'none';
            }

            if (thunderstormActive) {
                if (radarMeta && radarMeta.host) {
                    updateThunderstormRadar(radarMeta);
                }
                connectBlitzortung();
            } else {
                if (rainViewerRadarLayer) {
                    map.removeLayer(rainViewerRadarLayer);
                    rainViewerRadarLayer = null;
                }
                if (lightningStrikesGroup) {
                    lightningStrikesGroup.clearLayers();
                }
                lightningStrikesList = [];
                disconnectBlitzortung();
            }
            if (window._is3DActive && typeof syncThunderstormTo3D === 'function') {
                syncThunderstormTo3D();
            }
        }
        window.setThunderstormVisible = setThunderstormVisible;

        function updateThunderstormRadar(radarMeta) {
            if (!radarMeta || !radarMeta.host) return;
            window._radarMeta = radarMeta;
            var frames = radarMeta.frames || [];
            if (radarMeta.default_idx !== undefined && radarMeta.default_idx >= 0 && radarMeta.default_idx < frames.length) {
                window._radarFrameIdx = radarMeta.default_idx;
            } else {
                window._radarFrameIdx = Math.max(0, frames.length - 1);
            }
            renderThunderstormFrame();
        }
        window.updateThunderstormRadar = updateThunderstormRadar;

        function renderThunderstormFrame() {
            if (rainViewerRadarLayer) {
                map.removeLayer(rainViewerRadarLayer);
                rainViewerRadarLayer = null;
            }
            if (!thunderstormActive || !window._radarMeta) {
                if (window._is3DActive && typeof syncThunderstormTo3D === 'function') {
                    syncThunderstormTo3D();
                }
                return;
            }
            var frames = window._radarMeta.frames || [];
            var framePath = window._radarMeta.path || '';
            var frameTime = null;
            var isNowcast = false;

            if (frames.length > 0 && window._radarFrameIdx >= 0 && window._radarFrameIdx < frames.length) {
                var cur = frames[window._radarFrameIdx];
                framePath = cur.path;
                frameTime = cur.time;
                isNowcast = !!cur.is_nowcast;
            }

            if (framePath) {
                var tileUrl = window._radarMeta.host + framePath + '/256/{z}/{x}/{y}/2/1_1.png';
                rainViewerRadarLayer = L.tileLayer(tileUrl, {
                    opacity: 0.65,
                    maxNativeZoom: 7,
                    maxZoom: 19,
                    tileSize: 256,
                    updateWhenIdle: true,
                    pane: 'thunderstormPane'
                }).addTo(map);
            }

            var lbl = document.getElementById('thunder-radar-label');
            if (lbl) {
                if (frameTime) {
                    var diffMins = Math.round((frameTime * 1000 - Date.now()) / 60000);
                    var sign = diffMins > 0 ? '+' : '';
                    var typeStr = isNowcast ? 'Nowcast' : 'Past';
                    var dateObj = new Date(frameTime * 1000);
                    var timeStr = (dateObj.getUTCHours() < 10 ? '0' : '') + dateObj.getUTCHours() + ':' + (dateObj.getUTCMinutes() < 10 ? '0' : '') + dateObj.getUTCMinutes() + ' UTC';
                    lbl.innerText = typeStr + ' (' + sign + diffMins + 'm • ' + timeStr + ')';
                } else {
                    lbl.innerText = 'Radar: Live Feed';
                }
            }
            if (window._is3DActive && typeof syncThunderstormTo3D === 'function') {
                syncThunderstormTo3D();
            }
        }

        function stepThunderstormRadar(delta) {
            if (!window._radarMeta) return;
            var frames = window._radarMeta.frames || [];
            if (frames.length === 0) return;
            var newIdx = window._radarFrameIdx + delta;
            if (newIdx >= 0 && newIdx < frames.length) {
                window._radarFrameIdx = newIdx;
                renderThunderstormFrame();
            }
        }
        window.stepThunderstormRadar = stepThunderstormRadar;

        function getDistanceMiles(lat1, lon1, lat2, lon2) {
            var R = 3958.8;
            var dLat = (lat2 - lat1) * Math.PI / 180;
            var dLon = (lon2 - lon1) * Math.PI / 180;
            var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                    Math.sin(dLon / 2) * Math.sin(dLon / 2);
            var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
            return R * c;
        }

        function getBearingDeg(lat1, lon1, lat2, lon2) {
            var y = Math.sin((lon2 - lon1) * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180);
            var x = Math.cos(lat1 * Math.PI / 180) * Math.sin(lat2 * Math.PI / 180) -
                    Math.sin(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.cos((lon2 - lon1) * Math.PI / 180);
            var brng = Math.atan2(y, x) * 180 / Math.PI;
            return (brng + 360) % 360;
        }

        function jumpToNearestStrike() {
            if (window._nearestStrike && typeof window._nearestStrike.lat === 'number') {
                map.setView([window._nearestStrike.lat, window._nearestStrike.lon], Math.max(map.getZoom(), 11));
            }
        }
        window.jumpToNearestStrike = jumpToNearestStrike;

        function connectBlitzortung() {
            disconnectBlitzortung();
            if (!thunderstormActive) return;
            try {
                var sUrl = blitzServers[wsServerIdx % blitzServers.length];
                blitzortungWs = new WebSocket(sUrl);
                blitzortungWs.onopen = function() {
                    if (blitzortungWs) blitzortungWs.send(JSON.stringify({ a: 111 }));
                };
                blitzortungWs.onmessage = function(e) {
                    if (!thunderstormActive || !e.data) return;
                    try {
                        var strike = JSON.parse(e.data);
                        if (strike && typeof strike.lat === 'number' && typeof strike.lon === 'number') {
                            addLightningStrike(strike);
                        }
                    } catch (err) {}
                };
                blitzortungWs.onerror = function() {
                    wsServerIdx++;
                    setTimeout(function() { if (thunderstormActive) connectBlitzortung(); }, 3000);
                };
                blitzortungWs.onclose = function() {
                    if (thunderstormActive) {
                        setTimeout(function() { if (thunderstormActive) connectBlitzortung(); }, 4000);
                    }
                };
            } catch (e) {}
        }

        function disconnectBlitzortung() {
            if (blitzortungWs) {
                try { blitzortungWs.close(); } catch (e) {}
                blitzortungWs = null;
            }
        }

        function addLightningStrike(strike) {
            if (!thunderstormActive || !lightningStrikesGroup) return;
            var now = Date.now();
            var marker = L.marker([strike.lat, strike.lon], {
                icon: L.divIcon({
                    className: 'lightning-marker-wrap',
                    html: '<div class="lightning-flash-dot"></div>',
                    iconSize: [16, 16],
                    iconAnchor: [8, 8]
                }),
                pane: 'lightningPane',
                interactive: false
            });
            lightningStrikesGroup.addLayer(marker);
            lightningStrikesList.push({ marker: marker, ts: now, lat: strike.lat, lon: strike.lon });
            if (window._is3DActive && typeof syncThunderstormTo3D === 'function') {
                syncThunderstormTo3D();
            }

            var badge = document.getElementById('strike-count-badge');
            if (badge) badge.innerText = lightningStrikesList.length + ' strikes';

            var center = map.getCenter();
            var distMi = getDistanceMiles(center.lat, center.lng, strike.lat, strike.lon);
            var bearing = getBearingDeg(center.lat, center.lng, strike.lat, strike.lon);

            if (!window._nearestStrike || distMi < window._nearestStrike.dist || (now - window._nearestStrike.ts > 600000)) {
                window._nearestStrike = { lat: strike.lat, lon: strike.lon, dist: distMi, bearing: bearing, ts: now };
                var nVal = document.getElementById('thunder-nearest-val');
                var nTime = document.getElementById('thunder-nearest-time');
                var nDist = document.getElementById('thunder-stat-dist');
                if (nVal) nVal.innerText = distMi.toFixed(1) + ' mi (Bearing ' + Math.round(bearing) + '°)';
                if (nTime) nTime.innerText = 'Just now';
                if (nDist) nDist.innerText = distMi.toFixed(1) + ' mi';
            }

            var proxBadge = document.getElementById('thunder-proximity-badge');
            if (distMi <= 25.0) {
                if (proxBadge) proxBadge.style.display = 'block';
                if (now - window._lastLightningAlertTime > 60000) {
                    window._lastLightningAlertTime = now;
                    if (window.pyBridge && window.pyBridge.on_lightning_proximity_alert) {
                        window.pyBridge.on_lightning_proximity_alert(distMi, Math.round(bearing));
                    }
                }
            }

            var recentCount = 0;
            for (var i = 0; i < lightningStrikesList.length; i++) {
                if (now - lightningStrikesList[i].ts <= 60000) recentCount++;
            }
            var rateEl = document.getElementById('thunder-stat-rate');
            if (rateEl) rateEl.innerText = recentCount + ' / min';

            while (lightningStrikesList.length > 0 && (now - lightningStrikesList[0].ts > 1800000 || lightningStrikesList.length > 500)) {
                var old = lightningStrikesList.shift();
                if (old && old.marker) lightningStrikesGroup.removeLayer(old.marker);
            }
        }
        window.addLightningStrike = addLightningStrike;

        var currentAuroraLayer = null;
        var currentAuroraOpacity = 0.60;
        var auroraActive = false;

        function clearSpaceWeatherLayer() {
            if (currentAuroraLayer !== null) {
                map.removeLayer(currentAuroraLayer);
                currentAuroraLayer = null;
            }
            auroraActive = false;
            window._lastAuroraGeojson = null;
            var panel = document.getElementById('aurora-legend-panel');
            if (panel) panel.style.display = 'none';
            if (window._is3DActive && typeof syncSpaceWeatherTo3D === 'function') {
                syncSpaceWeatherTo3D();
            }
        }
        window.clearSpaceWeatherLayer = clearSpaceWeatherLayer;

        function updateAuroraOpacity(val) {
            currentAuroraOpacity = val / 100.0;
            var lbl = document.getElementById('aurora-opacity-label');
            if (lbl) lbl.textContent = val + '%';
            if (currentAuroraLayer) {
                currentAuroraLayer.eachLayer(function(layer) {
                    if (layer._baseFillOpacity !== undefined) {
                        layer.setStyle({ fillOpacity: layer._baseFillOpacity * currentAuroraOpacity });
                    }
                });
            }
            if (window._is3DActive && typeof syncSpaceWeatherTo3D === 'function') {
                syncSpaceWeatherTo3D();
            }
            if (window.pyBridge && window.pyBridge.on_space_weather_opacity) {
                window.pyBridge.on_space_weather_opacity(currentAuroraOpacity);
            }
        }
        window.updateAuroraOpacity = updateAuroraOpacity;

        window.onSpaceWeatherReady = function(payload) {
            if (!payload) return;
            auroraActive = true;
            var panel = document.getElementById('aurora-legend-panel');
            if (panel) panel.style.display = 'flex';

            var kpValEl = document.getElementById('aurora-kp-val');
            var kpSubEl = document.getElementById('aurora-kp-sub');
            var kpCard = document.getElementById('aurora-kp-card');
            if (kpValEl && payload.kp !== undefined) {
                kpValEl.textContent = 'Kp ' + payload.kp.toFixed(1);
                kpValEl.style.color = payload.kp_color || '#10B981';
            }
            if (kpSubEl && payload.kp_status) {
                kpSubEl.textContent = (payload.g_scale && payload.g_scale !== 'G0' ? payload.g_scale + ' • ' : '') + payload.kp_status;
            }
            if (kpCard && payload.kp_color) {
                kpCard.style.borderColor = payload.kp_color;
            }

            var windValEl = document.getElementById('aurora-wind-val');
            var bzSubEl = document.getElementById('aurora-bz-sub');
            if (windValEl) {
                windValEl.textContent = payload.solar_wind_speed ? payload.solar_wind_speed + ' km/s' : '-- km/s';
            }
            if (bzSubEl) {
                if (payload.solar_wind_bz !== null && payload.solar_wind_bz !== undefined) {
                    var isSouth = payload.solar_wind_bz < 0;
                    bzSubEl.innerHTML = 'Bz: <span style=\"color:' + (isSouth ? '#4ADE80' : '#94A3B8') + '\">' + payload.solar_wind_bz + ' nT ' + (isSouth ? '▼' : '▲') + '</span>';
                } else {
                    bzSubEl.textContent = 'Bz: -- nT';
                }
            }

            var sfiValEl = document.getElementById('aurora-sfi-val');
            var scalesSubEl = document.getElementById('aurora-scales-sub');
            if (sfiValEl) {
                sfiValEl.textContent = payload.solar_flux ? payload.solar_flux + ' sfu' : '-- sfu';
            }
            if (scalesSubEl && payload.scales) {
                scalesSubEl.textContent = 'R' + (payload.scales.R || '0') + ' S' + (payload.scales.S || '0') + ' G' + (payload.scales.G || '0');
            }

            var maxProbEl = document.getElementById('aurora-max-prob');
            if (maxProbEl && payload.max_prob !== undefined) {
                maxProbEl.textContent = 'Peak: ' + payload.max_prob + '%';
            }

            var timeLabel = document.getElementById('aurora-time-label');
            if (timeLabel) {
                var tStr = payload.forecast_time || payload.updated_at || '';
                if (tStr) {
                    try {
                        var dt = new Date(tStr);
                        timeLabel.textContent = 'NOAA SWPC • ' + dt.toUTCString().replace('GMT', 'UTC');
                    } catch(e) {
                        timeLabel.textContent = 'NOAA SWPC • ' + tStr;
                    }
                }
            }

            if (payload.grid && payload.grid.b64_grid) {
                try {
                    var grid = payload.grid;
                    var w = grid.w || 360;
                    var h = grid.h || 181;
                    var binStr = atob(grid.b64_grid);
                    var len = binStr.length;
                    var bytes = new Uint8Array(len);
                    for (var i = 0; i < len; i++) {
                        bytes[i] = binStr.charCodeAt(i);
                    }

                    var thresholds = [5, 15, 30, 50, 75];
                    var colorStyles = {
                        5:  { fill: '#22c55e', baseFillOpacity: 0.25, stroke: '#16a34a', weight: 1.0 },
                        15: { fill: '#4ade80', baseFillOpacity: 0.40, stroke: '#22c55e', weight: 1.2 },
                        30: { fill: '#38bdf8', baseFillOpacity: 0.55, stroke: '#0284c7', weight: 1.5 },
                        50: { fill: '#c084fc', baseFillOpacity: 0.70, stroke: '#9333ea', weight: 1.8 },
                        75: { fill: '#f43f5e', baseFillOpacity: 0.85, stroke: '#e11d48', weight: 2.0 }
                    };

                    var rawContours = d3.contours().size([w, h]).thresholds(thresholds)(bytes);
                    var features = [];

                    for (var cIdx = 0; cIdx < rawContours.length; cIdx++) {
                        var c = rawContours[cIdx];
                        if (!c.coordinates || c.coordinates.length === 0) continue;
                        var thVal = c.value;
                        var newCoords = c.coordinates.map(function(ringList) {
                            return ringList.map(function(ring) {
                                return ring.map(function(pt) {
                                    var lon = pt[0] - 180;
                                    var lat = pt[1] - 90;
                                    return [Number(lon.toFixed(2)), Number(lat.toFixed(2))];
                                });
                            });
                        });
                        features.push({
                            type: "Feature",
                            properties: { threshold: thVal },
                            geometry: {
                                type: "MultiPolygon",
                                coordinates: newCoords
                            }
                        });
                    }

                    if (currentAuroraLayer !== null) {
                        map.removeLayer(currentAuroraLayer);
                        currentAuroraLayer = null;
                    }

                    for (var fi = 0; fi < features.length; fi++) {
                        var th = features[fi].properties.threshold || 5;
                        var cs = colorStyles[th] || colorStyles[5];
                        features[fi].properties.fill = cs.fill;
                        features[fi].properties.stroke = cs.stroke;
                        features[fi].properties.baseFillOpacity = cs.baseFillOpacity;
                    }

                    var geoJsonData = { type: "FeatureCollection", features: features };
                    currentAuroraLayer = L.geoJSON(geoJsonData, {
                        pane: 'auroraPane',
                        style: function(feat) {
                            var th = feat.properties.threshold || 5;
                            var cs = colorStyles[th] || colorStyles[5];
                            return {
                                fillColor: cs.fill,
                                fillOpacity: cs.baseFillOpacity * currentAuroraOpacity,
                                color: cs.stroke,
                                weight: cs.weight,
                                opacity: 0.9,
                                interactive: false
                            };
                        },
                        onEachFeature: function(feat, layer) {
                            var th = feat.properties.threshold || 5;
                            var cs = colorStyles[th] || colorStyles[5];
                            layer._baseFillOpacity = cs.baseFillOpacity;
                        }
                    }).addTo(map);

                    window._lastAuroraGeojson = geoJsonData;
                    if (window._is3DActive && typeof syncSpaceWeatherTo3D === 'function') {
                        syncSpaceWeatherTo3D();
                    }

                } catch(err) {
                    console.error("Failed to contour aurora grid:", err);
                }
            }
        };

        // ==========================================
        // SATELLITE TRACKING & SGP4 ORBITAL ENGINE
        // ==========================================
        var satellitesActive = false;
        var satellitesRawData = [];
        var satellitesParsed = [];
        var satelliteMarkers = {};
        var selectedNoradId = null;
        var satGroundTrackLayers = [];
        var satFootprintLayer = null;
        var satObserver = null;
        var satPropagationInterval = null;
        var showSatFootprint = true;
        var showSatGroundTrack = true;
        var currentSatGroup = 'all';
        var satSearchQuery = '';

        function setSatellitesVisible(visible) {
            satellitesActive = !!visible;
            var panel = document.getElementById('satellite-panel');
            if (panel) {
                panel.style.display = satellitesActive ? 'flex' : 'none';
            }
            if (satellitesActive) {
                startSatellitePropagationLoop();
            } else {
                stopSatellitePropagationLoop();
                clearSatelliteMapLayers();
            }
            if (window._is3DActive && typeof syncSatellitesTo3D === 'function') {
                syncSatellitesTo3D();
            }
        }
        window.setSatellitesVisible = setSatellitesVisible;

        function clearSatelliteMapLayers() {
            for (var id in satelliteMarkers) {
                if (satelliteMarkers[id]) {
                    map.removeLayer(satelliteMarkers[id]);
                }
            }
            satelliteMarkers = {};
            clearSelectedSatGraphics();
        }

        function clearSelectedSatGraphics() {
            if (satGroundTrackLayers && satGroundTrackLayers.length > 0) {
                for (var i = 0; i < satGroundTrackLayers.length; i++) {
                    map.removeLayer(satGroundTrackLayers[i]);
                }
                satGroundTrackLayers = [];
            }
            if (satFootprintLayer) {
                map.removeLayer(satFootprintLayer);
                satFootprintLayer = null;
            }
        }

        function onSatellitesDataReady(payload) {
            if (!payload) return;
            satellitesRawData = payload.satellites || [];
            if (payload.observer && typeof payload.observer.lat === 'number' && typeof payload.observer.lon === 'number') {
                satObserver = {
                    lat: payload.observer.lat,
                    lon: payload.observer.lon,
                    alt_km: (payload.observer.alt_m || 0) / 1000.0
                };
            }

            satellitesParsed = [];
            if (typeof satellite !== 'undefined') {
                for (var i = 0; i < satellitesRawData.length; i++) {
                    var s = satellitesRawData[i];
                    try {
                        var satrec = satellite.twoline2satrec(s.line1, s.line2);
                        satellitesParsed.push({
                            norad_id: String(s.norad_id),
                            name: s.name || ('SAT-' + s.norad_id),
                            group_name: (s.group_name || 'amateur').toLowerCase(),
                            satrec: satrec,
                            frequencies: s.frequencies || [],
                            lat: 0,
                            lon: 0,
                            alt_km: 0,
                            speed_km_s: 0,
                            azimuth_deg: 0,
                            elevation_deg: -90,
                            range_km: 0,
                            range_rate_km_s: 0,
                            in_view: false
                        });
                    } catch (e) {
                        console.warn("Error parsing TLE for sat:", s.norad_id, e);
                    }
                }
            }

            var badge = document.getElementById('sat-count-badge');
            if (badge) badge.textContent = satellitesParsed.length;

            renderSatellitesQuickList();
            updateSatellitePositions();
        }
        window.onSatellitesDataReady = onSatellitesDataReady;

        function startSatellitePropagationLoop() {
            if (satPropagationInterval) clearInterval(satPropagationInterval);
            updateSatellitePositions();
            satPropagationInterval = setInterval(updateSatellitePositions, 1000);
        }

        function stopSatellitePropagationLoop() {
            if (satPropagationInterval) {
                clearInterval(satPropagationInterval);
                satPropagationInterval = null;
            }
        }

        function updateSatellitePositions() {
            if (!satellitesActive || typeof satellite === 'undefined' || satellitesParsed.length === 0) return;

            var now = new Date();
            var gstime = satellite.gstime(now);

            var obsGd = null;
            var obsEcf = null;
            if (satObserver) {
                obsGd = {
                    longitude: satellite.degreesToRadians(satObserver.lon),
                    latitude: satellite.degreesToRadians(satObserver.lat),
                    height: satObserver.alt_km
                };
                obsEcf = satellite.geodeticToEcf(obsGd);
            }

            for (var i = 0; i < satellitesParsed.length; i++) {
                var sat = satellitesParsed[i];
                try {
                    var prop = satellite.propagate(sat.satrec, now);
                    if (!prop.position || !prop.velocity) continue;

                    var gd = satellite.eciToGeodetic(prop.position, gstime);
                    var lat = satellite.degreesLat(gd.latitude);
                    var lon = satellite.degreesLong(gd.longitude);
                    var altKm = gd.height;

                    sat.lat = lat;
                    sat.lon = lon;
                    sat.alt_km = altKm;

                    var vx = prop.velocity.x;
                    var vy = prop.velocity.y;
                    var vz = prop.velocity.z;
                    sat.speed_km_s = Math.sqrt(vx * vx + vy * vy + vz * vz);

                    if (obsGd && obsEcf) {
                        var satEcf = satellite.eciToEcf(prop.position, gstime);
                        var look = satellite.ecfToLookAngles(obsGd, satEcf);
                        sat.azimuth_deg = satellite.radiansToDegrees(look.azimuth);
                        sat.elevation_deg = satellite.radiansToDegrees(look.elevation);
                        sat.range_km = look.rangeSat;
                        sat.in_view = sat.elevation_deg > 0;

                        var rx = satEcf.x - obsEcf.x;
                        var ry = satEcf.y - obsEcf.y;
                        var rz = satEcf.z - obsEcf.z;
                        var rMag = Math.sqrt(rx * rx + ry * ry + rz * rz);
                        if (rMag > 0.1) {
                            sat.range_rate_km_s = (rx * vx + ry * vy + rz * vz) / rMag;
                        } else {
                            sat.range_rate_km_s = 0;
                        }
                    } else {
                        sat.elevation_deg = -90;
                        sat.in_view = false;
                        sat.range_rate_km_s = 0;
                    }

                    var matchesGroup = currentSatGroup === 'all' || sat.group_name === currentSatGroup;
                    var matchesSearch = !satSearchQuery || sat.name.toLowerCase().indexOf(satSearchQuery) !== -1 || sat.norad_id.indexOf(satSearchQuery) !== -1;

                    if (matchesGroup && matchesSearch) {
                        updateSatMarker(sat);
                    } else if (satelliteMarkers[sat.norad_id]) {
                        map.removeLayer(satelliteMarkers[sat.norad_id]);
                        delete satelliteMarkers[sat.norad_id];
                    }
                } catch (err) {}
            }

            if (selectedNoradId) {
                var selSat = getSatelliteById(selectedNoradId);
                if (selSat) {
                    updateSatTelemetryHud(selSat);
                    if (showSatFootprint) {
                        renderSatFootprint(selSat);
                    }
                    if (showSatGroundTrack) {
                        renderSatGroundTrack(selSat, now);
                    }
                }
            }

            updateQuickListStatus();
            if (window._is3DActive && typeof syncSatellitesTo3D === 'function') {
                syncSatellitesTo3D();
            }
        }

        function updateSatMarker(sat) {
            var id = sat.norad_id;
            var latlng = [sat.lat, sat.lon];
            var isSelected = selectedNoradId === id;

            if (satelliteMarkers[id]) {
                satelliteMarkers[id].setLatLng(latlng);
                var el = satelliteMarkers[id].getElement();
                if (el) {
                    if (isSelected && !el.classList.contains('selected')) {
                        el.classList.add('selected');
                    } else if (!isSelected && el.classList.contains('selected')) {
                        el.classList.remove('selected');
                    }
                }
            } else {
                var grpCls = 'sat-marker-' + sat.group_name;
                var iconGlyph = '🛰️';
                if (sat.group_name === 'stations') iconGlyph = '🚀';
                else if (sat.group_name === 'weather') iconGlyph = '🌤️';
                else if (sat.group_name === 'amateur') iconGlyph = '📻';
                else if (sat.group_name === 'cubesat') iconGlyph = '📦';

                var html = '<div class="sat-marker ' + grpCls + (isSelected ? ' selected' : '') + '">' +
                           '<div class="sat-marker-icon">' + iconGlyph + '</div>' +
                           '<div class="sat-marker-label">' + escapeHtml(sat.name) + '</div>' +
                           '</div>';

                var divIcon = L.divIcon({
                    className: 'sat-leaflet-marker',
                    html: html,
                    iconSize: [60, 36],
                    iconAnchor: [30, 11]
                });

                var marker = L.marker(latlng, { icon: divIcon, zIndexOffset: 2000 });
                marker.on('click', function() {
                    selectSatellite(id);
                });
                marker.addTo(map);
                satelliteMarkers[id] = marker;
            }
        }

        function selectSatellite(noradId) {
            selectedNoradId = String(noradId);
            var sat = getSatelliteById(selectedNoradId);
            if (!sat) return;

            var rows = document.querySelectorAll('.sat-item-row');
            rows.forEach(function(r) {
                if (r.getAttribute('data-id') === selectedNoradId) {
                    r.classList.add('selected');
                    r.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                } else {
                    r.classList.remove('selected');
                }
            });

            for (var mid in satelliteMarkers) {
                var el = satelliteMarkers[mid].getElement();
                if (el) {
                    if (mid === selectedNoradId) el.classList.add('selected');
                    else el.classList.remove('selected');
                }
            }

            var box = document.getElementById('sat-telemetry-box');
            if (box) box.style.display = 'flex';

            updateSatTelemetryHud(sat);
            renderSatFootprint(sat);
            renderSatGroundTrack(sat, new Date());

            if (window.pyBridge && window.pyBridge.on_satellite_selected) {
                window.pyBridge.on_satellite_selected(selectedNoradId);
            }
        }
        window.selectSatellite = selectSatellite;

        function centerOnSelectedSatellite() {
            if (!selectedNoradId) return;
            var sat = getSatelliteById(selectedNoradId);
            if (sat && typeof sat.lat === 'number' && typeof sat.lon === 'number') {
                map.panTo([sat.lat, sat.lon]);
            }
        }
        window.centerOnSelectedSatellite = centerOnSelectedSatellite;

        function getSatelliteById(noradId) {
            for (var i = 0; i < satellitesParsed.length; i++) {
                if (satellitesParsed[i].norad_id === noradId) {
                    return satellitesParsed[i];
                }
            }
            return null;
        }

        function renderSatFootprint(sat) {
            if (satFootprintLayer) {
                map.removeLayer(satFootprintLayer);
                satFootprintLayer = null;
            }
            if (!showSatFootprint || !sat || sat.alt_km <= 0) return;

            var R = 6371.0;
            var h = sat.alt_km;
            var ratio = R / (R + h);
            if (ratio > 1.0) ratio = 1.0;
            var theta = Math.acos(ratio);
            var radiusMeters = R * theta * 1000.0;

            satFootprintLayer = L.circle([sat.lat, sat.lon], {
                radius: radiusMeters,
                color: '#38BDF8',
                weight: 1.5,
                fillColor: '#38BDF8',
                fillOpacity: 0.12,
                dashArray: '4, 6',
                interactive: false
            }).addTo(map);
        }

        function renderSatGroundTrack(sat, now) {
            if (satGroundTrackLayers && satGroundTrackLayers.length > 0) {
                for (var i = 0; i < satGroundTrackLayers.length; i++) {
                    map.removeLayer(satGroundTrackLayers[i]);
                }
                satGroundTrackLayers = [];
            }
            if (!showSatGroundTrack || !sat || !sat.satrec) return;

            var segments = [];
            var currentSegment = [];
            var prevLon = null;

            for (var m = 0; m <= 90; m++) {
                var t = new Date(now.getTime() + m * 60000);
                var gstime = satellite.gstime(t);
                var p = satellite.propagate(sat.satrec, t);
                if (!p.position) continue;
                var gd = satellite.eciToGeodetic(p.position, gstime);
                var lat = satellite.degreesLat(gd.latitude);
                var lon = satellite.degreesLong(gd.longitude);

                if (prevLon !== null && Math.abs(lon - prevLon) > 180) {
                    if (currentSegment.length > 1) {
                        segments.push(currentSegment);
                    }
                    currentSegment = [];
                }
                currentSegment.push([lat, lon]);
                prevLon = lon;
            }
            if (currentSegment.length > 1) {
                segments.push(currentSegment);
            }

            for (var s = 0; s < segments.length; s++) {
                var poly = L.polyline(segments[s], {
                    color: '#00FFC2',
                    weight: 2,
                    opacity: 0.65,
                    dashArray: '5, 5',
                    interactive: false
                }).addTo(map);
                satGroundTrackLayers.push(poly);
            }
        }

        function updateSatTelemetryHud(sat) {
            var nameEl = document.getElementById('sat-telem-name');
            var badgeEl = document.getElementById('sat-pass-badge');
            var latlonEl = document.getElementById('sat-telem-latlon');
            var altSpdEl = document.getElementById('sat-telem-alt-spd');
            var azElEl = document.getElementById('sat-telem-az-el');
            var rangeEl = document.getElementById('sat-telem-range');
            var freqsContainer = document.getElementById('sat-freqs-container');

            if (nameEl) nameEl.textContent = sat.name + ' (' + sat.norad_id + ')';
            if (badgeEl) {
                if (sat.in_view) {
                    badgeEl.className = 'sat-pass-badge in-view';
                    badgeEl.textContent = 'IN VIEW (' + sat.elevation_deg.toFixed(1) + '°)';
                } else {
                    badgeEl.className = 'sat-pass-badge below';
                    badgeEl.textContent = 'BELOW (' + (sat.elevation_deg < -80 ? 'N/A' : sat.elevation_deg.toFixed(1) + '°') + ')';
                }
            }
            if (latlonEl) {
                latlonEl.textContent = sat.lat.toFixed(2) + '°, ' + sat.lon.toFixed(2) + '°';
            }
            if (altSpdEl) {
                altSpdEl.textContent = Math.round(sat.alt_km) + ' km • ' + sat.speed_km_s.toFixed(2) + ' km/s';
            }
            if (azElEl) {
                if (satObserver) {
                    azElEl.textContent = Math.round(sat.azimuth_deg) + '° • ' + sat.elevation_deg.toFixed(1) + '°';
                } else {
                    azElEl.textContent = 'No Observer Coords';
                }
            }
            if (rangeEl) {
                if (satObserver && sat.range_km > 0) {
                    rangeEl.textContent = Math.round(sat.range_km) + ' km';
                } else {
                    rangeEl.textContent = '--';
                }
            }

            if (freqsContainer) {
                if (sat.frequencies && sat.frequencies.length > 0) {
                    var c = 299792.458;
                    var html = '<table class="sat-freq-table"><thead><tr><th>Channel</th><th>Nominal</th><th>Live Doppler</th></tr></thead><tbody>';
                    for (var f = 0; f < sat.frequencies.length; f++) {
                        var fr = sat.frequencies[f];
                        var f0 = fr.freq_mhz || 0;
                        var v_rad = sat.range_rate_km_s || 0;
                        var dopplerShiftHz = -f0 * 1e6 * (v_rad / c);
                        var liveFreqMhz = f0 + (dopplerShiftHz / 1e6);
                        var shiftFmt = (dopplerShiftHz >= 0 ? '+' : '') + (dopplerShiftHz / 1000.0).toFixed(2) + ' kHz';
                        var shiftCls = dopplerShiftHz >= 0 ? 'sat-doppler-pos' : 'sat-doppler-neg';

                        html += '<tr>' +
                                '<td><span style="font-weight:600;">' + escapeHtml(fr.label) + '</span> <span style="color:#9CA3AF;font-size:8px;">(' + escapeHtml(fr.mode || '') + ')</span></td>' +
                                '<td>' + f0.toFixed(3) + ' MHz</td>' +
                                '<td><span class="' + shiftCls + '">' + liveFreqMhz.toFixed(4) + ' MHz</span> <span style="font-size:8px;color:#9CA3AF;">(' + shiftFmt + ')</span></td>' +
                                '</tr>';
                    }
                    html += '</tbody></table>';
                    freqsContainer.innerHTML = html;
                } else {
                    freqsContainer.innerHTML = '<div style="font-size:9px;color:#9CA3AF;margin-top:2px;">No known downlink frequencies in catalog.</div>';
                }
            }
        }

        function renderSatellitesQuickList() {
            var listEl = document.getElementById('sat-quick-list');
            if (!listEl) return;

            var html = '';
            for (var i = 0; i < satellitesParsed.length; i++) {
                var s = satellitesParsed[i];
                var matchesGroup = currentSatGroup === 'all' || s.group_name === currentSatGroup;
                var matchesSearch = !satSearchQuery || s.name.toLowerCase().indexOf(satSearchQuery) !== -1 || s.norad_id.indexOf(satSearchQuery) !== -1;
                if (!matchesGroup || !matchesSearch) continue;

                var isSel = selectedNoradId === s.norad_id;
                var dotCls = s.in_view ? 'sat-in-view-dot' : 'sat-below-horizon-dot';
                var elText = s.in_view ? ('+' + s.elevation_deg.toFixed(0) + '°') : (s.elevation_deg < -80 ? '--' : s.elevation_deg.toFixed(0) + '°');

                html += '<div class="sat-item-row' + (isSel ? ' selected' : '') + '" data-id="' + s.norad_id + '" onclick="selectSatellite(this.dataset.id)">' +
                        '<span><span class="' + dotCls + '"></span>' + escapeHtml(s.name) + '</span>' +
                        '<span style="font-family:monospace;font-size:9px;color:' + (s.in_view ? '#34D399' : '#9CA3AF') + ';">' + elText + '</span>' +
                        '</div>';
            }
            listEl.innerHTML = html;
        }

        function updateQuickListStatus() {
            var rows = document.querySelectorAll('.sat-item-row');
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                var nid = r.getAttribute('data-id');
                var sat = getSatelliteById(nid);
                if (sat) {
                    var dot = r.querySelector('span > span');
                    if (dot) dot.className = sat.in_view ? 'sat-in-view-dot' : 'sat-below-horizon-dot';
                    var valSpan = r.children[1];
                    if (valSpan) {
                        valSpan.style.color = sat.in_view ? '#34D399' : '#9CA3AF';
                        valSpan.textContent = sat.in_view ? ('+' + sat.elevation_deg.toFixed(0) + '°') : (sat.elevation_deg < -80 ? '--' : sat.elevation_deg.toFixed(0) + '°');
                    }
                }
            }
        }

        function setSatelliteGroupFilter(grp) {
            currentSatGroup = grp;
            var pills = document.querySelectorAll('.sat-group-pill');
            pills.forEach(function(p) {
                if (p.id === 'sat-grp-' + grp) p.classList.add('active');
                else p.classList.remove('active');
            });
            renderSatellitesQuickList();
            updateSatellitePositions();
        }
        window.setSatelliteGroupFilter = setSatelliteGroupFilter;

        function filterSatellites(q) {
            satSearchQuery = (q || '').toLowerCase().trim();
            renderSatellitesQuickList();
            updateSatellitePositions();
        }
        window.filterSatellites = filterSatellites;

        function toggleSatFootprint(checked) {
            showSatFootprint = !!checked;
            if (selectedNoradId) {
                var sat = getSatelliteById(selectedNoradId);
                if (showSatFootprint) renderSatFootprint(sat);
                else if (satFootprintLayer) {
                    map.removeLayer(satFootprintLayer);
                    satFootprintLayer = null;
                }
            }
        }
        window.toggleSatFootprint = toggleSatFootprint;

        function toggleSatGroundTrack(checked) {
            showSatGroundTrack = !!checked;
            if (selectedNoradId) {
                var sat = getSatelliteById(selectedNoradId);
                if (showSatGroundTrack) renderSatGroundTrack(sat, new Date());
                else if (satGroundTrackLayers && satGroundTrackLayers.length > 0) {
                    for (var i = 0; i < satGroundTrackLayers.length; i++) {
                        map.removeLayer(satGroundTrackLayers[i]);
                    }
                    satGroundTrackLayers = [];
                }
            }
        }
        window.toggleSatGroundTrack = toggleSatGroundTrack;

        function previewPacketPath(coords, meta) {
            clearPreviewPacketPath();
            if (!coords || coords.length < 2) return;
            var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
            previewPathLayer = L.polyline(latlngs, {
                renderer: visualisedSvgRenderer,
                color: '#C084FC',
                weight: 4,
                opacity: 0.95,
                dashArray: '8, 12',
                className: 'animated-path-flow',
                lineCap: 'round',
                lineJoin: 'round',
                pane: 'overlayPane',
                interactive: false
            }).addTo(map);
        }
        window.previewPacketPath = previewPacketPath;

        function clearPreviewPacketPath() {
            if (previewPathLayer) {
                map.removeLayer(previewPathLayer);
                previewPathLayer = null;
            }
        }
        window.clearPreviewPacketPath = clearPreviewPacketPath;

        window.closeAdsbTooltip = function(hex, e) {
            if (e) {
                if (e.stopPropagation) e.stopPropagation();
                if (e.preventDefault) e.preventDefault();
            }
            if (_adsbHoverCloseTimer) {
                clearTimeout(_adsbHoverCloseTimer);
                _adsbHoverCloseTimer = null;
            }
            if (window._map3dAdsbPopup) {
                try {
                    window._map3dAdsbPopup.remove();
                } catch(ex) {}
                window._map3dAdsbPopup = null;
            }
            var h = (hex || '').toLowerCase();
            if (pinnedTooltipHex === h || (pinnedTooltipHex && pinnedTooltipHex.toLowerCase() === h)) {
                pinnedTooltipHex = null;
            }
            if (_activeHoverHex === h) {
                _activeHoverHex = null;
            }
            for (var mHex in aircraftMarkers) {
                if (mHex.toLowerCase() === h) {
                    var m = aircraftMarkers[mHex];
                    m.closeTooltip();
                    m.setZIndexOffset(0);
                    if (m._icon) {
                        var inner = m._icon.querySelector('.adsb-plane-marker');
                        if (inner) inner.classList.remove('pinned');
                    }
                }
            }
        };

        map.on('click', function(e) {
            if (window._p2pMeasureActive) {
                if (!window._p2pStartPoint) {
                    window._p2pStartPoint = { lat: e.latlng.lat, lng: e.latlng.lng };
                    if (window._p2pStartMarker) map.removeLayer(window._p2pStartMarker);
                    window._p2pStartMarker = L.circleMarker([e.latlng.lat, e.latlng.lng], {
                        pane: 'p2pPane',
                        radius: 7,
                        color: '#FFFFFF',
                        weight: 2,
                        fillColor: '#38BDF8',
                        fillOpacity: 1.0
                    }).bindTooltip('Point A (Now click Point B to calculate profile)', { permanent: true, className: 'node-tooltip' }).addTo(map);
                    return;
                } else {
                    var ptB = { lat: e.latlng.lat, lng: e.latlng.lng };
                    var ptA = window._p2pStartPoint;
                    if (window._p2pStartMarker) {
                        map.removeLayer(window._p2pStartMarker);
                        window._p2pStartMarker = null;
                    }
                    window._p2pStartPoint = null;
                    if (window.setP2PMeasureMode) window.setP2PMeasureMode(false);
                    if (window.pyBridge && window.pyBridge.on_p2p_path_selected) {
                        window.pyBridge.on_p2p_path_selected(ptA.lat, ptA.lng, ptB.lat, ptB.lng, "Point A", "Point B");
                    }
                    return;
                }
            }

            // Guard: If user clicked inside ANY marker, tooltip, popup, cluster pill, or UI panel, do NOT close!
            if (e.originalEvent && e.originalEvent.target) {
                var tgt = e.originalEvent.target;
                if (tgt.closest && (
                    tgt.closest('.leaflet-marker-icon') ||
                    tgt.closest('.leaflet-tooltip') ||
                    tgt.closest('.adsb-tooltip') ||
                    tgt.closest('.adsb-icon-wrap') ||
                    tgt.closest('.adsb-plane-container') ||
                    tgt.closest('.adsb-plane-marker') ||
                    tgt.closest('.adsb-distress-tag') ||
                    tgt.closest('.adsb-distress-echo-ring') ||
                    tgt.closest('.adsb-cluster-pill') ||
                    tgt.closest('.adsb-panel') ||
                    tgt.closest('.leaflet-popup')
                )) {
                    return;
                }
            }

            // Check if user clicked within 24px of any aircraft marker (forgiving hit tolerance)
            var clickedPt = e.containerPoint;
            var nearestHex = null;
            var nearestDist = 24;
            for (var h in aircraftMarkers) {
                var m = aircraftMarkers[h];
                if (m && m.getLatLng) {
                    var mPt = map.latLngToContainerPoint(m.getLatLng());
                    var d = Math.sqrt(Math.pow(clickedPt.x - mPt.x, 2) + Math.pow(clickedPt.y - mPt.y, 2));
                    if (d < nearestDist) {
                        nearestDist = d;
                        nearestHex = h;
                    }
                }
            }

            if (nearestHex && window.openAircraftTooltip) {
                var nMarker = aircraftMarkers[nearestHex];
                window.openAircraftTooltip(nearestHex, nMarker, true);
                return;
            }

            if (_adsbHoverCloseTimer) {
                clearTimeout(_adsbHoverCloseTimer);
                _adsbHoverCloseTimer = null;
            }
            if (_activeHoverHex && aircraftMarkers && aircraftMarkers[_activeHoverHex]) {
                var prevHover = aircraftMarkers[_activeHoverHex];
                try { prevHover.closeTooltip(); } catch (e) {}
                try { prevHover.setZIndexOffset(0); } catch (e) {}
                _activeHoverHex = null;
            }

            if (pinnedTooltipHex && aircraftMarkers && aircraftMarkers[pinnedTooltipHex]) {
                var prevMarker = aircraftMarkers[pinnedTooltipHex];
                try { prevMarker.closeTooltip(); } catch (e) {}
                try { prevMarker.setZIndexOffset(0); } catch (e) {}
                if (prevMarker._icon) {
                    var inner = prevMarker._icon.querySelector('.adsb-plane-marker');
                    if (inner) inner.classList.remove('pinned');
                }
                pinnedTooltipHex = null;
            }
        });

        // Hover grace period handling for ADS-B tooltips:
        // When mouse moves from an aircraft marker into the tooltip, keep the tooltip open.
        document.addEventListener('mouseover', function(e) {
            var tip = e.target && e.target.closest ? e.target.closest('.adsb-tooltip') : null;
            if (tip) {
                if (_adsbHoverCloseTimer) {
                    clearTimeout(_adsbHoverCloseTimer);
                    _adsbHoverCloseTimer = null;
                }
            }
        }, true);

        document.addEventListener('mouseout', function(e) {
            var tip = e.target && e.target.closest ? e.target.closest('.adsb-tooltip') : null;
            if (tip) {
                var related = e.relatedTarget && e.relatedTarget.closest ? e.relatedTarget.closest('.adsb-tooltip') : null;
                if (related === tip) {
                    return;
                }
                if (!pinnedTooltipHex && _activeHoverHex) {
                    if (_adsbHoverCloseTimer) clearTimeout(_adsbHoverCloseTimer);
                    _adsbHoverCloseTimer = setTimeout(function() {
                        if (!pinnedTooltipHex && _activeHoverHex) {
                            var m = aircraftMarkers[_activeHoverHex];
                            if (m) {
                                try { m.closeTooltip(); } catch (e) {}
                                try { m.setZIndexOffset(0); } catch (e) {}
                            }
                            _activeHoverHex = null;
                        }
                        _adsbHoverCloseTimer = null;
                    }, 800);
                }
            }
        }, true);

        // Prevent clicks and mousedowns inside .adsb-tooltip from propagating to Leaflet map canvas
        document.addEventListener('click', function(e) {
            if (e.target && e.target.closest && e.target.closest('.adsb-tooltip')) {
                // If it is the close button '✕', let closeAdsbTooltip handle it
                if (e.target.classList && e.target.classList.contains('adsb-tip-close')) {
                    return;
                }
                e.stopPropagation();
            }
        }, true);

        document.addEventListener('mousedown', function(e) {
            if (e.target && e.target.closest && e.target.closest('.adsb-tooltip')) {
                e.stopPropagation();
            }
        }, true);

        // Intercept right-click and suppress default Chromium browser context menu
        var _lastContextMenuTime = 0;
        function triggerMapContextMenu(lat, lon, x, y) {
            var now = Date.now();
            if (now - _lastContextMenuTime < 250) return;
            _lastContextMenuTime = now;
            if (window.pyBridge && window.pyBridge.on_map_context_menu) {
                try {
                    window.pyBridge.on_map_context_menu(
                        lat,
                        lon,
                        Math.round(x),
                        Math.round(y)
                    );
                } catch (err) {
                    console.error("Context menu error:", err);
                }
            }
        }

        document.addEventListener('contextmenu', function(e) {
            e.preventDefault();
        }, false);

        map.on('contextmenu', function(e) {
            if (e.originalEvent) {
                e.originalEvent.preventDefault();
                e.originalEvent.stopPropagation();
            }
            triggerMapContextMenu(e.latlng.lat, e.latlng.lng, e.containerPoint.x, e.containerPoint.y);
        });

        window._tempPinMarker = null;
        window.dropTemporaryPin = function(lat, lon, label) {
            if (window._tempPinMarker) {
                map.removeLayer(window._tempPinMarker);
                window._tempPinMarker = null;
            }
            var text = label || "Waypoint";
            window._tempPinMarker = L.marker([lat, lon], {
                icon: L.divIcon({
                    className: 'custom-temp-pin',
                    html: '<div style="display:inline-flex; align-items:center; gap:4px; background:rgba(239, 68, 68, 0.92); color:#FFFFFF; border:1.5px solid #FFFFFF; border-radius:12px; padding:3px 8px; font-size:11px; font-weight:700; box-shadow:0 3px 10px rgba(0,0,0,0.6); white-space:nowrap;">📍 ' + text + '</div>',
                    iconSize: [100, 26],
                    iconAnchor: [50, 13]
                })
            }).addTo(map);
        };

        window.clearTemporaryPin = function() {
            if (window._tempPinMarker) {
                map.removeLayer(window._tempPinMarker);
                window._tempPinMarker = null;
            }
        };

        // ==========================================
        // RF Line-of-Sight (LOS) & Topographic Viewshed Overlays
        // ==========================================
        var currentViewshedLayer = null;
        var viewshedCenterCircle = null;
        var losCenterCoords = null;
        var losRadiusM = 25000;

        function updateLosGradient() {
            if (!losCenterCoords) return;
            var losPane = map.getPane('losPane');
            if (!losPane) return;
            var gradEl = losPane.querySelector('#losRadialGrad');
            if (!gradEl) return;
            var centerPt = map.latLngToLayerPoint(losCenterCoords);
            var latOffset = (losRadiusM || 25000) / 111320.0;
            var edgePt = map.latLngToLayerPoint([losCenterCoords[0] + latOffset, losCenterCoords[1]]);
            var rPx = Math.max(10, centerPt.distanceTo(edgePt));
            gradEl.setAttribute('cx', centerPt.x);
            gradEl.setAttribute('cy', centerPt.y);
            gradEl.setAttribute('r', rPx);
        }

        function clearViewshedOverlay() {
            map.off('move zoom viewreset', updateLosGradient);
            losCenterCoords = null;
            if (currentViewshedLayer !== null) {
                map.removeLayer(currentViewshedLayer);
                currentViewshedLayer = null;
            }
            if (viewshedCenterCircle !== null) {
                map.removeLayer(viewshedCenterCircle);
                viewshedCenterCircle = null;
            }
            var losPane = map.getPane('losPane');
            if (losPane) {
                var defs = losPane.querySelector('#los-defs');
                if (defs) defs.remove();
            }
            window._lastViewshedPayload = null;
            if (window._is3DActive && typeof syncLosTo3D === 'function') {
                syncLosTo3D();
            }
        }
        window.clearViewshedOverlay = clearViewshedOverlay;

        function renderViewshedOverlay(payload) {
            clearViewshedOverlay();
            if (!payload) return;
            try {
                // 1. Add 2D coverage raster overlay via L.imageOverlay
                if (payload.image_data_url && payload.bounds) {
                    currentViewshedLayer = L.imageOverlay(payload.image_data_url, payload.bounds, {
                        pane: 'losPane',
                        opacity: 0.88,
                        interactive: false
                    }).addTo(map);
                } else if (payload.geojson) {
                    // Fallback to vector geojson if raster is absent
                    currentViewshedLayer = L.geoJSON(payload.geojson, {
                        pane: 'losPane',
                        style: {
                            stroke: false,
                            weight: 0,
                            fill: true,
                            fillColor: '#10B981',
                            fillOpacity: 0.55
                        }
                    }).addTo(map);
                }

                // 2. Anchor pulse emitter marker to transmitter coordinates
                if (payload.center_lat !== undefined && payload.center_lon !== undefined) {
                    var pulseIcon = L.divIcon({
                        className: 'los-beacon-icon-wrap',
                        html: '<div style="position:relative; width:26px; height:26px; display:flex; align-items:center; justify-content:center;">' +
                              '<div style="position:absolute; width:24px; height:24px; border-radius:50%; border:2px solid #10B981; animation:losBeaconPulse 2s infinite ease-out;"></div>' +
                              '<div style="width:10px; height:10px; border-radius:50%; background:#10B981; border:2px solid #FFFFFF; box-shadow:0 0 10px #10B981;"></div>' +
                              '</div>',
                        iconSize: [26, 26],
                        iconAnchor: [13, 13]
                    });
                    viewshedCenterCircle = L.marker([payload.center_lat, payload.center_lon], {
                        pane: 'p2pPane',
                        icon: pulseIcon
                    }).bindTooltip('LOS Emitter: ' + escapeHtml(payload.observer_alias || 'Observer') + ' (' + (payload.tx_height_m || 8) + 'm AGL)', { permanent: false, className: 'node-tooltip' }).addTo(map);
                }

                window._lastViewshedPayload = payload;
                if (window._is3DActive && typeof syncLosTo3D === 'function') {
                    syncLosTo3D();
                }
            } catch (err) {
                console.error('Error rendering viewshed coverage overlay:', err);
            }
        }
        window.renderViewshedOverlay = renderViewshedOverlay;

        // ==========================================
        // Point-to-Point Topographic Profile & Fresnel Zone
        // ==========================================
        var currentP2PLine = null;
        var currentP2PMarkers = [];
        var currentP2PScrubMarker = null;

        function clearP2PLine() {
            if (currentP2PLine !== null) {
                map.removeLayer(currentP2PLine);
                currentP2PLine = null;
            }
            for (var i = 0; i < currentP2PMarkers.length; i++) {
                map.removeLayer(currentP2PMarkers[i]);
            }
            currentP2PMarkers = [];
            clearP2PScrubMarker();
        }
        window.clearP2PLine = clearP2PLine;

        function renderP2PLine(lat1, lon1, lat2, lon2, status, alias1, alias2) {
            clearP2PLine();
            var color = '#38BDF8';
            if (status === 'FRESNEL_INCURSION') color = '#FBBF24';
            if (status === 'OBSTRUCTED') color = '#EF4444';

            currentP2PLine = L.polyline([[lat1, lon1], [lat2, lon2]], {
                pane: 'p2pPane',
                color: color,
                weight: 3.5,
                opacity: 0.85,
                dashArray: (status === 'OBSTRUCTED' ? '6, 6' : null)
            }).addTo(map);

            var m1 = L.circleMarker([lat1, lon1], {
                pane: 'p2pPane',
                radius: 6,
                color: '#FFFFFF',
                weight: 2,
                fillColor: '#38BDF8',
                fillOpacity: 1.0
            }).bindTooltip(escapeHtml(alias1 || 'Tx Point'), { permanent: false, className: 'node-tooltip' }).addTo(map);

            var m2 = L.circleMarker([lat2, lon2], {
                pane: 'p2pPane',
                radius: 6,
                color: '#FFFFFF',
                weight: 2,
                fillColor: color,
                fillOpacity: 1.0
            }).bindTooltip(escapeHtml(alias2 || 'Rx Point'), { permanent: false, className: 'node-tooltip' }).addTo(map);

            currentP2PMarkers = [m1, m2];
        }
        window.renderP2PLine = renderP2PLine;

        function updateP2PScrubMarker(lat, lon) {
            if (!currentP2PScrubMarker) {
                currentP2PScrubMarker = L.circleMarker([lat, lon], {
                    pane: 'p2pPane',
                    radius: 7,
                    color: '#FBBF24',
                    weight: 2.5,
                    fillColor: '#FFFFFF',
                    fillOpacity: 0.95
                }).addTo(map);
            } else {
                currentP2PScrubMarker.setLatLng([lat, lon]);
            }
        }
        window.updateP2PScrubMarker = updateP2PScrubMarker;

        function clearP2PScrubMarker() {
            if (currentP2PScrubMarker) {
                map.removeLayer(currentP2PScrubMarker);
                currentP2PScrubMarker = null;
            }
        }
        window.clearP2PScrubMarker = clearP2PScrubMarker;

        window._p2pMeasureActive = false;
        window._p2pStartPoint = null;
        window._p2pStartMarker = null;

        function setP2PMeasureMode(active) {
            window._p2pMeasureActive = !!active;
            window._p2pStartPoint = null;
            if (window._p2pStartMarker) {
                map.removeLayer(window._p2pStartMarker);
                window._p2pStartMarker = null;
            }
            if (active) {
                map.getContainer().style.cursor = 'crosshair';
            } else {
                map.getContainer().style.cursor = '';
            }
        }
        window.setP2PMeasureMode = setP2PMeasureMode;

        window.onProfileNodeClicked = function(btn) {
            if (window.pyBridge && window.pyBridge.on_profile_node_requested) {
                window.pyBridge.on_profile_node_requested(
                    decodeURIComponent(btn.dataset.nid),
                    decodeURIComponent(btn.dataset.alias),
                    parseFloat(btn.dataset.lat),
                    parseFloat(btn.dataset.lon)
                );
            }
        };

        window.onCalcViewshedClicked = function(btn) {
            if (window.pyBridge && window.pyBridge.on_calc_node_viewshed_requested) {
                window.pyBridge.on_calc_node_viewshed_requested(
                    decodeURIComponent(btn.dataset.nid),
                    decodeURIComponent(btn.dataset.alias),
                    parseFloat(btn.dataset.lat),
                    parseFloat(btn.dataset.lon)
                );
            }
        };

        function getRadarCircleBounds(lat, lon, radiusNm) {
            var dLat = radiusNm / 60.0;
            var latRad = lat * Math.PI / 180.0;
            var cosLat = Math.cos(latRad);
            var dLon = radiusNm / (60.0 * Math.max(0.1, cosLat));
            return L.latLngBounds([lat - dLat, lon - dLon], [lat + dLat, lon + dLon]);
        }

        function getAircraftSvgPath(category) {
            switch(category) {
                case 'airliner':
                    return '<path d="M12 2c-.6 0-1.1.5-1.1 1.4v5.3L3.2 12.8v2.1l7.7-2.3v5.2l-2.2 1.6v1.4l3.3-.9 3.3.9v-1.4l-2.2-1.6v-5.2l7.7 2.3v-2.1L13.1 8.7V3.4c0-.9-.5-1.4-1.1-1.4z"/>';
                case 'light':
                    return '<path d="M12 1.5c-.8 0-1.2.7-1.2 1.8v3.2L1.5 8.2v2.3l9.3-.8v6l-2.7 1.6v1.4l3.1-.7 3.1.7v-1.4l-2.7-1.6v-6l9.3.8V8.2l-9.3-1.7V3.3c0-1.1-.4-1.8-1.2-1.8z"/>';
                case 'military':
                    return '<path d="M12 1L13.8 6.5L21.5 13.5L19.5 15.5L14 13L14 17.5L16.5 21L15 22.2L12 20.8L9 22.2L7.5 21L10 17.5L10 13L4.5 15.5L2.5 13.5L10.2 6.5Z"/>';
                case 'helicopter':
                    return '<path d="M11 1.5h2v1.8h-2z M2 3.3h20v1.4H13v1.5c2.3.4 4.2 2.3 4.2 5.1 0 2.1-1.1 3.8-2.8 4.6L14 18.2h3.2v1.5H6.8v-1.5H10l-.4-3.4c-2.4-.6-3.8-2.4-3.8-4.7 0-2.8 1.9-4.7 4.2-5.1V4.7H2z M17.2 17.5h2v-2.8h-2z"/>';
                case 'glider':
                    return '<path d="M11.5 1c-.6 0-1 .6-1 1.8v3.5L1 8v1.3l9.5-.7v9.8l-2.4 1.1v1.1l3.9-.6 3.9.6v-1.1l-2.4-1.1V8.6l9.5.7V8l-9.5-1.7V2.8c0-1.2-.4-1.8-1-1.8z"/>';
                default:
                    return '<path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>';
            }
        }

        function getAircraftCategoryBadge(category) {
            switch(category) {
                case 'airliner': return '✈️ Airliner';
                case 'light': return '🛩️ Light Aircraft';
                case 'military': return '⚔️ Military';
                case 'helicopter': return '🚁 Helicopter';
                case 'glider': return '🪂 Glider';
                default: return '✈️ General';
            }
        }

        window._adsbColorMode = 'altitude';
        window._adsbColors = {
            alt_ground: '#FF00FF',
            alt_low: '#FF0000',
            alt_mid: '#0000FF',
            alt_high: '#FFFFFF',
            type_airliner: '#FFFFFF',
            type_light: '#0000FF',
            type_military: '#00FF00',
            type_helicopter: '#FFFF00',
            type_glider: '#FF00FF',
            dist_close: '#FF0000',
            dist_mid_close: '#FFA500',
            dist_mid_far: '#FFFF00',
            dist_far: '#00FF00'
        };
        window._aircraftPhotos = {};
        var currentAircraftData = {};

        window._adsbFilters = {
            airliner: true,
            light: true,
            military: true,
            helicopter: true,
            glider: true,
            general: true
        };

        window._adsbAlertConfig = {
            enabled: true,
            radiusMi: 10.0,
            categories: {
                military: true,
                helicopter: false,
                light: false,
                airliner: false,
                glider: false,
                general: false
            }
        };

        window._adsbAlertedHexes = {};

        window.isAircraftCategoryVisible = function(category) {
            var cat = (category || 'general').toLowerCase();
            return window._adsbFilters[cat] !== false;
        };

        window.updateAdsbFilters = function() {
            var cats = ['airliner', 'light', 'military', 'helicopter', 'glider', 'general'];
            for (var i = 0; i < cats.length; i++) {
                var chk = document.getElementById('adsb-flt-' + cats[i]);
                if (chk) {
                    window._adsbFilters[cats[i]] = chk.checked;
                }
            }

            var visibleCount = 0;
            var total = 0;
            for (var h in currentAircraftData) {
                total++;
                var p = currentAircraftData[h];
                var m = aircraftMarkers[h];
                var isVis = p ? window.isAircraftCategoryVisible(p.category) : true;
                if (isVis) visibleCount++;
                if (m) {
                    if (isVis) {
                        m.setOpacity(1);
                        if (m._icon) m._icon.style.display = '';
                        if (aircraftTrails[h]) aircraftTrails[h].setStyle({ opacity: 0.65 });
                    } else {
                        m.setOpacity(0);
                        if (m._icon) m._icon.style.display = 'none';
                        if (aircraftTrails[h]) aircraftTrails[h].setStyle({ opacity: 0 });
                        if (pinnedTooltipHex === h) {
                            m.closeTooltip();
                            pinnedTooltipHex = null;
                        }
                    }
                }
            }

            if (window._is3DActive && typeof syncAdsbTo3D === 'function') {
                syncAdsbTo3D();
            }

            var countBadge = document.getElementById('adsb-count-badge');
            if (countBadge) {
                countBadge.textContent = visibleCount + (visibleCount !== total ? ' / ' + total : '');
            }

            if (window.pyBridge && window.pyBridge.on_adsb_filters_changed) {
                window.pyBridge.on_adsb_filters_changed(JSON.stringify(window._adsbFilters));
            }
        };

        window.setAllAdsbFilters = function(allActive) {
            var cats = ['airliner', 'light', 'military', 'helicopter', 'glider', 'general'];
            for (var i = 0; i < cats.length; i++) {
                var chk = document.getElementById('adsb-flt-' + cats[i]);
                if (chk) chk.checked = !!allActive;
            }
            window.updateAdsbFilters();
        };

        window.updateAdsbAlertConfig = function() {
            var enChk = document.getElementById('adsb-alert-enabled');
            if (enChk) window._adsbAlertConfig.enabled = enChk.checked;

            var cats = ['military', 'helicopter', 'light', 'airliner', 'glider', 'general'];
            for (var i = 0; i < cats.length; i++) {
                var chk = document.getElementById('adsb-alert-' + cats[i]);
                if (chk) {
                    window._adsbAlertConfig.categories[cats[i]] = chk.checked;
                }
            }

            if (window.pyBridge && window.pyBridge.on_adsb_alert_config_changed) {
                window.pyBridge.on_adsb_alert_config_changed(JSON.stringify(window._adsbAlertConfig));
            }
        };

        window.setInitialAdsbConfig = function(filterCats, alertEn, alertCats, alertRad) {
            if (Array.isArray(filterCats)) {
                var catSet = {};
                for (var f = 0; f < filterCats.length; f++) catSet[filterCats[f].toLowerCase()] = true;
                var allCats = ['airliner', 'light', 'military', 'helicopter', 'glider', 'general'];
                for (var i = 0; i < allCats.length; i++) {
                    var act = !!catSet[allCats[i]];
                    window._adsbFilters[allCats[i]] = act;
                    var chk = document.getElementById('adsb-flt-' + allCats[i]);
                    if (chk) chk.checked = act;
                }
            }
            if (typeof alertEn === 'boolean') {
                window._adsbAlertConfig.enabled = alertEn;
                var enChk = document.getElementById('adsb-alert-enabled');
                if (enChk) enChk.checked = alertEn;
            }
            if (Array.isArray(alertCats)) {
                var aSet = {};
                for (var a = 0; a < alertCats.length; a++) aSet[alertCats[a].toLowerCase()] = true;
                var allCats2 = ['military', 'helicopter', 'light', 'airliner', 'glider', 'general'];
                for (var j = 0; j < allCats2.length; j++) {
                    var aAct = !!aSet[allCats2[j]];
                    window._adsbAlertConfig.categories[allCats2[j]] = aAct;
                    var achk = document.getElementById('adsb-alert-' + allCats2[j]);
                    if (achk) achk.checked = aAct;
                }
            }
            if (typeof alertRad === 'number' && alertRad > 0) {
                window._adsbAlertConfig.radiusMi = alertRad;
            }
        };

        function renderAdsbLegend() {
            var container = document.getElementById('adsb-legend-container');
            if (!container) return;
            var mode = window._adsbColorMode || 'altitude';
            var c = window._adsbColors;

            var distressItem = '<span class="adsb-legend-item"><span class="adsb-legend-dot adsb-legend-distress-dot"></span>Distress</span>';

            if (mode === 'altitude') {
                container.innerHTML = 
                    '<div class="adsb-legend-row">' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.alt_ground + ';"></span>&lt;2k ft</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.alt_low + ';"></span>&lt;7k ft</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.alt_mid + ';"></span>10-25k</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.alt_high + ';"></span>&gt;25k</span>' +
                    '  ' + distressItem +
                    '</div>';
            } else if (mode === 'type') {
                container.innerHTML = 
                    '<div class="adsb-legend-row" style="font-size: 8px;">' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.type_airliner + ';"></span>Airliner</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.type_light + ';"></span>Light</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.type_military + ';"></span>Military</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.type_helicopter + ';"></span>Heli</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.type_glider + ';"></span>Glider</span>' +
                    '  ' + distressItem +
                    '</div>';
            } else if (mode === 'distance') {
                container.innerHTML = 
                    '<div class="adsb-legend-row">' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.dist_close + ';"></span>Close</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.dist_mid_close + ';"></span>Mid-Close</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.dist_mid_far + ';"></span>Mid-Far</span>' +
                    '  <span class="adsb-legend-item"><span class="adsb-legend-dot" style="background:' + c.dist_far + ';"></span>Far</span>' +
                    '  ' + distressItem +
                    '</div>';
            }
        }

        function recolorAllAircraft() {
            for (var hex in aircraftMarkers) {
                var marker = aircraftMarkers[hex];
                if (marker && marker._planeData) {
                    var iconData = createAirplaneIcon(marker._planeData);
                    marker._lastColor = iconData.color;
                    var distress = getAircraftDistressInfo(marker._planeData);
                    var distressKey = distress ? distress.code : '';
                    if (marker._lastDistress !== distressKey) {
                        marker._lastDistress = distressKey;
                        marker.setIcon(iconData.icon);
                        if (marker._icon) {
                            L.DomEvent.disableClickPropagation(marker._icon);
                            L.DomEvent.disableScrollPropagation(marker._icon);
                        }
                    } else if (marker._icon) {
                        var svg = marker._icon.querySelector('svg');
                        if (svg) svg.setAttribute('fill', iconData.color);
                    }
                }
            }
            for (var hex in aircraftTrails) {
                var trailLine = aircraftTrails[hex];
                var plane = currentAircraftData[hex];
                if (trailLine && plane) {
                    var newCol = getAircraftColor(plane);
                    if (trailLine.setStyle) {
                        trailLine.setStyle({ color: newCol });
                    } else if (trailLine.eachLayer) {
                        trailLine.eachLayer(function(layer) {
                            if (layer.setStyle) layer.setStyle({ color: newCol });
                        });
                    }
                }
            }
        }

        window.setAdsbColorMode = function(mode, fromPython) {
            window._adsbColorMode = mode || 'altitude';
            var pills = ['alt', 'type', 'dist'];
            var modeKeys = { 'altitude': 'alt', 'type': 'type', 'distance': 'dist' };
            var activeKey = modeKeys[window._adsbColorMode] || 'alt';
            for (var i = 0; i < pills.length; i++) {
                var el = document.getElementById('adsb-mode-' + pills[i]);
                if (el) {
                    if (pills[i] === activeKey) el.classList.add('active');
                    else el.classList.remove('active');
                }
            }
            renderAdsbLegend();
            recolorAllAircraft();
            if (!fromPython && window.pyBridge && window.pyBridge.on_adsb_color_mode_changed) {
                window.pyBridge.on_adsb_color_mode_changed(window._adsbColorMode);
            }
        };

        window.setAdsbColorConfig = function(mode, colors) {
            if (colors) window._adsbColors = Object.assign(window._adsbColors, colors);
            window.setAdsbColorMode(mode || window._adsbColorMode, true);
        };

        function getOverlappingAircraft(targetPlane, pixelRadius) {
            var radius = pixelRadius || 26;
            if (!targetPlane || typeof targetPlane.lat !== 'number' || typeof targetPlane.lon !== 'number') return [];
            var tHex = (targetPlane.hex || '').toLowerCase();
            var targetPt = _aircraftContainerPoints[tHex];
            if (!targetPt) {
                targetPt = map.latLngToContainerPoint(L.latLng(targetPlane.lat, targetPlane.lon));
                _aircraftContainerPoints[tHex] = targetPt;
            }
            var cluster = [];
            for (var h in currentAircraftData) {
                var other = currentAircraftData[h];
                if (other && typeof other.lat === 'number' && typeof other.lon === 'number') {
                    var otherPt = _aircraftContainerPoints[h];
                    if (!otherPt) {
                        otherPt = map.latLngToContainerPoint(L.latLng(other.lat, other.lon));
                        _aircraftContainerPoints[h] = otherPt;
                    }
                    var dx = targetPt.x - otherPt.x;
                    var dy = targetPt.y - otherPt.y;
                    if (Math.sqrt(dx * dx + dy * dy) <= radius) {
                        cluster.push(other);
                    }
                }
            }
            return cluster;
        }

        function buildAircraftTooltipHtml(plane, clusterList) {
            var hexCode = escapeHtml(plane.hex || '').toUpperCase();
            var flightTitle = escapeHtml(plane.flight || hexCode);
            var planeType = escapeHtml(plane.t || 'Unknown');
            var reg = escapeHtml(plane.r || 'N/A');
            var altStr = (plane.alt_baro !== null && plane.alt_baro !== undefined) ? (plane.alt_baro.toLocaleString() + ' ft') : 'Ground';
            var spdStr = plane.gs ? (Math.round(plane.gs) + ' kts') : 'N/A';
            var distStr = (plane.dst !== null && plane.dst !== undefined) ? (plane.dst + ' NM') : '';
            var squawkStr = plane.squawk ? escapeHtml(plane.squawk) : '';
            var catBadge = getAircraftCategoryBadge(plane.category || 'general');
            var flightParam = encodeURIComponent(plane.flight || hexCode);
            var hexParam = encodeURIComponent(hexCode);

            var clusterBarHtml = '';
            if (clusterList && clusterList.length > 1) {
                var pills = '';
                for (var ci = 0; ci < clusterList.length; ci++) {
                    var cp = clusterList[ci];
                    var cHex = escapeHtml(cp.hex || '').toUpperCase();
                    var cLabel = escapeHtml(cp.flight || cHex);
                    var isActive = (cHex === hexCode);
                    pills += '<span class="adsb-cluster-pill' + (isActive ? ' active' : '') + '" title="Select ' + cLabel + ' (' + cHex + ')" onclick="selectAdsbAircraft(&quot;' + cHex + '&quot;, event)">' + cLabel + '</span>';
                }
                clusterBarHtml = '<div class="adsb-cluster-bar"><span style="color:#9CA3AF; margin-right:2px; font-weight: 500;">Stacked (' + clusterList.length + '):</span>' + pills + '</div>';
            }

            var photoCached = window._aircraftPhotos[hexCode] || window._aircraftPhotos[hexCode.toLowerCase()];
            var photoInnerHtml = '';
            if (photoCached && photoCached.thumbnail) {
                var thumb = escapeHtml(photoCached.thumbnail);
                var photog = escapeHtml(photoCached.photographer || 'Aircraft Photo');
                var pLink = escapeHtml(photoCached.link || ('https://globe.adsbexchange.com/?icao=' + hexParam));
                var srcLabel = escapeHtml(photoCached.source_label || (photoCached.source ? photoCached.source + ' ↗' : 'Photo ↗'));
                photoInnerHtml = '<div style="margin-top: 4px; border-top: 1px solid #374151; padding-top: 4px;">' +
                                 '  <img src="' + thumb + '" class="adsb-photo-img" alt="Aircraft photo" />' +
                                 '  <div class="adsb-photo-meta">' +
                                 '    <span>© ' + photog + '</span>' +
                                 '    <a href="' + pLink + '" class="adsb-photo-link" onclick="if(window.pyBridge&&window.pyBridge.on_open_external_url){window.pyBridge.on_open_external_url(this.href);return false;}">' + srcLabel + '</a>' +
                                 '  </div>' +
                                 '</div>';
            } else {
                photoInnerHtml = '<div class="adsb-photo-placeholder">' +
                                 '  <div class="adsb-photo-placeholder-icon">📷</div>' +
                                 '  <div class="adsb-photo-placeholder-text">No aircraft photo available</div>' +
                                 '</div>';
            }

            var distress = getAircraftDistressInfo(plane);
            var distressBannerHtml = '';
            if (distress) {
                distressBannerHtml = '<div class="adsb-distress-banner">' +
                                     '  <span class="adsb-distress-beacon-dot"></span>' +
                                     '  <span>DISTRESS BEACON: <b>' + escapeHtml(distress.label) + '</b></span>' +
                                     '</div>';
            }
            var squawkStr = plane.squawk ? escapeHtml(plane.squawk) : '';
            var squawkDisplay = squawkStr;
            if (distress) {
                squawkDisplay = '<b style="color: #EF4444;">' + squawkStr + ' (' + escapeHtml(distress.shortDesc) + ')</b>';
            }

            return '<div style="line-height: 1.35; min-width: 205px;">' +
                   clusterBarHtml +
                   distressBannerHtml +
                   '  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">' +
                   '    <div><span class="adsb-cat-badge">' + catBadge + '</span> <b>' + flightTitle + '</b> <span style="font-weight: normal; color: #9CA3AF; font-size: 9.5px;">(' + planeType + ')</span></div>' +
                   '    <span class="adsb-tip-close" onclick="closeAdsbTooltip(&quot;' + hexCode + '&quot;, event)" title="Close">✕</span>' +
                   '  </div>' +
                   '  <div style="color: #E5E7EB; font-size: 10px; margin-bottom: 2px;">' + altStr + ' • ' + spdStr + (distStr ? ' • ' + distStr : '') + '</div>' +
                   '  <div style="color: #9CA3AF; font-size: 9px; margin-bottom: 4px;">Reg: ' + reg + ' • Hdg: ' + Math.round(plane.track || 0) + '°' + (squawkDisplay ? ' • Sq: ' + squawkDisplay : '') + '</div>' +
                   '  <div id="adsb-photo-box-' + hexCode + '" class="adsb-photo-box" style="display: block;">' + photoInnerHtml + '</div>' +
                   '  <div style="border-top: 1px solid #374151; padding-top: 4px; margin-top: 4px; display: flex; flex-direction: column; gap: 3px;">' +
                   '    <a href="https://globe.adsbexchange.com/?icao=' + hexParam + '" class="adsb-tip-link" onclick="if(window.pyBridge&&window.pyBridge.on_open_external_url){window.pyBridge.on_open_external_url(this.href);return false;}">🌐 ADS-B Exchange ↗</a>' +
                   '    <div style="display: flex; gap: 8px;">' +
                   '      <a href="https://www.flightradar24.com/' + flightParam + '" class="adsb-tip-link" onclick="if(window.pyBridge&&window.pyBridge.on_open_external_url){window.pyBridge.on_open_external_url(this.href);return false;}">✈️ Flightradar24 ↗</a>' +
                   '      <a href="https://www.flightaware.com/live/modes/' + hexParam + '/redirect" class="adsb-tip-link" onclick="if(window.pyBridge&&window.pyBridge.on_open_external_url){window.pyBridge.on_open_external_url(this.href);return false;}">📡 FlightAware ↗</a>' +
                   '    </div>' +
                   '  </div>' +
                   '</div>';
        }

        window.openAircraftTooltip = function(h, marker, isPinned) {
            if (!h) return;
            var lowHex = h.toLowerCase();
            if (!marker) marker = aircraftMarkers[lowHex];
            var plane = currentAircraftData[lowHex];
            if (!plane) return;

            if (!window._aircraftPhotos) window._aircraftPhotos = {};
            if (!window._aircraftPhotos[lowHex] && window.pyBridge && window.pyBridge.request_aircraft_photo) {
                window._aircraftPhotos[lowHex] = { loading: true };
                window.pyBridge.request_aircraft_photo(lowHex.toUpperCase());
            }

            var cluster = getOverlappingAircraft(plane, 26);
            var tipHtml = buildAircraftTooltipHtml(plane, cluster);

            // 3D Map Popup
            if (window._is3DActive && map3d) {
                if (window._map3dAdsbPopup) {
                    window._map3dAdsbPopup.remove();
                    window._map3dAdsbPopup = null;
                }
                window._map3dAdsbPopup = new maplibregl.Popup({
                    className: 'adsb-3d-popup',
                    closeButton: false,
                    closeOnClick: false,
                    offset: 20,
                    maxWidth: '340px'
                })
                .setLngLat([plane.lon, plane.lat])
                .setHTML(tipHtml)
                .addTo(map3d);
            }

            if (!marker) return;

            // Unpin previous pinned marker if different
            if (pinnedTooltipHex && pinnedTooltipHex !== lowHex && aircraftMarkers[pinnedTooltipHex]) {
                var prev = aircraftMarkers[pinnedTooltipHex];
                prev.closeTooltip();
                prev.setZIndexOffset(0);
                if (prev._icon) {
                    var prevInner = prev._icon.querySelector('.adsb-plane-marker');
                    if (prevInner) prevInner.classList.remove('pinned');
                }
            }

            // If an unpinned hover tooltip is open on another plane, close it
            if (!isPinned && _activeHoverHex && _activeHoverHex !== lowHex && aircraftMarkers[_activeHoverHex]) {
                var prevH = aircraftMarkers[_activeHoverHex];
                prevH.closeTooltip();
                prevH.setZIndexOffset(0);
            }

            marker.setTooltipContent(tipHtml);
            marker.setZIndexOffset(10000);

            if (marker._icon) {
                var inner = marker._icon.querySelector('.adsb-plane-marker');
                if (inner) {
                    if (isPinned) inner.classList.add('pinned');
                    else inner.classList.remove('pinned');
                }
            }

            if (isPinned) {
                pinnedTooltipHex = lowHex;
                _activeHoverHex = null;
                if (_adsbHoverCloseTimer) {
                    clearTimeout(_adsbHoverCloseTimer);
                    _adsbHoverCloseTimer = null;
                }
            } else {
                _activeHoverHex = lowHex;
            }
            marker.openTooltip();

            var tip = marker.getTooltip();
            if (tip && tip._container) {
                L.DomEvent.disableClickPropagation(tip._container);
                L.DomEvent.disableScrollPropagation(tip._container);
            }
        };

        window.selectAdsbAircraft = function(hex, e) {
            if (e) {
                if (e.stopPropagation) e.stopPropagation();
                if (e.preventDefault) e.preventDefault();
            }
            if (_adsbHoverCloseTimer) {
                clearTimeout(_adsbHoverCloseTimer);
                _adsbHoverCloseTimer = null;
            }
            if (!hex) return;
            var h = hex.toLowerCase();
            var marker = aircraftMarkers[h];
            if (marker) {
                window.openAircraftTooltip(h, marker, true);
            }
        };

        window.onAircraftPhotoReady = function(hex, photoInfo) {
            if (!hex) return;
            var h = hex.toUpperCase();
            var lowH = hex.toLowerCase();
            window._aircraftPhotos[h] = photoInfo;
            window._aircraftPhotos[lowH] = photoInfo;

            if (window._is3DActive && map3d && window._map3dAdsbPopup) {
                var p3d = currentAircraftData[lowH];
                if (p3d) {
                    var cl3d = getOverlappingAircraft(p3d, 26);
                    window._map3dAdsbPopup.setHTML(buildAircraftTooltipHtml(p3d, cl3d));
                }
            }

            var box = document.getElementById('adsb-photo-box-' + h);
            if (box) {
                box.style.display = 'block';
                if (photoInfo && photoInfo.thumbnail) {
                    var thumb = escapeHtml(photoInfo.thumbnail);
                    var photog = escapeHtml(photoInfo.photographer || 'Aircraft Photo');
                    var link = escapeHtml(photoInfo.link || ('https://globe.adsbexchange.com/?icao=' + h));
                    var srcLabel = escapeHtml(photoInfo.source_label || (photoInfo.source ? photoInfo.source + ' ↗' : 'Photo ↗'));
                    box.innerHTML = 
                        '<div style="margin-top: 4px; border-top: 1px solid #374151; padding-top: 4px;">' +
                        '  <img src="' + thumb + '" class="adsb-photo-img" alt="Aircraft photo" />' +
                        '  <div class="adsb-photo-meta">' +
                        '    <span>© ' + photog + '</span>' +
                        '    <a href="' + link + '" class="adsb-photo-link" onclick="if(window.pyBridge&&window.pyBridge.on_open_external_url){window.pyBridge.on_open_external_url(this.href);return false;}">' + srcLabel + '</a>' +
                        '  </div>' +
                        '</div>';
                } else {
                    box.innerHTML = 
                        '<div class="adsb-photo-placeholder">' +
                        '  <div class="adsb-photo-placeholder-icon">📷</div>' +
                        '  <div class="adsb-photo-placeholder-text">No aircraft photo available</div>' +
                        '</div>';
                }
            }

            // Update marker tooltip content so subsequent hovers/clicks show photo immediately
            for (var mHex in aircraftMarkers) {
                if (mHex && mHex.toLowerCase() === lowH) {
                    var marker = aircraftMarkers[mHex];
                    var plane = currentAircraftData[mHex];
                    if (marker && plane) {
                        var cluster = getOverlappingAircraft(plane, 26);
                        var newTipHtml = buildAircraftTooltipHtml(plane, cluster);
                        marker.setTooltipContent(newTipHtml);
                    }
                    break;
                }
            }
        };

        function getAircraftDistressInfo(plane) {
            if (!plane) return null;
            var sq = String(plane.squawk || '').trim();
            var em = String(plane.emergency || '').toLowerCase().trim();
            if (sq === '7700' || em === 'general') {
                return {
                    code: '7700',
                    label: '7700 (General Emergency)',
                    badge: 'EMERG 7700',
                    shortDesc: 'General Emergency'
                };
            }
            if (sq === '7600' || em === 'radio') {
                return {
                    code: '7600',
                    label: '7600 (Radio Comm Failure)',
                    badge: 'NORDO 7600',
                    shortDesc: 'Radio Failure'
                };
            }
            if (sq === '7500' || em === 'unlawful') {
                return {
                    code: '7500',
                    label: '7500 (Hijack Alert)',
                    badge: 'HIJACK 7500',
                    shortDesc: 'Hijack Alert'
                };
            }
            if (em === 'lifeguard') {
                return {
                    code: 'MEDEVAC',
                    label: 'Lifeguard / Medevac',
                    badge: 'MEDEVAC',
                    shortDesc: 'Medevac'
                };
            }
            if (em && em !== 'none') {
                return {
                    code: 'DISTRESS',
                    label: 'Emergency (' + em + ')',
                    badge: 'DISTRESS',
                    shortDesc: em
                };
            }
            return null;
        }

        function getAircraftColor(plane) {
            var distress = getAircraftDistressInfo(plane);
            if (distress) return '#EF4444';

            var mode = window._adsbColorMode || 'altitude';
            var c = window._adsbColors;

            if (mode === 'type') {
                var cat = plane.category || 'general';
                if (cat === 'airliner') return c.type_airliner || '#FFFFFF';
                if (cat === 'light') return c.type_light || '#0000FF';
                if (cat === 'military') return c.type_military || '#00FF00';
                if (cat === 'helicopter') return c.type_helicopter || '#FFFF00';
                if (cat === 'glider') return c.type_glider || '#FF00FF';
                return '#00D2FF';
            }

            if (mode === 'distance') {
                var dst = (typeof plane.dst === 'number') ? plane.dst : 0;
                var maxRad = (window._adsbTarget && window._adsbTarget.radius_nm) ? window._adsbTarget.radius_nm : 50;
                var ratio = maxRad > 0 ? (dst / maxRad) : 0;
                if (ratio <= 0.25) return c.dist_close || '#FF0000';
                if (ratio <= 0.50) return c.dist_mid_close || '#FFA500';
                if (ratio <= 0.75) return c.dist_mid_far || '#FFFF00';
                return c.dist_far || '#00FF00';
            }

            // Default: 'altitude'
            var alt = (plane.alt_baro !== null && plane.alt_baro !== undefined) ? plane.alt_baro : (plane.alt_geom || 0);
            if (alt < 2000) return c.alt_ground || '#FF00FF';
            if (alt < 7000) return c.alt_low || '#FF0000';
            if (alt <= 25000) return c.alt_mid || '#0000FF';
            return c.alt_high || '#FFFFFF';
        }

        function createAirplaneIcon(plane) {
            var track = (plane.track !== null && plane.track !== undefined) ? plane.track : 0;
            var cat = plane.category || 'general';
            var color = getAircraftColor(plane);
            var distress = getAircraftDistressInfo(plane);

            var pathD = getAircraftSvgPath(cat);
            var iconW = (cat === 'glider') ? 26 : ((cat === 'airliner') ? 24 : 22);
            var iconH = (cat === 'glider') ? 26 : ((cat === 'airliner') ? 24 : 22);

            var pulseHtml = '';
            var distressTagHtml = '';
            if (distress) {
                pulseHtml = '<div class="adsb-distress-echo-ring"></div>' +
                            '<div class="adsb-distress-echo-ring adsb-distress-echo-ring-2"></div>';
                distressTagHtml = '<div class="adsb-distress-tag" title="Distress beacon: ' + escapeHtml(distress.label) + '">' +
                                  '  <span class="adsb-distress-beacon-dot"></span>' +
                                  '  <span>' + escapeHtml(distress.code) + '</span>' +
                                  '</div>';
            }

            var html = '<div class="adsb-plane-container">' +
                       pulseHtml +
                       '<div class="adsb-plane-marker" style="transform: rotate(' + track + 'deg);">' +
                       '<svg width="' + iconW + '" height="' + iconH + '" viewBox="0 0 24 24" fill="' + color + '" style="filter: drop-shadow(0 0 3px rgba(0,0,0,0.85));">' +
                       pathD +
                       '</svg>' +
                       '</div>' +
                       distressTagHtml +
                       '</div>';
            return {
                icon: L.divIcon({
                    className: 'adsb-icon-wrap',
                    html: html,
                    iconSize: [iconW, iconH],
                    iconAnchor: [Math.floor(iconW / 2), Math.floor(iconH / 2)]
                }),
                color: color,
                distress: distress
            };
        }

        function updateRadarSweepOverlay(target) {
            if (typeof target.lat !== 'number' || typeof target.lon !== 'number') return;
            window._lastAdsbTarget = target;
            var radNm = target.radius_nm || 50;
            var targetKey = target.lat.toFixed(4) + '_' + target.lon.toFixed(4) + '_' + radNm;

            if (adsbLastTargetCoord === targetKey) {
                return;
            }
            adsbLastTargetCoord = targetKey;

            if (adsbRadarRingsGroup) adsbRadarRingsGroup.clearLayers();
            if (adsbCenterMarker) {
                try {
                    if (adsbRadarRingsGroup) adsbRadarRingsGroup.removeLayer(adsbCenterMarker);
                    map.removeLayer(adsbCenterMarker);
                } catch (e) {}
                adsbCenterMarker = null;
            }

            var maxNm = radNm > 0 ? radNm : 50;
            var rings = [10, 25, 50];
            var hasOuterRing = false;

            // Concentric range rings marked at 10, 25, and 50 NM (using vector circles, hardware-clipped)
            for (var ri = 0; ri < rings.length; ri++) {
                var d = rings[ri];
                if (d <= maxNm) {
                    var isMax = Math.abs(d - maxNm) < 0.1;
                    if (isMax) hasOuterRing = true;
                    var ringCircle = L.circle([target.lat, target.lon], {
                        radius: d * 1852.0, // 1852 meters per Nautical Mile
                        color: isMax ? '#38BDF8' : '#9CA3AF',
                        weight: isMax ? 1.4 : 0.8,
                        opacity: isMax ? 0.55 : 0.28,
                        dashArray: isMax ? '6, 6' : '3, 5',
                        fill: false,
                        interactive: false,
                        pane: 'adsbRadarPane'
                    });
                    adsbRadarRingsGroup.addLayer(ringCircle);

                    var labelLat = target.lat + (d / 60.0);
                    var labelMarker = L.marker([labelLat, target.lon], {
                        icon: L.divIcon({
                            className: 'adsb-range-ring-label',
                            html: '<span style="color:' + (isMax ? '#38BDF8' : '#9CA3AF') + '; font-size:9.5px; font-weight:600; background:rgba(17,24,39,0.78); padding:1px 5px; border-radius:3px; border:1px solid rgba(75,85,99,0.5); font-family:system-ui, -apple-system, sans-serif;">' + d + ' NM</span>',
                            iconSize: [44, 16],
                            iconAnchor: [22, 8]
                        }),
                        interactive: false,
                        pane: 'adsbRadarPane'
                    });
                    adsbRadarRingsGroup.addLayer(labelMarker);
                }
            }

            if (!hasOuterRing) {
                var outerRing = L.circle([target.lat, target.lon], {
                    radius: maxNm * 1852.0,
                    color: '#38BDF8',
                    weight: 1.4,
                    opacity: 0.55,
                    dashArray: '6, 6',
                    fill: false,
                    interactive: false,
                    pane: 'adsbRadarPane'
                });
                adsbRadarRingsGroup.addLayer(outerRing);

                var outerLabelLat = target.lat + (maxNm / 60.0);
                var outerLabel = L.marker([outerLabelLat, target.lon], {
                    icon: L.divIcon({
                        className: 'adsb-range-ring-label',
                        html: '<span style="color:#38BDF8; font-size:9.5px; font-weight:600; background:rgba(17,24,39,0.78); padding:1px 5px; border-radius:3px; border:1px solid rgba(56,189,248,0.5); font-family:system-ui, -apple-system, sans-serif;">' + maxNm + ' NM</span>',
                        iconSize: [44, 16],
                        iconAnchor: [22, 8]
                    }),
                    interactive: false,
                    pane: 'adsbRadarPane'
                });
                adsbRadarRingsGroup.addLayer(outerLabel);
            }

            // Animated Center Radar Dish Beacon with rotating scanner sweep
            var centerHtml = '<div class="adsb-radar-center-beacon">' +
                             '  <div class="adsb-radar-center-pulse"></div>' +
                             '  <svg class="adsb-radar-center-svg" width="64" height="64" viewBox="0 0 200 200" style="position: absolute; top: -18px; left: -18px; pointer-events: none;">' +
                             '    <defs>' +
                             '      <filter id="radarSweepBlur" x="-20%" y="-20%" width="140%" height="140%">' +
                             '        <feGaussianBlur stdDeviation="1.5" />' +
                             '      </filter>' +
                             '    </defs>' +
                             '    <g class="adsb-radar-sweeper">' +
                             '      <line x1="100" y1="100" x2="100" y2="10" stroke="#38BDF8" stroke-width="0.65" opacity="0.85" stroke-linecap="round" />' +
                             '    </g>' +
                             '  </svg>' +
                             '  <div class="adsb-radar-center-dot">📡</div>' +
                             '</div>';
            adsbCenterMarker = L.marker([target.lat, target.lon], {
                icon: L.divIcon({
                    className: 'adsb-radar-center-wrap',
                    html: centerHtml,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14]
                }),
                interactive: false,
                pane: 'adsbRadarPane'
            });
            adsbRadarRingsGroup.addLayer(adsbCenterMarker);
        }

        function onAdsbDataReady(payload) {
            if (!adsbLayerGroup) return;
            if (!adsbActive) return;

            var target = payload.target_info || {};
            var total = payload.total || 0;
            var aircraft = payload.aircraft || [];

            // Update UI elements
            var countBadge = document.getElementById('adsb-count-badge');
            if (countBadge) countBadge.textContent = total;

            var targetLabel = document.getElementById('adsb-target-label');
            if (targetLabel) targetLabel.textContent = target.alias || target.node_id || 'Local Node';

            var radiusLabel = document.getElementById('adsb-radius-label');
            if (radiusLabel) radiusLabel.textContent = (target.radius_nm || 50) + ' NM';

            var resetBtn = document.getElementById('adsb-reset-btn');
            if (resetBtn) {
                resetBtn.style.display = (target.node_id && target.node_id !== '') ? 'block' : 'none';
            }

            // Update radar sweep overlay and range circle
            if (typeof target.lat === 'number' && typeof target.lon === 'number') {
                updateRadarSweepOverlay(target);
            }

            // Pre-index active aircraft and reset container point cache
            _aircraftContainerPoints = {};
            currentAircraftData = {};
            var currentHexes = new Set();
            for (var pi = 0; pi < aircraft.length; pi++) {
                var ap = aircraft[pi];
                if (typeof ap.lat !== 'number' || typeof ap.lon !== 'number') continue;
                var ah = (ap.hex || '').toLowerCase();
                if (!ah) continue;
                currentAircraftData[ah] = ap;
            }

            var visibleCount = 0;
            for (var i = 0; i < aircraft.length; i++) {
                var plane = aircraft[i];
                if (typeof plane.lat !== 'number' || typeof plane.lon !== 'number') continue;
                var hex = (plane.hex || '').toLowerCase();
                if (!hex) continue;
                currentHexes.add(hex);

                var isVis = window.isAircraftCategoryVisible ? window.isAircraftCategoryVisible(plane.category) : true;
                if (isVis) visibleCount++;

                var iconData = createAirplaneIcon(plane);

                // Update flight trajectory history
                var planeAltFt = (plane.alt_baro != null ? plane.alt_baro : (plane.alt_geom != null ? plane.alt_geom : (plane.altitude || 0)));
                if (!aircraftHistory[hex]) {
                    aircraftHistory[hex] = [{ lat: plane.lat, lon: plane.lon, alt_baro: planeAltFt, color: iconData.color, ts: Date.now() }];
                } else {
                    var hist = aircraftHistory[hex];
                    var lastPt = hist[hist.length - 1];
                    var distMoved = Math.abs(plane.lat - lastPt.lat) + Math.abs(plane.lon - lastPt.lon);
                    if (distMoved > 0.0001) {
                        hist.push({ lat: plane.lat, lon: plane.lon, alt_baro: planeAltFt, color: iconData.color, ts: Date.now() });
                        if (hist.length > 30) hist.shift();
                    } else {
                        lastPt.color = iconData.color;
                        lastPt.ts = Date.now();
                        if (planeAltFt) lastPt.alt_baro = planeAltFt;
                    }
                }

                // Render or update breadcrumb trail (single persistent L.polyline per aircraft updated in-place)
                var hist = aircraftHistory[hex];
                if (hist && hist.length >= 2) {
                    var latlngs = [];
                    for (var hi = 0; hi < hist.length; hi++) {
                        latlngs.push([hist[hi].lat, hist[hi].lon]);
                    }
                    var trailLine = aircraftTrails[hex];
                    if (!trailLine) {
                        trailLine = L.polyline(latlngs, {
                            color: iconData.color,
                            weight: 2,
                            opacity: isVis ? 0.65 : 0,
                            lineCap: 'round',
                            lineJoin: 'round',
                            pane: 'adsbTrailsPane',
                            interactive: false
                        });
                        adsbTrailsGroup.addLayer(trailLine);
                        aircraftTrails[hex] = trailLine;
                    } else {
                        trailLine.setLatLngs(latlngs);
                        trailLine.setStyle({
                            color: iconData.color,
                            opacity: isVis ? 0.65 : 0
                        });
                    }
                }

                currentAircraftData[hex] = plane;
                var marker = aircraftMarkers[hex];
                var distress = getAircraftDistressInfo(plane);
                var distressKey = distress ? distress.code : '';
                var track = (plane.track !== null && plane.track !== undefined) ? plane.track : 0;

                if (!marker) {
                    marker = L.marker([plane.lat, plane.lon], {
                        icon: iconData.icon,
                        pane: 'adsbMarkersPane',
                        opacity: isVis ? 1 : 0
                    });
                    marker._planeData = plane;
                    marker._lastTrack = track;
                    marker._lastColor = iconData.color;
                    marker._lastDistress = distressKey;

                    (function(h, m) {
                        m.on('click', function(e) {
                            if (e && e.originalEvent) {
                                if (e.originalEvent.stopPropagation) e.originalEvent.stopPropagation();
                                if (e.originalEvent.preventDefault) e.originalEvent.preventDefault();
                                if (L.DomEvent) L.DomEvent.stop(e.originalEvent);
                            }

                            // Check if multiple aircraft overlap in this cluster to cycle through
                            var p = currentAircraftData[h];
                            var cl = getOverlappingAircraft(p, 26);
                            if (cl.length > 1 && pinnedTooltipHex === h) {
                                var curIdx = 0;
                                for (var ci = 0; ci < cl.length; ci++) {
                                    if ((cl[ci].hex || '').toLowerCase() === h) {
                                        curIdx = ci;
                                        break;
                                    }
                                }
                                var nextIdx = (curIdx + 1) % cl.length;
                                var nextHex = (cl[nextIdx].hex || '').toLowerCase();
                                var nextMarker = aircraftMarkers[nextHex];
                                if (nextMarker) {
                                    window.openAircraftTooltip(nextHex, nextMarker, true);
                                    return;
                                }
                            }

                            // Left-clicking an aircraft marker ALWAYS pins and keeps the tooltip open!
                            window.openAircraftTooltip(h, m, true);
                        });
                        m.on('mouseover', function() {
                            if (_adsbHoverCloseTimer) {
                                clearTimeout(_adsbHoverCloseTimer);
                                _adsbHoverCloseTimer = null;
                            }
                            if (!pinnedTooltipHex) {
                                window.openAircraftTooltip(h, m, false);
                            }
                        });
                        m.on('mouseout', function() {
                            if (pinnedTooltipHex === h) {
                                return; // PINNED! NEVER close on mouseout!
                            }
                            if (_adsbHoverCloseTimer) {
                                clearTimeout(_adsbHoverCloseTimer);
                            }
                            _adsbHoverCloseTimer = setTimeout(function() {
                                if (pinnedTooltipHex !== h) {
                                    try { m.closeTooltip(); } catch (e) {}
                                    try { m.setZIndexOffset(0); } catch (e) {}
                                    if (_activeHoverHex === h) {
                                        _activeHoverHex = null;
                                    }
                                }
                                _adsbHoverCloseTimer = null;
                            }, 800);
                        });
                    })(hex, marker);

                    marker.bindTooltip('', {
                        className: 'adsb-tooltip',
                        direction: 'top',
                        offset: [0, -10],
                        interactive: true
                    });

                    // CRITICAL: Disable Leaflet's built-in automatic tooltip listeners!
                    marker.off('mouseout', marker.closeTooltip);
                    marker.off('mouseover', marker._openTooltip);
                    marker.off('click', marker._openTooltip);

                    adsbLayerGroup.addLayer(marker);
                    aircraftMarkers[hex] = marker;

                    if (marker._icon) {
                        L.DomEvent.disableClickPropagation(marker._icon);
                        L.DomEvent.disableScrollPropagation(marker._icon);
                        if (!isVis) marker._icon.style.display = 'none';
                    }
                } else {
                    marker._planeData = plane;
                    marker.setLatLng([plane.lat, plane.lon]);
                    marker.setOpacity(isVis ? 1 : 0);
                    if (marker._icon) marker._icon.style.display = isVis ? '' : 'none';
                    if (marker._lastTrack !== track) {
                        marker._lastTrack = track;
                        if (marker._icon) {
                            var inner = marker._icon.querySelector('.adsb-plane-marker');
                            if (inner) inner.style.transform = 'rotate(' + track + 'deg)';
                        }
                    }
                    if (marker._lastColor !== iconData.color) {
                        marker._lastColor = iconData.color;
                        if (marker._icon) {
                            var svg = marker._icon.querySelector('svg');
                            if (svg) svg.setAttribute('fill', iconData.color);
                        }
                    }
                    if (marker._lastDistress !== distressKey) {
                        marker._lastDistress = distressKey;
                        marker.setIcon(iconData.icon);
                        if (marker._icon) {
                            L.DomEvent.disableClickPropagation(marker._icon);
                            L.DomEvent.disableScrollPropagation(marker._icon);
                            if (!isVis) marker._icon.style.display = 'none';
                        }
                    }
                }

                // If this aircraft tooltip is pinned or hovered, keep it open and follow the aircraft!
                var isTargetOpen = (pinnedTooltipHex === hex) || (_activeHoverHex === hex && !pinnedTooltipHex);
                if (isTargetOpen) {
                    var cluster = getOverlappingAircraft(plane, 26);
                    var tipHtml = buildAircraftTooltipHtml(plane, cluster);
                    marker.setTooltipContent(tipHtml);
                    marker.setZIndexOffset(10000);
                    if (!marker.isTooltipOpen()) {
                        marker.openTooltip();
                    } else {
                        var tip = marker.getTooltip();
                        if (tip) {
                            tip.setLatLng(marker.getLatLng());
                        }
                    }
                    if (pinnedTooltipHex === hex && marker._icon) {
                        var inner = marker._icon.querySelector('.adsb-plane-marker');
                        if (inner) inner.classList.add('pinned');
                    }
                    var tipObj = marker.getTooltip();
                    if (tipObj && tipObj._container) {
                        L.DomEvent.disableClickPropagation(tipObj._container);
                    }
                }
            }

            // Update badge count with visible / total count
            var countBadge = document.getElementById('adsb-count-badge');
            if (countBadge) {
                countBadge.textContent = visibleCount + (visibleCount !== total ? ' / ' + total : '');
            }

            // Proximity alert detection for watched aircraft types within 10 miles of observed location
            if (window._adsbAlertConfig && window._adsbAlertConfig.enabled) {
                var watchedCats = window._adsbAlertConfig.categories || {};
                var alertRadiusMi = window._adsbAlertConfig.radiusMi || 10.0;
                var nowTs = Date.now();

                for (var ai = 0; ai < aircraft.length; ai++) {
                    var pl = aircraft[ai];
                    if (typeof pl.lat !== 'number' || typeof pl.lon !== 'number') continue;
                    var pHex = (pl.hex || '').toLowerCase();
                    if (!pHex) continue;

                    var pCat = (pl.category || 'general').toLowerCase();
                    if (!watchedCats[pCat]) continue;

                    // pl.dst is Great Circle distance in nautical miles from observed target
                    // Convert to statute miles: 1 NM = 1.15078 mi
                    var pDistMi = (typeof pl.dst === 'number') ? (pl.dst * 1.15078) : null;
                    if (pDistMi === null || pDistMi > alertRadiusMi) continue;

                    // Proximity hit! Check 5-minute cooldown per aircraft
                    var lastAlert = window._adsbAlertedHexes[pHex] || 0;
                    if (nowTs - lastAlert > 300000) {
                        window._adsbAlertedHexes[pHex] = nowTs;
                        var pCallsign = pl.flight || pHex.toUpperCase();

                        // 1. Automatically pop up its information box on the map (2D or 3D)
                        var pMarker = aircraftMarkers[pHex];
                        window.openAircraftTooltip(pHex, pMarker, true);

                        // 2. Notify Python for native desktop balloon / sound alert
                        if (window.pyBridge && window.pyBridge.on_adsb_proximity_alert) {
                            window.pyBridge.on_adsb_proximity_alert(pHex, pCallsign, pDistMi, pCat);
                        }
                    }
                }
            }

            // Remove markers for aircraft that left coverage
            for (var oldHex in aircraftMarkers) {
                if (!currentHexes.has(oldHex)) {
                    if (pinnedTooltipHex === oldHex) {
                        pinnedTooltipHex = null;
                    }
                    if (_activeHoverHex === oldHex) {
                        _activeHoverHex = null;
                    }
                    adsbLayerGroup.removeLayer(aircraftMarkers[oldHex]);
                    delete aircraftMarkers[oldHex];
                    delete currentAircraftData[oldHex];
                    delete _aircraftContainerPoints[oldHex];
                }
            }

            // Prune expired aircraft trails
            var now = Date.now();
            for (var hexKey in aircraftHistory) {
                if (!currentHexes.has(hexKey)) {
                    var hist = aircraftHistory[hexKey];
                    var lastSeen = (hist && hist.length > 0) ? hist[hist.length - 1].ts : 0;
                    if (now - lastSeen > 30000) {
                        if (aircraftTrails[hexKey]) {
                            adsbTrailsGroup.removeLayer(aircraftTrails[hexKey]);
                            delete aircraftTrails[hexKey];
                        }
                        delete aircraftHistory[hexKey];
                    }
                }
            }
            if (window._is3DActive && typeof syncAdsbTo3D === 'function') {
                syncAdsbTo3D();
            }
        }
        window.onAdsbDataReady = onAdsbDataReady;
    </script>
</body>
</html>
"""


def latlon_to_maidenhead(lat: float, lon: float) -> str:
    """Convert decimal lat/lon to standard 6-character Maidenhead QTH Locator."""
    try:
        adj_lon = max(-180.0, min(179.999999, float(lon))) + 180.0
        adj_lat = max(-90.0, min(89.999999, float(lat))) + 90.0

        field_lon = chr(ord('A') + int(adj_lon / 20.0))
        field_lat = chr(ord('A') + int(adj_lat / 10.0))

        rem_lon = adj_lon % 20.0
        rem_lat = adj_lat % 10.0
        square_lon = str(int(rem_lon / 2.0))
        square_lat = str(int(rem_lat / 1.0))

        sub_lon = chr(ord('a') + int((rem_lon % 2.0) / (2.0 / 24.0)))
        sub_lat = chr(ord('a') + int((rem_lat % 1.0) / (1.0 / 24.0)))

        return f"{field_lon}{field_lat}{square_lon}{square_lat}{sub_lon}{sub_lat}"
    except Exception:
        return "Unknown"


def calculate_distance_and_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float, float]:
    """Calculate great-circle distance (km, miles) and initial bearing (degrees) between two points."""
    try:
        r_km = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
        c = 2.0 * math.atan2(math.sqrt(max(0.0, a)), math.sqrt(max(0.0, 1.0 - a)))
        dist_km = r_km * c
        dist_mi = dist_km * 0.621371

        y = math.sin(delta_lambda) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
        initial_bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

        return dist_km, dist_mi, initial_bearing
    except Exception:
        return 0.0, 0.0, 0.0


class WebBridge(QObject):
    """Bridge for communication from Leaflet JavaScript to Python."""
    node_clicked_signal = pyqtSignal(str)
    map_moved_signal = pyqtSignal(float, float, int)
    visualised_path_closed_signal = pyqtSignal()
    hop_candidate_selected_signal = pyqtSignal(str, str, str)
    phantom_node_toggled_signal = pyqtSignal(str, str, bool)
    map_context_menu_signal = pyqtSignal(float, float, int, int)
    node_context_menu_signal = pyqtSignal(str, str, bool, bool, float, float, int, int)

    @pyqtSlot(float, float, int, int)
    def on_map_context_menu(self, lat: float, lon: float, x: int, y: int):
        self.map_context_menu_signal.emit(lat, lon, x, y)

    @pyqtSlot(str, str, bool, bool, float, float, int, int)
    def on_node_context_menu(self, node_id: str, alias: str, is_repeater: bool, is_phantom: bool, lat: float, lon: float, x: int, y: int):
        self.node_context_menu_signal.emit(node_id, alias, is_repeater, is_phantom, lat, lon, x, y)

    @pyqtSlot(str)
    def on_node_clicked(self, node_id: str):
        self.node_clicked_signal.emit(node_id)

    @pyqtSlot(float, float, int)
    def on_map_moved(self, lat: float, lon: float, zoom: int):
        self.map_moved_signal.emit(lat, lon, zoom)

    @pyqtSlot()
    def on_visualised_path_closed(self):
        self.visualised_path_closed_signal.emit()

    @pyqtSlot(str, str, str)
    def on_hop_candidate_selected(self, hop_prefix: str, target_node_id: str, msg_id: str = ""):
        self.hop_candidate_selected_signal.emit(hop_prefix, target_node_id, msg_id)

    node_deleted_signal = pyqtSignal(str)

    @pyqtSlot(str, str, bool)
    def on_phantom_node_toggled(self, node_id: str, alias: str, is_phantom: bool):
        self.phantom_node_toggled_signal.emit(node_id, alias, is_phantom)

    @pyqtSlot(str)
    def on_delete_node(self, node_id: str):
        self.node_deleted_signal.emit(node_id)

    repeater_neighbors_cleared_signal = pyqtSignal()
    tropo_stepped_signal = pyqtSignal(int)
    tropo_toggled_signal = pyqtSignal(bool)
    node_scope_changed_signal = pyqtSignal(str, str)

    @pyqtSlot()
    def on_repeater_neighbors_cleared(self):
        self.repeater_neighbors_cleared_signal.emit()

    @pyqtSlot(int)
    def on_tropo_stepped(self, delta_hours: int):
        self.tropo_stepped_signal.emit(delta_hours)

    @pyqtSlot(bool)
    def on_tropo_toggled(self, enabled: bool):
        self.tropo_toggled_signal.emit(enabled)

    @pyqtSlot(str, str)
    def on_node_scope_changed(self, node_id: str, scope_name: str):
        self.node_scope_changed_signal.emit(node_id, scope_name)

    set_adsb_target_signal = pyqtSignal(str, str, float, float)
    adsb_toggled_signal = pyqtSignal(bool)
    reset_adsb_target_signal = pyqtSignal()
    open_external_url_signal = pyqtSignal(str)

    @pyqtSlot(str, str, float, float)
    def on_set_adsb_target(self, node_id: str, alias: str, lat: float, lon: float):
        self.set_adsb_target_signal.emit(node_id, alias, lat, lon)

    @pyqtSlot(bool)
    def on_adsb_toggled(self, enabled: bool):
        self.adsb_toggled_signal.emit(enabled)

    @pyqtSlot()
    def on_reset_adsb_target(self):
        self.reset_adsb_target_signal.emit()

    activity_timeframe_changed_signal = pyqtSignal(int)
    activity_heatmap_toggled_signal = pyqtSignal(bool)
    thunderstorm_toggled_signal = pyqtSignal(bool)

    @pyqtSlot(int)
    def on_activity_timeframe_changed(self, hours: int):
        self.activity_timeframe_changed_signal.emit(hours)

    @pyqtSlot(bool)
    def on_activity_heatmap_toggled(self, enabled: bool):
        self.activity_heatmap_toggled_signal.emit(enabled)

    @pyqtSlot(bool)
    def on_thunderstorm_toggled(self, enabled: bool):
        self.thunderstorm_toggled_signal.emit(enabled)

    new_nodes_timeframe_changed_signal = pyqtSignal(int)
    new_nodes_toggled_signal = pyqtSignal(bool)

    @pyqtSlot(int)
    def on_new_nodes_timeframe_changed(self, hours: int):
        self.new_nodes_timeframe_changed_signal.emit(hours)

    @pyqtSlot(bool)
    def on_new_nodes_toggled(self, enabled: bool):
        self.new_nodes_toggled_signal.emit(enabled)

    mqtt_nodes_toggled_signal = pyqtSignal(bool)

    @pyqtSlot(bool)
    def on_mqtt_nodes_toggled(self, enabled: bool):
        self.mqtt_nodes_toggled_signal.emit(enabled)

    map_3d_toggled_signal = pyqtSignal(bool)

    @pyqtSlot(bool)
    def on_map_3d_toggled(self, enabled: bool):
        self.map_3d_toggled_signal.emit(enabled)

    mark_all_nodes_known_signal = pyqtSignal()

    @pyqtSlot()
    def mark_all_nodes_known(self):
        self.mark_all_nodes_known_signal.emit()

    space_weather_toggled_signal = pyqtSignal(bool)
    space_weather_refresh_signal = pyqtSignal()
    space_weather_opacity_signal = pyqtSignal(float)

    @pyqtSlot(bool)
    def on_space_weather_toggled(self, enabled: bool):
        self.space_weather_toggled_signal.emit(enabled)

    @pyqtSlot()
    def on_space_weather_refresh(self):
        self.space_weather_refresh_signal.emit()

    @pyqtSlot(float)
    def on_space_weather_opacity(self, opacity: float):
        self.space_weather_opacity_signal.emit(opacity)

    satellite_toggled_signal = pyqtSignal(bool)
    satellite_refresh_signal = pyqtSignal()
    satellite_selected_signal = pyqtSignal(str)

    @pyqtSlot(bool)
    def on_satellite_toggled(self, enabled: bool):
        self.satellite_toggled_signal.emit(enabled)

    @pyqtSlot()
    def on_satellite_refresh(self):
        self.satellite_refresh_signal.emit()

    @pyqtSlot(str)
    def on_satellite_selected(self, norad_id: str):
        self.satellite_selected_signal.emit(str(norad_id))

    adsb_color_mode_changed_signal = pyqtSignal(str)
    request_aircraft_photo_signal = pyqtSignal(str)

    @pyqtSlot(str)
    def on_adsb_color_mode_changed(self, mode: str):
        self.adsb_color_mode_changed_signal.emit(mode)

    @pyqtSlot(str)
    def request_aircraft_photo(self, hex_code: str):
        self.request_aircraft_photo_signal.emit(hex_code)

    adsb_proximity_alert_signal = pyqtSignal(str, str, float, str)
    adsb_filters_changed_signal = pyqtSignal(str)
    adsb_alert_config_changed_signal = pyqtSignal(str)

    @pyqtSlot(str, str, float, str)
    def on_adsb_proximity_alert(self, hex_code: str, flight: str, dist_mi: float, category: str):
        self.adsb_proximity_alert_signal.emit(hex_code, flight, dist_mi, category)

    @pyqtSlot(str)
    def on_adsb_filters_changed(self, filters_json: str):
        self.adsb_filters_changed_signal.emit(filters_json)

    @pyqtSlot(str)
    def on_adsb_alert_config_changed(self, config_json: str):
        self.adsb_alert_config_changed_signal.emit(config_json)

    @pyqtSlot(str)
    def on_open_external_url(self, url: str):
        if not url:
            return
        try:
            QDesktopServices.openUrl(QUrl(url))
            self.open_external_url_signal.emit(url)
        except Exception as e:
            logger.error(f"Failed opening external URL {url}: {e}")

    p2p_path_selected_signal = pyqtSignal(float, float, float, float, str, str)
    profile_node_signal = pyqtSignal(str, str, float, float)
    calc_node_viewshed_signal = pyqtSignal(str, str, float, float)

    @pyqtSlot(float, float, float, float, str, str)
    def on_p2p_path_selected(self, lat1: float, lon1: float, lat2: float, lon2: float, alias1: str, alias2: str):
        self.p2p_path_selected_signal.emit(lat1, lon1, lat2, lon2, alias1, alias2)

    @pyqtSlot(str, str, float, float)
    def on_profile_node_requested(self, node_id: str, alias: str, lat: float, lon: float):
        self.profile_node_signal.emit(node_id, alias, lat, lon)

    @pyqtSlot(str, str, float, float)
    def on_calc_node_viewshed_requested(self, node_id: str, alias: str, lat: float, lon: float):
        self.calc_node_viewshed_signal.emit(node_id, alias, lat, lon)

    search_node_id_toggled_signal = pyqtSignal(bool)
    lightning_proximity_alert_signal = pyqtSignal(float, int)
    copy_clipboard_signal = pyqtSignal(str)

    @pyqtSlot(bool)
    def on_search_node_id_toggled(self, enabled: bool):
        self.search_node_id_toggled_signal.emit(enabled)

    @pyqtSlot(float, int)
    def on_lightning_proximity_alert(self, distance_mi: float, bearing_deg: int):
        self.lightning_proximity_alert_signal.emit(distance_mi, bearing_deg)

    @pyqtSlot(str)
    def on_copy_clipboard(self, text: str):
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QClipboard
            cb = QApplication.clipboard()
            if cb:
                cb.setText(text, QClipboard.Mode.Clipboard)
                cb.setText(text, QClipboard.Mode.Selection)
        except Exception as e:
            logger.warning(f"Error copying to clipboard: {e}")
        self.copy_clipboard_signal.emit(text)

    packet_hud_toggled_signal = pyqtSignal(bool)
    activity_timeline_toggled_signal = pyqtSignal(bool)
    map_legend_toggled_signal = pyqtSignal(bool)

    @pyqtSlot(bool)
    def on_packet_hud_toggled(self, visible: bool):
        self.packet_hud_toggled_signal.emit(visible)

    @pyqtSlot(bool)
    def on_activity_timeline_toggled(self, visible: bool):
        self.activity_timeline_toggled_signal.emit(visible)

    @pyqtSlot(bool)
    def on_map_legend_toggled(self, visible: bool):
        self.map_legend_toggled_signal.emit(visible)


class DraggableOverlayFrame(QFrame):
    """Repositionable/draggable floating overlay container with Discord dark theme styling."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._drag_start_pos = None
        self._user_moved = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child and isinstance(child, (QPushButton, QComboBox, QAbstractItemView)):
                super().mousePressEvent(event)
                return
            self._dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.raise_()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start_pos is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_start_pos
            if self.parent():
                parent_w = self.parent().width()
                parent_h = self.parent().height()
                max_x = max(0, parent_w - self.width())
                max_y = max(0, parent_h - self.height())
                nx = max(0, min(max_x, new_pos.x()))
                ny = max(0, min(max_y, new_pos.y()))
                self.move(nx, ny)
            else:
                self.move(new_pos)
            self._user_moved = True
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_start_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ReformingMapOverlay(QWidget):
    """Semi-transparent loading HUD displayed over Leaflet map during view recovery or resize."""

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reformingMapOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("""
            QWidget#reformingMapOverlay {
                background-color: rgba(14, 17, 23, 0.90);
            }
        """)

        self._spinner_idx = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(80)
        self._spinner_timer.timeout.connect(self._advance_spinner)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame()
        self.card.setObjectName("reformingCard")
        self.card.setFixedWidth(440)
        self.card.setStyleSheet("""
            QFrame#reformingCard {
                background-color: #161920;
                border: 1.5px solid #10B981;
                border-radius: 14px;
            }
        """)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(28, 22, 28, 22)
        card_layout.setSpacing(10)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Large animated cyber braille spinner centered above title
        self.spinner_lbl = QLabel("⠋")
        self.spinner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spinner_lbl.setFixedHeight(48)
        self.spinner_lbl.setStyleSheet("""
            font-size: 36px;
            font-weight: bold;
            color: #10B981;
            background: transparent;
            border: none;
            font-family: monospace;
        """)
        card_layout.addWidget(self.spinner_lbl)

        self.title_lbl = QLabel("INITIALIZING MESH MAP")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setFixedHeight(24)
        self.title_lbl.setStyleSheet("""
            font-size: 15px;
            font-weight: bold;
            color: #FFFFFF;
            background: transparent;
            border: none;
        """)
        card_layout.addWidget(self.title_lbl)

        # Stage status description
        self.desc_lbl = QLabel("Synchronizing compositor & mesh layers...")
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet("""
            font-size: 12px;
            color: #94A3B8;
            background: transparent;
            border: none;
        """)
        card_layout.addWidget(self.desc_lbl)

        # Stage pills row
        self.pills_container = QWidget()
        self.pills_container.setStyleSheet("background: transparent; border: none;")
        self.pills_layout = QHBoxLayout(self.pills_container)
        self.pills_layout.setContentsMargins(0, 4, 0, 4)
        self.pills_layout.setSpacing(6)
        self.pills_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.stage_pills: List[QLabel] = []
        stage_names = ["1 ENGINE", "2 VIEWPORT", "3 NODES", "4 RADIO"]
        for name in stage_names:
            pill = QLabel(name)
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pill.setFixedHeight(22)
            pill.setStyleSheet("""
                background-color: #1E293B;
                color: #64748B;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.8px;
            """)
            self.stage_pills.append(pill)
            self.pills_layout.addWidget(pill)
        card_layout.addWidget(self.pills_container)

        # High-tech progress bar + percent readout
        bar_layout = QVBoxLayout()
        bar_layout.setSpacing(4)
        bar_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(25)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setFixedWidth(320)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgba(16, 185, 129, 0.4);
                border-radius: 4px;
                background-color: #0F172A;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #059669, stop:0.5 #10B981, stop:1 #34D399);
                border-radius: 3px;
            }
        """)
        bar_layout.addWidget(self.progress_bar, alignment=Qt.AlignmentFlag.AlignCenter)

        self.percent_lbl = QLabel("25%")
        self.percent_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.percent_lbl.setStyleSheet("""
            font-size: 11px;
            font-weight: bold;
            color: #10B981;
            font-family: monospace;
            background: transparent;
            border: none;
        """)
        bar_layout.addWidget(self.percent_lbl)
        card_layout.addLayout(bar_layout)

        # Retain pulse_bar attribute for backwards compatibility
        self.pulse_bar = self.progress_bar

        layout.addWidget(self.card)
        self.hide()

    def _advance_spinner(self):
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNER_FRAMES)
        self.spinner_lbl.setText(self.SPINNER_FRAMES[self._spinner_idx])

    def _update_stage_pills(self, active_stage: Optional[int]):
        if not active_stage:
            self.pills_container.hide()
            return
        self.pills_container.show()
        for idx, pill in enumerate(self.stage_pills, start=1):
            if idx < active_stage:
                # Completed stage
                pill.setStyleSheet("""
                    background-color: #064E3B;
                    color: #A7F3D0;
                    border: 1px solid #059669;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: bold;
                    letter-spacing: 0.8px;
                """)
            elif idx == active_stage:
                # Active stage
                pill.setStyleSheet("""
                    background-color: #10B981;
                    color: #0F172A;
                    border: 1.5px solid #34D399;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: 900;
                    letter-spacing: 0.8px;
                """)
            else:
                # Pending stage
                pill.setStyleSheet("""
                    background-color: #1E293B;
                    color: #64748B;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: bold;
                    letter-spacing: 0.8px;
                """)

    def show_reforming(
        self,
        message: str = "Synchronizing compositor & mesh layers...",
        title: Optional[str] = None,
        stage: Optional[int] = None,
        percent: Optional[int] = None,
    ):
        if title:
            self.title_lbl.setText(title)
        self.desc_lbl.setText(message)

        if percent is not None:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
            self.percent_lbl.setText(f"{percent}%")
            self.percent_lbl.show()
        elif stage is not None:
            pct = min(100, max(0, stage * 25))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
            self.percent_lbl.setText(f"{pct}%")
            self.percent_lbl.show()
        else:
            # Indeterminate resize/recovery mode
            self.progress_bar.setRange(0, 0)
            self.percent_lbl.hide()

        self._update_stage_pills(stage)

        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

        if self.parentWidget():
            self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()

    def hide_reforming(self):
        if self._spinner_timer.isActive():
            self._spinner_timer.stop()
        self.hide()


class MeshMapWidget(QWidget):
    """Interactive Mesh Map & Packet Path Watcher widget displayed between Chat and Pixoo mirror."""

    node_selected = pyqtSignal(str)
    map_ready = pyqtSignal()
    search_node_id_toggled = pyqtSignal(bool)
    lightning_proximity_alert = pyqtSignal(float, int)
    adsb_proximity_alert = pyqtSignal(str, str, float, str)
    packet_hud_toggled = pyqtSignal(bool)
    activity_timeline_toggled = pyqtSignal(bool)
    map_legend_toggled = pyqtSignal(bool)

    def __init__(self, storage=None, config=None, driver=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.driver = driver
        self.setObjectName("mesh_map_widget")

        self.node_filter_mode = "ALL"
        self.show_repeaters_only = False
        self.show_rf_links = True
        self.show_paths = True
        self.show_companion_orbitals = False
        self.show_search_node_id = False
        self.show_packet_hud = getattr(self.config.meshcore, "map_show_packet_hud", False) if (self.config and hasattr(self.config, "meshcore")) else False
        self.show_activity_timeline = getattr(self.config.meshcore, "map_show_activity_timeline", True) if (self.config and hasattr(self.config, "meshcore")) else True
        self.show_map_legend = False
        self._page_ready = not WEBENGINE_AVAILABLE
        self._last_traced_path_info = None
        self._pending_visualise_msg = None
        self._pending_neighbors_payload = None
        self._watchdog_grace_until = 0.0
        self._watchdog_unanswered = 0
        self._recent_packet_events = {}

        self._initial_loading_active = WEBENGINE_AVAILABLE
        self._stagger_stage = 1 if WEBENGINE_AVAILABLE else 0
        self._geometry_in_motion = False
        self._last_geometry_motion_time = 0.0
        self._pending_refresh = False
        self._stagger_timer = QTimer(self)
        self._stagger_timer.setSingleShot(True)
        self._stagger_timer.timeout.connect(self._on_stagger_timer_timeout)

        self.tropo_service = TropoForecastService(parent=self)
        self.tropo_service.forecast_ready.connect(self._on_tropo_forecast_ready)
        self.tropo_service.forecast_loading.connect(self._on_tropo_forecast_loading)
        self.tropo_service.forecast_error.connect(self._on_tropo_forecast_error)

        self.show_adsb = False
        self.adsb_service = ADSBService(parent=self)
        self.adsb_service.flights_updated.connect(self._on_adsb_flights_updated)

        self.activity_heatmap_active = False
        self.activity_timeframe_hours = 1
        self.show_thunderstorm = False
        self.thunderstorm_service = ThunderstormService(parent=self)
        self.thunderstorm_service.radar_updated.connect(self._on_thunderstorm_radar_updated)

        self.new_nodes_active = False
        self.new_nodes_timeframe_hours = getattr(self.config.meshcore if (self.config and hasattr(self.config, "meshcore")) else self.config, "map_new_nodes_timeframe_hours", 72) if self.config else 72
        self.mqtt_nodes_active = False
        self.is_3d_mode = False

        self.show_space_weather = False
        self.space_weather_service = SpaceWeatherService(parent=self)
        self.space_weather_service.weather_updated.connect(self._on_space_weather_updated)
        self.space_weather_service.weather_loading.connect(self._on_space_weather_loading)
        self.space_weather_service.weather_error.connect(self._on_space_weather_error)

        self.show_satellites = getattr(self.config.satellites, "enabled", False) if (self.config and hasattr(self.config, "satellites")) else False
        self.satellite_service = SatelliteService(storage=self.storage, config=self.config, parent=self)
        self.satellite_service.tles_updated.connect(self._on_satellites_updated)

        self.elevation_service = ElevationService(parent=self)
        self.elevation_service.profile_ready.connect(self._on_elevation_profile_ready)
        self.elevation_service.profile_error.connect(self._on_elevation_profile_error)

        self.viewshed_service = ViewshedService(parent=self)
        self.viewshed_service.viewshed_ready.connect(self._on_viewshed_ready)
        self.viewshed_service.viewshed_error.connect(self._on_viewshed_error)
        self.viewshed_service.loading_signal.connect(self._on_viewshed_loading)

        self._current_tx_height = 8.0
        self._current_rx_height = 2.0
        self._current_los_radius = 25.0
        self._current_los_center = None
        self._last_p2p_coords = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._do_refresh_map_data)

        self._setup_ui()
        self._subscribe_events()
        if not WEBENGINE_AVAILABLE:
            self._do_refresh_map_data()
            QTimer.singleShot(600, self.map_ready.emit)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Internal compatibility button handles
        self.btn_repeaters = QPushButton("📡 Repeaters")
        self.btn_repeaters.setCheckable(True)
        self.btn_repeaters.clicked.connect(self._on_repeaters_toggle)
        self.btn_links = QPushButton("⚡ Links")
        self.btn_links.setCheckable(True)
        self.btn_links.setChecked(True)
        self.btn_links.clicked.connect(self._on_links_toggle)
        self.btn_path_modes = QPushButton("🛣️ Path Modes")
        self.btn_path_modes.setCheckable(True)
        self.btn_path_modes.setChecked(getattr(self.config.meshcore, "map_show_path_modes", False) if self.config else False)
        self.btn_path_modes.clicked.connect(self._on_path_modes_toggle)
        self.btn_orbitals = QPushButton("🛰️ Orbitals")
        self.btn_orbitals.setCheckable(True)
        self.btn_orbitals.setChecked(getattr(self.config.meshcore, "map_show_companion_orbitals", False) if self.config else False)
        self.btn_orbitals.clicked.connect(self._on_orbitals_toggle)
        self.btn_scopes = QPushButton("🌐 Scopes")
        self.btn_scopes.setCheckable(True)
        self.btn_scopes.setChecked(getattr(self.config.meshcore, "map_show_scopes", False) if self.config else False)
        self.btn_scopes.clicked.connect(self._on_scopes_toggle)
        self.btn_tropo = QPushButton("📡 Tropo")
        self.btn_tropo.setCheckable(True)
        self.btn_tropo.clicked.connect(self._on_tropo_toggle)
        self.btn_adsb = QPushButton("✈️ ADS-B")
        self.btn_adsb.setCheckable(True)
        self.btn_adsb.clicked.connect(self._on_adsb_toggle)
        self.btn_heatmap = QPushButton("🔥 Heatmap")
        self.btn_heatmap.setCheckable(True)
        self.btn_heatmap.clicked.connect(lambda: self.set_activity_heatmap(self.btn_heatmap.isChecked()))
        self.btn_thunderstorm = QPushButton("⛈️ Storms")
        self.btn_thunderstorm.setCheckable(True)
        self.btn_thunderstorm.clicked.connect(lambda: self.set_thunderstorm(self.btn_thunderstorm.isChecked()))
        self.btn_satellites = QPushButton("🛰️ Satellites")
        self.btn_satellites.setCheckable(True)
        self.btn_satellites.setChecked(self.show_satellites)
        self.btn_satellites.clicked.connect(lambda: self.set_satellites(self.btn_satellites.isChecked()))
        self.btn_map_3d = QPushButton("🏔️ 3D View")
        self.btn_map_3d.setCheckable(True)
        self.btn_map_3d.clicked.connect(lambda: self.set_3d_mode(self.btn_map_3d.isChecked()))

        self.stats_badge = QLabel("0 Nodes")
        self.btn_add_node = QPushButton("➕ Add Node")
        self.btn_clear_path = QPushButton("🧹 Clear")
        self.btn_refresh = QPushButton("🔄")

        # Map Area (Goes flush to top edge!)
        if WEBENGINE_AVAILABLE:
            self.web_view = QWebEngineView()
            try:
                self.web_page = LoggingWebEnginePage(self.web_view)
                self.web_view.setPage(self.web_page)
            except Exception as e:
                logger.warning(f"Could not attach LoggingWebEnginePage, using default: {e}")
            self.web_view.setStyleSheet("background-color: #12151A; border: none;")
            self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
            self.channel = QWebChannel()
            self.bridge = WebBridge()
            self.bridge.node_clicked_signal.connect(self._on_bridge_node_clicked)
            self.bridge.map_moved_signal.connect(self._on_bridge_map_moved)
            self.bridge.visualised_path_closed_signal.connect(self._on_visualised_path_closed)
            self.bridge.hop_candidate_selected_signal.connect(self._on_hop_candidate_selected)
            self.bridge.phantom_node_toggled_signal.connect(self._on_phantom_node_toggled)
            self.bridge.node_deleted_signal.connect(self._on_bridge_delete_node)
            self.bridge.repeater_neighbors_cleared_signal.connect(self._on_repeater_neighbors_cleared)
            self.bridge.tropo_stepped_signal.connect(self._on_bridge_tropo_stepped)
            self.bridge.tropo_toggled_signal.connect(self._on_bridge_tropo_toggled)
            self.bridge.node_scope_changed_signal.connect(self._on_node_scope_changed)
            self.bridge.set_adsb_target_signal.connect(self._on_bridge_set_adsb_target)
            self.bridge.adsb_toggled_signal.connect(self._on_bridge_adsb_toggled)
            self.bridge.reset_adsb_target_signal.connect(self._on_bridge_reset_adsb_target)
            self.bridge.activity_timeframe_changed_signal.connect(self._on_bridge_activity_timeframe_changed)
            self.bridge.activity_heatmap_toggled_signal.connect(self._on_bridge_activity_heatmap_toggled)
            self.bridge.new_nodes_timeframe_changed_signal.connect(self._on_bridge_new_nodes_timeframe_changed)
            self.bridge.new_nodes_toggled_signal.connect(self._on_bridge_new_nodes_toggled)
            self.bridge.mqtt_nodes_toggled_signal.connect(self._on_bridge_mqtt_nodes_toggled)
            self.bridge.map_3d_toggled_signal.connect(self._on_bridge_map_3d_toggled)
            self.bridge.mark_all_nodes_known_signal.connect(self._on_bridge_mark_all_nodes_known)
            self.bridge.thunderstorm_toggled_signal.connect(self._on_bridge_thunderstorm_toggled)
            self.bridge.space_weather_toggled_signal.connect(self.set_space_weather)
            self.bridge.space_weather_refresh_signal.connect(lambda: self.space_weather_service.fetch_weather(force=True))
            self.bridge.space_weather_opacity_signal.connect(self._on_space_weather_opacity_changed)
            self.bridge.satellite_toggled_signal.connect(self.set_satellites)
            self.bridge.satellite_refresh_signal.connect(lambda: self.satellite_service.refresh_now(force=True))
            self.bridge.search_node_id_toggled_signal.connect(self._on_bridge_search_node_id_toggled)
            self.bridge.packet_hud_toggled_signal.connect(self._on_bridge_packet_hud_toggled)
            self.bridge.activity_timeline_toggled_signal.connect(self._on_bridge_activity_timeline_toggled)
            self.bridge.map_legend_toggled_signal.connect(self._on_bridge_map_legend_toggled)
            self.bridge.lightning_proximity_alert_signal.connect(self._on_bridge_lightning_proximity_alert)
            self.bridge.map_context_menu_signal.connect(
                lambda lat, lon, x, y: QTimer.singleShot(0, lambda: self._show_map_context_menu(lat, lon, x, y))
            )
            self.bridge.node_context_menu_signal.connect(
                lambda nid, alias, is_rep, is_phant, lat, lon, x, y: QTimer.singleShot(
                    0, lambda: self._show_node_context_menu(nid, alias, is_rep, is_phant, lat, lon, x, y)
                )
            )
            self.bridge.adsb_color_mode_changed_signal.connect(self._on_bridge_adsb_color_mode_changed)
            self.bridge.adsb_proximity_alert_signal.connect(self.adsb_proximity_alert.emit)
            self.bridge.adsb_filters_changed_signal.connect(self._on_bridge_adsb_filters_changed)
            self.bridge.adsb_alert_config_changed_signal.connect(self._on_bridge_adsb_alert_config_changed)
            self.bridge.request_aircraft_photo_signal.connect(self._on_bridge_request_aircraft_photo)
            self.bridge.p2p_path_selected_signal.connect(self._on_p2p_path_selected)
            self.bridge.profile_node_signal.connect(self._on_profile_node_requested)
            self.bridge.calc_node_viewshed_signal.connect(self._on_calc_node_viewshed_requested)
            self.bridge.copy_clipboard_signal.connect(lambda txt: self._notify_user(f"📋 Copied Repeater ID to clipboard: {txt}"))
            self.adsb_service.photo_received.connect(self._on_adsb_photo_received)
            self.channel.registerObject("pyBridge", self.bridge)
            self.web_view.page().setWebChannel(self.channel)
            if hasattr(self.web_view.page(), "renderProcessTerminated"):
                self.web_view.page().renderProcessTerminated.connect(self._on_render_process_terminated)
            self.web_view.loadFinished.connect(self._on_map_loaded)
            self.web_view.setHtml(get_leaflet_html(), QUrl("http://localhost"))

            # Vertical Splitter: 4/5ths map area, 1/5th point-to-point elevation profile
            self.map_splitter = QSplitter(Qt.Orientation.Vertical, self)
            self.map_splitter.setChildrenCollapsible(False)
            self.map_splitter.setStyleSheet("""
                QSplitter::handle {
                    background-color: #1F242D;
                    height: 4px;
                }
                QSplitter::handle:hover {
                    background-color: #10B981;
                }
            """)
            self.map_splitter.addWidget(self.web_view)

            # Map Action Controls (Integrated into the bottom Network Activity Timeline)
            self.floating_controls = QFrame(self.web_view)
            self.floating_controls.hide()

            self.btn_center = QPushButton("📍 Re-center")
            self.btn_center.setToolTip("Center map on active nodes")
            self.btn_center.clicked.connect(self._on_center_clicked)

            self.btn_reset_layers = QPushButton("🧹 Reset Layers")
            self.btn_reset_layers.setToolTip("Reset map layers, clear paths, and restore default view")
            self.btn_reset_layers.clicked.connect(self.reset_map_layers)

            self.btn_age_fade = QPushButton("⏳ Age Fade")
            self.btn_age_fade.setCheckable(True)
            self.btn_age_fade.setToolTip("Toggle Node Age Fading (dim inactive nodes on map)")
            fade_init = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
            self.btn_age_fade.setChecked(fade_init)
            self.btn_age_fade.clicked.connect(self._on_floating_age_fade_clicked)

            self._current_base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
            self.btn_base_map = QPushButton(self._get_base_map_button_label(self._current_base_layer))
            self.btn_base_map.setToolTip("Switch Base Map: CoreScope Dark vs Dark Canvas vs OpenTopoMap (Click to cycle, right-click for menu)")
            self.btn_base_map.clicked.connect(self._on_toggle_base_map_clicked)
            self.btn_base_map.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.btn_base_map.customContextMenuRequested.connect(self._show_base_map_menu)

            # Floating Line-of-Sight & Topographic Profile Controls (Portrait Unified Style)
            self.los_controls = DraggableOverlayFrame(self.web_view)
            self.los_controls.setObjectName("losControlsOverlay")
            self.los_controls.setFixedWidth(238)
            self.los_controls.setStyleSheet("""
                QFrame#losControlsOverlay {
                    background-color: rgba(30, 31, 34, 0.96);
                    border: 1px solid #383A40;
                    border-radius: 8px;
                }
                QPushButton {
                    background-color: #2B2D31;
                    color: #DBDEE1;
                    border: 1px solid #383A40;
                    padding: 3px 4px;
                    font-size: 10px;
                    font-weight: 600;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #35373C;
                    color: #FFFFFF;
                    border-color: #4E5058;
                }
                QPushButton:checked {
                    background-color: #23A55A;
                    color: #FFFFFF;
                    font-weight: 700;
                    border-color: #23A55A;
                }
                QComboBox {
                    background-color: #2B2D31;
                    color: #DBDEE1;
                    border: 1px solid #383A40;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 10.5px;
                    font-weight: 600;
                }
                QComboBox:hover {
                    border-color: #4E5058;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 16px;
                }
                QComboBox QAbstractItemView {
                    background-color: #1E1F22;
                    color: #DBDEE1;
                    selection-background-color: #23A55A;
                    selection-color: #FFFFFF;
                    border: 1px solid #383A40;
                    outline: none;
                }
            """)
            los_main_layout = QVBoxLayout(self.los_controls)
            los_main_layout.setContentsMargins(9, 7, 9, 8)
            los_main_layout.setSpacing(5)

            # Header row with drag handle, title, and close button
            hdr_layout = QHBoxLayout()
            hdr_layout.setContentsMargins(0, 0, 0, 0)
            hdr_layout.setSpacing(4)

            lbl_grip = QLabel("⠿")
            lbl_grip.setStyleSheet("color: #4E5058; font-size: 12px; font-weight: bold; background: transparent; border: none;")
            hdr_layout.addWidget(lbl_grip)

            lbl_los_title = QLabel("📡 LINE-OF-SIGHT & TOPO")
            lbl_los_title.setStyleSheet("color: #F2F3F5; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; background: transparent; border: none;")
            hdr_layout.addWidget(lbl_los_title, 1)

            self.btn_close_los = QPushButton("✕")
            self.btn_close_los.setObjectName("losCloseBtn")
            self.btn_close_los.setToolTip("Close LOS & Topo panel")
            self.btn_close_los.setFixedSize(18, 18)
            self.btn_close_los.setStyleSheet("""
                QPushButton#losCloseBtn {
                    background: transparent;
                    color: #80848E;
                    border: none;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 0;
                    border-radius: 3px;
                }
                QPushButton#losCloseBtn:hover {
                    background-color: #ED4245;
                    color: #FFFFFF;
                }
            """)
            self.btn_close_los.clicked.connect(lambda: self.set_los_view_active(False))
            hdr_layout.addWidget(self.btn_close_los)
            los_main_layout.addLayout(hdr_layout)

            # Antenna Height section
            lbl_height = QLabel("ANTENNA HEIGHT (TX/RX)")
            lbl_height.setStyleSheet("color: #949BA4; font-size: 9px; font-weight: 700; letter-spacing: 0.5px; background: transparent; border: none;")
            los_main_layout.addWidget(lbl_height)

            pills_layout = QHBoxLayout()
            pills_layout.setContentsMargins(0, 0, 0, 0)
            pills_layout.setSpacing(3)

            self.btn_los_ground = QPushButton("Ground (2m)")
            self.btn_los_ground.setCheckable(True)
            self.btn_los_ground.setToolTip("Handheld / mobile antenna (2m AGL)")
            self.btn_los_ground.setFixedHeight(22)

            self.btn_los_rooftop = QPushButton("Rooftop (8m)")
            self.btn_los_rooftop.setCheckable(True)
            self.btn_los_rooftop.setChecked(True)
            self.btn_los_rooftop.setToolTip("Residential chimney / eaves mount (8m AGL)")
            self.btn_los_rooftop.setFixedHeight(22)

            self.btn_los_mast = QPushButton("Mast (15m)")
            self.btn_los_mast.setCheckable(True)
            self.btn_los_mast.setToolTip("High mast / tower mount (15m AGL)")
            self.btn_los_mast.setFixedHeight(22)

            self.height_group = QButtonGroup(self)
            self.height_group.addButton(self.btn_los_ground, 2)
            self.height_group.addButton(self.btn_los_rooftop, 8)
            self.height_group.addButton(self.btn_los_mast, 15)
            self.height_group.idClicked.connect(self._on_los_height_button_clicked)

            pills_layout.addWidget(self.btn_los_ground)
            pills_layout.addWidget(self.btn_los_rooftop)
            pills_layout.addWidget(self.btn_los_mast)
            los_main_layout.addLayout(pills_layout)

            # Radius section
            lbl_radius = QLabel("VIEWSHED RADIUS")
            lbl_radius.setStyleSheet("color: #949BA4; font-size: 9px; font-weight: 700; letter-spacing: 0.5px; background: transparent; border: none;")
            los_main_layout.addWidget(lbl_radius)

            self.combo_los_radius = QComboBox()
            self.combo_los_radius.addItems(["15 km", "25 km", "50 km"])
            self.combo_los_radius.setCurrentText("25 km")
            self.combo_los_radius.setFixedHeight(24)
            self.combo_los_radius.currentTextChanged.connect(self._on_los_radius_changed)
            los_main_layout.addWidget(self.combo_los_radius)

            # Action buttons: Viewshed & Profile
            actions_row = QHBoxLayout()
            actions_row.setContentsMargins(0, 0, 0, 0)
            actions_row.setSpacing(4)

            self.btn_toggle_los = QPushButton("🟢 Viewshed")
            self.btn_toggle_los.setCheckable(True)
            self.btn_toggle_los.setFixedHeight(24)
            self.btn_toggle_los.setToolTip("Toggle 360° Terrain-Aware RF Line-of-Sight Viewshed Coverage")
            self.btn_toggle_los.clicked.connect(self._on_los_toggle_clicked)
            actions_row.addWidget(self.btn_toggle_los)

            self.btn_profile_path = QPushButton("🏔️ Profile")
            self.btn_profile_path.setCheckable(True)
            self.btn_profile_path.setFixedHeight(24)
            self.btn_profile_path.setToolTip("Click two points on the map to profile topographic elevation & 1st Fresnel zone clearance")
            self.btn_profile_path.clicked.connect(self._on_profile_path_toggle_clicked)
            actions_row.addWidget(self.btn_profile_path)
            los_main_layout.addLayout(actions_row)

            # Clear button row
            self.btn_clear_los = QPushButton("🧹 Clear Overlays")
            self.btn_clear_los.setToolTip("Clear Viewshed and Path Profile overlays")
            self.btn_clear_los.setFixedHeight(22)
            self.btn_clear_los.setStyleSheet("""
                QPushButton {
                    background-color: #232428;
                    color: #949BA4;
                    border: 1px solid #383A40;
                    font-size: 10px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #35373C;
                    color: #F2F3F5;
                }
            """)
            self.btn_clear_los.clicked.connect(self._on_clear_los_clicked)
            los_main_layout.addWidget(self.btn_clear_los)

            self.los_controls.adjustSize()
            show_los = getattr(self.config, "map_show_rf_los", False) if self.config else False
            self.los_controls.setVisible(show_los)
        else:
            self._current_base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
            self.btn_base_map = QPushButton(self._get_base_map_button_label(self._current_base_layer))
            self.btn_base_map.clicked.connect(self._on_toggle_base_map_clicked)
            self.btn_age_fade = QPushButton("⏳ Age Fade")
            self.btn_age_fade.setCheckable(True)
            fade_init = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
            self.btn_age_fade.setChecked(fade_init)
            self.btn_age_fade.clicked.connect(self._on_floating_age_fade_clicked)
            self.btn_center = QPushButton("📍 Re-center")
            self.btn_center.clicked.connect(self._on_center_clicked)
            self.btn_reset_layers = QPushButton("🧹 Reset Layers")
            self.btn_reset_layers.clicked.connect(self.reset_map_layers)
            self.floating_controls = QFrame(self)
            self.floating_controls.hide()

            self.fallback_label = QLabel(
                "🗺️ <b>Mesh Topology & Nodes</b><br><br>"
                "WebEngine is not active in this environment.<br>"
                "Node coordinates and RF links are active and monitored in the background."
            )
            self.fallback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.fallback_label.setStyleSheet("""
                background-color: #1C1C1C;
                color: #9CA3AF;
                border: none;
                padding: 16px;
            """)
            self.map_splitter = QSplitter(Qt.Orientation.Vertical, self)
            self.map_splitter.addWidget(self.fallback_label)

        # Bottom Dock: Point-to-Point Topographic RF Elevation Profile Widget (1/5th split)
        self.elevation_profile_dock = ElevationProfileWidget(self.map_splitter)
        self.elevation_profile_dock.close_requested.connect(self._on_close_elevation_profile)
        self.elevation_profile_dock.point_scrubbed.connect(self._on_dock_point_scrubbed)
        self.elevation_profile_dock.scrub_cleared.connect(self._on_dock_scrub_cleared)
        self.elevation_profile_dock.heights_changed.connect(self._on_dock_heights_changed)
        self.elevation_profile_dock.hide()
        self.map_splitter.addWidget(self.elevation_profile_dock)

        # Bottom Dock: CoreScope Network Activity Timeline Widget (Standard Feature)
        self.activity_timeline_dock = NetworkActivityTimelineWidget(
            storage=self.storage,
            config=self.config,
            parent=self.map_splitter
        )
        self.activity_timeline_dock.set_map_controls(
            self.btn_center,
            self.btn_reset_layers,
            self.btn_age_fade,
            self.btn_base_map
        )
        self.activity_timeline_dock.close_requested.connect(
            lambda: self.set_activity_timeline_visible(False)
        )
        self.map_splitter.addWidget(self.activity_timeline_dock)
        if not self.show_activity_timeline:
            self.activity_timeline_dock.hide()
        else:
            self.activity_timeline_dock.show()

        self.map_splitter.setStretchFactor(0, 4)
        self.map_splitter.setStretchFactor(1, 1)
        self.map_splitter.setChildrenCollapsible(False)

        # Horizontal container for optional layer dock and map splitter
        self.map_content_row = QWidget()
        self.map_content_layout = QHBoxLayout(self.map_content_row)
        self.map_content_layout.setContentsMargins(0, 0, 0, 0)
        self.map_content_layout.setSpacing(0)
        self.map_content_layout.addWidget(self.map_splitter, 1)

        layout.addWidget(self.map_content_row, 1)

        # Purple Status Bar Below Map (Status of flood messages & watcher)
        self.watcher_status = QLabel("⚡ Watcher: Listening for live RF packet paths...")
        self.watcher_status.setStyleSheet("""
            background-color: #1A1B1E;
            color: #C084FC;
            border-top: 1px solid #2E3035;
            padding: 5px 12px;
            font-size: 11px;
            font-family: monospace;
            font-weight: bold;
        """)
        layout.addWidget(self.watcher_status)

        self.reforming_overlay = ReformingMapOverlay(self)
        if WEBENGINE_AVAILABLE:
            self.reforming_overlay.show_reforming(
                message="Stage 1/4: Initializing map engine & cache...",
                title="INITIALIZING MESH MAP",
                stage=1,
                percent=25,
            )

    def attach_layer_dock(self, dock: QWidget):
        """Attaches the vertical MapLayerDockWidget to the left edge of the map canvas."""
        if hasattr(self, "map_content_layout"):
            self.map_content_layout.insertWidget(0, dock)

    def pause_geometry_motion(self):
        """Temporarily pauses JS dispatch and watchdog during window moves, resizes or splitter drags.
        
        This prevents compositor buffer contention and guarantees no failsafes, reloads, or
        recovery alerts are triggered during user resizing.
        """
        self._geometry_in_motion = True
        self._last_geometry_motion_time = time.time()
        self._watchdog_grace_until = time.time() + 60.0
        self._watchdog_unanswered = 0
        if hasattr(self, "reforming_overlay") and self.reforming_overlay.isVisible():
            self.reforming_overlay.setGeometry(self.rect())
        self._reposition_floating_controls()
        self._schedule_map_invalidate(delay_ms=250)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.pause_geometry_motion()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Window maximized, restored, or fullscreened.
            self.pause_geometry_motion()

    def _reposition_floating_controls(self):
        if hasattr(self, "floating_controls") and not self.floating_controls.isHidden():
            self.floating_controls.move(10, 10)
            self.floating_controls.raise_()
        if hasattr(self, "los_controls") and hasattr(self, "web_view"):
            if getattr(self.los_controls, "_user_moved", False):
                return
            if hasattr(self, "floating_controls") and not self.floating_controls.isHidden():
                fl_w = self.floating_controls.width()
                los_w = self.los_controls.width()
                try:
                    web_w = int(self.web_view.width())
                except Exception:
                    web_w = 800
                if fl_w + los_w + 30 <= web_w:
                    self.los_controls.move(fl_w + 20, 10)
                else:
                    self.los_controls.move(10, self.floating_controls.height() + 16)
            else:
                self.los_controls.move(10, 10)
            self.los_controls.raise_()

    def run_js(self, script: str, callback: Optional[Callable] = None):
        """Safely executes JavaScript in the WebEngine view if ready.

        When callback is None, ensures the script terminates with 'void 0;'
        so that Chromium's V8 engine evaluates to 'undefined' and skips
        recursively serializing complex or circular JavaScript objects
        (such as Leaflet L.Map instances) across Mojo IPC into Python.
        """
        if not (WEBENGINE_AVAILABLE and hasattr(self, "web_view") and getattr(self, "_page_ready", False)):
            return
        if not hasattr(self.web_view, "page"):
            return
        page = self.web_view.page()
        if not page:
            return
        try:
            if callback is None:
                trimmed = script.strip()
                if not trimmed.endswith(";"):
                    trimmed += ";"
                if not trimmed.endswith("void 0;"):
                    trimmed += " void 0;"
                page.runJavaScript(trimmed)
            else:
                page.runJavaScript(script, callback)
        except Exception as e:
            logger.warning(f"Error executing run_js: {e}")

    def moveEvent(self, event):
        super().moveEvent(event)
        self._connect_screen_listener()
        self.pause_geometry_motion()

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_screen_listener()
        self._start_renderer_watchdog()
        self.run_js("if (typeof map !== 'undefined') map.invalidateSize(false);")

    def _connect_screen_listener(self):
        try:
            win = self.window()
            if win and win.windowHandle():
                handle = win.windowHandle()
                try:
                    handle.screenChanged.disconnect(self._on_window_screen_changed)
                except Exception:
                    pass
                handle.screenChanged.connect(self._on_window_screen_changed)
        except Exception as e:
            logger.debug(f"Could not connect screenChanged listener: {e}")

    def _on_window_screen_changed(self, new_screen):
        if new_screen:
            dpr = new_screen.devicePixelRatio()
            geo = new_screen.geometry()
            logger.info(f"Window moved to screen {new_screen.name()} (DPR={dpr}, bounds={geo.width()}x{geo.height()})")
            self._schedule_map_invalidate(delay_ms=250)

    def _schedule_map_invalidate(self, delay_ms: int = 150):
        if not hasattr(self, "_map_resize_timer"):
            self._map_resize_timer = QTimer(self)
            self._map_resize_timer.setSingleShot(True)
            self._map_resize_timer.timeout.connect(self._on_debounced_map_resize)
        self._map_resize_timer.stop()
        self._map_resize_timer.start(delay_ms)

    def _on_debounced_map_resize(self):
        self._geometry_in_motion = False
        self.run_js("if (typeof map !== 'undefined' && map) map.invalidateSize(false);")
        if getattr(self, "_initial_loading_active", False):
            if hasattr(self, "_stagger_timer") and not self._stagger_timer.isActive():
                self._stagger_timer.start(100)
        elif getattr(self, "_pending_refresh", False):
            self._pending_refresh = False
            self._do_refresh_map_data()

    def _on_stagger_timer_timeout(self):
        if not getattr(self, "_initial_loading_active", False):
            return
        # If the window is currently in motion (resizing/moving), wait for stillness!
        if getattr(self, "_geometry_in_motion", False):
            if time.time() - getattr(self, "_last_geometry_motion_time", 0.0) >= 0.35:
                self._geometry_in_motion = False
            else:
                self._stagger_timer.start(150)
                return

        if self._stagger_stage == 2:
            # Stage 2 complete: Viewport geometry is now settled
            if self.config and hasattr(self.config.meshcore, "map_center_lat") and self.config.meshcore.map_center_lat is not None and self.config.meshcore.map_center_lon is not None:
                z = self.config.meshcore.map_zoom or 8
                lon = ((float(self.config.meshcore.map_center_lon) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
                lat = max(-85.0, min(85.0, float(self.config.meshcore.map_center_lat)))
                self.run_js(f"map.setView([{lat}, {lon}], {z}); window._initialViewSet = true; map.invalidateSize(false);")
            else:
                self.run_js("if (typeof map !== 'undefined' && map) map.invalidateSize(false);")

            fading = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
            self.set_freshness_fading(fading)
            self.apply_colors()

            self._stagger_stage = 3
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.show_reforming(
                    message="Stage 3/4: Loading cached mesh nodes...",
                    title="INITIALIZING MESH MAP",
                    stage=3,
                    percent=75,
                )
            self._stagger_timer.start(300)
            return

        elif self._stagger_stage == 3:
            # Stage 3: Load nodes from SQLite disk cache
            self._do_refresh_map_data()

            self._stagger_stage = 4
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.show_reforming(
                    message="Stage 4/4: Ready • Synchronizing radio streams...",
                    title="INITIALIZING MESH MAP",
                    stage=4,
                    percent=100,
                )
            self._stagger_timer.start(400)
            return

        elif self._stagger_stage == 4:
            # Stage 4 complete: Final release of the map view!
            self._initial_loading_active = False
            self._stagger_stage = 0
            if getattr(self, "_pending_refresh", False):
                self._pending_refresh = False
                self._do_refresh_map_data()
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.hide_reforming()
            if self.show_packet_hud:
                self.run_js("if (window.setPacketHudVisible) window.setPacketHudVisible(true);")
            if getattr(self, "show_map_legend", False):
                self.run_js("if (window.toggleMapLegend) window.toggleMapLegend(true);")
            if getattr(self, "show_satellites", False):
                self.set_satellites(True)
            self.map_ready.emit()
            logger.info("Mesh map staggered initialization completed successfully.")

    def _start_renderer_watchdog(self):
        if not hasattr(self, "_renderer_watchdog_timer"):
            self._renderer_watchdog_timer = QTimer(self)
            self._renderer_watchdog_timer.setInterval(10000)  # 10s heartbeat
            self._renderer_watchdog_timer.timeout.connect(self._check_renderer_watchdog)
            self._watchdog_unanswered = 0
            self._watchdog_grace_until = 0.0
        if not self._renderer_watchdog_timer.isActive():
            self._renderer_watchdog_timer.start()

    def _reset_watchdog_activity(self):
        """Called whenever WebBridge receives user interaction or data signals (proves JS runtime is alive)."""
        self._watchdog_unanswered = 0

    def _check_renderer_watchdog(self):
        if not (WEBENGINE_AVAILABLE and hasattr(self, "web_view")):
            return

        # If page is currently marked unready, monitor recovery deadline rather than silencing checks
        if not getattr(self, "_page_ready", False):
            if getattr(self, "_recovery_in_progress", False):
                now = time.time()
                recovery_start = getattr(self, "_recovery_start_time", 0.0)
                if now - recovery_start > 15.0:
                    attempts = getattr(self, "_recovery_attempts", 0) + 1
                    self._recovery_attempts = attempts
                    if attempts <= 3:
                        logger.warning(
                            f"WebEngine page recovery deadline exceeded (attempt {attempts}/3). "
                            "Forcing fresh LoggingWebEnginePage..."
                        )
                        self._recovery_start_time = now
                        self._force_fresh_page_recovery()
                    else:
                        logger.error("WebEngine page recovery exceeded maximum retry budget (3 attempts).")
                        self._recovery_in_progress = False
            return

        if getattr(self, "_geometry_in_motion", False) or getattr(self, "_initial_loading_active", False):
            self._watchdog_unanswered = 0
            self._watchdog_probe_inflight = False
            return
        win = self.window()
        if win and (win.isMinimized() or not win.isVisible()):
            self._watchdog_unanswered = 0
            self._watchdog_probe_inflight = False
            return
        if not self.isVisible():
            self._watchdog_unanswered = 0
            self._watchdog_probe_inflight = False
            return
        now = time.time()
        if now < getattr(self, "_watchdog_grace_until", 0.0):
            self._watchdog_unanswered = 0
            self._watchdog_probe_inflight = False
            return

        # Bound probe concurrency: don't stack probes if previous is still unanswered
        if getattr(self, "_watchdog_probe_inflight", False):
            self._watchdog_unanswered += 1
        else:
            self._watchdog_probe_inflight = True
            self._watchdog_probe_token = getattr(self, "_watchdog_probe_token", 0) + 1

        token = getattr(self, "_watchdog_probe_token", 0)

        # Require 10 consecutive unanswered pings outside grace period (100 seconds of complete silence)
        if self._watchdog_unanswered >= 10:
            logger.warning(
                f"WebEngine renderer process unresponsive ({self._watchdog_unanswered} consecutive watchdog timeouts over 100s). "
                "Triggering graceful view recovery..."
            )
            self._watchdog_unanswered = 0
            self._watchdog_probe_inflight = False
            self._page_ready = False
            self._recovery_in_progress = True
            self._recovery_start_time = time.time()
            self._recovery_attempts = 1

            # Forcibly terminate the hung renderer process so RAM is freed and Qt catches termination cleanly
            try:
                page = self.web_view.page() if hasattr(self, "web_view") else None
                pid = page.renderProcessPid() if (page and hasattr(page, "renderProcessPid")) else 0
                if pid and pid > 0:
                    logger.warning(f"Terminating unresponsive WebEngine renderer PID {pid} to force clean recovery...")
                    import os, signal
                    os.kill(pid, signal.SIGKILL)
                    # When killed, Qt emits renderProcessTerminated which schedules _recover_web_view_after_termination
                    return
            except Exception as e:
                logger.warning(f"Could not kill unresponsive renderer PID: {e}")

            self._recover_web_view_after_termination()
            return

        try:
            self.web_view.page().runJavaScript("1 + 1;", lambda res, t=token: self._on_watchdog_pong(res, t))
        except Exception as e:
            self._watchdog_probe_inflight = False
            logger.warning(f"Error issuing watchdog ping to WebEngine: {e}")

    def _on_watchdog_pong(self, result, token=None):
        if token is not None and token != getattr(self, "_watchdog_probe_token", None):
            # Ignore stale response from earlier probe cycle
            return
        self._watchdog_probe_inflight = False
        if result == 2:
            self._watchdog_unanswered = 0

    def _on_render_process_terminated(self, termination_status, exit_code):
        logger.warning(f"WebEngine render process terminated ({termination_status}, code={exit_code}). Auto-recovering map view...")
        self._page_ready = False
        QTimer.singleShot(250, self._recover_web_view_after_termination)

    def _recover_web_view_after_termination(self):
        try:
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.show_reforming("Reforming Map View...")

            # Safety timeout: auto-dismiss recovery overlay after 8s so user is never locked out
            def _dismiss_recovery_overlay():
                if hasattr(self, "reforming_overlay") and not getattr(self, "_initial_loading_active", False):
                    logger.info("Recovery safety timeout reached; dismissing reforming overlay.")
                    self.reforming_overlay.hide_reforming()
            QTimer.singleShot(8000, _dismiss_recovery_overlay)

            if hasattr(self, "web_view"):
                logger.info("Reloading Leaflet map HTML after WebEngine render process recovery...")
                page = self.web_view.page()
                if page:
                    try:
                        # Soft recovery: avoid page.setWebChannel() on existing page to prevent Qt 6.11 C++ crash!
                        self.web_view.setHtml(get_leaflet_html(), QUrl("http://localhost"))
                        return
                    except Exception as e:
                        logger.warning(f"Could not perform soft recovery on existing page: {e}")
                self._force_fresh_page_recovery()
        except Exception as e:
            logger.error(f"Failed recovering web view: {e}")

    def _force_fresh_page_recovery(self):
        """Forces attachment of a clean LoggingWebEnginePage and re-registers the WebChannel."""
        if not hasattr(self, "web_view"):
            return
        try:
            new_channel = QWebChannel(self.web_view)
            new_channel.registerObject("pyBridge", self.bridge)
            new_page = LoggingWebEnginePage(self.web_view)
            new_page.setWebChannel(new_channel)
            self.channel = new_channel
            self.web_page = new_page
            self.web_view.setPage(new_page)
            if hasattr(new_page, "renderProcessTerminated"):
                new_page.renderProcessTerminated.connect(self._on_render_process_terminated)
            self.web_view.setHtml(get_leaflet_html(), QUrl("http://localhost"))
        except Exception as e:
            logger.warning(f"Could not attach fresh LoggingWebEnginePage on recovery: {e}")

    def _btn_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background-color: #464C5A;
                    color: #FFFFFF;
                    border: 1px solid #60687A;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """
        return """
            QPushButton {
                background-color: #2B2F38;
                color: #E5E7EB;
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #353A45;
                border-color: #60687A;
                color: #FFFFFF;
            }
        """

    def _subscribe_events(self):
        bus.subscribe(EventType.MAP_NODES_UPDATED, lambda _: self.refresh_map_data(debounce=True))
        bus.subscribe(EventType.NEIGHBOURS_UPDATED, lambda _: self.refresh_map_data(debounce=True))
        bus.subscribe(EventType.FAVORITES_UPDATED, lambda _: self.refresh_map_data(debounce=True))
        bus.subscribe(EventType.PACKET_PATH_TRACED, self._on_packet_path_traced)
        bus.subscribe(EventType.MESSAGE_RECEIVED, self._on_message_received)
        bus.subscribe(EventType.SETTINGS_UPDATED, self._on_settings_updated)
        bus.subscribe(EventType.VISUALISE_MESSAGE_PATH, self.visualise_message_path)
        bus.subscribe(EventType.CLEAR_VISUALISED_PATHS, lambda _: self.clear_visualised_path())

    def _on_settings_updated(self, cfg: AppConfig):
        self.config = cfg
        self.apply_colors(getattr(cfg, "app_colors", None))
        fading = getattr(cfg.meshcore, "node_freshness_fading", True)
        self.set_freshness_fading(fading)
        if hasattr(self, "btn_path_modes"):
            show_pm = getattr(cfg.meshcore, "map_show_path_modes", False)
            self.btn_path_modes.blockSignals(True)
            self.btn_path_modes.setChecked(show_pm)
            self.btn_path_modes.setStyleSheet(self._btn_style(active=show_pm))
            self.btn_path_modes.blockSignals(False)
            pm_str = "true" if show_pm else "false"
            self.run_js(f"setPathModesVisible({pm_str});")
        if hasattr(self, "btn_orbitals"):
            show_orb = getattr(cfg.meshcore, "map_show_companion_orbitals", False)
            self.btn_orbitals.blockSignals(True)
            self.btn_orbitals.setChecked(show_orb)
            self.btn_orbitals.setStyleSheet(self._btn_style(active=show_orb))
            self.btn_orbitals.blockSignals(False)
        if hasattr(self, "btn_scopes"):
            show_scopes = getattr(cfg.meshcore, "map_show_scopes", False)
            self.btn_scopes.blockSignals(True)
            self.btn_scopes.setChecked(show_scopes)
            self.btn_scopes.setStyleSheet(self._btn_style(active=show_scopes))
            self.btn_scopes.blockSignals(False)
            if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
                self._update_scope_overlays()
        carto_key = getattr(cfg, "carto_api_key", "")
        self.run_js(f"if (window.setCartoApiKey) window.setCartoApiKey('{carto_key}');")
        self.refresh_map_data()

    def _on_map_loaded(self, ok: bool):
        logger.info(f"Leaflet map loaded in WebEngine (ok={ok})")
        if not ok:
            logger.warning("Leaflet map load failed in WebEngine.")
            self._initial_loading_active = False
            if getattr(self, "_failed_recovery_retries", 0) < 1:
                self._failed_recovery_retries = getattr(self, "_failed_recovery_retries", 0) + 1
                logger.info("Retrying recovery with fresh LoggingWebEnginePage and WebChannel...")
                QTimer.singleShot(300, self._force_fresh_page_recovery)
                return
            self._failed_recovery_retries = 0
            self._recovery_in_progress = False
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.hide_reforming()
            return

        self._failed_recovery_retries = 0
        self._recovery_in_progress = False
        self._recovery_attempts = 0
        self._watchdog_probe_inflight = False
        self._page_ready = True
        self._watchdog_unanswered = 0
        self._connect_screen_listener()
        self._start_renderer_watchdog()

        carto_key = getattr(self.config, "carto_api_key", "") if self.config else ""
        if carto_key:
            self.run_js(f"if (window.setCartoApiKey) window.setCartoApiKey('{carto_key}');")

        base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
        if base_layer in ("topo", "corescope", "carto"):
            self.run_js(f"if (window.setBaseMapLayer) window.setBaseMapLayer('{base_layer}');")

        if getattr(self, "_pending_neighbors_payload", None):
            try:
                p = self._pending_neighbors_payload
                self._pending_neighbors_payload = None
                js_payload = json.dumps(p)
                self.run_js(f"drawRepeaterNeighbors({js_payload});")
            except Exception as e:
                logger.warning(f"Error drawing pending repeater neighbors on map load: {e}")

        if getattr(self, "_initial_loading_active", False):
            self._geometry_in_motion = False
            self._stagger_stage = 2
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.show_reforming(
                    message="Stage 2/4: Map engine ready • Settling viewport geometry...",
                    title="INITIALIZING MESH MAP",
                    stage=2,
                    percent=50,
                )
            if hasattr(self, "_stagger_timer"):
                self._stagger_timer.start(350)
        else:
            if hasattr(self, "reforming_overlay"):
                self.reforming_overlay.hide_reforming()
            self.refresh_map_data()
            if getattr(self, "show_satellites", False):
                self.set_satellites(True)
            if getattr(self, "show_companion_orbitals", False):
                self.set_orbitals(True)
            if self.show_packet_hud:
                self.run_js("if (window.setPacketHudVisible) window.setPacketHudVisible(true);")
            if getattr(self, "show_map_legend", False):
                self.run_js("if (window.toggleMapLegend) window.toggleMapLegend(true);")
            self.map_ready.emit()

    def _on_path_modes_toggle(self):
        visible = self.btn_path_modes.isChecked()
        self.btn_path_modes.setStyleSheet(self._btn_style(active=visible))
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_path_modes = visible
            try:
                self.config.save()
            except Exception:
                pass
        pm_str = "true" if visible else "false"
        self.run_js(f"setPathModesVisible({pm_str});")

    def _on_orbitals_toggle(self):
        self.set_orbitals(self.btn_orbitals.isChecked())

    def _on_scopes_toggle(self):
        visible = self.btn_scopes.isChecked()
        self.btn_scopes.setStyleSheet(self._btn_style(active=visible))
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_scopes = visible
            try:
                self.config.save()
            except Exception:
                pass
        self._update_scope_overlays()

    def _update_scope_overlays(self):
        if not WEBENGINE_AVAILABLE or not hasattr(self, "web_view") or not self._page_ready:
            return
        visible = self.btn_scopes.isChecked() if hasattr(self, "btn_scopes") else False
        if visible and self.storage and hasattr(self.storage, "get_repeaters_by_scope"):
            try:
                scope_data = self.storage.get_repeaters_by_scope()
            except Exception as e:
                logger.error(f"Error fetching repeaters by scope: {e}")
                scope_data = {}
            js_data = json.dumps(scope_data)
            self.run_js(f"setScopeOverlaysVisible(true, {js_data});")
        else:
            self.run_js("setScopeOverlaysVisible(false, {});")

    def _on_node_scope_changed(self, node_id: str, scope_name: str):
        """Called from Leaflet when operator changes or removes/excludes a repeater from scope."""
        if self.storage and hasattr(self.storage, "save_contact_scope"):
            self.storage.save_contact_scope(node_id, scope_name=scope_name)
        self._update_scope_overlays()
        self.refresh_map_data()

    def _on_tropo_toggle(self, checked: Optional[bool] = None):
        if checked is None:
            checked = self.btn_tropo.isChecked()
        self.btn_tropo.setChecked(checked)
        self.btn_tropo.setStyleSheet(self._btn_style(active=checked))
        if checked:
            self.btn_tropo.setText("📡 Tropo (fetching...)")
            self.tropo_service.fetch_forecast(offset_hours=0)
        else:
            self.btn_tropo.setText("📡 Tropo")
            self.run_js("clearTropoLayer();")

    def _on_bridge_tropo_stepped(self, delta_hours: int):
        self.btn_tropo.setText("📡 Tropo (fetching...)")
        self.tropo_service.step_forecast(delta_hours)

    def _on_bridge_tropo_toggled(self, enabled: bool):
        self._on_tropo_toggle(enabled)

    def _on_tropo_forecast_loading(self, status_msg: str):
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"📡 <b>Tropo:</b> {status_msg}")

    def _on_tropo_forecast_ready(self, grid_dict: dict, filename: str, display_label: str):
        self.btn_tropo.setText("📡 Tropo")
        self.btn_tropo.setStyleSheet(self._btn_style(active=True))
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"📡 <b>Tropo:</b> Loaded {display_label} ({filename})")
        payload = json.dumps({"grid": grid_dict, "filename": filename, "label": display_label})
        self.run_js(f"window.onTropoDataReady({payload});")

    def _on_tropo_forecast_error(self, err_msg: str):
        self.btn_tropo.setText("📡 Tropo")
        self.btn_tropo.setChecked(False)
        self.btn_tropo.setStyleSheet(self._btn_style(active=False))
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⚠️ <b>Tropo Error:</b> {err_msg}")
        self.run_js("clearTropoLayer();")

    def _on_adsb_toggle(self, checked: Optional[bool] = None):
        if checked is None:
            checked = self.btn_adsb.isChecked()
        self.btn_adsb.setChecked(checked)
        self.btn_adsb.setStyleSheet(self._btn_style(active=checked))
        self.set_adsb(checked)

    def set_adsb(self, visible: bool):
        self.show_adsb = bool(visible)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_adsb = self.show_adsb
            try:
                self.config.save()
            except Exception:
                pass
        if hasattr(self, "btn_adsb"):
            self.btn_adsb.blockSignals(True)
            self.btn_adsb.setChecked(self.show_adsb)
            self.btn_adsb.setStyleSheet(self._btn_style(active=self.show_adsb))
            self.btn_adsb.blockSignals(False)

        # Initialize target if needed
        if self.adsb_service.target_lat is None or self.adsb_service.target_lon is None:
            target_nid = getattr(self.config.meshcore, "adsb_target_node_id", "") if (self.config and hasattr(self.config, "meshcore")) else ""
            target_alias = getattr(self.config.meshcore, "adsb_target_alias", "") if (self.config and hasattr(self.config, "meshcore")) else ""
            radius = getattr(self.config.meshcore, "adsb_radius_nm", 50) if (self.config and hasattr(self.config, "meshcore")) else 50
            found_coords = False
            if target_nid and self.storage:
                contact = self.storage.get_contact(target_nid)
                if contact and contact.latitude is not None and contact.longitude is not None:
                    self.adsb_service.set_target(target_nid, contact.alias or target_alias or target_nid, contact.latitude, contact.longitude, radius)
                    found_coords = True
            if not found_coords:
                local_lat = getattr(self.config.meshcore, "latitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
                local_lon = getattr(self.config.meshcore, "longitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
                local_alias = getattr(self.config.meshcore, "node_alias", "Local Node") if (self.config and hasattr(self.config, "meshcore")) else "Local Node"
                self.adsb_service.set_target_from_local_or_default(local_lat, local_lon, alias=local_alias)

        self.adsb_service.set_enabled(self.show_adsb)
        vis_str = "true" if self.show_adsb else "false"
        self.run_js(f"setAdsbVisible({vis_str});")
        if self.show_adsb:
            filter_cats = getattr(self.config.meshcore, "adsb_filter_categories", None) if (self.config and hasattr(self.config, "meshcore")) else None
            alert_en = getattr(self.config.meshcore, "adsb_alert_enabled", True) if (self.config and hasattr(self.config, "meshcore")) else True
            alert_cats = getattr(self.config.meshcore, "adsb_alert_categories", None) if (self.config and hasattr(self.config, "meshcore")) else None
            alert_rad = getattr(self.config.meshcore, "adsb_alert_radius_mi", 10.0) if (self.config and hasattr(self.config, "meshcore")) else 10.0
            js_init = f"if (window.setInitialAdsbConfig) window.setInitialAdsbConfig({json.dumps(filter_cats)}, {str(alert_en).lower()}, {json.dumps(alert_cats)}, {alert_rad});"
            self.run_js(js_init)

    def set_adsb_target(self, node_id: str, alias: str, lat: float, lon: float, radius_nm: int = 50):
        """Sets the center point for ADS-B queries to a specific node."""
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.adsb_target_node_id = str(node_id or "")
            self.config.meshcore.adsb_target_alias = str(alias or "")
            self.config.meshcore.adsb_radius_nm = int(radius_nm)
            try:
                self.config.save()
            except Exception:
                pass
        self.run_js("if (window.clearAdsbForRetarget) window.clearAdsbForRetarget();")
        self.adsb_service.set_target(node_id, alias, lat, lon, radius_nm)
        if not self.show_adsb:
            self.set_adsb(True)

    def _on_bridge_set_adsb_target(self, node_id: str, alias: str, lat: float, lon: float):
        self._reset_watchdog_activity()
        radius = getattr(self.config.meshcore, "adsb_radius_nm", 50) if (self.config and hasattr(self.config, "meshcore")) else 50
        self.set_adsb_target(node_id, alias, lat, lon, radius)

    def _on_bridge_adsb_toggled(self, enabled: bool):
        self._reset_watchdog_activity()
        self.set_adsb(enabled)
        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_adsb"):
            p.nav_dock.btn_adsb.blockSignals(True)
            p.nav_dock.btn_adsb.setChecked(enabled)
            p.nav_dock.btn_adsb.blockSignals(False)

    def _on_bridge_adsb_color_mode_changed(self, mode: str):
        """Handle user changing ADS-B color mode from map panel."""
        self._reset_watchdog_activity()
        if self.config and hasattr(self.config, "app_colors"):
            self.config.app_colors.adsb_color_mode = mode
            try:
                self.config.save()
            except Exception as e:
                logger.error(f"Failed saving adsb_color_mode: {e}")

    def _on_bridge_adsb_filters_changed(self, filters_json: str):
        """Persist updated aircraft display filters to config."""
        self._reset_watchdog_activity()
        try:
            filters = json.loads(filters_json)
            active_cats = [cat for cat, active in filters.items() if active]
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.adsb_filter_categories = active_cats
                try:
                    self.config.save()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed saving adsb_filter_categories: {e}")

    def _on_bridge_adsb_alert_config_changed(self, config_json: str):
        """Persist proximity alert configuration to config."""
        self._reset_watchdog_activity()
        try:
            alert_cfg = json.loads(config_json)
            if self.config and hasattr(self.config, "meshcore"):
                self.config.meshcore.adsb_alert_enabled = bool(alert_cfg.get("enabled", True))
                cats_dict = alert_cfg.get("categories", {})
                self.config.meshcore.adsb_alert_categories = [c for c, act in cats_dict.items() if act]
                self.config.meshcore.adsb_alert_radius_mi = float(alert_cfg.get("radiusMi", 10.0))
                try:
                    self.config.save()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed saving adsb alert config: {e}")

    def _on_bridge_request_aircraft_photo(self, hex_code: str):
        """Asynchronously queries Planespotters photo for aircraft hex."""
        self._reset_watchdog_activity()
        self.adsb_service.request_aircraft_photo(hex_code)

    def _on_adsb_photo_received(self, hex_code: str, photo_info: dict):
        """Delivers photo metadata back to Leaflet map tooltip."""
        info_json = json.dumps(photo_info or {})
        self.run_js(f"if (window.onAircraftPhotoReady) window.onAircraftPhotoReady('{hex_code}', {info_json});")

    def _on_bridge_reset_adsb_target(self):
        """Reset ADS-B target back to local node."""
        local_lat = getattr(self.config.meshcore, "latitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        local_lon = getattr(self.config.meshcore, "longitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        local_alias = getattr(self.config.meshcore, "node_alias", "Local Node") if (self.config and hasattr(self.config, "meshcore")) else "Local Node"
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.adsb_target_node_id = ""
            self.config.meshcore.adsb_target_alias = local_alias
            try:
                self.config.save()
            except Exception:
                pass
        self.adsb_service.set_target_from_local_or_default(local_lat, local_lon, alias=local_alias)
        if self.show_adsb:
            self.adsb_service.refresh()

    def _on_adsb_flights_updated(self, payload: dict):
        self.run_js(f"onAdsbDataReady({json.dumps(payload)});")

    def center_map_at(self, lat: float, lon: float):
        """Smoothly pans the map to center at the specified coordinates."""
        self.run_js(f"map.panTo([{lat}, {lon}]);")

    def zoom_in_at(self, lat: float, lon: float):
        """Pans and zooms in one level at the specified coordinates."""
        self.run_js(f"map.setView([{lat}, {lon}], Math.min(map.getMaxZoom(), map.getZoom() + 1));")

    def zoom_out(self):
        """Zooms out one level."""
        self.run_js("map.setZoom(Math.max(map.getMinZoom(), map.getZoom() - 1));")

    def _context_monitor_adsb(self, lat: float, lon: float, qth: str):
        """Sets the ADS-B radar monitoring center to the clicked coordinate."""
        alias = f"Radar @ {qth}"
        radius = getattr(self.config.meshcore, "adsb_radius_nm", 50) if (self.config and hasattr(self.config, "meshcore")) else 50
        self.set_adsb_target("", alias, lat, lon, radius)
        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_adsb"):
            p.nav_dock.btn_adsb.blockSignals(True)
            p.nav_dock.btn_adsb.setChecked(True)
            p.nav_dock.btn_adsb.blockSignals(False)
        self._notify_user(f"✈️ Monitoring ADS-B air traffic around {alias} ({lat:.4f}, {lon:.4f})")

    def _context_set_station_location(self, lat: float, lon: float, qth: str):
        """Sets local station latitude and longitude in configuration."""
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.latitude = float(lat)
            self.config.meshcore.longitude = float(lon)
            try:
                self.config.save()
            except Exception as e:
                logger.error(f"Failed saving station location: {e}")
        self._notify_user(f"📡 Station Home Location set to {lat:.4f}, {lon:.4f} ({qth})")

    def _context_drop_temporary_pin(self, lat: float, lon: float, qth: str):
        """Drops a visual waypoint pin marker on the map."""
        label = f"Pin {qth}"
        self.run_js(f"if (window.dropTemporaryPin) window.dropTemporaryPin({lat}, {lon}, '{label}');")
        self._notify_user(f"📌 Dropped waypoint pin at {lat:.4f}, {lon:.4f} ({qth})")

    def _context_clear_traces_and_pins(self):
        """Clears active packet path lines, repeater neighbor routes, and waypoint pins."""
        self.clear_visualised_path()
        self.clear_repeater_neighbors()
        self.clear_preview_packet_path()
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setChecked(False)
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(False)
        self.run_js("if (window.clearTemporaryPin) window.clearTemporaryPin(); if (window.clearViewshedOverlay) window.clearViewshedOverlay(); if (window.clearP2PLine) window.clearP2PLine();")
        if hasattr(self, "elevation_profile_dock"):
            self.elevation_profile_dock.hide()
        self._notify_user("🧹 Cleared visualised routes, repeater neighbors, and waypoint pins")

    def _context_check_weather(self, lat: float, lon: float):
        """Centers map at coordinates and focuses weather radar."""
        self.center_map_at(lat, lon)
        self._notify_user(f"🌧️ Centered map on {lat:.4f}, {lon:.4f}")

    def _context_check_lightning(self, lat: float, lon: float):
        """Centers map at coordinates and enables lightning strikes layer."""
        self.center_map_at(lat, lon)
        if not getattr(self, "show_thunderstorm", False):
            self.set_thunderstorm(True)
            p = self.window()
            if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_thunderstorm"):
                p.nav_dock.btn_thunderstorm.blockSignals(True)
                p.nav_dock.btn_thunderstorm.setChecked(True)
                p.nav_dock.btn_thunderstorm.blockSignals(False)
        self._notify_user(f"⚡ Monitoring lightning strikes around {lat:.4f}, {lon:.4f}")

    def _copy_to_clipboard(self, text: str, label: str):
        """Copies text to system clipboard and notifies user."""
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            self._notify_user(f"📋 Copied {label} to clipboard: {text}")
        except Exception as e:
            logger.error(f"Failed copying to clipboard: {e}")

    def _notify_user(self, text: str):
        """Displays status bar notification in main window."""
        try:
            p = self.window()
            if p and hasattr(p, "statusBar") and p.statusBar():
                p.statusBar().showMessage(text, 4000)
        except Exception:
            pass

    def _get_active_observer_coords(self) -> tuple[float, float, str]:
        """Resolves observer coordinates for viewshed / path profiling."""
        if self._current_los_center:
            return self._current_los_center
        # Home station from config
        home_lat = getattr(self.config.meshcore, "latitude", None) if self.config else None
        home_lon = getattr(self.config.meshcore, "longitude", None) if self.config else None
        if home_lat is not None and home_lon is not None and is_valid_coordinate(home_lat, home_lon):
            return (home_lat, home_lon, "Home Station")
        # Try local or first node
        if self.storage:
            try:
                nodes = self.storage.get_nodes()
                for n in nodes:
                    if getattr(n, "is_local", False) and is_valid_coordinate(n.lat, n.lon):
                        return (n.lat, n.lon, n.alias or "Local Node")
                for n in nodes:
                    if is_valid_coordinate(n.lat, n.lon):
                        return (n.lat, n.lon, n.alias or n.node_id)
            except Exception:
                pass
        # Default fallback: map center or central UK
        return (51.5074, -0.1278, "Observer")

    def set_los_view_active(self, active: bool):
        """Toggles Line-of-Sight & Topo elevation toolbar and features from Nav Dock or action."""
        if self.config:
            self.config.map_show_rf_los = active
            try:
                self.config.save()
            except Exception:
                pass
        if hasattr(self, "los_controls"):
            self.los_controls.setVisible(active)
            self._reposition_floating_controls()
        if not active:
            if hasattr(self, "elevation_profile_dock"):
                self.elevation_profile_dock.hide()
            if hasattr(self, "btn_profile_path"):
                self.btn_profile_path.setChecked(False)
            if hasattr(self, "btn_toggle_los"):
                self.btn_toggle_los.setChecked(False)
            self.run_js("clearViewshedOverlay()")
            self.run_js("clearP2PLine()")
            self.run_js("setP2PMeasureMode(false)")
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("⚡ Line-of-Sight overlay & profile hidden")

    def _get_base_map_button_label(self, layer_type: str) -> str:
        layer_type = (layer_type or "canvas").lower()
        if layer_type in ("corescope", "carto"):
            return "🏔️ Topo / 🗺️ Canvas"
        elif layer_type == "topo":
            return "🗺️ Canvas / 🌌 CoreScope"
        else:
            return "🌌 CoreScope / 🏔️ Topo"

    def _on_toggle_base_map_clicked(self):
        curr = getattr(self, "_current_base_layer", "canvas").lower()
        if curr == "canvas":
            next_layer = "corescope"
        elif curr in ("corescope", "carto"):
            next_layer = "topo"
        else:
            next_layer = "canvas"
        self.set_base_map_layer(next_layer)

    def _show_base_map_menu(self, pos):
        if not hasattr(self, "btn_base_map"):
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E1F22;
                color: #F2F3F5;
                border: 1px solid #35373C;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 4px 18px 4px 12px;
            }
            QMenu::item:selected {
                background-color: #2B2D31;
                color: #60A5FA;
            }
        """)
        curr = getattr(self, "_current_base_layer", "canvas").lower()

        act_corescope = menu.addAction("🌌 CoreScope Dark (Black Land / Grey Sea)")
        act_corescope.setCheckable(True)
        act_corescope.setChecked(curr in ("corescope", "carto"))
        act_corescope.triggered.connect(lambda: self.set_base_map_layer("corescope"))

        act_canvas = menu.addAction("🗺️ Esri Dark Canvas")
        act_canvas.setCheckable(True)
        act_canvas.setChecked(curr == "canvas")
        act_canvas.triggered.connect(lambda: self.set_base_map_layer("canvas"))

        act_topo = menu.addAction("🏔️ OpenTopoMap Relief")
        act_topo.setCheckable(True)
        act_topo.setChecked(curr == "topo")
        act_topo.triggered.connect(lambda: self.set_base_map_layer("topo"))

        menu.addSeparator()
        act_carto_key = menu.addAction("🔑 Configure Carto API Key (Free)...")
        act_carto_key.triggered.connect(self.prompt_carto_api_key)

        menu.exec(self.btn_base_map.mapToGlobal(pos))

    def prompt_carto_api_key(self):
        """Displays modal dialog for entering or obtaining a free CARTO basemap API key."""
        dlg = QDialog(self)
        dlg.setWindowTitle("CARTO Basemaps API Key")
        dlg.setFixedWidth(470)
        dlg.setStyleSheet("""
            QDialog {
                background-color: #1E1F22;
                color: #F2F3F5;
            }
            QLabel {
                color: #DBDEE1;
                font-size: 12px;
            }
            QLineEdit {
                background-color: #2B2D31;
                color: #F2F3F5;
                border: 1px solid #383A40;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3B82F6;
            }
            QPushButton {
                background-color: #2B2D31;
                color: #F2F3F5;
                border: 1px solid #383A40;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #35373C;
            }
            QPushButton#btnSaveKey {
                background-color: #2563EB;
                color: #FFFFFF;
                border: 1px solid #1D4ED8;
                font-weight: bold;
            }
            QPushButton#btnSaveKey:hover {
                background-color: #1D4ED8;
            }
            QPushButton#btnGetKey {
                background-color: #1E3A8A;
                color: #93C5FD;
                border: 1px solid #3B82F6;
                font-weight: bold;
            }
            QPushButton#btnGetKey:hover {
                background-color: #2563EB;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        title_lbl = QLabel("<b>🔑 CARTO Dark Basemap API Key</b>")
        title_lbl.setStyleSheet("font-size: 14px; color: #60A5FA;")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            "CARTO requires an API key for its raster basemaps. "
            "Without a key, Dark tiles display an <i>'API KEY REQUIRED'</i> watermark.<br><br>"
            "Keys are <b>100% free</b> (up to 5 million requests/month) and available "
            "immediately with no credit card required."
        )
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        btn_get_key = QPushButton("🌐 Get Free Key at carto.com/basemaps/apikey")
        btn_get_key.setObjectName("btnGetKey")
        btn_get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://carto.com/basemaps/apikey")))
        layout.addWidget(btn_get_key)

        lbl_input = QLabel("Paste your CARTO API key:")
        layout.addWidget(lbl_input)

        cur_key = getattr(self.config, "carto_api_key", "") if self.config else ""
        txt_key = QLineEdit(cur_key)
        txt_key.setPlaceholderText("e.g. default_public... or your CARTO key")
        layout.addWidget(txt_key)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(lambda: txt_key.setText(""))
        btn_box.addWidget(btn_clear)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("Save & Apply")
        btn_save.setObjectName("btnSaveKey")
        btn_box.addWidget(btn_save)

        def _do_save():
            new_key = txt_key.text().strip()
            if self.config:
                self.config.carto_api_key = new_key
                try:
                    self.config.save()
                except Exception:
                    pass
            self.run_js(f"window.setCartoApiKey && window.setCartoApiKey('{new_key}');")
            if hasattr(self, "watcher_status"):
                if new_key:
                    self.watcher_status.setText("🔑 CARTO API key applied — map tiles refreshed")
                else:
                    self.watcher_status.setText("🔑 CARTO API key cleared")
            dlg.accept()

        btn_save.clicked.connect(_do_save)
        layout.addLayout(btn_box)

        dlg.exec()

    def set_base_map_layer(self, layer_type: str):
        """Switches base map layer between 'corescope' (Carto Dark), 'canvas' (Esri Dark Canvas), and 'topo' (OpenTopoMap Relief)."""
        layer_type = (layer_type or "canvas").lower()
        self._current_base_layer = layer_type
        if hasattr(self, "btn_base_map"):
            self.btn_base_map.setText(self._get_base_map_button_label(layer_type))
        if self.config:
            self.config.map_base_layer = layer_type
            try:
                self.config.save()
            except Exception:
                pass
        if hasattr(self, "watcher_status"):
            if layer_type in ("corescope", "carto"):
                has_key = bool(getattr(self.config, "carto_api_key", "").strip()) if self.config else False
                if not has_key:
                    layer_name = "CoreScope Dark (Right-click map button to set free Carto key & remove watermark)"
                else:
                    layer_name = "CoreScope Dark (Carto Black Land & Grey Water)"
            elif layer_type == "topo":
                layer_name = "Dark Topographic Relief (OpenTopoMap)"
            else:
                layer_name = "Dark Canvas (Esri)"
            self.watcher_status.setText(f"🗺️ Base map switched to: {layer_name}")
        if layer_type in ("corescope", "carto"):
            carto_key = getattr(self.config, "carto_api_key", "") if self.config else ""
            if carto_key:
                self.run_js(f"window.setCartoApiKey && window.setCartoApiKey('{carto_key}');")
        self.run_js(f"window.setBaseMapLayer && window.setBaseMapLayer('{layer_type}');")

    def _on_los_toggle_clicked(self):
        enabled = self.btn_toggle_los.isChecked()
        if enabled:
            self._trigger_viewshed_calc()
        else:
            self.run_js("clearViewshedOverlay()")
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("⚡ Watcher: Viewshed overlay cleared")

    def _on_los_height_button_clicked(self, height_m: int):
        self._current_tx_height = float(height_m)
        if hasattr(self, "btn_toggle_los") and self.btn_toggle_los.isChecked():
            self._trigger_viewshed_calc()

    def _on_los_radius_changed(self, text: str):
        try:
            self._current_los_radius = float(text.replace("km", "").strip())
        except Exception:
            self._current_los_radius = 25.0
        if hasattr(self, "btn_toggle_los") and self.btn_toggle_los.isChecked():
            self._trigger_viewshed_calc()

    def _trigger_viewshed_calc(self, lat: Optional[float] = None, lon: Optional[float] = None, alias: Optional[str] = None):
        if lat is None or lon is None:
            lat, lon, alias = self._get_active_observer_coords()
        self._current_los_center = (lat, lon, alias or "Observer")
        if hasattr(self, "los_controls") and not self.los_controls.isVisible():
            self.set_los_view_active(True)
            p = self.window()
            if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_rf_los"):
                p.nav_dock.btn_rf_los.blockSignals(True)
                p.nav_dock.btn_rf_los.setChecked(True)
                p.nav_dock.btn_rf_los.blockSignals(False)
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setChecked(True)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⏳ Viewshed: Calculating coverage ({self._current_los_radius:.0f} km @ {self._current_tx_height:.0f}m AGL from {alias})...")
        self.viewshed_service.calculate_viewshed(
            center_lat=lat,
            center_lon=lon,
            tx_height_m=self._current_tx_height,
            rx_height_m=self._current_rx_height,
            radius_km=self._current_los_radius,
            observer_alias=alias or "Observer"
        )

    def _on_viewshed_ready(self, payload: dict):
        self.run_js("if (window.hideLoadingHud) window.hideLoadingHud();")
        self.run_js(f"renderViewshedOverlay({json.dumps(payload)})")
        pct = payload.get("visible_pct", 0.0)
        sq_km = payload.get("coverage_sq_km", 0.0)
        r_km = payload.get("radius_km", 0.0)
        h_m = payload.get("tx_height_m", 8.0)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🟢 LOS Coverage: {pct}% visible within {r_km:.0f} km ({sq_km:.1f} km²) at {h_m:.0f}m AGL")

    def _on_viewshed_error(self, err: str):
        self.run_js("if (window.hideLoadingHud) window.hideLoadingHud();")
        logger.warning(f"Viewshed error: {err}")
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setChecked(False)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⚠️ Viewshed error: {err}")

    def _on_viewshed_loading(self, loading: bool):
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setText("⏳ Computing..." if loading else "🟢 Viewshed")
        if loading:
            self.run_js("if (window.showLoadingHud) window.showLoadingHud('Calculating Line-of-Sight Coverage (DEM Terrain & 4/3 Refraction)...');")
        else:
            self.run_js("if (window.hideLoadingHud) window.hideLoadingHud();")

    def _on_profile_path_toggle_clicked(self):
        active = self.btn_profile_path.isChecked()
        self.run_js(f"setP2PMeasureMode({json.dumps(active)})")
        if active:
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("🏔️ Point-to-Point Mode: Click Point A on map, then click Point B to calculate profile")
        else:
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("⚡ Point-to-Point measuring cancelled")

    def _start_p2p_measure_from_context(self):
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(True)
        self._on_profile_path_toggle_clicked()

    def _on_clear_los_clicked(self):
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setChecked(False)
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(False)
        self.run_js("clearViewshedOverlay()")
        self.run_js("clearP2PLine()")
        self.run_js("setP2PMeasureMode(false)")
        if hasattr(self, "elevation_profile_dock"):
            self.elevation_profile_dock.hide()
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("🧹 Overlays cleared")

    def _on_p2p_path_selected(self, lat1: float, lon1: float, lat2: float, lon2: float, alias1: str, alias2: str):
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(False)
        self.show_elevation_profile(lat1, lon1, lat2, lon2, alias1=alias1, alias2=alias2)

    def _on_profile_node_requested(self, node_id: str, alias: str, lat: float, lon: float):
        home_lat, home_lon, home_alias = self._get_active_observer_coords()
        if abs(home_lat - lat) < 0.0001 and abs(home_lon - lon) < 0.0001:
            self._notify_user("Cannot profile from node to itself. Select a different target or measure on map.")
            return
        self.show_elevation_profile(home_lat, home_lon, lat, lon, alias1=home_alias, alias2=alias or node_id)

    def _on_calc_node_viewshed_requested(self, node_id: str, alias: str, lat: float, lon: float):
        self._trigger_viewshed_calc(lat=lat, lon=lon, alias=alias or node_id)

    def show_elevation_profile(self, lat1: float, lon1: float, lat2: float, lon2: float, alias1: str = "Point A", alias2: str = "Point B"):
        """Initiates topographic RF elevation profile calculation and docks the panel."""
        self._last_p2p_coords = (lat1, lon1, lat2, lon2, alias1, alias2)
        if hasattr(self, "los_controls") and not self.los_controls.isVisible():
            self.set_los_view_active(True)
            p = self.window()
            if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_rf_los"):
                p.nav_dock.btn_rf_los.blockSignals(True)
                p.nav_dock.btn_rf_los.setChecked(True)
                p.nav_dock.btn_rf_los.blockSignals(False)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⏳ Profiling terrain & 868MHz Fresnel zone: {alias1} → {alias2}...")
        self.run_js("if (window.showLoadingHud) window.showLoadingHud('Calculating Topographic Terrain Elevation Profile...');")
        self.elevation_service.calculate_profile(
            lat1=lat1,
            lon1=lon1,
            lat2=lat2,
            lon2=lon2,
            tx_height_m=self._current_tx_height,
            rx_height_m=self._current_rx_height,
            alias1=alias1,
            alias2=alias2
        )

    def _on_elevation_profile_ready(self, payload: dict):
        self.run_js("if (window.hideLoadingHud) window.hideLoadingHud();")
        if hasattr(self, "elevation_profile_dock"):
            self.elevation_profile_dock.set_profile(payload)
            self.elevation_profile_dock.show()
            if hasattr(self, "map_splitter"):
                total_h = self.map_splitter.height()
                if total_h < 300:
                    total_h = 600
                map_h = int(total_h * 0.8)
                prof_h = max(140, total_h - map_h)
                self.map_splitter.setSizes([map_h, prof_h])

        lat1 = payload.get("lat1", 0.0)
        lon1 = payload.get("lon1", 0.0)
        lat2 = payload.get("lat2", 0.0)
        lon2 = payload.get("lon2", 0.0)
        status = payload.get("status", "CLEAR")
        a1 = payload.get("alias1", "Point A")
        a2 = payload.get("alias2", "Point B")
        self.run_js(f"renderP2PLine({lat1}, {lon1}, {lat2}, {lon2}, '{status}', '{a1}', '{a2}')")

        dist_km = payload.get("total_distance_km", 0.0)
        status_lbl = payload.get("status_label", status)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🏔️ Profile: {a1} → {a2} ({dist_km} km) | Status: {status_lbl}")

    def _on_elevation_profile_error(self, err: str):
        self.run_js("if (window.hideLoadingHud) window.hideLoadingHud();")
        logger.warning(f"Elevation profile error: {err}")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⚠️ Elevation profile error: {err}")

    def _on_dock_point_scrubbed(self, lat: float, lon: float):
        self.run_js(f"updateP2PScrubMarker({lat}, {lon})")

    def _on_dock_scrub_cleared(self):
        self.run_js("clearP2PScrubMarker()")

    def _on_dock_heights_changed(self, tx_height: float, rx_height: float):
        self._current_tx_height = tx_height
        self._current_rx_height = rx_height
        if self._last_p2p_coords:
            lat1, lon1, lat2, lon2, a1, a2 = self._last_p2p_coords
            self.elevation_service.calculate_profile(
                lat1=lat1,
                lon1=lon1,
                lat2=lat2,
                lon2=lon2,
                tx_height_m=tx_height,
                rx_height_m=rx_height,
                alias1=a1,
                alias2=a2
            )

    def _on_close_elevation_profile(self):
        if hasattr(self, "elevation_profile_dock"):
            self.elevation_profile_dock.hide()
        self.run_js("clearP2PLine()")
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(False)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ Watcher: Ready")

    def _show_node_context_menu(self, node_id: str, alias: str, is_repeater: bool, is_phantom: bool, lat: float, lon: float, x: int, y: int):
        """Displays rich context menu when user right-clicks directly on a node/repeater marker."""
        now = time.time()
        if hasattr(self, "_last_context_menu_time") and (now - self._last_context_menu_time) < 0.25:
            return
        self._last_context_menu_time = now

        if hasattr(self, "_active_context_menu") and self._active_context_menu:
            try:
                self._active_context_menu.close()
            except Exception:
                pass
            self._active_context_menu = None

        menu = QMenu(self)
        self._active_context_menu = menu
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E2024;
                color: #F3F4F6;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 18px;
                border-radius: 5px;
                font-size: 12px;
                font-weight: 600;
            }
            QMenu::item:selected {
                background-color: #2D3748;
                color: #38BDF8;
            }
            QMenu::item:disabled {
                color: #38BDF8;
                font-size: 12px;
                font-weight: 700;
                padding: 6px 14px;
            }
            QMenu::separator {
                height: 1px;
                background-color: #374151;
                margin: 4px 6px;
            }
        """)

        clean_alias = alias or node_id
        is_fav = False
        contact = None
        if self.storage:
            contact = self.storage.get_contact(node_id)
            if not contact and alias:
                contact = self.storage.get_contact(alias)
        if contact:
            is_fav = bool(contact.is_favorite or (self.config and self.config.is_user_favorite(contact.node_id, contact.alias)))
        elif self.config:
            is_fav = bool(self.config.is_user_favorite(node_id, clean_alias))

        is_room = (getattr(contact, "is_room_server", False) or is_room_server_contact(contact)) if contact else is_room_server_contact({"alias": clean_alias, "node_id": node_id})
        is_actual_phantom = self.storage.is_phantom_node(node_id, clean_alias) if self.storage else is_phantom

        # 1. Header
        type_str = "Phantom Node" if is_actual_phantom else ("Room Server" if is_room else ("Repeater" if is_repeater else "Companion"))
        icon_str = "👻" if is_actual_phantom else ("🏢" if is_room else ("📡" if is_repeater else "👤"))
        header_text = f"{icon_str} {clean_alias} • {type_str}"
        act_header = menu.addAction(header_text)
        act_header.setEnabled(False)

        menu.addSeparator()

        # 2. Phantom Node Action (for repeaters and phantom nodes)
        if is_repeater or is_actual_phantom:
            if is_actual_phantom:
                act_phantom = menu.addAction("👻 Unmark Phantom Node (Restore Normal)")
                act_phantom.triggered.connect(lambda: self._on_phantom_node_toggled(node_id, clean_alias, False))
            else:
                act_phantom = menu.addAction("👻 Mark as Phantom Node (Style Black)")
                act_phantom.triggered.connect(lambda: self._on_phantom_node_toggled(node_id, clean_alias, True))
            menu.addSeparator()

        # 3. Favorite Action
        fav_label = "⭐ Remove from Favorites" if is_fav else "⭐ Add to Favorites"
        act_fav = menu.addAction(fav_label)
        def toggle_node_fav():
            new_f = not is_fav
            if self.storage:
                self.storage.set_contact_favorite(node_id, new_f)
            if self.config:
                if new_f:
                    if node_id not in self.config.favorite_users:
                        self.config.favorite_users.append(node_id)
                else:
                    self.config.favorite_users = [u for u in self.config.favorite_users if u != node_id and u != clean_alias]
                self.config.save()
            from meshcore_tray.core.event_bus import bus, EventType
            bus.emit(EventType.FAVORITES_UPDATED, node_id)
            self.refresh_map_data()
        act_fav.triggered.connect(toggle_node_fav)

        # 4. Open Console / DM
        if is_room:
            act_open = menu.addAction("🏢 Open Room Server Console")
        elif is_repeater:
            act_open = menu.addAction("📻 Open Repeater Console")
        else:
            act_open = menu.addAction("💬 Send Direct Message")
        act_open.triggered.connect(lambda: self._on_bridge_node_clicked(node_id))

        menu.addSeparator()

        # 5. RF & Profile Operations
        home_lat = getattr(self.config.meshcore, "latitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        home_lon = getattr(self.config.meshcore, "longitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        if home_lat is not None and home_lon is not None and lat is not None and lon is not None:
            act_profile = menu.addAction(f"🏔️ Profile Path to Home ({clean_alias})")
            act_profile.triggered.connect(lambda: self.show_elevation_profile(home_lat, home_lon, lat, lon, alias1="Home Station", alias2=clean_alias))

        if lat is not None and lon is not None:
            act_viewshed = menu.addAction(f"🟢 Calculate LOS Viewshed for {clean_alias}")
            act_viewshed.triggered.connect(lambda: self._trigger_viewshed_calc(lat=lat, lon=lon, alias=clean_alias))

            act_adsb = menu.addAction(f"✈️ Track ADS-B Air Traffic Around {clean_alias}")
            act_adsb.triggered.connect(lambda: self._on_bridge_set_adsb_target(node_id, clean_alias, lat, lon))

            act_vis = menu.addAction(f"🛣️ Visualise Route / Path ({clean_alias})")
            act_vis.triggered.connect(lambda: self.visualise_node_path(node_id, clean_alias, lat, lon))

        menu.addSeparator()

        # 6. Copy details
        act_copy_id = menu.addAction(f"📋 Copy Node ID ({node_id})")
        act_copy_id.triggered.connect(lambda: self._copy_to_clipboard(node_id, "Node ID"))

        if lat is not None and lon is not None:
            act_copy_coords = menu.addAction(f"📋 Copy Coordinates ({lat:.5f}, {lon:.5f})")
            act_copy_coords.triggered.connect(lambda: self._copy_to_clipboard(f"{lat:.5f}, {lon:.5f}", "Coordinates"))

        if hasattr(self, "web_view"):
            safe_x = max(0, min(self.web_view.width(), int(x)))
            safe_y = max(0, min(self.web_view.height(), int(y)))
            global_pos = self.web_view.mapToGlobal(QPoint(safe_x, safe_y))
        else:
            global_pos = self.mapToGlobal(QPoint(int(x), int(y)))
        menu.popup(global_pos)

    def _show_map_context_menu(self, lat: float, lon: float, x: int, y: int):
        """Displays custom dark-themed context menu for map operations."""
        now = time.time()
        if hasattr(self, "_last_context_menu_time") and (now - self._last_context_menu_time) < 0.25:
            return
        self._last_context_menu_time = now

        if hasattr(self, "_active_context_menu") and self._active_context_menu:
            try:
                self._active_context_menu.close()
            except Exception:
                pass
            self._active_context_menu = None

        menu = QMenu(self)
        self._active_context_menu = menu
        menu.setStyleSheet("""
            QMenu {
                background-color: #1E2024;
                color: #F3F4F6;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 18px;
                border-radius: 5px;
                font-size: 12px;
                font-weight: 600;
            }
            QMenu::item:selected {
                background-color: #2D3748;
                color: #38BDF8;
            }
            QMenu::item:disabled {
                color: #38BDF8;
                font-size: 12px;
                font-weight: 700;
                padding: 6px 14px;
            }
            QMenu::separator {
                height: 1px;
                background-color: #374151;
                margin: 4px 6px;
            }
        """)

        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        qth = latlon_to_maidenhead(lat, lon)
        header_text = f"📍 {abs(lat):.4f}° {lat_dir}, {abs(lon):.4f}° {lon_dir}  •  {qth}"

        # 1. Location Header
        act_header = menu.addAction(header_text)
        act_header.setEnabled(False)

        menu.addSeparator()

        # Check if right-clicking near any known repeater or phantom node (within 1km)
        nearby_rep = None
        if self.storage:
            try:
                coords_nodes = self.storage.get_nodes_with_coordinates()
                for c in coords_nodes:
                    if (c.is_repeater or self.storage.is_phantom_node(c.node_id, c.alias)) and c.latitude is not None and c.longitude is not None:
                        d_km, _, _ = calculate_distance_and_bearing(lat, lon, c.latitude, c.longitude)
                        if d_km < 1.0:
                            nearby_rep = c
                            break
            except Exception:
                pass

        if nearby_rep:
            is_p = self.storage.is_phantom_node(nearby_rep.node_id, nearby_rep.alias)
            p_label = f"👻 Unmark '{nearby_rep.alias}' as Phantom Node" if is_p else f"👻 Mark '{nearby_rep.alias}' as Phantom Node (Style Black)"
            act_rep_phantom = menu.addAction(p_label)
            act_rep_phantom.triggered.connect(lambda: self._on_phantom_node_toggled(nearby_rep.node_id, nearby_rep.alias, not is_p))
            menu.addSeparator()

        # 2. Navigation
        act_center = menu.addAction("🎯 Center Map Here")
        act_center.triggered.connect(lambda: self.center_map_at(lat, lon))

        act_zoom_in = menu.addAction("🔍 Zoom In Here")
        act_zoom_in.triggered.connect(lambda: self.zoom_in_at(lat, lon))

        act_zoom_out = menu.addAction("🔎 Zoom Out")
        act_zoom_out.triggered.connect(lambda: self.zoom_out())

        menu.addSeparator()

        # 3. ADS-B Monitoring
        act_adsb = menu.addAction("✈️ Monitor ADS-B Air Traffic Here")
        act_adsb.triggered.connect(lambda: self._context_monitor_adsb(lat, lon, qth))

        target_alias = getattr(self.config.meshcore, "adsb_target_alias", "") if (self.config and hasattr(self.config, "meshcore")) else ""
        if target_alias and "Local Node" not in target_alias and "Station" not in target_alias:
            act_reset_adsb = menu.addAction("↺ Reset ADS-B Center to Home Station")
            act_reset_adsb.triggered.connect(self._on_bridge_reset_adsb_target)

        menu.addSeparator()

        # 4. Station & Distance
        act_set_station = menu.addAction("📡 Set Station Home Location Here")
        act_set_station.triggered.connect(lambda: self._context_set_station_location(lat, lon, qth))

        home_lat = getattr(self.config.meshcore, "latitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        home_lon = getattr(self.config.meshcore, "longitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        if home_lat is not None and home_lon is not None:
            d_km, d_mi, bearing = calculate_distance_and_bearing(home_lat, home_lon, lat, lon)
            act_dist = menu.addAction(f"📏 Distance from Home: {d_mi:.1f} mi ({d_km:.1f} km) @ {bearing:.0f}°")
            act_dist.triggered.connect(lambda: self._notify_user(f"📍 Point is {d_mi:.1f} miles ({d_km:.1f} km) bearing {bearing:.0f}° from station ({qth})"))

        menu.addSeparator()

        # 5. RF Line of Sight & Topographic Path Profile
        act_los_calc = menu.addAction("🟢 Calculate LOS Viewshed Here")
        act_los_calc.triggered.connect(lambda: self._trigger_viewshed_calc(lat=lat, lon=lon, alias=f"Point ({lat:.3f}, {lon:.3f})"))

        if home_lat is not None and home_lon is not None:
            act_profile_here = menu.addAction("🏔️ Profile RF Path From Home Here")
            act_profile_here.triggered.connect(lambda: self.show_elevation_profile(home_lat, home_lon, lat, lon, alias1="Home Station", alias2=f"Point ({lat:.3f}, {lon:.3f})"))

        act_measure_p2p = menu.addAction("📏 Measure Point-to-Point Profile...")
        act_measure_p2p.triggered.connect(self._start_p2p_measure_from_context)

        menu.addSeparator()

        # 6. Waypoint Pins & Path Traces
        act_drop_pin = menu.addAction("📌 Drop Waypoint Pin Here")
        act_drop_pin.triggered.connect(lambda: self._context_drop_temporary_pin(lat, lon, qth))

        act_clear = menu.addAction("🧹 Clear Traces && Waypoints")
        act_clear.triggered.connect(self._context_clear_traces_and_pins)

        menu.addSeparator()

        # 6. Weather & Atmosphere
        act_weather = menu.addAction("🌧️ Check Rain Radar Here")
        act_weather.triggered.connect(lambda: self._context_check_weather(lat, lon))

        act_lightning = menu.addAction("⚡ Monitor Lightning Strikes Here")
        act_lightning.triggered.connect(lambda: self._context_check_lightning(lat, lon))

        menu.addSeparator()

        # 7. Clipboard Tools
        act_copy_coords = menu.addAction(f"📋 Copy Coordinates ({lat:.5f}, {lon:.5f})")
        act_copy_coords.triggered.connect(lambda: self._copy_to_clipboard(f"{lat:.5f}, {lon:.5f}", "Coordinates"))

        act_copy_qth = menu.addAction(f"📻 Copy Maidenhead QTH Locator ({qth})")
        act_copy_qth.triggered.connect(lambda: self._copy_to_clipboard(qth, "Maidenhead Locator"))

        # Popup asynchronously without blocking event loop or WebEngine IPC
        if hasattr(self, "web_view"):
            safe_x = max(0, min(self.web_view.width(), int(x)))
            safe_y = max(0, min(self.web_view.height(), int(y)))
            global_pos = self.web_view.mapToGlobal(QPoint(safe_x, safe_y))
        else:
            global_pos = self.mapToGlobal(QPoint(int(x), int(y)))
        menu.popup(global_pos)

    def set_activity_heatmap(self, enabled: bool, timeframe_hours: Optional[int] = None):
        """Toggles the node activity heatmap and updates repeater colors based on message traffic."""
        self.activity_heatmap_active = bool(enabled)
        if timeframe_hours is not None:
            self.activity_timeframe_hours = int(timeframe_hours)

        data = {}
        if self.activity_heatmap_active and self.storage:
            data = self.storage.get_node_activity_counts(self.activity_timeframe_hours)

        if hasattr(self, "btn_heatmap"):
            self.btn_heatmap.blockSignals(True)
            self.btn_heatmap.setChecked(self.activity_heatmap_active)
            self.btn_heatmap.blockSignals(False)

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_heatmap"):
            p.nav_dock.btn_heatmap.blockSignals(True)
            p.nav_dock.btn_heatmap.setChecked(self.activity_heatmap_active)
            p.nav_dock.btn_heatmap.blockSignals(False)

        if hasattr(self, "watcher_status"):
            if self.activity_heatmap_active:
                self.watcher_status.setText(f"🔥 <b>Activity Heatmap:</b> Showing node message volume for past {self.activity_timeframe_hours}h")
            else:
                self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

        act_str = "true" if self.activity_heatmap_active else "false"
        self.run_js(f"setActivityHeatmap({act_str}, {self.activity_timeframe_hours}, {json.dumps(data)});")

    def _on_bridge_activity_timeframe_changed(self, hours: int):
        self.set_activity_heatmap(True, timeframe_hours=hours)

    def _on_bridge_activity_heatmap_toggled(self, enabled: bool):
        self.set_activity_heatmap(enabled)

    def set_new_nodes(self, enabled: bool, timeframe_hours: Optional[int] = None):
        """Toggles the 'New Nodes' discovery view (highlights new nodes in gold, greys out older nodes)."""
        self.new_nodes_active = bool(enabled)
        if timeframe_hours is not None:
            self.new_nodes_timeframe_hours = int(timeframe_hours)

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "map_layers") and hasattr(p.nav_dock.map_layers, "btn_new_nodes"):
            p.nav_dock.map_layers.btn_new_nodes.blockSignals(True)
            p.nav_dock.map_layers.btn_new_nodes.setChecked(self.new_nodes_active)
            p.nav_dock.map_layers.btn_new_nodes.blockSignals(False)

        if hasattr(self, "watcher_status"):
            if self.new_nodes_active:
                self.watcher_status.setText(f"👋 <b>New Nodes:</b> Highlighting nodes discovered in past {self.new_nodes_timeframe_hours}h in gold")
            else:
                self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

        nn_str = "true" if self.new_nodes_active else "false"
        self.run_js(f"setNewNodes({nn_str}, {self.new_nodes_timeframe_hours});")

    def _on_bridge_new_nodes_timeframe_changed(self, hours: int):
        self.set_new_nodes(True, timeframe_hours=hours)

    def _on_bridge_new_nodes_toggled(self, enabled: bool):
        self.set_new_nodes(enabled)

    def _on_bridge_mark_all_nodes_known(self):
        """Marks all current contacts as known in storage and reloads map nodes."""
        if self.storage and hasattr(self.storage, "mark_all_contacts_as_known"):
            self.storage.mark_all_contacts_as_known()
            self.load_contacts(self.storage.get_contacts())
            self.run_js("if (window.updateNewNodesStats) window.updateNewNodesStats();")
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("👋 <b>New Nodes:</b> All current nodes marked as known. Starting discovery baseline from now.")

    def set_mqtt_nodes(self, enabled: bool):
        """Toggles the 'MQTT Ingest' nodes view mode (highlights nodes discovered via MQTT in orange)."""
        self.mqtt_nodes_active = bool(enabled)

        p = self.window()
        if p and hasattr(p, "nav_dock"):
            if hasattr(p.nav_dock, "set_layer_active"):
                p.nav_dock.set_layer_active("mqtt_nodes", self.mqtt_nodes_active)
            elif hasattr(p.nav_dock, "map_layers"):
                p.nav_dock.map_layers.set_layer_active("mqtt_nodes", self.mqtt_nodes_active)

        if hasattr(self, "watcher_status"):
            if self.mqtt_nodes_active:
                self.watcher_status.setText("🌐 <b>MQTT Ingest View:</b> Highlighting nodes discovered via MQTT broker feeds in orange")
            else:
                self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

        mn_str = "true" if self.mqtt_nodes_active else "false"
        self.run_js(f"setMqttNodes({mn_str});")

    def _on_bridge_mqtt_nodes_toggled(self, enabled: bool):
        self.set_mqtt_nodes(enabled)

    def set_thunderstorm(self, enabled: bool):
        """Toggles real-time thunderstorm radar and lightning strike tracking."""
        self.show_thunderstorm = bool(enabled)
        self.thunderstorm_service.set_enabled(self.show_thunderstorm)

        if hasattr(self, "btn_thunderstorm"):
            self.btn_thunderstorm.blockSignals(True)
            self.btn_thunderstorm.setChecked(self.show_thunderstorm)
            self.btn_thunderstorm.blockSignals(False)

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_thunderstorm"):
            p.nav_dock.btn_thunderstorm.blockSignals(True)
            p.nav_dock.btn_thunderstorm.setChecked(self.show_thunderstorm)
            p.nav_dock.btn_thunderstorm.blockSignals(False)

        if hasattr(self, "watcher_status") and self.show_thunderstorm:
            self.watcher_status.setText("🌩️ <b>Thunderstorms:</b> Live RainViewer radar & Blitzortung lightning active")

        vis_str = "true" if self.show_thunderstorm else "false"
        meta_json = json.dumps(self.thunderstorm_service.latest_radar or {})
        self.run_js(f"setThunderstormVisible({vis_str}, {meta_json});")

    def _on_thunderstorm_radar_updated(self, radar_meta: dict):
        if self.show_thunderstorm:
            self.run_js(f"updateThunderstormRadar({json.dumps(radar_meta)});")

    def _on_bridge_thunderstorm_toggled(self, enabled: bool):
        self.set_thunderstorm(enabled)

    def _on_bridge_lightning_proximity_alert(self, distance_mi: float, bearing_deg: int):
        """Dispatches 25-mile lightning strike proximity warning to listeners / system tray."""
        self.lightning_proximity_alert.emit(float(distance_mi), int(bearing_deg))
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(
                f"⚡ <b>LIGHTNING ALERT:</b> Strike detected {distance_mi:.1f} mi away (bearing {bearing_deg}°)"
            )

    def set_search_node_id(self, enabled: bool):
        """Toggles the Search Node IDs interactive overlay and marker illumination."""
        self.show_search_node_id = bool(enabled)

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_search_node_id"):
            p.nav_dock.btn_search_node_id.blockSignals(True)
            p.nav_dock.btn_search_node_id.setChecked(self.show_search_node_id)
            p.nav_dock.btn_search_node_id.blockSignals(False)

        if hasattr(self, "watcher_status") and self.show_search_node_id:
            self.watcher_status.setText("🔍 <b>Search Node IDs:</b> Matching byte prefixes illuminated in neon green")

        vis_str = "true" if self.show_search_node_id else "false"
        self.run_js(f"setSearchNodeIdVisible({vis_str});")
        self.search_node_id_toggled.emit(self.show_search_node_id)

    def _on_bridge_search_node_id_toggled(self, enabled: bool):
        self.set_search_node_id(enabled)

    def set_3d_mode(self, enabled: bool):
        """Toggles between 2D Leaflet map and UKMesh-style 3D MapLibre terrain view."""
        self.is_3d_mode = bool(enabled)
        if hasattr(self, "btn_map_3d"):
            self.btn_map_3d.blockSignals(True)
            self.btn_map_3d.setChecked(self.is_3d_mode)
            self.btn_map_3d.blockSignals(False)

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "set_layer_active"):
            p.nav_dock.set_layer_active("map_3d", self.is_3d_mode)

        if hasattr(self, "watcher_status") and self.is_3d_mode:
            self.watcher_status.setText("🏔️ <b>3D View:</b> MapLibre GL 3D Terrain & Globe active (UKMesh style)")

        m_str = "true" if self.is_3d_mode else "false"
        if self.is_3d_mode and not getattr(self, "_maplibre_injected", False):
            js_code = _load_vendor_asset("maplibre-gl.js")
            if js_code:
                self.run_js(js_code)
                self._maplibre_injected = True
        self.run_js(f"if (window.set3DMode) set3DMode({m_str});")

    def _on_bridge_map_3d_toggled(self, enabled: bool):
        self.set_3d_mode(enabled)

    def set_space_weather(self, enabled: bool):
        """Toggles real-time NOAA space weather telemetry and aurora forecast overlay."""
        self.show_space_weather = bool(enabled)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_space_weather = self.show_space_weather
            try:
                self.config.save()
            except Exception:
                pass

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_space_weather"):
            p.nav_dock.btn_space_weather.blockSignals(True)
            p.nav_dock.btn_space_weather.setChecked(self.show_space_weather)
            p.nav_dock.btn_space_weather.blockSignals(False)

        if self.show_space_weather:
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText("🌌 <b>Space Weather:</b> Connecting to NOAA SWPC...")
            poll_int = getattr(self.config.meshcore, "space_weather_poll_interval_min", 15) if self.config else 15
            self.space_weather_service.start_polling(interval_min=poll_int)
        else:
            self.space_weather_service.stop_polling()
            self.run_js("clearSpaceWeatherLayer();")

    def _on_space_weather_updated(self, payload: dict):
        if not getattr(self, "show_space_weather", False):
            return
        kp = payload.get("kp", 0.0)
        kp_status = payload.get("kp_status", "")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🌌 <b>Space Weather:</b> Kp {kp:.1f} ({kp_status})")
        self.run_js(f"window.onSpaceWeatherReady({json.dumps(payload)});")

    def _on_space_weather_loading(self, msg: str):
        if getattr(self, "show_space_weather", False) and hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🌌 <b>Space Weather:</b> {msg}")

    def _on_space_weather_error(self, err: str):
        if getattr(self, "show_space_weather", False) and hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⚠️ <b>Space Weather Error:</b> {err}")

    def _on_space_weather_opacity_changed(self, opacity: float):
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.space_weather_opacity = opacity
            try:
                self.config.save()
            except Exception:
                pass

    def set_satellites(self, enabled: bool):
        """Toggles real-time satellite tracking layer and syncs orbital elements to Leaflet."""
        self.show_satellites = bool(enabled)
        if self.config and hasattr(self.config, "satellites"):
            self.config.satellites.enabled = self.show_satellites
            try:
                self.config.save()
            except Exception:
                pass

        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "btn_satellites"):
            p.nav_dock.btn_satellites.blockSignals(True)
            p.nav_dock.btn_satellites.setChecked(self.show_satellites)
            p.nav_dock.btn_satellites.blockSignals(False)

        if hasattr(self, "btn_satellites"):
            self.btn_satellites.blockSignals(True)
            self.btn_satellites.setChecked(self.show_satellites)
            self.btn_satellites.blockSignals(False)

        vis_str = "true" if self.show_satellites else "false"
        self.run_js(f"setSatellitesVisible({vis_str});")

        if self.show_satellites:
            self._push_satellites_to_map()
            self.satellite_service.start()
        else:
            self.satellite_service.stop()

    def _push_satellites_to_map(self):
        """Pushes active satellite orbital TLEs and observer coords to Leaflet."""
        sats = self.satellite_service.get_satellites_for_map(active_only=False)
        local_lat = getattr(self.config.meshcore, "latitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        local_lon = getattr(self.config.meshcore, "longitude", None) if (self.config and hasattr(self.config, "meshcore")) else None
        payload = {
            "satellites": sats,
            "observer": {
                "lat": local_lat,
                "lon": local_lon,
                "alt_m": 0.0,
            } if (local_lat is not None and local_lon is not None) else None
        }
        self.run_js(f"if (window.onSatellitesDataReady) window.onSatellitesDataReady({json.dumps(payload)});")

    def set_packet_hud_visible(self, visible: bool):
        """Toggles on-map CoreScope Live Packet Feed HUD ticker overlay."""
        self.show_packet_hud = bool(visible)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_packet_hud = self.show_packet_hud
            try:
                self.config.save()
            except Exception:
                pass
        vis_str = "true" if self.show_packet_hud else "false"
        self.run_js(f"if (window.setPacketHudVisible) {{ window.setPacketHudVisible({vis_str}); }}")
        self.packet_hud_toggled.emit(self.show_packet_hud)

    def _on_bridge_packet_hud_toggled(self, visible: bool):
        self.show_packet_hud = bool(visible)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_packet_hud = self.show_packet_hud
            try:
                self.config.save()
            except Exception:
                pass
        self.packet_hud_toggled.emit(self.show_packet_hud)

    def set_activity_timeline_visible(self, visible: bool):
        """Toggles bottom docked CoreScope Network Activity Timeline split view."""
        self.show_activity_timeline = bool(visible)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_activity_timeline = self.show_activity_timeline
            try:
                self.config.save()
            except Exception:
                pass
        if hasattr(self, "activity_timeline_dock"):
            if self.show_activity_timeline:
                self.activity_timeline_dock.refresh_data()
                self.activity_timeline_dock.show()
                # Split so timeline receives ~46px
                total_h = self.height()
                self.map_splitter.setSizes([max(150, total_h - 46), 46])
            else:
                self.activity_timeline_dock.hide()
        self.activity_timeline_toggled.emit(self.show_activity_timeline)

    def _on_bridge_activity_timeline_toggled(self, visible: bool):
        self.set_activity_timeline_visible(visible)

    def set_map_legend_visible(self, visible: bool):
        """Toggles on-map CoreScope Map Legend overlay."""
        self.show_map_legend = bool(visible)
        vis_str = "true" if self.show_map_legend else "false"
        self.run_js(f"if (window.toggleMapLegend) window.toggleMapLegend({vis_str});")
        self.map_legend_toggled.emit(self.show_map_legend)

    def _on_bridge_map_legend_toggled(self, visible: bool):
        self.show_map_legend = bool(visible)
        self.map_legend_toggled.emit(self.show_map_legend)

    def _on_satellites_updated(self, tles: list):
        """Triggered when SatelliteService finishes refreshing or seeding TLEs."""
        if getattr(self, "show_satellites", False):
            self._push_satellites_to_map()

    def select_satellite(self, norad_id: str):
        """Focuses and selects a satellite on the map, opening its tracker panel."""
        if not self.show_satellites:
            self.set_satellites(True)
        clean_id = str(norad_id).strip()
        self.run_js(
            f"if (window.selectSatellite) {{ "
            f"window.selectSatellite('{clean_id}'); "
            f"setTimeout(function() {{ if (window.centerOnSelectedSatellite) window.centerOnSelectedSatellite(); }}, 200); "
            f"}}"
        )

    def preview_packet_path(self, path: PacketPathInfo):
        """Temporarily highlights a multi-hop flood trajectory on hover from the floods view."""
        if not path or not path.coordinates or len(path.coordinates) < 2:
            return
        route_coords = [list(pt) for pt in path.coordinates]
        local_coord = self._get_local_coordinates()
        if local_coord and (not route_coords or route_coords[-1] != local_coord):
            route_coords.append(local_coord)

        hops_str = " ➔ ".join(path.hop_nodes) if path.hop_nodes else f"{len(path.coordinates)} hops"
        t_str = datetime.now().strftime("%H:%M:%S")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"[{t_str}] 🔍 <b>Previewing:</b> {path.sender_name or path.sender_id} ➔ {hops_str}")

        js_coords = json.dumps(route_coords)
        meta = {
            "sender": path.sender_name or path.sender_id,
            "hops": path.hop_nodes or [],
            "route_type": path.route_type or "FLOOD"
        }
        self.run_js(f"previewPacketPath({js_coords}, {json.dumps(meta)});")

    def clear_preview_packet_path(self):
        """Clears the temporary on-hover path preview."""
        self.run_js("clearPreviewPacketPath();")
        if hasattr(self, "watcher_status") and not self.activity_heatmap_active and not self.show_thunderstorm:
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def set_freshness_fading(self, enabled: bool):
        self.freshness_fading = enabled
        if hasattr(self, "btn_age_fade"):
            self.btn_age_fade.blockSignals(True)
            self.btn_age_fade.setChecked(bool(enabled))
            self.btn_age_fade.blockSignals(False)
        fading_str = "true" if enabled else "false"
        self.run_js(f"setFreshnessFading({fading_str});")

    def _on_floating_age_fade_clicked(self):
        enabled = self.btn_age_fade.isChecked()
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.node_freshness_fading = enabled
            try:
                self.config.save()
            except Exception:
                pass
        self.set_freshness_fading(enabled)
        bus.emit(EventType.SETTINGS_UPDATED, self.config)

    def _open_add_contact_dialog(self, initial_name: str = "", lat: Optional[float] = None, lon: Optional[float] = None):
        """Launches the Contact Discovery & Manual Contact Management dialog."""
        from meshcore_tray.ui.contact_dialog import ContactDiscoveryDialog
        dlg = ContactDiscoveryDialog(
            storage=self.storage,
            driver=getattr(self, "driver", None),
            parent=self.window(),
            initial_name=initial_name,
            initial_lat=lat,
            initial_lon=lon
        )
        dlg.exec()

    def _on_bridge_map_moved(self, lat: float, lon: float, zoom: int):
        """Persists the user's chosen map view so it remains locked and preserved."""
        self._reset_watchdog_activity()
        if self.config and hasattr(self.config, "meshcore"):
            wrapped_lon = ((float(lon) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
            clamped_lat = max(-85.0, min(85.0, float(lat)))
            self.config.meshcore.map_center_lat = round(clamped_lat, 5)
            self.config.meshcore.map_center_lon = round(wrapped_lon, 5)
            self.config.meshcore.map_zoom = int(zoom)

    def _on_visualised_path_closed(self):
        """Called when the user closes the visualised path popup on the map."""
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def _on_hop_candidate_selected(self, hop_prefix: str, target_node_id: str, msg_id: str = ""):
        """Called when user manually selects an alternate candidate repeater in the route popup."""
        if not hop_prefix or not target_node_id or not self.storage:
            return
        contact = self.storage.get_contact(target_node_id)
        alias = contact.alias if contact else target_node_id

        # 1. Save preference for all future routing requests
        self.storage.save_hop_preference(hop_prefix, target_node_id, alias)
        logger.info(f"Saved persistent hop route preference: prefix '{hop_prefix}' -> {alias} ({target_node_id})")

        # 2. Update current message metadata if msg_id provided
        if msg_id:
            try:
                with self.storage._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT metadata_json FROM messages WHERE id = ?", (msg_id,))
                    row = cur.fetchone()
                    if row:
                        meta = json.loads(row["metadata_json"] or "{}")
                        pref_map = meta.get("hop_preferences", {})
                        pref_map[hop_prefix] = target_node_id
                        meta["hop_preferences"] = pref_map
                        cur.execute("UPDATE messages SET metadata_json = ? WHERE id = ?", (json.dumps(meta), msg_id))
            except Exception as e:
                logger.debug(f"Error updating message metadata with hop preference: {e}")

        # 3. Update status text with confirmation
        if hasattr(self, "watcher_status"):
            cur_txt = self.watcher_status.text()
            if "(💾 Saved" not in cur_txt:
                self.watcher_status.setText(f"{cur_txt} (💾 Saved: {alias})")

    def _on_phantom_node_toggled(self, node_id: str, alias: str, is_phantom: bool):
        """Called when user toggles phantom node status from the route popup."""
        if not node_id and not alias:
            return
        clean_alias = alias.replace("?? Unknown", "").strip()
        if is_phantom:
            if self.storage:
                self.storage.mark_phantom_node(node_id, clean_alias)
            if self.config:
                self.config.mark_phantom_node(node_id)
                if clean_alias and clean_alias != node_id:
                    self.config.mark_phantom_node(clean_alias)
            logger.info(f"Marked phantom node: {alias} ({node_id})")
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText(f"⚡ <b>Phantom Node:</b> Marked '{clean_alias or node_id}' as phantom (styled black)")
        else:
            if self.storage:
                self.storage.unmark_phantom_node(node_id)
                if clean_alias:
                    self.storage.unmark_phantom_node(clean_alias)
            if self.config:
                self.config.unmark_phantom_node(node_id)
                if clean_alias:
                    self.config.unmark_phantom_node(clean_alias)
            logger.info(f"Unmarked phantom node: {alias} ({node_id})")
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText(f"⚡ <b>Phantom Node:</b> Unmarked '{clean_alias or node_id}' (restored normal)")

        if self.storage:
            try:
                self.refresh_map_data()
            except Exception as e:
                logger.debug(f"Error refreshing map nodes after phantom toggle: {e}")

        from meshcore_tray.core.event_bus import bus, EventType
        bus.emit(EventType.MAP_NODES_UPDATED, None)

        if hasattr(self, "_active_visualise_msg") and self._active_visualise_msg:
            self.visualise_message_path(self._active_visualise_msg)
        elif hasattr(self, "_pending_visualise_msg") and self._pending_visualise_msg:
            self.visualise_message_path(self._pending_visualise_msg)

    def _on_bridge_delete_node(self, node_id: str):
        """Called when user confirms node deletion from the Leaflet popup."""
        if not node_id:
            return
        clean_id = node_id.strip().lstrip("!@").lower()
        alias = clean_id
        if self.storage:
            c = self.storage.get_contact(clean_id)
            if c and c.alias:
                alias = c.alias
            self.storage.delete_contact(clean_id)
        if self.config:
            self.config.favorite_users = [u for u in self.config.favorite_users if u != clean_id and u != alias]
            self.config.save()
        logger.info(f"Deleted node {clean_id} ({alias}) via Leaflet map popup.")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🗑️ Deleted node <b>{alias}</b> ({clean_id})")

        safe_nid_json = json.dumps(clean_id)
        if hasattr(self, "web_view") and self.web_view and self.web_view.page():
            self.web_view.page().runJavaScript(f"if (window.deleteNodeMarker) {{ window.deleteNodeMarker({safe_nid_json}); }}")

        if self.storage:
            try:
                self.refresh_map_data()
            except Exception as e:
                logger.debug(f"Error refreshing map nodes after delete: {e}")

        from meshcore_tray.core.event_bus import bus, EventType
        bus.emit(EventType.CONTACT_DELETED, clean_id)
        bus.emit(EventType.MAP_NODES_UPDATED, None)

    def apply_colors(self, app_colors=None):
        """Applies configured theme colors to map markers, lines, and watcher status."""
        if not app_colors and self.config:
            app_colors = getattr(self.config, "app_colors", None)
        if not app_colors:
            return

        stat_col = getattr(app_colors, "map_watcher_status_color", "#7EE787")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setStyleSheet(f"""
                background-color: #222327;
                color: {stat_col};
                border: 1px solid #414143;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-family: monospace;
            """)

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            theme_dict = {
                "repeater": app_colors.map_repeater_color,
                "repeaterHover": getattr(app_colors, "map_repeater_hover_color", "#60A5FA"),
                "companion": app_colors.map_companion_color,
                "companionHover": getattr(app_colors, "map_companion_hover_color", "#22D3EE"),
                "favorite": app_colors.favorite_user_color,
                "watcherStart": app_colors.map_watcher_line_start,
                "watcherEnd": app_colors.map_watcher_line_end,
                "messageStart": app_colors.map_message_line_start,
                "messageEnd": app_colors.map_message_line_end,
                "visualisedPath": getattr(app_colors, "map_visualised_path_color", "#FF00FF"),
                "visualisedHeading": getattr(app_colors, "map_visualised_heading_color", "#FF00FF"),
                "phantomPath": getattr(app_colors, "map_phantom_path_color", "#FFFF00"),
                "unknownPath": getattr(app_colors, "map_unknown_path_color", "#EF4444"),
                "noGpsPath": getattr(app_colors, "map_no_gps_path_color", "#000000"),
                "orbitalRepeater": getattr(app_colors, "map_orbital_repeater_color", "#FFD335"),
                "roomServer": getattr(app_colors, "map_room_server_color", "#A855F7"),
                "roomServerHover": getattr(app_colors, "map_room_server_hover_color", "#C084FC"),
                "dotSize": getattr(app_colors, "map_dot_size", 6.4)
            }
            self.run_js(f"setMapColors({json.dumps(theme_dict)});")

            # Sync ADS-B color mode and customized color thresholds
            adsb_colors = {
                "alt_ground": getattr(app_colors, "adsb_alt_ground", "#FF00FF"),
                "alt_low": getattr(app_colors, "adsb_alt_low", "#FF0000"),
                "alt_mid": getattr(app_colors, "adsb_alt_mid", "#0000FF"),
                "alt_high": getattr(app_colors, "adsb_alt_high", "#FFFFFF"),
                "type_airliner": getattr(app_colors, "adsb_type_airliner", "#FFFFFF"),
                "type_light": getattr(app_colors, "adsb_type_light", "#0000FF"),
                "type_military": getattr(app_colors, "adsb_type_military", "#00FF00"),
                "type_helicopter": getattr(app_colors, "adsb_type_helicopter", "#FFFF00"),
                "type_glider": getattr(app_colors, "adsb_type_glider", "#FF00FF"),
                "dist_close": getattr(app_colors, "adsb_dist_close", "#FF0000"),
                "dist_mid_close": getattr(app_colors, "adsb_dist_mid_close", "#FFA500"),
                "dist_mid_far": getattr(app_colors, "adsb_dist_mid_far", "#FFFF00"),
                "dist_far": getattr(app_colors, "adsb_dist_far", "#00FF00"),
            }
            mode = getattr(app_colors, "adsb_color_mode", "altitude")
            self.run_js(f"if (window.setAdsbColorConfig) window.setAdsbColorConfig('{mode}', {json.dumps(adsb_colors)});\nif (window.renderAdsbLegend) window.renderAdsbLegend();")

    def _on_bridge_node_clicked(self, node_id: str):
        self._reset_watchdog_activity()
        logger.info(f"Map marker clicked for node: {node_id}")
        self.node_selected.emit(node_id)

    def _on_center_clicked(self):
        self.run_js("centerOnAll();")

    def center_on_node(self, node_id: str, lat: Optional[float] = None, lon: Optional[float] = None, alias: str = ""):
        """Centers map on a specific node coordinates, opening its popup or pulsing marker."""
        if lat is None or lon is None:
            if self.storage:
                c = self.storage.get_contact(node_id)
                if c and c.latitude is not None and c.longitude is not None:
                    lat, lon = c.latitude, c.longitude
                    alias = c.alias or alias
        if lat is None or lon is None:
            if hasattr(self, "watcher_status"):
                self.watcher_status.setText(f"⚠️ Node '{alias or node_id}' has no GPS coordinates.")
            return False

        clean_alias = (alias or node_id).replace("'", "\\'")
        clean_id = node_id.replace("'", "\\'")
        js_code = f"""
            (function() {{
                if (typeof map !== 'undefined') {{
                    map.setView([{lat}, {lon}], 14);
                    if (typeof markers !== 'undefined' && markers['{clean_id}']) {{
                        markers['{clean_id}'].openPopup();
                    }} else if (typeof pulseOriginNode === 'function') {{
                        pulseOriginNode([{lat}, {lon}], '{clean_id}', '{clean_alias}');
                    }}
                }}
            }})();
        """
        self.run_js(js_code)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"📍 Centered map on: {alias or node_id} ({lat:.4f}, {lon:.4f})")
        return True

    def _on_repeaters_toggle(self):
        self.show_repeaters_only = self.btn_repeaters.isChecked()
        self.btn_repeaters.setStyleSheet(self._btn_style(self.show_repeaters_only))
        self.refresh_map_data()

    def _on_links_toggle(self):
        self.show_rf_links = self.btn_links.isChecked()
        self.show_paths = self.show_rf_links
        self.btn_links.setStyleSheet(self._btn_style(self.show_rf_links))
        self.refresh_map_data()

    def set_node_filter_mode(self, mode: str):
        self.node_filter_mode = mode
        self.refresh_map_data()

    def set_rf_links(self, visible: bool):
        self.show_rf_links = visible
        self.show_paths = visible
        self.refresh_map_data()

    def set_path_modes(self, visible: bool):
        if hasattr(self, "btn_path_modes"):
            self.btn_path_modes.blockSignals(True)
            self.btn_path_modes.setChecked(visible)
            self.btn_path_modes.setStyleSheet(self._btn_style(active=visible))
            self.btn_path_modes.blockSignals(False)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_path_modes = visible
            try:
                self.config.save()
            except Exception:
                pass
        pm_str = "true" if visible else "false"
        self.run_js(f"setPathModesVisible({pm_str});")

    def set_orbitals(self, visible: bool):
        self.show_companion_orbitals = visible
        if hasattr(self, "btn_orbitals"):
            self.btn_orbitals.blockSignals(True)
            self.btn_orbitals.setChecked(visible)
            self.btn_orbitals.setStyleSheet(self._btn_style(active=visible))
            self.btn_orbitals.blockSignals(False)
        p = self.window()
        if p and hasattr(p, "nav_dock") and hasattr(p.nav_dock, "set_layer_active"):
            p.nav_dock.set_layer_active("orbitals", visible)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_companion_orbitals = visible
            try:
                self.config.save()
            except Exception:
                pass
        docked_data = self.storage.get_docked_companions() if (self.storage and visible) else {}
        orb_str = "true" if visible else "false"
        self.run_js(f"setCompanionOrbitalsVisible({orb_str}, {json.dumps(docked_data)});")
        self.refresh_map_data()

    def set_scopes(self, visible: bool):
        if hasattr(self, "btn_scopes"):
            self.btn_scopes.blockSignals(True)
            self.btn_scopes.setChecked(visible)
            self.btn_scopes.setStyleSheet(self._btn_style(active=visible))
            self.btn_scopes.blockSignals(False)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_scopes = visible
            try:
                self.config.save()
            except Exception:
                pass
        self._update_scope_overlays()

    def set_tropo(self, visible: bool):
        self._on_tropo_toggle(visible)

    def reset_map_layers(self):
        self.clear_visualised_path()
        if hasattr(self, "btn_toggle_los"):
            self.btn_toggle_los.setChecked(False)
        if hasattr(self, "btn_profile_path"):
            self.btn_profile_path.setChecked(False)
        self.run_js("clearViewshedOverlay()")
        self.run_js("clearP2PLine()")
        self.run_js("setP2PMeasureMode(false)")
        if hasattr(self, "elevation_profile_dock"):
            self.elevation_profile_dock.hide()
        if hasattr(self, "bridge"):
            self.bridge.repeater_neighbors_cleared_signal.emit()
        self._on_center_clicked()
        bus.emit(EventType.CLEAR_VISUALISED_PATHS, None)

    def set_nodes(self, contacts=None):
        """Refreshes map nodes. Provided for compatibility with external callers."""
        self.refresh_map_data()

    def on_node_clicked(self, node_id: str):
        """Dispatches node selection event."""
        self._on_bridge_node_clicked(node_id)

    def refresh_map_data(self, debounce: bool = False):
        """Fetches nodes and RF links with coordinates and pushes to Leaflet map.
        
        If geometry is in motion or app is in initial staggered load, buffers the request.
        If debounce is True, defers the refresh by 150ms to coalesce burst events.
        """
        if getattr(self, "_geometry_in_motion", False) or getattr(self, "_initial_loading_active", False):
            self._pending_refresh = True
            return

        if debounce and hasattr(self, "_refresh_timer"):
            self._refresh_timer.start()
        else:
            if hasattr(self, "_refresh_timer") and self._refresh_timer.isActive():
                self._refresh_timer.stop()
            self._do_refresh_map_data()

    def _do_refresh_map_data(self):
        """Fetches nodes and RF links with coordinates and pushes to Leaflet map."""
        if not self.storage:
            return
        if WEBENGINE_AVAILABLE and not getattr(self, "_page_ready", False):
            self._pending_refresh = True
            return

        local_coord = self._get_local_coordinates()
        ref_lat, ref_lon = local_coord[0], local_coord[1]
        contacts = [
            c for c in self.storage.get_nodes_with_coordinates()
            if is_plausible_rf_coordinate(c.latitude, c.longitude, ref_lat=ref_lat, ref_lon=ref_lon, max_distance_km=2500.0)
        ]
        if self.node_filter_mode == "CLIENTS":
            contacts = [c for c in contacts if not c.is_repeater and not getattr(c, "is_room_server", False) and not is_room_server_contact(c) and "[rep]" not in (c.alias or "").lower() and "[room]" not in (c.alias or "").lower() and "[server]" not in (c.alias or "").lower()]
        elif self.node_filter_mode == "REPEATERS" or self.show_repeaters_only:
            contacts = [c for c in contacts if c.is_repeater or "[rep]" in (c.alias or "").lower()]
        elif self.node_filter_mode == "ROOMS":
            contacts = [c for c in contacts if getattr(c, "is_room_server", False) or is_room_server_contact(c)]

        nodes_data = []
        node_coords_map = {}

        overheard_modes = {}
        if self.storage and hasattr(self.storage, "get_overheard_path_modes"):
            try:
                overheard_modes = self.storage.get_overheard_path_modes() or {}
            except Exception as e:
                logger.warning(f"Error getting overheard path modes: {e}")
                overheard_modes = {}

        for c in contacts:
            node_coords_map[c.node_id] = [c.latitude, c.longitude]
            if c.public_key:
                node_coords_map[c.public_key[:12]] = [c.latitude, c.longitude]
                node_coords_map[c.public_key] = [c.latitude, c.longitude]

            is_fav = bool(c.is_favorite) or (self.config and self.config.is_user_favorite(c.node_id, c.alias))

            c_p_len = getattr(c, "out_path_len", -1)
            c_p_mode = getattr(c, "out_path_hash_mode", -1)
            c_p_src = "configured"

            if overheard_modes:
                clean_nid = str(c.node_id or "").lstrip("!@").strip().lower()
                clean_alias = str(c.alias or "").strip().lower()
                clean_pk = str(c.public_key or "").strip().lower()
                ov = (
                    overheard_modes.get(clean_nid)
                    or overheard_modes.get(clean_alias)
                    or (overheard_modes.get(clean_pk[:4]) if len(clean_pk) >= 4 else None)
                    or (overheard_modes.get(clean_pk[:6]) if len(clean_pk) >= 6 else None)
                    or (overheard_modes.get(clean_pk[:8]) if len(clean_pk) >= 8 else None)
                    or (overheard_modes.get(clean_pk[:12]) if len(clean_pk) >= 12 else None)
                    or (overheard_modes.get(clean_pk) if clean_pk else None)
                )
                if ov:
                    try:
                        ov_mode = int(ov.get("path_mode", -1))
                        if c_p_mode is None or c_p_mode < 0:
                            c_p_mode = ov_mode
                            c_p_src = str(ov.get("source", "overheard"))
                        elif ov_mode > c_p_mode:
                            c_p_mode = ov_mode
                            c_p_src = str(ov.get("source", "overheard"))

                        if c_p_len is None or c_p_len <= 0:
                            c_p_len = int(ov.get("path_len", -1))
                    except Exception:
                        pass

            nodes_data.append({
                "node_id": str(c.node_id or ""),
                "alias": str(c.alias or c.node_id or "Node"),
                "is_repeater": bool(c.is_repeater),
                "is_room_server": bool(getattr(c, "is_room_server", False) or is_room_server_contact(c)),
                "is_phantom": bool(self.storage and self.storage.is_phantom_node(c.node_id, c.alias)),
                "is_favorite": is_fav,
                "is_local": c.node_id == "local" or (self.config and c.node_id == self.config.meshcore.node_id.lstrip("!")),
                "lat": float(c.latitude),
                "lon": float(c.longitude),
                "snr": float(c.snr_db) if c.snr_db is not None else None,
                "rssi": int(c.rssi_dbm) if c.rssi_dbm is not None else None,
                "last_seen": c.last_seen,
                "first_seen": getattr(c, "first_seen", "") or "",
                "out_path_len": int(c_p_len) if c_p_len is not None else -1,
                "out_path_hash_mode": int(c_p_mode) if c_p_mode is not None else -1,
                "out_path_src": c_p_src,
                "out_path": getattr(c, "out_path", "") or "",
                "scope_name": getattr(c, "scope_name", None),
                "allowed_regions": getattr(c, "allowed_regions", []) or [],
                "source": str(getattr(c, "source", "radio") or "radio").strip().lower(),
                "is_mqtt": (str(getattr(c, "source", "radio") or "radio").strip().lower() == "mqtt")
            })

        rep_count = sum(1 for c in contacts if c.is_repeater)
        room_count = sum(1 for c in contacts if getattr(c, "is_room_server", False) or is_room_server_contact(c))
        if room_count > 0:
            self.stats_badge.setText(f"{len(contacts)} Nodes • {rep_count} Repeaters • {room_count} Rooms")
        else:
            self.stats_badge.setText(f"{len(contacts)} Nodes • {rep_count} Repeaters")

        # Companion Orbitals data & badge counter
        docked_data = {}
        docked_count = 0
        if self.storage:
            try:
                docked_count = int(self.storage.get_docked_companion_count() or 0)
            except Exception:
                docked_count = 0
            if hasattr(self, "btn_orbitals"):
                self.btn_orbitals.setText(f"🛰️ Orbitals ({docked_count})" if docked_count > 0 else "🛰️ Orbitals")
            is_orb_active = self.btn_orbitals.isChecked() if hasattr(self, "btn_orbitals") else getattr(self, "show_companion_orbitals", False)
            if is_orb_active:
                try:
                    docked_data = self.storage.get_docked_companions()
                except Exception:
                    docked_data = {}

        # RF Links
        links_data = []
        if self.show_rf_links:
            neighbours = self.storage.get_neighbours(limit=50)
            for n in neighbours:
                if n.node_id in node_coords_map:
                    n_coord = node_coords_map[n.node_id]
                    # Link to local or via_node
                    via_coord = None
                    if n.via_node_id and n.via_node_id in node_coords_map:
                        via_coord = node_coords_map[n.via_node_id]
                    elif "local" in node_coords_map:
                        via_coord = node_coords_map["local"]

                    if via_coord:
                        links_data.append({
                            "coords": [via_coord, n_coord],
                            "snr": n.snr_db,
                            "alias": n.alias
                        })

        js_nodes = json.dumps(nodes_data)
        js_links = json.dumps(links_data)
        self.run_js(f"setNodes({js_nodes});")
        self.run_js(f"setRfLinks({js_links});")
        is_orb_active = self.btn_orbitals.isChecked() if hasattr(self, "btn_orbitals") else getattr(self, "show_companion_orbitals", False)
        orb_active_str = "true" if is_orb_active else "false"
        js_docked = json.dumps(docked_data)
        self.run_js(f"setCompanionOrbitalsVisible({orb_active_str}, {js_docked});")
        if hasattr(self, "btn_scopes") and self.btn_scopes.isChecked():
            self._update_scope_overlays()

    def _on_packet_path_traced(self, path: PacketPathInfo, force_animate: bool = False):
        """Displays traced multi-hop packet trajectory on the map."""
        if not path:
            return

        # Deduplication check: extract raw message/packet id and logical signature
        now = time.time()
        if len(self._recent_packet_events) > 50:
            self._recent_packet_events = {k: ts for k, ts in self._recent_packet_events.items() if now - ts < 3.5}

        pkt_id = getattr(path, "packet_id", "")
        raw_id = pkt_id[5:] if pkt_id.startswith("path-") else pkt_id
        decoded = getattr(path, "decoded_info", None) or {}
        text = str(decoded.get("text") or "").strip()
        chan = str(decoded.get("channel") or "").strip()
        sig = f"{chan}:{text}" if text else ""

        if not force_animate:
            if raw_id and raw_id in self._recent_packet_events and (now - self._recent_packet_events[raw_id] < 3.5):
                return
            if sig and sig in self._recent_packet_events and (now - self._recent_packet_events[sig] < 3.5):
                return

        if raw_id:
            self._recent_packet_events[raw_id] = now
        if sig:
            self._recent_packet_events[sig] = now

        # Record packet in network activity timeline
        if hasattr(self, "activity_timeline_dock") and self.activity_timeline_dock:
            self.activity_timeline_dock.record_packet()

        hops_str = " ➔ ".join(path.hop_nodes) if path.hop_nodes else f"{len(path.coordinates or [])} hops"
        snr_text = f" ({path.hop_snrs[0]:+.1f} dB)" if path.hop_snrs else ""
        t_str = datetime.now().strftime("%H:%M:%S")

        self.watcher_status.setText(f"[{t_str}] ⚡ {path.route_type}: {hops_str}{snr_text}")
        # Assemble multi-hop route coordinates across repeaters with per-hop verification
        local_coord = self._get_local_coordinates()
        node_seq = []

        # 1. Sender coordinate
        if path.coordinates and len(path.coordinates) > 0:
            node_seq.append({"coord": [float(path.coordinates[0][0]), float(path.coordinates[0][1])], "is_ambiguous": False})
        elif self.storage and path.sender_id and path.sender_id != "mesh":
            c_s = self.storage.get_contact(path.sender_id.lstrip("!@").strip())
            if c_s and c_s.latitude is not None and c_s.longitude is not None:
                node_seq.append({"coord": [float(c_s.latitude), float(c_s.longitude)], "is_ambiguous": False})
            else:
                node_seq.append({"coord": None, "is_ambiguous": False})
        else:
            node_seq.append({"coord": None, "is_ambiguous": False})

        # 2. Intermediate repeater coordinates
        if path.coordinates and len(path.coordinates) >= 2:
            for pt in path.coordinates[1:]:
                coord = [float(pt[0]), float(pt[1])]
                node_seq.append({"coord": coord, "is_ambiguous": False})
        elif path.hop_nodes and self.storage:
            clean_prefixes = [hn.lstrip("@!🌐☁️ ").strip() for hn in path.hop_nodes]
            sender_tuple = (node_seq[0]["coord"][0], node_seq[0]["coord"][1]) if (node_seq and node_seq[0]["coord"]) else None
            local_tuple = (local_coord[0], local_coord[1]) if local_coord else (54.65897, -3.4346)
            if hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                chain = self.storage.resolve_hop_chain_with_candidates(
                    clean_prefixes,
                    sender_coord=sender_tuple,
                    home_coord=local_tuple,
                    user_station_prefix="M7NCY",
                    sender_name=path.sender_name
                )
                for item in chain:
                    c_hop = item.get("contact")
                    is_phant = bool(item.get("is_phantom") or (self.storage and c_hop and self.storage.is_phantom_node(c_hop.node_id, c_hop.alias)))
                    c_coord = [float(c_hop.latitude), float(c_hop.longitude)] if (c_hop and c_hop.latitude is not None and c_hop.longitude is not None and not is_phant) else None
                    node_seq.append({
                        "coord": c_coord,
                        "is_ambiguous": bool(item.get("is_ambiguous")),
                        "is_phantom": is_phant
                    })
            else:
                for clean_h in clean_prefixes:
                    c_hop, cands, is_amb = (None, [], False)
                    if hasattr(self.storage, "resolve_hop_with_candidates"):
                        c_hop, cands, is_amb = self.storage.resolve_hop_with_candidates(clean_h)
                    else:
                        c_hop = self.storage.get_contact(clean_h)
                    c_coord = [float(c_hop.latitude), float(c_hop.longitude)] if (c_hop and c_hop.latitude is not None and c_hop.longitude is not None) else None
                    node_seq.append({
                        "coord": c_coord,
                        "is_ambiguous": is_amb,
                        "is_phantom": False
                    })

        # 3. Local receiver coordinate (Home station)
        if local_coord:
            node_seq.append({"coord": local_coord, "is_ambiguous": False})

        route_coords = []
        hop_metas = []
        last_loc_idx = None
        for idx, n in enumerate(node_seq):
            if n["coord"]:
                if last_loc_idx is not None:
                    p2 = n["coord"]
                    if not route_coords or route_coords[-1] != p2:
                        route_coords.append(p2)
                    has_missing = False
                    has_amb = bool(n.get("is_ambiguous"))
                    for m in range(last_loc_idx + 1, idx):
                        if not node_seq[m]["coord"]:
                            has_missing = True
                        if node_seq[m].get("is_ambiguous"):
                            has_amb = True
                    hop_metas.append({
                        "is_ambiguous": has_amb,
                        "is_inferred": has_missing
                    })
                else:
                    route_coords.append(n["coord"])
                last_loc_idx = idx

        # If incoming via MQTT with fewer coordinates resolved than hops reported, mark as inferred trajectory
        if getattr(path, "source", "") == "mqtt" or getattr(path, "packet_id", "").startswith("mqtt-"):
            if len(path.hop_nodes or []) > len(route_coords) - 1:
                for hm in hop_metas:
                    hm["is_inferred"] = True

        ambiguous_hops = sum(1 for hm in hop_metas if hm.get("is_ambiguous"))
        inferred_hops = sum(1 for hm in hop_metas if hm.get("is_inferred"))

        if route_coords:
            self._last_traced_path_info = (datetime.now().timestamp(), route_coords)

        js_coords = json.dumps(route_coords) if len(route_coords) >= 2 else "[]"
        text = ""
        chan = ""
        if path.decoded_info and isinstance(path.decoded_info, dict):
            text = str(path.decoded_info.get("text", "") or "")
            chan = str(path.decoded_info.get("channel", "") or "")
        if not chan and "[" in (path.sender_name or "") and "]" in (path.sender_name or ""):
            try:
                chan = path.sender_name.split("[", 1)[1].split("]", 1)[0]
            except Exception:
                pass

        p_source = "mqtt" if getattr(path, "source", "") == "mqtt" or getattr(path, "packet_id", "").startswith("mqtt-") else "radio"

        js_meta = json.dumps({
            "hops": max(1, len(route_coords) - 1) if len(route_coords) >= 2 else (len(path.hop_nodes) if path.hop_nodes else 1),
            "ambiguous_hops": ambiguous_hops,
            "inferred_hops": inferred_hops,
            "hop_metas": hop_metas,
            "route_type": path.route_type,
            "payload_type": getattr(path, "payload_type", path.route_type) or path.route_type,
            "sender_id": path.sender_id,
            "sender_name": path.sender_name,
            "channel": chan,
            "text": text,
            "raw_hex": getattr(path, "raw_hex", ""),
            "recipient_id": getattr(path, "recipient_id", ""),
            "recipient_name": getattr(path, "recipient_name", ""),
            "source": p_source,
            "packet_id": getattr(path, "packet_id", ""),
            "color": "orange"
        })

        # Send to CoreScope Live Packet Feed HUD
        self.run_js(f"if (window.addPacketToHud) window.addPacketToHud({js_meta}, {js_coords});")

        if len(route_coords) < 2:
            return

        if force_animate or self.show_paths or self.show_rf_links:
            self.run_js(f"drawPacketPath({js_coords}, {js_meta}); if (window.tracePacketPath3D) window.tracePacketPath3D({js_meta}, {js_coords});")

    def trigger_corescope_trace(self, path: PacketPathInfo):
        """Explicitly executes CoreScope traveling particle beam animation on the map."""
        if path:
            self._on_packet_path_traced(path, force_animate=True)

    def _get_local_coordinates(self) -> List[float]:

        """Resolves latitude & longitude of the local radio receiver (home station)."""
        # 1. Direct configuration
        if self.config and hasattr(self.config.meshcore, "latitude") and hasattr(self.config.meshcore, "longitude"):
            if self.config.meshcore.latitude is not None and self.config.meshcore.longitude is not None:
                return [float(self.config.meshcore.latitude), float(self.config.meshcore.longitude)]

        if self.storage:
            # 2. Local contact
            c_local = self.storage.get_contact("local")
            if c_local and c_local.latitude and c_local.longitude:
                return [c_local.latitude, c_local.longitude]

            # 3. Contact matching configured node_id
            if self.config and self.config.meshcore.node_id:
                c_node = self.storage.get_contact(self.config.meshcore.node_id.lstrip("!"))
                if c_node and c_node.latitude and c_node.longitude:
                    return [c_node.latitude, c_node.longitude]

            # 4. Known station matching user callsign prefix (e.g. M7NCY)
            coords_nodes = self.storage.get_nodes_with_coordinates()
            for c in coords_nodes:
                if "M7NCY" in c.alias.upper():
                    return [c.latitude, c.longitude]

        # 5. Default home station coordinates (Workington, Cumbria)
        return [54.65897, -3.4346]

    def _on_message_received(self, msg: MessageEnvelope):
        """When an incoming message arrives, display the route it took to get to me with a green line on the map."""
        if not msg or not WEBENGINE_AVAILABLE or not hasattr(self, "web_view") or not self._page_ready:
            return
        if not self.storage:
            return

        now = time.time()
        if len(self._recent_packet_events) > 50:
            self._recent_packet_events = {k: ts for k, ts in self._recent_packet_events.items() if now - ts < 3.5}

        msg_id = getattr(msg, "id", "")
        text = str(getattr(msg, "text", "") or "").strip()
        chan = str(getattr(msg, "channel", "") or "").strip()
        sig = f"{chan}:{text}" if text else ""

        if msg_id and msg_id in self._recent_packet_events and (now - self._recent_packet_events[msg_id] < 3.5):
            return
        if sig and sig in self._recent_packet_events and (now - self._recent_packet_events[sig] < 3.5):
            return

        if msg_id:
            self._recent_packet_events[msg_id] = now
        if sig:
            self._recent_packet_events[sig] = now

        # Record packet in network activity timeline
        if hasattr(self, "activity_timeline_dock") and self.activity_timeline_dock:
            self.activity_timeline_dock.record_packet()

        # 1. Resolve Sender Coordinate
        sender_contact = self.storage.get_contact(msg.sender_id) or self.storage.get_contact(msg.sender_name)
        sender_coord = None
        if sender_contact and sender_contact.latitude and sender_contact.longitude:
            sender_coord = [sender_contact.latitude, sender_contact.longitude]

        # 2. Resolve Intermediate Hop Coordinates from Path metadata or recent Watcher trace
        path_str = ""
        path_len = 0
        if msg.metadata:
            path_str = str(msg.metadata.get("path", "") or "")
            path_len = int(msg.metadata.get("path_len", 0) or 0)

        # 3. Resolve Local Receiver Coordinate
        local_coord = self._get_local_coordinates()

        node_seq = []
        if sender_coord:
            node_seq.append({"coord": sender_coord, "is_ambiguous": False})
        else:
            node_seq.append({"coord": None, "is_ambiguous": False})

        if path_str and path_len > 0:
            chunk_size = max(2, len(path_str) // path_len)
            raw_hops = [path_str[i * chunk_size : (i + 1) * chunk_size] for i in range(path_len)]
            if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
                sender_tuple = (sender_coord[0], sender_coord[1]) if (sender_coord and len(sender_coord) >= 2) else None
                local_tuple = (local_coord[0], local_coord[1]) if (local_coord and len(local_coord) >= 2) else (54.65897, -3.4346)
                chain = self.storage.resolve_hop_chain_with_candidates(
                    raw_hops,
                    sender_coord=sender_tuple,
                    home_coord=local_tuple,
                    user_station_prefix="M7NCY",
                    sender_name=msg.sender_name
                )
                for item in chain:
                    c_h = item.get("contact")
                    is_phant = bool(item.get("is_phantom") or (self.storage and c_h and self.storage.is_phantom_node(c_h.node_id, c_h.alias)))
                    c_coord = [c_h.latitude, c_h.longitude] if (c_h and c_h.latitude is not None and c_h.longitude is not None and not is_phant) else None
                    node_seq.append({
                        "coord": c_coord,
                        "is_ambiguous": bool(item.get("is_ambiguous")),
                        "is_phantom": is_phant
                    })
            else:
                last_ref_lat, last_ref_lon = (54.65897, -3.4346)
                for sub_h in raw_hops:
                    c_hop, cands, is_amb = (None, [], False)
                    if hasattr(self.storage, "resolve_hop_with_candidates"):
                        c_hop, cands, is_amb = self.storage.resolve_hop_with_candidates(sub_h, ref_lat=last_ref_lat, ref_lon=last_ref_lon)
                    elif self.storage:
                        c_hop = self.storage.get_contact(sub_h)
                    c_coord = [c_hop.latitude, c_hop.longitude] if (c_hop and c_hop.latitude and c_hop.longitude) else None
                    node_seq.append({
                        "coord": c_coord,
                        "is_ambiguous": is_amb,
                        "is_phantom": False
                    })
                    if c_coord:
                        last_ref_lat, last_ref_lon = float(c_coord[0]), float(c_coord[1])
        elif hasattr(self, "_last_traced_path_info") and self._last_traced_path_info:
            now_ts = datetime.now().timestamp()
            l_time, l_coords = self._last_traced_path_info
            if (now_ts - l_time <= 2.5) and l_coords:
                for pt in l_coords:
                    node_seq.append({"coord": list(pt), "is_ambiguous": False})

        if local_coord:
            node_seq.append({"coord": local_coord, "is_ambiguous": False})

        # 4. Assemble Complete Route and per-hop verification metadata
        route_coords = []
        hop_metas = []
        last_loc_idx = None
        for idx, n in enumerate(node_seq):
            if n["coord"]:
                if last_loc_idx is not None:
                    p2 = n["coord"]
                    if not route_coords or route_coords[-1] != p2:
                        route_coords.append(p2)
                    has_missing = False
                    has_amb = bool(n.get("is_ambiguous"))
                    for m in range(last_loc_idx + 1, idx):
                        if not node_seq[m]["coord"]:
                            has_missing = True
                        if node_seq[m].get("is_ambiguous"):
                            has_amb = True
                    hop_metas.append({
                        "is_ambiguous": has_amb,
                        "is_inferred": has_missing
                    })
                else:
                    route_coords.append(n["coord"])
                last_loc_idx = idx

        # If incoming via MQTT with fewer coordinates resolved than hops reported, mark as inferred trajectory
        p_src = getattr(msg, "source_driver", "")
        if p_src == "mqtt" and path_len > len(route_coords) - 1:
            for hm in hop_metas:
                hm["is_inferred"] = True

        ambiguous_hops = sum(1 for hm in hop_metas if hm.get("is_ambiguous"))
        inferred_hops = sum(1 for hm in hop_metas if hm.get("is_inferred"))

        display_name = f"@{msg.sender_name}"
        if sender_contact:
            display_name = f"📡 {sender_contact.alias}" if sender_contact.is_repeater else f"@{sender_contact.alias}"

        payload_type = "GRP_TXT" if (msg.channel and msg.channel.lower() not in ("direct", "dm")) else "TXT_MSG"
        route_type = msg.metadata.get("route_type", "FLOOD") if msg.metadata else "FLOOD"

        js_coords = json.dumps(route_coords) if len(route_coords) >= 2 else (json.dumps([sender_coord]) if sender_coord else "[]")
        js_meta = json.dumps({
            "hops": max(1, len(route_coords) - 1) if len(route_coords) >= 2 else 1,
            "ambiguous_hops": ambiguous_hops,
            "inferred_hops": inferred_hops,
            "hop_metas": hop_metas,
            "route_type": route_type,
            "payload_type": payload_type,
            "sender_id": msg.sender_id,
            "sender_name": display_name,
            "channel": msg.channel,
            "text": msg.text,
            "is_incoming": True,
            "source": "mqtt" if getattr(msg, "source_driver", "") == "mqtt" else "radio",
            "packet_id": getattr(msg, "id", ""),
            "color": "green"
        })

        # Prepend message to CoreScope Live Packet Feed HUD ticker
        self.run_js(f"if (window.addPacketToHud) window.addPacketToHud({js_meta}, {js_coords});")

        # If 2 or more coordinates are resolved, draw the animated particle beam on the map!
        if len(route_coords) >= 2 and (self.show_paths or self.show_rf_links):
            self.run_js(f"drawPacketPath({js_coords}, {js_meta}); if (window.tracePacketPath3D) window.tracePacketPath3D({js_meta}, {js_coords});")
        elif sender_coord:
            # Fallback: pulse origin node if only sender is known
            js_coord = json.dumps(sender_coord)
            js_id = json.dumps(msg.sender_id)
            js_name = json.dumps(display_name)
            self.run_js(f"pulseOriginNode({js_coord}, {js_id}, {js_name});")

    def clear_visualised_path(self):
        """Clears any currently visualised dotted message path and repeater highlights from the map."""
        self._active_visualise_msg = None
        self.run_js("clearVisualisedPath();")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def clear_repeater_neighbors(self):
        """Clears repeater neighbours overlay from the map."""
        self.run_js("clearRepeaterNeighbors();")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def _on_repeater_neighbors_cleared(self):
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def display_repeater_neighbors(self, repeater_contact, neighbors_data: list):
        """Resolves neighbour locations and displays white pulsing beacons, white connecting lines, and midpoint SNR/time badges."""
        if not repeater_contact:
            return

        # 1. Resolve host repeater coordinates
        if isinstance(repeater_contact, dict):
            rep_lat = repeater_contact.get("latitude") or (repeater_contact.get("coord", [None, None])[0] if repeater_contact.get("coord") else None)
            rep_lon = repeater_contact.get("longitude") or (repeater_contact.get("coord", [None, None])[1] if repeater_contact.get("coord") else None)
            rep_id = repeater_contact.get("node_id") or repeater_contact.get("id", "")
            rep_alias = repeater_contact.get("alias", "") or rep_id
        else:
            rep_lat = getattr(repeater_contact, "latitude", None)
            rep_lon = getattr(repeater_contact, "longitude", None)
            rep_id = getattr(repeater_contact, "node_id", "")
            rep_alias = getattr(repeater_contact, "alias", "") or rep_id

        if (rep_lat is None or rep_lon is None) and self.storage:
            found = self.storage.get_contact(rep_id) or self.storage.get_contact(f"!{rep_id}")
            if found:
                rep_lat = found.latitude
                rep_lon = found.longitude

        rep_coord = [rep_lat, rep_lon] if (rep_lat is not None and rep_lon is not None) else None

        # 2. Resolve each neighbour's coordinate from storage
        resolved_neighbors = []
        for n in neighbors_data:
            nid = str(n.get("node_id", "")).lstrip("!")
            alias = None
            neigh_coord = None
            if self.storage:
                c = self.storage.get_contact(f"!{nid}") or self.storage.get_contact(nid)
                if not c:
                    cand, _, _ = self.storage.resolve_hop_with_candidates(nid)
                    c = cand
                if c:
                    alias = c.alias
                    if c.latitude is not None and c.longitude is not None:
                        neigh_coord = [c.latitude, c.longitude]

            resolved_neighbors.append({
                "node_id": nid,
                "alias": alias or f"!{nid}",
                "coord": neigh_coord,
                "snr_str": n.get("snr_str") or (f"{n.get('snr'):+.1f} dB" if isinstance(n.get("snr"), (int, float)) else (f"{n.get('snr')} dB" if n.get("snr") is not None else "0.0 dB")),
                "time_str": n.get("time_str") or n.get("time_ago") or "recently"
            })

        plotted_count = sum(1 for n in resolved_neighbors if n["coord"] is not None)
        total_count = len(resolved_neighbors)

        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(
                f"🌐 <b>Neighbours:</b> @{rep_alias} ({plotted_count}/{total_count} mapped with GPS) • White Lines"
            )

        payload = {
            "repeater": {
                "id": rep_id,
                "alias": rep_alias,
                "coord": rep_coord
            },
            "neighbors": resolved_neighbors
        }

        if getattr(self, "_page_ready", False):
            js_payload = json.dumps(payload)
            self.run_js(f"drawRepeaterNeighbors({js_payload});")
        else:
            self._pending_neighbors_payload = payload

    def visualise_node_path(self, node_id: str, clean_alias: str, lat: float, lon: float):
        """Visualises transmission route/path for a specific node from context menu or user action."""
        msg = None
        if self.storage:
            try:
                with self.storage._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT id, sender_id, sender_name, channel, text, timestamp, is_outgoing, metadata
                        FROM messages
                        WHERE sender_id = ? OR sender_name = ?
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """, (node_id, clean_alias))
                    row = cur.fetchone()
                    if row:
                        from meshcore_tray.core.models import MessageEnvelope
                        meta = json.loads(row["metadata"]) if row["metadata"] else {}
                        msg = MessageEnvelope(
                            id=row["id"],
                            sender_id=row["sender_id"],
                            sender_name=row["sender_name"],
                            channel=row["channel"],
                            text=row["text"],
                            timestamp=row["timestamp"],
                            is_outgoing=bool(row["is_outgoing"]),
                            metadata=meta
                        )
            except Exception as e:
                logger.debug(f"Error querying last message for node {node_id}: {e}")

        if msg:
            self.visualise_message_path(msg)
        else:
            home_coords = self._get_local_coordinates()
            segments = [{
                "coords": [home_coords, [lat, lon]],
                "color": "#FF00FF",
                "is_unknown": False
            }]
            meta = {
                "sender_name": "Home Station",
                "recipient_name": clean_alias,
                "color": "#FF00FF",
                "repeaters": [{"name": clean_alias, "lat": lat, "lon": lon, "is_known": True}]
            }
            js_segments = json.dumps(segments)
            js_meta = json.dumps(meta)
            self.run_js(f"drawVisualisedMessagePath({js_segments}, {js_meta});")

    def visualise_message_path(self, msg: MessageEnvelope):
        """Visualises the multi-hop transmission path of a specific message on the map with a dotted line and highlighted repeaters."""
        if not msg:
            return
        self._active_visualise_msg = msg

        # 1. Resolve Sender Coordinate
        sender_coord = None
        sender_contact = None
        if msg.is_outgoing:
            sender_coord = self._get_local_coordinates()
            sender_display = "You (Local Node)"
        else:
            if self.storage:
                sender_contact = (
                    self.storage.get_best_contact_for_hop(msg.sender_id, ref_lat=54.65897, ref_lon=-3.4346)
                    or self.storage.get_contact(msg.sender_id)
                    or self.storage.get_contact(msg.sender_name)
                )
            if sender_contact and sender_contact.latitude and sender_contact.longitude:
                sender_coord = [sender_contact.latitude, sender_contact.longitude]
                sender_display = f"@{sender_contact.alias}"
            else:
                sender_display = f"@{msg.sender_name}"

        # 2. Resolve Intermediate Repeaters / Hops
        repeaters_info = []
        hop_coords = []

        path_str = ""
        path_len = 0
        if msg.metadata:
            path_str = str(msg.metadata.get("path", "") or "")
            path_len = int(msg.metadata.get("path_len", 0) or 0)

        # Check if hop_nodes or repeaters are already stored in message metadata
        raw_reps = []
        if msg.metadata and (msg.metadata.get("repeaters") or msg.metadata.get("hop_nodes")):
            raw_reps = list(msg.metadata.get("repeaters") or msg.metadata.get("hop_nodes") or [])

        # If not present in metadata, check packet_paths database table by timestamp match
        if not path_str and not raw_reps and self.storage:
            try:
                with self.storage._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT hop_nodes, route_type FROM packet_paths
                        WHERE ABS(strftime('%s', timestamp) - strftime('%s', ?)) <= 5
                        ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC
                        LIMIT 1
                    """, (msg.timestamp, msg.timestamp))
                    m_row = cur.fetchone()
                    if m_row and m_row["hop_nodes"]:
                        raw_reps = json.loads(m_row["hop_nodes"])
            except Exception as e:
                logger.debug(f"Error querying packet_paths for message {msg.id}: {e}")

        home_lat = 54.65897
        home_lon = -3.4346
        home_alias = "M7NCY"
        if self.config and hasattr(self.config, "meshcore"):
            if self.config.meshcore.latitude is not None:
                home_lat = float(self.config.meshcore.latitude)
            if self.config.meshcore.longitude is not None:
                home_lon = float(self.config.meshcore.longitude)
            if self.config.meshcore.node_alias:
                home_alias = str(self.config.meshcore.node_alias)

        # Determine raw hop identifiers in sequence
        raw_hop_hashes = []
        if path_str and path_len > 0:
            chunk_size = max(2, len(path_str) // path_len)
            for i in range(path_len):
                raw_hop_hashes.append(path_str[i * chunk_size : (i + 1) * chunk_size])
        elif raw_reps:
            for r_item in raw_reps:
                raw_hop_hashes.append(str(r_item).lstrip("@!").strip())

        sender_coord_tuple = (sender_coord[0], sender_coord[1]) if (sender_coord and len(sender_coord) >= 2) else None
        home_coord_tuple = (home_lat, home_lon)

        chain_results = []
        if self.storage and hasattr(self.storage, "resolve_hop_chain_with_candidates"):
            chain_results = self.storage.resolve_hop_chain_with_candidates(
                raw_hop_hashes,
                sender_coord=sender_coord_tuple,
                home_coord=home_coord_tuple,
                user_station_prefix=home_alias,
                sender_name=sender_display
            )
        else:
            for sub_h in raw_hop_hashes:
                c_hop, cands, is_amb = (None, [], False)
                if self.storage and hasattr(self.storage, "resolve_hop_with_candidates"):
                    c_hop, cands, is_amb = self.storage.resolve_hop_with_candidates(
                        sub_h, ref_lat=home_lat, ref_lon=home_lon, user_station_prefix=home_alias
                    )
                elif self.storage:
                    c_hop = self.storage.get_contact(sub_h)
                chain_results.append({
                    "contact": c_hop,
                    "candidates": cands,
                    "is_ambiguous": is_amb,
                    "ref_name": home_alias,
                    "ref_coord": home_coord_tuple,
                    "hash": sub_h
                })

        for item in chain_results:
            c_hop = item.get("contact")
            cands = item.get("candidates", [])
            is_amb = item.get("is_ambiguous", False)
            h_str = item.get("hash", "")
            r_name_ref = item.get("ref_name", "")

            if c_hop:
                rep_name = c_hop.alias
                c_lat = c_hop.latitude
                c_lon = c_hop.longitude
                is_phantom = bool(
                    item.get("is_phantom")
                    or (self.storage and self.storage.is_phantom_node(c_hop.node_id, c_hop.alias))
                    or (self.config and self.config.is_phantom_node(c_hop.node_id))
                    or (self.config and self.config.is_phantom_node(c_hop.alias))
                )
                if is_phantom:
                    c_lat = None
                    c_lon = None

                cand_payload = [
                    {
                        "alias": cd["alias"],
                        "node_id": cd["node_id"],
                        "lat": cd["latitude"] if not (
                            cd.get("is_phantom")
                            or (self.storage and self.storage.is_phantom_node(cd["node_id"], cd["alias"]))
                            or (self.config and self.config.is_phantom_node(cd["node_id"]))
                            or (self.config and self.config.is_phantom_node(cd["alias"]))
                        ) else None,
                        "lon": cd["longitude"] if not (
                            cd.get("is_phantom")
                            or (self.storage and self.storage.is_phantom_node(cd["node_id"], cd["alias"]))
                            or (self.config and self.config.is_phantom_node(cd["node_id"]))
                            or (self.config and self.config.is_phantom_node(cd["alias"]))
                        ) else None,
                        "dist_km": cd["dist_km"],
                        "snr": cd["snr_db"],
                        "is_selected": (cd["node_id"] == c_hop.node_id),
                        "is_saved_preference": bool(cd.get("is_saved_preference", False)),
                        "is_phantom": bool(
                            cd.get("is_phantom")
                            or (self.storage and self.storage.is_phantom_node(cd["node_id"], cd["alias"]))
                            or (self.config and self.config.is_phantom_node(cd["node_id"]))
                            or (self.config and self.config.is_phantom_node(cd["alias"]))
                        )
                    }
                    for cd in cands
                ] if is_amb else []

                repeaters_info.append({
                    "name": rep_name,
                    "alias": rep_name,
                    "node_id": c_hop.node_id,
                    "is_known": True,
                    "is_phantom": is_phantom,
                    "lat": c_lat,
                    "lon": c_lon,
                    "snr": c_hop.snr_db,
                    "hash": h_str,
                    "is_ambiguous": is_amb,
                    "candidates": cand_payload,
                    "ref_name": r_name_ref
                })
                if c_lat is not None and c_lon is not None and not is_phantom:
                    hop_coords.append([c_lat, c_lon])
            else:
                is_phantom = bool(
                    (self.storage and self.storage.is_phantom_node(f"!{h_str}"))
                    or (self.config and self.config.is_phantom_node(f"!{h_str}"))
                    or (self.config and self.config.is_phantom_node(h_str))
                )
                repeaters_info.append({
                    "name": f"?? Unknown ({h_str})",
                    "alias": f"?? Unknown ({h_str})",
                    "node_id": f"!{h_str}",
                    "is_known": False,
                    "is_phantom": is_phantom,
                    "lat": None,
                    "lon": None,
                    "snr": None,
                    "hash": h_str,
                    "is_ambiguous": False,
                    "candidates": [],
                    "ref_name": r_name_ref
                })

        # 3. Resolve Receiver Coordinate (Always terminates at Home Node for incoming, or Recipient Node for outgoing)
        receiver_coord = None
        receiver_display = home_alias
        if msg.is_outgoing:
            receiver_display = f"@{msg.recipient_name or 'Recipient'}"
            if msg.recipient_id and self.storage:
                recip_contact = (
                    self.storage.get_best_contact_for_hop(msg.recipient_id, ref_lat=home_lat, ref_lon=home_lon)
                    or self.storage.get_contact(msg.recipient_id)
                    or self.storage.get_contact(msg.recipient_name)
                )
                if recip_contact and recip_contact.latitude and recip_contact.longitude:
                    receiver_coord = [recip_contact.latitude, recip_contact.longitude]
                    receiver_display = f"@{recip_contact.alias}"
        else:
            receiver_coord = self._get_local_coordinates()
            receiver_display = home_alias

        # 4. Assemble Transmission Line Segments
        path_color = "#FF00FF"
        heading_color = "#FF00FF"
        phantom_path_color = "#FFFF00"
        unknown_path_color = "#EF4444"
        no_gps_path_color = "#000000"
        if self.config and hasattr(self.config, "app_colors"):
            path_color = getattr(self.config.app_colors, "map_visualised_path_color", "#FF00FF")
            heading_color = getattr(self.config.app_colors, "map_visualised_heading_color", "#FF00FF")
            phantom_path_color = getattr(self.config.app_colors, "map_phantom_path_color", "#FFFF00")
            unknown_path_color = getattr(self.config.app_colors, "map_unknown_path_color", "#EF4444")
            no_gps_path_color = getattr(self.config.app_colors, "map_no_gps_path_color", "#000000")

        node_chain = []
        if sender_coord:
            node_chain.append({"name": sender_display, "coords": sender_coord, "is_known": True})
        elif not msg.is_outgoing:
            node_chain.append({"name": sender_display, "coords": None, "is_known": False})

        for r in repeaters_info:
            c = [r["lat"], r["lon"]] if (r.get("lat") is not None and r.get("lon") is not None and not r.get("is_phantom")) else None
            node_chain.append({
                "name": r.get("alias") or r.get("name"),
                "coords": c,
                "is_known": bool(r.get("is_known")),
                "is_phantom": bool(r.get("is_phantom")),
                "is_ambiguous": bool(r.get("is_ambiguous")),
                "candidates_count": len(r.get("candidates") or [])
            })

        if receiver_coord:
            node_chain.append({"name": receiver_display, "coords": receiver_coord, "is_known": True, "is_ambiguous": False})
        elif msg.is_outgoing:
            node_chain.append({"name": receiver_display, "coords": None, "is_known": False, "is_ambiguous": False})

        route_segments = []
        last_loc_idx = None
        for i, node in enumerate(node_chain):
            if node["coords"]:
                if last_loc_idx is not None:
                    p1 = node_chain[last_loc_idx]["coords"]
                    p2 = node["coords"]
                    has_no_gps = False
                    has_unknown = False
                    has_phantom = False
                    has_ambiguous = bool(node.get("is_ambiguous"))
                    cands_count = node.get("candidates_count", 0)
                    missing_names = []
                    for k in range(last_loc_idx + 1, i):
                        if not node_chain[k]["coords"]:
                            missing_names.append(node_chain[k]["name"])
                            if node_chain[k].get("is_phantom"):
                                has_phantom = True
                            elif not node_chain[k]["is_known"]:
                                has_unknown = True
                            else:
                                has_no_gps = True
                        if node_chain[k].get("is_ambiguous"):
                            has_ambiguous = True
                            if node_chain[k].get("candidates_count", 0) > cands_count:
                                cands_count = node_chain[k].get("candidates_count", 0)

                    if has_phantom:
                        seg_color = phantom_path_color
                    elif has_no_gps:
                        seg_color = no_gps_path_color
                    elif has_unknown:
                        seg_color = unknown_path_color
                    else:
                        seg_color = path_color

                    route_segments.append({
                        "coords": [p1, p2],
                        "from_name": node_chain[last_loc_idx]["name"],
                        "to_name": node["name"],
                        "is_unknown": has_unknown,
                        "is_no_gps": has_no_gps,
                        "is_phantom": has_phantom,
                        "is_ambiguous": has_ambiguous,
                        "is_inferred": bool(has_unknown or has_no_gps or has_phantom),
                        "candidates_count": cands_count,
                        "missing_names": missing_names,
                        "color": seg_color
                    })
                last_loc_idx = i

        # 5. Format Time
        time_str = ""
        try:
            dt = datetime.fromisoformat(str(msg.timestamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            time_str = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            time_str = str(msg.timestamp)[:8]

        # 6. Status Text Update
        known_count = sum(1 for r in repeaters_info if r["is_known"] and not r.get("is_phantom"))
        phantom_count = sum(1 for r in repeaters_info if r.get("is_phantom"))
        unknown_count = len(repeaters_info) - known_count - phantom_count
        ambiguous_count = sum(1 for r in repeaters_info if r.get("is_ambiguous"))
        amb_str = f", {ambiguous_count} with alternates" if ambiguous_count > 0 else ""
        phant_str = f", {phantom_count} phantom" if phantom_count > 0 else ""
        rep_summary = f"{len(repeaters_info)} repeaters ({known_count} known{amb_str}{phant_str}, {unknown_count} unknown)" if repeaters_info else "Direct (0 repeaters)"
        if hasattr(self, "watcher_status"):
            target_icon = "🎯" if msg.is_outgoing else "🏠"
            self.watcher_status.setText(f"📍 <b>Path:</b> {sender_display} ➔ [{rep_summary}] ➔ {target_icon} {receiver_display}")

        # 7. Push to Leaflet
        if hasattr(self, "web_view"):
            if not self._page_ready:
                self._pending_visualise_msg = msg
                return
            self._pending_visualise_msg = None

            js_segments = json.dumps(route_segments)
            js_meta = json.dumps({
                "msg_id": msg.id,
                "sender_id": msg.sender_id,
                "sender_name": sender_display,
                "sender_coord": sender_coord,
                "home_coord": receiver_coord,
                "channel": msg.channel,
                "route_type": msg.metadata.get("route_type", "FLOOD") if msg.metadata else "FLOOD",
                "time": time_str,
                "color": path_color,
                "heading_color": heading_color,
                "phantom_color": phantom_path_color,
                "unknown_color": unknown_path_color,
                "no_gps_color": no_gps_path_color,
                "home_name": receiver_display,
                "repeaters": repeaters_info
            })
            self.run_js(f"drawVisualisedMessagePath({js_segments}, {js_meta});")

    def cleanup(self):
        """Stops background polling timers and detaches WebEngine page cleanly."""
        if getattr(self, "_is_cleaned_up", False):
            return
        self._is_cleaned_up = True
        try:
            if hasattr(self, "_refresh_timer") and self._refresh_timer.isActive():
                self._refresh_timer.stop()
            if hasattr(self, "adsb_service") and self.adsb_service:
                self.adsb_service.set_enabled(False)
            if hasattr(self, "thunderstorm_service") and self.thunderstorm_service:
                self.thunderstorm_service.set_enabled(False)
            if hasattr(self, "space_weather_service") and self.space_weather_service:
                self.space_weather_service.stop_polling()
            if hasattr(self, "satellite_service") and self.satellite_service:
                self.satellite_service.stop()
            if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self.web_view:
                try:
                    self.web_view.stop()
                    page = self.web_view.page()
                    if page is not None:
                        self.web_view.setPage(None)
                        page.deleteLater()
                except RuntimeError:
                    pass
        except Exception as e:
            logger.debug(f"Map cleanup exception: {e}")
