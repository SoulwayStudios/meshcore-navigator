> [!WARNING]
> **Early Development Notice**: MESHCORE NAVIGATOR is under active community development. Bug reports, diagnostic logs, and feature requests are very welcome!
> If you find MESHCORE NAVIGATOR useful, consider supporting development on [Buy Me a Coffee](https://buymeacoffee.com/soulwaystudios).

---

## 🌟 What's New in v0.4.0

### 🤖 Procedural User Avatars & Style Switcher
- **Unique Deterministic Identities**: Never confuse users with identical initials again! Avatars are procedurally generated from each contact's node ID and name across 10 cyberpunk neon palettes.
- **Style C: Cyberpunk Radio Droid (Robots)**: Over 180,000 distinct combinations of retro radio droid faces, complete with antennas, chassis variations, audio grilles, and optic visors with independent eye hues and heterochromia.
- **Style A: Cyber Initials (Decorated Circuit Frame)**: For users who prefer classic letters, bold 2-letter uppercase initials are framed by procedural bilateral circuit brackets, solder pads, and reticle ticks.
- **Switchable in Settings**: Choose your preferred user avatar style in `Settings` → `💬 Chat Settings` → `👤 User Contact Avatar Style` (`🤖 Cyberpunk Radio Droids` or `🔤 Cyber Initials`).
- **Chat Stream Avatars**: Message bubbles in the chat stream display sender avatars (2 lines high, inside the bubble card) with incoming avatars on the left and outgoing on the right. Can be toggled on/off in Settings.

### 📡 Tactical Radar Repeaters
- **Infrastructure Identity**: Repeater nodes automatically receive a dedicated **Tactical Radar Constellation** avatar featuring concentric range rings, deterministic orbital satellite blips, and unique beacon tower colors.
- **Enlarged Repeater Console Header**: The repeater inspector header now features an enlarged 54×54px radar badge spanning the full header height.

### 🗺️ Map Loading HUD & Startup Controls
- **Centered Map Loading Screen**: Redesigned map loading HUD with an enlarged 36px emerald braille spinner centered above the initialization title, preventing text clipping.
- **Staggered Initialization Bar**: Visual stage indicators (`1 ENGINE`, `2 VIEWPORT`, `3 NODES`, `4 RADIO`) with percentage progress.
- **Splash Screen Setting**: Added a toggle in `Settings` → `🖥️ Application Startup & Behavior` to disable the full-screen splash cover on startup, letting you watch the map and its loading progress directly.

### 🎨 Sleek Vector Line Navigation & Toolbar Icons
- **Crisp White Line Icons**: Primary action dock and secondary map overlay toolbar upgraded to matching vector line art that illuminates with emerald neon when active:
  - 👁️ **Observer / Watcher Traffic**: Clean vector eye icon (now defaulted to ON on startup).
  - 🔥 **Node Activity Heatmap**: White vector flame icon.
  - ⛈️ **Thunderstorms**: Vector cloud with lightning bolt.
  - ✈️ **ADS-B Air Traffic**: Symmetrical top-down airplane vector.
  - 🌌 **Space Weather**: Vertical wavy aurora borealis ribbons.
- **Rich Contact Tooltips**: Hovering over favorited user icons in the left navigation dock now displays full alias, node ID, status, signal metrics, hop distance, and coordinates.

---

## 📦 Previous Highlights (v0.3.x)

- **NOAA Space Weather & OVATION Aurora Forecast**: Live SWPC planetary Kp-index, solar flux, magnetic vectors, and real-time auroral precipitation oval map layer.
- **Linux Wayland & Multi-Monitor Hardening**: Vulkan compositor fallback, renderer watchdog protection, and debounced window geometry transitions.
- **Coordinate Sanitization**: Automatic rejection of corrupted RF coordinates and automated database startup self-repair.
- **Clean Tray Exit**: Orderly shutdown lifecycle ensuring serial radio driver and Pixoo display thread parking with atomic SQLite backups.

