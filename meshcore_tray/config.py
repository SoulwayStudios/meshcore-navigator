"""Configuration management with JSON persistence for MeshCore Pixoo Tray."""

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("meshcore_tray.config")

def get_app_dir() -> Path:
    """Returns platform-appropriate config and data directory."""
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "meshcore-navigator"
    return Path.home() / ".config" / "meshcore-tray"


CONFIG_DIR = get_app_dir()
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
    radio_preset: str = "UK Narrow (869.618 MHz, 62.5 kHz, SF8, CR 4/5)"
    frequency_mhz: float = 869.618
    bandwidth_khz: float = 62.5
    spreading_factor: int = 8
    coding_rate: str = "4/5"
    tx_power_dbm: int = 22
    latitude: Optional[float] = 54.65897
    longitude: Optional[float] = -3.4346
    map_center_lat: Optional[float] = None
    map_center_lon: Optional[float] = None
    map_zoom: Optional[int] = None
    node_freshness_fading: bool = True
    map_show_path_modes: bool = False
    map_show_companion_orbitals: bool = False
    map_show_scopes: bool = False
    map_show_adsb: bool = False
    map_show_rf_los: bool = False
    map_show_space_weather: bool = False
    map_show_packet_hud: bool = False
    map_show_activity_timeline: bool = True
    map_timeline_scope_hours: int = 24
    space_weather_opacity: float = 0.60
    space_weather_poll_interval_min: int = 15
    map_base_layer: str = "canvas"  # "corescope", "canvas", or "topo"
    carto_api_key: str = ""  # Free key from https://carto.com/basemaps/apikey for CoreScope Dark tiles
    map3d_api_key: str = ""  # Optional API key for custom 3D vector tile provider (MapTiler, Mapbox, etc.)
    map3d_custom_style_url: str = ""  # Optional custom 3D MapLibre style JSON URL (defaults to OpenFreeMap dark)
    adsb_radius_nm: int = 50
    adsb_target_node_id: str = ""
    adsb_target_alias: str = ""
    adsb_filter_categories: List[str] = field(default_factory=lambda: ["airliner", "light", "military", "helicopter", "glider", "general"])
    adsb_alert_enabled: bool = True
    adsb_alert_categories: List[str] = field(default_factory=lambda: ["military"])
    adsb_alert_radius_mi: float = 10.0
    path_hash_mode: int = 1  # 0 = 1-Byte Path, 1 = 2-Byte Multibyte Path, 2 = 3-Byte Multibyte Path
    autoadd_contacts: bool = True
    advert_loc_policy: int = 0  # 0 = Precise GPS, 1 = Approximate, 2 = Private / None
    multi_acks: bool = False
    rx_delay_ms: int = 0
    # Hardware contact management for radio flash memory
    auto_prune_hardware_contacts: bool = True
    hardware_contact_limit: int = 64
    hardware_prune_threshold: int = 52
    hardware_prune_target_free: int = 15
    map_new_nodes_timeframe_hours: int = 72


@dataclass
class AppColors:
    favorite_channel_color: str = "#AA55FF"
    favorite_user_color: str = "#AA55FF"
    send_button_color: str = "#00FF7F"
    send_button_text_color: str = "#000000"
    new_messages_bar_color: str = "#00FF7F"
    map_repeater_color: str = "#3B82F6"
    map_repeater_hover_color: str = "#60A5FA"
    map_companion_color: str = "#06B6D4"
    map_companion_hover_color: str = "#22D3EE"
    map_room_server_color: str = "#A855F7"
    map_room_server_hover_color: str = "#C084FC"
    map_watcher_line_start: str = "#AA55FF"
    map_watcher_line_end: str = "#67397A"
    map_message_line_start: str = "#00FFFF"
    map_message_line_end: str = "#00FF7F"
    map_dot_size: float = 6.4
    radio_connected_color: str = "#00FF7F"
    sync_status_color: str = "#00FF7F"
    map_watcher_status_color: str = "#AA55FF"
    message_snr_color: str = "#AA55FF"
    # Route Visualisation Colors
    map_visualised_path_color: str = "#FF00FF"
    map_visualised_heading_color: str = "#FF00FF"
    map_phantom_path_color: str = "#FFFF00"
    map_unknown_path_color: str = "#EF4444"
    map_no_gps_path_color: str = "#000000"
    map_orbital_repeater_color: str = "#FFD335"
    # ADS-B Aircraft Flight Color Scheme Settings
    adsb_color_mode: str = "altitude"  # "altitude", "type", "distance"
    # Altitude Scheme Colors
    adsb_alt_ground: str = "#FF00FF"    # Lowest / Ground (<2k ft)
    adsb_alt_low: str = "#FF0000"       # Sub 7k ft (<7k ft)
    adsb_alt_mid: str = "#0000FF"       # 10k - 25k ft
    adsb_alt_high: str = "#FFFFFF"      # Cruise (>25k ft)
    # Aircraft Type Scheme Colors
    adsb_type_airliner: str = "#FFFFFF"   # Commercial Airliner
    adsb_type_light: str = "#0000FF"      # Light Aircraft / General Aviation
    adsb_type_military: str = "#00FF00"   # Military Fast Jet / Transport
    adsb_type_helicopter: str = "#FFFF00" # Helicopter
    adsb_type_glider: str = "#FF00FF"     # Glider / Sailplane
    # Distance Ramp Scheme Colors (relative to monitoring center)
    adsb_dist_close: str = "#FF0000"      # Closest (0 - 25% radius)
    adsb_dist_mid_close: str = "#FFA500"  # Mid-Close (25% - 50% radius)
    adsb_dist_mid_far: str = "#FFFF00"    # Mid-Far (50% - 75% radius)
    adsb_dist_far: str = "#00FF00"        # Far (75% - 100%+ radius)


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
    })
    show_live_mirror: bool = False
    show_direct_messages: bool = False


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
    api_token: str = ""


@dataclass
class SatelliteConfig:
    enabled: bool = False
    active_groups: List[str] = field(default_factory=lambda: ["stations", "amateur", "weather"])
    update_interval_hours: int = 24
    min_pass_elevation_deg: int = 15
    show_ground_tracks: bool = True
    show_footprints: bool = True
    selected_satellites: List[str] = field(default_factory=lambda: ["25544", "25338", "28654"])  # ISS, NOAA-15, NOAA-18
    custom_tle_url: str = ""


@dataclass
class MqttConfig:
    enabled: bool = True
    broker_host: str = "mqtt.ukmesh.com"
    broker_port: int = 443
    username: str = "soulway"
    password: str = "ZM3d2A94ZBu5btbK"
    use_tls: bool = True
    transport: str = "websockets"  # "tcp" or "websockets"
    ws_path: str = "/mqtt"
    client_id: str = ""
    subscribe_topics: List[str] = field(default_factory=lambda: ["public/+/+/packets", "public/#"])
    publish_enabled: bool = False
    publish_topic: str = "meshcore/packets"
    dedup_window_secs: float = 5.0
    preset_name: str = "🇬🇧 UKMesh Network"


@dataclass
class AppConfig:
    meshcore: MeshcoreConfig = field(default_factory=MeshcoreConfig)
    satellites: SatelliteConfig = field(default_factory=SatelliteConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)

    pixoo_colors: PixooColors = field(default_factory=PixooColors)
    quiet_hours: PixooQuietHours = field(default_factory=PixooQuietHours)
    telemetry: PixooTelemetryConfig = field(default_factory=PixooTelemetryConfig)
    neighbours: PixooNeighboursConfig = field(default_factory=PixooNeighboursConfig)
    pixoo: PixooDeviceConfig = field(default_factory=PixooDeviceConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    app_colors: AppColors = field(default_factory=AppColors)
    default_app_colors: Dict[str, Any] = field(default_factory=dict)
    favorite_channels: List[str] = field(default_factory=list)
    favorite_users: List[str] = field(default_factory=list)
    favorites: List[str] = field(default_factory=list)
    blocked_users: List[str] = field(default_factory=list)
    phantom_nodes: List[str] = field(default_factory=list)
    channel_groups: Dict[str, str] = field(default_factory=dict)
    collapsed_channel_groups: List[str] = field(default_factory=list)
    channel_order: List[str] = field(default_factory=list)
    group_order: List[str] = field(default_factory=list)
    last_active_channel: str = "Public"
    first_run_completed: bool = False
    window_maximized: bool = False
    window_width: int = 1380
    window_height: int = 800
    show_splash_screen: bool = True
    check_updates_on_startup: bool = True
    show_chat_avatars: bool = True
    user_avatar_style: str = "droid"  # "droid" (Cyberpunk Radio Droid) or "letters" (Decorated 2-letter Initials)
    map_base_layer: str = "canvas"  # "corescope", "canvas", or "topo"
    carto_api_key: str = ""  # Free key from https://carto.com/basemaps/apikey for CoreScope Dark tiles
    map3d_api_key: str = ""  # Optional API key for custom 3D vector tile provider (MapTiler, Mapbox, etc.)
    map3d_custom_style_url: str = ""  # Optional custom 3D MapLibre style JSON URL (defaults to OpenFreeMap dark)

    def is_channel_favorite(self, channel_name: str) -> bool:
        if not channel_name:
            return False
        clean = channel_name.lstrip("#").lower()
        return any(clean == c.lstrip("#").lower() for c in self.favorite_channels) or any(clean == c.lstrip("#").lower() for c in self.favorites)

    def set_channel_favorite(self, channel_name: str, is_fav: bool):
        if not channel_name:
            return
        clean = channel_name.lstrip("#")
        clean_lower = clean.lower()
        if is_fav:
            if not any(c.lstrip("#").lower() == clean_lower for c in self.favorite_channels):
                self.favorite_channels.append(clean)
            if not any(c.lstrip("#").lower() == clean_lower for c in self.favorites):
                self.favorites.append(clean)
        else:
            self.favorite_channels = [c for c in self.favorite_channels if c.lstrip("#").lower() != clean_lower]
            self.favorites = [c for c in self.favorites if c.lstrip("#").lower() != clean_lower]
        self.save()

    def is_user_favorite(self, user_id: str, alias: str = "") -> bool:
        uid = (user_id or "").lower()
        al = (alias or "").lower()
        for fav in self.favorite_users:
            f_clean = fav.lower().lstrip("!@")
            if uid and (uid == f_clean or uid == f"!{f_clean}"):
                return True
            if al and al == f_clean:
                return True
        return False

    def is_phantom_node(self, identifier_or_alias: str) -> bool:
        if not identifier_or_alias:
            return False
        clean = identifier_or_alias.strip().lstrip("!@").lower()
        for p in self.phantom_nodes:
            p_clean = p.strip().lstrip("!@").lower()
            if clean == p_clean or (len(clean) >= 2 and p_clean.startswith(clean)) or (len(p_clean) >= 2 and clean.startswith(p_clean)):
                return True
        return False

    def mark_phantom_node(self, identifier_or_alias: str):
        if not identifier_or_alias:
            return
        clean = identifier_or_alias.strip()
        if not self.is_phantom_node(clean):
            self.phantom_nodes.append(clean)
            self.save()

    def unmark_phantom_node(self, identifier_or_alias: str):
        if not identifier_or_alias:
            return
        clean = identifier_or_alias.strip().lstrip("!@").lower()
        self.phantom_nodes = [
            p for p in self.phantom_nodes
            if p.strip().lstrip("!@").lower() != clean
        ]
        self.save()

    def get_channel_group(self, channel_name: str) -> str:
        clean = channel_name.strip().lstrip("#").lower()
        for k, v in self.channel_groups.items():
            if k.strip().lstrip("#").lower() == clean:
                return v.strip()
        return "Channels"

    def set_channel_group(self, channel_name: str, group_name: str):
        clean = channel_name.strip().lstrip("#")
        if not group_name or group_name.strip().upper() in ("CHANNELS", "DEFAULT"):
            self.channel_groups = {k: v for k, v in self.channel_groups.items() if k.strip().lstrip("#").lower() != clean.lower()}
        else:
            self.channel_groups[clean] = group_name.strip()
        self.save()

    def get_channel_order(self) -> List[str]:
        return list(self.channel_order)

    def set_channel_order(self, order: List[str]):
        cleaned = []
        for c in order:
            cl = str(c).strip().lstrip("#")
            if cl and cl.lower() not in [x.lower() for x in cleaned]:
                cleaned.append(cl)
        self.channel_order = cleaned
        self.save()

    def get_group_order(self) -> List[str]:
        return list(self.group_order)

    def set_group_order(self, order: List[str]):
        cleaned = []
        for g in order:
            gl = str(g).strip()
            if gl and gl.lower() not in [x.lower() for x in cleaned]:
                cleaned.append(gl)
        self.group_order = cleaned
        self.save()

    def is_group_collapsed(self, group_name: str) -> bool:
        gn = group_name.strip().upper()
        return gn in [g.strip().upper() for g in self.collapsed_channel_groups]

    def toggle_group_collapsed(self, group_name: str) -> bool:
        gn = group_name.strip().upper()
        current = [g.strip().upper() for g in self.collapsed_channel_groups]
        if gn in current:
            self.collapsed_channel_groups = [g for g in self.collapsed_channel_groups if g.strip().upper() != gn]
            collapsed = False
        else:
            self.collapsed_channel_groups.append(group_name.strip())
            collapsed = True
        self.save()
        return collapsed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        config = cls()
        if "meshcore" in data:
            config.meshcore = MeshcoreConfig(**{k: v for k, v in data["meshcore"].items() if k in MeshcoreConfig.__dataclass_fields__})
        if "app_colors" in data:
            config.app_colors = AppColors(**{k: v for k, v in data["app_colors"].items() if k in AppColors.__dataclass_fields__})
            if getattr(config.app_colors, "map_orbital_repeater_color", None) == "#FFD700":
                config.app_colors.map_orbital_repeater_color = "#FFD335"
        if "default_app_colors" in data and isinstance(data["default_app_colors"], dict):
            config.default_app_colors = dict(data["default_app_colors"])
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
        if "satellites" in data:
            config.satellites = SatelliteConfig(**{k: v for k, v in data["satellites"].items() if k in SatelliteConfig.__dataclass_fields__})
        if "mqtt" in data:
            config.mqtt = MqttConfig(**{k: v for k, v in data["mqtt"].items() if k in MqttConfig.__dataclass_fields__})
            if "ukmesh.com" in config.mqtt.broker_host.lower():
                # Ensure UKMesh uses public/ topic tree
                if not any("public/" in t for t in config.mqtt.subscribe_topics):
                    config.mqtt.subscribe_topics = ["public/+/+/packets", "public/#"]

        if "favorite_channels" in data:
            config.favorite_channels = list(data["favorite_channels"])
        if "favorite_users" in data:
            config.favorite_users = list(data["favorite_users"])
        if "favorites" in data:
            config.favorites = list(data["favorites"])
        if "blocked_users" in data:
            config.blocked_users = list(data["blocked_users"])
        if "phantom_nodes" in data:
            config.phantom_nodes = list(data["phantom_nodes"])
        if "channel_groups" in data:
            config.channel_groups = dict(data["channel_groups"])
        if "collapsed_channel_groups" in data:
            config.collapsed_channel_groups = list(data["collapsed_channel_groups"])
        if "channel_order" in data:
            config.channel_order = list(data["channel_order"])
        if "group_order" in data:
            config.group_order = list(data["group_order"])
        if "first_run_completed" in data:
            config.first_run_completed = bool(data["first_run_completed"])
        if "window_maximized" in data:
            config.window_maximized = bool(data["window_maximized"])
        if "window_width" in data:
            config.window_width = int(data["window_width"])
        if "window_height" in data:
            config.window_height = int(data["window_height"])
        if "last_active_channel" in data:
            config.last_active_channel = str(data["last_active_channel"])
        if "show_splash_screen" in data:
            config.show_splash_screen = bool(data["show_splash_screen"])
        if "check_updates_on_startup" in data:
            config.check_updates_on_startup = bool(data["check_updates_on_startup"])
        if "show_chat_avatars" in data:
            config.show_chat_avatars = bool(data["show_chat_avatars"])
        if "user_avatar_style" in data:
            config.user_avatar_style = str(data["user_avatar_style"])
        if "map_base_layer" in data:
            config.map_base_layer = str(data["map_base_layer"])
            config.meshcore.map_base_layer = config.map_base_layer
        elif hasattr(config.meshcore, "map_base_layer") and config.meshcore.map_base_layer:
            config.map_base_layer = config.meshcore.map_base_layer

        if "carto_api_key" in data:
            config.carto_api_key = str(data["carto_api_key"])
            config.meshcore.carto_api_key = config.carto_api_key
        elif hasattr(config.meshcore, "carto_api_key") and config.meshcore.carto_api_key:
            config.carto_api_key = config.meshcore.carto_api_key

        if "map3d_api_key" in data:
            config.map3d_api_key = str(data["map3d_api_key"])
            config.meshcore.map3d_api_key = config.map3d_api_key
        elif hasattr(config.meshcore, "map3d_api_key") and config.meshcore.map3d_api_key:
            config.map3d_api_key = config.meshcore.map3d_api_key

        if "map3d_custom_style_url" in data:
            config.map3d_custom_style_url = str(data["map3d_custom_style_url"])
            config.meshcore.map3d_custom_style_url = config.map3d_custom_style_url
        elif hasattr(config.meshcore, "map3d_custom_style_url") and config.meshcore.map3d_custom_style_url:
            config.map3d_custom_style_url = config.meshcore.map3d_custom_style_url
        return config

    def save(self, filepath: Optional[Path] = None):
        target_path = filepath if filepath is not None else CONFIG_FILE
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
                f.flush()
            import os
            os.replace(tmp_path, target_path)
            logger.info(f"Saved configuration to {target_path}")
        except Exception as e:
            logger.error(f"Failed to save configuration to {target_path}: {e}")

    @classmethod
    def load(cls, filepath: Optional[Path] = None) -> "AppConfig":
        target_path = filepath if filepath is not None else CONFIG_FILE
        if not target_path.exists():
            config = cls()
            config.save(target_path)
            return config
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load config from {target_path}, using defaults: {e}")
            return cls()
