from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "select_toolchain",
    Path(__file__).resolve().parents[1] / "sandbox" / "select_toolchain.py",
)
assert _SPEC is not None and _SPEC.loader is not None
toolchain = importlib.util.module_from_spec(_SPEC)
sys.modules["select_toolchain"] = toolchain
_SPEC.loader.exec_module(toolchain)

Version = toolchain.Version
Request = toolchain.Request

NODE = [
    Version(20, 20, 2),
    Version(22, 23, 3),
    Version(24, 21, 0),
]
PYTHON = [
    Version(3, 11, 16),
    Version(3, 12, 14),
    Version(3, 13, 15),
    Version(3, 14, 7),
]
GO = [
    Version(1, 24, 13),
    Version(1, 25, 14),
    Version(1, 26, 8),
    Version(1, 27, 1),
]


def test_node_pin_stays_on_that_major():
    assert toolchain.choose(NODE, Request(20), line="major") == Version(20, 20, 2)
    assert toolchain.choose(NODE, Request(20, 11, 1), line="major") == Version(20, 20, 2)
    assert toolchain.choose(NODE, Request(22, 14, 0), line="major") == Version(22, 23, 3)


def test_node_without_that_major_uses_the_closest():
    assert toolchain.choose(NODE, Request(18), line="major") == Version(20, 20, 2)
    assert toolchain.choose(NODE, Request(21), line="major") == Version(22, 23, 3)
    assert toolchain.choose(NODE, Request(26), line="major") == Version(24, 21, 0)


def test_python_and_go_match_the_minor_line():
    assert toolchain.choose(PYTHON, Request(3, 12, 1), line="minor") == Version(3, 12, 14)
    assert toolchain.choose(PYTHON, Request(3, 10), line="minor") == Version(3, 11, 16)
    assert toolchain.choose(GO, Request(1, 22, 0), line="minor") == Version(1, 24, 13)
    assert toolchain.choose(GO, Request(1, 26, 2), line="minor") == Version(1, 26, 8)
    assert toolchain.choose(GO, Request(1, 27, 1), line="minor") == Version(1, 27, 1)


def test_missing_pin_uses_the_newest_install():
    assert toolchain.choose(NODE, None, line="major") == Version(24, 21, 0)
    assert toolchain.choose(PYTHON, None, line="minor") == Version(3, 14, 7)


def test_repo_files_name_a_version(tmp_path: Path):
    (tmp_path / ".nvmrc").write_text("lts/iron\n")
    assert toolchain.detect_node(tmp_path) == Request(20)

    (tmp_path / ".nvmrc").write_text("20\n")
    assert toolchain.detect_node(tmp_path) == Request(20)

    (tmp_path / ".nvmrc").unlink()
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=22.12.0"}}')
    assert toolchain.detect_node(tmp_path) == Request(22, 12, 0)

    (tmp_path / ".python-version").write_text("3.12.3\n")
    assert toolchain.detect_python(tmp_path) == Request(3, 12, 3)

    (tmp_path / ".python-version").unlink()
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
    assert toolchain.detect_python(tmp_path) == Request(3, 11)

    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22.0\n\ntoolchain go1.26.2\n")
    assert toolchain.detect_go(tmp_path) == Request(1, 26, 2)


def test_activate_retargets_the_symlink(tmp_path: Path):
    home = tmp_path / "node"
    for version in NODE:
        (home / str(version) / "bin").mkdir(parents=True)
    link = tmp_path / "toolchain" / "node"
    message = toolchain.activate(
        tmp_path,
        "node",
        home,
        link,
        Request(20),
        line="major",
    )
    assert link.resolve() == (home / "20.20.2").resolve()
    assert message == "toolchain node 20.20.2 (requested 20)"
