"""Tests for the deterministic procedural avatar generator."""

import pytest
from PyQt6.QtWidgets import QApplication
from meshcore_tray.core.models import NodeContact
from meshcore_tray.ui.avatar_generator import (
    CYBER_PALETTES,
    REPEATER_PALETTES,
    _hash_contact,
    generate_droid_svg,
    generate_repeater_radar_svg,
    get_contact_avatar_icon,
    _AVATAR_CACHE,
)
from meshcore_tray.ui.nav_dock import NavDockWidget
from meshcore_tray.config import AppConfig


@pytest.fixture(scope="session")
def app():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_hash_determinism():
    """Identical node IDs and aliases must yield identical hash values."""
    h1 = _hash_contact("!abcd1234", "CyberNinja")
    h2 = _hash_contact("!abcd1234", "CyberNinja")
    assert h1 == h2

    # Case-insensitivity and whitespace trimming
    h3 = _hash_contact("  !ABCD1234  ", "cyberninja  ")
    assert h1 == h3

    # Different inputs yield different hashes
    h4 = _hash_contact("!abcd1234", "OtherUser")
    assert h1 != h4


def test_droid_svg_generation_and_diversity():
    """Verify that Style C Cyberpunk Droid produces valid SVG with diverse features."""
    generated_svgs = set()
    used_palettes = set()

    # Generate 50 distinct droids
    for i in range(50):
        nid = f"!node_{i:04d}"
        alias = f"Agent_{i:03d}"
        h = _hash_contact(nid, alias)
        svg = generate_droid_svg(h)

        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert "viewBox=\"0 0 32 32\"" in svg
        generated_svgs.add(svg)

        palette_idx = (h >> 0) % len(CYBER_PALETTES)
        used_palettes.add(palette_idx)

    # All 50 should be distinct visual combinations
    assert len(generated_svgs) == 50
    # Multiple distinct cyberpunk palettes should have been utilized
    assert len(used_palettes) >= 5


def test_repeater_radar_svg_generation():
    """Verify that Style B Tactical Radar produces valid SVG with radar elements and antenna tower."""
    for i in range(10):
        nid = f"!rep_{i:04d}"
        alias = f"Mountain Repeater {i} [REP]"
        h = _hash_contact(nid, alias)
        svg = generate_repeater_radar_svg(h)

        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert "<!-- Radar concentric rings -->" in svg
        assert "<!-- Radar sweep beam -->" in svg
        assert "<!-- Center Repeater Transmission Tower -->" in svg
        assert "<!-- Constellation satellite nodes -->" in svg


def test_get_contact_avatar_icon_caching(app):
    """Verify avatar rendering to QIcon and memory caching."""
    _AVATAR_CACHE.clear()

    # Render user droid icon
    icon1 = get_contact_avatar_icon("!user_alpha", "Alpha User", is_repeater=False, size=36)
    assert not icon1.isNull()
    assert len(_AVATAR_CACHE) == 1

    # Fetching again should hit cache
    icon2 = get_contact_avatar_icon("!user_alpha", "Alpha User", is_repeater=False, size=36)
    assert icon1 is icon2

    # Repeater icon should produce different cached entry
    icon_rep = get_contact_avatar_icon("!rep_beta", "Beta Repeater", is_repeater=True, size=36)
    assert not icon_rep.isNull()
    assert len(_AVATAR_CACHE) == 2


def test_nav_dock_repeater_detection(app):
    """Verify that NavDockWidget correctly identifies repeaters from various flags and aliases."""
    dock = NavDockWidget(config=AppConfig())

    c_flag = NodeContact(node_id="!1", alias="Node 1", is_repeater=True)
    c_rep = NodeContact(node_id="!2", alias="Hilltop [REP]")
    c_router = NodeContact(node_id="!3", alias="Valley [ROUTER]")
    c_gw = NodeContact(node_id="!4", alias="City [GW]")
    c_user = NodeContact(node_id="!5", alias="Regular User")

    assert dock._is_repeater_contact(c_flag) is True
    assert dock._is_repeater_contact(c_rep) is True
    assert dock._is_repeater_contact(c_router) is True
    assert dock._is_repeater_contact(c_gw) is True
    assert dock._is_repeater_contact(c_user) is False


def test_dms_contact_item_renders_avatar_and_rich_tooltip(app):
    """Verify ContactItemWidget displays procedural avatar pixmap and rich tooltip."""
    from meshcore_tray.ui.dms_view import ContactItemWidget

    user = NodeContact(
        node_id="!c001user",
        alias="CyberPilot",
        is_favorite=True,
        snr_db=9.5,
        rssi_dbm=-75.0,
        out_path_len=2,
        latitude=54.5,
        longitude=-3.2,
        last_seen="2026-09-12T17:00:00Z"
    )
    widget = ContactItemWidget(user, is_favorite=True)
    assert widget.badge.pixmap() is not None
    assert not widget.badge.pixmap().isNull()
    assert widget.badge.property("letter") == "C"

    # Rich tooltip verification
    tip = widget.toolTip()
    assert "CyberPilot" in tip
    assert "!c001user" in tip
    assert "User / Client Node" in tip
    assert "Signal: -75 dBm (SNR +9.5 dB)" in tip
    assert "Path: 2 hops" in tip
    assert "54.5000, -3.2000" in tip


def test_avatar_generator_outlines_palette_swap_and_repeater_towers():
    """Verify squircle outlines, gold/magenta palette swap, independent eye colors, and colored repeater towers."""
    from meshcore_tray.ui.avatar_generator import (
        generate_droid_svg, generate_repeater_radar_svg, _hash_contact,
        CYBER_PALETTES, EYE_COLORS, REPEATER_TOWER_COLORS
    )

    # 1. Verify gold and magenta swap: index 2 is Solar Amber, index 4 is Synthwave Magenta
    assert CYBER_PALETTES[2]["primary"] == "#FFB703" # Solar Amber
    assert CYBER_PALETTES[4]["primary"] == "#FF007F" # Synthwave Magenta

    # 2. Heltec-V3 should resolve to Synthwave Magenta
    h_heltec = _hash_contact("!989afa62", "Heltec-V3")
    assert (h_heltec >> 0) % len(CYBER_PALETTES) == 4

    # 3. Droid SVG has outer squircle stroke outline
    droid_svg = generate_droid_svg(h_heltec)
    assert '<rect x="1" y="1" width="30" height="30" rx="8"' in droid_svg
    assert 'stroke="#FF007F"' in droid_svg

    # 4. Repeater radar SVG has squircle stroke outline and distinct tower color from REPEATER_TOWER_COLORS
    h_rep = _hash_contact("!dub_goat", "DUB-GOAT-RPT-01")
    rep_svg = generate_repeater_radar_svg(h_rep)
    assert '<rect x="1" y="1" width="30" height="30" rx="8"' in rep_svg
    assert any(f'stroke="{col}"' in rep_svg for col in REPEATER_TOWER_COLORS)


def test_repeaters_view_item_and_console_render_radar_avatar(app):
    """Verify RepeaterRowWidget and RepeaterConsoleWidget render Style B Tactical Radar avatars."""
    from meshcore_tray.ui.repeaters_view import RepeaterRowWidget
    from meshcore_tray.ui.repeater_console import RepeaterConsoleWidget

    rep = NodeContact(
        node_id="!r001rep",
        alias="Highland Peak [REP]",
        is_repeater=True,
        snr_db=14.0,
        rssi_dbm=-68.0,
        last_seen="2026-09-12T17:15:00Z"
    )
    row = RepeaterRowWidget(rep, is_favorite=False)
    assert row.badge.pixmap() is not None
    assert not row.badge.pixmap().isNull()
    assert "Highland Peak" in row.name_lbl.text()
    assert "Repeater Node" in row.toolTip()
    assert "!r001rep" in row.toolTip()

    console = RepeaterConsoleWidget()
    console.set_repeater(rep)
    assert console.icon_lbl.pixmap() is not None
    assert not console.icon_lbl.pixmap().isNull()
    assert console.icon_lbl.width() == 54
    assert console.icon_lbl.height() == 54


def test_map_layer_dock_cycle_button_green_and_watcher_default_on(app):
    """Verify cycle filter button uses emerald green styling and observer/watcher defaults to ON."""
    from meshcore_tray.ui.nav_dock import MapLayerDockWidget
    dock = MapLayerDockWidget(config=AppConfig())

    # Watcher / Observer traffic defaults to ON
    assert dock.btn_rf_links.isChecked() is True
    assert "Watcher" in dock.btn_rf_links.toolTip()

    # Cycle button styling uses emerald green, not gold
    assert "#FACC15" not in dock.btn_cycle_filter.styleSheet()
    assert "#CA8A04" not in dock.btn_cycle_filter.styleSheet()
    assert "#10B981" in dock.btn_cycle_filter.styleSheet()
    assert "#34D399" in dock.btn_cycle_filter.styleSheet()


def test_initials_cyber_avatar_generation_and_style_switching(app):
    """Verify Style A bilateral cyber initials generation, extraction, and global style switching."""
    from meshcore_tray.ui.avatar_generator import (
        get_initials, generate_initials_cyber_svg, _hash_contact,
        get_contact_avatar_icon, set_global_avatar_style, get_global_avatar_style,
        _AVATAR_CACHE
    )

    # 1. Test get_initials extraction
    assert get_initials("Bob Base", "!b0b0001") == "BB"
    assert get_initials("Bob Baker", "!b0b0002") == "BB"
    assert get_initials("M7NCY West Yagi", "!dd2df7a1") == "MW"
    assert get_initials("Nicky", "!nicky001") == "NI"
    assert get_initials("Heltec-V3", "!989afa62") == "HV"
    assert get_initials("", "!989afa62") == "98"
    assert get_initials("A", "!alpha") == "AA"

    # 2. Test generate_initials_cyber_svg markup
    h = _hash_contact("!b0b0001", "Bob Base")
    svg = generate_initials_cyber_svg("!b0b0001", "Bob Base", h)
    assert '<rect x="1" y="1" width="30" height="30" rx="8"' in svg
    assert ">BB</text>" in svg
    assert "<svg" in svg

    # 3. Test global style switching
    set_global_avatar_style("droid")
    assert get_global_avatar_style() == "droid"
    icon_droid = get_contact_avatar_icon("!user_test", "Test User", is_repeater=False, size=38)
    assert not icon_droid.isNull()

    set_global_avatar_style("letters")
    assert get_global_avatar_style() == "letters"
    icon_letters = get_contact_avatar_icon("!user_test", "Test User", is_repeater=False, size=38)
    assert not icon_letters.isNull()

    # Repeater should always return tactical radar regardless of style
    icon_rep = get_contact_avatar_icon("!rep_test", "Test Repeater", is_repeater=True, size=38)
    assert not icon_rep.isNull()

    # Restore default
    set_global_avatar_style("droid")

