from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlencode

import pytest

from codeloom_cloud.app import create_app
from codeloom_cloud.engine.client import EngineClient
from codeloom_cloud.engine.commands import ProtocolError, prepare_command
from codeloom_cloud.sandbox.fake import FakeEngine, FakeSandboxDriver

from tests.helpers import allow_repo, auth_header, login, wait_until_settled


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

    engine = driver.engines[ready["id"]]
    assert engine.received[0] == {"type": "StartSession", "workspace": "/workspace"}

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


def test_engine_failure_stops_the_sandbox(settings, github):
    driver = FakeSandboxDriver(fail_engine=True)
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

    from codeloom_cloud.db import SessionLocal
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
    db = SessionLocal()
    db.add_all([user, project, session])
    db.commit()
    session_id = session.id
    db.close()
    driver.running["fake-left"] = True

    await app.state.manager.reattach()

    db = SessionLocal()
    try:
        row = db.get(SessionRecord, session_id)
        assert row is not None
        assert row.status == "error"
        assert row.error == "controller restarted during provisioning"
    finally:
        db.close()
    assert "fake-left" in driver.stopped


def test_host_data_dir_is_the_docker_bind_source(tmp_path):
    from codeloom_cloud.config import Settings

    data = tmp_path / "data"
    host = tmp_path / "host"
    cfg = Settings(
        database_url="sqlite://",
        data_dir=data,
        host_data_dir=host,
        session_secret="test-secret",
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
