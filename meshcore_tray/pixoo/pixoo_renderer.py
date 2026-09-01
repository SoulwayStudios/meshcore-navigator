"""Pixoo 64 Matrix Renderer with Chat Stream, Alternating Colors & Vertical Bounce."""

from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
import time
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw

from meshcore_tray.core.models import MessageEnvelope, NeighbourInfo, TelemetryEnvelope
from meshcore_tray.pixoo.pixel_fonts import (
    FONT_4X6, GLYPH_STAR, GLYPH_ANTENNA, GLYPH_PEOPLE, hex_to_rgb
)

logger = logging.getLogger("meshcore_tray.pixoo_renderer")


class DisplayMode:
    IDLE = "idle"
    ALERT = "alert"
    TELEMETRY = "telemetry"
    NEIGHBOURS = "neighbours"


# Palette of distinctive colors for different channel names
CHANNEL_PALETTE = {
    "public": (0, 255, 204),      # Mint / Cyan
    "general": (56, 139, 253),     # Blue
    "ops": (255, 145, 0),         # Orange
    "emergency": (255, 68, 68),   # Red
    "weather": (118, 255, 3),     # Lime
    "dm": (224, 64, 251),         # Magenta
}


@dataclass
class ChatGroup:
    """Group of consecutive messages from the same sender on the same channel."""
    channel: str
    sender_name: str
    is_favorite: bool
    time_str: str
    messages: List[str] = field(default_factory=list)


class PixooRenderer:
    """Renders continuous chat stream with alternating colors and smooth vertical bounce."""

    def __init__(self, config=None):
        self.config = config
        self.mode = DisplayMode.IDLE
        self.current_channel = "Public"
        self.chat_groups: List[ChatGroup] = []

        self.latest_telemetry: Optional[TelemetryEnvelope] = None
        self.latest_neighbours: List[NeighbourInfo] = []

        # Strobe timing registers
        self.is_flashing = False
        self.flash_start_time: float = 0.0
        self.flash_count: int = 5
        self.flash_cycle_duration: float = 0.25  # 250ms per cycle

        # Screen rotation timers
        self.telemetry_start_time: float = 0.0
        self.telemetry_duration: float = 8.0
        self.neighbours_start_time: float = 0.0
        self.neighbours_duration: float = 8.0

        # Animation frame counter
        self.anim_frame: int = 0

    def set_config(self, config):
        self.config = config

    def set_current_channel(self, channel: str):
        self.current_channel = channel

    def trigger_message_alert(self, message: MessageEnvelope):
        """Processes incoming message, groups it, triggers strobe, and updates chat stream."""
        # 1. Channel Filter Check
        if self.config:
            channel_key = message.channel if not message.is_direct_message else "DM"
            filter_map = self.config.pixoo.channel_filters
            if channel_key in filter_map and not filter_map[channel_key]:
                logger.info(f"Pixoo filtered out message from channel: {channel_key}")
                return

        chan = message.channel if not message.is_direct_message else "DM"
        sender = message.sender_name or "Unknown"
        is_fav = message.is_favorite or bool(self.config and sender in self.config.favorites)
        t_str = message.timestamp[11:16] if len(message.timestamp) >= 16 else datetime.now().strftime("%H:%M")

        # 2. Grouping Logic: Same user & channel -> append under same heading
        if self.chat_groups and self.chat_groups[-1].channel == chan and self.chat_groups[-1].sender_name == sender:
            self.chat_groups[-1].messages.append(message.text)
            self.chat_groups[-1].time_str = t_str
            self.chat_groups[-1].is_favorite = is_fav
        else:
            new_group = ChatGroup(
                channel=chan,
                sender_name=sender,
                is_favorite=is_fav,
                time_str=t_str,
                messages=[message.text]
            )
            self.chat_groups.append(new_group)
            if len(self.chat_groups) > 15:
                self.chat_groups.pop(0)

        # 3. Trigger 5x Strobe Flash (unless in quiet hours)
        if not self.is_in_quiet_hours():
            self.is_flashing = True
            self.flash_start_time = time.time()
            self.flash_count = self.config.pixoo.flash_count if self.config else 5

        # Reset animation frame to let user read newest message
        self.anim_frame = 0

        # If we were in Telemetry or Neighbours mode, return to chat
        if self.mode in (DisplayMode.TELEMETRY, DisplayMode.NEIGHBOURS):
            self.mode = DisplayMode.IDLE

    def trigger_telemetry(self, telem: Optional[TelemetryEnvelope] = None):
        if telem:
            self.latest_telemetry = telem
        self.mode = DisplayMode.TELEMETRY
        self.telemetry_start_time = time.time()
        self.telemetry_duration = self.config.telemetry.duration_secs if self.config else 8.0
        self.anim_frame = 0

    def trigger_neighbours(self, neighbours: Optional[List[NeighbourInfo]] = None):
        if neighbours:
            self.latest_neighbours = neighbours
        self.mode = DisplayMode.NEIGHBOURS
        self.neighbours_start_time = time.time()
        self.neighbours_duration = self.config.neighbours.duration_secs if self.config else 8.0
        self.anim_frame = 0

    def is_in_quiet_hours(self) -> bool:
        if not self.config or not self.config.quiet_hours.enabled:
            return False

        try:
            now = datetime.now().time()
            start = datetime.strptime(self.config.quiet_hours.start_time, "%H:%M").time()
            end = datetime.strptime(self.config.quiet_hours.end_time, "%H:%M").time()

            if start <= end:
                return start <= now <= end
            else:
                return now >= start or now <= end
        except Exception as e:
            logger.warning(f"Error parsing quiet hours: {e}")
            return False

    def get_channel_color(self, chan_name: str) -> Tuple[int, int, int]:
        clean = chan_name.lower().lstrip("#")
        if clean in CHANNEL_PALETTE:
            return CHANNEL_PALETTE[clean]
        if self.config:
            return hex_to_rgb(self.config.pixoo_colors.channel_color)
        return (0, 255, 204)

    def render_frame(self) -> Image.Image:
        """Renders 64x64 frame for animation loop."""
        now = time.time()
        self.anim_frame += 1

        # Check Telemetry / Neighbours timeout
        if self.mode == DisplayMode.TELEMETRY:
            if (now - self.telemetry_start_time) >= self.telemetry_duration:
                self.mode = DisplayMode.IDLE
                self.anim_frame = 0
        elif self.mode == DisplayMode.NEIGHBOURS:
            if (now - self.neighbours_start_time) >= self.neighbours_duration:
                self.mode = DisplayMode.IDLE
                self.anim_frame = 0

        # Background color
        bg_col = hex_to_rgb(self.config.pixoo_colors.background_color) if self.config else (0, 0, 0)
        img = Image.new("RGB", (64, 64), bg_col)
        draw = ImageDraw.Draw(img)

        # Quiet hours blackout check
        if self.is_in_quiet_hours() and self.config and self.config.quiet_hours.action == "blackout":
            return img

        if self.mode == DisplayMode.TELEMETRY:
            self._render_telemetry_mode(img, draw, now)
        elif self.mode == DisplayMode.NEIGHBOURS:
            self._render_neighbours_mode(img, draw, now)
        else:
            self._render_chat_stream(img, draw, now)

        # Strobe border overlay (3 rows top, 3 rows bottom, 5x flash)
        if self.is_flashing:
            elapsed_flash = now - self.flash_start_time
            total_flash_time = self.flash_count * self.flash_cycle_duration
            if elapsed_flash < total_flash_time:
                phase = (elapsed_flash % self.flash_cycle_duration) / self.flash_cycle_duration
                if phase < 0.5:
                    alert_col = hex_to_rgb(self.config.pixoo_colors.alert_color) if self.config else (0, 255, 68)
                    draw.rectangle([0, 0, 63, 2], fill=alert_col)
                    draw.rectangle([0, 61, 63, 63], fill=alert_col)
            else:
                self.is_flashing = False

        # Quiet hours dim check
        if self.is_in_quiet_hours() and self.config and self.config.quiet_hours.action == "dim":
            img = img.point(lambda p: int(p * 0.3))

        return img

    # --- Continuous Chat Stream with Alternating Colors & Vertical Bounce ---

    def _render_chat_stream(self, img: Image.Image, draw: ImageDraw.Draw, now: float):
        if not self.chat_groups:
            # Standby prompt
            cyan = (0, 230, 255)
            gray = (120, 140, 160)
            self._draw_text(img, "MESH READY", 8, 22, cyan)
            self._draw_text(img, f"#{self.current_channel}", 10, 34, self.get_channel_color(self.current_channel))
            self._draw_text(img, "Listening...", 8, 46, gray)
            return

        sender_col = hex_to_rgb(self.config.pixoo_colors.sender_color) if self.config else (255, 215, 0)
        star_col = hex_to_rgb(self.config.pixoo_colors.favorite_star_color) if self.config else (255, 234, 0)
        time_col = (130, 145, 165)  # Grey for time

        # Alternating message body colors:
        COLOR_WHITE = (255, 255, 255)
        COLOR_LIGHT_GREY = (175, 185, 200)

        # 1. Pre-calculate height of all chat groups
        group_layouts = []
        total_stream_height = 0
        global_msg_idx = 0

        for g in self.chat_groups:
            # Wrapped lines for all messages in this group with alternating color per message
            msg_entries = []
            for msg_text in g.messages:
                # Alternate color for each consecutive message
                body_color = COLOR_WHITE if (global_msg_idx % 2 == 0) else COLOR_LIGHT_GREY
                lines = self._wrap_text(msg_text, max_chars_per_line=12)
                msg_entries.append((lines, body_color))
                global_msg_idx += 1

            total_lines_count = sum(len(lines) for lines, _ in msg_entries)
            # Header height (8px) + Message lines (7px each) + 3px group gap
            g_height = 8 + (total_lines_count * 7) + 3
            group_layouts.append((g, msg_entries, g_height))
            total_stream_height += g_height

        # 2. Virtual canvas for full chat stream
        canvas_h = max(64, total_stream_height + 4)
        canvas = Image.new("RGB", (64, canvas_h), (0, 0, 0))

        # 3. Slow Vertical "Bounce" Animation when total text exceeds 64px
        scroll_y = 0.0
        if total_stream_height > 64:
            max_scroll = float(total_stream_height - 64)
            # Bounce cycle:
            # 1. Pause at top for 60 frames (~2.4s)
            # 2. Smoothly scroll down to max_scroll over N frames (~12 px/sec)
            # 3. Pause at bottom for 60 frames (~2.4s)
            # 4. Smoothly scroll back up to 0
            scroll_duration_frames = max(40, int(max_scroll * 2.2))
            pause_frames = 60
            total_cycle_frames = (pause_frames * 2) + (scroll_duration_frames * 2)

            phase_frame = self.anim_frame % total_cycle_frames

            if phase_frame < pause_frames:
                # 1. Pause at top
                scroll_y = 0.0
            elif phase_frame < pause_frames + scroll_duration_frames:
                # 2. Scroll from top down to bottom
                t = (phase_frame - pause_frames) / float(scroll_duration_frames)
                # Smooth sinusoidal easing
                scroll_y = max_scroll * (0.5 - 0.5 * math.cos(t * math.pi))
            elif phase_frame < pause_frames * 2 + scroll_duration_frames:
                # 3. Pause at bottom
                scroll_y = max_scroll
            else:
                # 4. Scroll from bottom back up to top
                t = (phase_frame - (pause_frames * 2 + scroll_duration_frames)) / float(scroll_duration_frames)
                scroll_y = max_scroll * (0.5 + 0.5 * math.cos(t * math.pi))
        else:
            scroll_y = 0.0

        # 4. Render all groups onto virtual canvas
        y_cursor = 2
        for g, msg_entries, g_h in group_layouts:
            chan_col = self.get_channel_color(g.channel)

            # --- Render Combined Header Line: #channel @user time ---
            chan_tag = f"#{g.channel}"
            user_tag = f"@{g.sender_name}"
            time_tag = g.time_str

            star_prefix = "★" if g.is_favorite else ""
            full_header_text = f"{chan_tag} {star_prefix}{user_tag} {time_tag}"
            header_pixel_width = len(full_header_text) * 5

            header_band = Image.new("RGBA", (max(header_pixel_width + 40, 64), 8), (0, 0, 0, 0))

            # Slow horizontal scroll for header if it overflows 60px
            x_shift = 0
            if header_pixel_width > 60:
                overflow_x = header_pixel_width - 56
                x_shift = int((self.anim_frame * 0.4) % (overflow_x + 20))
                if x_shift > overflow_x:
                    x_shift = overflow_x

            x_pos = 1 - x_shift

            # 1. #channelname
            self._draw_text(header_band, chan_tag, x_pos, 1, chan_col)
            x_pos += (len(chan_tag) + 1) * 5

            # 2. @username (with star if favorite)
            if g.is_favorite:
                self._draw_glyph(header_band, GLYPH_STAR, x_pos, 1, star_col)
                x_pos += 6

            self._draw_text(header_band, user_tag, x_pos, 1, sender_col)
            x_pos += (len(user_tag) + 1) * 5

            # 3. time in grey
            self._draw_text(header_band, time_tag, x_pos, 1, time_col)

            # Paste cropped header onto canvas
            cropped_header = header_band.crop((0, 0, 64, 8))
            canvas.paste(cropped_header, (0, y_cursor), cropped_header)
            y_cursor += 8

            # --- Render Message Lines with Alternating Color ---
            for lines, body_col in msg_entries:
                for line in lines:
                    self._draw_text(canvas, line, 3, y_cursor, body_col)
                    y_cursor += 7

            y_cursor += 3  # Gap before next group

        # 5. Crop viewport from virtual canvas according to slow vertical bounce
        scroll_int = max(0, min(int(scroll_y), canvas_h - 64))
        viewport = canvas.crop((0, scroll_int, 64, scroll_int + 64))
        img.paste(viewport, (0, 0))

    # --- Mode 3: Radio Telemetry Dashboard ---

    def _render_telemetry_mode(self, img: Image.Image, draw: ImageDraw.Draw, now: float):
        t = self.latest_telemetry or TelemetryEnvelope()
        cyan = (0, 229, 255)
        lime = (118, 255, 3)
        orange = (255, 145, 0)
        magenta = (224, 64, 251)
        gray = (130, 150, 170)

        self._draw_glyph(img, GLYPH_ANTENNA, 2, 2, cyan)
        self._draw_text(img, "RF STATUS", 10, 2, cyan)
        draw.line([0, 9, 63, 9], fill=(40, 50, 70))

        self._draw_text(img, f"FQ:{t.frequency_mhz}M", 2, 12, cyan)
        self._draw_text(img, f"BW:{int(t.bandwidth_khz)}k", 2, 22, lime)
        self._draw_text(img, f"SF{t.spreading_factor}", 38, 22, lime)
        self._draw_text(img, f"CR:{t.coding_rate}", 2, 32, orange)
        self._draw_text(img, f"TX:+{t.tx_power_dbm}dB", 34, 32, orange)
        self._draw_text(img, f"NF:{int(t.noise_floor_dbm)}dBm", 2, 42, magenta)
        self._draw_text(img, f"SNR:{t.snr_db}dB", 2, 52, gray)
        self._draw_text(img, f"B:{t.battery_pct}%", 42, 52, lime if t.battery_pct > 30 else (255, 50, 50))

    # --- Mode 4: Nearest Neighbours & SNR Screen ---

    def _render_neighbours_mode(self, img: Image.Image, draw: ImageDraw.Draw, now: float):
        cyan = (0, 229, 255)
        green = (0, 255, 68)
        yellow = (255, 215, 0)
        red = (255, 68, 68)
        gray = (140, 160, 180)

        self._draw_glyph(img, GLYPH_PEOPLE, 2, 2, cyan)
        header_title = "NEIGHBOURS"
        if self.config and self.config.neighbours.source == "repeater" and self.config.neighbours.target_repeater_node_id:
            header_title = f"REP:{self.config.neighbours.target_repeater_node_id[:5]}"

        self._draw_text(img, header_title, 10, 2, cyan)
        draw.line([0, 9, 63, 9], fill=(40, 50, 70))

        neighbours = self.latest_neighbours[:4]
        if not neighbours:
            self._draw_text(img, "Scanning mesh...", 2, 24, gray)
            return

        y_offset = 12
        for n in neighbours:
            alias_str = (n.alias or n.node_id)[:6]
            snr_str = f"{n.snr_db:+.1f}dB"
            snr_col = green if n.snr_db >= 5.0 else (yellow if n.snr_db >= 0.0 else red)

            self._draw_text(img, alias_str, 2, y_offset, (220, 230, 240))
            self._draw_text(img, snr_str, 32, y_offset, snr_col)

            bar_len = min(5, max(1, int((n.snr_db + 10) / 4)))
            for b in range(bar_len):
                draw.point((57 + b, y_offset + 3), fill=snr_col)

            y_offset += 13

    # --- Bitmap Text and Glyph Drawing Helpers ---

    def _draw_glyph(self, img: Image.Image, glyph: List[List[int]], start_x: int, start_y: int, color: Tuple[int, int, int]):
        pixels = img.load()
        for r_idx, row in enumerate(glyph):
            y = start_y + r_idx
            if y < 0 or y >= img.height:
                continue
            for c_idx, val in enumerate(row):
                x = start_x + c_idx
                if 0 <= x < img.width and val == 1:
                    pixels[x, y] = color

    def _draw_text(self, img: Image.Image, text: str, start_x: int, start_y: int, color: Tuple[int, int, int]):
        pixels = img.load()
        cur_x = start_x

        for char in text:
            glyph = FONT_4X6.get(char, FONT_4X6.get('?'))
            if not glyph:
                cur_x += 4
                continue

            for row_idx, row_val in enumerate(glyph):
                y = start_y + row_idx
                if y < 0 or y >= img.height:
                    continue
                for col_idx in range(4):
                    x = cur_x + col_idx
                    if 0 <= x < img.width and (row_val & (1 << (3 - col_idx))):
                        pixels[x, y] = color
            cur_x += 5

    def _wrap_text(self, text: str, max_chars_per_line: int = 12) -> List[str]:
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            candidate = " ".join(current_line + [word])
            if len(candidate) <= max_chars_per_line:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                while len(word) > max_chars_per_line:
                    lines.append(word[:max_chars_per_line])
                    word = word[max_chars_per_line:]
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines or [""]
