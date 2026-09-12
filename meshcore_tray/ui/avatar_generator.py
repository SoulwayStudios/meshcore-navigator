"""Deterministic procedural avatar generator for MeshCore contacts.

Generates unique, reproducible visual identities from (node_id, alias):
- Style C: Cyberpunk Radio Droid (for regular user nodes): 180,000+ distinct combinations
  of head chassis, antennas, ear modules, optic visors, and audio grilles in high-contrast neon palettes.
- Style A: Bilateral Cyber Initials (for regular user nodes): 2-letter uppercase initials framed
  by procedural circuit brackets, solder pads, reticle ticks, and neon cyber palettes.
- Style B: Tactical Radar Constellation (for repeater infrastructure nodes):
  concentric radar rings, center beacon tower, sweep angles, and orbital satellite nodes.
"""

import hashlib
import math
from typing import Dict, Tuple, Optional
from PyQt6.QtCore import QByteArray, QSize, Qt, QRectF
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QRadialGradient, QBrush
from PyQt6.QtSvg import QSvgRenderer

# In-memory LRU cache for rendered QIcon/QPixmap objects to ensure zero UI lag:
# key is (node_id, alias, is_repeater, size, style)
_AVATAR_CACHE: Dict[Tuple[str, str, bool, int, str], QIcon] = {}

# Current global avatar style for user contacts ("droid" or "letters")
_CURRENT_USER_AVATAR_STYLE: str = "droid"


def set_global_avatar_style(style: str):
    """Sets the active avatar style for user contacts ('droid' or 'letters') and flushes cache."""
    global _CURRENT_USER_AVATAR_STYLE
    _CURRENT_USER_AVATAR_STYLE = style if style in ("droid", "letters") else "droid"
    clear_avatar_cache()


def get_global_avatar_style() -> str:
    """Returns the currently active avatar style ('droid' or 'letters')."""
    return _CURRENT_USER_AVATAR_STYLE


def clear_avatar_cache():
    """Flushes the avatar QIcon cache."""
    _AVATAR_CACHE.clear()

# 10 Cyberpunk neon color palettes (primary neon, secondary accent, deep dark background)
CYBER_PALETTES = [
    # 0. Neon Cyan / Electric Teal
    {"primary": "#00F0FF", "secondary": "#38BDF8", "bg": "#0B132B"},
    # 1. Matrix Emerald / Toxic Mint
    {"primary": "#00FF66", "secondary": "#34D399", "bg": "#051C14"},
    # 2. Solar Amber / Cyber Gold (swapped with magenta per user request)
    {"primary": "#FFB703", "secondary": "#FBBF24", "bg": "#221300"},
    # 3. Electric Violet / Ultra Purple
    {"primary": "#A855F7", "secondary": "#C084FC", "bg": "#130924"},
    # 4. Synthwave Magenta / Hot Pink (swapped with gold per user request)
    {"primary": "#FF007F", "secondary": "#E879F9", "bg": "#1C0620"},
    # 5. High-Volt Lime / Acid Yellow
    {"primary": "#CCFF00", "secondary": "#A3E635", "bg": "#121C03"},
    # 6. Laser Red / Blood Orange
    {"primary": "#FF3344", "secondary": "#FB7185", "bg": "#22080C"},
    # 7. Glacier Blue / Ice White
    {"primary": "#38BDF8", "secondary": "#F0F9FF", "bg": "#061A28"},
    # 8. Vaporwave Aqua / Lilac
    {"primary": "#06B6D4", "secondary": "#D8B4FE", "bg": "#0C1425"},
    # 9. Retro Blaze / Tangerine
    {"primary": "#FB923C", "secondary": "#F43F5E", "bg": "#220C12"},
]

# Independent vibrant eye color palette for robot droids
EYE_COLORS = [
    "#00F0FF",  # Neon Cyan
    "#00FF66",  # Matrix Emerald
    "#FF007F",  # Synthwave Magenta
    "#A855F7",  # Electric Violet
    "#FFB703",  # Solar Amber
    "#CCFF00",  # High-Volt Lime
    "#FF3344",  # Laser Red
    "#38BDF8",  # Glacier Blue
    "#FFFFFF",  # Laser White
    "#FB923C",  # Retro Blaze
    "#F43F5E",  # Rose Pink
    "#FBBF24",  # Warm Gold
]

# Repeater tactical palettes
REPEATER_PALETTES = [
    {"primary": "#C084FC", "secondary": "#A855F7", "bg": "#180B2B"},
    {"primary": "#FACC15", "secondary": "#EAB308", "bg": "#241802"},
    {"primary": "#34D399", "secondary": "#10B981", "bg": "#062118"},
    {"primary": "#38BDF8", "secondary": "#0284C7", "bg": "#091D33"},
]

# Distinct tactical colors for center repeater transmission tower symbol
REPEATER_TOWER_COLORS = [
    "#00F0FF",  # Cyan
    "#34D399",  # Emerald
    "#FACC15",  # Yellow / Gold
    "#FF007F",  # Magenta
    "#A855F7",  # Purple
    "#FB923C",  # Orange
    "#38BDF8",  # Sky Blue
    "#FFFFFF",  # Crisp White
    "#F43F5E",  # Rose
    "#22D3EE",  # Aqua
]


def _hash_contact(node_id: str, alias: str) -> int:
    """Derives a deterministic 64-bit integer hash from contact info."""
    clean_id = (node_id or "").strip().lower()
    clean_alias = (alias or "").strip().lower()
    seed = f"{clean_id}:{clean_alias}"
    h_bytes = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(h_bytes[:8], byteorder="big")


def generate_droid_svg(h: int) -> str:
    """Generates procedural SVG markup for Style C: Cyberpunk Radio Droid."""
    palette = CYBER_PALETTES[(h >> 0) % len(CYBER_PALETTES)]
    p = palette["primary"]
    s = palette["secondary"]
    bg = palette["bg"]

    # 1. Head Chassis (8 shapes)
    head_type = (h >> 4) % 8
    if head_type == 0:
        head = f'<rect x="7" y="10" width="18" height="15" rx="4" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 1:
        head = f'<polygon points="9,10 23,10 25,25 7,25" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 2:
        head = f'<rect x="6" y="11" width="20" height="13" rx="2.5" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 3:
        head = f'<polygon points="11,10 21,10 25,17 21,25 11,25 7,17" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 4:
        head = f'<path d="M7 25V17A9 9 0 0 1 25 17V25H7Z" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 5:
        head = f'<polygon points="9,10 23,10 25,12 25,23 23,25 9,25 7,23 7,12" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    elif head_type == 6:
        head = f'<path d="M8 10h16a3 3 0 0 1 3 3v8a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4v-8a3 3 0 0 1 3-3z" fill="{bg}" stroke="{p}" stroke-width="1.8"/>'
    else:
        head = f'<rect x="6" y="9" width="20" height="16" rx="4" fill="{bg}" stroke="{p}" stroke-width="1.8"/><line x1="6" y1="13" x2="26" y2="13" stroke="{s}" stroke-width="1"/>'

    # 2. Antennas (8 styles)
    ant_type = (h >> 7) % 8
    if ant_type == 0:
        antenna = f'<line x1="16" y1="10" x2="16" y2="4" stroke="{p}" stroke-width="1.6"/><circle cx="16" cy="3.5" r="1.8" fill="{s}"/>'
    elif ant_type == 1:
        antenna = f'<line x1="11" y1="10" x2="7" y2="4" stroke="{p}" stroke-width="1.6"/><line x1="21" y1="10" x2="25" y2="4" stroke="{p}" stroke-width="1.6"/><circle cx="7" cy="4" r="1.2" fill="{s}"/><circle cx="25" cy="4" r="1.2" fill="{s}"/>'
    elif ant_type == 2:
        antenna = f'<path d="M16 10V8c1.5 0 1.5-1 0-1s-1.5-1 0-1 1.5-1 0-1V3" stroke="{p}" stroke-width="1.5" fill="none"/>'
    elif ant_type == 3:
        antenna = f'<path d="M13 5a4 4 0 0 1 6 0" stroke="{p}" stroke-width="1.8" fill="none"/><line x1="16" y1="10" x2="16" y2="5" stroke="{p}" stroke-width="1.5"/><circle cx="16" cy="5" r="1.2" fill="{s}"/>'
    elif ant_type == 4:
        antenna = f'<line x1="16" y1="10" x2="16" y2="4" stroke="{p}" stroke-width="1.5"/><line x1="11" y1="4" x2="21" y2="4" stroke="{s}" stroke-width="2" stroke-linecap="round"/>'
    elif ant_type == 5:
        antenna = f'<line x1="12" y1="10" x2="12" y2="6" stroke="{p}" stroke-width="1.4"/><line x1="16" y1="10" x2="16" y2="3" stroke="{s}" stroke-width="1.6"/><line x1="20" y1="10" x2="20" y2="6" stroke="{p}" stroke-width="1.4"/>'
    elif ant_type == 6:
        antenna = f'<polygon points="15 3 13 7 17 7 15 10" fill="{s}" stroke="{p}" stroke-width="0.8"/>'
    else:
        antenna = f'<ellipse cx="16" cy="5" rx="4.5" ry="2" stroke="{s}" stroke-width="1.5" fill="none"/><line x1="16" y1="10" x2="16" y2="7" stroke="{p}" stroke-width="1.5"/>'

    # 3. Ears / Side Modules (6 styles)
    ear_type = (h >> 10) % 6
    if ear_type == 0:
        ears = f'<circle cx="5" cy="17" r="2" fill="{s}" stroke="{p}" stroke-width="0.8"/><circle cx="27" cy="17" r="2" fill="{s}" stroke="{p}" stroke-width="0.8"/>'
    elif ear_type == 1:
        ears = f'<line x1="4" y1="15" x2="7" y2="15" stroke="{s}" stroke-width="1.5"/><line x1="4" y1="18" x2="7" y2="18" stroke="{s}" stroke-width="1.5"/><line x1="25" y1="15" x2="28" y2="15" stroke="{s}" stroke-width="1.5"/><line x1="25" y1="18" x2="28" y2="18" stroke="{s}" stroke-width="1.5"/>'
    elif ear_type == 2:
        ears = f'<polygon points="7 14 3 17 7 20" fill="{s}"/><polygon points="25 14 29 17 25 20" fill="{s}"/>'
    elif ear_type == 3:
        ears = f'<rect x="4" y="15" width="3" height="4" rx="1" fill="{p}"/><rect x="25" y="15" width="3" height="4" rx="1" fill="{p}"/>'
    elif ear_type == 4:
        ears = f'<path d="M5 14v6" stroke="{p}" stroke-width="2.5" stroke-linecap="round"/><path d="M27 14v6" stroke="{p}" stroke-width="2.5" stroke-linecap="round"/>'
    else:
        ears = f'<circle cx="5" cy="15" r="1.5" fill="{p}"/><circle cx="5" cy="19" r="1.5" fill="{s}"/><circle cx="27" cy="15" r="1.5" fill="{p}"/><circle cx="27" cy="19" r="1.5" fill="{s}"/>'

    # 4. Eyes / Visors (8 styles) with independent eye colors
    eye_type = (h >> 13) % 8
    ec = EYE_COLORS[(h >> 19) % len(EYE_COLORS)]
    ec2 = EYE_COLORS[(h >> 23) % len(EYE_COLORS)] if ((h >> 27) % 4 == 0) else ec
    if eye_type == 0:
        eyes = f'<circle cx="11.5" cy="15.5" r="2.2" fill="{ec}"/><circle cx="20.5" cy="15.5" r="2.2" fill="{ec2}"/>'
    elif eye_type == 1:
        eyes = f'<rect x="9" y="14" width="14" height="3" rx="1.5" fill="{ec}" stroke="{p}" stroke-width="0.8"/>'
    elif eye_type == 2:
        eyes = f'<rect x="10" y="14" width="3.5" height="3.5" rx="0.8" fill="{ec}"/><rect x="18.5" y="14" width="3.5" height="3.5" rx="0.8" fill="{ec2}"/>'
    elif eye_type == 3:
        eyes = f'<ellipse cx="16" cy="15.5" rx="5.5" ry="3" fill="none" stroke="{p}" stroke-width="1.4"/><circle cx="16" cy="15.5" r="1.8" fill="{ec}"/>'
    elif eye_type == 4:
        # Winking / Asymmetric
        eyes = f'<circle cx="11.5" cy="15.5" r="2.2" fill="{ec}"/><path d="M18.5 15.5l3-1.5M18.5 15.5l3 1.5" stroke="{ec2}" stroke-width="1.8" stroke-linecap="round"/>'
    elif eye_type == 5:
        eyes = f'<circle cx="11" cy="15.5" r="1.3" fill="{ec}"/><circle cx="13" cy="15.5" r="1.3" fill="{ec}"/><circle cx="19" cy="15.5" r="1.3" fill="{ec2}"/><circle cx="21" cy="15.5" r="1.3" fill="{ec2}"/>'
    elif eye_type == 6:
        eyes = f'<circle cx="11.5" cy="15.5" r="2.2" stroke="{ec}" stroke-width="1" fill="none"/><line x1="11.5" y1="13" x2="11.5" y2="18" stroke="{ec}" stroke-width="1"/><circle cx="20.5" cy="15.5" r="2.2" stroke="{ec2}" stroke-width="1" fill="none"/><line x1="20.5" y1="13" x2="20.5" y2="18" stroke="{ec2}" stroke-width="1"/>'
    else:
        eyes = f'<line x1="10" y1="15.5" x2="13.5" y2="15.5" stroke="{ec}" stroke-width="2.2" stroke-linecap="round"/><line x1="18.5" y1="15.5" x2="22" y2="15.5" stroke="{ec2}" stroke-width="2.2" stroke-linecap="round"/>'

    # 5. Mouths / Grilles (6 styles)
    mouth_type = (h >> 16) % 6
    if mouth_type == 0:
        mouth = f'<line x1="11" y1="21" x2="21" y2="21" stroke="{p}" stroke-width="1.5" stroke-linecap="round"/><line x1="13" y1="23" x2="19" y2="23" stroke="{p}" stroke-width="1.5" stroke-linecap="round"/>'
    elif mouth_type == 1:
        mouth = f'<path d="M12 21c1.2 1.6 2.8 1.6 4 1.6s2.8 0 4-1.6" stroke="{s}" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    elif mouth_type == 2:
        mouth = f'<path d="M11 21.5h2l1-1.8 2 3.6 1-2.5 1 0.7h3" stroke="{s}" stroke-width="1.4" stroke-linecap="round" fill="none"/>'
    elif mouth_type == 3:
        mouth = f'<circle cx="12" cy="21.5" r="1.1" fill="{s}"/><circle cx="16" cy="21.5" r="1.1" fill="{p}"/><circle cx="20" cy="21.5" r="1.1" fill="{s}"/>'
    elif mouth_type == 4:
        mouth = f'<rect x="12" y="20.5" width="8" height="2" rx="1" fill="{s}"/>'
    else:
        mouth = f'<rect x="11" y="20" width="10" height="3" rx="0.5" stroke="{p}" stroke-width="1" fill="none"/><line x1="14" y1="20" x2="14" y2="23" stroke="{p}" stroke-width="0.8"/><line x1="18" y1="20" x2="18" y2="23" stroke="{p}" stroke-width="0.8"/>'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#11141A" stroke="{p}" stroke-width="1.2" />
        {antenna}
        {ears}
        {head}
        {eyes}
        {mouth}
    </svg>"""


def generate_repeater_radar_svg(h: int) -> str:
    """Generates procedural SVG markup for Style B: Tactical Radar Constellation (for Repeaters)."""
    palette = REPEATER_PALETTES[(h >> 0) % len(REPEATER_PALETTES)]
    p = palette["primary"]
    s = palette["secondary"]
    bg = palette["bg"]

    # Sweep ray angle
    angle_deg = (h % 360)
    rad = math.radians(angle_deg)
    x2 = 16.0 + 12.0 * math.cos(rad)
    y2 = 16.0 + 12.0 * math.sin(rad)

    # 3 constellation satellite blips at deterministic orbital angles
    b1_rad = math.radians((h * 7) % 360)
    b1_r = 11.5
    b1_x = 16.0 + b1_r * math.cos(b1_rad)
    b1_y = 16.0 + b1_r * math.sin(b1_rad)

    b2_rad = math.radians((h * 13 + 90) % 360)
    b2_r = 7.0
    b2_x = 16.0 + b2_r * math.cos(b2_rad)
    b2_y = 16.0 + b2_r * math.sin(b2_rad)

    b3_rad = math.radians((h * 19 + 210) % 360)
    b3_r = 11.5
    b3_x = 16.0 + b3_r * math.cos(b3_rad)
    b3_y = 16.0 + b3_r * math.sin(b3_rad)

    # Dash pattern for outer radar ring
    dash = "3 2" if (h % 2 == 0) else "4 1.5"

    # Deterministic transmission tower symbol color per repeater
    tower_col = REPEATER_TOWER_COLORS[(h >> 15) % len(REPEATER_TOWER_COLORS)]

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="{bg}" stroke="{p}" stroke-width="1.2" />
        <!-- Radar concentric rings -->
        <circle cx="16" cy="16" r="12" stroke="{p}" stroke-width="1.3" stroke-dasharray="{dash}" fill="none" opacity="0.8"/>
        <circle cx="16" cy="16" r="7" stroke="{s}" stroke-width="1.1" fill="none" opacity="0.5"/>
        <circle cx="16" cy="16" r="2.5" fill="{p}" opacity="0.9"/>
        
        <!-- Radar sweep beam -->
        <line x1="16" y1="16" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{s}" stroke-width="1.5" stroke-linecap="round" opacity="0.9"/>
        
        <!-- Center Repeater Transmission Tower -->
        <path d="M16 13v6M14 19h4M16 13l2-5h-4l2 5z" stroke="{tower_col}" stroke-width="1.4" stroke-linecap="round" fill="none"/>
        
        <!-- Constellation satellite nodes -->
        <circle cx="{b1_x:.1f}" cy="{b1_y:.1f}" r="1.8" fill="{s}"/>
        <circle cx="{b1_x:.1f}" cy="{b1_y:.1f}" r="3.2" stroke="{s}" stroke-width="0.6" fill="none" opacity="0.5"/>
        <circle cx="{b2_x:.1f}" cy="{b2_y:.1f}" r="1.4" fill="{p}"/>
        <circle cx="{b3_x:.1f}" cy="{b3_y:.1f}" r="1.6" fill="{s}"/>
    </svg>"""


def get_initials(alias: str, node_id: str) -> str:
    """Extracts 2 clean uppercase initials from alias, or hex characters from node_id."""
    clean_alias = "".join(c for c in (alias or "").strip().lstrip("@!#[]") if c.isalnum() or c.isspace() or c in "-_")
    words = [w for w in clean_alias.replace("-", " ").replace("_", " ").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    elif len(words) == 1 and len(words[0]) >= 2:
        return words[0][:2].upper()
    elif len(words) == 1 and len(words[0]) == 1:
        clean_id = "".join(c for c in (node_id or "").strip().lstrip("!@#") if c.isalnum())
        return (words[0][0] + (clean_id[0] if clean_id else "0")).upper()
    else:
        clean_id = "".join(c for c in (node_id or "").strip().lstrip("!@#") if c.isalnum())
        return (clean_id[:2] if len(clean_id) >= 2 else "MC").upper()


def generate_initials_cyber_svg(node_id: str, alias: str, h: int) -> str:
    """Generates procedural SVG markup for Style A: Bilateral Cyber Initials with decorative frame."""
    palette = CYBER_PALETTES[(h >> 0) % len(CYBER_PALETTES)]
    p = palette["primary"]
    s = palette["secondary"]
    bg = palette["bg"]

    initials = get_initials(alias, node_id)

    # 4 Corner bracket styles
    corner_style = (h >> 4) % 4
    if corner_style == 0:
        # Sharp tech L-brackets
        corners = f"""
            <path d="M 4 10 V 4 H 10" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 22 4 H 28 V 10" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 4 22 V 28 H 10" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 22 28 H 28 V 22" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        """
    elif corner_style == 1:
        # Circuit trace brackets with terminal solder pads
        corners = f"""
            <path d="M 5 10 V 5 H 10" stroke="{p}" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="10" cy="5" r="1.2" fill="{s}"/>
            <circle cx="5" cy="10" r="1.2" fill="{s}"/>
            <path d="M 22 5 H 27 V 10" stroke="{p}" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="22" cy="5" r="1.2" fill="{s}"/>
            <circle cx="27" cy="10" r="1.2" fill="{s}"/>
            <path d="M 5 22 V 27 H 10" stroke="{p}" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="5" cy="22" r="1.2" fill="{s}"/>
            <circle cx="10" cy="27" r="1.2" fill="{s}"/>
            <path d="M 22 27 H 27 V 22" stroke="{p}" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="22" cy="27" r="1.2" fill="{s}"/>
            <circle cx="27" cy="22" r="1.2" fill="{s}"/>
        """
    elif corner_style == 2:
        # Stepped notch brackets with accent tick
        corners = f"""
            <path d="M 4 10 V 6 H 8 V 4 H 11" stroke="{p}" stroke-width="1.3" fill="none" stroke-linecap="round"/>
            <path d="M 21 4 H 24 V 6 H 28 V 10" stroke="{p}" stroke-width="1.3" fill="none" stroke-linecap="round"/>
            <path d="M 4 22 V 26 H 8 V 28 H 11" stroke="{p}" stroke-width="1.3" fill="none" stroke-linecap="round"/>
            <path d="M 21 28 H 24 V 26 H 28 V 22" stroke="{p}" stroke-width="1.3" fill="none" stroke-linecap="round"/>
        """
    else:
        # Chamfer 45-degree cyber brackets
        corners = f"""
            <path d="M 4 10 L 4 7 L 7 4 L 10 4" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 22 4 L 25 4 L 28 7 L 28 10" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 4 22 L 4 25 L 7 28 L 10 28" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 22 28 L 25 28 L 28 25 L 28 22" stroke="{p}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        """

    # Edge reticle accents (4 variations)
    edge_style = (h >> 6) % 4
    if edge_style == 0:
        # Cardinal tick marks
        edges = f"""
            <line x1="16" y1="3" x2="16" y2="5.5" stroke="{s}" stroke-width="1.2" stroke-linecap="round"/>
            <line x1="16" y1="26.5" x2="16" y2="29" stroke="{s}" stroke-width="1.2" stroke-linecap="round"/>
            <line x1="3" y1="16" x2="5.5" y2="16" stroke="{s}" stroke-width="1.2" stroke-linecap="round"/>
            <line x1="26.5" y1="16" x2="29" y2="16" stroke="{s}" stroke-width="1.2" stroke-linecap="round"/>
        """
    elif edge_style == 1:
        # Cardinal cyber node dots
        edges = f"""
            <circle cx="16" cy="4.5" r="1.3" fill="{s}"/>
            <circle cx="16" cy="27.5" r="1.3" fill="{s}"/>
            <circle cx="4.5" cy="16" r="1.3" fill="{s}"/>
            <circle cx="27.5" cy="16" r="1.3" fill="{s}"/>
        """
    elif edge_style == 2:
        # Lateral notches
        edges = f"""
            <line x1="13" y1="3.5" x2="19" y2="3.5" stroke="{s}" stroke-width="1.2" stroke-linecap="round" opacity="0.8"/>
            <line x1="13" y1="28.5" x2="19" y2="28.5" stroke="{s}" stroke-width="1.2" stroke-linecap="round" opacity="0.8"/>
            <circle cx="4.5" cy="16" r="1.1" fill="{p}"/>
            <circle cx="27.5" cy="16" r="1.1" fill="{p}"/>
        """
    else:
        # Flanking micro dots
        edges = f"""
            <circle cx="13" cy="4.5" r="0.9" fill="{s}"/>
            <circle cx="19" cy="4.5" r="0.9" fill="{s}"/>
            <circle cx="13" cy="27.5" r="0.9" fill="{s}"/>
            <circle cx="19" cy="27.5" r="0.9" fill="{s}"/>
            <line x1="3.5" y1="14" x2="3.5" y2="18" stroke="{p}" stroke-width="1.2" stroke-linecap="round"/>
            <line x1="28.5" y1="14" x2="28.5" y2="18" stroke="{p}" stroke-width="1.2" stroke-linecap="round"/>
        """

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="{bg}" stroke="{p}" stroke-width="1.2" />
        {corners}
        {edges}
        <text x="16" y="20.5" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="11.5" font-weight="bold" fill="{p}" text-anchor="middle" letter-spacing="0.5">{initials}</text>
    </svg>"""


def get_contact_avatar_icon(
    node_id: str,
    alias: str = "",
    is_repeater: bool = False,
    size: int = 40,
    style: Optional[str] = None,
) -> QIcon:
    """Renders and returns a cached QIcon with the appropriate deterministic avatar.
    
    If is_repeater is True, always renders Style B: Tactical Radar Constellation.
    If is_repeater is False, uses the configured user avatar style ('droid' or 'letters').
    """
    effective_style = style or _CURRENT_USER_AVATAR_STYLE
    cache_key = (node_id or "", alias or "", is_repeater, size, effective_style)
    if cache_key in _AVATAR_CACHE:
        return _AVATAR_CACHE[cache_key]

    h = _hash_contact(node_id, alias)
    if is_repeater:
        svg_str = generate_repeater_radar_svg(h)
    elif effective_style == "letters":
        svg_str = generate_initials_cyber_svg(node_id, alias, h)
    else:
        svg_str = generate_droid_svg(h)

    try:
        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, float(size), float(size)))
        painter.end()
        icon = QIcon(pix)
        _AVATAR_CACHE[cache_key] = icon
        return icon
    except Exception:
        return QIcon()

