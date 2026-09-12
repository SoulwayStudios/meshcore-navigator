#!/usr/bin/env python3
"""Semantic Version bumping script for MESHCORE NAVIGATOR (SemVer 2.0.0).

Usage:
    python scripts/bump_version.py [--patch | --minor | --major | --feature]
    --patch (default): Increments patch version by 1 (e.g., 0.3.0 -> 0.3.1)
    --minor / --feature: Increments minor version by 1 (e.g., 0.3.0 -> 0.4.0)
    --major:           Increments major version by 1 (e.g., 0.3.0 -> 1.0.0)
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


def bump(current: str, mode: str = "patch") -> str:
    parts = [int(p) for p in current.split(".")]
    while len(parts) < 3:
        parts.append(0)

    major, minor, patch = parts[0], parts[1], parts[2]
    if mode == "major":
        major += 1
        minor = 0
        patch = 0
    elif mode in ("minor", "feature"):
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
    mode = "patch"
    if "--major" in sys.argv:
        mode = "major"
    elif "--minor" in sys.argv or "--feature" in sys.argv:
        mode = "minor"
    elif "--patch" in sys.argv:
        mode = "patch"

    current = get_current_version()
    new_version = bump(current, mode)
    update_files(new_version)


if __name__ == "__main__":
    main()
