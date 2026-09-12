> [!WARNING]
> **Early Development Notice**: MESHCORE NAVIGATOR is under active community development. Bug reports, diagnostic logs, and feature requests are very welcome!
> If you find MESHCORE NAVIGATOR useful, consider supporting development on [Buy Me a Coffee](https://buymeacoffee.com/soulwaystudios).

---

## 🌟 What's New in v0.4.1

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
