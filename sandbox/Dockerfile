# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        ca-certificates \
        curl \
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
RUN pip install --no-cache-dir -r /opt/codeloom.engine/requirements.txt

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY tcp_proxy.py /usr/local/bin/tcp_proxy.py
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
