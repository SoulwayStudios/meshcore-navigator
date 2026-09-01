"""Configuration management with JSON persistence for MeshCore Pixoo Tray."""

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("meshcore_tray.config")

CONFIG_DIR = Path.home() / ".config" / "meshcore-tray"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class MeshcoreConfig:
    serial_port: str = "auto"
    baudrate: int = 115200
    connection_type: str = "serial"  # serial, ble, mock
    ble_address: str = ""
    node_id: str = "!4a2f8b1c"
    node_alias: str = "Heltec-V3"
    auto_reconnect: bool = True
    simulation_mode: bool = False


@dataclass
class PixooColors:
    channel_color: str = "#00FFCC"       # Mint / Cyan
    alert_color: str = "#00FF44"         # Green strobe
    message_color: str = "#FFFFFF"       # White
    background_color: str = "#000000"    # Black
    sender_color: str = "#FFD700"        # Gold / Yellow
    favorite_star_color: str = "#FFEA00" # Radiant Star Yellow


@dataclass
class PixooQuietHours:
    enabled: bool = False
    start_time: str = "22:00"
    end_time: str = "07:00"
    action: str = "mute_flash"  # mute_flash, dim, blackout


@dataclass
class PixooTelemetryConfig:
    enabled: bool = True
    interval_mins: int = 5
    duration_secs: int = 8


@dataclass
class PixooNeighboursConfig:
    enabled: bool = True
    interval_mins: int = 10
    duration_secs: int = 8
    source: str = "local"  # local, repeater
    target_repeater_node_id: str = ""


@dataclass
class PixooDeviceConfig:
    ip_address: str = "192.168.1.150"
    brightness: int = 80
    alert_duration_secs: int = 10
    flash_count: int = 5
    page_duration_secs: int = 30
    vertical_scroll_speed: int = 3
    horizontal_marquee_speed: int = 4
    channel_filters: Dict[str, bool] = field(default_factory=lambda: {
        "Public": True,
        "#general": True,
        "#ops": True,
        "#test": False,  # Filtered out from Pixoo by default
        "#telemetry": True
    })


@dataclass
class NotificationConfig:
    watched_keywords: List[str] = field(default_factory=lambda: [
        "emergency", "alert", "weather", "help", "CQ", "repeater", "beacon"
    ])
    notify_on_node_mentions: bool = True
    play_sound: bool = True
    desktop_notifications: bool = True


@dataclass
class GatewayConfig:
    http_bridge_enabled: bool = True
    http_port: int = 18680


@dataclass
class AppConfig:
    meshcore: MeshcoreConfig = field(default_factory=MeshcoreConfig)
    pixoo_colors: PixooColors = field(default_factory=PixooColors)
    quiet_hours: PixooQuietHours = field(default_factory=PixooQuietHours)
    telemetry: PixooTelemetryConfig = field(default_factory=PixooTelemetryConfig)
    neighbours: PixooNeighboursConfig = field(default_factory=PixooNeighboursConfig)
    pixoo: PixooDeviceConfig = field(default_factory=PixooDeviceConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    favorites: List[str] = field(default_factory=lambda: ["Public"])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        config = cls()
        if "meshcore" in data:
            config.meshcore = MeshcoreConfig(**{k: v for k, v in data["meshcore"].items() if k in MeshcoreConfig.__dataclass_fields__})
        if "pixoo_colors" in data:
            config.pixoo_colors = PixooColors(**{k: v for k, v in data["pixoo_colors"].items() if k in PixooColors.__dataclass_fields__})
        if "quiet_hours" in data:
            config.quiet_hours = PixooQuietHours(**{k: v for k, v in data["quiet_hours"].items() if k in PixooQuietHours.__dataclass_fields__})
        if "telemetry" in data:
            config.telemetry = PixooTelemetryConfig(**{k: v for k, v in data["telemetry"].items() if k in PixooTelemetryConfig.__dataclass_fields__})
        if "neighbours" in data:
            config.neighbours = PixooNeighboursConfig(**{k: v for k, v in data["neighbours"].items() if k in PixooNeighboursConfig.__dataclass_fields__})
        if "pixoo" in data:
            config.pixoo = PixooDeviceConfig(**{k: v for k, v in data["pixoo"].items() if k in PixooDeviceConfig.__dataclass_fields__})
        if "notifications" in data:
            config.notifications = NotificationConfig(**{k: v for k, v in data["notifications"].items() if k in NotificationConfig.__dataclass_fields__})
        if "gateway" in data:
            config.gateway = GatewayConfig(**{k: v for k, v in data["gateway"].items() if k in GatewayConfig.__dataclass_fields__})
        if "favorites" in data:
            config.favorites = list(data["favorites"])
        return config

    def save(self, filepath: Path = CONFIG_FILE):
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            logger.info(f"Saved configuration to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save configuration to {filepath}: {e}")

    @classmethod
    def load(cls, filepath: Path = CONFIG_FILE) -> "AppConfig":
        if not filepath.exists():
            config = cls()
            config.save(filepath)
            return config
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load config from {filepath}, using defaults: {e}")
            return cls()
