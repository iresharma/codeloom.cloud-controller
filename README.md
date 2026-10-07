# CodeLoom cloud controller

HTTP control plane for [codeloom.engine](https://github.com/iresharma/codeloom.engine). It authenticates users with GitHub, stores the repositories they want to work on, and starts one Docker sandbox per chat. The sandbox clones that repo and boots the engine. The web client then speaks the engine's NDJSON protocol through a WebSocket. Subagents stay inside that one engine process; the controller forwards their events and the commands that select them.

## Run

The quickest path, from the umbrella `codeloom` checkout root, starts this and the web
client together and prints the web client's URL once both are up:

```bash
./scripts/run
```

It builds any missing sandbox image itself, on first run — set `SANDBOX_BUILD_ON_STARTUP=false`
to manage images yourself instead (see below). To run the controller on its own:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Create a GitHub OAuth app. The callback URL must match `GITHUB_OAUTH_CALLBACK_URL`. The login flow requests `read:user` and `repo`, so the controller can list repositories, clone private ones, and let the engine's `gh` tools open pull requests as that user. If the OAuth app expires user tokens, GitHub also returns a refresh token; the controller stores it and mints a new access token before a sandbox starts. A grant that cannot be refreshed fails the session with a sign-in error instead of a push that dies halfway through a run. Generate a Fernet key for `TOKEN_ENCRYPTION_KEY` if you do not want the GitHub token encrypted with a key derived from `SESSION_SECRET`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Build the three sandbox images from the local engine checkout (`workspace/engine`). The harness only runs Python, Go, and JavaScript/TypeScript, so each image already has that language's toolchain and language server. Playwright and its Chromium build are installed in that same harness (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`), so the browser tools do not download a browser when a session starts. A session uses the image that matches the repository's GitHub language (`Python`, `Go`, `JavaScript`, `TypeScript`). Any other language still starts, on the python image, and the engine reports that diagnostics are unavailable.

After the clone, the image switches to the release the repo pins, using a copy that is already installed. Node reads `.nvmrc`, `.node-version`, or `package.json` `engines.node` and selects Node 20.20.2, 22.23.3, or 24.21.0. Python reads `.python-version` or `requires-python` and selects 3.11.16, 3.12.14, 3.13.15, or 3.14.7. Go reads `go.mod` and selects 1.24.13, 1.25.14, 1.26.8, or 1.27.1. A pin we do not have uses the closest installed line, so a Node 20 repo does not run on Node 24. The Node image also has Yarn 1.22.22 and pnpm 12.6.0 on each of those Node lines.

```bash
for runtime in python node golang; do
  docker build --target "$runtime" -t "codeloom-sandbox:$runtime" \
    -f sandbox/Dockerfile \
    --build-context engine=../engine \
    sandbox
done
```

The controller runs this same build, per missing image, on its own startup (`sandbox.build.ensure_sandbox_images`)
— an image that already exists is left alone, so a repeat startup is a fast no-op. A build failure, a missing
engine checkout, or no Docker daemon is only logged; the controller still starts and sessions fail with their own
clear error until the image exists. Build manually (as above) to pre-warm images, rebuild after an engine change,
or on a host without Docker on `PATH`; set `SANDBOX_BUILD_ON_STARTUP=false` to disable the automatic build
entirely. `SANDBOX_DIR` and `ENGINE_PATH` override where it looks for `sandbox/Dockerfile` and the engine checkout
— unset, both are found relative to a `workspace/cloud-controller` next to `workspace/engine`.

`SANDBOX_IMAGE` is the repository. The controller replaces the tag with `python`, `node`, or `golang`, so `codeloom-sandbox:python` starts a Go repo as `codeloom-sandbox:golang`. `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` are injected into each sandbox and are not written into the clone. The engine uses the OpenRouter key for chat and the TypeSafe key for the judge. A session can still reach `ready` when either key is empty; chat or judging then fails inside the engine.

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
