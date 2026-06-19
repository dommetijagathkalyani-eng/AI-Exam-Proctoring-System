# Proctoring System - Folder Workflow and Cleanup Plan

This document separates the project into two categories:

- Source code and templates (should be committed)
- Runtime/generated data (should not be committed)

## Current Workflow Paths

- App entry: `app.py`
- Core logic: `models/`
- DB access: `database/database.py`
- UI: `templates/` + `static/`
- Runtime session output: `logs/session_<exam_id>_<student_id>/`
- Face registry output: `known_faces/`

## Source-of-Truth Folders (Commit These)

- `app.py`
- `models/`
- `database/database.py`
- `templates/`
- `static/`
- `scripts/`
- `tests/`
- `PROJECT_STRUCTURE.md`

## Runtime/Generated Folders (Ignore These)

- `logs/`
- `database/*.db`
- `__pycache__/` and `*.pyc`
- generated images in `known_faces/`
- diff/debug exports (`*_diff.txt`, `registered_face.jpg`, etc.)

## Recommended Target Layout (Phase 2)

Use this layout in a future refactor to make boundaries explicit:

- `src/`
  - `app.py`
  - `models/`
  - `database/`
  - `templates/`
  - `static/`
- `data/`
  - `database/`
  - `known_faces/`
  - `logs/`
- `scripts/`
- `tests/`
- `docs/`

## Low-Risk Refactor Steps

1. Move runtime output roots behind env vars:
   - `PROCTORING_DB_PATH`
   - `PROCTORING_LOGS_DIR`
   - `PROCTORING_FACES_DIR`
2. Default all three to `data/...` paths.
3. Keep backward-compatible fallbacks for existing folders during migration.
4. Move existing files from old paths to new paths once app runs cleanly.

## What Was Done Now

- Added `.gitignore` rules to keep runtime and generated files out of git.

## Optional Next Changes I Can Apply

- Add `config.py` for centralized path config.
- Update `app.py`, `database/database.py`, `models/face_registration.py`, and `models/proctoring_systems.py` to use env-driven paths.
- Add automatic creation of `data/database`, `data/logs`, `data/known_faces`.
