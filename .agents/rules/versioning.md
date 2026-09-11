# Versioning Policy for MESHCORE NAVIGATOR

## Rules
1. **Base Version**: The project version starts at `0.0.2`.
2. **Commit Bumps (+0.0.1)**:
   - For ANY new commit to GitHub (bug fixes, styling tweaks, minor adjustments, documentation updates, routine improvements), increment the patch version by `0.0.1`.
   - Example: `0.0.2` -> `0.0.3`.
3. **Feature Bumps (+0.1.0)**:
   - When introducing a new feature to the application, increment the minor version by `0.1.0` (and reset patch to 0).
   - Example: `0.0.3` -> `0.1.0`.

## Files to Update
When bumping version, synchronize:
1. `meshcore_tray/__init__.py`: `__version__ = "X.Y.Z"`
2. `pyproject.toml`: `version = "X.Y.Z"`

Helper script available:
`python3 scripts/bump_version.py` (or `python3 scripts/bump_version.py --feature`)
