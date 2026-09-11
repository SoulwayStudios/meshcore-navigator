# ⚡ MESHCORE NAVIGATOR

> **The modern desktop station, interactive RF mesh map, multi-hop packet tracer, and LED matrix engine for MeshCore & Heltec V3 LoRa radios.**  
> **Created by Nicky Proniewicz - M7NCY**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform: Linux | Windows](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-brightgreen.svg)]()
[![Buy Me A Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-ffdd00.svg?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/m7ncy)

---

> ### ⚠️ Early Development Notice
> **MESHCORE NAVIGATOR is very early in development. This app will have bugs and problems.**  
> This app is developed with AI assistance by a professional working designer.  
> 
> If you like what you see so far, and want to help the development of this app, how about **[buying me a coffee](https://buymeacoffee.com/m7ncy)**?  
> Feel free to give feedback and report any issues or suggestions on the [GitHub Issues](https://github.com/SoulwayStudios/meshcore-navigator/issues) page!

---

## 📖 Table of Contents
- [✨ Key Features](#-key-features)
- [📥 Installation & Quick Start](#-installation--quick-start)
  - [Linux (One-Click Installer)](#linux-one-click-installer)
  - [Linux (Portable Tarball)](#linux-portable-tarball)
  - [Windows](#windows)
  - [Running from Source](#running-from-source)
- [📡 Radio Connection & Hardware Setup](#-radio-connection--hardware-setup)
- [🗺️ Map Overlays & Weather Systems](#️-map-overlays--weather-systems)
- [💬 Application Views](#-application-views)
- [🎨 Themes & UI Color Customization](#-themes--ui-color-customization)
- [🖼️ Divoom Pixoo 64 Integration](#️-divoom-pixoo-64-integration)
- [🌐 Local REST API & SDR Bridge](#-local-rest-api--sdr-bridge)
- [☕ Support the Developer](#-support-the-developer)

---

## ✨ Key Features

- **🗺️ High-Performance Interactive Leaflet Map**:
  - GPU-accelerated HTML5 Canvas rendering for hundreds of nodes and live RF routes.
  - Multi-hop packet path visualisation tracing exact repeaters and zero-hop transmissions.
  - Real-time **Tropospheric Ducting Forecasts** (VHF/UHF propagation conditions).
  - Live **Thunderstorm & Lightning Tracking** with RainViewer radar overlays and Blitzortung lightning strikes.
  - **ADS-B Aircraft Tracking** around nodes with flight altitude color-coding and live flight trails.
  - **Node Activity Heatmap** (1h, 6h, 24h) identifying busy repeaters.
- **💬 Modern Discord-Style Architecture**:
  - Foldable channel categories, unread message badges, favorites system (`★`), and direct messaging.
  - **Power Composer**: Instant `#channel` routing, `@contact` autocomplete ranked by recent RF activity, slash commands (`/join`, `/switch`), and full-text SQLite history search (`? query`).
  - **Heard RF Floods View**: Dedicated flood monitoring panel with hover-to-visualise route trajectories on the map.
- **📡 MeshCore Radio Controller**:
  - Full LoRa frequency, bandwidth, spreading factor, coding rate, and transmit power configuration.
  - One-click zero-hop and flood-routed node adverts broadcast.
  - Auto-detection of CP210x, CH340, FTDI, and native USB serial chips.
- **🖼️ Divoom Pixoo 64 Matrix Integration**:
  - Real-time 64x64 pixel animations for incoming chat messages with vertical and marquee scrolling.
  - Periodic Radio Telemetry screens and Heard Neighbours SNR signal bars.
- **⚙️ Integrated Full-Width Settings**:
  - Embedded settings suite with live auto-saving color swatches and one-click Theme Presets.

---

## 📥 Installation & Quick Start

### Linux (One-Click Installer)

Clone the repository and run the automated installer:
```bash
git clone --recursive https://github.com/SoulwayStudios/meshcore-navigator.git
cd meshcore-navigator
chmod +x install.sh
./install.sh
```
`install.sh` configures a Python virtual environment, installs dependencies, and creates a desktop menu shortcut with high-resolution app icon. You can then launch **MESHCORE NAVIGATOR** directly from your application launcher or by typing `meshcore-navigator`.

### Linux (Portable Tarball)
Download `meshcore-navigator-v0.0.2-linux.tar.gz` from GitHub Releases:
```bash
tar -xzf meshcore-navigator-v0.0.2-linux.tar.gz
cd meshcore-navigator-v0.0.2-linux
./run.sh
```

### Windows
1. Download **`meshcore-navigator-windows-x64.zip`** from [Releases](https://github.com/SoulwayStudios/meshcore-navigator/releases).
2. Extract the ZIP archive anywhere on your PC.
3. Double-click **`MESHCORE-NAVIGATOR.exe`**.

### Running from Source
Ensure Python 3.10+ is installed:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -e .
python3 -m meshcore_tray.main
```
To launch in offline simulation mode without hardware:
```bash
python3 -m meshcore_tray.main --mock
```

---

## 📡 Radio Connection & Hardware Setup

1. Connect your **Heltec V3**, **T-Beam**, or compatible MeshCore node via USB.
2. Open **Settings** (click the `⚡` icon at the top of the left dock).
3. Under **Radio & Node**:
   - **Serial Port**: Leave as `auto` for automatic radio discovery, or pick your specific port (`/dev/ttyUSB*`, `/dev/ttyACM*` on Linux, `COM3` on Windows).
   - **Baud Rate**: `115200` (standard for MeshCore firmware).
   - **Connection Mode**: Choose `USB Serial (COM / tty)` or `Bluetooth Low Energy (BLE)`.
   - **LoRa Frequency & Regional Presets**: Select presets such as `UK Narrow (869.618 MHz)`, `EU868`, or `US915`.
   - Click **⚡ Program Radio Node Now** to dispatch parameters over serial to your device.

---

## 🗺️ Map Overlays & Weather Systems

The 9 left-dock map buttons toggle specialized visual layers:

| Icon | Overlay Name | Description |
| :---: | :--- | :--- |
| `🌐` | **Node Type** | Cycle filter between Companion nodes, Repeaters, Rooms, or All. |
| `⚡` | **Observer Traffic** | Visualises observed RF packets and trajectory lines heard by other stations. |
| `🛣️` | **Path Modes** | Color-codes node markers by hop mode (1-Byte cyan, 2-Byte purple, 3-Byte magenta, Flood blue). |
| `🔥` | **Node Activity Heatmap** | Colors repeaters based on message traffic volume with 1h, 6h, and 24h timeframe toggles. |
| `🛰️` | **Orbital View** | Displays companion nodes docked or hovering in orbital radius around repeaters. |
| `📶` | **Tropo Forecast View** | Live VHF/UHF tropospheric ducting forecasts from F5LEN for long-distance DX tracking. |
| `⛈️` | **Thunderstorm View** | Real-time RainViewer Doppler precipitation radar and live Blitzortung lightning strikes. |
| `✈️` | **ADSB View** | Live aircraft positions, altitudes, and radar sweeps around mesh nodes using OpenCommunityFeeds. |
| `🌐` | **Scope View** | Visualises regional frequency scopes and channel zones. |

> **Map Floating Controls**: Use the on-map buttons in the top-right corner to **📍 Re-center** on your coordinates, **🧹 Reset Layers**, and toggle **⏳ Age Fade** (dimming stale nodes over time).

---

## 💬 Application Views

- **💬 Main View**: Live chat channel stream, channel management sidebar, interactive map, and collapsible Pixoo mirror preview.
- **🌊 Heard Floods View**: Comprehensive log of flood-routed packets with hop counts, SNR, and path tracing. Hover over any entry to trace its multi-hop path on the map.
- **👥 Direct Messages & Contacts**: Private 1-to-1 conversations, node discovery, contact aliasing, and quick map locate.
- **📡 Repeaters & Infrastructure**: Command console for issuing repeater commands, tracking battery telemetry, and displaying neighbour topologies.
- **⚙️ Embedded Settings**: Full-width settings suite covering Radio, Matrix, Channels, UI Colors, Watched Word Alerts, and Gateway.

---

## 🎨 Themes & UI Color Customization

Navigate to **Settings ➔ App UI Colors**:
- Customize colors for Favorite Channels, Send Button, Channel Header, Node Markers, Visualised Packet Routes, and Phantom Nodes.
- Changes save in real-time and apply across the UI immediately.
- Use **`💾 Save Theme`**, **`⭐ Set Current Theme as Default`**, or **`↺ Reset to Default Theme`** to preserve and restore your custom look.

---

## 🖼️ Divoom Pixoo 64 Integration

Configure your Divoom Pixoo 64 under **Settings ➔ Pixoo Integration**:
- Enter the device IP address.
- Adjust brightness, alert duration, and strobe flash cycles.
- Enable or disable specific channels from displaying on the LED matrix.
- Configure Quiet Hours (e.g. `22:00` to `07:00`) to automatically dim or silence alerts at night.

---

## 🌐 Local REST API & SDR Bridge

MESHCORE NAVIGATOR features an integrated HTTP JSON REST bridge listening on `http://127.0.0.1:18680`:
- `GET /api/v1/status`: Radio status, current node alias, and statistics.
- `GET /api/v1/messages?channel=public`: Retrieve channel messages.
- `POST /api/v1/messages`: Dispatch messages programmatically from external scripts, RTL-SDR bridges, or APRS gateways.

---

## ☕ Support the Developer

MESHCORE NAVIGATOR is developed and maintained as an open-source amateur radio tool by **Nicky Proniewicz (M7NCY)**.

If you find this application helpful for your MeshCore station and repeater network, consider supporting development:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20M7NCY-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/m7ncy)

---

**73 de M7NCY**
