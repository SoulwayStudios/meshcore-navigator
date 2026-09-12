"""Interactive Mesh Map & Packet Path Watcher Widget for PyQt6."""

from datetime import datetime
import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from meshcore_tray.config import AppConfig

import math
import time
from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot, QTimer, QPoint, QEvent
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QSplitter, QVBoxLayout, QWidget, QApplication
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
from meshcore_tray.core.models import MessageEnvelope, NodeContact, PacketPathInfo, is_valid_coordinate, is_plausible_rf_coordinate
from meshcore_tray.core.tropo_service import TropoForecastService
from meshcore_tray.core.adsb_service import ADSBService
from meshcore_tray.core.thunderstorm_service import ThunderstormService
from meshcore_tray.core.elevation_service import ElevationService
from meshcore_tray.core.viewshed_service import ViewshedService
from meshcore_tray.core.space_weather_service import SpaceWeatherService
from meshcore_tray.ui.elevation_profile_widget import ElevationProfileWidget

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
        vendor_css = f"<style>\n{_load_vendor_asset('leaflet.css')}\n</style>\n"
        vendor_js = (
            f"<script>{_load_vendor_asset('leaflet.js')}</script>\n"
            f"<script>{_load_vendor_asset('d3.v4.min.js')}</script>\n"
            f"<script>{_load_vendor_asset('d3-contour.min.js')}</script>\n"
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
        html, body, #map {
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
            background-color: #12151a;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #c9d1d9;
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
            --repeater-color: #FFA500;
            --repeater-hover-color: #FF6600;
            --companion-color: #10B981;
            --companion-hover-color: #34D399;
            --favorite-color: #FFD700;
            --dot-size-repeater: 3px;
            --dot-size-companion: 2.5px;
            --dot-size-favorite: 3.2px;
            --dot-size-local: 4px;
            --visualised-path-color: #FF00FF;
            --visualised-heading-color: #FF00FF;
            --orbital-repeater-color: #FFD335;
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

        .floating-route-panel {
            position: absolute;
            top: 12px;
            left: 12px;
            width: 290px;
            max-width: calc(100% - 24px);
            max-height: calc(100% - 24px);
            background: rgba(28, 28, 28, 0.96);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid #414143;
            border-radius: 8px;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.7);
            z-index: 1000;
            display: none;
            flex-direction: column;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        .floating-route-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: #1C1C1C;
            border-bottom: 1px solid #414143;
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

        /* Hover animations when mouse enters the 20px hitbox */
        .node-marker-wrap:hover .node-dot {
            transform: scale(3.0);
            opacity: 1.0 !important;
        }
        .node-marker-wrap:hover .node-dot-repeater {
            background: var(--repeater-hover-color) !important;
            box-shadow: 0 0 10px var(--repeater-hover-color);
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
                border-color: rgba(255, 255, 255, 0.95);
            }
            60% {
                transform: scale(1.6);
                opacity: 0.7;
                border-color: rgba(255, 255, 255, 0.7);
            }
            100% {
                transform: scale(2.4);
                opacity: 0.0;
                border-color: rgba(255, 255, 255, 0);
            }
        }
        .radar-ping-ring {
            position: absolute;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            border: 1.5px solid #FFFFFF;
            background: rgba(255, 255, 255, 0.12);
            animation: radar-ping-ring 1.0s ease-out infinite;
            pointer-events: none;
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
        }
        .leaflet-container a.leaflet-popup-close-button {
            color: #9CA3AF !important;
            padding: 6px 8px 0 0 !important;
        }
        .leaflet-container a.leaflet-popup-close-button:hover {
            color: #FFFFFF !important;
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
        /* Floating Path Mode Legend */
        .path-mode-legend {
            position: absolute;
            bottom: 24px;
            left: 12px;
            background: rgba(28, 28, 28, 0.94);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid #414143;
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 11px;
            color: #E5E7EB;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
            z-index: 1000;
            display: none;
            pointer-events: none;
            user-select: none;
        }
        .path-legend-item {
            display: inline-flex;
            align-items: center;
            margin-right: 12px;
            font-weight: 500;
        }
        .path-legend-item:last-child {
            margin-right: 0;
        }
        .path-legend-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
            display: inline-block;
        }
        /* Floating Tropo Legend Panel */
        .tropo-legend-panel {
            position: absolute;
            bottom: 24px;
            right: 12px;
            background: rgba(20, 22, 28, 0.94);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid #3B4252;
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 11px;
            color: #E5E7EB;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.7);
            z-index: 1000;
            display: none;
            user-select: none;
            min-width: 250px;
        }
        .tropo-legend-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
            gap: 8px;
        }
        .tropo-legend-title {
            font-weight: 700;
            font-size: 11px;
            color: #93C5FD;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .tropo-stepper {
            display: inline-flex;
            align-items: center;
            background: #1F2430;
            border: 1px solid #374151;
            border-radius: 4px;
            padding: 2px 4px;
            gap: 6px;
        }
        .tropo-step-btn {
            background: transparent;
            border: none;
            color: #9CA3AF;
            cursor: pointer;
            font-size: 10px;
            padding: 2px 5px;
            border-radius: 3px;
            line-height: 1;
        }
        .tropo-step-btn:hover {
            background: #374151;
            color: #FFFFFF;
        }
        .tropo-time-label {
            font-size: 10px;
            font-weight: 600;
            color: #F3F4F6;
            white-space: nowrap;
        }
        .tropo-legend-close {
            background: transparent;
            border: none;
            color: #9CA3AF;
            cursor: pointer;
            font-size: 14px;
            line-height: 1;
            padding: 2px 4px;
        }
        .tropo-legend-close:hover {
            color: #EF4444;
        }
        .tropo-scale-bar {
            display: flex;
            height: 10px;
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 4px;
            border: 1px solid #374151;
        }
        .tropo-scale-step {
            flex: 1;
            height: 100%;
        }
        .tropo-scale-labels {
            display: flex;
            justify-content: space-between;
            font-size: 9px;
            color: #9CA3AF;
            margin-bottom: 6px;
        }
        .tropo-legend-footer {
            font-size: 9px;
            color: #6B7280;
            text-align: right;
            border-top: 1px solid #282E3D;
            padding-top: 4px;
            margin-top: 4px;
        }
        .scope-filter-bar {
            position: absolute;
            top: 50px;
            left: 10px;
            z-index: 1000;
            background: rgba(34, 35, 39, 0.95);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid #414143;
            border-radius: 8px;
            padding: 6px 8px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
            user-select: none;
            width: 145px;
            max-height: calc(100% - 120px);
            overflow-y: auto;
            overflow-x: hidden;
        }
        .scope-filter-bar::-webkit-scrollbar {
            width: 4px;
        }
        .scope-filter-bar::-webkit-scrollbar-track {
            background: transparent;
        }
        .scope-filter-bar::-webkit-scrollbar-thumb {
            background: #414143;
            border-radius: 2px;
        }
        .scope-filter-bar::-webkit-scrollbar-thumb:hover {
            background: #60687A;
        }
        .scope-filter-title {
            font-size: 10px;
            font-weight: 700;
            color: #9CA3AF;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 2px 2px 4px 2px;
            border-bottom: 1px solid #414143;
            margin-bottom: 2px;
            white-space: nowrap;
        }
        .scope-pills-container {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        .scope-pill {
            background: #2B2F38;
            border: 1px solid #414143;
            color: #E5E7EB;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 7px;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.15s ease, border-color 0.15s ease;
            white-space: nowrap;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
        }
        .scope-pill:hover {
            background: #353A45;
            border-color: #60687A;
            color: #FFFFFF;
        }
        .scope-pill.active {
            background: #464C5A;
            border-color: #60687A;
            color: #FFFFFF;
            box-shadow: none;
        }
        .scope-pill-name {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .scope-pill-count {
            background: #1C1C1E;
            border: 1px solid #383A40;
            border-radius: 8px;
            padding: 1px 5px;
            font-size: 9px;
            color: #9CA3AF;
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
            background: rgba(255, 255, 255, 0.08);
            color: #D1D5DB;
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 4px;
            padding: 3px 7px;
            font-size: 10px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }
        .scope-btn:hover {
            background: rgba(255, 255, 255, 0.18);
            color: #FFFFFF;
            border-color: rgba(255, 255, 255, 0.35);
        }
        .scope-btn.exclude-scope-btn:hover {
            background: rgba(239, 68, 68, 0.25);
            color: #FCA5A5;
            border-color: rgba(239, 68, 68, 0.5);
        }
        .scope-btn.restore-scope-btn {
            background: rgba(16, 185, 129, 0.15);
            color: #6EE7B7;
            border-color: rgba(16, 185, 129, 0.3);
        }
        .scope-btn.restore-scope-btn:hover {
            background: rgba(16, 185, 129, 0.28);
            color: #A7F3D0;
            border-color: rgba(16, 185, 129, 0.6);
        }
        .scope-btn.apply-scope-btn {
            background: #2563EB;
            color: #FFFFFF;
            border-color: #3B82F6;
        }
        .scope-btn.apply-scope-btn:hover {
            background: #1D4ED8;
        }
        /* Floating Activity Heatmap Bar over Map */
        .activity-heatmap-bar {
            position: absolute;
            top: 50px;
            left: 12px;
            z-index: 1000;
            background: rgba(30, 31, 34, 0.92);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
            padding: 5px 10px;
            display: flex;
            align-items: center;
            gap: 8px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
            user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .activity-bar-title {
            font-size: 11px;
            font-weight: bold;
            color: #FBBF24;
            white-space: nowrap;
        }
        .activity-btn-group {
            display: flex;
            gap: 4px;
            background: rgba(0, 0, 0, 0.35);
            padding: 2px;
            border-radius: 5px;
            border: 1px solid #374151;
        }
        .activity-tf-btn {
            background: transparent;
            color: #9CA3AF;
            border: none;
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 10.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .activity-tf-btn:hover {
            color: #FFFFFF;
            background: rgba(255, 255, 255, 0.1);
        }
        .activity-tf-btn.active {
            background: #5865F2;
            color: #FFFFFF;
            font-weight: bold;
        }
        .activity-legend {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 9.5px;
            color: #9CA3AF;
            border-left: 1px solid #374151;
            padding-left: 8px;
        }
        .act-leg-item {
            display: flex;
            align-items: center;
            gap: 3px;
        }
        .act-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
        }
        .activity-bar-close {
            background: transparent;
            border: none;
            color: #9CA3AF;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            padding: 0 4px;
            line-height: 1;
        }
        .activity-bar-close:hover {
            color: #FFFFFF;
        }

        /* Floating Thunderstorm Panel */
        .thunderstorm-panel {
            position: absolute;
            top: 64px;
            right: 12px;
            z-index: 1000;
            background: rgba(26, 28, 35, 0.95);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid #38BDF8;
            border-radius: 8px;
            padding: 8px 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.6);
            user-select: none;
            width: 220px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        .thunderstorm-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #374151;
            padding-bottom: 4px;
        }
        .thunderstorm-title {
            font-size: 11px;
            font-weight: bold;
            color: #38BDF8;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .thunderstorm-sub {
            font-size: 9.5px;
            color: #9CA3AF;
            margin-top: 2px;
        }
        .thunderstorm-close-btn {
            background: transparent;
            border: none;
            color: #9CA3AF;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            line-height: 1;
        }
        .thunderstorm-close-btn:hover {
            color: #FFFFFF;
        }

        /* Floating Space Weather & Aurora Panel */
        .aurora-legend-panel {
            position: absolute;
            top: 70px;
            left: 55px;
            z-index: 1000;
            background: rgba(15, 23, 42, 0.94);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(168, 85, 247, 0.45);
            border-radius: 10px;
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.7), 0 0 15px rgba(168, 85, 247, 0.2);
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
            background: rgba(34, 35, 39, 0.95);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid #414143;
            border-radius: 8px;
            padding: 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
            user-select: none;
            width: 235px;
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
    </style>
</head>
<body>
    <div id="map"></div>
    <div id="map-loading-hud" class="map-loading-hud hidden">
        <span class="loading-spinner"></span>
        <span id="loading-hud-text">Initializing MeshCore Map &amp; RF Services...</span>
    </div>
    <div id="adsb-panel" class="adsb-panel" style="display: none;">
        <div class="adsb-header">
            <span class="adsb-title">✈️ ADS-B FLIGHTS <span id="adsb-count-badge" class="scope-pill-count">0</span></span>
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
        <div id="adsb-legend-container"></div>
    </div>
    <div id="scope-filter-bar" class="scope-filter-bar" style="display: none;">
        <div class="scope-filter-title">🌐 Scopes</div>
        <div id="scope-pills-container" class="scope-pills-container"></div>
    </div>
    <div id="orbital-tactical-tooltip" class="orbital-tactical-tooltip"></div>
    <div id="path-mode-legend" class="path-mode-legend">
        <span class="path-legend-item"><span class="path-legend-dot" style="background: #EF4444; box-shadow: 0 0 6px #EF4444;"></span>1-Byte Path</span>
        <span class="path-legend-item" title="Multibyte Path (2-Byte)"><span class="path-legend-dot" style="background: #00D2FF; box-shadow: 0 0 6px #00D2FF;"></span>2-Byte Path</span>
        <span class="path-legend-item" title="Multibyte Path (3-Byte)"><span class="path-legend-dot" style="background: #00FF7F; box-shadow: 0 0 6px #00FF7F;"></span>3-Byte Path</span>
        <span class="path-legend-item"><span class="path-legend-dot" style="background: #6B7280;"></span>Direct / Flood</span>
    </div>
    <div id="tropo-legend-panel" class="tropo-legend-panel">
        <div class="tropo-legend-header">
            <span class="tropo-legend-title">📡 Tropo Ducting Forecast</span>
            <div class="tropo-stepper">
                <button class="tropo-step-btn" onclick="if (window.pyBridge && window.pyBridge.on_tropo_stepped) window.pyBridge.on_tropo_stepped(-3)" title="Previous 3h">◀</button>
                <span id="tropo-time-label" class="tropo-time-label">--:-- UTC</span>
                <button class="tropo-step-btn" onclick="if (window.pyBridge && window.pyBridge.on_tropo_stepped) window.pyBridge.on_tropo_stepped(3)" title="Next 3h">▶</button>
            </div>
            <button class="tropo-legend-close" onclick="if (window.pyBridge && window.pyBridge.on_tropo_toggled) window.pyBridge.on_tropo_toggled(false)" title="Close Tropo Overlay">×</button>
        </div>
        <div class="tropo-scale-bar">
            <span class="tropo-scale-step" style="background: MediumOrchid;" title="Marginal (43-55)"></span>
            <span class="tropo-scale-step" style="background: purple;" title="Fair (55-60)"></span>
            <span class="tropo-scale-step" style="background: #10B981;" title="Moderate (60-70)"></span>
            <span class="tropo-scale-step" style="background: #FBBF24;" title="Good (70-90)"></span>
            <span class="tropo-scale-step" style="background: #EF4444;" title="Strong (90-110)"></span>
            <span class="tropo-scale-step" style="background: #FFFFFF;" title="Extreme (>110)"></span>
        </div>
        <div class="tropo-scale-labels">
            <span>Marginal</span>
            <span>Fair</span>
            <span>Moderate</span>
            <span>Good</span>
            <span>Strong</span>
            <span>Extreme</span>
        </div>
        <div class="tropo-legend-footer">
            <span>NOAA GFS Refractivity Index • F5LEN</span>
        </div>
    </div>
    <div id="visualised-floating-panel" class="floating-route-panel">
        <div class="floating-route-header" id="floating-route-drag-handle">
            <div class="floating-route-title">
                <span style="opacity: 0.7; font-size: 11px;">⠿</span>
                <span id="floating-route-title-text">📍 Visualised Route</span>
            </div>
            <button class="floating-route-close" id="floating-route-close-btn" title="Close Route (Esc)">×</button>
        </div>
        <div class="floating-route-body" id="floating-route-body-content"></div>
    </div>
    <!-- Floating Node Activity Heatmap Bar -->
    <div id="activity-heatmap-bar" class="activity-heatmap-bar" style="display: none;">
        <div class="activity-bar-title">🔥 Node Activity</div>
        <div class="activity-btn-group">
            <button id="act-btn-1h" class="activity-tf-btn active" onclick="setActivityTimeframe(1)">1 hour</button>
            <button id="act-btn-6h" class="activity-tf-btn" onclick="setActivityTimeframe(6)">6 hours</button>
            <button id="act-btn-24h" class="activity-tf-btn" onclick="setActivityTimeframe(24)">24 hours</button>
        </div>
        <div class="activity-legend">
            <span class="act-leg-item"><span class="act-dot" style="background: #10B981;"></span>Low</span>
            <span class="act-leg-item"><span class="act-dot" style="background: #FACC15;"></span>Med</span>
            <span class="act-leg-item"><span class="act-dot" style="background: #FB923C;"></span>High</span>
            <span class="act-leg-item"><span class="act-dot" style="background: #EF4444;"></span>V.High</span>
        </div>
        <button class="activity-bar-close" onclick="closeActivityHeatmap()" title="Close Activity Heatmap">×</button>
    </div>
    <!-- Floating Thunderstorm & Radar Panel -->
    <div id="thunderstorm-panel" class="thunderstorm-panel" style="display: none;">
        <div class="thunderstorm-header">
            <span class="thunderstorm-title">🌩️ THUNDERSTORMS <span id="strike-count-badge" class="scope-pill-count">0 strikes</span></span>
            <button class="thunderstorm-close-btn" onclick="if (window.pyBridge && window.pyBridge.on_thunderstorm_toggled) window.pyBridge.on_thunderstorm_toggled(false)" title="Close Thunderstorm Layer">×</button>
        </div>
        <div class="thunderstorm-sub">
            <span>📡 RainViewer Radar • ⚡ Blitzortung Live Feed</span>
        </div>
    </div>
    <!-- Floating Space Weather & Aurora Panel -->
    <div id="aurora-legend-panel" class="aurora-legend-panel" style="display: none;">
        <div class="aurora-header">
            <div class="aurora-title">
                <span>🌌</span>
                <span>SPACE WEATHER & AURORA</span>
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

        // Default to Canvas base layer initially
        canvasBaseLayer.addTo(map);
        var currentBaseLayerType = 'canvas';

        function setBaseMapLayer(type) {
            if (type === 'topo') {
                if (map.hasLayer(canvasBaseLayer)) map.removeLayer(canvasBaseLayer);
                if (!map.hasLayer(topoBaseLayer)) map.addLayer(topoBaseLayer);
                currentBaseLayerType = 'topo';
            } else {
                if (map.hasLayer(topoBaseLayer)) map.removeLayer(topoBaseLayer);
                if (!map.hasLayer(canvasBaseLayer)) map.addLayer(canvasBaseLayer);
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

        // Dedicated pane for Reference labels at zIndex 380 so town/city names stay legible above tropo colors
        map.createPane('labelsPane');
        map.getPane('labelsPane').style.zIndex = 380;
        map.getPane('labelsPane').style.pointerEvents = 'none';

        // Esri World Dark Gray Reference - Subtle town/city labels and borders
        L.tileLayer('https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 16,
            opacity: 0.85,
            updateWhenIdle: true,
            updateWhenZooming: false,
            keepBuffer: 2,
            pane: 'labelsPane'
        }).addTo(map);

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

        // Preview Path Layer (for on-hover flood path visualization)
        var previewPathLayer = null;

        var markers = {};
        var rfLinks = [];
        var activePaths = [];
        var pyBridge = null;
        var mapColors = {
            repeater: '#FFA500',
            companion: '#10B981',
            favorite: '#FFD700',
            watcherStart: '#FF5500',
            watcherEnd: '#DC2626',
            messageStart: '#10B981',
            messageEnd: '#047857',
            visualisedPath: '#FF00FF',
            visualisedHeading: '#FF00FF',
            orbitalRepeater: '#FFD335'
        };
        var visualisedPathLayer = null;
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
                (function(scopeKey) {
                    var sc = window._scopeData[scopeKey];
                    var cnt = (sc.nodes || []).length;
                    var pill = document.createElement('div');
                    var isActive = (window._activeScopeFilter === scopeKey);
                    pill.className = 'scope-pill' + (isActive ? ' active' : '');
                    if (isActive) {
                        pill.style.borderColor = sc.color || '#60687A';
                    }
                    var dotColor = sc.color || '#9CA3AF';
                    pill.innerHTML = '<span class="scope-pill-name"><span style="color:' + dotColor + '; font-weight:bold; margin-right:4px;">#</span>' + escapeHtml(scopeKey) + '</span><span class="scope-pill-count">' + cnt + '</span>';
                    pill.onclick = function() {
                        window._activeScopeFilter = (window._activeScopeFilter === scopeKey ? 'all' : scopeKey);
                        renderScopeOverlays();
                        renderScopeFilterPills();
                        refreshMarkersForScope();
                    };
                    container.appendChild(pill);
                })(k);
            }
        }

        function refreshMarkersForScope() {
            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
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

                var polyFillOp = isSelected ? 0.12 : 0.03;
                var polyWeight = isSelected ? 2 : 1;

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

        function applyNodeMarkerStyling(marker, node) {
            if (!marker || !node) return;
            var el = marker.getElement();
            if (!el) return;
            var dot = el.querySelector('.node-dot');
            if (!dot) return;

            var isLocal = !!node.is_local;
            var opacity = (!isLocal && freshnessFading) ? calculateFreshnessOpacity(node.last_seen) : 1.0;
            dot.style.opacity = opacity.toFixed(2);

            if (pathModesActive && !isLocal) {
                var pLen = (node.out_path_len !== undefined && node.out_path_len !== null) ? Number(node.out_path_len) : -1;
                var pMode = (node.out_path_hash_mode !== undefined && node.out_path_hash_mode !== null) ? Number(node.out_path_hash_mode) : -1;

                if (pMode >= 0) {
                    if (pMode === 0) {
                        // 1-Byte Path: Vibrant Red
                        dot.style.setProperty('background-color', '#EF4444', 'important');
                        dot.style.setProperty('border-color', '#B91C1C', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(239, 68, 68, 0.7)', 'important');
                    } else if (pMode === 1) {
                        // 2-Byte Path: Vibrant Sky Blue / Cyan
                        dot.style.setProperty('background-color', '#00D2FF', 'important');
                        dot.style.setProperty('border-color', '#0284C7', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(0, 210, 255, 0.7)', 'important');
                    } else {
                        // 3-Byte Path: Emerald Green
                        dot.style.setProperty('background-color', '#00FF7F', 'important');
                        dot.style.setProperty('border-color', '#047857', 'important');
                        dot.style.setProperty('box-shadow', '0 0 8px rgba(0, 255, 127, 0.7)', 'important');
                    }
                } else if (pLen > 0) {
                    // Fallback routed path
                    dot.style.setProperty('background-color', '#EF4444', 'important');
                    dot.style.setProperty('border-color', '#B91C1C', 'important');
                    dot.style.setProperty('box-shadow', '0 0 8px rgba(239, 68, 68, 0.7)', 'important');
                } else {
                    // Direct / Flood: Muted Slate Gray
                    dot.style.setProperty('background-color', '#6B7280', 'important');
                    dot.style.setProperty('border-color', '#4B5563', 'important');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                }
            } else if (window._scopeOverlaysActive && !isLocal && node.is_repeater) {
                var scMeta = (window._scopeNodeMap && (window._scopeNodeMap[node.node_id] || (node.alias && window._scopeNodeMap[node.alias]))) ? (window._scopeNodeMap[node.node_id] || window._scopeNodeMap[node.alias]) : null;
                if (scMeta) {
                    var isFiltered = (window._activeScopeFilter !== 'all' && window._activeScopeFilter !== scMeta.scope_name);
                    if (isFiltered) {
                        dot.style.opacity = '0.15';
                        dot.style.removeProperty('background-color');
                        dot.style.removeProperty('border-color');
                        dot.style.removeProperty('box-shadow');
                    } else {
                        var scCol = scMeta.color || '#00E5FF';
                        dot.style.opacity = '1.0';
                        dot.style.setProperty('background-color', scCol, 'important');
                        dot.style.setProperty('border-color', scCol, 'important');
                        dot.style.setProperty('box-shadow', '0 0 10px ' + scCol, 'important');
                    }
                } else {
                    dot.style.removeProperty('background-color');
                    dot.style.removeProperty('border-color');
                    dot.style.removeProperty('box-shadow');
                    dot.style.opacity = (window._activeScopeFilter !== 'all') ? '0.15' : '0.4';
                }
            } else if (activityHeatmapActive && !isLocal && node.is_repeater) {
                var cleanId = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                var cleanAlias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                var actMap = activityHeatmapData || {};
                var count = (actMap[cleanId] !== undefined) ? actMap[cleanId] : (actMap[cleanAlias] || 0);

                if (count <= 0) {
                    dot.style.opacity = '0.35';
                    dot.style.setProperty('background-color', '#4B5563', 'important');
                    dot.style.setProperty('border-color', '#374151', 'important');
                    dot.style.setProperty('box-shadow', 'none', 'important');
                } else {
                    dot.style.opacity = '1.0';
                    var actColor, actBorder, actShadow;
                    if (count <= 2) {
                        actColor = '#10B981'; // Green (Low)
                        actBorder = '#059669';
                        actShadow = '0 0 8px rgba(16, 185, 129, 0.7)';
                    } else if (count <= 5) {
                        actColor = '#FACC15'; // Yellow (Medium)
                        actBorder = '#CA8A04';
                        actShadow = '0 0 8px rgba(250, 204, 21, 0.7)';
                    } else if (count <= 10) {
                        actColor = '#FB923C'; // Orange (High)
                        actBorder = '#EA580C';
                        actShadow = '0 0 10px rgba(251, 146, 60, 0.8)';
                    } else {
                        actColor = '#EF4444'; // Red (Very High)
                        actBorder = '#DC2626';
                        actShadow = '0 0 12px rgba(239, 68, 68, 0.9)';
                    }
                    dot.style.setProperty('background-color', actColor, 'important');
                    dot.style.setProperty('border-color', actBorder, 'important');
                    dot.style.setProperty('box-shadow', actShadow, 'important');
                }
            } else {
                // Restore standard role styling
                dot.style.removeProperty('background-color');
                dot.style.removeProperty('border-color');
                dot.style.removeProperty('box-shadow');
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

        function setPathModesVisible(visible) {
            pathModesActive = !!visible;
            var legend = document.getElementById('path-mode-legend');
            if (legend) {
                legend.style.display = pathModesActive ? 'block' : 'none';
            }
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
            if (mapColors.dotSize) {
                var s = parseFloat(mapColors.dotSize);
                document.documentElement.style.setProperty('--dot-size-repeater', (s * 1.0).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-companion', (s * 0.85).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-favorite', (s * 1.1).toFixed(1) + 'px');
                document.documentElement.style.setProperty('--dot-size-local', (s * 1.25).toFixed(1) + 'px');
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
            var panel = document.getElementById('tropo-legend-panel');
            if (panel) panel.style.display = 'none';
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

                currentTropoLayer = L.geoJSON(geojson, {
                    style: getContourStyle,
                    pane: 'tropoPane'
                }).addTo(map);

                tropoActive = true;
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

        function buildNodePopupContent(node) {
            var isLocal = !!node.is_local;
            var isRep = !!node.is_repeater;
            var isFav = !!node.is_favorite;
            var favLabel = isFav ? 'Favorite ' : '';
            var typeLabel = isLocal ? 'Local Companion' : (isRep ? favLabel + 'Repeater' : favLabel + 'Companion Node');
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

            return '<div class="custom-popup">' +
                '<div class="popup-title">' + starHtml + (isRep ? '📡 ' : '👤 ') + safeAlias + '</div>' +
                '<div class="popup-stat">ID: ' + safeNodeId + ' (' + typeLabel + ')</div>' +
                '<div class="popup-stat">Last heard: ' + lastHeardStr + '</div>' +
                '<div class="popup-stat">Routing: ' + pathStr + '</div>' +
                snrHtml + rssiHtml +
                '<div class="popup-stat">Coords: ' + Number(node.lat).toFixed(4) + ', ' + Number(node.lon).toFixed(4) + '</div>' +
                scopeInfoHtml +
                dockedOrbitalsHtml +
                '<button class="popup-btn" data-node-id="' + encodeURIComponent(node.node_id) + '" onclick="onNodeClicked(decodeURIComponent(this.dataset.nodeId))">' + (isRep ? 'Open Repeater Console' : 'Direct Message') + '</button>' +
                '<div style="margin-top: 5px; display: flex; gap: 4px;">' +
                    '<button class="popup-btn" style="flex: 1; margin-top: 0; background-color: #1E293B; color: #38BDF8; border: 1px solid #38BDF8;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onProfileNodeClicked(this)">🏔️ Path Profile</button>' +
                    '<button class="popup-btn" style="flex: 1; margin-top: 0; background-color: #064E3B; color: #34D399; border: 1px solid #10B981;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onCalcViewshedClicked(this)">🟢 LOS Viewshed</button>' +
                '</div>' +
                '<button class="popup-btn" style="margin-top: 5px; background-color: #24262B; color: #38BDF8; border: 1px solid #38BDF8;" data-nid="' + encodeURIComponent(node.node_id) + '" data-alias="' + encodeURIComponent(node.alias || node.node_id || '') + '" data-lat="' + Number(node.lat) + '" data-lon="' + Number(node.lon) + '" onclick="onTrackAdsbClicked(this)">✈️ Track ADS-B Around Node</button>' +
                '</div>';
        }

        function setNodes(nodesList) {
            for (var id in markers) {
                map.removeLayer(markers[id]);
            }
            markers = {};
            var latLngs = [];

            nodesList.forEach(function(node) {
                try {
                    if (!node || typeof node.lat !== 'number' || typeof node.lon !== 'number' || isNaN(node.lat) || isNaN(node.lon)) return;
                    if (node.lat < -85.0 || node.lat > 85.0 || node.lon < -180.0 || node.lon > 180.0) return;

                    var isLocal = !!node.is_local;
                    var isRep = !!node.is_repeater;
                    var isFav = !!node.is_favorite;

                    var dotClass = 'node-dot ';
                    if (isLocal) {
                        dotClass += 'node-dot-local';
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

                    // Generous 20px hit area around the dot for effortless mouse hovering & clicking
                    var icon = L.divIcon({
                        className: 'node-marker-wrap',
                        html: '<div class="' + dotClass + '"' + styleAttr + '></div>',
                        iconSize: [20, 20],
                        iconAnchor: [10, 10]
                    });

                    var marker = L.marker([node.lat, node.lon], { icon: icon }).addTo(map);
                    marker._nodeData = node;
                    applyNodeMarkerStyling(marker, node);

                    // Hover tooltip shows node name, last heard, and path info
                    var starPrefix = isFav ? '⭐ ' : '';
                    var lastHeardStr = isLocal ? 'Active now (Local node)' : formatLastHeard(node.last_seen);
                    var pathStr = formatPathInfo(node);
                    var rawAlias = node.alias || node.node_id || 'Node';
                    var safeAlias = escapeHtml(rawAlias);

                    var actInfo = '';
                    if (activityHeatmapActive && isRep) {
                        var cleanId = (node.node_id || '').toLowerCase().replace(/^[!@]+/, '');
                        var cleanAlias = (node.alias || '').toLowerCase().replace(/^[!@]+/, '');
                        var cAct = (activityHeatmapData[cleanId] !== undefined) ? activityHeatmapData[cleanId] : (activityHeatmapData[cleanAlias] || 0);
                        var actLabel = cAct <= 0 ? 'Inactive' : (cAct <= 2 ? 'Low' : (cAct <= 5 ? 'Medium' : (cAct <= 10 ? 'High' : 'Very High')));
                        var actBadgeCol = cAct <= 0 ? '#9CA3AF' : (cAct <= 2 ? '#10B981' : (cAct <= 5 ? '#FACC15' : (cAct <= 10 ? '#FB923C' : '#EF4444')));
                        actInfo = '<div style="font-size: 10px; color: ' + actBadgeCol + '; font-weight: bold; margin-top: 2px;">🔥 Activity (' + activityTimeframeHours + 'h): ' + cAct + ' msgs (' + actLabel + ')</div>';
                    }

                    var tipContent = '<div style="text-align: center; line-height: 1.35;">' +
                        '<div>' + starPrefix + '<b>' + safeAlias + '</b></div>' +
                        '<div style="font-size: 10px; color: #9CA3AF; margin-top: 2px;">Last heard: ' + lastHeardStr + '</div>' +
                        '<div style="font-size: 10px; margin-top: 2px;">' + pathStr + '</div>' +
                        actInfo +
                        '</div>';
                    marker.bindTooltip(tipContent, {
                        direction: 'top',
                        offset: [0, -6],
                        className: 'node-tooltip'
                    });

                    // Dynamic popup content including standard repeater info and docked orbital nodes list
                    marker.bindPopup(function() {
                        return buildNodePopupContent(node);
                    }, { className: 'custom-popup', maxWidth: 320 });

                    if (isRep) {
                        marker.on('click', function(ev) {
                            if (window._companionOrbitalsActive) {
                                var dMap = window._dockedCompanionsData || {};
                                var dList = dMap[node.node_id] || dMap[node.alias];
                                if (!dList && node.alias) {
                                    dList = dMap['@' + node.alias] || dMap[node.alias.replace(/^@/, '')];
                                }
                                if (dList && dList.length > 0) {
                                    // When zoomed out, clicking the gold ring zooms in to the level where orbitals show
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
                    latLngs.push([node.lat, node.lon]);
                } catch (nodeErr) {
                    console.error('Error rendering node marker:', node, nodeErr);
                }
            });

            // Only fit bounds on first-ever load if user has not set/restored a custom viewport
            if (!window._initialViewSet && latLngs.length > 0) {
                map.fitBounds(latLngs, { padding: [25, 25], maxZoom: 12 });
                window._initialViewSet = true;
            }

            renderCompanionOrbitals();
        }

        function setCompanionOrbitalsVisible(active, dockedData) {
            window._companionOrbitalsActive = !!active;
            if (dockedData !== undefined && dockedData !== null) {
                window._dockedCompanionsData = dockedData;
            }
            renderCompanionOrbitals();
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

        function pulseOriginNode(coord, senderId, senderName) {
            if (!coord || coord.length < 2) return;

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

            // Add sleek tactical radar blip + floating sender badge
            var blipIcon = L.divIcon({
                className: 'radar-blip-wrap',
                html: '<div class="radar-blip-container">' +
                          '<div class="radar-ping-ring"></div>' +
                          '<div class="radar-center-dot"></div>' +
                          '<div class="radar-sender-badge">' + cleanName + '</div>' +
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

        function drawPacketPath(coords, meta) {
            if (!coords || coords.length < 2) return;

            // Trigger tactical radar blip and sender badge at origin for 3 seconds
            pulseOriginNode(coords[0], meta ? meta.sender_id : null, meta ? meta.sender_name : null);

            // Group sub-segments for this path
            var pathGroup = L.layerGroup().addTo(map);

            // Interpolate path into smooth sub-steps from origin to destination
            var totalSubSteps = Math.max(12, (coords.length - 1) * 6);
            var flatPoints = [];

            for (var seg = 0; seg < coords.length - 1; seg++) {
                var p0 = coords[seg];
                var p1 = coords[seg + 1];
                var subCount = Math.max(3, Math.round(totalSubSteps / (coords.length - 1)));
                for (var s = 0; s < subCount; s++) {
                    var t = s / subCount;
                    var lat = p0[0] + (p1[0] - p0[0]) * t;
                    var lon = p0[1] + (p1[1] - p0[1]) * t;
                    flatPoints.push([lat, lon]);
                }
            }
            flatPoints.push(coords[coords.length - 1]);

            // Draw thin lines starting strong orange (#FF5500), fading out to red (#DC2626)
            // Or if green (incoming message route):
            // Starting radiant bright green (#10B981 / rgb(16, 185, 129)), fading towards deep emerald (#047857 / rgb(4, 120, 87))
            var isGreen = meta && (meta.color === 'green' || meta.is_incoming);
            var startCol = isGreen ? mapColors.messageStart : mapColors.watcherStart;
            var endCol = isGreen ? mapColors.messageEnd : mapColors.watcherEnd;
            var c0 = hexToRgb(startCol);
            var c1 = hexToRgb(endCol);

            var subPolylines = [];
            for (var i = 0; i < flatPoints.length - 1; i++) {
                var ratio = i / Math.max(1, flatPoints.length - 2);
                var r = Math.round(c0[0] + (c1[0] - c0[0]) * ratio);
                var g = Math.round(c0[1] + (c1[1] - c0[1]) * ratio);
                var b = Math.round(c0[2] + (c1[2] - c0[2]) * ratio);
                var baseOpacity = 0.95 - ratio * 0.55; // 0.95 at origin down to 0.40 at destination

                var color = 'rgb(' + r + ',' + g + ',' + b + ')';
                var poly = L.polyline([flatPoints[i], flatPoints[i + 1]], {
                    color: color,
                    weight: 2,
                    opacity: baseOpacity
                }).addTo(pathGroup);
                poly._baseOpacity = baseOpacity;
                subPolylines.push(poly);
            }

            var infoText = '';
            if (isGreen) {
                var chanStr = meta.channel ? ' on #' + meta.channel.replace(/^#/, '') : '';
                var senderStr = meta.sender_name ? ' from ' + meta.sender_name : '';
                infoText = '📥 Incoming Route: ' + (meta.hops || coords.length - 1) + ' hop(s)' + senderStr + chanStr;
            } else {
                infoText = 'Watcher: ' + (meta.hops || coords.length - 1) + ' hop(s) ' + (meta.route_type || '');
            }
            pathGroup.bindTooltip(infoText, { sticky: true });
            activePaths.push(pathGroup);

            // Hold on screen for 10 seconds, then smoothly fade out over 1 second (1000ms)
            setTimeout(function() {
                var fadeStart = Date.now();
                var fadeDuration = 1000;

                var fadeInterval = setInterval(function() {
                    var elapsed = Date.now() - fadeStart;
                    var progress = Math.min(1.0, elapsed / fadeDuration);
                    var fadeMultiplier = 1.0 - progress;

                    for (var j = 0; j < subPolylines.length; j++) {
                        var p = subPolylines[j];
                        var currentOp = (p._baseOpacity || 0.8) * fadeMultiplier;
                        p.setStyle({ opacity: Math.max(0, currentOp) });
                    }

                    if (progress >= 1.0) {
                        clearInterval(fadeInterval);
                        try {
                            map.removeLayer(pathGroup);
                            var idx = activePaths.indexOf(pathGroup);
                            if (idx !== -1) activePaths.splice(idx, 1);
                        } catch(e) {}
                    }
                }, 50);
            }, 10000);
        }

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
                        '<div><span style="color:#38BDF8;">SNR:</span> ' + n.snr_str + '</div>' +
                        '<div style="font-size:10px; color:#9CA3AF; font-weight:normal;">' + n.time_str + '</div>' +
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

            // 3. Fit bounds so all neighbours and repeater are visible
            if (allBounds.length > 0) {
                map.fitBounds(allBounds, { padding: [60, 60], maxZoom: 13 });
            }

            // 4. Floating Banner
            var banner = document.getElementById('neighbors-overlay-banner');
            if (banner) banner.remove();

            var repTitle = rep ? (rep.alias || rep.id) : 'Repeater';
            var bannerHtml = '<div id="neighbors-overlay-banner" style="position:absolute; top:48px; left:12px; z-index:1000; background:#222327; border:1px solid #414143; border-radius:6px; padding:6px 12px; color:#E5E7EB; font-size:12px; font-weight:bold; display:flex; align-items:center; gap:10px; box-shadow:0 4px 12px rgba(0,0,0,0.6);">' +
                '<span>🌐 Neighbours: <span style="color:#38BDF8;">@' + repTitle + '</span> (' + plottedCount + ' mapped with GPS)</span>' +
                '<button onclick="clearRepeaterNeighbors()" style="background:#2B2F38; color:#EF4444; border:1px solid #414143; border-radius:4px; padding:2px 8px; font-size:11px; cursor:pointer; font-weight:bold;">✖ Clear</button>' +
                '</div>';
            document.body.insertAdjacentHTML('beforeend', bannerHtml);
        }

        function initDraggablePanel() {
            var panel = document.getElementById('visualised-floating-panel');
            var handle = document.getElementById('floating-route-drag-handle');
            var closeBtn = document.getElementById('floating-route-close-btn');
            if (!panel || !handle) return;

            L.DomEvent.disableClickPropagation(panel);
            L.DomEvent.disableScrollPropagation(panel);

            if (closeBtn) {
                closeBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    closeVisualisedPanel();
                });
            }

            var isDragging = false;
            var startX = 0, startY = 0;
            var origLeft = 0, origTop = 0;

            handle.addEventListener('mousedown', function(e) {
                if (e.target && e.target.closest && e.target.closest('.floating-route-close')) return;
                isDragging = true;
                startX = e.clientX;
                startY = e.clientY;
                origLeft = panel.offsetLeft;
                origTop = panel.offsetTop;
                document.body.style.userSelect = 'none';
                e.preventDefault();
            });

            document.addEventListener('mousemove', function(e) {
                if (!isDragging) return;
                var dx = e.clientX - startX;
                var dy = e.clientY - startY;
                var mapEl = document.getElementById('map');
                var maxW = (mapEl ? mapEl.clientWidth : window.innerWidth) - panel.offsetWidth;
                var maxH = (mapEl ? mapEl.clientHeight : window.innerHeight) - panel.offsetHeight;
                var newLeft = Math.max(0, Math.min(Math.max(0, maxW), origLeft + dx));
                var newTop = Math.max(0, Math.min(Math.max(0, maxH), origTop + dy));
                panel.style.left = newLeft + 'px';
                panel.style.top = newTop + 'px';
                panel.style.right = 'auto';
                panel.style.bottom = 'auto';
            });

            document.addEventListener('mouseup', function() {
                if (isDragging) {
                    isDragging = false;
                    document.body.style.userSelect = '';
                }
            });
        }
        setTimeout(initDraggablePanel, 100);

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

                // Broad transparent hit-area (28px wide corridor) for effortless hovering anywhere near the line
                var hitPoly = L.polyline(seg.coords, {
                    weight: 28,
                    opacity: 0.0001,
                    color: '#000000',
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: true
                }).addTo(visualisedPathLayer);

                // Outer subtle glow / contrast halo (white backing for black line on dark map, matching color otherwise)
                var isBlack = (segCol && (segCol.toLowerCase() === '#000000' || segCol.toLowerCase() === '#000' || segCol.toLowerCase() === 'black'));
                var glowCol = isBlack ? '#FFFFFF' : segCol;
                var glowOpacity = isPhantom ? 0.35 : (isNoGps ? 0.35 : (isUnk ? 0.35 : 0.25));
                var glowWidth = isBlack ? 5.5 : 7;

                var glowPoly = L.polyline(seg.coords, {
                    color: glowCol,
                    weight: glowWidth,
                    opacity: glowOpacity,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(visualisedPathLayer);

                // Dotted animated line flowing in packet transmission direction (period = 20px)
                var poly = L.polyline(seg.coords, {
                    color: segCol,
                    weight: 3.5,
                    dashArray: (isUnk || isNoGps || isPhantom) ? '6, 14' : '8, 12',
                    className: 'animated-path-flow',
                    opacity: 0.95,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false
                }).addTo(visualisedPathLayer);

                var tipText = '';
                if (isPhantom) {
                    var repNameInfo = (seg.missing_names && seg.missing_names.length > 0) ? (' (' + seg.missing_names.join(', ') + ')') : '';
                    tipText = '👻 Path connects through phantom node' + repNameInfo + ': ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
                } else if (isNoGps) {
                    var repNameInfo = (seg.missing_names && seg.missing_names.length > 0) ? (' (' + seg.missing_names.join(', ') + ')') : '';
                    tipText = '⚠️ Repeater in route has unknown location' + repNameInfo + ': ' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
                } else if (isUnk) {
                    tipText = '⚠️ Unknown path: connects through unknown repeater (' + (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node') + ')';
                } else {
                    tipText = (seg.from_name || 'Node') + ' ➔ ' + (seg.to_name || 'Node');
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
        };

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

            for (var id in markers) {
                var m = markers[id];
                if (m && m._nodeData) {
                    applyNodeMarkerStyling(m, m._nodeData);
                }
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

        var wsServerIdx = 0;
        var blitzServers = ['wss://ws7.blitzortung.org', 'wss://ws1.blitzortung.org', 'wss://ws8.blitzortung.org'];

        function setThunderstormVisible(visible, radarMeta) {
            thunderstormActive = !!visible;
            var panel = document.getElementById('thunderstorm-panel');
            if (panel) {
                panel.style.display = thunderstormActive ? 'flex' : 'none';
            }

            if (thunderstormActive) {
                if (radarMeta && radarMeta.host && radarMeta.path) {
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
        }
        window.setThunderstormVisible = setThunderstormVisible;

        function updateThunderstormRadar(radarMeta) {
            if (!radarMeta || !radarMeta.host || !radarMeta.path) return;
            if (rainViewerRadarLayer) {
                map.removeLayer(rainViewerRadarLayer);
                rainViewerRadarLayer = null;
            }
            if (!thunderstormActive) return;
            var tileUrl = radarMeta.host + radarMeta.path + '/256/{z}/{x}/{y}/2/1_1.png';
            rainViewerRadarLayer = L.tileLayer(tileUrl, {
                opacity: 0.65,
                maxNativeZoom: 7,
                maxZoom: 19,
                tileSize: 256,
                updateWhenIdle: true,
                pane: 'thunderstormPane'
            }).addTo(map);
        }
        window.updateThunderstormRadar = updateThunderstormRadar;

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
            lightningStrikesList.push({ marker: marker, ts: now });

            var badge = document.getElementById('strike-count-badge');
            if (badge) badge.innerText = lightningStrikesList.length + ' strikes';

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
            var panel = document.getElementById('aurora-legend-panel');
            if (panel) panel.style.display = 'none';
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

                } catch(err) {
                    console.error("Failed to contour aurora grid:", err);
                }
            }
        };

        function previewPacketPath(coords, meta) {
            clearPreviewPacketPath();
            if (!coords || coords.length < 2) return;
            var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
            previewPathLayer = L.polyline(latlngs, {
                color: '#C084FC',
                weight: 4,
                opacity: 0.95,
                dashArray: '6, 6',
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
            if (!marker) return;
            var plane = currentAircraftData[lowHex];
            if (!plane) return;

            if (!window._aircraftPhotos) window._aircraftPhotos = {};
            if (!window._aircraftPhotos[lowHex] && window.pyBridge && window.pyBridge.request_aircraft_photo) {
                window._aircraftPhotos[lowHex] = { loading: true };
                window.pyBridge.request_aircraft_photo(lowHex.toUpperCase());
            }

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

            var cluster = getOverlappingAircraft(plane, 26);
            var tipHtml = buildAircraftTooltipHtml(plane, cluster);
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

            for (var i = 0; i < aircraft.length; i++) {
                var plane = aircraft[i];
                if (typeof plane.lat !== 'number' || typeof plane.lon !== 'number') continue;
                var hex = (plane.hex || '').toLowerCase();
                if (!hex) continue;
                currentHexes.add(hex);

                var iconData = createAirplaneIcon(plane);

                // Update flight trajectory history
                if (!aircraftHistory[hex]) {
                    aircraftHistory[hex] = [{ lat: plane.lat, lon: plane.lon, color: iconData.color, ts: Date.now() }];
                } else {
                    var hist = aircraftHistory[hex];
                    var lastPt = hist[hist.length - 1];
                    var distMoved = Math.abs(plane.lat - lastPt.lat) + Math.abs(plane.lon - lastPt.lon);
                    if (distMoved > 0.0001) {
                        hist.push({ lat: plane.lat, lon: plane.lon, color: iconData.color, ts: Date.now() });
                        if (hist.length > 30) hist.shift();
                    } else {
                        lastPt.color = iconData.color;
                        lastPt.ts = Date.now();
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
                            opacity: 0.65,
                            lineCap: 'round',
                            lineJoin: 'round',
                            pane: 'adsbTrailsPane',
                            interactive: false
                        });
                        adsbTrailsGroup.addLayer(trailLine);
                        aircraftTrails[hex] = trailLine;
                    } else {
                        trailLine.setLatLngs(latlngs);
                        if (trailLine.options.color !== iconData.color) {
                            trailLine.setStyle({ color: iconData.color });
                        }
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
                        pane: 'adsbMarkersPane'
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
                    }
                } else {
                    marker._planeData = plane;
                    marker.setLatLng([plane.lat, plane.lon]);
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

    @pyqtSlot(float, float, int, int)
    def on_map_context_menu(self, lat: float, lon: float, x: int, y: int):
        self.map_context_menu_signal.emit(lat, lon, x, y)

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

    @pyqtSlot(str, str, bool)
    def on_phantom_node_toggled(self, node_id: str, alias: str, is_phantom: bool):
        self.phantom_node_toggled_signal.emit(node_id, alias, is_phantom)

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

    adsb_color_mode_changed_signal = pyqtSignal(str)
    request_aircraft_photo_signal = pyqtSignal(str)

    @pyqtSlot(str)
    def on_adsb_color_mode_changed(self, mode: str):
        self.adsb_color_mode_changed_signal.emit(mode)

    @pyqtSlot(str)
    def request_aircraft_photo(self, hex_code: str):
        self.request_aircraft_photo_signal.emit(hex_code)

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


class MeshMapWidget(QWidget):
    """Interactive Mesh Map & Packet Path Watcher widget displayed between Chat and Pixoo mirror."""

    node_selected = pyqtSignal(str)
    map_ready = pyqtSignal()

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
        self.show_companion_orbitals = getattr(self.config.meshcore, "map_show_companion_orbitals", False) if self.config else False
        self._page_ready = not WEBENGINE_AVAILABLE
        self._last_traced_path_info = None
        self._pending_visualise_msg = None
        self._pending_neighbors_payload = None

        self.tropo_service = TropoForecastService(parent=self)
        self.tropo_service.forecast_ready.connect(self._on_tropo_forecast_ready)
        self.tropo_service.forecast_loading.connect(self._on_tropo_forecast_loading)
        self.tropo_service.forecast_error.connect(self._on_tropo_forecast_error)

        self.show_adsb = getattr(self.config.meshcore, "map_show_adsb", False) if self.config else False
        self.adsb_service = ADSBService(parent=self)
        self.adsb_service.flights_updated.connect(self._on_adsb_flights_updated)

        self.activity_heatmap_active = False
        self.activity_timeframe_hours = 1
        self.show_thunderstorm = getattr(self.config.meshcore, "map_show_thunderstorm", False) if self.config else False
        self.thunderstorm_service = ThunderstormService(parent=self)
        self.thunderstorm_service.radar_updated.connect(self._on_thunderstorm_radar_updated)

        self.show_space_weather = getattr(self.config.meshcore, "map_show_space_weather", False) if self.config else False
        self.space_weather_service = SpaceWeatherService(parent=self)
        self.space_weather_service.weather_updated.connect(self._on_space_weather_updated)
        self.space_weather_service.weather_loading.connect(self._on_space_weather_loading)
        self.space_weather_service.weather_error.connect(self._on_space_weather_error)

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
        self._do_refresh_map_data()
        if not WEBENGINE_AVAILABLE:
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
            self.bridge.repeater_neighbors_cleared_signal.connect(self._on_repeater_neighbors_cleared)
            self.bridge.tropo_stepped_signal.connect(self._on_bridge_tropo_stepped)
            self.bridge.tropo_toggled_signal.connect(self._on_bridge_tropo_toggled)
            self.bridge.node_scope_changed_signal.connect(self._on_node_scope_changed)
            self.bridge.set_adsb_target_signal.connect(self._on_bridge_set_adsb_target)
            self.bridge.adsb_toggled_signal.connect(self._on_bridge_adsb_toggled)
            self.bridge.reset_adsb_target_signal.connect(self._on_bridge_reset_adsb_target)
            self.bridge.activity_timeframe_changed_signal.connect(self._on_bridge_activity_timeframe_changed)
            self.bridge.activity_heatmap_toggled_signal.connect(self._on_bridge_activity_heatmap_toggled)
            self.bridge.thunderstorm_toggled_signal.connect(self._on_bridge_thunderstorm_toggled)
            self.bridge.space_weather_toggled_signal.connect(self.set_space_weather)
            self.bridge.space_weather_refresh_signal.connect(lambda: self.space_weather_service.fetch_weather(force=True))
            self.bridge.space_weather_opacity_signal.connect(self._on_space_weather_opacity_changed)
            self.bridge.map_context_menu_signal.connect(
                lambda lat, lon, x, y: QTimer.singleShot(0, lambda: self._show_map_context_menu(lat, lon, x, y))
            )
            self.bridge.adsb_color_mode_changed_signal.connect(self._on_bridge_adsb_color_mode_changed)
            self.bridge.request_aircraft_photo_signal.connect(self._on_bridge_request_aircraft_photo)
            self.bridge.p2p_path_selected_signal.connect(self._on_p2p_path_selected)
            self.bridge.profile_node_signal.connect(self._on_profile_node_requested)
            self.bridge.calc_node_viewshed_signal.connect(self._on_calc_node_viewshed_requested)
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

            # Floating In-Overlay Controls in Top-Left Corner of Map
            self.floating_controls = QFrame(self.web_view)
            self.floating_controls.setStyleSheet("""
                QFrame {
                    background-color: rgba(30, 31, 34, 0.90);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 6px;
                }
                QPushButton {
                    background-color: transparent;
                    color: #F2F3F5;
                    border: none;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #404249;
                    color: #FFFFFF;
                }
                QPushButton:checked {
                    background-color: #38BDF8;
                    color: #0F172A;
                }
                QPushButton:checked:hover {
                    background-color: #7DD3FC;
                    color: #0F172A;
                }
            """)
            fl_layout = QHBoxLayout(self.floating_controls)
            fl_layout.setContentsMargins(4, 4, 4, 4)
            fl_layout.setSpacing(4)

            self.btn_center = QPushButton("📍 Re-center")
            self.btn_center.setToolTip("Center map on active nodes")
            self.btn_center.clicked.connect(self._on_center_clicked)
            fl_layout.addWidget(self.btn_center)

            self.btn_reset_layers = QPushButton("🧹 Reset Layers")
            self.btn_reset_layers.setToolTip("Reset map layers, clear paths, and restore default view")
            self.btn_reset_layers.clicked.connect(self.reset_map_layers)
            fl_layout.addWidget(self.btn_reset_layers)

            self.btn_age_fade = QPushButton("⏳ Age Fade")
            self.btn_age_fade.setCheckable(True)
            self.btn_age_fade.setToolTip("Toggle Node Age Fading (dim inactive nodes on map)")
            fade_init = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
            self.btn_age_fade.setChecked(fade_init)
            self.btn_age_fade.clicked.connect(self._on_floating_age_fade_clicked)
            fl_layout.addWidget(self.btn_age_fade)

            self._current_base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
            self.btn_base_map = QPushButton("🗺️ Canvas" if self._current_base_layer == "topo" else "🏔️ Topo")
            self.btn_base_map.setToolTip("Switch Base Map Layer: Dark Canvas vs Dark Topographic (OpenTopoMap)")
            self.btn_base_map.clicked.connect(self._on_toggle_base_map_clicked)
            fl_layout.addWidget(self.btn_base_map)

            self.floating_controls.adjustSize()
            self.floating_controls.move(10, 10)
            self.floating_controls.show()

            # Floating Line-of-Sight & Topographic Profile Controls at Top of Map
            self.los_controls = QFrame(self.web_view)
            self.los_controls.setStyleSheet("""
                QFrame {
                    background-color: rgba(24, 27, 32, 0.94);
                    border: 1px solid rgba(16, 185, 129, 0.4);
                    border-radius: 6px;
                }
                QLabel {
                    color: #10B981;
                    font-size: 11px;
                    font-weight: 700;
                    background: transparent;
                    border: none;
                }
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #D1D5DB;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: rgba(16, 185, 129, 0.2);
                    color: #FFFFFF;
                    border-color: #10B981;
                }
                QPushButton:checked {
                    background-color: #10B981;
                    color: #064E3B;
                    font-weight: 700;
                    border-color: #34D399;
                }
                QComboBox {
                    background-color: #1F242D;
                    color: #F3F4F6;
                    border: 1px solid #374151;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 14px;
                }
                QComboBox QAbstractItemView {
                    background-color: #1E2024;
                    color: #F3F4F6;
                    selection-background-color: #10B981;
                    selection-color: #064E3B;
                    border: 1px solid #374151;
                }
            """)
            los_layout = QHBoxLayout(self.los_controls)
            los_layout.setContentsMargins(5, 4, 5, 4)
            los_layout.setSpacing(5)

            lbl_los = QLabel("📡 LOS:")
            lbl_los.setToolTip("Line-of-Sight & RF Propagation Coverage Tools")
            los_layout.addWidget(lbl_los)

            self.btn_toggle_los = QPushButton("🟢 Viewshed")
            self.btn_toggle_los.setCheckable(True)
            self.btn_toggle_los.setToolTip("Toggle 360° Terrain-Aware RF Line-of-Sight Viewshed Coverage")
            self.btn_toggle_los.clicked.connect(self._on_los_toggle_clicked)
            los_layout.addWidget(self.btn_toggle_los)

            self.btn_los_ground = QPushButton("Ground (2m)")
            self.btn_los_ground.setCheckable(True)
            self.btn_los_ground.setToolTip("Handheld / mobile antenna (2m AGL)")
            self.btn_los_rooftop = QPushButton("Rooftop (8m)")
            self.btn_los_rooftop.setCheckable(True)
            self.btn_los_rooftop.setChecked(True)
            self.btn_los_rooftop.setToolTip("Residential chimney / eaves mount (8m AGL)")
            self.btn_los_mast = QPushButton("Mast (15m)")
            self.btn_los_mast.setCheckable(True)
            self.btn_los_mast.setToolTip("High mast / tower mount (15m AGL)")

            self.height_group = QButtonGroup(self)
            self.height_group.addButton(self.btn_los_ground, 2)
            self.height_group.addButton(self.btn_los_rooftop, 8)
            self.height_group.addButton(self.btn_los_mast, 15)
            self.height_group.idClicked.connect(self._on_los_height_button_clicked)

            los_layout.addWidget(self.btn_los_ground)
            los_layout.addWidget(self.btn_los_rooftop)
            los_layout.addWidget(self.btn_los_mast)

            lbl_r = QLabel("Radius:")
            lbl_r.setStyleSheet("color: #9CA3AF; font-weight: normal;")
            los_layout.addWidget(lbl_r)

            self.combo_los_radius = QComboBox()
            self.combo_los_radius.addItems(["15 km", "25 km", "50 km"])
            self.combo_los_radius.setCurrentText("25 km")
            self.combo_los_radius.currentTextChanged.connect(self._on_los_radius_changed)
            los_layout.addWidget(self.combo_los_radius)

            self.btn_profile_path = QPushButton("🏔️ Profile Path")
            self.btn_profile_path.setCheckable(True)
            self.btn_profile_path.setToolTip("Click two points on the map to profile topographic elevation & 1st Fresnel zone clearance")
            self.btn_profile_path.clicked.connect(self._on_profile_path_toggle_clicked)
            los_layout.addWidget(self.btn_profile_path)

            self.btn_clear_los = QPushButton("✕")
            self.btn_clear_los.setToolTip("Clear Viewshed and Path Profile overlays")
            self.btn_clear_los.setFixedWidth(24)
            self.btn_clear_los.clicked.connect(self._on_clear_los_clicked)
            los_layout.addWidget(self.btn_clear_los)

            self.los_controls.adjustSize()
            show_los = getattr(self.config, "map_show_rf_los", False) if self.config else False
            self.los_controls.setVisible(show_los)
        else:
            self._current_base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
            self.btn_base_map = QPushButton("🗺️ Canvas" if self._current_base_layer == "topo" else "🏔️ Topo")
            self.btn_age_fade = QPushButton("⏳ Age Fade")
            self.btn_age_fade.setCheckable(True)
            fade_init = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
            self.btn_age_fade.setChecked(fade_init)
            self.btn_age_fade.clicked.connect(self._on_floating_age_fade_clicked)

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

        self.map_splitter.setStretchFactor(0, 4)
        self.map_splitter.setStretchFactor(1, 1)
        self.map_splitter.setChildrenCollapsible(False)

        layout.addWidget(self.map_splitter, 1)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_floating_controls()
        self._schedule_map_invalidate(150)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Window maximized, restored, or fullscreened.
            # Allow OS window manager, compositor, and Chromium swapchain 250ms to settle
            # before requesting Leaflet viewport invalidation, preventing buffer thrashing/hangs.
            self._reposition_floating_controls()
            self._schedule_map_invalidate(delay_ms=250)

    def _reposition_floating_controls(self):
        if hasattr(self, "floating_controls"):
            self.floating_controls.move(10, 10)
            self.floating_controls.raise_()
        if hasattr(self, "los_controls") and hasattr(self, "floating_controls") and hasattr(self, "web_view"):
            fl_w = self.floating_controls.width()
            los_w = self.los_controls.width()
            web_w = self.web_view.width()
            if fl_w + los_w + 30 <= web_w:
                self.los_controls.move(fl_w + 20, 10)
            else:
                self.los_controls.move(10, self.floating_controls.height() + 16)
            self.los_controls.raise_()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._connect_screen_listener()
        self._schedule_map_invalidate(180)

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_screen_listener()
        self._start_renderer_watchdog()
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and getattr(self, "_page_ready", False):
            self.web_view.page().runJavaScript("if (typeof map !== 'undefined') map.invalidateSize(false);")

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
            logger.info(f"Display monitor changed to: {new_screen.name()} (DPI scale={dpr}, size={geo.width()}x{geo.height()})")
            # 25-second grace period after monitor change so compositor sync never triggers a false-positive watchdog reload
            self._watchdog_grace_until = time.time() + 25.0
            self._watchdog_unanswered = 0
            self._schedule_map_invalidate(delay_ms=250)

    def _schedule_map_invalidate(self, delay_ms: int = 150):
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and getattr(self, "_page_ready", False):
            if not hasattr(self, "_map_resize_timer"):
                self._map_resize_timer = QTimer(self)
                self._map_resize_timer.setSingleShot(True)
                self._map_resize_timer.timeout.connect(self._on_debounced_map_resize)
            self._map_resize_timer.stop()
            self._map_resize_timer.start(delay_ms)

    def _on_debounced_map_resize(self):
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and getattr(self, "_page_ready", False):
            self.web_view.page().runJavaScript("if (typeof map !== 'undefined' && map) map.invalidateSize(false);")

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
        if not (WEBENGINE_AVAILABLE and hasattr(self, "web_view") and getattr(self, "_page_ready", False)):
            return
        now = time.time()
        if now < getattr(self, "_watchdog_grace_until", 0.0):
            # Window or screen was recently moved; allow compositor buffer synchronization
            self._watchdog_unanswered = 0
            return

        # Require 6 consecutive unanswered pings outside grace period (60+ seconds of complete silence)
        if self._watchdog_unanswered >= 6:
            logger.error(
                f"WebEngine renderer process unresponsive ({self._watchdog_unanswered} consecutive watchdog timeouts over 60s). "
                "Triggering automatic view recovery..."
            )
            self._watchdog_unanswered = 0
            self._on_render_process_terminated(None, -1)
            return

        self._watchdog_unanswered += 1
        try:
            self.web_view.page().runJavaScript("1 + 1;", lambda res: self._on_watchdog_pong(res))
        except Exception as e:
            logger.warning(f"Error issuing watchdog ping to WebEngine: {e}")

    def _on_watchdog_pong(self, result):
        if result == 2:
            self._watchdog_unanswered = 0

    def _on_render_process_terminated(self, termination_status, exit_code):
        logger.warning(f"WebEngine render process terminated ({termination_status}, code={exit_code}). Auto-recovering map view...")
        self._page_ready = False
        QTimer.singleShot(250, self._recover_web_view_after_termination)

    def _recover_web_view_after_termination(self):
        try:
            if hasattr(self, "web_view"):
                logger.info("Reloading Leaflet map HTML after WebEngine render process recovery...")
                try:
                    self.web_page = LoggingWebEnginePage(self.web_view)
                    self.web_view.setPage(self.web_page)
                except Exception as e:
                    logger.warning(f"Could not attach LoggingWebEnginePage on recovery: {e}")
                self.channel = QWebChannel()
                self.channel.registerObject("pyBridge", self.bridge)
                self.web_view.page().setWebChannel(self.channel)
                if hasattr(self.web_view.page(), "renderProcessTerminated"):
                    self.web_view.page().renderProcessTerminated.connect(self._on_render_process_terminated)
                self.web_view.setHtml(get_leaflet_html(), QUrl("http://localhost"))
        except Exception as e:
            logger.error(f"Failed recovering web view: {e}")

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
            if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
                pm_str = "true" if show_pm else "false"
                self.web_view.page().runJavaScript(f"setPathModesVisible({pm_str});")
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
        self.refresh_map_data()

    def _on_map_loaded(self, ok: bool):
        logger.info(f"Leaflet map loaded in WebEngine (ok={ok})")
        self._page_ready = True
        self._watchdog_unanswered = 0
        self._connect_screen_listener()
        self._start_renderer_watchdog()
        if self.config and hasattr(self.config.meshcore, "map_center_lat") and self.config.meshcore.map_center_lat is not None and self.config.meshcore.map_center_lon is not None:
            z = self.config.meshcore.map_zoom or 8
            lon = ((float(self.config.meshcore.map_center_lon) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
            lat = max(-85.0, min(85.0, float(self.config.meshcore.map_center_lat)))
            self.web_view.page().runJavaScript(f"map.setView([{lat}, {lon}], {z}); window._initialViewSet = true; map.invalidateSize();")
        fading = getattr(self.config.meshcore, "node_freshness_fading", True) if self.config else True
        self.set_freshness_fading(fading)
        show_pm = getattr(self.config.meshcore, "map_show_path_modes", False) if self.config else False
        pm_str = "true" if show_pm else "false"
        self.web_view.page().runJavaScript(f"setPathModesVisible({pm_str});")
        show_orb = getattr(self.config.meshcore, "map_show_companion_orbitals", False) if self.config else False
        docked_data = self.storage.get_docked_companions() if (self.storage and show_orb) else {}
        orb_str = "true" if show_orb else "false"
        self.web_view.page().runJavaScript(f"setCompanionOrbitalsVisible({orb_str}, {json.dumps(docked_data)});")
        self._update_scope_overlays()
        show_adsb = getattr(self.config.meshcore, "map_show_adsb", False) if self.config else False
        if show_adsb:
            self.set_adsb(True)
        base_layer = getattr(self.config, "map_base_layer", "canvas") if self.config else "canvas"
        if base_layer == "topo":
            self.web_view.page().runJavaScript("if (window.setBaseMapLayer) window.setBaseMapLayer('topo');")
        self.apply_colors()
        self.refresh_map_data()
        if getattr(self, "show_space_weather", False):
            self.set_space_weather(True)
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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            pm_str = "true" if visible else "false"
            self.web_view.page().runJavaScript(f"setPathModesVisible({pm_str});")

    def _on_orbitals_toggle(self):
        visible = self.btn_orbitals.isChecked()
        self.btn_orbitals.setStyleSheet(self._btn_style(active=visible))
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_companion_orbitals = visible
            try:
                self.config.save()
            except Exception:
                pass
        docked_data = self.storage.get_docked_companions() if (self.storage and visible) else {}
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            orb_str = "true" if visible else "false"
            self.web_view.page().runJavaScript(f"setCompanionOrbitalsVisible({orb_str}, {json.dumps(docked_data)});")
        self.refresh_map_data()

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
            self.web_view.page().runJavaScript(f"setScopeOverlaysVisible(true, {js_data});")
        else:
            self.web_view.page().runJavaScript("setScopeOverlaysVisible(false, {});")

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
            if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
                self.web_view.page().runJavaScript("clearTropoLayer();")

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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            payload = json.dumps({"grid": grid_dict, "filename": filename, "label": display_label})
            self.web_view.page().runJavaScript(f"window.onTropoDataReady({payload});")

    def _on_tropo_forecast_error(self, err_msg: str):
        self.btn_tropo.setText("📡 Tropo")
        self.btn_tropo.setChecked(False)
        self.btn_tropo.setStyleSheet(self._btn_style(active=False))
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"⚠️ <b>Tropo Error:</b> {err_msg}")
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("clearTropoLayer();")

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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            vis_str = "true" if self.show_adsb else "false"
            self.web_view.page().runJavaScript(f"setAdsbVisible({vis_str});")

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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("if (window.clearAdsbForRetarget) window.clearAdsbForRetarget();")
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

    def _on_bridge_request_aircraft_photo(self, hex_code: str):
        """Asynchronously queries Planespotters photo for aircraft hex."""
        self._reset_watchdog_activity()
        self.adsb_service.request_aircraft_photo(hex_code)

    def _on_adsb_photo_received(self, hex_code: str, photo_info: dict):
        """Delivers photo metadata back to Leaflet map tooltip."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            info_json = json.dumps(photo_info or {})
            self.web_view.page().runJavaScript(f"if (window.onAircraftPhotoReady) window.onAircraftPhotoReady('{hex_code}', {info_json});")

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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            payload_json = json.dumps(payload)
            self.web_view.page().runJavaScript(f"onAdsbDataReady({payload_json});")

    def center_map_at(self, lat: float, lon: float):
        """Smoothly pans the map to center at the specified coordinates."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript(f"map.panTo([{lat}, {lon}]);")

    def zoom_in_at(self, lat: float, lon: float):
        """Pans and zooms in one level at the specified coordinates."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript(f"map.setView([{lat}, {lon}], Math.min(map.getMaxZoom(), map.getZoom() + 1));")

    def zoom_out(self):
        """Zooms out one level."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("map.setZoom(Math.max(map.getMinZoom(), map.getZoom() - 1));")

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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript(f"if (window.dropTemporaryPin) window.dropTemporaryPin({lat}, {lon}, '{label}');")
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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("if (window.clearTemporaryPin) window.clearTemporaryPin(); if (window.clearViewshedOverlay) window.clearViewshedOverlay(); if (window.clearP2PLine) window.clearP2PLine();")
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

    def run_js(self, script: str):
        """Safely executes JavaScript in the WebEngine view if ready."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            try:
                self.web_view.page().runJavaScript(script)
            except Exception as e:
                logger.warning(f"Error executing run_js: {e}")

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

    def _on_toggle_base_map_clicked(self):
        new_layer = "topo" if getattr(self, "_current_base_layer", "canvas") == "canvas" else "canvas"
        self.set_base_map_layer(new_layer)

    def set_base_map_layer(self, layer_type: str):
        """Switches base map layer between 'canvas' (Esri Dark Canvas) and 'topo' (OpenTopoMap Relief)."""
        self._current_base_layer = layer_type
        if hasattr(self, "btn_base_map"):
            self.btn_base_map.setText("🗺️ Canvas" if layer_type == "topo" else "🏔️ Topo")
        if self.config:
            self.config.map_base_layer = layer_type
            try:
                self.config.save()
            except Exception:
                pass
        if hasattr(self, "watcher_status"):
            layer_name = "Dark Topographic Relief (OpenTopoMap)" if layer_type == "topo" else "Dark Canvas"
            self.watcher_status.setText(f"🗺️ Base map switched to: {layer_name}")
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

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            act_str = "true" if self.activity_heatmap_active else "false"
            data_json = json.dumps(data)
            self.web_view.page().runJavaScript(f"setActivityHeatmap({act_str}, {self.activity_timeframe_hours}, {data_json});")

    def _on_bridge_activity_timeframe_changed(self, hours: int):
        self.set_activity_heatmap(True, timeframe_hours=hours)

    def _on_bridge_activity_heatmap_toggled(self, enabled: bool):
        self.set_activity_heatmap(enabled)

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

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            vis_str = "true" if self.show_thunderstorm else "false"
            meta_json = json.dumps(self.thunderstorm_service.latest_radar or {})
            self.web_view.page().runJavaScript(f"setThunderstormVisible({vis_str}, {meta_json});")

    def _on_thunderstorm_radar_updated(self, radar_meta: dict):
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready and self.show_thunderstorm:
            meta_json = json.dumps(radar_meta)
            self.web_view.page().runJavaScript(f"updateThunderstormRadar({meta_json});")

    def _on_bridge_thunderstorm_toggled(self, enabled: bool):
        self.set_thunderstorm(enabled)

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
            if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
                self.web_view.page().runJavaScript("clearSpaceWeatherLayer();")

    def _on_space_weather_updated(self, payload: dict):
        if not getattr(self, "show_space_weather", False):
            return
        kp = payload.get("kp", 0.0)
        kp_status = payload.get("kp_status", "")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"🌌 <b>Space Weather:</b> Kp {kp:.1f} ({kp_status})")
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            payload_json = json.dumps(payload)
            self.web_view.page().runJavaScript(f"window.onSpaceWeatherReady({payload_json});")

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

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            js_coords = json.dumps(route_coords)
            meta = {
                "sender": path.sender_name or path.sender_id,
                "hops": path.hop_nodes or [],
                "route_type": path.route_type or "FLOOD"
            }
            self.web_view.page().runJavaScript(f"previewPacketPath({js_coords}, {json.dumps(meta)});")

    def clear_preview_packet_path(self):
        """Clears the temporary on-hover path preview."""
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("clearPreviewPacketPath();")
        if hasattr(self, "watcher_status") and not self.activity_heatmap_active and not self.show_thunderstorm:
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def set_freshness_fading(self, enabled: bool):
        self.freshness_fading = enabled
        if hasattr(self, "btn_age_fade"):
            self.btn_age_fade.blockSignals(True)
            self.btn_age_fade.setChecked(bool(enabled))
            self.btn_age_fade.blockSignals(False)
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            fading_str = "true" if enabled else "false"
            self.web_view.page().runJavaScript(f"setFreshnessFading({fading_str});")

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
                self.watcher_status.setText(f"⚡ <b>Phantom Node:</b> Marked '{clean_alias or node_id}' as phantom (excluded from map)")
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
                self.watcher_status.setText(f"⚡ <b>Phantom Node:</b> Unmarked '{clean_alias or node_id}'")

        if hasattr(self, "_active_visualise_msg") and self._active_visualise_msg:
            self.visualise_message_path(self._active_visualise_msg)
        elif hasattr(self, "_pending_visualise_msg") and self._pending_visualise_msg:
            self.visualise_message_path(self._pending_visualise_msg)

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
                "repeaterHover": getattr(app_colors, "map_repeater_hover_color", "#FF55FF"),
                "companion": app_colors.map_companion_color,
                "companionHover": getattr(app_colors, "map_companion_hover_color", "#00FFFF"),
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
                "dotSize": getattr(app_colors, "map_dot_size", 6.4)
            }
            self.web_view.page().runJavaScript(f"setMapColors({json.dumps(theme_dict)});")

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
            self.web_view.page().runJavaScript(f"if (window.setAdsbColorConfig) window.setAdsbColorConfig('{mode}', {json.dumps(adsb_colors)});\nif (window.renderAdsbLegend) window.renderAdsbLegend();")

    def _on_bridge_node_clicked(self, node_id: str):
        self._reset_watchdog_activity()
        logger.info(f"Map marker clicked for node: {node_id}")
        self.node_selected.emit(node_id)

    def _on_center_clicked(self):
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("centerOnAll();")

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

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
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
            self.web_view.page().runJavaScript(js_code)
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"📍 Centered map on: {alias or node_id} ({lat:.4f}, {lon:.4f})")
        return True

    def _on_repeaters_toggle(self):
        self.show_repeaters_only = self.btn_repeaters.isChecked()
        self.btn_repeaters.setStyleSheet(self._btn_style(self.show_repeaters_only))
        self.refresh_map_data()

    def _on_links_toggle(self):
        self.show_rf_links = self.btn_links.isChecked()
        self.btn_links.setStyleSheet(self._btn_style(self.show_rf_links))
        self.refresh_map_data()

    def set_node_filter_mode(self, mode: str):
        self.node_filter_mode = mode
        self.refresh_map_data()

    def set_rf_links(self, visible: bool):
        self.show_rf_links = visible
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
        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            pm_str = "true" if visible else "false"
            self.web_view.page().runJavaScript(f"setPathModesVisible({pm_str});")

    def set_orbitals(self, visible: bool):
        self.show_companion_orbitals = visible
        if hasattr(self, "btn_orbitals"):
            self.btn_orbitals.blockSignals(True)
            self.btn_orbitals.setChecked(visible)
            self.btn_orbitals.setStyleSheet(self._btn_style(active=visible))
            self.btn_orbitals.blockSignals(False)
        if self.config and hasattr(self.config, "meshcore"):
            self.config.meshcore.map_show_companion_orbitals = visible
            try:
                self.config.save()
            except Exception:
                pass
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

    def refresh_map_data(self, debounce: bool = False):
        """Fetches nodes and RF links with coordinates and pushes to Leaflet map.
        
        If debounce is True, defers the refresh by 150ms to coalesce burst events.
        """
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

        local_coord = self._get_local_coordinates()
        ref_lat, ref_lon = local_coord[0], local_coord[1]
        contacts = [
            c for c in self.storage.get_nodes_with_coordinates()
            if is_plausible_rf_coordinate(c.latitude, c.longitude, ref_lat=ref_lat, ref_lon=ref_lon, max_distance_km=2500.0)
            and not (self.storage and self.storage.is_phantom_node(c.node_id, c.alias))
        ]
        if self.node_filter_mode == "CLIENTS":
            contacts = [c for c in contacts if not c.is_repeater and "[rep]" not in (c.alias or "").lower() and "[room]" not in (c.alias or "").lower() and "[server]" not in (c.alias or "").lower()]
        elif self.node_filter_mode == "REPEATERS" or self.show_repeaters_only:
            contacts = [c for c in contacts if c.is_repeater or "[rep]" in (c.alias or "").lower()]
        elif self.node_filter_mode == "ROOMS":
            contacts = [c for c in contacts if "[room]" in (c.alias or "").lower() or "[server]" in (c.alias or "").lower()]

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
                "is_favorite": is_fav,
                "is_local": c.node_id == "local" or (self.config and c.node_id == self.config.meshcore.node_id.lstrip("!")),
                "lat": float(c.latitude),
                "lon": float(c.longitude),
                "snr": float(c.snr_db) if c.snr_db is not None else None,
                "rssi": int(c.rssi_dbm) if c.rssi_dbm is not None else None,
                "last_seen": c.last_seen,
                "out_path_len": int(c_p_len) if c_p_len is not None else -1,
                "out_path_hash_mode": int(c_p_mode) if c_p_mode is not None else -1,
                "out_path_src": c_p_src,
                "out_path": getattr(c, "out_path", "") or "",
                "scope_name": getattr(c, "scope_name", None),
                "allowed_regions": getattr(c, "allowed_regions", []) or []
            })

        rep_count = sum(1 for c in contacts if c.is_repeater)
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
            is_orb_active = getattr(self, "show_companion_orbitals", False) or (hasattr(self, "btn_orbitals") and self.btn_orbitals.isChecked())
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

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self._page_ready:
            js_nodes = json.dumps(nodes_data)
            js_links = json.dumps(links_data)
            self.web_view.page().runJavaScript(f"setNodes({js_nodes});")
            self.web_view.page().runJavaScript(f"setRfLinks({js_links});")
            is_orb_active = getattr(self, "show_companion_orbitals", False) or (hasattr(self, "btn_orbitals") and self.btn_orbitals.isChecked())
            orb_active_str = "true" if is_orb_active else "false"
            js_docked = json.dumps(docked_data)
            self.web_view.page().runJavaScript(f"setCompanionOrbitalsVisible({orb_active_str}, {js_docked});")
            if hasattr(self, "btn_scopes") and self.btn_scopes.isChecked():
                self._update_scope_overlays()

    def _on_packet_path_traced(self, path: PacketPathInfo):
        """Displays traced multi-hop packet trajectory on the map."""
        if not path or not path.coordinates:
            return

        hops_str = " ➔ ".join(path.hop_nodes) if path.hop_nodes else f"{len(path.coordinates)} hops"
        snr_text = f" ({path.hop_snrs[0]:+.1f} dB)" if path.hop_snrs else ""
        t_str = datetime.now().strftime("%H:%M:%S")

        self.watcher_status.setText(f"[{t_str}] ⚡ {path.route_type}: {hops_str}{snr_text}")
        self._last_traced_path_info = (datetime.now().timestamp(), path.coordinates)

        route_coords = [list(pt) for pt in path.coordinates]
        local_coord = self._get_local_coordinates()
        if local_coord:
            if not route_coords or route_coords[-1] != local_coord:
                route_coords.append(local_coord)

        if len(route_coords) < 2:
            return

        if WEBENGINE_AVAILABLE and hasattr(self, "web_view") and self.show_paths and self._page_ready:
            js_coords = json.dumps(route_coords)
            js_meta = json.dumps({
                "hops": len(route_coords) - 1,
                "route_type": path.route_type,
                "sender_id": path.sender_id,
                "sender_name": path.sender_name,
                "color": "orange"
            })
            self.web_view.page().runJavaScript(f"drawPacketPath({js_coords}, {js_meta});")

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

        # 1. Resolve Sender Coordinate
        sender_contact = self.storage.get_contact(msg.sender_id) or self.storage.get_contact(msg.sender_name)
        sender_coord = None
        if sender_contact and sender_contact.latitude and sender_contact.longitude:
            sender_coord = [sender_contact.latitude, sender_contact.longitude]

        # 2. Resolve Intermediate Hop Coordinates from Path metadata or recent Watcher trace
        hop_coords = []
        path_str = ""
        path_len = 0
        if msg.metadata:
            path_str = str(msg.metadata.get("path", "") or "")
            path_len = int(msg.metadata.get("path_len", 0) or 0)

        # 3. Resolve Local Receiver Coordinate
        local_coord = self._get_local_coordinates()

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
                    if c_h and c_h.latitude is not None and c_h.longitude is not None:
                        hop_coords.append([c_h.latitude, c_h.longitude])
            else:
                last_ref_lat, last_ref_lon = (54.65897, -3.4346)
                for sub_h in raw_hops:
                    c_hop = self.storage.get_best_contact_for_hop(sub_h, ref_lat=last_ref_lat, ref_lon=last_ref_lon) if (self.storage and hasattr(self.storage, "get_best_contact_for_hop")) else (self.storage.get_contact(sub_h) if self.storage else None)
                    if c_hop and c_hop.latitude and c_hop.longitude:
                        hop_coords.append([c_hop.latitude, c_hop.longitude])
                        last_ref_lat = float(c_hop.latitude)
                        last_ref_lon = float(c_hop.longitude)
        elif hasattr(self, "_last_traced_path_info") and self._last_traced_path_info:
            now_ts = datetime.now().timestamp()
            l_time, l_coords = self._last_traced_path_info
            if (now_ts - l_time <= 2.5) and l_coords:
                hop_coords = [list(pt) for pt in l_coords]

        # 4. Assemble Complete Route
        route_coords = []
        if sender_coord:
            route_coords.append(sender_coord)
        for h in hop_coords:
            if not route_coords or route_coords[-1] != h:
                route_coords.append(h)
        if local_coord:
            if not route_coords or route_coords[-1] != local_coord:
                route_coords.append(local_coord)

        display_name = f"@{msg.sender_name}"
        if sender_contact:
            display_name = f"📡 {sender_contact.alias}" if sender_contact.is_repeater else f"@{sender_contact.alias}"

        # If 2 or more coordinates are resolved, draw the green route on the map!
        if len(route_coords) >= 2 and self.show_paths:
            js_coords = json.dumps(route_coords)
            js_meta = json.dumps({
                "hops": len(route_coords) - 1,
                "route_type": msg.metadata.get("route_type", "FLOOD") if msg.metadata else "FLOOD",
                "sender_id": msg.sender_id,
                "sender_name": display_name,
                "channel": msg.channel,
                "is_incoming": True,
                "color": "green"
            })
            self.web_view.page().runJavaScript(f"drawPacketPath({js_coords}, {js_meta});")
        elif sender_coord:
            # Fallback: pulse origin node if only sender is known
            js_coord = json.dumps(sender_coord)
            js_id = json.dumps(msg.sender_id)
            js_name = json.dumps(display_name)
            self.web_view.page().runJavaScript(f"pulseOriginNode({js_coord}, {js_id}, {js_name});")

    def clear_visualised_path(self):
        """Clears any currently visualised dotted message path and repeater highlights from the map."""
        self._active_visualise_msg = None
        if hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("clearVisualisedPath();")
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText("⚡ <b>Watcher:</b> Listening for live RF packet paths...")

    def clear_repeater_neighbors(self):
        """Clears repeater neighbours overlay from the map."""
        if hasattr(self, "web_view") and self._page_ready:
            self.web_view.page().runJavaScript("clearRepeaterNeighbors();")
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

        if hasattr(self, "web_view") and self._page_ready:
            js_payload = json.dumps(payload)
            self.web_view.page().runJavaScript(f"drawRepeaterNeighbors({js_payload});")
        else:
            self._pending_neighbors_payload = payload

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

        # 3. Resolve Receiver Coordinate (Always terminates at Home Node)
        receiver_coord = self._get_local_coordinates()

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
                "is_phantom": bool(r.get("is_phantom"))
            })

        if receiver_coord:
            node_chain.append({"name": home_alias, "coords": receiver_coord, "is_known": True})

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
                        "missing_names": missing_names,
                        "color": seg_color
                    })
                last_loc_idx = i

        # 5. Format Time
        time_str = ""
        try:
            time_str = datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00")).strftime("%H:%M:%S")
        except Exception:
            time_str = msg.timestamp[:8]

        # 6. Status Text Update
        known_count = sum(1 for r in repeaters_info if r["is_known"] and not r.get("is_phantom"))
        phantom_count = sum(1 for r in repeaters_info if r.get("is_phantom"))
        unknown_count = len(repeaters_info) - known_count - phantom_count
        ambiguous_count = sum(1 for r in repeaters_info if r.get("is_ambiguous"))
        amb_str = f", {ambiguous_count} with alternates" if ambiguous_count > 0 else ""
        phant_str = f", {phantom_count} phantom" if phantom_count > 0 else ""
        rep_summary = f"{len(repeaters_info)} repeaters ({known_count} known{amb_str}{phant_str}, {unknown_count} unknown)" if repeaters_info else "Direct (0 repeaters)"
        if hasattr(self, "watcher_status"):
            self.watcher_status.setText(f"📍 <b>Path:</b> {sender_display} ➔ [{rep_summary}] ➔ 🏠 {home_alias}")

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
                "home_name": home_alias,
                "repeaters": repeaters_info
            })
            self.web_view.page().runJavaScript(f"drawVisualisedMessagePath({js_segments}, {js_meta});")

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
