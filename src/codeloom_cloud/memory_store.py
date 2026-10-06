from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class MemoryStore(Protocol):
    """Per-project agent memory, owned by the controller.

    The engine is stateless across runs: it is seeded from here at session start
    and streams its memory back for the controller to persist. Implementations
    are interchangeable (local files now, an object store such as S3 later).
    """

    def load(self, project_id: str) -> dict | None: ...

    def save(self, project_id: str, data: dict) -> None: ...

    def delete(self, project_id: str) -> None: ...


class FileMemoryStore:
    """One JSON file per project under ``root`` (``<root>/<project_id>.json``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, project_id: str) -> Path:
        if not _SAFE_KEY.match(project_id or ""):
            raise ValueError(f"unsafe project id: {project_id!r}")
        return self.root / f"{project_id}.json"

    def load(self, project_id: str) -> dict | None:
        try:
            raw = json.loads(self._path(project_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("unreadable memory for project %s; starting empty", project_id)
            return None
        return raw if isinstance(raw, dict) else None

    def save(self, project_id: str, data: dict) -> None:
        path = self._path(project_id)
        self.root.mkdir(parents=True, exist_ok=True)
        # Atomic within the directory: write a sibling temp file then rename.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def delete(self, project_id: str) -> None:
        self._path(project_id).unlink(missing_ok=True)
