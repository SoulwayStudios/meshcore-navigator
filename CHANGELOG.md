# Changelog

All notable changes to **MESHCORE NAVIGATOR** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows semantic versioning with automated build increments:
- **Patch (+0.0.1)**: Routine bug fixes, UI adjustments, maintenance, and regular GitHub commits.
- **Minor (+0.1.0)**: Substantial new features and architectural additions.

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
