#!/bin/sh
set -eu

umask 000

# gh already reads GITHUB_TOKEN. Do not run `gh auth login` — with the token
# already in the environment that command exits after a warning and kills
# the container before the engine starts.
if [ -n "${GITHUB_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
fi

python /usr/local/bin/tcp_proxy.py &
proxy_pid=$!

if [ ! -d /workspace/.git ]; then
  if [ -z "${GIT_URL:-}" ]; then
    echo "GIT_URL is required" >&2
    exit 1
  fi
  mkdir -p /tmp/src
  if [ -n "${GIT_BRANCH:-}" ]; then
    git clone --branch "$GIT_BRANCH" "$GIT_URL" /tmp/src
  else
    git clone "$GIT_URL" /tmp/src
  fi
  # /workspace may already have a tmpfs .engine; copy the clone beside it.
  cp -a /tmp/src/. /workspace/
  rm -rf /tmp/src
fi

python /opt/codeloom.engine/app.py /workspace &
pid=$!

shutdown() {
  kill -TERM "$proxy_pid" 2>/dev/null || true
  kill -TERM "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}
trap shutdown TERM INT

i=0
while [ "$i" -lt 120 ]; do
  if [ -S /workspace/.engine/engine.sock ]; then
    chmod 666 /workspace/.engine/engine.sock || true
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"
    status=$?
    kill -TERM "$proxy_pid" 2>/dev/null || true
    exit "$status"
  fi
  i=$((i + 1))
  sleep 0.5
done

set +e
wait "$pid"
status=$?
set -e
kill -TERM "$proxy_pid" 2>/dev/null || true
wait "$proxy_pid" 2>/dev/null || true
exit "$status"
