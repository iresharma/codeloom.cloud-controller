from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from codeloom_cloud.config import Settings
from codeloom_cloud.crypto import TokenError, decrypt_token
from codeloom_cloud.db import open_session
from codeloom_cloud.engine.bridge import SessionBridge
from codeloom_cloud.engine.client import EngineClient
from codeloom_cloud.models import Project, SessionRecord, User
from codeloom_cloud.sandbox.driver import SandboxDriver

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, settings: Settings, driver: SandboxDriver) -> None:
        self.settings = settings
        self.driver = driver
        self._bridges: dict[str, SessionBridge] = {}
        self._tasks: set[asyncio.Task] = set()
        self._cancelled: set[str] = set()

    def schedule(self, session_id: str) -> asyncio.Task:
        task = asyncio.create_task(self.provision(session_id), name=f"provision-{session_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def bridge_for(self, session_id: str) -> SessionBridge | None:
        return self._bridges.get(session_id)

    async def provision(self, session_id: str) -> None:
        token = ""
        container_id: str | None = None
        try:
            if session_id in self._cancelled:
                return
            row = self._load(session_id)
            workspace = Path(row.workspace_path)
            workspace.mkdir(parents=True, exist_ok=True)
            project, user = self._project_and_user(row.project_id, row.user_id)
            try:
                token = decrypt_token(user.access_token_encrypted, self.settings)
            except TokenError as exc:
                raise RuntimeError(str(exc)) from exc
            env = {
                "GIT_URL": f"https://github.com/{project.owner}/{project.repo}.git",
                "GIT_BRANCH": project.default_branch,
            }
            if token:
                env["GITHUB_TOKEN"] = token
            if self.settings.openrouter_api_key:
                env["OPENROUTER_API_KEY"] = self.settings.openrouter_api_key
            handle = await self.driver.start(session_id, workspace, env)
            container_id = handle.container_id
            self._update(session_id, container_id=container_id, socket_path=str(handle.socket_path))
            if session_id in self._cancelled:
                await self.driver.stop(container_id)
                return
            client = EngineClient(self.settings.engine_workspace)
            await self._connect(client, handle.socket_path, self.settings.socket_wait_timeout)
            bridge = SessionBridge(client)
            bridge.start()
            self._bridges[session_id] = bridge
            await bridge.send(
                {"type": "StartSession", "workspace": self.settings.engine_workspace}
            )
            engine_session_id = await bridge.wait_ready(self.settings.engine_ready_timeout)
            if session_id in self._cancelled:
                await self._close_bridge(session_id)
                await self.driver.stop(container_id)
                return
            self._update(
                session_id,
                status="ready",
                engine_session_id=engine_session_id,
                error=None,
            )
        except asyncio.CancelledError:
            await self._close_bridge(session_id)
            raise
        except Exception as exc:
            logger.exception("provision failed for %s", session_id)
            await self._close_bridge(session_id)
            if container_id and session_id not in self._cancelled:
                try:
                    await self.driver.stop(container_id)
                except Exception:
                    logger.exception("stop after failure failed for %s", session_id)
            if session_id not in self._cancelled:
                message = str(exc) or exc.__class__.__name__
                if token and token in message:
                    message = message.replace(token, "[redacted]")
                self._update(session_id, status="error", error=message)

    async def stop(self, session_id: str, user_id: str) -> SessionRecord:
        row = self._load(session_id)
        if row.user_id != user_id:
            raise KeyError(session_id)
        self._cancelled.add(session_id)
        bridge = self._bridges.get(session_id)
        if bridge is not None:
            try:
                await bridge.send({"type": "Shutdown"})
            except Exception:
                logger.info("engine shutdown send failed for %s", session_id)
            await self._close_bridge(session_id)
        if row.container_id:
            try:
                await self.driver.stop(row.container_id)
            except Exception:
                logger.exception("container stop failed for %s", session_id)
        return self._update(
            session_id,
            status="stopped",
            stopped_at=datetime.now(timezone.utc),
        )

    async def reattach(self) -> None:
        with self._scope() as db:
            rows = (
                db.query(SessionRecord)
                .filter(SessionRecord.status.in_(("ready", "provisioning")))
                .all()
            )
            pending = [
                (row.id, row.status, row.container_id, row.socket_path or "")
                for row in rows
            ]
        for session_id, status, container_id, socket_path in pending:
            if session_id in self._bridges:
                continue
            if status == "provisioning":
                self._update(
                    session_id,
                    status="error",
                    error="controller restarted during provisioning",
                )
                if container_id:
                    await self._stop_container(container_id)
                continue
            path = Path(socket_path) if socket_path else None
            running = bool(container_id) and await self.driver.is_running(container_id)
            if not running or path is None or not path.exists():
                self._update(
                    session_id,
                    status="stopped",
                    stopped_at=datetime.now(timezone.utc),
                )
                if container_id and running:
                    await self._stop_container(container_id)
                continue
            try:
                await self._attach(session_id, path)
            except Exception as exc:
                logger.exception("reattach failed for %s", session_id)
                await self._close_bridge(session_id)
                self._update(session_id, status="error", error=str(exc))
                if container_id:
                    await self._stop_container(container_id)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for session_id in list(self._bridges):
            await self._close_bridge(session_id)

    async def _attach(self, session_id: str, socket_path: Path) -> None:
        client = EngineClient(self.settings.engine_workspace)
        await self._connect(client, socket_path, self.settings.socket_wait_timeout)
        bridge = SessionBridge(client)
        bridge.start()
        self._bridges[session_id] = bridge

    async def _connect(self, client: EngineClient, socket_path: Path, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        last: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if socket_path.exists():
                try:
                    await client.connect(str(socket_path))
                    return
                except OSError as exc:
                    last = exc
            await asyncio.sleep(0.05)
        raise TimeoutError(f"engine socket not ready: {socket_path}") from last

    async def _close_bridge(self, session_id: str) -> None:
        bridge = self._bridges.pop(session_id, None)
        if bridge is not None:
            await bridge.close()

    async def _stop_container(self, container_id: str) -> None:
        try:
            await self.driver.stop(container_id)
        except Exception:
            logger.exception("container stop failed for %s", container_id)

    def _project_and_user(self, project_id: str, user_id: str) -> tuple[Project, User]:
        with self._scope() as db:
            project = db.get(Project, project_id)
            user = db.get(User, user_id)
            if project is None or user is None:
                raise RuntimeError("session is missing its project or user")
            db.expunge(project)
            db.expunge(user)
            return project, user

    def _load(self, session_id: str) -> SessionRecord:
        with self._scope() as db:
            row = db.get(SessionRecord, session_id)
            if row is None:
                raise KeyError(session_id)
            db.expunge(row)
            return row

    def _update(self, session_id: str, **fields: object) -> SessionRecord:
        with self._scope() as db:
            row = db.get(SessionRecord, session_id)
            if row is None:
                raise KeyError(session_id)
            for key, value in fields.items():
                setattr(row, key, value)
            db.flush()
            db.expunge(row)
            return row

    @contextmanager
    def _scope(self):
        db = open_session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
