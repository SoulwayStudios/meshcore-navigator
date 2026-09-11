#!/usr/bin/env python3
"""Version bumping script for MESHCORE NAVIGATOR.

Usage:
    python scripts/bump_version.py [--feature | --commit]
    --commit (default): Increments patch version by 0.0.1 (e.g., 0.0.2 -> 0.0.3)
    --feature:         Increments minor version by 0.1.0 (e.g., 0.0.2 -> 0.1.0)
"""

import sys
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_FILE = REPO_ROOT / "meshcore_tray" / "__init__.py"
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"


def get_current_version() -> str:
    content = INIT_FILE.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    if not match:
        raise ValueError(f"Could not find __version__ in {INIT_FILE}")
    return match.group(1)


def bump(current: str, is_feature: bool) -> str:
    parts = [int(p) for p in current.split(".")]
    while len(parts) < 3:
        parts.append(0)

    major, minor, patch = parts[0], parts[1], parts[2]
    if is_feature:
        minor += 1
        patch = 0
    else:
        patch += 1

    return f"{major}.{minor}.{patch}"


def update_files(new_version: str):
    # Update meshcore_tray/__init__.py
    init_content = INIT_FILE.read_text(encoding="utf-8")
    init_content = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"', init_content)
    INIT_FILE.write_text(init_content, encoding="utf-8")

    # Update pyproject.toml
    if PYPROJECT_FILE.exists():
        toml_content = PYPROJECT_FILE.read_text(encoding="utf-8")
        toml_content = re.sub(r'version\s*=\s*"[^"]+"', f'version = "{new_version}"', toml_content, count=1)
        PYPROJECT_FILE.write_text(toml_content, encoding="utf-8")

    print(f"✓ Version bumped to {new_version}")


def main():
    is_feature = "--feature" in sys.argv
    current = get_current_version()
    new_version = bump(current, is_feature)
    update_files(new_version)


if __name__ == "__main__":
    main()
