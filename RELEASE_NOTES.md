> [!WARNING]
> **Early Development Notice**: There WILL be bugs and issues with ADS-B air traffic tracking as this is an experimental new feature under active development. Community feedback, logs, and edge-case reports are very welcome!
> If you find MESHCORE NAVIGATOR useful, consider supporting development on [Buy Me a Coffee](https://buymeacoffee.com/soulwaystudios).

---

## ✈️ Real-Time ADS-B Air Traffic Radar (Experimental)
- **Live Flight Radar Overlay**: Tracks civilian, commercial, and military aircraft in real time around your station home or any arbitrary map coordinate.
- **Dynamic Color Schemes**:
  - **Altitude Scheme**: Smooth gradient from low-level magenta (<2,000 ft) to red, yellow, blue, and white (>25,000 ft cruise).
  - **Aircraft Type Scheme**: Category-based identification for Airliners (White), Light Aircraft (Blue), Military (Green), Helicopters (Yellow), and Gliders (Magenta).
  - **Distance to Station Scheme**: Proximity heatmap showing distance from your monitoring station.
- **Custom Color Palette Editor**: Customize all 13 aircraft colors directly inside the **Settings → App UI Colors** panel.
- **Emergency Distress Beacon Detection**:
  - Automatically identifies international emergency squawk codes (`7700` Emergency, `7600` Radio Failure, `7500` Hijack Alert).
  - Radiates animated dual radar echo pulses and displays red beacon tags on the map and tooltip header.
- **Aircraft Photos & Spotter Data**:
  - Integrated high-res aircraft photo thumbnails from Planespotters.net with fallback to Airport-Data.com (great for UK general aviation, light aircraft, and gliders).
- **Interactive Tooltips & Stacked Pill Bars**:
  - Left-click to pin aircraft tooltips so they follow the aircraft in real-time across the radar sweep.
  - Interactive stacked flight selector pills (`[FLIGHT 1] [FLIGHT 2]...`) to inspect overlapping aircraft at airports or in formation.

---

## 🏔️ Topographic RF Elevation Profile & 1st Fresnel Zone Analysis
- **Split-Screen RF Link Profile**: Draggable terrain cross-section dock dividing the map view into map area and elevation graph.
- **Copernicus 30m Satellite Radar DEM**: High-precision topographic sampling powered by AWS Terrarium DEM tiles with local offline caching.
- **4/3 Earth Curvature & RF Physics**: Incorporates true Earth bulge ($k = 1.333$) for tropospheric RF bending.
- **1st Fresnel Zone Ellipsoid**: Live 868 MHz / 915 MHz clearance calculation with instant status badges (`Clear`, `Fresnel Incursion`, or `Line of Sight Blocked`).
- **Interactive Scrubbing & Antenna Controls**: Dual height spinboxes (`Tx Ant` and `Rx Ant`) with synchronized map reticle tracking as you scrub across the terrain profile.

---

## 🟢 2D Line-of-Sight (LOS) Terrain Viewshed Coverage
- **Organic Coverage Heatmap**: Replaced coarse pie-slice beams with a smooth 2D surface raster highlighting exact ground and rooftop terrain visible from your node.
- **High-Definition DEM Engine**: Samples over 580,000 elevation points (~80m spacing) to realistically cast radio shadows behind hills, ridges, and river valleys.
- **Dedicated Left Dock Button (`🏔️`) & Presets**: Keep the map completely clean and uncluttered by default, with quick-select toolbar presets for Ground (2m), Rooftop (8m), and Mast (15m) up to 50 km.

---

## 🗺️ Monochromatic Dark Topographic Map & Context Menu
- **Pure Dark Relief Base Map**: Minimalist charcoal & black shaded relief with glowing white mountain ridges, switchable via the `[ 🏔️ Topo / 🗺️ Canvas ]` toggle button.
- **Custom Map Context Menu**: Right-click anywhere on the map or nodes for quick coordinates, Maidenhead QTH grid locator, station distance/bearing, waypoint pins, and instant path profiling.

---

## ⚡ Performance, Multi-Monitor & Stability Fixes
- **Instant Map Startup**: Locally vendored Leaflet libraries inside the app, eliminating remote unpkg.com network fetches on launch.
- **Window Maximization Persistence**: The application remembers its maximized state and dimensions between sessions without compositor stalls.
- **Renderer Watchdog & Memory Optimizations**: Replaced thousands of transient polyline SVG elements with single persistent paths, preventing Chromium GPU buffer leaks.
- **In-Map Loading HUD**: Animated floating badge providing real-time feedback during viewshed generation and terrain profiling.
