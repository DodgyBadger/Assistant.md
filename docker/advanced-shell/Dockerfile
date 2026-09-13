ARG PYTHON_IMAGE="python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca"
ARG NODE_IMAGE="node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e"
ARG UV_IMAGE="ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1"

FROM ${NODE_IMAGE} AS node-runtime
FROM ${UV_IMAGE} AS uv-runtime
FROM ${PYTHON_IMAGE}

COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=uv-runtime /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        findutils \
        gzip \
        git \
        jq \
        openssh-server \
        procps \
        ripgrep \
        tar \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG SHELL_USER_ID=1000
ARG SHELL_GROUP_ID=1000

RUN set -eux; \
    case "${SHELL_USER_ID}" in ''|*[!0-9]*) \
        echo "SHELL_USER_ID must be a decimal integer." >&2; exit 1;; esac; \
    case "${SHELL_GROUP_ID}" in ''|*[!0-9]*) \
        echo "SHELL_GROUP_ID must be a decimal integer." >&2; exit 1;; esac; \
    if [ "${SHELL_USER_ID}" = "0" ] || [ "${SHELL_GROUP_ID}" = "0" ]; then \
        echo "SHELL_USER_ID and SHELL_GROUP_ID must be non-root identities." >&2; \
        exit 1; \
    fi; \
    groupadd --gid "${SHELL_GROUP_ID}" advanced-shell \
    && useradd \
        --uid "${SHELL_USER_ID}" \
        --gid advanced-shell \
        --create-home \
        --home-dir /home/advanced-shell \
        --shell /bin/bash \
        advanced-shell \
    && passwd --delete advanced-shell \
    && mkdir -p \
        /run/sshd \
        /run/advanced-shell/host-identity \
        /run/advanced-shell/host-public \
        /run/advanced-shell/client-public \
        /workspace \
    && chown advanced-shell:advanced-shell /workspace \
    && chmod 1777 \
        /run/advanced-shell/client-public \
        /run/advanced-shell/host-public

COPY docker/advanced-shell/sshd_config /etc/ssh/sshd_config
COPY docker/advanced-shell/start-sshd.sh /usr/local/bin/start-advanced-shell
COPY docker/advanced-shell/forced_command.py /usr/local/bin/advanced-shell-entry

RUN chmod 0755 \
        /usr/local/bin/start-advanced-shell \
        /usr/local/bin/advanced-shell-entry

EXPOSE 2222

ENTRYPOINT ["/usr/local/bin/start-advanced-shell"]
