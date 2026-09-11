# Changelog

All notable changes to **MESHCORE NAVIGATOR** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows semantic versioning with automated build increments:
- **Patch (+0.0.1)**: Routine bug fixes, UI adjustments, maintenance, and regular GitHub commits.
- **Minor (+0.1.0)**: Substantial new features and architectural additions.

## [0.2.5] - 2026-09-11

### Fixed & Enhanced
- **High-Resolution Terrarium DEM for Point-to-Point RF Elevation Profile**:
  - Replaced the rate-limited Open-Meteo REST API in `ElevationProfileWorker` with direct local/AWS Terrarium DEM satellite radar tiles (30m–80m Copernicus / EU-DEM / SRTM).
  - Resolved the bug where Open-Meteo returned `HTTP 429 Too Many Requests`, causing missing elevation samples to fall back to `0.0m` (sea level), which created a false cliff/obstruction at 0.07km and a flatline profile.
  - Implemented smart gap interpolation across profile sample points to eliminate synthetic single-point spikes.
  - Profile path elevations now seamlessly render true topography from Broughton Cross (~35m ASL) through Great Broughton up to Broughton Moor (~118m ASL) with exact 1st Fresnel zone ellipsoid clearance.

## [0.2.4] - 2026-09-11

### Added & Enhanced
- **High-Definition Terrarium DEM Elevation Engine**:
  - Integrated AWS Terrarium satellite radar DEM tiles (Copernicus / EU-DEM / SRTM) into `ViewshedWorker`.
  - Increased raw topographical data density by **~1,470×** (from 400 sample points to over **589,000 elevation points** at 30m–80m true topographic resolution).
  - Increased output viewshed raster resolution from 120×120 to **280×280** high definition, rendering exact river valley paths, mountain walls, crags, and shoreline shadows with surgical precision.
  - Implemented multithreaded parallel tile downloading (`ThreadPoolExecutor`) with automatic local disk caching in `~/.config/meshcore-tray/dem_tiles/` for instant offline recalculation.
  - Included robust fallback to cached local elevations in offline environments.

## [0.2.3] - 2026-09-11

### Added & Enhanced
- **2D Line-of-Sight Coverage Raster (Terrain Surface Heatmap)**:
  - Replaced radial pie-slice "beams of data" with an organic 2D coverage surface raster matching real-world RF propagation tools.
  - Visible terrain hit by direct line-of-sight is rendered in translucent emerald green (`#10B981`) with antialiased edge smoothing, while valleys and obstructed regions in radio shadow remain transparent.
  - Generates an in-memory RGBA raster via Pillow and overlays it on Leaflet using GPU-accelerated `L.imageOverlay`.
- **Fast 20x20 DEM Grid Fetching (Zero HTTP 429 Errors)**:
  - Re-architected viewshed sampling from 2,520 polar ray points across 32 network batches to a structured 20x20 DEM surface grid (400 points in only 5 batches of 80 points).
  - Completely eliminates Open-Meteo burst rate-limiting (HTTP 429), completing DEM queries in ~1.5s instead of 10+ seconds.
  - Interpolates the 20x20 elevation matrix into a 120x120 fine visibility grid (67ms calculation time) with 4/3 earth curvature refraction.
- **Pure Grayscale Monochromatic Dark Elevation Base Map**:
  - Replaced the previous colored OpenTopoMap filter with a sleek dark monochromatic elevation relief:
    `filter: grayscale(100%) invert(100%) brightness(70%) contrast(140%) !important; background-color: #12151a !important;`
  - Eliminates all muddy green/yellow/brown hues, producing crisp charcoal/black shaded relief and glowing white mountain ridge lines matching the concept mockup.
- **Instant Map Startup & Local Leaflet Vendoring**:
  - Locally vendored `leaflet.js` (1.9.4) and `leaflet.css` in `meshcore_tray/ui/static/vendor/` and inlined them into the page template.
  - Completely eliminates blocking remote network calls to unpkg.com on application startup.
- **Window Maximized State Persistence**:
  - Added `window_maximized`, `window_width`, and `window_height` persistence in `AppConfig`.
  - Application restores maximized state immediately upon launch without requiring manual maximization or triggering late viewport redraws.
- **In-Map Animated Loading HUD**:
  - Added a non-blocking floating dark-glass HUD badge (`#map-loading-hud`) at the top of the map with an animated spinner.
  - Provides instant, clear visual feedback during map initialization, 2D viewshed raster generation, and elevation profile calculations.

## [0.2.2] - 2026-09-11

### Added
- **Dedicated Left Navigation Dock Button (`🏔️`) for Line of Sight & Topo Elevation**:
  - Moved the Line-of-Sight (LOS) and point-to-point path elevation profile behind a dedicated button on `NavDockWidget` (`btn_rf_los`).
  - Keeps the map view completely clean and uncluttered by default; controls only appear when toggled on.
  - Automatically activates the dock button and toolbar if the user triggers viewshed calculation or elevation profiling from right-click context menus on repeaters, nodes, or map coordinates.
- **Selectable Dark Topographic Base Map Layer (OpenTopoMap)**:
  - Added full dark-mode topographic relief layer with mountains, hillshade, elevation contours, and valleys via OpenTopoMap (`https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png`).
  - Tuned with high-contrast dark mode CSS filter: `invert(100%) hue-rotate(180deg) brightness(75%) contrast(115%) saturate(45%)`.
  - Added floating in-overlay toggle button `[ 🏔️ Topo / 🗺️ Canvas ]` in top-left map controls to instantly switch between the minimalist dark canvas and dark topographic relief.
  - Preference persisted across application sessions via `AppConfig.map_base_layer`.

### Fixed & Enhanced
- **Open-Meteo 100-Coordinate Hard Limit & Topographical DEM Fetching**:
  - Resolved HTTP 400 Bad Request caused by exceeding Open-Meteo's strict 100-coordinate batch limit, which had resulted in flat 0.0m terrain elevation fallbacks.
  - Implemented 80-coordinate chunking with 120ms rate-pacing and exponential backoff retry on HTTP 429 across both `ElevationService` and `ViewshedService`.
- **Mockup-Accurate Radiating LOS Light Ray Styling**:
  - Replaced polygon grid trapezoids with continuous radial light rays extending from the emitter center to the horizon or terrain crest.
  - Rendered via Leaflet SVG renderer (`renderer: L.svg({ pane: 'losPane' })`) with zero stroke (`stroke: false, weight: 0`) and dynamic SVG radial gradient (`#10B981` emerald green at emitter core -> `#00E5FF` cyan at outer boundary, fading to transparent).
  - Dynamic gradient coordinates (`cx, cy, r`) update smoothly during panning and zooming.
  - Added animated pulsing beacon marker at the RF emitter site.

---

## [0.2.1] - 2026-09-11

### Fixed
- **Viewshed Calculation `observer_alias` TypeError**:
  - Added `observer_alias: str = "Observer"`, `num_rays: int = 72`, `samples_per_ray: int = 35`, and `**kwargs` support to `ViewshedService.calculate_viewshed()` and `ViewshedWorker`.
  - Resolved `TypeError: ViewshedService.calculate_viewshed() got an unexpected keyword argument 'observer_alias'` when right-clicking repeaters or nodes to calculate LOS viewshed coverage.
  - Included observer station alias metadata in the GeoJSON Feature properties and UI status updates.
- **Window Maximise Viewport Pause & Compositor Lockup**:
  - Implemented debounced `ResizeObserver` and `window.onresize` listeners (120ms) inside Leaflet HTML, replacing synchronous un-throttled geometry invalidations during resize transitions.
  - Added a 150ms debounce to Leaflet's `moveend` event to prevent spamming Qt with QWebChannel IPC messages while the window is being maximized or resized.
  - Added `changeEvent(event)` in `MeshMapWidget` intercepting `QEvent.Type.WindowStateChange` with a 250ms grace delay to allow the OS window manager and Chromium swapchain to complete buffer reallocation before invalidating the map.
  - Configured `map_splitter` with `setStretchFactor(0, 4)`, `setStretchFactor(1, 1)`, and `setChildrenCollapsible(False)` to ensure smooth, non-collapsing viewport distribution.

---

## [0.2.0] - 2026-09-11

### Added
- **Topographic RF Elevation Profile & 1st Fresnel Zone Clearance**:
  - Point-to-Point terrain cross-section dock dividing the map view into 4/5ths map area and 1/5th interactive elevation graph via a draggable `QSplitter`.
  - Calculates Great Circle intermediate coordinates using Copernicus 30m Digital Elevation Model (DEM) via Open-Meteo with local SQLite caching (`elevation_cache.db`).
  - Incorporates true 4/3 Earth curvature bulge ($k = 1.333$) for tropospheric RF bending and calculates the 868 MHz / 915 MHz 1st Fresnel zone clearance boundary.
  - Interactive QPainter canvas rendering dark mountain terrain with emerald boundary stroke and vertical gradient, optical LOS beam, dotted gold 1st Fresnel zone ellipse, and summit tower beacons.
  - Status badges with clearance telemetry: `🟢 100% Line of Sight Clear (+Xm Fresnel)`, `🟡 Visual LOS Clear (Fresnel Incursion)`, and `🔴 Line of Sight Blocked (+Xm @ Ykm)`.
  - Dual antenna height spinboxes (`Tx Ant` and `Rx Ant`) with instant clearance recalculation upon height adjustment.
  - Synchronized mouse scrubbing across the profile canvas that moves a real-time reticle marker along the RF link on the Leaflet map.
- **Radial Terrain-Aware Line-of-Sight (LOS) Viewshed Coverage**:
  - 360-degree viewshed propagation ray-marching engine computing visible sectors across 72 azimuth rays and up to 50 km radius.
  - Topographical features (ridges, hills, mountains) naturally cast realistic signal shadows behind obstructions where LOS drops below the horizon line.
  - Renders as a radiating emerald green (`#10B981`) vector polygon overlay in a dedicated Leaflet pane (`losPane`, zIndex 370).
  - Floating in-overlay toolbar at the top of the map with quick presets:
    - Ground level (2m handheld / mobile)
    - Average rooftop height (8m residential eaves/chimney mount)
    - High mast (15m tower/repeater mount)
    - Radius selector: 15 km, 25 km, and 50 km.
  - One-click interactive "🏔️ Profile Path" measurement mode allowing two arbitrary clicks on the map to profile any terrain path.
  - Node popup actions and map right-click context menu options for instant profiling from Home Station or calculating viewshed from any coordinate.

---

## [0.1.8] - 2026-09-11

### Fixed
- **ADS-B Aggregator Rate Limiting & HTTP 429 Backoff**:
  - Implemented automatic rate-limit cooldown tracking (`_ENDPOINT_COOLDOWNS`) across community ADS-B feeds.
  - When an aggregator returns HTTP 429 (Too Many Requests), the service now parses `Retry-After` (defaulting to a 60-second cooldown) and gracefully skips queries to that specific host during backoff without spamming error logs.
- **Aircraft Photo Lookup Spam & Negative Result Caching**:
  - Added negative result caching (`_negative_photo_cache`) with a 3-minute cooldown in `ADSBService` and pre-query checks in JavaScript (`window._aircraftPhotos[lowHex]`).
  - Completely eliminates rapid 404 lookup storms against Planespotters and Airport-Data when hovering or pinning aircraft that lack registered photos.
- **Renderer Watchdog Multi-Monitor False Positives**:
  - Replaced aggressive 12-second watchdog timeout with a 10-second heartbeat requiring 60+ seconds of unresponsiveness outside grace periods.
  - Added a 25-second grace window upon screen changes and window moves to permit Wayland/X11 compositor Vulkan swapchain synchronization.
  - Automatically resets watchdog timers upon receiving any bridge event (`map_moved`, `node_clicked`, ADS-B signals), eliminating false-positive reloads during user interactions.
  - Removed duplicate `showEvent` and redundant `loadFinished` connections that triggered multiple map resets.

---

## [0.1.7] - 2026-09-11

### Added
- **Persistent Centralized Logging System (`meshcore_tray/logger.py`)**:
  - Implemented automatic rotating file logging writing to `~/.config/meshcore-tray/meshcore_navigator.log` (10 MB per file, 5 backups) with console output.
  - Intercepts Qt C++ runtime warnings/errors via `qInstallMessageHandler` (tracking Wayland, OpenGL/Vulkan, and WebEngine messages).
  - Intercepts uncaught Python exceptions with full tracebacks via `sys.excepthook`.
  - Added `LoggingWebEnginePage` capturing all Leaflet/Chromium JavaScript console logs (`[JS-INFO]`, `[JS-WARN]`, `[JS-ERROR]`), unhandled promise rejections, window errors, and Leaflet tile fetch errors into the Python log file.
- **Chromium WebEngine Heartbeat Watchdog**:
  - Added 4-second recurring heartbeat (`1 + 1;` evaluation).
  - Automatically detects renderer hangs/freezes (>10 seconds unresponsive) and triggers an automatic clean reload of the map view (`_recover_web_view_after_termination`).

### Fixed
- **Multi-Monitor Window Drag & Map Desynchronization Crash**:
  - Added native DOM `ResizeObserver` on `#map` and `window.addEventListener('resize')` to immediately call `map.invalidateSize(false)` whenever container bounds change.
  - Hooked Qt `windowHandle().screenChanged` and `moveEvent` to track cross-monitor transitions with differing DPI scale factors and resolutions, debouncing map invalidation to eliminate grey/black unrendered boundaries and compositor stalls when dragging the map across displays.

---

## [0.1.6] - 2026-09-11

### Fixed
- **ADS-B Window Maximize Crash & Renderer Memory Leak**:
  - **Single Persistent Trail Polylines**: Replaced per-segment breadcrumb polylines (which instantiated and destroyed up to 5,800 `L.polyline` SVG DOM paths every 5 seconds) with a single persistent `L.polyline` per aircraft updated in-place via `setLatLngs()`.
  - **In-Place Marker Icon Updates**: Eliminated continuous DOM recreation and click-propagation listener churn (`marker.setIcon`) on heading or speed updates. Heading rotations are applied directly via `planeDiv.style.transform` and SVG `fill` attributes, only re-instantiating icons when distress or category changes.
  - **Lazy Tooltip Construction**: Deferred `buildAircraftTooltipHtml` and `marker.setTooltipContent()` exclusively to active or pinned tooltips rather than eagerly generating and parsing HTML strings for 200 closed aircraft markers every radar sweep.
  - **Cluster Search Optimization**: Replaced O(N²) all-aircraft container coordinate calculations on every tick with on-demand cluster lookups only when opening or updating an active tooltip.
  - **Hardware Rasterization Enablement**: Removed legacy `--disable-gpu` from `main.py`, `run.sh`, and `install.sh`, allowing Chromium WebEngine to leverage Vulkan/GPU hardware compositing on Wayland/X11 instead of thrashing gigabytes of shared memory in SwiftShader CPU rasterization buffers.

---

## [0.1.5] - 2026-09-11

### Fixed
- **ADS-B Retargeting Crash on Map Right-Click**:
  - **Thread-Safe Generational Response Tracking**: Replaced cross-thread signal disconnection with generation sequence counters (`_query_generation`) and target coordinate validation in `_on_worker_success()`. Obsolete payloads from previous radar centers are safely discarded without mutating active worker signals from foreign threads.
  - **QThread Termination & Retirement Lifecycle**: Kept completed background fetch workers in `_retired_workers` with explicit `w.wait()` joins before memory deallocation, eliminating `qFatal("QThread: Destroyed while thread is still running")` caused by premature Python garbage collection.
  - **Non-Blocking Context Menu Popup**: Replaced nested modal `QMenu.exec()` inside WebChannel IPC turns with non-blocking `QMenu.popup()`, preventing Chromium renderer IPC deadlocks and memory use-after-free crashes.
  - **Leaflet Retargeting & Layer Clearance Safeguards**: Wrapped `map.removeLayer()`, `closeTooltip()`, and range marker cleanup inside `window.clearAdsbForRetarget` with robust `try/catch` and layer group validation.

---

## [0.1.4] - 2026-09-11

### Fixed
- **Multi-Monitor Window Move & Maximize Freeze**:
  - Enabled `QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)` before `QApplication` creation to prevent Chromium OpenGL context corruption when moving across different monitors or display servers.
  - Added Chromium fallback flags `--disable-gpu --no-sandbox` via `QTWEBENGINE_CHROMIUM_FLAGS`.
  - Added automatic WebEngine recovery via `renderProcessTerminated` signal on `QWebEnginePage`, reloading the map if the Chromium render process crashes.
  - Consolidated duplicate `resizeEvent` handlers into a debounced 120ms timer, preventing rapid concurrent `invalidateSize()` IPC calls from freezing the WebEngine swapchain during window maximize and resize.
- **Aircraft Tooltip Hover Grace Period & Auto-Dismissal**:
  - Unhooked Leaflet's built-in 0ms `mouseout` auto-closing listener (`marker.off('mouseout', marker.closeTooltip)`), restoring full control to our hover grace timer.
  - Increased hover grace timer to 800ms, ensuring tooltips never abruptly disappear if the cursor accidentally slips off the marker icon.
  - Entering `.adsb-tooltip` or moving between stacked aircraft pills safely cancels dismissal.
- **Aircraft Left-Click Pinning & Real-Time Tracking**:
  - Left-clicking an aircraft marker or stacked flight pill permanently pins the tooltip open (`pinnedTooltipHex = hex`).
  - Isolated click and mousedown events (`disableClickPropagation`) so clicking inside tooltips or on aircraft markers never triggers map-level deselection.
  - Pinned tooltips now dynamically follow the aircraft in real-time as position coordinates update on each radar scan (`tip.setLatLng(marker.getLatLng())`).

---

## [0.1.3] - 2026-09-11

### Fixed
- **Map Context Menu & ADS-B Freeze**:
  - Decoupled `_show_map_context_menu` from the synchronous QWebChannel IPC slot via `QTimer.singleShot(0, ...)`, preventing deadlocks between Qt and Chromium WebEngine renderer when right-clicking and retargeting ADS-B.
  - Decoupled all context menu action executions so the modal menu loop unwinds cleanly before background services trigger map updates.
  - Protected `ADSBService.set_target()` against worker collisions by safely disconnecting obsolete worker signals and queuing a fresh refresh when retargeting while queries are active.
  - Eliminated redundant secondary `refresh()` calls in `set_adsb_target()`.
- **Aircraft Marker Left-Click Pinning & Tooltip Interaction**:
  - Left-clicking an aircraft marker now reliably pins and locks the tooltip open so links and stacked aircraft pills can be clicked without unintentional dismissal.
  - Added click and mousedown capture-phase event isolation on `.adsb-tooltip` to prevent interaction clicks from bubbling to Leaflet map canvas.
  - Guarded `map.on('click')` against unpinning when clicks originate inside the tooltip, aircraft marker, stacked cluster pills, or UI overlay controls.
- **Dual-Source Aircraft Photo Fetching & Fallback**:
  - Added seamless fallback to `Airport-Data.com` API when `Planespotters.net` has no photo, unlocking imagery for UK general aviation, light aircraft, helicopters, gliders, and regional turboprops.
  - Fixed cache pollution so empty/failed lookups are never permanently cached, allowing retry upon subsequent views or network recovery.
  - Dynamically displays photo provider attribution (`Planespotters ↗` or `Airport-Data ↗`) and photographer credit.

---

## [0.1.2] - 2026-09-11

### Added
- **ADS-B Aircraft Distress Beacon & Echo Pulse**:
  - Automatically detects international emergency transponder squawk codes (`7700` General Emergency, `7600` Radio Failure, `7500` Hijack Alert) and special emergency states.
  - Added animated dual **radar echo pulse rings** radiating continuously from distressed aircraft markers for instant visual acquisition.
  - Added persistent **distress beacon tag** (`🔴 7700`, `🔴 7600`, `🔴 7500`) located directly below the aircraft on the map displaying the exact emergency beacon.
  - Added dedicated **Distress Beacon banner** and highlighted squawk code inside aircraft tooltips.
  - Replaced ambiguous "7700" label in map overlay legend with **"Distress"** featuring a gentle, non-obnoxious slowly pulsing red beacon dot (`2.2s` cycle).
  - Ensured the Distress indicator is prominently displayed across **all three color schemas** (`Altitude`, `Aircraft Type`, and `Distance`).

---

## [0.1.1] - 2026-09-11

### Added
- **ADS-B Aircraft Tooltip Hover Delay & Grace Period**:
  - Implemented 600ms hover delay so moving cursor from aircraft marker to the tooltip does not immediately close the window.
  - Added document-level tooltip event delegation to keep tooltip open while moving cursor inside it.
  - Hover bridge pseudo-element (`.adsb-tooltip::after`) bridging marker and tooltip hitboxes.
- **Stacked Aircraft Overlap Selection**:
  - Added interactive `Stacked (N):` cluster pill bar (`[FLIGHT1] [FLIGHT2]...`) allowing users to easily select and pin overlapping or stacked aircraft at airports and in formation.
- **Missing Aircraft Photo Placeholder**:
  - Implemented dedicated placeholder container with camera icon and styled `"No aircraft photo available"` label when Planespotters has no photo or when lookups are unavailable, ensuring the UI remains clean and polished.

---

## [0.1.0] - 2026-09-11

### Added
- **Dynamic ADS-B Aircraft Coloring Modes**:
  - Interactive 3-mode switcher (`Altitude`, `Type`, `Distance`) integrated directly into the map overlay panel with dynamic real-time legends.
  - **Altitude Scheme**: Full color ramp starting at Magenta (`#FF00FF`) for lowest/ground (< 2,000 ft), Red (`#FF0000`) for sub-7,000 ft, Blue (`#0000FF`) for mid altitudes (10,000–25,000 ft), and White (`#FFFFFF`) for cruise (> 25,000 ft).
  - **Aircraft Type Scheme**: Category-based coloring: White for Airliners, Blue for Light aircraft, Green for Military, Yellow for Helicopters, and Magenta for Gliders.
  - **Distance to Station Scheme**: Proximity-based heatmap coloring: Red (`#FF0000`) for closest aircraft to monitoring station, fading to Orange (`#FFA500`), Yellow (`#FFFF00`), and Green (`#00FF00`) at the radar boundary.
  - Dynamic recoloring of both aircraft markers and fading breadcrumb trails upon mode switch.
- **Customizable Color Schemes in Settings**:
  - Added dedicated ADS-B Aircraft Flight Color Schemes configuration panel under `App UI Colors` tab with mode selector and 13 color picker buttons.
  - Instant auto-save to `config.json` with reset defaults support.
- **Planespotters.net Aircraft Photo Integration**:
  - Integrated Planespotters.net public photo API in background worker thread with required custom User-Agent and in-memory caching.
  - Enriched map tooltips with aircraft photo thumbnails, photographer copyright attribution, and direct external Planespotters profile links.

---

## [0.0.8] - 2026-09-11

### Added
- **Custom Map Right-Click Context Menu**:
  - Replaced default Chromium web browser menu with native dark-themed MESHCORE NAVIGATOR context menu.
  - **Location Header**: Displays live GPS coordinates (latitude, longitude) and 6-character Maidenhead QTH grid locator.
  - **Map Navigation**: Direct actions to center map, zoom in, and zoom out at the clicked coordinates.
  - **ADS-B Air Traffic Tracking**: Re-centers radar monitoring radius around clicked point and allows quick reset back to station home.
  - **Station & Ham Radio Utilities**: Sets station home location and computes great-circle distance (miles & km) plus bearing angle from station home.
  - **Waypoint Pins & Route Tracing**: Drops temporary visual waypoint pins with Maidenhead labels and clears traces.
  - **Atmosphere & Weather**: Centers map on rain radar and toggles Blitzortung lightning strikes.
  - **Clipboard Tools**: Quick copy of latitude/longitude coordinates and Maidenhead QTH grid.
  - **Suppression & Debounce**: Implemented Chromium menu prevention across Qt (`PreventContextMenu`) and DOM event layers with debounce protection against duplicate triggers.

---

## [0.0.7] - 2026-09-11

### Changed
- **Multi-Platform Release Pipeline Optimization**:
  - Decoupled standalone binary packaging jobs for Linux and Windows from headless virtual display testing dependencies, ensuring multi-platform releases compile and deploy reliably.
  - Added real-time test log streaming and automated artifact capture.

---

## [0.0.6] - 2026-09-11

### Added
- **Linux CI Headless Virtual Framebuffer & OpenGL Support**:
  - Integrated `xvfb-run` and system OpenGL/X11 rendering libraries (`libgl1`, `libegl1`, `libxcb-*`) into automated testing workflows.
- **Dedicated Windows PyInstaller Builder**:
  - Added standalone `build_windows.py` cross-platform build script eliminating shell quotation ambiguities in PowerShell CI runners.

---

## [0.0.5] - 2026-09-11

### Added
- **GPL-3.0 Open-Source License**:
  - Adopted the GNU General Public License v3.0 (GPL-3.0-or-later) with root `LICENSE` file and updated project metadata in `pyproject.toml`, `README.md`, and Settings About dialog.
- **Multi-Platform Automated Releases**:
  - Configured automated GitHub Releases delivering portable Linux archives (`.tar.gz`) and Windows standalone x64 binaries (`.zip` containing `MESHCORE-NAVIGATOR.exe`).
  - Created native multi-resolution Windows application icon resource (`icon.ico`).
- **Self-Contained Dependency Architecture**:
  - Bundled vendor components directly into the repository, resolving remote git submodule checkout failures on automated CI runners.

---

## [0.0.4] - 2026-09-11

### Added
- **Persistent Channel Ordering & Grouping**:
  - Full drag-and-drop channel reordering within and between custom category groups.
  - Channels and groups remember their exact manual ordering across app restarts via `AppConfig` and SQLite `app_state` synchronization.
- **Heard Floods Unread Tracking & Auto-Scroll**:
  - Automatically records read flood packets in storage.
  - New/unread flood cards display a distinctive `● NEW` pill badge and purple neon border accent.
  - When opening the RF Floods view, the list automatically scrolls to position the first unread flood at the top of the viewport.

### Changed
- **Channels Sidebar Visibility**:
  - The channels sidebar is now exclusively displayed on the main chat page and is cleanly hidden on the RF Floods, Direct Messages, Repeaters, and Settings views.
- **Eliminated Floods Hover Jitter**:
  - Replaced dynamic stylesheet swapping with native Qt `:hover` CSS, keeping a consistent 1px border width to eliminate layout reflow and sizing movement when hovering over flood cards.

---

## [0.0.3] - 2026-09-11

### Changed
- **Support Links**:
  - Updated all Buy Me a Coffee links across the splash overlay, settings window, and documentation to [`buymeacoffee.com/soulwaystudios`](https://buymeacoffee.com/soulwaystudios).
  - Centralized support URL definition in `meshcore_tray.__coffee_url__`.

---

## [0.0.2] - 2026-09-11

### Added
- **Early Development Disclaimer**:
  - Added notice in `README.md` highlighting early alpha status, AI assistance by a professional designer, and open feedback channels.
- **Automated Versioning Policy**:
  - Codified repo guidelines in `AGENTS.md` and created `scripts/bump_version.py` helper.

### Changed
- **Version Baseline**:
  - Reset application base version to `v0.0.2` for early development tracking.

---

## [0.0.1] - 2026-09-11

### Added
- Initial public station release of **MESHCORE NAVIGATOR**:
  - Interactive GPU-accelerated Leaflet map with multi-hop packet path tracing.
  - VHF/UHF tropospheric ducting forecasts, RainViewer weather radar, Blitzortung lightning strikes, and ADS-B aircraft flight tracks.
  - Discord-style channel navigation, Power Composer with autocomplete and SQLite full-text search.
  - Divoom Pixoo 64 pixel matrix animations and telemetry displays.
  - Embedded full-width Settings suite and custom UI color theme manager.
  - Glassmorphic branded launch splash mask with first-run auto-dismiss logic.
  - Cross-platform support for Linux and Windows x64.
