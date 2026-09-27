from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from codeloom_cloud.api.projects import owned_project
from codeloom_cloud.api.schemas import SessionOut
from codeloom_cloud.auth.deps import get_current_user
from codeloom_cloud.auth.tokens import read_session_token
from codeloom_cloud.db import SessionLocal, get_db
from codeloom_cloud.engine.commands import ProtocolError
from codeloom_cloud.models import Project, SessionRecord, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


def session_out(row: SessionRecord, project: Project) -> SessionOut:
    return SessionOut(
        id=row.id,
        project_id=row.project_id,
        status=row.status,
        repo=f"{project.owner}/{project.repo}",
        branch=project.default_branch,
        engine_session_id=row.engine_session_id,
        error=row.error,
        created_at=row.created_at,
        stopped_at=row.stopped_at,
    )


def owned_session(db: Session, user: User, session_id: str) -> tuple[SessionRecord, Project]:
    row = db.get(SessionRecord, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    project = db.get(Project, row.project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return row, project


@router.post("/projects/{project_id}/sessions", response_model=SessionOut, status_code=201)
def create_session(
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
    if SessionLocal is None:
        await websocket.close(code=1011)
        return
    db = SessionLocal()
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
