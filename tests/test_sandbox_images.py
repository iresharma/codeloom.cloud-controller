from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from codeloom_cloud.db import init_db, open_session
from codeloom_cloud.models import Project
from codeloom_cloud.sandbox.images import image_for, runtime_for_language


def test_image_for_replaces_the_tag():
    assert image_for("codeloom-sandbox:main", "golang") == "codeloom-sandbox:golang"
    assert image_for("codeloom-sandbox:python", "node") == "codeloom-sandbox:node"
    assert (
        image_for("ghcr.io/org/codeloom-sandbox:python", "golang")
        == "ghcr.io/org/codeloom-sandbox:golang"
    )
    assert (
        image_for("localhost:5000/codeloom-sandbox", "python")
        == "localhost:5000/codeloom-sandbox:python"
    )
    assert image_for("codeloom-sandbox:python", "rust") == "codeloom-sandbox:python"


def test_runtime_for_language():
    assert runtime_for_language("Go") == "golang"
    assert runtime_for_language("TypeScript") == "node"
    assert runtime_for_language("JavaScript") == "node"
    assert runtime_for_language("Python") == "python"
    assert runtime_for_language(None) == "python"
    assert runtime_for_language("Rust") == "python"


def test_existing_projects_gain_a_runtime_column(tmp_path):
    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE projects ("
                "id VARCHAR(32) PRIMARY KEY, "
                "user_id VARCHAR(32), "
                "name VARCHAR(255), "
                "owner VARCHAR(255), "
                "repo VARCHAR(255), "
                "default_branch VARCHAR(255), "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO projects "
                "(id, user_id, name, owner, repo, default_branch, created_at) "
                "VALUES ('p1', 'u1', 'hello', 'octocat', 'hello', 'main', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE sessions ("
                "id VARCHAR(32) PRIMARY KEY, "
                "project_id VARCHAR(32), "
                "user_id VARCHAR(32), "
                "status VARCHAR(32), "
                "container_id VARCHAR(128), "
                "workspace_path TEXT, "
                "socket_path TEXT, "
                "engine_session_id VARCHAR(64), "
                "error TEXT, "
                "created_at DATETIME, "
                "stopped_at DATETIME)"
            )
        )
    engine.dispose()

    init_db(f"sqlite:///{db_path}")
    db = open_session()
    try:
        project = db.get(Project, "p1")
        assert project is not None
        assert project.runtime == "python"
        columns = {column["name"] for column in inspect(db.get_bind()).get_columns("sessions")}
        assert "title" in columns
        assert "archive" in columns
    finally:
        db.close()
