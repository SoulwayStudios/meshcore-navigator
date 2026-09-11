"""Crash Report Dialog and Global Unhandled Exception Handler for MESHCORE NAVIGATOR."""

from datetime import datetime, timezone
import logging
import platform
import subprocess
import sys
import traceback
import urllib.parse
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QFrame, QApplication
)

from meshcore_tray import __version__, __app_name__, __author__
from meshcore_tray.config import get_app_dir

logger = logging.getLogger("meshcore_tray.crash")

GITHUB_ISSUES_URL = "https://github.com/SoulwayStudios/meshcore-navigator/issues/new"


def build_system_info() -> dict:
    """Collects safe, non-sensitive diagnostic environment details."""
    return {
        "app_name": __app_name__,
        "app_version": f"v{__version__}",
        "author": __author__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "os": sys.platform,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_crash_markdown(exc_type, exc_val, exc_tb) -> str:
    """Formats an exception and traceback into a structured GitHub issue markdown report."""
    sys_info = build_system_info()
    tb_str = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))

    return f"""### ⚠️ Crash Report: {exc_type.__name__ if exc_type else 'Exception'}

**Error Message**: `{str(exc_val)}`

#### Diagnostic Environment
- **Application**: {sys_info['app_name']} {sys_info['app_version']}
- **Author**: {sys_info['author']}
- **Operating System**: `{sys_info['platform']}`
- **Python Version**: `{sys_info['python_version']}`
- **Timestamp (UTC)**: `{sys_info['timestamp']}`

#### Traceback
```python
{tb_str}
```

#### Steps to Reproduce
1. 
2. 
3. 

#### Additional Context
<!-- Add any details about connected hardware, settings, or recent operations -->
"""


def open_bug_report_in_browser(title: str = "Bug Report", body: str = ""):
    """Opens browser with pre-filled GitHub issue title and body."""
    if not body:
        sys_info = build_system_info()
        body = f"""### 🐛 Bug / Issue Description
<!-- Describe the problem you encountered -->

#### Diagnostic Environment
- **Application**: {sys_info['app_name']} {sys_info['app_version']}
- **OS**: `{sys_info['platform']}`
- **Python**: `{sys_info['python_version']}`
- **Timestamp (UTC)**: `{sys_info['timestamp']}`

#### Expected Behavior
<!-- What did you expect to happen? -->

#### Actual Behavior
<!-- What actually happened? -->
"""
    params = urllib.parse.urlencode({
        "title": title,
        "body": body
    })
    url = f"{GITHUB_ISSUES_URL}?{params}"
    QDesktopServices.openUrl(QUrl(url))


class CrashReportDialog(QDialog):
    """Modern modal dialog displayed when an unhandled application error occurs."""

    def __init__(self, exc_type, exc_val, exc_tb, parent=None):
        super().__init__(parent)
        self.exc_type = exc_type
        self.exc_val = exc_val
        self.exc_tb = exc_tb
        self.markdown_report = format_crash_markdown(exc_type, exc_val, exc_tb)

        self.setWindowTitle("⚠️ MESHCORE NAVIGATOR - Application Error")
        self.resize(720, 520)
        self.setMinimumSize(600, 420)
        self.setStyleSheet("""
            QDialog {
                background-color: #12141A;
                color: #FFFFFF;
            }
            QLabel {
                color: #E2E8F0;
            }
        """)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header Card
        header_card = QFrame()
        header_card.setStyleSheet("""
            QFrame {
                background-color: #1E222B;
                border: 1.5px solid #EF4444;
                border-radius: 12px;
                padding: 12px 16px;
            }
        """)
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(14)

        icon_lbl = QLabel("⚠️")
        icon_lbl.setStyleSheet("font-size: 32px; background: transparent; border: none;")
        h_layout.addWidget(icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        title_lbl = QLabel("An Unexpected Error Occurred")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #EF4444; background: transparent; border: none;")
        text_layout.addWidget(title_lbl)

        err_name = self.exc_type.__name__ if self.exc_type else "Error"
        err_msg = str(self.exc_val)
        desc_lbl = QLabel(f"<b>{err_name}</b>: {err_msg}")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 12px; color: #FCA5A5; background: transparent; border: none;")
        text_layout.addWidget(desc_lbl)

        h_layout.addLayout(text_layout, 1)
        layout.addWidget(header_card)

        # Traceback text area
        tb_label = QLabel("<b>Error Traceback & Diagnostics:</b>")
        tb_label.setStyleSheet("font-size: 12px; color: #94A3B8;")
        layout.addWidget(tb_label)

        self.tb_edit = QTextEdit()
        self.tb_edit.setReadOnly(True)
        self.tb_edit.setFont(QFont("monospace", 10))
        self.tb_edit.setText("".join(traceback.format_exception(self.exc_type, self.exc_val, self.exc_tb)))
        self.tb_edit.setStyleSheet("""
            QTextEdit {
                background-color: #0A0C10;
                color: #38BDF8;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        layout.addWidget(self.tb_edit, 1)

        # Action button row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_copy = QPushButton("📋 Copy Crash Report")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #475569;
            }
        """)
        self.btn_copy.clicked.connect(self._copy_report)
        btn_layout.addWidget(self.btn_copy)

        self.btn_github = QPushButton("🐛 Report Bug on GitHub")
        self.btn_github.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_github.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #38BDF8;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: 700;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
        """)
        self.btn_github.clicked.connect(self._report_github)
        btn_layout.addWidget(self.btn_github)

        btn_layout.addStretch()

        self.btn_restart = QPushButton("↺ Restart App")
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #FFFFFF;
            }
        """)
        self.btn_restart.clicked.connect(self._restart_app)
        btn_layout.addWidget(self.btn_restart)

        self.btn_close = QPushButton("Close")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #FFFFFF;
            }
        """)
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def _copy_report(self):
        QApplication.clipboard().setText(self.markdown_report)
        self.btn_copy.setText("✓ Copied to Clipboard!")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #065F46;
                color: #34D399;
                border: 1px solid #059669;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-size: 12px;
            }
        """)

    def _report_github(self):
        err_name = self.exc_type.__name__ if self.exc_type else "Error"
        title = f"[Crash]: {err_name}: {str(self.exc_val)[:60]}"
        open_bug_report_in_browser(title=title, body=self.markdown_report)

    def _restart_app(self):
        self.accept()
        try:
            subprocess.Popen([sys.executable] + sys.argv)
        except Exception as e:
            logger.error(f"Failed to restart application: {e}")
        QApplication.quit()


def install_crash_handler():
    """Installs a global uncaught exception hook that writes to crash.log and presents the CrashReportDialog."""
    old_hook = sys.excepthook

    def uncaught_exception_handler(exc_type, exc_val, exc_tb):
        # Ignore normal keyboard interrupt
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            old_hook(exc_type, exc_val, exc_tb)
            return

        # 1. Log to stderr and logfile
        logger.critical("Uncaught application exception:", exc_info=(exc_type, exc_val, exc_tb))
        crash_log_path = get_app_dir() / "crash.log"
        try:
            crash_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(crash_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(format_crash_markdown(exc_type, exc_val, exc_tb))
                f.write(f"\n{'='*60}\n")
        except Exception as e:
            print(f"Failed to write crash log: {e}", file=sys.stderr)

        # 2. Display interactive GUI crash dialog if Qt application is active
        app = QApplication.instance()
        if app is not None:
            try:
                dlg = CrashReportDialog(exc_type, exc_val, exc_tb)
                dlg.exec()
            except Exception as e:
                print(f"Error displaying crash dialog: {e}", file=sys.stderr)
        else:
            old_hook(exc_type, exc_val, exc_tb)

    sys.excepthook = uncaught_exception_handler
