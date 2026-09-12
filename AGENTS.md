# AGENTS.md

## Repository Instructions & Versioning Rule

1. **Semantic Versioning Rule (SemVer 2.0.0)**:
   - Version format: `MAJOR.MINOR.PATCH` (e.g. `0.3.0`).
   - **PATCH** (e.g. `0.3.0` -> `0.3.1`): Backwards-compatible bug fixes, security patches, hardening, refactoring.
   - **MINOR** (e.g. `0.3.0` -> `0.4.0`): Backwards-compatible new features, new UI views, major capabilities.
   - **MAJOR** (e.g. `0.3.0` -> `1.0.0`): Breaking architectural, protocol, API, or database changes.
   - Files to update: `meshcore_tray/__init__.py` and `pyproject.toml`.
   - Script: `python3 scripts/bump_version.py [--patch | --minor | --major]`. Version bumps are made for releases or major milestones, not on every individual git commit.

2. **Early Development Disclaimer**:
   - Maintain the early development notice and Coffee link (`https://buymeacoffee.com/soulwaystudios`) in `README.md` and app About/Splash tabs.
