"""Unit tests for SatelliteImageModal pan/zoom lightbox and clipboard/save hooks."""

import pytest
from pathlib import Path
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication

from meshcore_tray.ui.satellite_image_modal import SatelliteImageModal, PanZoomImageLabel


@pytest.fixture
def sample_image_path(tmp_path):
    from PIL import Image
    img_file = tmp_path / "sample_downlink.png"
    im = Image.new("RGB", (400, 300), color=(50, 100, 150))
    im.save(str(img_file))
    return str(img_file)


def test_satellite_image_modal_init(qapp, sample_image_path):
    modal = SatelliteImageModal(
        image_path=sample_image_path,
        title="NOAA-19 Downlinked APT Scan",
        subtitle="137.100 MHz FM Live Telemetry",
        metadata={
            "sensor": "AVHRR/3 Infrared Scanner",
            "downlink": "137.100 MHz FM APT",
            "acquired": "2026-09-18 14:00 UTC",
        },
    )

    assert "NOAA-19" in modal.windowTitle()
    assert modal.orig_pixmap.width() == 400
    assert modal.orig_pixmap.height() == 300
    assert modal.lbl_title.text() == "🛰️  NOAA-19 Downlinked APT Scan"
    assert modal.lbl_zoom_pct.text() != ""


def test_satellite_image_modal_zoom_and_fit(qapp, sample_image_path):
    modal = SatelliteImageModal(
        image_path=sample_image_path,
        title="Test Scan",
    )

    initial_zoom = modal.zoom_scale
    modal._zoom_in()
    assert modal.zoom_scale > initial_zoom

    modal._zoom_out()
    modal._zoom_out()
    assert modal.zoom_scale < initial_zoom

    modal._reset_zoom()
    assert modal.zoom_scale == 1.0
    assert modal.lbl_zoom_pct.text() == "100%"


def test_satellite_image_modal_clipboard_copy(qapp, sample_image_path):
    modal = SatelliteImageModal(
        image_path=sample_image_path,
        title="Test Scan",
    )

    modal._copy_image()
    assert modal.btn_copy.text() == "✓ Copied!"

    cb = QApplication.clipboard()
    pix = cb.pixmap()
    assert not pix.isNull()
    assert pix.width() == 400
    assert pix.height() == 300
