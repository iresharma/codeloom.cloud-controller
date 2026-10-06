from __future__ import annotations

import json

import pytest

from codeloom_cloud.memory_store import FileMemoryStore


def test_roundtrip_and_delete(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    assert store.load("proj1") is None  # nothing stored yet

    payload = {"engineering": [{"text": "orchestrator binds the loop"}], "files": {}}
    store.save("proj1", payload)
    assert store.load("proj1") == payload
    assert (tmp_path / "memory" / "proj1.json").is_file()

    store.delete("proj1")
    assert store.load("proj1") is None
    store.delete("proj1")  # idempotent


def test_projects_are_isolated(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    store.save("a", {"files": {"x": {}}})
    store.save("b", {"files": {"y": {}}})
    assert store.load("a") == {"files": {"x": {}}}
    assert store.load("b") == {"files": {"y": {}}}


def test_corrupt_file_loads_as_none(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    store.save("proj", {"files": {}})
    (tmp_path / "memory" / "proj.json").write_text("{not json", encoding="utf-8")
    assert store.load("proj") is None


def test_non_dict_payload_on_disk_is_rejected(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "proj.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert store.load("proj") is None


def test_unsafe_project_id_is_rejected(tmp_path):
    store = FileMemoryStore(tmp_path / "memory")
    for bad in ("../escape", "a/b", "", "with space"):
        with pytest.raises(ValueError):
            store.load(bad)
