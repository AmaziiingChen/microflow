# Changelog

All notable changes to MicroFlow will be documented in this file.

The project currently uses a lightweight `vX.Y.Z` version style. Before the first formal public release, the changelog is maintained manually.

## [Unreleased]

### Added

- Added a lightweight article-list payload mode and on-demand article detail hydration.
- Added unified `get_article_detail` backend entry for detail-page full content loading.
- Added backend normalization and validation before saving HTML / RSS custom rules.
- Added `pytest.ini` so the default automated test entry only collects `tests/`.
- Added release-preparation docs for versioning policy and database release baseline.

### Changed

- Deduplicated startup remote version / config checks.
- Switched custom rule loading to lazy initialization instead of eager startup fetch.
- Reduced search-result payload size to match list-page lightweight loading strategy.
- Unified article payload handling after edit / regenerate flows.
- Converted `tools/test_script_creator.py` into a manual utility script that reads API key from environment variables.

### Fixed

- Fixed the frontend syntax error in `frontend/js/app.js`.
- Fixed RSS regenerate-summary flow so annotation cleanup count remains compatible with mocked / non-numeric return values.
- Fixed default full-test execution being polluted by ad-hoc scripts under `tools/`.

### Validation

- `node --check frontend/js/app.js`
- `python3 -m py_compile src/api.py src/database.py`
- `/Users/chen/Code/MicroFlow/.venv/bin/pytest -q`
- Current default test result: `98 passed`

## [v1.1.3]

### Notes

- Current in-repo development version.
- Still treated as a pre-release development line rather than a formal public stable release.
