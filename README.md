# CodeLoom cloud controller

HTTP control plane for [codeloom.engine](https://github.com/iresharma/codeloom.engine). It authenticates users with GitHub, stores the repositories they want to work on, and starts one Docker sandbox per chat. The sandbox clones that repo and boots the engine. The web client then speaks the engine's NDJSON protocol through a WebSocket. Subagents stay inside that one engine process; the controller forwards their events and the commands that select them.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Create a GitHub OAuth app. The callback URL must match `GITHUB_OAUTH_CALLBACK_URL`. The login flow requests `read:user` and `repo`, so the controller can list repositories, clone private ones, and let the engine's `gh` tools open pull requests as that user. Generate a Fernet key for `TOKEN_ENCRYPTION_KEY` if you do not want the GitHub token encrypted with a key derived from `SESSION_SECRET`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Build the sandbox image (this clones the engine at `ENGINE_REF`):

```bash
docker build -t codeloom-sandbox:main --build-arg ENGINE_REF=main sandbox
```

`SANDBOX_IMAGE` must match that tag. `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` are injected into each sandbox and are not written into the clone. The engine uses the OpenRouter key for chat and the TypeSafe key for the judge. A session can still reach `ready` when either key is empty; chat or judging then fails inside the engine.

```bash
uvicorn codeloom_cloud.main:app --host 0.0.0.0 --port 8000
```

If the controller itself runs in a container, set `HOST_DATA_DIR` to the host path of `DATA_DIR`. Docker bind-mounts the session workspace from that host path, because the daemon does not see the controller container's filesystem.

## HTTP

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/auth/github/login` | Redirect to GitHub |
| `GET` | `/auth/github/callback` | Store the user and redirect to `FRONTEND_ORIGIN/auth/callback#token=...` |
| `GET` | `/me` | Current user |
| `GET` | `/github/repos` | Repositories the user can pick |
| `GET` `POST` | `/projects` | List or register `owner/repo` |
| `GET` `DELETE` | `/projects/{id}` | Fetch or delete. Delete stops live sandboxes first |
| `POST` | `/projects/{id}/sessions` | Start a sandbox. Returns `provisioning` immediately |
| `GET` | `/projects/{id}/sessions` | Sessions for that project |
| `GET` | `/sessions/{id}` | Status, repo, branch, timestamps, last error |
| `DELETE` | `/sessions/{id}` | `Shutdown`, stop the container, keep the workspace directory |

Later calls send `Authorization: Bearer`. A session is ready once `status` is `ready`. `error` means the sandbox or the engine failed and the container was stopped.

## Session socket

`WS /sessions/{id}/stream?token=...` is the engine protocol. The controller accepts every command in the engine registry (`SubmitUserMessage`, `OpenFile`, `CreatePath`, `RequestGit`, `RequestMemory`, `RequestContext`, `RequestAgentTranscript`, `AbortAgent`, `AnswerPrompt`, and the rest) and writes the JSON object through unchanged. Unknown `type` values come back as `ErrorOccurred` and are not sent. The only rewrite is `StartSession.workspace`, which is forced to `/workspace` inside the sandbox.

On connect the controller sends `RequestSnapshot` with `replay=true`, then forwards every engine event. That snapshot includes the live `agents` array.

A subagent is not a second session. Selecting one in the UI is the same pair of commands the TUI sends:

- `RequestAgentTranscript` with that `agent_id` returns `ChatHistoryAdded` lines and then `ChatHistoryComplete`
- `RequestContext` with that `agent_id` (or `""` for the orchestrator) returns the context breakdown
- `AbortAgent` with that `agent_id` cancels the child. With no id, it cancels only the orchestrator's current reply

`AgentsUpdated`, `AgentStarted`, `AgentFinished`, `AgentStateChanged`, and the chat and tool events carry `agent_id`. An empty `agent_id` is the orchestrator. The client owns the graph and which agent is selected.

## Tests

```bash
pytest
```

The suite uses an in-process engine socket and a fake Docker driver. A real engine and Docker daemon are not required.
