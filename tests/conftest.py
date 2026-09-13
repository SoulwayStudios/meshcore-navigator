import os
import sys
import pytest
from PyQt6.QtWidgets import QApplication

os.environ["MESHCORE_TEST_MODE"] = "1"

# When running headlessly with offscreen platform, disconnect from any display to prevent GLX conflicts
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"

@pytest.fixture(scope="session", autouse=True)
def ensure_qapp():
    """Ensure a global QApplication exists with valid non-empty arguments so QtWebEngine initializes cleanly."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test", "-platform", "offscreen"])
    return app
