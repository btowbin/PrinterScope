import json

import pytest

from cnc_microscope import session


def test_roundtrip_and_atomic_write(tmp_path):
    path = tmp_path / "sub" / "s.json"
    session.write_session(path, {"settings": {"repeats": 3}, "points": []})
    data = session.read_session(path)
    assert data["settings"]["repeats"] == 3 and data["version"] == session.SESSION_VERSION
    assert [p.name for p in path.parent.iterdir()] == ["s.json"]  # no temp file left behind


def test_overwrite_replaces_content(tmp_path):
    path = tmp_path / "s.json"
    session.write_session(path, {"settings": {"a": 1}})
    session.write_session(path, {"settings": {"a": 2}})
    assert session.read_session(path)["settings"]["a"] == 2


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '{"hello": 1}'])
def test_rejects_files_that_are_not_sessions(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        session.read_session(path)


def test_rejects_newer_version(tmp_path):
    path = tmp_path / "new.json"
    path.write_text(json.dumps({"version": session.SESSION_VERSION + 1, "settings": {}}))
    with pytest.raises(ValueError, match="newer"):
        session.read_session(path)


def test_settings_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CNC_MICROSCOPE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert session.autosave_path() == tmp_path / "cfg" / "last_session.json"
