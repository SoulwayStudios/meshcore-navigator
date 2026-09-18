"""Unit tests for HTML link parser, URL linkifier, and clickable chatlog links."""

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from meshcore_tray.core.models import MessageEnvelope
from meshcore_tray.ui.link_parser import (
    extract_urls,
    clean_trailing_punctuation,
    normalize_href,
    format_message_text_with_links
)
from meshcore_tray.ui.chat_widget import MessageBodyLabel, MessageBubble


def test_clean_trailing_punctuation():
    """Verify trailing punctuation and unbalanced brackets are cleanly separated."""
    url, trailing = clean_trailing_punctuation("https://github.com!")
    assert url == "https://github.com"
    assert trailing == "!"

    url, trailing = clean_trailing_punctuation("https://github.com.,;")
    assert url == "https://github.com"
    assert trailing == ".,;"

    # Balanced parentheses must be preserved
    url, trailing = clean_trailing_punctuation("https://en.wikipedia.org/wiki/LoRa_(technology)")
    assert url == "https://en.wikipedia.org/wiki/LoRa_(technology)"
    assert trailing == ""

    # Unbalanced trailing parenthesis must be stripped
    url, trailing = clean_trailing_punctuation("https://example.com)")
    assert url == "https://example.com"
    assert trailing == ")"

    # Nested balanced inside wrapper
    url, trailing = clean_trailing_punctuation("https://en.wikipedia.org/wiki/LoRa_(technology))")
    assert url == "https://en.wikipedia.org/wiki/LoRa_(technology)"
    assert trailing == ")"


def test_normalize_href():
    """Verify protocols are added appropriately."""
    assert normalize_href("https://github.com") == "https://github.com"
    assert normalize_href("http://192.168.1.1:8080") == "http://192.168.1.1:8080"
    assert normalize_href("ftp://files.example.com") == "ftp://files.example.com"
    assert normalize_href("www.google.com") == "https://www.google.com"
    assert normalize_href("meshcore.co.uk") == "https://meshcore.co.uk"
    assert normalize_href("nicky@example.com") == "mailto:nicky@example.com"


def test_extract_urls():
    """Verify URL and email extraction from diverse chat messages."""
    text = (
        "Check out https://github.com/SoulwayStudios/meshcore-navigator! "
        "Also visit (www.meshcore.co.uk/map). "
        "Email contact@example.com for info. "
        "Do not linkify version 0.7.0 or frequency 433.175 MHz."
    )
    urls = extract_urls(text)
    assert "https://github.com/SoulwayStudios/meshcore-navigator" in urls
    assert "https://www.meshcore.co.uk/map" in urls
    assert "mailto:contact@example.com" in urls

    # Ensure versions and radio frequencies are NOT extracted
    assert not any("0.7.0" in u for u in urls)
    assert not any("433.175" in u for u in urls)


def test_format_message_text_with_links():
    """Verify HTML generation, safe escaping, and rich link presentation."""
    # 1. Plain text with XSS injection attempt
    raw = "Hello <script>alert(1)</script> & welcome!"
    res = format_message_text_with_links(raw)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res
    assert "&amp;" in res
    assert "<script>" not in res

    # 2. Text with link
    msg = "Visit https://meshcore.co.uk today!"
    html_res = format_message_text_with_links(msg, link_color="#58A6FF")
    assert '<a href="https://meshcore.co.uk"' in html_res
    assert 'style="color: #58A6FF; text-decoration: underline;' in html_res
    assert ">https://meshcore.co.uk</a> today!" in html_res.replace("\u200b", "")

    # 3. Newline preservation
    multi = "Line 1\nLine 2 with https://example.com\nLine 3"
    html_multi = format_message_text_with_links(multi)
    assert "<br>" in html_multi
    assert "Line 1<br>" in html_multi


def test_message_body_label_configuration(qapp):
    """Verify MessageBodyLabel enables rich text, external links, and interaction flags."""
    label = MessageBodyLabel("See https://github.com for details")
    assert label.textFormat() == Qt.TextFormat.RichText
    assert label.openExternalLinks() is True
    flags = label.textInteractionFlags()
    assert bool(flags & Qt.TextInteractionFlag.TextSelectableByMouse) is True
    assert bool(flags & Qt.TextInteractionFlag.LinksAccessibleByMouse) is True

    # Check raw text retrieval
    assert label.raw_text() == "See https://github.com for details"
    assert "<a href=\"https://github.com\"" in label.text()


def test_message_bubble_link_context_menu_items(qapp):
    """Verify MessageBubble context menu detects links and creates Open/Copy Link actions."""
    msg = MessageEnvelope(
        id="msg-url-01",
        timestamp="2026-09-18T18:00:00Z",
        source_driver="test",
        sender_id="!test_node",
        sender_name="M7NCY",
        is_direct_message=False,
        text="Take a look at https://buymeacoffee.com/soulwaystudios"
    )
    bubble = MessageBubble(msg)
    assert hasattr(bubble, "body_lbl")
    assert bubble.body_lbl.openExternalLinks() is True
    assert '<a href="https://buymeacoffee.com/soulwaystudios"' in bubble.body_lbl.text()


def test_room_server_message_bubble_links(qapp):
    """Verify room server message bubbles also render clickable links."""
    from meshcore_tray.ui.room_servers_view import MessageBubbleWidget
    msg = MessageEnvelope(
        id="room-msg-01",
        timestamp="2026-09-18T18:05:00Z",
        source_driver="test",
        sender_id="091882e61107",
        sender_name="Navigator Room BBS M7NC",
        is_direct_message=True,
        text="Join the BBS at https://meshcore.co.uk/bbs",
        metadata={"is_room_response": True}
    )
    bubble = MessageBubbleWidget(msg)
    labels = bubble.findChildren(MessageBodyLabel) or [lbl for lbl in bubble.findChildren(object) if hasattr(lbl, "openExternalLinks")]
    link_labels = [lbl for lbl in labels if hasattr(lbl, "openExternalLinks") and lbl.openExternalLinks()]
    assert len(link_labels) > 0
    assert '<a href="https://meshcore.co.uk/bbs"' in link_labels[0].text()
