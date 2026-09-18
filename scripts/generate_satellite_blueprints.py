"""
Cyberpunk Neon Linework Blueprint Generator for MeshCore Satellites.
Generates technical HUD schematics for Cubesats, Amateur Repeaters, Weather Satellites, and Space Stations.
"""
import os
import sys
from pathlib import Path
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPixmap,
    QPainterPath, QLinearGradient, QRadialGradient
)
from PyQt6.QtWidgets import QApplication

def create_cyberpunk_blueprint(sat_type: str, output_path: Path):
    pix = QPixmap(520, 520)
    pix.fill(QColor(11, 15, 23)) # Cyber dark background

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Color themes
    if sat_type == "cubesat":
        neon_pri = QColor(192, 132, 252)   # Neon Purple/Violet
        neon_sec = QColor(232, 121, 249)   # Neon Magenta
        neon_glow = QColor(168, 85, 247, 60)
        grid_col = QColor(46, 26, 71, 90)
        title = "3U NANOSAT PLATFORM // SCHEMATIC"
        cat_badge = "CATEGORY: CUBESAT / 3U BUS"
        tag1 = "[CHASSIS: 100x100x340 mm AL-7075]"
        tag2 = "[RF: 435-438 MHz HELICAL + DIPOLE]"
        tag3 = "[PWR: TRIPLE-JUNCTION GAAS 24W]"
        tag4 = "[PAYLOAD: SDR LORAMON / TRANSCEIVER]"
    elif sat_type == "amateur":
        neon_pri = QColor(52, 211, 153)    # Neon Emerald
        neon_sec = QColor(16, 185, 129)    # Emerald Dark
        neon_glow = QColor(52, 211, 153, 60)
        grid_col = QColor(16, 60, 40, 90)
        title = "AMSAT OSCAR // LEO TRANSPONDER"
        cat_badge = "CATEGORY: AMATEUR RADIO RELAY"
        tag1 = "[ARCH: LINEAR V/U TRANSPONDER]"
        tag2 = "[UPLINK: 145.900 MHz FM/SSB]"
        tag3 = "[DOWNLINK: 435.250 MHz 500mW]"
        tag4 = "[ORBIT: 500-800 KM INCLINED LEO]"
    elif sat_type == "weather":
        neon_pri = QColor(251, 191, 36)    # Neon Amber
        neon_sec = QColor(245, 158, 11)    # Amber Dark
        neon_glow = QColor(245, 158, 11, 60)
        grid_col = QColor(60, 45, 15, 90)
        title = "POES POLAR METEOROLOGICAL BUS"
        cat_badge = "CATEGORY: WEATHER / EARTH OBS"
        tag1 = "[PAYLOAD: AVHRR/3 RADIOMETER]"
        tag2 = "[DOWNLINK: 137.100 MHz APT FM]"
        tag3 = "[RESOLUTION: 1.1 KM @ 850 KM]"
        tag4 = "[REGIME: SUN-SYNCHRONOUS POLAR]"
    elif sat_type == "stations":
        neon_pri = QColor(56, 189, 248)    # Neon Cyan
        neon_sec = QColor(14, 165, 233)    # Cyan Dark
        neon_glow = QColor(56, 189, 248, 60)
        grid_col = QColor(15, 45, 65, 90)
        title = "ORBITAL HABITAT // RESEARCH COMPLEX"
        cat_badge = "CATEGORY: CREWED SPACE STATION"
        tag1 = "[MODULES: PRESSURIZED MULTI-NODE]"
        tag2 = "[COMMS: ARISS PACKET 145.825 MHz]"
        tag3 = "[SOLAR: 8 WINGS / 120 KW ARRAY]"
        tag4 = "[ALTITUDE: 418 KM // INCL: 51.64°]"
    else:
        neon_pri = QColor(56, 189, 248)
        neon_sec = QColor(52, 211, 153)
        neon_glow = QColor(56, 189, 248, 60)
        grid_col = QColor(20, 40, 50, 90)
        title = "AUTONOMOUS RF SATELLITE RELAY"
        cat_badge = "CATEGORY: COMMUNICATIONS BUS"
        tag1 = "[RF: MULTI-BAND TRANSPONDER]"
        tag2 = "[ATTITUDE: 3-AXIS REACTION WHEELS]"
        tag3 = "[POWER: DUAL ARTICULATED ARRAYS]"
        tag4 = "[ORBIT: LOW EARTH TELEMETRY]"

    # 1. Subtle Cyber Grid
    grid_pen = QPen(grid_col, 1, Qt.PenStyle.DotLine)
    painter.setPen(grid_pen)
    for x in range(0, 520, 26):
        painter.drawLine(x, 0, x, 520)
    for y in range(0, 520, 26):
        painter.drawLine(0, y, 520, y)

    # 2. Outer Technical Border & Corner Brackets
    border_pen = QPen(QColor(neon_pri.red(), neon_pri.green(), neon_pri.blue(), 120), 1.5)
    painter.setPen(border_pen)
    painter.drawRect(18, 18, 484, 484)

    # Glowing Corner Brackets
    bracket_pen = QPen(neon_pri, 2.5)
    painter.setPen(bracket_pen)
    b_len = 24
    # Top-Left
    painter.drawLine(14, 14, 14 + b_len, 14)
    painter.drawLine(14, 14, 14, 14 + b_len)
    # Top-Right
    painter.drawLine(506 - b_len, 14, 506, 14)
    painter.drawLine(506, 14, 506, 14 + b_len)
    # Bottom-Left
    painter.drawLine(14, 506, 14 + b_len, 506)
    painter.drawLine(14, 506, 14, 506 - b_len)
    # Bottom-Right
    painter.drawLine(506 - b_len, 506, 506, 506)
    painter.drawLine(506, 506, 506, 506 - b_len)

    # 3. HUD Header & Badges
    painter.setFont(QFont("DejaVu Sans Mono", 11, QFont.Weight.Bold))
    painter.setPen(neon_pri)
    painter.drawText(30, 44, title)

    painter.setFont(QFont("DejaVu Sans Mono", 8, QFont.Weight.DemiBold))
    painter.setPen(neon_sec)
    painter.drawText(30, 60, cat_badge)

    # Technical Crosshairs
    painter.setPen(QPen(QColor(neon_pri.red(), neon_pri.green(), neon_pri.blue(), 100), 1))
    painter.drawLine(250, 260, 270, 260)
    painter.drawLine(260, 250, 260, 270)
    painter.drawEllipse(252, 252, 16, 16)

    # Helper function for drawing glowing lines
    def draw_glow_line(x1, y1, x2, y2, width=2, color=neon_pri):
        # Outer glow
        glow_pen = QPen(QColor(color.red(), color.green(), color.blue(), 45), width + 5)
        painter.setPen(glow_pen)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        # Mid glow
        mid_pen = QPen(QColor(color.red(), color.green(), color.blue(), 110), width + 2)
        painter.setPen(mid_pen)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        # Sharp core
        core_pen = QPen(QColor(255, 255, 255, 230), width)
        painter.setPen(core_pen)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    def draw_glow_rect(x, y, w, h, width=2, color=neon_pri, fill=None):
        if fill:
            painter.fillRect(int(x), int(y), int(w), int(h), fill)
        glow_pen = QPen(QColor(color.red(), color.green(), color.blue(), 45), width + 4)
        painter.setPen(glow_pen)
        painter.drawRect(int(x), int(y), int(w), int(h))
        core_pen = QPen(color, width)
        painter.setPen(core_pen)
        painter.drawRect(int(x), int(y), int(w), int(h))

    # 4. Satellite Specific Vector Cyberpunk Linework
    if sat_type == "cubesat":
        # Isometric 3U CubeSat Chassis
        # Center = (260, 240)
        # Main front body face
        path_body_front = QPainterPath()
        path_body_front.moveTo(220, 150)
        path_body_front.lineTo(300, 195)
        path_body_front.lineTo(300, 365)
        path_body_front.lineTo(220, 320)
        path_body_front.closeSubpath()
        painter.fillPath(path_body_front, QColor(25, 15, 38, 180))

        # Body side face
        path_body_side = QPainterPath()
        path_body_side.moveTo(220, 150)
        path_body_side.lineTo(160, 185)
        path_body_side.lineTo(160, 355)
        path_body_side.lineTo(220, 320)
        path_body_side.closeSubpath()
        painter.fillPath(path_body_side, QColor(35, 20, 55, 180))

        # Body top face
        path_body_top = QPainterPath()
        path_body_top.moveTo(220, 150)
        path_body_top.lineTo(300, 195)
        path_body_top.lineTo(240, 230)
        path_body_top.lineTo(160, 185)
        path_body_top.closeSubpath()
        painter.fillPath(path_body_top, QColor(48, 28, 75, 180))

        # Outline Body
        for p1, p2 in [
            ((220, 150), (300, 195)), ((300, 195), (300, 365)),
            ((300, 365), (220, 320)), ((220, 320), (220, 150)),
            ((220, 150), (160, 185)), ((160, 185), (160, 355)),
            ((160, 355), (220, 320)), ((300, 195), (240, 230)),
            ((240, 230), (160, 185))
        ]:
            draw_glow_line(p1[0], p1[1], p2[0], p2[1], 2, neon_pri)

        # 3U division lines on front face
        draw_glow_line(220, 207, 300, 252, 1.5, neon_sec)
        draw_glow_line(220, 264, 300, 309, 1.5, neon_sec)

        # Solar cell grid hashes on front face
        cell_pen = QPen(QColor(neon_sec.red(), neon_sec.green(), neon_sec.blue(), 130), 1, Qt.PenStyle.DashLine)
        painter.setPen(cell_pen)
        painter.drawLine(260, 172, 260, 342)

        # Deployable Tape Measure Antennas (4 extending out in diagonal space)
        draw_glow_line(300, 365, 390, 415, 2.5, QColor(255, 255, 255))
        draw_glow_line(160, 355, 70, 405, 2.5, QColor(255, 255, 255))
        draw_glow_line(220, 150, 220, 75, 2.5, QColor(255, 255, 255))
        draw_glow_line(240, 230, 240, 310, 1.5, neon_sec)

        # Deployed Solar Wings
        path_wing_r = QPainterPath()
        path_wing_r.moveTo(300, 220)
        path_wing_r.lineTo(440, 180)
        path_wing_r.lineTo(440, 320)
        path_wing_r.lineTo(300, 360)
        path_wing_r.closeSubpath()
        painter.fillPath(path_wing_r, QColor(25, 12, 40, 190))
        for p1, p2 in [((300, 220), (440, 180)), ((440, 180), (440, 320)), ((440, 320), (300, 360))]:
            draw_glow_line(p1[0], p1[1], p2[0], p2[1], 2, neon_pri)
        # Solar wing cells
        draw_glow_line(370, 200, 370, 340, 1.5, neon_sec)
        painter.setPen(cell_pen)
        painter.drawLine(335, 210, 335, 350)
        painter.drawLine(405, 190, 405, 330)

        path_wing_l = QPainterPath()
        path_wing_l.moveTo(160, 215)
        path_wing_l.lineTo(25, 175)
        path_wing_l.lineTo(25, 315)
        path_wing_l.lineTo(160, 355)
        path_wing_l.closeSubpath()
        painter.fillPath(path_wing_l, QColor(25, 12, 40, 190))
        for p1, p2 in [((160, 215), (25, 175)), ((25, 175), (25, 315)), ((25, 315), (160, 355))]:
            draw_glow_line(p1[0], p1[1], p2[0], p2[1], 2, neon_pri)
        draw_glow_line(92, 195, 92, 335, 1.5, neon_sec)

        # Dimension leader lines
        dim_pen = QPen(QColor(232, 121, 249, 140), 1, Qt.PenStyle.DashDotLine)
        painter.setPen(dim_pen)
        painter.drawLine(315, 195, 330, 195)
        painter.drawLine(315, 365, 330, 365)
        painter.drawLine(325, 195, 325, 365)

    elif sat_type == "amateur":
        # Hexagonal / Microsat Bus with Turnstile Antennas & Horizon Projection
        # Central Chassis
        path_hex = QPainterPath()
        hex_pts = [(260, 170), (330, 210), (330, 290), (260, 330), (190, 290), (190, 210)]
        path_hex.moveTo(hex_pts[0][0], hex_pts[0][1])
        for pt in hex_pts[1:]:
            path_hex.lineTo(pt[0], pt[1])
        path_hex.closeSubpath()
        painter.fillPath(path_hex, QColor(10, 35, 25, 180))

        # Hexagon inner facets
        for pt in hex_pts:
            draw_glow_line(260, 250, pt[0], pt[1], 1.5, neon_sec)

        for i in range(len(hex_pts)):
            p1 = hex_pts[i]
            p2 = hex_pts[(i + 1) % len(hex_pts)]
            draw_glow_line(p1[0], p1[1], p2[0], p2[1], 2, neon_pri)

        # Crossed Turnstile Antennas (4 large elements extending outward at 45 deg)
        draw_glow_line(260, 170, 260, 70, 2.5, QColor(255, 255, 255))
        draw_glow_line(330, 210, 420, 160, 2.5, QColor(255, 255, 255))
        draw_glow_line(330, 290, 420, 340, 2.5, QColor(255, 255, 255))
        draw_glow_line(190, 290, 100, 340, 2.5, QColor(255, 255, 255))
        draw_glow_line(190, 210, 100, 160, 2.5, QColor(255, 255, 255))

        # Phasing Rings at Antenna Tips
        for cx, cy in [(260, 70), (420, 160), (420, 340), (100, 340), (100, 160)]:
            painter.setPen(QPen(neon_pri, 2))
            painter.drawEllipse(cx - 5, cy - 5, 10, 10)

        # Radio wave downlink beacon rings radiating downwards
        wave_pen = QPen(QColor(52, 211, 153, 90), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(wave_pen)
        painter.drawArc(160, 320, 200, 100, 200 * 16, 140 * 16)
        painter.drawArc(120, 340, 280, 140, 200 * 16, 140 * 16)
        painter.drawArc(80, 360, 360, 180, 200 * 16, 140 * 16)

    elif sat_type == "weather":
        # POES Weather Satellite: Boxy instrument bus + Single huge articulated solar sail wing
        # Main Body
        draw_glow_rect(190, 210, 120, 140, 2, neon_pri, fill=QColor(40, 30, 10, 180))

        # Radiometer Scanner Mirror Aperture (AVHRR drum)
        painter.setPen(QPen(neon_pri, 2))
        painter.setBrush(QBrush(QColor(245, 158, 11, 70)))
        painter.drawEllipse(230, 280, 40, 40)
        painter.drawLine(250, 280, 250, 320)
        painter.drawLine(230, 300, 270, 300)

        # Single huge solar wing extending to right
        draw_glow_line(310, 240, 350, 240, 3, neon_sec) # Boom
        draw_glow_rect(350, 170, 120, 160, 2, neon_pri, fill=QColor(35, 25, 10, 190))
        # Wing cell divisions
        draw_glow_line(390, 170, 390, 330, 1.5, neon_sec)
        draw_glow_line(430, 170, 430, 330, 1.5, neon_sec)
        draw_glow_line(350, 223, 470, 223, 1, neon_sec)
        draw_glow_line(350, 276, 470, 276, 1, neon_sec)

        # APT Helical / Nadir Antennas pointing downwards
        draw_glow_line(210, 350, 210, 420, 2.5, QColor(255, 255, 255))
        draw_glow_line(290, 350, 290, 410, 2.5, QColor(255, 255, 255))
        # Helix spirals
        for y_sp in range(365, 415, 10):
            painter.drawArc(203, y_sp, 14, 8, 0, 180 * 16)

        # Radiometer scanning cone to Earth
        scan_pen = QPen(QColor(251, 191, 36, 80), 1, Qt.PenStyle.DashDotLine)
        painter.setPen(scan_pen)
        painter.drawLine(250, 320, 100, 470)
        painter.drawLine(250, 320, 400, 470)
        painter.drawArc(100, 440, 300, 60, 200 * 16, 140 * 16)

    elif sat_type == "stations":
        # Space Station Complex: Central truss, Dual mega solar arrays, pressurized module cluster
        # Central Integrated Truss
        draw_glow_rect(90, 245, 340, 20, 2, neon_pri, fill=QColor(15, 40, 60, 190))
        # Truss diagonals
        truss_pen = QPen(QColor(neon_sec.red(), neon_sec.green(), neon_sec.blue(), 140), 1.5)
        painter.setPen(truss_pen)
        for tx in range(100, 420, 20):
            painter.drawLine(tx, 245, tx + 20, 265)
            painter.drawLine(tx + 20, 245, tx, 265)

        # Central pressurized modules (vertical stack)
        draw_glow_rect(240, 190, 40, 130, 2, neon_pri, fill=QColor(20, 50, 75, 200))
        draw_glow_rect(230, 220, 60, 35, 2, neon_sec, fill=QColor(25, 60, 90, 200))

        # Cupola Viewing Bay on Nadir (bottom)
        painter.setPen(QPen(neon_pri, 2))
        painter.setBrush(QBrush(QColor(56, 189, 248, 80)))
        painter.drawChord(245, 310, 30, 24, 180 * 16, 180 * 16)

        # Massive Port & Starboard Photovoltaic Wings (4 pairs)
        # Port Solar Wings
        draw_glow_rect(40, 120, 50, 110, 2, neon_pri, fill=QColor(10, 35, 55, 190))
        draw_glow_rect(40, 280, 50, 110, 2, neon_pri, fill=QColor(10, 35, 55, 190))
        # Starboard Solar Wings
        draw_glow_rect(430, 120, 50, 110, 2, neon_pri, fill=QColor(10, 35, 55, 190))
        draw_glow_rect(430, 280, 50, 110, 2, neon_pri, fill=QColor(10, 35, 55, 190))

        # Solar cell lines
        for wx in [40, 430]:
            draw_glow_line(wx + 25, 120, wx + 25, 230, 1.2, neon_sec)
            draw_glow_line(wx + 25, 280, wx + 25, 390, 1.2, neon_sec)

        # Radiator Panels
        draw_glow_rect(170, 195, 35, 45, 1.5, neon_sec)
        draw_glow_rect(315, 195, 35, 45, 1.5, neon_sec)

    else:
        # Generic Satellite Platform
        draw_glow_rect(210, 200, 100, 110, 2, neon_pri, fill=QColor(20, 35, 50, 190))
        # Parabolic Dish
        painter.setPen(QPen(neon_pri, 2.5))
        painter.setBrush(QBrush(QColor(56, 189, 248, 60)))
        painter.drawChord(210, 130, 100, 50, 0, 180 * 16)
        draw_glow_line(260, 155, 260, 200, 2, neon_sec)
        # Dual solar wings
        draw_glow_rect(80, 220, 110, 70, 2, neon_pri, fill=QColor(15, 30, 45, 190))
        draw_glow_rect(330, 220, 110, 70, 2, neon_pri, fill=QColor(15, 30, 45, 190))
        draw_glow_line(135, 220, 135, 290, 1.5, neon_sec)
        draw_glow_line(385, 220, 385, 290, 1.5, neon_sec)

    # 5. Technical Telemetry Callout Tags (Bottom HUD)
    painter.setFont(QFont("DejaVu Sans Mono", 8, QFont.Weight.Medium))
    hud_bg = QColor(16, 22, 34, 210)
    painter.fillRect(28, 410, 464, 76, hud_bg)
    painter.setPen(QPen(QColor(neon_pri.red(), neon_pri.green(), neon_pri.blue(), 90), 1))
    painter.drawRect(28, 410, 464, 76)

    painter.setPen(neon_pri)
    painter.drawText(38, 430, tag1)
    painter.drawText(260, 430, tag2)
    painter.setPen(neon_sec)
    painter.drawText(38, 454, tag3)
    painter.drawText(260, 454, tag4)

    painter.setFont(QFont("DejaVu Sans Mono", 7))
    painter.setPen(QColor(156, 163, 175))
    painter.drawText(38, 474, "STATUS: NOMINAL [TELEMETRY ENCODED // CYBERPUNK HUD v2.0]")

    painter.end()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_path), "PNG")
    print(f"Generated {output_path} ({output_path.stat().st_size} bytes)")

def main():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)
    out_dir = Path("meshcore_tray/ui/static/satellites")

    types = ["cubesat", "amateur", "weather", "stations", "generic"]
    for t in types:
        p = out_dir / f"blueprint_{t}.png"
        create_cyberpunk_blueprint(t, p)

if __name__ == "__main__":
    main()
