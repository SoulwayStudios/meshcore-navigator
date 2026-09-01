# Heltec V3 MeshCore System Tray App & Divoom Pixoo 64 Integration

A modern, highly extensible Linux desktop system tray application and background service interfacing with a **Heltec V3 LoRa node** running MeshCore Companion firmware and rendering custom real-time pixel animations onto a **Divoom Pixoo 64** display.

---

## Key Features

1. **Linux System Tray Modal & Badges**:
   - `QSystemTrayIcon` with connection status indicator, unread message badges, desktop balloons, and quick-toggle menu.
2. **Keyboard-First Power Composer**:
   - **Quick Channel Routing**: Type `#channelname <message>` (e.g. `#public Radio check`) to dispatch directly to `#public` without leaving your current view.
   - **Quick Direct Messaging**: Type `@username <message>` (e.g. `@Alice Status check`) to send a DM.
   - **Smart Autocomplete**: `#` and `@` popup suggestions prioritized and ranked by **most recent message activity**!
   - **Slash (`/`) Channel Switcher**: Type `/pu` to instantly switch to `Public`.
   - **Search Mode (`?`)**: Type `? <query>` (e.g. `? emergency`) for real-time full-text search across SQLite message history.
3. **Custom Pixoo 64 Display Modes (64x64 Grid)**:
   - **New Message Alert (10s)**:
     - 3-row top (`y=0..2`) and bottom (`y=61..63`) green strobe flash (5 cycles).
     - Static top sender banner with gold star (`★`) for favorites.
     - Static bottom channel footer (`#public`, `DM`).
     - Middle viewport (`y=13..50`) with smooth **vertical scrolling** for longer messages.
   - **Idle Mode**:
     - Static top channel header (`#public`).
     - Main stream displaying sender and message with **horizontal marquee scrolling** for long lines.
   - **Radio Telemetry Dashboard**: Periodic screen (every X min) displaying Frequency (`FQ`), Bandwidth (`BW`), Spreading Factor (`SF`), Coding Rate (`CR`), Transmit Power (`TX`), and Noise Floor.
   - **Nearest Neighbours & SNR Screen**: Periodic screen displaying top heard mesh nodes with live SNR values and signal quality bars (with support for querying owned repeaters).
4. **Favorites System (⭐)**:
   - Mark contacts or channels as Favorites; rendered with glowing pixel stars (`★`) on the Pixoo screen.
5. **Configurable Quiet Hours**:
   - Set active time windows (e.g. `22:00` to `07:00`) to mute green strobe flashes, dim brightness, or blackout the Pixoo screen.
6. **Full Color Customization**:
   - Dedicated color pickers with real-time swatches for *Channel Name*, *Alert Flash*, *Message Text*, *Background*, *Sender Name*, and *Favorite Star*.
7. **Channel Filtering**:
   - Enable or disable Pixoo display per channel (e.g. silence `#test` while keeping it visible in the desktop app).
8. **Extensibility & Local Gateway REST API**:
   - Built-in local JSON/REST API (`http://127.0.0.1:18680`) and decoupled EventBus for SDRs (e.g. `rtl_433`), amateur radio tools (APRS), and scripts.

---

## Quick Start

### 1. Installation
Ensure Python 3.10+ and system dependencies are installed:
```bash
./venv/bin/pip install -r requirements.txt
```

### 2. Running the Application
Launch via the helper script:
```bash
./run.sh
```

Or run directly with Python:
```bash
./venv/bin/python -m meshcore_tray.main
```

To run in forced simulation mode (without hardware attached):
```bash
./run.sh --mock
```

---

## Power Composer Shortcuts & Grammar

| Input Syntax | Action |
| :--- | :--- |
| `Hello world!` | Sends message to currently open channel or direct message |
| `#public Signal check 5/9` | Dispatches message to `#public` without leaving current view |
| `@Alice Ready for frequency test` | Dispatches direct message to `@Alice` |
| `? emergency` | Searches all message history in SQLite for "emergency" |
| `#pu [Tab / Enter]` | Autocompletes channel `#Public` (ranks most recent channel first) |
| `@Al [Tab / Enter]` | Autocompletes contact `@Alice` |
| `/pu [Enter]` | Fast-switches view to `#Public` |
| `Esc` | Dismisses popup autocomplete or clears search mode |

---

## Extensibility Gateway REST API

When enabled in Settings, external scripts, Node-RED, or SDR decoders can communicate with the app via HTTP:

- **Send Channel Message**:
  ```bash
  curl -X POST http://127.0.0.1:18680/api/send \
       -H "Content-Type: application/json" \
       -d '{"channel": "Public", "text": "Automated beacon from SDR receiver"}'
  ```
- **Fetch Recent Messages**:
  ```bash
  curl http://127.0.0.1:18680/api/messages
  ```
- **Fetch RF Telemetry**:
  ```bash
  curl http://127.0.0.1:18680/api/telemetry
  ```
- **Fetch Nearest Neighbours**:
  ```bash
  curl http://127.0.0.1:18680/api/neighbours
  ```

---

## Running Unit Tests
```bash
./venv/bin/pytest tests/ -v
```
