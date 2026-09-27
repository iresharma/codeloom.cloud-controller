#!/bin/sh
set -eu

umask 000

if [ -n "${GITHUB_TOKEN:-}" ]; then
  printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
  gh auth setup-git
fi

if [ ! -d /workspace/.git ]; then
  if [ -z "${GIT_URL:-}" ]; then
    echo "GIT_URL is required" >&2
    exit 1
  fi
  if [ -n "${GIT_BRANCH:-}" ]; then
    git clone --branch "$GIT_BRANCH" "$GIT_URL" /workspace
  else
    git clone "$GIT_URL" /workspace
  fi
fi

python /opt/codeloom.engine/app.py /workspace &
pid=$!

shutdown() {
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
    exit $?
  fi
  i=$((i + 1))
  sleep 0.5
done

wait "$pid"
