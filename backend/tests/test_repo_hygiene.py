"""What goes into the repository is fit to be public: no folder path from the PC it was written on (which would
carry a user name with it), and none of the files that hold secrets or personal data.

These look at the files Git tracks or would add, so they are skipped where there is no Git checkout."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
NOT_TEXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".mp4", ".mov", ".ttf", ".otf", ".woff", ".woff2", ".onnx", ".db",
            ".npz", ".wav", ".pdf", ".zip"}


def repository_files() -> list[Path]:
    """Every file Git tracks, plus new ones it would add (ignored files are left out)."""
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a Git checkout")
    listed = subprocess.run([git, "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
                            capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return [ROOT / name for name in listed.split("\0") if name and (ROOT / name).is_file()]


def test_no_file_carries_a_folder_path_from_the_pc_it_was_written_on():
    home = Path.home()
    here = {str(home), home.as_posix(), str(ROOT), ROOT.as_posix()}
    here |= {path.replace("\\", "\\\\") for path in here}          # as it is written inside JSON
    needles = {path.lower() for path in here if len(path) > 3}       # never just "C:\" or "/"
    found = []
    for path in repository_files():
        if path.suffix.lower() in NOT_TEXT or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(needle in text for needle in needles):
            found.append(path.relative_to(ROOT).as_posix())

    assert found == [], "these files name a folder on this PC (write the path from the project folder instead)"


def test_nothing_that_holds_secrets_or_your_own_data_is_in_the_repository():
    names = [path.relative_to(ROOT).as_posix() for path in repository_files()]

    private = [name for name in names
               if name == ".env" or name.startswith("data/") or name.endswith((".key", ".pem", ".db", ".sqlite"))
               or name == ".venv-path" or name.startswith((".claude/", "graphify-out/"))]
    assert private == []
    assert ".env.example" in names                                    # the template, with no secret in it


def test_the_settings_template_holds_no_secret():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")

    for line in template.splitlines():
        name, _, value = line.partition("=")
        if line.lstrip().startswith("#") or not value:
            continue
        assert not any(word in name.upper() for word in ("KEY", "SECRET", "TOKEN", "PASSWORD")), name
