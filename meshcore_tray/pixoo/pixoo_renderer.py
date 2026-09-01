"""Pixoo 64 Matrix Renderer displaying the Single Latest Post per Channel with 10s Fade-Out."""

from collections import defaultdict
from dataclasses import dataclass
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
    CAROUSEL = "carousel"
    TELEMETRY = "telemetry"
    NEIGHBOURS = "neighbours"


# Channel Theme Color Palette
CHANNEL_PALETTE = {
    "public": (0, 255, 204),      # Mint / Cyan
    "general": (56, 139, 253),     # Dodger Blue
    "ops": (255, 145, 0),         # Orange
    "emergency": (255, 68, 68),   # Red
    "weather": (118, 255, 3),     # Lime
    "telemetry": (224, 64, 251),   # Magenta
    "dm": (224, 64, 251),         # Magenta
}

# Blue highlighting for #channels and @users in message bodies
COLOR_TAG_BLUE = (56, 189, 248)  # Sky Blue #38BDF8
COLOR_BODY_WHITE = (255, 255, 255)  # Pure White


@dataclass
class ChannelMessage:
    """Latest message entry stored on a channel page."""
    sender_name: str
    is_favorite: bool
    time_str: str
    text: str


class PixooRenderer:
    """Renders tabbed channel pages displaying the single latest post with 10s fade-out."""

    def __init__(self, config=None):
        self.config = config
        self.mode = DisplayMode.CAROUSEL
        self.current_channel = "Public"

        # Channel Pages: maps clean channel_name -> latest ChannelMessage
        self.latest_messages: Dict[str, Optional[ChannelMessage]] = defaultdict(lambda: None)

        self.latest_telemetry: Optional[TelemetryEnvelope] = None
        self.latest_neighbours: List[NeighbourInfo] = []

        # Carousel page state
        self.current_page_idx: int = 0
        self.page_start_time: float = time.time()
        self.page_duration_secs: float = 30.0

        # Queued channel switching on rapid message arrival (15s minimum display)
        self.last_switch_time: float = time.time()
        self.pending_channel_switch: Optional[str] = None
        self.pending_switch_time: float = 0.0

        # Slide-left transition state
        self.is_transitioning: bool = False
        self.transition_start_time: float = 0.0
        self.transition_duration: float = 0.8  # 800ms slide transition
        self.prev_page_idx: int = 0

        # Inverted Header Alert Strobe
        self.is_flashing: bool = False
        self.flash_start_time: float = 0.0
        self.flash_count: int = 5
        self.flash_cycle_duration: float = 0.25  # 250ms per on/off cycle

        # Screen rotation timers
        self.telemetry_start_time: float = 0.0
        self.telemetry_duration: float = 8.0
        self.neighbours_start_time: float = 0.0
        self.neighbours_duration: float = 8.0

        # Animation ticker (resets on new message arrival)
        self.anim_frame: int = 0

    def set_config(self, config):
        self.config = config
        if config and hasattr(config.pixoo, "page_duration_secs"):
            self.page_duration_secs = float(config.pixoo.page_duration_secs)

    def set_current_channel(self, channel: str):
        self.current_channel = channel
        active = self.get_active_channel_pages()
        for idx, ch in enumerate(active):
            if ch.lower().lstrip("#") == channel.lower().lstrip("#"):
                self.current_page_idx = idx
                self.page_start_time = time.time()
                self.last_switch_time = time.time()
                break

    def get_active_channel_pages(self) -> List[str]:
        """Returns enabled channel pages from configuration."""
        if not self.config:
            return ["Public", "general", "ops", "telemetry"]

        filters = self.config.pixoo.channel_filters
        enabled = [k.lstrip("#") for k, v in filters.items() if v]
        if not enabled:
            return ["Public"]
        return enabled

    def trigger_message_alert(self, message: MessageEnvelope):
        """Processes an incoming message, stores it as the latest message for that channel, and resets page timer."""
        chan = message.channel if not message.is_direct_message else "DM"
        clean_chan = chan.lstrip("#")

        # 1. Filter Check
        if self.config:
            filter_map = self.config.pixoo.channel_filters
            if (chan in filter_map and not filter_map[chan]) or (clean_chan in filter_map and not filter_map[clean_chan]):
                logger.info(f"Pixoo filtered out message from channel: {chan}")
                return

        sender = message.sender_name or "Unknown"
        is_fav = message.is_favorite or bool(self.config and sender in self.config.favorites)
        t_str = message.timestamp[11:16] if len(message.timestamp) >= 16 else datetime.now().strftime("%H:%M")

        # 2. Store Single Latest Message for this Channel
        msg_entry = ChannelMessage(
            sender_name=sender,
            is_favorite=is_fav,
            time_str=t_str,
            text=message.text
        )
        self.latest_messages[clean_chan] = msg_entry

        # 3. Reset page timer and handle channel switching
        now = time.time()
        active_pages = self.get_active_channel_pages()
        current_active_page = active_pages[self.current_page_idx % len(active_pages)].lower()

        if current_active_page == clean_chan.lower():
            # Already viewing this channel: reset 30s timer and flash inverted header
            self.page_start_time = now
            self.last_switch_time = now
            if not self.is_in_quiet_hours():
                self.is_flashing = True
                self.flash_start_time = now
                self.flash_count = self.config.pixoo.flash_count if self.config else 5
        else:
            # Different channel: check if we need to queue (if current page was shown for < 15s)
            time_on_current_page = now - self.last_switch_time
            if time_on_current_page < 15.0:
                self.pending_channel_switch = clean_chan
                self.pending_switch_time = self.last_switch_time + 15.0
            else:
                self._switch_to_channel_page(clean_chan, now)

        # Reset animation ticks so message visibility is 100% full opacity immediately
        self.anim_frame = 0

        # Return from Telemetry or Neighbours mode if active
        if self.mode in (DisplayMode.TELEMETRY, DisplayMode.NEIGHBOURS):
            self.mode = DisplayMode.CAROUSEL

    def _switch_to_channel_page(self, channel_name: str, now: float):
        """Switches carousel to target channel page and resets 30s timer."""
        active_pages = self.get_active_channel_pages()
        for idx, p in enumerate(active_pages):
            if p.lower() == channel_name.lower():
                self.current_page_idx = idx
                self.page_start_time = now
                self.last_switch_time = now
                break

        if not self.is_in_quiet_hours():
            self.is_flashing = True
            self.flash_start_time = now
            self.flash_count = self.config.pixoo.flash_count if self.config else 5

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
        """Main rendering pipeline with queued switching, slide transitions, and inverted header alert."""
        now = time.time()
        self.anim_frame += 1

        # Check queued channel switch
        if self.pending_channel_switch and now >= self.pending_switch_time:
            target_chan = self.pending_channel_switch
            self.pending_channel_switch = None
            self._switch_to_channel_page(target_chan, now)

        # Check Telemetry / Neighbours timeout
        if self.mode == DisplayMode.TELEMETRY:
            if (now - self.telemetry_start_time) >= self.telemetry_duration:
                self.mode = DisplayMode.CAROUSEL
                self.anim_frame = 0
        elif self.mode == DisplayMode.NEIGHBOURS:
            if (now - self.neighbours_start_time) >= self.neighbours_duration:
                self.mode = DisplayMode.CAROUSEL
                self.anim_frame = 0

        # Background color
        bg_col = hex_to_rgb(self.config.pixoo_colors.background_color) if self.config else (0, 0, 0)
        img = Image.new("RGB", (64, 64), bg_col)
        draw = ImageDraw.Draw(img)

        # Quiet hours blackout check
        if self.is_in_quiet_hours() and self.config and self.config.quiet_hours.action == "blackout":
            return img

        # Inverted header flash state
        is_header_inverted = False
        if self.is_flashing:
            elapsed_flash = now - self.flash_start_time
            total_flash_time = self.flash_count * self.flash_cycle_duration
            if elapsed_flash < total_flash_time:
                phase = (elapsed_flash % self.flash_cycle_duration) / self.flash_cycle_duration
                if phase < 0.5:
                    is_header_inverted = True
            else:
                self.is_flashing = False

        if self.mode == DisplayMode.TELEMETRY:
            self._render_telemetry_mode(img, draw, now)
        elif self.mode == DisplayMode.NEIGHBOURS:
            self._render_neighbours_mode(img, draw, now)
        else:
            self._render_carousel(img, draw, now, is_header_inverted)

        # Quiet hours dim check
        if self.is_in_quiet_hours() and self.config and self.config.quiet_hours.action == "dim":
            img = img.point(lambda p: int(p * 0.3))

        return img

    # --- Carousel & Slide-Left Transition Engine ---

    def _render_carousel(self, img: Image.Image, draw: ImageDraw.Draw, now: float, is_header_inverted: bool):
        active_pages = self.get_active_channel_pages()
        if not active_pages:
            return

        elapsed_page = now - self.page_start_time
        page_dur = self.page_duration_secs if self.page_duration_secs > 0 else 30.0

        # Timed carousel rotation
        if not self.pending_channel_switch and not self.is_transitioning and len(active_pages) > 1 and elapsed_page >= page_dur:
            self.is_transitioning = True
            self.transition_start_time = now
            self.prev_page_idx = self.current_page_idx
            self.current_page_idx = (self.current_page_idx + 1) % len(active_pages)

        if self.is_transitioning:
            trans_elapsed = now - self.transition_start_time
            if trans_elapsed >= self.transition_duration:
                self.is_transitioning = False
                self.page_start_time = now
                self.last_switch_time = now
                page_name = active_pages[self.current_page_idx % len(active_pages)]
                page_img = self._render_channel_page(page_name, is_header_inverted)
                img.paste(page_img, (0, 0))
            else:
                progress = trans_elapsed / self.transition_duration
                shift = int(64.0 * (0.5 - 0.5 * math.cos(progress * math.pi)))

                prev_page_name = active_pages[self.prev_page_idx % len(active_pages)]
                next_page_name = active_pages[self.current_page_idx % len(active_pages)]

                prev_img = self._render_channel_page(prev_page_name, False)
                next_img = self._render_channel_page(next_page_name, False)

                img.paste(prev_img, (-shift, 0))
                img.paste(next_img, (64 - shift, 0))
        else:
            page_name = active_pages[self.current_page_idx % len(active_pages)]
            page_img = self._render_channel_page(page_name, is_header_inverted)
            img.paste(page_img, (0, 0))

    # --- Render Single Latest Message on Channel Page with 10s Fade-Out ---

    def _render_channel_page(self, channel_name: str, is_header_inverted: bool = False) -> Image.Image:
        page = Image.new("RGB", (64, 64), (0, 0, 0))
        p_draw = ImageDraw.Draw(page)

        chan_col = self.get_channel_color(channel_name)
        sender_col = hex_to_rgb(self.config.pixoo_colors.sender_color) if self.config else (255, 110, 127)
        star_col = hex_to_rgb(self.config.pixoo_colors.favorite_star_color) if self.config else (255, 234, 0)
        time_col = (139, 148, 158)
        divider_col = (33, 38, 45)

        # 1. Top Channel Heading (y=0..8) with Inverted Color Flash Support
        header_title = f"#{channel_name.upper()}"
        if is_header_inverted:
            p_draw.rectangle([0, 0, 63, 8], fill=chan_col)
            self._draw_text(page, header_title[:12], 2, 1, (0, 0, 0))
        else:
            self._draw_text(page, header_title[:12], 2, 1, chan_col)

        p_draw.line([0, 9, 63, 9], fill=divider_col)

        # 2. Get Single Latest Message for this Channel
        clean_name = channel_name.lstrip("#")
        msg = self.latest_messages.get(clean_name)

        if not msg:
            self._draw_text(page, "No messages", 6, 26, (100, 120, 140))
            self._draw_text(page, "Standby...", 10, 38, (70, 90, 110))
            return page

        # 3. User Header Line (y=11..17): [★] @username time
        star_str = "★" if msg.is_favorite else ""
        header_text = f"{star_str}@{msg.sender_name} {msg.time_str}"
        header_width = len(header_text) * 5

        header_band = Image.new("RGBA", (max(header_width + 40, 64), 7), (0, 0, 0, 0))

        # Horizontal pan if user header overflows 60px
        x_shift = 0
        if header_width > 60:
            overflow_x = header_width - 56
            x_shift = int((self.anim_frame * 0.4) % (overflow_x + 20))
            if x_shift > overflow_x:
                x_shift = overflow_x

        x_pos = 1 - x_shift
        if msg.is_favorite:
            self._draw_glyph(header_band, GLYPH_STAR, x_pos, 0, star_col)
            x_pos += 6

        user_tag = f"@{msg.sender_name}"
        self._draw_text(header_band, user_tag, x_pos, 0, sender_col)
        x_pos += (len(user_tag) + 1) * 5

        self._draw_text(header_band, msg.time_str, x_pos, 0, time_col)
        cropped_header = header_band.crop((0, 0, 64, 7))

        # 4. Tokenize and Wrap Single Latest Message Body in Pure White with Blue Tag Highlighting
        line_tokens = self._wrap_and_tokenize(msg.text, base_color=COLOR_BODY_WHITE, max_chars_per_line=12)
        total_msg_height = len(line_tokens) * 7

        # Available display height below user header (y=19..63 -> 44 pixels)
        available_h = 44
        content_buffer = Image.new("RGBA", (64, max(total_msg_height, available_h)), (0, 0, 0, 0))

        # 5. Timing, Bounce & 10-Second Fade-Out Logic
        # Rules:
        # - Short Message (<= 44px): display for 10 seconds (250 frames), then fade out over 1.5s (38 frames).
        # - Long Message (> 44px): executes top hold (5s) -> scroll down -> bottom hold (4s) -> scroll up.
        #   Once back at top, hold for 10 seconds (250 frames), then fade out over 1.5s (38 frames).
        bounce_y = 0.0
        opacity = 1.0

        FADE_DURATION_FRAMES = 38  # ~1.5 seconds smooth fade

        if total_msg_height <= available_h:
            # Short Message
            HOLD_SHORT_FRAMES = 250  # 10 seconds at 25fps
            bounce_y = 0.0

            if self.anim_frame < HOLD_SHORT_FRAMES:
                opacity = 1.0
            elif self.anim_frame < HOLD_SHORT_FRAMES + FADE_DURATION_FRAMES:
                fade_progress = (self.anim_frame - HOLD_SHORT_FRAMES) / float(FADE_DURATION_FRAMES)
                opacity = max(0.0, 1.0 - fade_progress)
            else:
                opacity = 0.0
        else:
            # Long Message
            overflow_y = float(total_msg_height - available_h)
            pause_top_frames = 125     # ~5.0s hold on start of message
            scroll_down_frames = max(40, int(overflow_y * 2.5))
            pause_bottom_frames = 100  # ~4.0s hold at end of message
            scroll_up_frames = max(40, int(overflow_y * 2.5))

            cycle_duration = pause_top_frames + scroll_down_frames + pause_bottom_frames + scroll_up_frames
            HOLD_AFTER_SCROLL_FRAMES = 250  # 10 seconds hold after scroll completes

            fade_start_frame = cycle_duration + HOLD_AFTER_SCROLL_FRAMES

            if self.anim_frame < pause_top_frames:
                # 1. HOLD on start of message at TOP for ~5 seconds
                bounce_y = 0.0
                opacity = 1.0
            elif self.anim_frame < pause_top_frames + scroll_down_frames:
                # 2. Smoothly scroll DOWN to bottom of message
                t = (self.anim_frame - pause_top_frames) / float(scroll_down_frames)
                bounce_y = overflow_y * (0.5 - 0.5 * math.cos(t * math.pi))
                opacity = 1.0
            elif self.anim_frame < pause_top_frames + scroll_down_frames + pause_bottom_frames:
                # 3. HOLD at bottom for ~4 seconds
                bounce_y = overflow_y
                opacity = 1.0
            elif self.anim_frame < cycle_duration:
                # 4. Smoothly scroll UP back to start of message at top
                t = (self.anim_frame - (pause_top_frames + scroll_down_frames + pause_bottom_frames)) / float(scroll_up_frames)
                bounce_y = overflow_y * (0.5 + 0.5 * math.cos(t * math.pi))
                opacity = 1.0
            elif self.anim_frame < fade_start_frame:
                # 5. HOLD at top for 10 seconds after scroll completes
                bounce_y = 0.0
                opacity = 1.0
            elif self.anim_frame < fade_start_frame + FADE_DURATION_FRAMES:
                # 6. Fade out over 1.5s
                bounce_y = 0.0
                fade_progress = (self.anim_frame - fade_start_frame) / float(FADE_DURATION_FRAMES)
                opacity = max(0.0, 1.0 - fade_progress)
            else:
                # 7. Fully faded out
                bounce_y = 0.0
                opacity = 0.0

        # If fully faded out, render a clean standby prompt below top heading
        if opacity <= 0.0:
            self._draw_text(page, "Standby...", 10, 32, (60, 75, 95))
            return page

        # Draw lines onto content buffer
        y_cursor = 0
        for words_on_line in line_tokens:
            x_cursor = 2
            for word_text, word_color in words_on_line:
                self._draw_text(content_buffer, word_text, x_cursor, y_cursor, word_color)
                x_cursor += (len(word_text) + 1) * 5
            y_cursor += 7

        crop_top = int(bounce_y)
        crop_bottom = crop_top + available_h
        cropped_content = content_buffer.crop((0, crop_top, 64, crop_bottom))

        # Apply visibility opacity
        if opacity < 1.0:
            cropped_header = self._apply_opacity_rgba(cropped_header, opacity)
            cropped_content = self._apply_opacity_rgba(cropped_content, opacity)

        page.paste(cropped_header, (0, 11), cropped_header)
        page.paste(cropped_content, (0, 19), cropped_content)

        return page

    def _apply_opacity_rgba(self, img_rgba: Image.Image, opacity: float) -> Image.Image:
        """Scales RGBA channels by opacity."""
        r, g, b, a = img_rgba.split()
        op = max(0.0, min(1.0, opacity))
        r = r.point(lambda p: int(p * op))
        g = g.point(lambda p: int(p * op))
        b = b.point(lambda p: int(p * op))
        a = a.point(lambda p: int(p * op))
        return Image.merge("RGBA", (r, g, b, a))

    def _wrap_and_tokenize(self, text: str, base_color: Tuple[int, int, int], max_chars_per_line: int = 12) -> List[List[Tuple[str, Tuple[int, int, int]]]]:
        """Wraps text into lines, highlighting words starting with '#' or '@' in Blue."""
        words = text.split()
        lines: List[List[Tuple[str, Tuple[int, int, int]]]] = []
        current_line: List[Tuple[str, Tuple[int, int, int]]] = []
        current_line_char_count = 0

        for word in words:
            if word.startswith("#") or word.startswith("@"):
                word_col = COLOR_TAG_BLUE
            else:
                word_col = base_color

            needed_len = len(word) if not current_line else (len(word) + 1)

            if current_line_char_count + needed_len <= max_chars_per_line:
                current_line.append((word, word_col))
                current_line_char_count += needed_len
            else:
                if current_line:
                    lines.append(current_line)
                while len(word) > max_chars_per_line:
                    chunk = word[:max_chars_per_line]
                    lines.append([(chunk, word_col)])
                    word = word[max_chars_per_line:]
                current_line = [(word, word_col)]
                current_line_char_count = len(word)

        if current_line:
            lines.append(current_line)

        return lines or [[("", base_color)]]

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
