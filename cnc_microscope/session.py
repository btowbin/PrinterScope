"""Remembered settings and saved sessions (plain JSON, written atomically)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

SESSION_VERSION = 1
APP_NAME = "cnc_microscope"


def settings_dir() -> Path:
    """Per-user folder for remembered settings; CNC_MICROSCOPE_CONFIG_DIR overrides it."""
    override = os.environ.get("CNC_MICROSCOPE_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "CNCMicroscope"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def autosave_path() -> Path:
    return settings_dir() / "last_session.json"


def write_session(path: Path, data: dict) -> None:
    """Write via a temporary file so a crash never leaves a half-written session."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"app": APP_NAME, "version": SESSION_VERSION, **data}
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_session(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not a valid session file: {exc}") from exc
    if not isinstance(data, dict) or not ({"settings", "points"} & data.keys()):
        raise ValueError("This file is not a microscope session")
    if int(data.get("version", 1)) > SESSION_VERSION:
        raise ValueError("This session was saved by a newer version of the software")
    return data
