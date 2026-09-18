"""Live Thunderstorm & Weather Radar Service for MeshCore Tray.

Fetches open weather radar precipitation imagery from RainViewer API
and coordinates real-time lightning strike streams from Blitzortung
for correlation with RF mesh propagation anomalies and atmospheric noise.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Dict, List, Optional
import urllib.request
import urllib.error

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

logger = logging.getLogger("meshcore_tray.thunderstorm_service")

RAINVIEWER_API_URL = "https://api.rainviewer.com/public/weather-maps.json"
USER_AGENT = "MeshCore-Tray/1.0 (Thunderstorm & Radar Layer)"
REFRESH_INTERVAL_MS = 600000  # 10 minutes (RainViewer radar updates every 10 min)

BLITZORTUNG_WS_SERVERS = [
    "wss://ws7.blitzortung.org",
    "wss://ws1.blitzortung.org",
    "wss://ws8.blitzortung.org"
]


def fetch_rainviewer_metadata() -> Optional[Dict]:
    """Queries RainViewer public API for current weather radar precipitation tilepaths."""
    req = urllib.request.Request(
        RAINVIEWER_API_URL,
        headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            host = data.get("host") or "https://tilecache.rainviewer.com"
            radar_sec = data.get("radar", {})
            past = radar_sec.get("past", [])
            nowcast = radar_sec.get("nowcast", [])
            all_frames = []
            for f in past:
                if isinstance(f, dict) and "path" in f:
                    all_frames.append({
                        "path": f["path"],
                        "time": f.get("time", 0),
                        "is_nowcast": False
                    })
            for f in nowcast:
                if isinstance(f, dict) and "path" in f:
                    all_frames.append({
                        "path": f["path"],
                        "time": f.get("time", 0),
                        "is_nowcast": True
                    })

            # Default to latest recorded past frame, or first nowcast
            latest_frame = past[-1] if past else (nowcast[0] if nowcast else None)
            default_idx = max(0, len(past) - 1) if past else 0

            if latest_frame and "path" in latest_frame:
                return {
                    "host": host,
                    "path": latest_frame["path"],
                    "time": latest_frame.get("time", 0),
                    "frames": all_frames if all_frames else past[-6:],
                    "default_idx": default_idx,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
    except Exception as e:
        logger.warning(f"Failed fetching RainViewer radar metadata: {e}")
    return None


class RainViewerFetchWorker(QThread):
    """Background worker to fetch RainViewer radar metadata without blocking the UI."""
    success_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def run(self):
        meta = fetch_rainviewer_metadata()
        if meta:
            self.success_signal.emit(meta)
        else:
            self.error_signal.emit("Failed retrieving RainViewer radar tiles")


class ThunderstormService(QObject):
    """Orchestrates real-time thunderstorm radar and lightning detection."""

    radar_updated = pyqtSignal(dict)
    status_updated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self.latest_radar: Optional[dict] = None
        self._worker: Optional[RainViewerFetchWorker] = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(REFRESH_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)

    def set_enabled(self, enabled: bool):
        """Enables or disables thunderstorm radar polling."""
        self.enabled = bool(enabled)
        if self.enabled:
            self.refresh()
            self._poll_timer.start()
            self.status_updated.emit("🌩️ Thunderstorm & Radar layer active")
        else:
            self._poll_timer.stop()
            self.status_updated.emit("Thunderstorm layer disabled")

    def refresh(self):
        """Triggers asynchronous refresh of RainViewer radar tiles."""
        if self._worker and self._worker.isRunning():
            return
        self._worker = RainViewerFetchWorker()
        self._worker.success_signal.connect(self._on_radar_success)
        self._worker.error_signal.connect(self._on_radar_error)
        self._worker.start()

    def _on_radar_success(self, meta: dict):
        self.latest_radar = meta
        self.radar_updated.emit(meta)
        self.status_updated.emit("🌩️ Radar: Live precipitation loaded")

    def _on_radar_error(self, err: str):
        logger.debug(f"ThunderstormService radar fetch notice: {err}")
        self.status_updated.emit(f"⚠️ Radar: {err}")

    @staticmethod
    def get_blitzortung_servers() -> List[str]:
        """Returns the list of active Blitzortung WebSocket endpoints."""
        return list(BLITZORTUNG_WS_SERVERS)
