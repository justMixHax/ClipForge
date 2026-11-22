from __future__ import annotations

from pathlib import Path

import pytest

from app.security.paths import (PathSecurityError, has_allowed_extension, resolve_inside, safe_filename,
                                validate_import_path)

EXTS = (".mp4", ".mov", ".mkv", ".webm")


def test_safe_filename_drops_directory_parts():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename(r"C:\Users\someone\clip.mp4") == "clip.mp4"


def test_safe_filename_removes_characters_windows_rejects():
    assert safe_filename('my: "best" video?<>|*.mp4') == "my best video.mp4"


def test_safe_filename_never_returns_a_reserved_device_name():
    assert safe_filename("CON.mp4").lower() != "con.mp4"
    assert safe_filename("nul").lower() != "nul"


def test_safe_filename_falls_back_when_nothing_usable_is_left():
    assert safe_filename("  ..  ") == "file"
    assert safe_filename("") == "file"


def test_safe_filename_truncates_but_keeps_the_extension():
    name = safe_filename("a" * 400 + ".mkv")
    assert len(name) <= 120
    assert name.endswith(".mkv")


def test_extension_check_is_case_insensitive():
    assert has_allowed_extension("Stream.MP4", EXTS)
    assert not has_allowed_extension("notes.txt", EXTS)
    assert not has_allowed_extension("video.mp4.exe", EXTS)


def test_resolve_inside_accepts_paths_under_the_base(tmp_path: Path):
    assert resolve_inside(tmp_path, "uploads/abc/source.mp4") == tmp_path / "uploads" / "abc" / "source.mp4"


@pytest.mark.parametrize("bad", ["../outside.mp4", "uploads/../../outside.mp4", "/etc/passwd",
                                 r"C:\Windows\system32\cmd.exe", r"..\..\x", ""])
def test_resolve_inside_rejects_traversal_and_absolute_paths(tmp_path: Path, bad: str):
    with pytest.raises(PathSecurityError):
        resolve_inside(tmp_path, bad)


def test_import_path_accepts_an_existing_video_file(tmp_path: Path):
    f = tmp_path / "recording.mkv"
    f.write_bytes(b"x")
    assert validate_import_path(str(f), EXTS) == f


def test_import_path_strips_surrounding_quotes(tmp_path: Path):
    f = tmp_path / "recording.mp4"
    f.write_bytes(b"x")
    assert validate_import_path(f'"{f}"', EXTS) == f


def test_import_path_rejects_missing_files_folders_and_other_types(tmp_path: Path):
    with pytest.raises(PathSecurityError, match="not found"):
        validate_import_path(str(tmp_path / "nope.mp4"), EXTS)
    with pytest.raises(PathSecurityError, match="not a file"):
        validate_import_path(str(tmp_path), EXTS)
    script = tmp_path / "run.bat"
    script.write_text("echo hi")
    with pytest.raises(PathSecurityError, match="type"):
        validate_import_path(str(script), EXTS)


@pytest.mark.parametrize("bad", [r"\\server\share\video.mp4", r"\\?\C:\video.mp4", r"\\.\pipe\video.mp4",
                                 "relative/video.mp4", "http://example.com/video.mp4"])
def test_import_path_rejects_network_device_and_relative_paths(bad: str):
    with pytest.raises(PathSecurityError):
        validate_import_path(bad, EXTS)
