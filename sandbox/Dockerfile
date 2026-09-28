# syntax=docker/dockerfile:1
#
# Three sandbox images, one harness. Build each target:
#
#   docker build --target python -t codeloom-sandbox:python ...
#   docker build --target node   -t codeloom-sandbox:node ...
#   docker build --target golang -t codeloom-sandbox:golang ...
#
# The harness is Python in every image. Each language image ships several
# releases. After clone, select_toolchain.py points PATH at the release the
# repo pins, or the closest installed release. Language servers are already
# on PATH, so the first diagnostics call does not download them.

FROM python:3.11-slim-bookworm AS harness

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GIT_TERMINAL_PROMPT=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        ca-certificates \
        curl \
        xz-utils \
        build-essential \
    && ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) GH_ARCH=amd64 ;; \
         arm64) GH_ARCH=arm64 ;; \
         *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/gh.tgz \
         "https://github.com/cli/cli/releases/download/v2.62.0/gh_2.62.0_linux_${GH_ARCH}.tar.gz" \
    && tar -xzf /tmp/gh.tgz -C /tmp \
    && mv "/tmp/gh_2.62.0_linux_${GH_ARCH}/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh.tgz "/tmp/gh_2.62.0_linux_${GH_ARCH}" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=engine app.py requirements.txt pyproject.toml /opt/codeloom.engine/
COPY --from=engine agents /opt/codeloom.engine/agents
COPY --from=engine clients /opt/codeloom.engine/clients
COPY --from=engine llm /opt/codeloom.engine/llm
COPY --from=engine protocol /opt/codeloom.engine/protocol
COPY --from=engine runtime /opt/codeloom.engine/runtime
COPY --from=engine tools /opt/codeloom.engine/tools
RUN pip install --no-cache-dir -r /opt/codeloom.engine/requirements.txt \
 && python -m playwright install --with-deps chromium

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY select_toolchain.py /usr/local/bin/select_toolchain.py
COPY tcp_proxy.py /usr/local/bin/tcp_proxy.py
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]


# Node 24 hosts pyright. The node image adds the other lines beside it.
FROM harness AS with-node

ARG NODE_24=24.21.0
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    PATH="/opt/toolchain/node/bin:${PATH}"

RUN ARCH="$(dpkg --print-architecture)" \
 && case "$ARCH" in \
      amd64) NODE_ARCH=x64 ;; \
      arm64) NODE_ARCH=arm64 ;; \
      *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac \
 && mkdir -p /opt/node /opt/toolchain \
 && curl -fsSL "https://nodejs.org/dist/v${NODE_24}/node-v${NODE_24}-linux-${NODE_ARCH}.tar.xz" \
    | tar -xJ -C /opt/node \
 && mv "/opt/node/node-v${NODE_24}-linux-${NODE_ARCH}" "/opt/node/${NODE_24}" \
 && ln -sfn "/opt/node/${NODE_24}" /opt/toolchain/node \
 && node --version \
 && npm --version


FROM with-node AS python

# Project Pythons live under /opt/python. /usr/local/bin/python stays the
# harness interpreter. pyright-langserver is what the harness execs.
ARG PYTHON_STANDALONE=20260924
ARG PYTHON_VERSIONS="3.11.16 3.12.14 3.13.15 3.14.7"
ARG PYRIGHT_VERSION=1.1.407
ENV PATH="/opt/toolchain/python/bin:/opt/lsp/bin:${PATH}"

RUN ARCH="$(dpkg --print-architecture)" \
 && case "$ARCH" in \
      amd64) PY_ARCH=x86_64 ;; \
      arm64) PY_ARCH=aarch64 ;; \
      *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac \
 && mkdir -p /opt/python /opt/lsp \
 && for ver in $PYTHON_VERSIONS; do \
      curl -fsSL -o /tmp/py.tgz \
        "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE}/cpython-${ver}%2B${PYTHON_STANDALONE}-${PY_ARCH}-unknown-linux-gnu-install_only.tar.gz" \
      && mkdir -p "/opt/python/${ver}" \
      && tar -xzf /tmp/py.tgz -C "/opt/python/${ver}" --strip-components=1 \
      && "/opt/python/${ver}/bin/python" -m pip install --no-cache-dir pytest \
      && rm -f /tmp/py.tgz; \
    done \
 && ln -sfn /opt/python/3.14.7 /opt/toolchain/python \
 && npm install -g --prefix /opt/lsp "pyright@${PYRIGHT_VERSION}" \
 && command -v pyright-langserver \
 && pyright --version \
 && python3 --version \
 && pytest --version


FROM with-node AS node

ARG NODE_20=20.20.2
ARG NODE_22=22.23.3
ARG NODE_24=24.21.0
ARG YARN_VERSION=1.22.22
ARG PNPM_VERSION=12.6.0
ARG TYPESCRIPT_VERSION=5.9.3
ARG TS_LANGSERVER_VERSION=4.4.1
ENV PATH="/opt/lsp/bin:${PATH}"

RUN ARCH="$(dpkg --print-architecture)" \
 && case "$ARCH" in \
      amd64) NODE_ARCH=x64 ;; \
      arm64) NODE_ARCH=arm64 ;; \
      *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac \
 && for ver in "$NODE_20" "$NODE_22"; do \
      curl -fsSL "https://nodejs.org/dist/v${ver}/node-v${ver}-linux-${NODE_ARCH}.tar.xz" \
        | tar -xJ -C /opt/node \
      && mv "/opt/node/node-v${ver}-linux-${NODE_ARCH}" "/opt/node/${ver}"; \
    done \
 && for ver in "$NODE_20" "$NODE_22" "$NODE_24"; do \
      PATH="/opt/node/${ver}/bin:${PATH}" \
        "/opt/node/${ver}/bin/npm" install -g --prefix "/opt/node/${ver}" \
          "yarn@${YARN_VERSION}" "pnpm@${PNPM_VERSION}"; \
    done \
 && npm install -g --prefix /opt/lsp \
        "typescript@${TYPESCRIPT_VERSION}" \
        "typescript-language-server@${TS_LANGSERVER_VERSION}" \
 && command -v yarn \
 && command -v pnpm \
 && command -v typescript-language-server \
 && yarn --version \
 && pnpm --version \
 && node --version \
 && typescript-language-server --version


FROM harness AS golang

ARG GO_VERSIONS="1.24.13 1.25.14 1.26.8 1.27.1"
ARG GOPLS_VERSION=v0.23.0

ENV PATH="/opt/toolchain/go/bin:${PATH}" \
    GOTOOLCHAIN=local \
    GOPROXY=https://proxy.golang.org,direct \
    GO111MODULE=on

RUN ARCH="$(dpkg --print-architecture)" \
 && case "$ARCH" in \
      amd64) GO_ARCH=amd64 ;; \
      arm64) GO_ARCH=arm64 ;; \
      *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;; \
    esac \
 && mkdir -p /opt/go /opt/toolchain \
 && for ver in $GO_VERSIONS; do \
      curl -fsSL "https://dl.google.com/go/go${ver}.linux-${GO_ARCH}.tar.gz" \
        | tar -xz -C /tmp \
      && mv /tmp/go "/opt/go/${ver}"; \
    done \
 && ln -sfn /opt/go/1.27.1 /opt/toolchain/go \
 && GOBIN=/usr/local/bin /opt/go/1.27.1/bin/go install "golang.org/x/tools/gopls@${GOPLS_VERSION}" \
 && go version \
 && gopls version \
 && rm -rf /root/go /root/.cache/go-build
