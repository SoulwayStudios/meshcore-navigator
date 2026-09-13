"""GitHub Release Version Checker for MESHCORE NAVIGATOR.

Checks for updates asynchronously against the GitHub repository releases API.
Provides cached, rate-limit safe version comparisons with non-blocking QThread execution.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import re
from typing import Optional

from packaging.version import parse as parse_version, InvalidVersion
from PyQt6.QtCore import QObject, QThread, pyqtSignal
import requests

from meshcore_tray import __version__

logger = logging.getLogger("meshcore_tray.version_checker")

GITHUB_RELEASES_API = "https://api.github.com/repos/SoulwayStudios/meshcore-navigator/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/SoulwayStudios/meshcore-navigator/releases"


@dataclass
class ReleaseInfo:
    """Metadata representing a GitHub release."""
    tag_name: str
    version: str
    name: str
    html_url: str
    published_at: str = ""
    body: str = ""
    is_newer: bool = False


def clean_version_str(ver: str) -> str:
    """Strips leading 'v' or 'V' and surrounding whitespace."""
    if not ver:
        return "0.0.0"
    return ver.strip().lstrip("vV")


def compare_versions(latest: str, current: str) -> bool:
    """Returns True if latest is strictly newer than current according to SemVer."""
    c_latest = clean_version_str(latest)
    c_current = clean_version_str(current)
    try:
        return parse_version(c_latest) > parse_version(c_current)
    except (InvalidVersion, TypeError):
        # Fallback numeric comparison only if both have leading major.minor digits
        m_latest = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", c_latest)
        m_current = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", c_current)
        if m_latest and m_current:
            t_latest = tuple(int(x or 0) for x in m_latest.groups())
            t_current = tuple(int(x or 0) for x in m_current.groups())
            return t_latest > t_current
        return False


class VersionCheckWorker(QThread):
    """Background QThread worker to fetch latest GitHub release without blocking UI."""

    check_completed = pyqtSignal(bool, object, str)  # (is_newer, Optional[ReleaseInfo], error_msg)

    def __init__(
        self,
        current_version: str = __version__,
        api_url: str = GITHUB_RELEASES_API,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.current_version = current_version
        self.api_url = api_url

    def run(self):
        try:
            headers = {
                "User-Agent": f"MeshCore-Navigator/{self.current_version}",
                "Accept": "application/vnd.github.v3+json",
            }
            logger.debug("Checking GitHub releases at %s", self.api_url)
            resp = requests.get(self.api_url, headers=headers, timeout=4.0)

            if resp.status_code == 200:
                data = resp.json()
                tag_name = data.get("tag_name", "")
                latest_clean = clean_version_str(tag_name)
                html_url = data.get("html_url", GITHUB_RELEASES_PAGE)
                name = data.get("name") or tag_name
                body = data.get("body", "")
                published_at = data.get("published_at", "")

                is_newer = compare_versions(latest_clean, self.current_version)
                info = ReleaseInfo(
                    tag_name=tag_name,
                    version=latest_clean,
                    name=name,
                    html_url=html_url,
                    published_at=published_at,
                    body=body,
                    is_newer=is_newer,
                )
                logger.info(
                    "Version check result: latest=%s, current=%s, is_newer=%s",
                    latest_clean,
                    self.current_version,
                    is_newer,
                )
                self.check_completed.emit(is_newer, info, "")

            elif resp.status_code == 404:
                logger.info("GitHub API returned 404: No releases found.")
                self.check_completed.emit(False, None, "No releases published yet on GitHub.")
            elif resp.status_code == 403:
                logger.warning("GitHub API rate limit reached.")
                self.check_completed.emit(False, None, "GitHub API rate limit reached. Try again later.")
            else:
                msg = f"GitHub returned HTTP {resp.status_code}."
                logger.warning(msg)
                self.check_completed.emit(False, None, msg)

        except requests.exceptions.Timeout:
            logger.debug("GitHub version check connection timed out.")
            self.check_completed.emit(False, None, "Connection to GitHub timed out.")
        except requests.exceptions.RequestException as e:
            logger.debug("GitHub version check network error: %s", e)
            self.check_completed.emit(False, None, f"Network error: {e}")
        except Exception as e:
            logger.exception("Unexpected error checking GitHub version: %s", e)
            self.check_completed.emit(False, None, f"Unexpected error: {e}")


class VersionChecker(QObject):
    """Manages version check requests, caching results, and notifying UI subscribers."""

    update_available = pyqtSignal(ReleaseInfo)
    check_finished = pyqtSignal(bool, object, str)  # (is_newer, Optional[ReleaseInfo], error_msg)

    _instance: Optional["VersionChecker"] = None

    @classmethod
    def get_instance(cls) -> "VersionChecker":
        if cls._instance is None:
            cls._instance = VersionChecker()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker: Optional[VersionCheckWorker] = None
        self._last_checked_at: Optional[datetime] = None
        self._cached_result: Optional[tuple] = None  # (is_newer, ReleaseInfo, error_msg)
        self._cache_ttl = timedelta(hours=1)

    def check_for_updates(
        self,
        force: bool = False,
        current_version: str = __version__,
        api_url: str = GITHUB_RELEASES_API,
    ):
        """Asynchronously triggers a version check or returns cached result if within TTL."""
        now = datetime.now(timezone.utc)

        # Return cached result if valid and not forcing refresh
        if not force and self._cached_result is not None and self._last_checked_at is not None:
            if now - self._last_checked_at < self._cache_ttl:
                is_newer, info, err = self._cached_result
                logger.debug("Returning cached version check result (is_newer=%s)", is_newer)
                self.check_finished.emit(is_newer, info, err)
                if is_newer and info:
                    self.update_available.emit(info)
                return

        # Avoid spawning concurrent workers
        if self._worker is not None and self._worker.isRunning():
            logger.debug("Version check worker already in flight, skipping duplicate.")
            return

        self._worker = VersionCheckWorker(current_version=current_version, api_url=api_url, parent=self)
        self._worker.check_completed.connect(self._on_worker_completed)
        self._worker.start()

    def _on_worker_completed(self, is_newer: bool, info: Optional[ReleaseInfo], error_msg: str):
        if not error_msg and info is not None:
            self._last_checked_at = datetime.now(timezone.utc)
            self._cached_result = (is_newer, info, error_msg)

        self.check_finished.emit(is_newer, info, error_msg)
        if is_newer and info:
            self.update_available.emit(info)

    def stop(self, timeout_ms: int = 500):
        """Waits for active worker thread to terminate cleanly."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(timeout_ms)
