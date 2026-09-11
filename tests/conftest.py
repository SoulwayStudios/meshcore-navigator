"""Global pytest configuration and fixtures for meshcore-navigator tests."""

import sys
import pytest
from PyQt6.QtWidgets import QApplication

@pytest.fixture(scope="session", autouse=True)
def ensure_qapp():
    """Ensure a global QApplication exists with valid non-empty arguments so QtWebEngine initializes cleanly."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["meshcore-test", "-platform", "offscreen"])
    return app
