from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from codeloom_cloud.api.projects import owned_project
from codeloom_cloud.api.schemas import SessionArchiveIn, SessionOut, SessionTitleIn
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.github import GitHubError
from codeloom_cloud.auth.tokens import read_session_token
from codeloom_cloud.config import Settings
from codeloom_cloud.crypto import TokenError, decrypt_token
from codeloom_cloud.db import get_db, open_session
from codeloom_cloud.engine.commands import ProtocolError
from codeloom_cloud.models import Project, SessionRecord, User
from codeloom_cloud.sandbox.images import runtime_for_language

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


def clip_title(text: str, limit: int = 120) -> str:
    """First line of a prompt, cut on a word boundary."""
    stripped = text.strip()
    if not stripped:
        return ""
    line = " ".join(stripped.splitlines()[0].split())
    if len(line) <= limit:
        return line
    cut = line[: limit - 1]
    space = cut.rfind(" ")
    if space >= 40:
        cut = cut[:space]
    return cut.rstrip(".,;:—- ") + "…"


def remember_session_title(session_id: str, text: object) -> None:
    """Keep the first user prompt as the run title. Later messages stay put."""
    if not isinstance(text, str):
        return
    title = clip_title(text)
    if not title:
        return
    try:
        db = open_session()
    except RuntimeError:
        return
    try:
        row = db.get(SessionRecord, session_id)
        if row is None or (row.title and row.title.strip()):
            return
        row.title = title
        db.commit()
    except Exception:
        logger.exception("could not store session title for %s", session_id)
        db.rollback()
    finally:
        db.close()


def session_out(row: SessionRecord, project: Project) -> SessionOut:
    return SessionOut(
        id=row.id,
        project_id=row.project_id,
        status=row.status,
        repo=f"{project.owner}/{project.repo}",
        branch=project.default_branch,
        engine_session_id=row.engine_session_id,
        error=row.error,
        title=row.title,
        created_at=row.created_at,
        stopped_at=row.stopped_at,
    )


async def refresh_project_runtime(request: Request, user: User, project: Project, db: Session) -> None:
    """Pick the sandbox image from the repo's current GitHub language.

    Projects created before the three images default to python. A failed
    lookup keeps the stored runtime so session start still proceeds.
    """
    try:
        token = decrypt_token(user.access_token_encrypted, request.app.state.settings)
        remote = await request.app.state.github.get_repo(
            token, f"{project.owner}/{project.repo}"
        )
    except (TokenError, GitHubError):
        logger.info("could not refresh runtime for %s/%s", project.owner, project.repo)
        return
    if remote is None:
        return
    runtime = runtime_for_language(remote.language)
    if project.runtime == runtime:
        return
    project.runtime = runtime
    db.commit()


def owned_session(db: Session, user: User, session_id: str) -> tuple[SessionRecord, Project]:
    row = db.get(SessionRecord, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    project = db.get(Project, row.project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return row, project


@router.post("/projects/{project_id}/sessions", response_model=SessionOut, status_code=201)
async def create_session(
    project_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    project = owned_project(db, user, project_id)
    session_id = uuid4().hex
    workspace = (request.app.state.settings.data_dir / "sessions" / session_id).resolve()
    socket_path = workspace / ".engine" / "engine.sock"
    row = SessionRecord(
        id=session_id,
        project_id=project.id,
        user_id=user.id,
        status="provisioning",
        container_id=None,
        workspace_path=str(workspace),
        socket_path=str(socket_path),
        engine_session_id=None,
        error=None,
        created_at=datetime.now(timezone.utc),
        stopped_at=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    await refresh_project_runtime(request, user, project, db)
    request.app.state.manager.schedule(row.id)
    return session_out(row, project)


@router.get("/projects/{project_id}/sessions", response_model=list[SessionOut])
def list_sessions(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    project = owned_project(db, user, project_id)
    rows = (
        db.query(SessionRecord)
        .filter(SessionRecord.project_id == project.id, SessionRecord.user_id == user.id)
        .order_by(SessionRecord.created_at.desc())
        .all()
    )
    return [session_out(row, project) for row in rows]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    row, project = owned_session(db, user, session_id)
    return session_out(row, project)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
def set_session_title(
    session_id: str,
    body: SessionTitleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    row, project = owned_session(db, user, session_id)
    title = clip_title(body.title)
    if not title:
        raise HTTPException(status_code=422, detail="title is empty")
    if not (row.title and row.title.strip()):
        row.title = title
        db.commit()
        db.refresh(row)
    return session_out(row, project)


ARCHIVE_LIMIT = 1_500_000


def read_archive(raw: str | None) -> dict:
    if not raw:
        return {"stats": None, "items": []}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"stats": None, "items": []}
    if not isinstance(parsed, dict):
        return {"stats": None, "items": []}
    stats = parsed.get("stats")
    items = parsed.get("items")
    return {
        "stats": stats if isinstance(stats, dict) else None,
        "items": items if isinstance(items, list) else [],
    }


@router.get("/sessions/{session_id}/archive")
def get_archive(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row, _project = owned_session(db, user, session_id)
    return read_archive(row.archive)


@router.put("/sessions/{session_id}/archive")
def put_archive(
    session_id: str,
    body: SessionArchiveIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row, _project = owned_session(db, user, session_id)
    raw = body.model_dump_json()
    if len(raw) > ARCHIVE_LIMIT:
        raise HTTPException(status_code=413, detail="archive is too large")
    row.archive = raw
    db.commit()
    return {"stats": body.stats, "items": body.items}


@router.delete("/sessions/{session_id}", response_model=SessionOut)
async def stop_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    row, project = owned_session(db, user, session_id)
    try:
        updated = await request.app.state.manager.stop(row.id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return session_out(updated, project)


def remove_session_workspace(settings: Settings, session_id: str) -> None:
    root = (settings.data_dir / "sessions").resolve()
    target = (root / session_id).resolve()
    if target.parent != root or not target.is_dir():
        return
    shutil.rmtree(target)


@router.delete("/sessions/{session_id}/record", status_code=204)
async def delete_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row, _project = owned_session(db, user, session_id)
    session_id = row.id
    live = row.status in ("provisioning", "ready")
    db.commit()
    if live:
        try:
            await request.app.state.manager.stop(session_id, user.id)
        except KeyError:
            pass
    current = db.get(SessionRecord, session_id)
    if current is not None and current.user_id == user.id:
        db.delete(current)
        db.commit()
    remove_session_workspace(request.app.state.settings, session_id)
    return Response(status_code=204)


@router.websocket("/sessions/{session_id}/stream")
async def stream_session(websocket: WebSocket, session_id: str, token: str = "") -> None:
    settings = websocket.app.state.settings
    manager = websocket.app.state.manager
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user_id = read_session_token(settings, token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    try:
        db = open_session()
    except RuntimeError:
        await websocket.close(code=1011)
        return
    try:
        user = db.get(User, user_id)
        row = db.get(SessionRecord, session_id)
        if user is None or row is None or row.user_id != user.id:
            await websocket.close(code=4404)
            return
        if row.status != "ready":
            await websocket.close(code=4409)
            return
    finally:
        db.close()

    bridge = manager.bridge_for(session_id)
    if bridge is None:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    queue = bridge.subscribe()
    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    try:
        try:
            await bridge.send({"type": "RequestSnapshot", "replay": True})
        except ProtocolError as exc:
            await emit({"type": "ErrorOccurred", "message": str(exc)})

        async def pump() -> None:
            while True:
                event = await queue.get()
                await emit(event)

        async def read_commands() -> None:
            while True:
                raw = await websocket.receive_text()
                try:
                    command = json.loads(raw)
                except json.JSONDecodeError:
                    await emit({"type": "ErrorOccurred", "message": "malformed JSON"})
                    continue
                if isinstance(command, dict) and command.get("type") == "SubmitUserMessage":
                    remember_session_title(session_id, command.get("text"))
                try:
                    await bridge.send(command)
                except ProtocolError as exc:
                    await emit({"type": "ErrorOccurred", "message": str(exc)})

        pump_task = asyncio.create_task(pump())
        read_task = asyncio.create_task(read_commands())
        done, pending = await asyncio.wait(
            {pump_task, read_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception:
                logger.exception("session stream failed for %s", session_id)
    finally:
        bridge.unsubscribe(queue)
