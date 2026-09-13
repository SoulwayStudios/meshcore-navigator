> [!WARNING]
> **Early Development Notice**: MESHCORE NAVIGATOR is under active community development. Bug reports, diagnostic logs, and feature requests are very welcome!
> If you find MESHCORE NAVIGATOR useful, consider supporting development on [Buy Me a Coffee](https://buymeacoffee.com/soulwaystudios).

---

## 🌟 What's New in v0.6.1

### ⛰️ RF Line-of-Sight (LOS) Viewshed & Fresnel Zone Elevation Profiling
- **Real-Time Terrain Elevation Cross-Sections**: Perform high-precision point-to-point RF propagation surveys directly from the interactive map.
- **Fresnel Zone Ray Tracing**: Accurately computes Line-of-Sight ray trajectories and the 1st Fresnel Zone ellipse (60% and 100% clearance thresholds) across digital elevation terrain.
- **Antenna Height Presets**: Adjustable transmitter and receiver antenna elevations (**Ground 2m**, **Rooftop 8m**, **Mast 15m**) with live clearance/incursion margin reporting.
- **Radial LOS Viewshed Coverage**: Radial line-of-sight coverage simulation up to 25 km around repeaters and stations.

### 📡 Interactive Repeater Neighbor Topology Mapping
- **Radiating RF Links**: Click **"View Neighbors on Map"** in the Repeater Command Console to draw real-time RF propagation lines to all heard repeater stations.
- **Per-Link Signal Metrics**: Displays floating SNR badges (`-9.0 dB`, `-11.5 dB`, etc.) and radar pulse rings directly on the tactical map.

### 🛡️ Linux Display Server & NVIDIA Driver Mismatch Resilience
- **Native Wayland + XCB Fallback**: Default display platform updated to `wayland;xcb` for native Wayland compositing without XWayland context confusion.
- **Automatic NVIDIA Mismatch Safeguard**: Detects when package upgrades have installed newer NVIDIA user libraries before a system reboot, dynamically routing GLX context creation through Mesa (`__GLX_VENDOR_LIBRARY_NAME=mesa`) to prevent `GLOzone not found for unknown` crashes while keeping hardware acceleration fully active.

---

## 📦 Previous Highlights (v0.6.0)
 
### 🚀 Headline Feature: Asynchronous Startup Version Checker & Release Notifier
- **Automatic GitHub Release Checks**: On launch, MeshCore Navigator checks the official GitHub repository releases in the background. It is completely non-blocking with a strict 4-second timeout and rate-limit caching, ensuring zero impact on startup performance even when offline.
- **Interactive Splash Screen Update Prompt**:
  - When an update is detected, the splash screen pauses auto-dismissal and displays an update card with the new version badge and release title.
  - Direct **"📥 Download Update"** button opens the GitHub release download page in your default browser.
  - **"Remind Later"** button lets you bypass the update prompt and continue immediately into the app.
  - When up-to-date or offline, the splash screen auto-dismisses smoothly as usual (1.8s + map ready).
- **Fallback Top Notification Banner**: If the splash screen is disabled in user preferences, a slim, dismissable update banner appears directly above the main chat and map view.
- **Manual Checks in Settings**: Added a dedicated **"🚀 Software Updates & Releases"** card in the Settings → About tab. Check for updates on demand with live status indicators and direct links to release notes.
- **Configurable**: Easily toggle automated launch checks under Settings → General / Startup.

### 🛡️ Leaflet Map Resize & Chromium IPC Crash Hardening
- **Eliminated Map Maximize Freeze**: Fixed a critical crash where maximizing or resizing the map window with dense overlays active (such as Tropospheric Ducting forecasts) caused Chromium V8 to lock up at 100% CPU attempting to serialize the entire cyclic Leaflet `L.Map` object graph across the Mojo IPC bridge.
- **Centralized `run_js()` Execution**: All fire-and-forget JavaScript evaluations now safely append `void 0;`, ensuring sub-5ms viewport resizes and zero memory leakage.

---

## 📦 Previous Highlights (v0.5.0)

### 🏢 Headline Feature: Room Servers (BBS & Mesh Chatrooms)
- **3rd Dedicated Node Type**: MeshCore Room Servers are now first-class citizens alongside Companions and Repeaters across telemetry decoding, database models, and user interfaces.
- **How Room Servers Work**: Room servers advertise via `AdvType.ROOM` (0x03) or identifying tags (`[room]`, `[server]`, `-bbs`). Users can connect and authenticate with `!login <password>`, read public bulletin feeds, execute server commands (`!help`, `!read`, `!info`, `!status`, `!list`), and broadcast room messages.
- **Persistent Saved Credentials**: Passwords can be saved in SQLite (`room_credentials`) with automatic pre-fill and clear `🔑 Saved` status pills.
- **Dedicated Dual-Pane Console**: Added a primary navigation dock button (`btn_rooms`) positioned directly below Contacts/Companions:
  - **Left Pane**: Discovered room servers directory with search filtering, signal quality (SNR/RSSI), GPS positions, and saved-password status.
  - **Top Auth Card**: Host details, map/ADS-B shortcuts, password box with visibility eye toggle (👁️/🔒), Login/Logout controls, and quick action command buttons.
  - **Bottom Message Stream**: Real-time room server bulletin feed with sender styling, timestamps, and interactive message composer.

### 💎 45° Diamond Map Markers & Filter Mode
- **Diamond Geometry**: Room servers render as sharp 45-degree rotated diamonds on the interactive mesh map, distinguishing them from circular companion nodes and tactical radar repeaters.
- **Vibrant Magenta Theme**: Styled in high-contrast magenta (`#FF00FF`) by default with dynamic hover halo effects (`#FF55FF`), fully customizable in Settings (`Settings` → `🎨 Map Visualisation Colors`).
- **Dedicated Node Filter Mode**: Map node type cycler now includes `ROOMS` mode (`ALL` → `CLIENTS` → `REPEATERS` → `ROOMS`) with live count badges.

---

## 📦 Previous Highlights (v0.4.1)

### 📻 Canonical Channel Hash Matching & Firmware Sync
- **Deterministic `#` Canonicalization**: Channels are canonically formatted with a `#` prefix in SQLite storage and the radio driver, aligning SHA-256 channel hashes (`f9` for `#cumbria`, etc.) with over-the-air firmware packets.
- **Dynamic Hardware Slot Allocation**: When sending to or receiving on newly heard channels, the driver dynamically scans and allocates available hardware slots (1–7) or recycles the oldest non-favorite slot without losing messages.
- **Zero-Latency In-Memory Decryption**: Channel switching immediately registers cryptographic channel keys in the packet parser in-memory without waiting on serial locks, allowing instantaneous packet decryption.
- **Non-Blocking Channel Programming**: Hardware radio sync now enforces a 3.5s timeout with local fallback, preventing serial queue lockups and sluggish UI responsiveness during tab navigation.

### 👥 Precision Contact Resolution & Shadowing Fix
- **Eliminated False Substring Masking**: Fixed an issue where repeater nodes with short names (e.g. `M1`, `M6`) shadowed human companion/client nodes with similar names.
- **Strict Resolution Precedence**: Contact queries now follow an exact hierarchy (`Exact Node ID` → `Exact Alias` → `Public Key` → `Prefix Match` → `Substring Match`), prioritizing companion contacts over repeaters.
- **Dedicated Repeater Management**: Added explicit repeater flag toggling (`set_contact_repeater_status`) synchronized across both `contacts` and `neighbours` tables.

### 🌐 Out-of-the-Box RF Traffic & Map Overlay Resilience
- **Traffic Routes Defaulted ON**: Real-time packet routing paths (`show_paths`) are now enabled by default and linked to the primary RF Observer / Watcher mode (`show_rf_links`).
- **Overlay & Dock Hardening**: Resolved channel switching edge cases across the map HUD, chat streams, and sidebar channel lists.

---

## 📦 Previous Highlights (v0.4.0)

- **Procedural User Avatars & Style Switcher**: Over 180,000 deterministic Cyberpunk Radio Droid faces and bilateral Cyber Initials framed in circuit brackets across 10 neon palettes.
- **Tactical Radar Repeaters**: Infrastructure repeater nodes receive procedural radar constellation avatars with concentric range rings and orbital satellite blips.
- **Map Loading HUD & Startup Controls**: Redesigned centered map initialization screen with 36px emerald spinner and stage progress bar.
- **Sleek Vector Line Navigation**: High-contrast white line vector icons for Observer traffic, Heatmap, Thunderstorms, ADS-B, and Space Weather.
