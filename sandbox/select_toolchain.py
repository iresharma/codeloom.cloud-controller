#!/usr/bin/env python3
"""Point the active toolchain at the version this repo asks for.

Each sandbox image already has several releases installed. After the clone,
this picks the same major (Node) or minor (Python, Go) line, or the closest
installed line when the requested one is not on the image. Nothing is downloaded.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_VERSION = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_GO_TOOLCHAIN = re.compile(r"(?m)^toolchain\s+go(\d+(?:\.\d+){1,2})\s*$")
_GO_LINE = re.compile(r"(?m)^go\s+(\d+(?:\.\d+){1,2})\s*$")
_REQUIRES_PYTHON = re.compile(
    r"(?m)^requires-python\s*=\s*[\"']([^\"']+)[\"']"
)

# nvm aliases. lts/* means "newest installed", which is the default.
_NODE_ALIASES = {
    "lts/hydrogen": 18,
    "lts/iron": 20,
    "lts/jod": 22,
    "lts/krypton": 24,
}
_NODE_DEFAULTS = {"lts/*", "lts", "node", "stable", "system", "default"}


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class Request:
    """A version named by the repo. Minor and patch are unset when omitted."""

    major: int
    minor: int | None = None
    patch: int | None = None

    def __str__(self) -> str:
        text = str(self.major)
        if self.minor is not None:
            text += f".{self.minor}"
        if self.patch is not None:
            text += f".{self.patch}"
        return text


def parse_request(text: str) -> Request | None:
    line = ""
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].strip().strip("\"'")
        if stripped:
            line = stripped
            break
    if not line:
        return None
    token = line.lower()
    if token in _NODE_DEFAULTS:
        return None
    alias = _NODE_ALIASES.get(token)
    if alias is not None:
        return Request(alias)
    match = _VERSION.search(line)
    if match is None:
        return None
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else None
    patch = int(match.group(3)) if match.group(3) is not None else None
    return Request(major, minor, patch)


def choose(installed: list[Version], request: Request | None, *, line: str) -> Version:
    """Pick an installed release.

    ``line`` is ``major`` for Node (20, 22, 24) and ``minor`` for Python and
    Go (3.12, 1.24). An exact line uses the newest patch of that line. Anything
    else uses the closest line, preferring the newer release when two are
    equally close.
    """
    if not installed:
        raise ValueError("no toolchains installed")
    if request is None:
        return max(installed)
    if line == "major":
        same = [version for version in installed if version.major == request.major]
        if same:
            return max(same)
        return min(
            installed,
            key=lambda version: (
                abs(version.major - request.major),
                -version.major,
                -version.minor,
                -version.patch,
            ),
        )
    if request.minor is None:
        same_major = [version for version in installed if version.major == request.major]
        return max(same_major or installed)
    same = [
        version
        for version in installed
        if version.major == request.major and version.minor == request.minor
    ]
    if same:
        if request.patch is not None:
            exact = [version for version in same if version.patch == request.patch]
            if exact:
                return exact[0]
        return max(same)
    return min(
        installed,
        key=lambda version: (
            abs(version.major - request.major),
            abs(version.minor - request.minor),
            -version.major,
            -version.minor,
            -version.patch,
        ),
    )


def _read(path: Path, limit: int = 65_536) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _tool_version(root: Path, names: set[str]) -> str | None:
    text = _read(root / ".tool-versions")
    if not text:
        return None
    for raw in text.splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0].lower() in names:
            return parts[1]
    return None


def _package_json(root: Path) -> dict | None:
    text = _read(root / "package.json")
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def detect_node(root: Path) -> Request | None:
    for name in (".nvmrc", ".node-version"):
        text = _read(root / name)
        if text and text.strip():
            return parse_request(text)
    pinned = _tool_version(root, {"nodejs", "node"})
    if pinned:
        return parse_request(pinned)
    package = _package_json(root)
    if package is None:
        return None
    volta = package.get("volta")
    if isinstance(volta, dict) and volta.get("node"):
        return parse_request(str(volta["node"]))
    engines = package.get("engines")
    if isinstance(engines, dict) and engines.get("node"):
        return parse_request(str(engines["node"]))
    return None


def detect_python(root: Path) -> Request | None:
    pinned = _read(root / ".python-version")
    if pinned and pinned.strip():
        return parse_request(pinned)
    tool = _tool_version(root, {"python"})
    if tool:
        return parse_request(tool)
    runtime = _read(root / "runtime.txt")
    if runtime and runtime.strip():
        return parse_request(runtime)
    project = _read(root / "pyproject.toml")
    if project:
        match = _REQUIRES_PYTHON.search(project)
        if match:
            return parse_request(match.group(1))
    return None


def detect_go(root: Path) -> Request | None:
    text = _read(root / "go.mod")
    if text:
        toolchain = _GO_TOOLCHAIN.search(text)
        if toolchain:
            return parse_request(toolchain.group(1))
        language = _GO_LINE.search(text)
        if language:
            return parse_request(language.group(1))
    tool = _tool_version(root, {"go", "golang"})
    if tool:
        return parse_request(tool)
    return None


def installed_versions(home: Path) -> list[Version]:
    if not home.is_dir():
        return []
    found: list[Version] = []
    for child in home.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        request = parse_request(child.name)
        if request is None or request.minor is None or request.patch is None:
            continue
        found.append(Version(request.major, request.minor, request.patch))
    return found


def activate(
    workspace: Path,
    family: str,
    home: Path,
    link: Path,
    request: Request | None,
    *,
    line: str,
) -> str | None:
    installed = installed_versions(home)
    if not installed:
        return None
    chosen = choose(installed, request, line=line)
    target = home / str(chosen)
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(link.name + ".next")
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    temporary.symlink_to(target)
    temporary.replace(link)
    why = f"requested {request}" if request is not None else "default"
    message = f"toolchain {family} {chosen} ({why})"
    print(message, file=sys.stderr)
    return message


def select(workspace: Path) -> None:
    activate(
        workspace,
        "node",
        Path("/opt/node"),
        Path("/opt/toolchain/node"),
        detect_node(workspace),
        line="major",
    )
    activate(
        workspace,
        "python",
        Path("/opt/python"),
        Path("/opt/toolchain/python"),
        detect_python(workspace),
        line="minor",
    )
    activate(
        workspace,
        "go",
        Path("/opt/go"),
        Path("/opt/toolchain/go"),
        detect_go(workspace),
        line="minor",
    )


def main() -> None:
    workspace = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace")
    select(workspace)


if __name__ == "__main__":
    main()
