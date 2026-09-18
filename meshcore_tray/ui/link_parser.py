"""HTML Link Parser and URL Linkifier for MeshCore Navigator chatlogs."""

import html
import re
from typing import Callable, List, Optional, Tuple

# Comprehensive URL and email recognition pattern
# Matches http/https/ftp, www. prefixes, common domains with paths or TLDs, and email addresses
URL_PATTERN = re.compile(
    r"(?i)\b(?:https?://|ftp://)[^\s<>\"]+|\b(?:www\.)[^\s<>\"]+|\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+|\b[a-zA-Z0-9-]+\.(?:com|org|net|io|co\.uk|uk|de|app|dev|xyz|info|edu|gov)(?:/[^\s<>\"]*)?",
    re.IGNORECASE
)

TRAILING_PUNCT = ".,;:!?\"'`"


def clean_trailing_punctuation(raw: str) -> Tuple[str, str]:
    """Separates non-URL trailing punctuation or unbalanced brackets from a candidate URL."""
    trailing = ""
    while raw:
        last_char = raw[-1]
        if last_char in TRAILING_PUNCT:
            trailing = last_char + trailing
            raw = raw[:-1]
        elif last_char == ")" and raw.count(")") > raw.count("("):
            trailing = last_char + trailing
            raw = raw[:-1]
        elif last_char == "]" and raw.count("]") > raw.count("["):
            trailing = last_char + trailing
            raw = raw[:-1]
        elif last_char == "}" and raw.count("}") > raw.count("{"):
            trailing = last_char + trailing
            raw = raw[:-1]
        else:
            break
    return raw, trailing


def normalize_href(raw_url: str) -> str:
    """Ensures URL has an appropriate protocol prefix (https://, mailto:, etc.)."""
    if not raw_url:
        return ""
    if "@" in raw_url and "://" not in raw_url and not raw_url.lower().startswith("mailto:"):
        return f"mailto:{raw_url}"
    if raw_url.lower().startswith(("http://", "https://", "ftp://", "mailto:")):
        return raw_url
    return f"https://{raw_url}"


def extract_urls(text: str) -> List[str]:
    """Extracts all clean, valid URLs from text, with protocol normalized."""
    if not text:
        return []

    urls = []
    for m in URL_PATTERN.finditer(text):
        raw_match = m.group(0)
        clean_url, _ = clean_trailing_punctuation(raw_match)
        if clean_url:
            urls.append(normalize_href(clean_url))
    return urls


def format_message_text_with_links(
    text: str,
    link_color: str = "#58A6FF",
    insert_break_func: Optional[Callable[[str], str]] = None
) -> str:
    """Parses plain message text and converts detected URLs and emails into clickable HTML links.
    Safely escapes all HTML tags in user-generated text to prevent XSS / formatting corruption.
    Preserves newlines as <br> for rich text presentation in QLabels and text browsers.
    """
    if not text:
        return ""

    if insert_break_func is None:
        def _noop_breaks(s: str) -> str:
            return s
        insert_break_func = _noop_breaks

    parts = []
    last_idx = 0

    for m in URL_PATTERN.finditer(text):
        start, end = m.span()
        # Non-URL preceding text
        if start > last_idx:
            chunk = text[last_idx:start]
            safe_chunk = html.escape(insert_break_func(chunk), quote=False)
            parts.append(safe_chunk)

        raw_match = m.group(0)
        clean_url, trailing = clean_trailing_punctuation(raw_match)

        if clean_url:
            href = normalize_href(clean_url)
            # href must be cleanly escaped with quotes escaped to prevent attribute breakout
            safe_href = html.escape(href, quote=True)
            # Display text can have break opportunities so long URLs wrap inside narrow cards
            safe_display = html.escape(insert_break_func(clean_url), quote=False)
            parts.append(
                f'<a href="{safe_href}" style="color: {link_color}; text-decoration: underline; font-weight: 500;">{safe_display}</a>'
            )

        if trailing:
            parts.append(html.escape(insert_break_func(trailing), quote=False))

        last_idx = end

    # Remaining trailing text
    if last_idx < len(text):
        chunk = text[last_idx:]
        safe_chunk = html.escape(insert_break_func(chunk), quote=False)
        parts.append(safe_chunk)

    return "".join(parts).replace("\n", "<br>")
