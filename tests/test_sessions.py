from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlencode

import pytest

from types import SimpleNamespace

from codeloom_cloud.app import create_app
from codeloom_cloud.config import Settings
from codeloom_cloud.engine.client import EngineClient
from codeloom_cloud.engine.commands import ProtocolError, prepare_command
from codeloom_cloud.memory_store import FileMemoryStore
from codeloom_cloud.sandbox.driver import SandboxHandle
from codeloom_cloud.sandbox.fake import FakeEngine, FakeSandboxDriver
from codeloom_cloud.sessions.manager import SessionManager

from tests.helpers import allow_repo, auth_header, login, wait_until_settled


def test_sandbox_env_omits_blank_model_keys():
    settings = Settings(
        session_secret="test-secret-test-secret-test-secret",
        openrouter_api_key="",
        typesafe_api_key="",
    )
    manager = SessionManager(settings, FakeSandboxDriver())
    project = SimpleNamespace(owner="octocat", repo="hello", default_branch="main")
    env = manager.sandbox_env(project, "gho_secret")
    assert env["GIT_URL"] == "https://github.com/octocat/hello.git"
    assert env["GIT_BRANCH"] == "main"
    assert env["GITHUB_TOKEN"] == "gho_secret"
    assert "OPENROUTER_API_KEY" not in env
    assert "TYPESAFE_API_KEY" not in env


def test_prepare_command_rewrites_workspace_and_rejects_unknown():
    prepared = prepare_command(
        {"type": "StartSession", "workspace": "/evil", "session_id": None},
        "/workspace",
    )
    assert prepared == {"type": "StartSession", "workspace": "/workspace"}
    with pytest.raises(ProtocolError, match="unknown command"):
        prepare_command({"type": "DropDatabase"}, "/workspace")


@pytest.fixture
async def fake_engine():
    root = Path(tempfile.mkdtemp(prefix="cle"))
    engine = FakeEngine(root / "engine.sock")
    await engine.start()
    yield engine
    await engine.close()
    shutil.rmtree(root, ignore_errors=True)


async def test_client_rewrites_start_session(fake_engine):
    client = EngineClient("/workspace")
    await client.connect(str(fake_engine.socket_path))
    await client.send({"type": "StartSession", "workspace": "/evil"})
    event = await _next_event(client)
    assert fake_engine.received == [{"type": "StartSession", "workspace": "/workspace"}]
    assert event["type"] == "SnapshotReady"
    assert event["snapshot"]["session_id"] == "engine-session"
    await client.close()


async def test_client_forwards_agent_transcript(fake_engine):
    client = EngineClient("/workspace")
    await client.connect(str(fake_engine.socket_path))
    reader = asyncio.create_task(_take(client, 2))
    await asyncio.sleep(0)
    await client.send({"type": "RequestAgentTranscript", "agent_id": "child-1"})
    events = await reader
    assert fake_engine.received[-1] == {
        "type": "RequestAgentTranscript",
        "agent_id": "child-1",
    }
    assert events[0]["type"] == "ChatHistoryAdded"
    assert events[0]["agent_id"] == "child-1"
    assert events[1] == {"type": "ChatHistoryComplete", "count": 1, "agent_id": "child-1"}
    await client.close()


async def test_unknown_command_is_not_written(fake_engine):
    client = EngineClient("/workspace")
    await client.connect(str(fake_engine.socket_path))
    with pytest.raises(ProtocolError, match="unknown command"):
        await client.send({"type": "DropDatabase"})
    await asyncio.sleep(0.05)
    assert fake_engine.received == []
    await client.close()


def test_revoked_github_token_rejects_session_create(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    github.users.pop(access)
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert created.status_code == 401
    assert created.json()["detail"] == {
        "code": "github_auth_expired",
        "message": "GitHub authorization expired. Sign in again.",
    }
    listed = client.get(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert listed.json() == []


def test_session_create_refreshes_an_expiring_grant(client, github, driver, settings):
    from codeloom_cloud.auth.github import GitHubToken
    from codeloom_cloud.crypto import decrypt_token
    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import User

    github.grants["gho_octocat"] = GitHubToken(
        access_token="gho_octocat",
        refresh_token="ghr_old",
        expires_in=28800,
    )
    token, access = login(client, github, access_token="gho_octocat")
    allow_repo(github, access)
    allow_repo(github, "gho_new")
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    github.users.pop(access)
    github.add_user("gho_new", github_id=1, login="octocat")
    github.refresh_grants["ghr_old"] = GitHubToken(
        access_token="gho_new",
        refresh_token="ghr_new",
        expires_in=28800,
    )
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert created.status_code == 201
    db = open_session()
    try:
        user = db.query(User).one()
        assert decrypt_token(user.access_token_encrypted, settings) == "gho_new"
        assert decrypt_token(user.refresh_token_encrypted, settings) == "ghr_new"
    finally:
        db.close()


@pytest.mark.parametrize(
    ("language", "runtime"),
    [
        ("Python", "python"),
        ("TypeScript", "node"),
        ("JavaScript", "node"),
        ("Go", "golang"),
        ("Rust", "python"),
        (None, "python"),
    ],
)
def test_session_image_follows_repo_language(client, github, driver, language, runtime):
    token, access = login(client, github)
    slug = (language or "unknown").lower()
    full_name = f"octocat/{slug}-app"
    allow_repo(github, access, full_name=full_name, language=language)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": full_name},
    ).json()
    assert project["runtime"] == runtime
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert created.status_code == 201
    ready = wait_until_settled(client, token, created.json()["id"])
    assert ready["status"] == "ready"
    assert driver.images[ready["id"]] == f"codeloom-sandbox:{runtime}"


def test_session_refreshes_a_stale_runtime(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/tools")
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/tools"},
    ).json()
    assert project["runtime"] == "python"
    stored = github.repos[access][0]
    github.repos[access][0] = stored.__class__(
        **{**stored.__dict__, "language": "Go"}
    )
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    ready = wait_until_settled(client, token, created.json()["id"])
    assert ready["status"] == "ready"
    assert driver.images[ready["id"]] == "codeloom-sandbox:golang"
    refreshed = client.get(f"/projects/{project['id']}", headers=auth_header(token)).json()
    assert refreshed["runtime"] == "golang"


def test_session_provisions_and_streams_subagent_events(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert created.status_code == 201
    assert created.json()["status"] == "provisioning"
    ready = wait_until_settled(client, token, created.json()["id"])
    assert ready["status"] == "ready"
    assert ready["repo"] == "octocat/hello"
    assert ready["branch"] == "main"
    assert ready["engine_session_id"] == "engine-session"
    assert ready["error"] is None
    assert ready["created_at"].endswith("Z")
    listed = client.get(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    ).json()
    assert listed[0]["created_at"].endswith("Z")
    assert listed[0]["stopped_at"] is None

    engine = driver.engines[ready["id"]]
    assert engine.received[0] == {"type": "StartSession", "workspace": "/workspace"}
    assert driver.envs[ready["id"]]["OPENROUTER_API_KEY"] == "sk-test"
    assert driver.envs[ready["id"]]["OPENROUTER_MODEL"] == "openai/gpt-5.6-luna"
    assert driver.envs[ready["id"]]["TYPESAFE_API_KEY"] == "ts-test"
    assert "ts-test" not in driver.envs[ready["id"]]["GIT_URL"]

    other, _other_access = login(
        client,
        github,
        login_name="hubot",
        github_id=2,
        access_token="gho_hubot",
    )
    hidden = client.get(f"/sessions/{ready['id']}", headers=auth_header(other))
    assert hidden.status_code == 404

    query = urlencode({"token": token})
    with client.websocket_connect(f"/sessions/{ready['id']}/stream?{query}") as ws:
        snapshot = _until(ws, "SnapshotReady")
        assert snapshot["snapshot"]["agents"] == []
        ws.send_json({"type": "RequestAgentTranscript", "agent_id": "child-1"})
        history = _until(ws, "ChatHistoryAdded")
        complete = _until(ws, "ChatHistoryComplete")
        assert history["agent_id"] == "child-1"
        assert complete["agent_id"] == "child-1"
        ws.send_json({"type": "NoSuchCommand"})
        error = _until(ws, "ErrorOccurred")
        assert "unknown command" in error["message"]

    assert not any(item["type"] == "NoSuchCommand" for item in engine.received)
    assert any(
        item == {"type": "RequestAgentTranscript", "agent_id": "child-1"}
        for item in engine.received
    )


async def test_client_speaks_ndjson_over_tcp():
    received: list[dict] = []

    async def handle(reader, writer) -> None:
        line = await reader.readline()
        received.append(json.loads(line))
        writer.write(
            (
                json.dumps(
                    {
                        "type": "SnapshotReady",
                        "snapshot": {"session_id": "tcp-session"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = EngineClient("/workspace")
    await client.connect_tcp("127.0.0.1", port)
    await client.send({"type": "StartSession", "workspace": "/evil"})
    event = await _next_event(client)
    assert received == [{"type": "StartSession", "workspace": "/workspace"}]
    assert event["type"] == "SnapshotReady"
    assert event["snapshot"]["session_id"] == "tcp-session"
    await client.close()
    server.close()
    await server.wait_closed()


async def test_start_engine_retries_after_tcp_eof(settings):
    hits = {"n": 0}

    async def handle(reader, writer) -> None:
        hits["n"] += 1
        if hits["n"] == 1:
            writer.close()
            await writer.wait_closed()
            return
        await reader.readline()
        writer.write(
            (
                json.dumps(
                    {
                        "type": "SnapshotReady",
                        "snapshot": {"session_id": "retried-session"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        await reader.read()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    manager = SessionManager(settings, FakeSandboxDriver())
    handle_info = SandboxHandle(
        container_id="c",
        socket_path=Path("/unused"),
        engine_host="127.0.0.1",
        engine_port=port,
    )
    try:
        session_id = await manager._start_engine("retry-session", handle_info)
        assert session_id == "retried-session"
        assert hits["n"] == 2
    finally:
        await manager._close_bridge("retry-session")
        server.close()
        await server.wait_closed()


async def test_dropped_engine_connection_emits_error():
    from codeloom_cloud.engine.bridge import SessionBridge

    async def handle(reader, writer) -> None:
        await reader.readline()
        writer.write(
            (
                json.dumps(
                    {
                        "type": "SnapshotReady",
                        "snapshot": {"session_id": "engine-session"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = EngineClient("/workspace")
    await client.connect_tcp("127.0.0.1", port)
    disconnected = asyncio.Event()
    bridge = SessionBridge(client, on_disconnect=disconnected.set)
    queue = bridge.subscribe()
    bridge.start()
    await bridge.send({"type": "StartSession", "workspace": "/workspace"})
    assert await bridge.wait_ready(2) == "engine-session"
    await asyncio.wait_for(disconnected.wait(), 2)
    found = None
    while not queue.empty():
        event = queue.get_nowait()
        if event.get("type") == "ErrorOccurred":
            found = event
    assert found is not None
    assert "engine connection closed" in found["message"]
    await bridge.close()
    server.close()
    await server.wait_closed()


def test_engine_failure_stops_the_sandbox(settings, github):
    driver = FakeSandboxDriver(fail_engine=True)
    driver.log_text = "git clone: repository not found"
    app = create_app(settings, driver=driver, github=github)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        token, access = login(client, github)
        allow_repo(github, access)
        project_id = client.post(
            "/projects",
            headers=auth_header(token),
            json={"full_name": "octocat/hello"},
        ).json()["id"]
        session_id = client.post(
            f"/projects/{project_id}/sessions",
            headers=auth_header(token),
        ).json()["id"]
        body = wait_until_settled(client, token, session_id)
    assert body["status"] == "error"
    assert "workspace mismatch" in body["error"]
    assert "repository not found" in body["error"]
    assert driver.stopped


def test_sandbox_start_failure_marks_the_session(settings, github):
    driver = FakeSandboxDriver(fail_start=True)
    app = create_app(settings, driver=driver, github=github)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        token, access = login(client, github)
        allow_repo(github, access)
        project_id = client.post(
            "/projects",
            headers=auth_header(token),
            json={"full_name": "octocat/hello"},
        ).json()["id"]
        session_id = client.post(
            f"/projects/{project_id}/sessions",
            headers=auth_header(token),
        ).json()["id"]
        body = wait_until_settled(client, token, session_id)
    assert body["status"] == "error"
    assert "sandbox failed" in body["error"]
    assert driver.stopped == []


def test_stop_sends_shutdown_and_delete_removes_the_project(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access)
    project_id = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()["id"]
    session_id = client.post(
        f"/projects/{project_id}/sessions",
        headers=auth_header(token),
    ).json()["id"]
    assert wait_until_settled(client, token, session_id)["status"] == "ready"

    stopped = client.delete(f"/sessions/{session_id}", headers=auth_header(token))
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["stopped_at"] is not None
    engine = driver.engines[session_id]
    assert any(item["type"] == "Shutdown" for item in engine.received)
    assert driver.stopped

    listed = client.get(
        f"/projects/{project_id}/sessions",
        headers=auth_header(token),
    )
    assert listed.json()[0]["status"] == "stopped"

    deleted = client.delete(f"/projects/{project_id}", headers=auth_header(token))
    assert deleted.status_code == 204
    assert client.get(f"/sessions/{session_id}", headers=auth_header(token)).status_code == 404
    assert client.get(f"/projects/{project_id}", headers=auth_header(token)).status_code == 404


def test_delete_removes_a_live_run(client, github, driver, settings):
    token, access = login(client, github)
    allow_repo(github, access)
    project_id = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()["id"]
    session_id = client.post(
        f"/projects/{project_id}/sessions",
        headers=auth_header(token),
    ).json()["id"]
    assert wait_until_settled(client, token, session_id)["status"] == "ready"
    workspace = settings.data_dir / "sessions" / session_id
    assert workspace.is_dir()

    other, _other_access = login(
        client,
        github,
        login_name="hubot",
        github_id=2,
        access_token="gho_hubot",
    )
    hidden = client.delete(f"/sessions/{session_id}/record", headers=auth_header(other))
    assert hidden.status_code == 404
    assert workspace.is_dir()

    removed = client.delete(f"/sessions/{session_id}/record", headers=auth_header(token))
    assert removed.status_code == 204
    assert client.get(f"/sessions/{session_id}", headers=auth_header(token)).status_code == 404
    listed = client.get(f"/projects/{project_id}/sessions", headers=auth_header(token))
    assert listed.json() == []
    assert not workspace.exists()
    assert driver.stopped


def test_reattach_keeps_a_running_sandbox(settings, github):
    from fastapi.testclient import TestClient

    driver = FakeSandboxDriver()
    app = create_app(settings, driver=driver, github=github)
    with TestClient(app) as client:
        token, access = login(client, github)
        allow_repo(github, access)
        project_id = client.post(
            "/projects",
            headers=auth_header(token),
            json={"full_name": "octocat/hello"},
        ).json()["id"]
        session_id = client.post(
            f"/projects/{project_id}/sessions",
            headers=auth_header(token),
        ).json()["id"]
        assert wait_until_settled(client, token, session_id)["status"] == "ready"

    app2 = create_app(settings, driver=driver, github=github)
    with TestClient(app2) as client:
        token, _access = login(client, github)
        body = client.get(f"/sessions/{session_id}", headers=auth_header(token)).json()
        assert body["status"] == "ready"
        assert app2.state.manager.bridge_for(session_id) is not None
    starts = [item for item in driver.engines[session_id].received if item["type"] == "StartSession"]
    assert starts == [{"type": "StartSession", "workspace": "/workspace"}]


def test_reattach_stops_when_the_container_is_gone(settings, github):
    from fastapi.testclient import TestClient

    driver = FakeSandboxDriver()
    app = create_app(settings, driver=driver, github=github)
    with TestClient(app) as client:
        token, access = login(client, github)
        allow_repo(github, access)
        project_id = client.post(
            "/projects",
            headers=auth_header(token),
            json={"full_name": "octocat/hello"},
        ).json()["id"]
        session_id = client.post(
            f"/projects/{project_id}/sessions",
            headers=auth_header(token),
        ).json()["id"]
        assert wait_until_settled(client, token, session_id)["status"] == "ready"
    container_id = next(iter(driver.running))
    driver.running[container_id] = False

    app2 = create_app(settings, driver=driver, github=github)
    with TestClient(app2) as client:
        token, _access = login(client, github)
        body = client.get(f"/sessions/{session_id}", headers=auth_header(token)).json()
    assert body["status"] == "stopped"


async def test_reattach_marks_an_interrupted_provision(settings, github):
    from datetime import datetime, timezone
    from uuid import uuid4

    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import Project, SessionRecord, User

    driver = FakeSandboxDriver()
    app = create_app(settings, driver=driver, github=github)
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid4().hex,
        github_id=9,
        login="octocat",
        name=None,
        avatar_url=None,
        access_token_encrypted="enc",
        created_at=now,
    )
    project = Project(
        id=uuid4().hex,
        user_id=user.id,
        name="hello",
        owner="octocat",
        repo="hello",
        default_branch="main",
        created_at=now,
    )
    session = SessionRecord(
        id=uuid4().hex,
        project_id=project.id,
        user_id=user.id,
        status="provisioning",
        container_id="fake-left",
        workspace_path="/tmp/unused",
        socket_path="/tmp/unused/engine.sock",
        engine_session_id=None,
        error=None,
        created_at=now,
        stopped_at=None,
    )
    db = open_session()
    db.add(user)
    db.flush()
    db.add(project)
    db.flush()
    db.add(session)
    db.commit()
    session_id = session.id
    db.close()
    driver.running["fake-left"] = True

    await app.state.manager.reattach()

    db = open_session()
    try:
        row = db.get(SessionRecord, session_id)
        assert row is not None
        assert row.status == "error"
        assert row.error == "controller restarted during provisioning"
    finally:
        db.close()
    assert "fake-left" in driver.stopped


def test_clip_title_uses_the_first_line():
    from codeloom_cloud.api.sessions import clip_title

    assert clip_title("  Make the sidebar readable\n\nand more") == "Make the sidebar readable"
    clipped = clip_title("word " * 40)
    assert clipped.endswith("…")
    assert len(clipped) <= 120
    assert clip_title("   ") == ""


def test_first_prompt_names_the_session(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    )
    assert created.json()["title"] is None
    ready = wait_until_settled(client, token, created.json()["id"])
    query = urlencode({"token": token})
    with client.websocket_connect(f"/sessions/{ready['id']}/stream?{query}") as ws:
        _until(ws, "SnapshotReady")
        ws.send_json(
            {
                "type": "SubmitUserMessage",
                "text": "Make the sidebar show what each run was about\nextra detail",
            }
        )
        echoed = _until(ws, "ChatMessageAdded")
        assert echoed["text"].startswith("Make the sidebar")
        ws.send_json({"type": "SubmitUserMessage", "text": "A later message should not rename it"})
        _until(ws, "ChatMessageAdded")

    named = client.get(f"/sessions/{ready['id']}", headers=auth_header(token)).json()
    assert named["title"] == "Make the sidebar show what each run was about"
    listed = client.get(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    ).json()
    assert listed[0]["title"] == named["title"]


def test_title_patch_keeps_the_first_prompt(client, github):
    token, access = login(client, github)
    allow_repo(github, access)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    ).json()
    first = client.patch(
        f"/sessions/{created['id']}",
        headers=auth_header(token),
        json={"title": "  Explain the repo  "},
    )
    assert first.status_code == 200
    assert first.json()["title"] == "Explain the repo"
    second = client.patch(
        f"/sessions/{created['id']}",
        headers=auth_header(token),
        json={"title": "Rename after the fact"},
    )
    assert second.json()["title"] == "Explain the repo"
    blank = client.patch(
        f"/sessions/{created['id']}",
        headers=auth_header(token),
        json={"title": "   "},
    )
    assert blank.status_code == 422


def test_session_archive_round_trip(client, github):
    token, access = login(client, github)
    allow_repo(github, access)
    project = client.post(
        "/projects",
        headers=auth_header(token),
        json={"full_name": "octocat/hello"},
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions",
        headers=auth_header(token),
    ).json()
    empty = client.get(f"/sessions/{created['id']}/archive", headers=auth_header(token))
    assert empty.status_code == 200
    assert empty.json() == {"stats": None, "items": []}
    payload = {
        "stats": {"total_tokens": 1200, "requests": 4, "cost": 0.02},
        "items": [{"kind": "message", "id": "m1", "role": "user", "text": "Explain the repo"}],
    }
    saved = client.put(
        f"/sessions/{created['id']}/archive",
        headers=auth_header(token),
        json=payload,
    )
    assert saved.status_code == 200
    assert saved.json()["stats"]["total_tokens"] == 1200
    again = client.get(f"/sessions/{created['id']}/archive", headers=auth_header(token))
    assert again.json()["items"][0]["text"] == "Explain the repo"
    other, _other_access = login(
        client,
        github,
        login_name="hubot",
        github_id=2,
        access_token="gho_hubot",
    )
    hidden = client.get(f"/sessions/{created['id']}/archive", headers=auth_header(other))
    assert hidden.status_code == 404


def test_host_data_dir_is_the_docker_bind_source(tmp_path):
    from codeloom_cloud.config import Settings

    data = tmp_path / "data"
    host = tmp_path / "host"
    cfg = Settings(
        database_url="sqlite://",
        data_dir=data,
        host_data_dir=host,
        session_secret="test-secret-test-secret-test-secret",
    )
    source = cfg.docker_bind_source(data / "sessions" / "abc")
    assert source == str((host / "sessions" / "abc").resolve())


async def _next_event(client: EngineClient) -> dict:
    async for event in client.events():
        return event
    raise AssertionError("engine closed")


async def _take(client: EngineClient, count: int) -> list[dict]:
    found = []
    async for event in client.events():
        found.append(event)
        if len(found) >= count:
            return found
    return found


def _until(ws, event_type: str) -> dict:
    for _ in range(10):
        event = ws.receive_json()
        if event["type"] == event_type:
            return event
    raise AssertionError(event_type)


def _memory_payload(text: str) -> dict:
    return {
        "files": {},
        "engineering": [{"text": text, "updated_at": ""}],
        "product": [],
        "cicd": [],
        "other": [],
    }


def _start_session_memory(engine: FakeEngine):
    starts = [c for c in engine.received if c.get("type") == "StartSession"]
    assert starts, "engine never received StartSession"
    return starts[0].get("memory", "__missing__")


def test_provision_seeds_stored_memory(client, github, driver, settings):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/seeded")
    project = client.post(
        "/projects", headers=auth_header(token), json={"full_name": "octocat/seeded"}
    ).json()
    payload = _memory_payload("remembered from an earlier run")
    FileMemoryStore(settings.data_dir / "memory").save(project["id"], payload)

    created = client.post(
        f"/projects/{project['id']}/sessions", headers=auth_header(token)
    )
    assert created.status_code == 201
    ready = wait_until_settled(client, token, created.json()["id"])
    assert ready["status"] == "ready"

    assert _start_session_memory(driver.engines[created.json()["id"]]) == payload


def test_provision_without_stored_memory_sends_no_memory_key(client, github, driver):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/blank")
    project = client.post(
        "/projects", headers=auth_header(token), json={"full_name": "octocat/blank"}
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions", headers=auth_header(token)
    )
    ready = wait_until_settled(client, token, created.json()["id"])
    assert ready["status"] == "ready"

    # No stored memory -> StartSession carries no memory key at all.
    assert _start_session_memory(driver.engines[created.json()["id"]]) == "__missing__"


def test_open_bridge_wires_memory_persistence(app, client, github, driver, settings):
    token, access = login(client, github)
    allow_repo(github, access, full_name="octocat/persist")
    project = client.post(
        "/projects", headers=auth_header(token), json={"full_name": "octocat/persist"}
    ).json()
    created = client.post(
        f"/projects/{project['id']}/sessions", headers=auth_header(token)
    )
    session_id = created.json()["id"]
    wait_until_settled(client, token, session_id)

    bridge = app.state.manager.bridge_for(session_id)
    assert bridge is not None
    # _open_bridge wired the engine's memory stream to this project's store.
    payload = _memory_payload("learned during the run")
    bridge._on_memory(payload)

    stored = FileMemoryStore(settings.data_dir / "memory").load(project["id"])
    assert stored == payload


async def test_bridge_intercepts_memory_exported_without_fanout():
    from codeloom_cloud.engine.bridge import SessionBridge

    class _Client:
        def __init__(self, events):
            self._events = events

        async def events(self):
            for event in self._events:
                yield event

        async def close(self):
            return None

    got: list[dict] = []
    events = [
        {"type": "SnapshotReady", "snapshot": {"session_id": "s"}},
        {"type": "MemoryExported", "memory": {"engineering": [{"text": "x"}]}},
    ]
    bridge = SessionBridge(_Client(events), on_memory=got.append)
    sub = bridge.subscribe()
    bridge.start()
    await asyncio.sleep(0.05)

    assert got == [{"engineering": [{"text": "x"}]}]
    seen = []
    while not sub.empty():
        seen.append(sub.get_nowait().get("type"))
    assert "SnapshotReady" in seen
    assert "MemoryExported" not in seen  # controller-internal, never fanned out
    await bridge.close()
