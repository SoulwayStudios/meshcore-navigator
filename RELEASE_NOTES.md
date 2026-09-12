> [!WARNING]
> **Early Development Notice**: MESHCORE NAVIGATOR is under active community development. Bug reports, diagnostic logs, and feature requests are very welcome!
> If you find MESHCORE NAVIGATOR useful, consider supporting development on [Buy Me a Coffee](https://buymeacoffee.com/soulwaystudios).

---

## 🌟 What's New in v0.3.0

### 🌌 NOAA Space Weather Telemetry & Aurora Forecast Layer
- **Real-Time SWPC Ingestion**: Integrated live space weather telemetry from NOAA Space Weather Prediction Center (SWPC) models.
- **Planetary K-Index & Solar Telemetry**: Live tracking of Planetary Kp index, solar wind speed, IMF Bz magnetic vector, 10.7cm Solar Flux Index (SFI), and NOAA geomagnetic storm scale warnings (G1–G5).
- **OVATION Aurora Forecast Model**: Real-time auroral precipitation oval visualized directly on the map with smooth contour rendering, adjustable opacity, and dynamic HUD status badges.

### 🛡️ Hardware Acceleration & Linux Wayland / NVIDIA Stability
- **Vulkan Compositor Fallback**: Restored Vulkan compositor fallback in Chromium WebEngine, resolving `dma_buf acquisition failure` / `null texture` crashes on Wayland sessions using NVIDIA proprietary drivers.
- **Renderer Watchdog & Multi-Monitor Protection**: Guarded the Chromium renderer watchdog against false-positive timeout triggers when the map tab is hidden in the background.
- **Debounced Window Geometry Events**: Consolidated viewport and geometry recalculation into debounced timers to prevent renderer deadlocks during window maximizing and cross-monitor dragging.

### 📍 Coordinate Sanitization & Implausible RF Rejection
- **Corrupt Coordinate Filtering**: Radio driver automatically sanitizes and rejects corrupted or shifted RF coordinates located greater than 2,000 km from the station.
- **Database Self-Repair**: Automated startup database verification and sanitization routine repairs corrupted coordinates, cleans duplicate/phantom repeaters, and corrects forward timestamp skew.

### 📡 Repeater Neighbours Hardening
- **Safe HTML Escaping & Bounds Clamping**: Sanitized all node identifiers, SNR strings, and last-heard timestamps to ensure error-free rendering of repeater neighbour graphs.
- **Graceful WebEngine Recovery**: Implemented soft page recovery on renderer process termination without destructive DOM cascades.

### 🛑 Orderly Application Shutdown & Resource Lifecycle
- **Clean Tray Exit**: Coordinated graceful shutdown sequence from the system tray menu: awaits serial radio driver and Pixoo display thread park, cleans up WebEngine surfaces, and writes an atomic SQLite parking backup.
- **Asyncio Task Teardown**: Properly cancels and gathers pending background asyncio workers on event loop termination, eliminating unhandled loop exceptions on exit.
- **Font-Independent Chat Wrapping**: Hardened chat message bubble text wrapping across varied Linux desktop font rendering engines.

---

## 📦 Previous Highlights (v0.2.x)

- **Real-Time ADS-B Air Traffic Radar**: Live civilian, commercial, and military flight tracking with emergency distress beacon detection and aircraft spotter photos.
- **Topographic RF Elevation Profile & 1st Fresnel Zone**: 30m satellite radar DEM terrain profiling with 4/3 Earth curvature refraction and antenna height tuning.
- **2D Line-of-Sight (LOS) Viewshed Coverage**: High-density 2D terrain coverage rasters displaying true radio line-of-sight and topographical shadow zones.
- **Monochromatic Dark Topographic Map**: Minimalist dark shaded relief base map with instant offline caching and rich context menu tools.
