ARG PYTHON_IMAGE="python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca"
ARG NODE_IMAGE="node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e"
ARG UV_IMAGE="ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1"

# Modern, efficient Dockerfile with proper user permissions
# Multi-stage build for smaller final image and better security

#######################################################################
## Stage 1: Build dependencies with UV
#######################################################################
FROM ${UV_IMAGE} AS uv-runtime
FROM ${PYTHON_IMAGE} AS builder

# Install system dependencies needed for building Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install UV - fast Python package manager
COPY --from=uv-runtime /uv /usr/local/bin/uv

# Set up UV environment - install directly to system Python (safer for Docker)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/usr/local
ENV UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app
# Copy dependency files first for better layer caching
COPY ./pyproject.toml /app/
COPY ./uv.lock /app/

# Install dependencies directly to system Python (no venv)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

#######################################################################
## Stage 2: Build Tailwind CSS with Node.js
#######################################################################
FROM ${NODE_IMAGE} AS css-builder

WORKDIR /app

# Copy Tailwind configuration and all source files (HTML/JS/CSS)
COPY package.json package-lock.json tailwind.config.js ./
COPY static ./static

# Install dependencies and build CSS
RUN npm ci && \
    npm run build:css

#######################################################################
## Stage 3: Runtime image - minimal and secure
#######################################################################
FROM ${PYTHON_IMAGE} AS runtime

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    tzdata \
    curl \
    openssh-client \
    ripgrep \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xvfb \
    fonts-noto-color-emoji \
    fonts-unifont \
    libfontconfig1 \
    libfreetype6 \
    xfonts-scalable \
    fonts-liberation \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    fonts-tlwg-loma-otf \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create application user with configurable UID/GID for host compatibility
# These build args can be overridden from docker-compose.yml
ARG USER_ID=1000
ARG GROUP_ID=1000

RUN set -eux; \
    case "${USER_ID}" in ''|*[!0-9]*) \
        echo "USER_ID must be a decimal integer." >&2; exit 1;; esac; \
    case "${GROUP_ID}" in ''|*[!0-9]*) \
        echo "GROUP_ID must be a decimal integer." >&2; exit 1;; esac; \
    if [ "${USER_ID}" = "0" ] || [ "${GROUP_ID}" = "0" ]; then \
        echo "USER_ID and GROUP_ID must be non-root identities." >&2; exit 1; \
    fi; \
    group_name="$(getent group "${GROUP_ID}" | cut -d: -f1 || true)"; \
    if [ -z "${group_name}" ]; then \
        group_name=appuser; \
        groupadd -g "${GROUP_ID}" "${group_name}"; \
    fi; \
    useradd -u "${USER_ID}" -g "${group_name}" -m -d /home/appuser -s /bin/bash appuser

# Set up application directory
WORKDIR /app

# Store Playwright browser binaries outside any specific user's home directory.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy system Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Install Chromium without the legacy headless shell; the app launches channel="chromium".
RUN python -m playwright install --no-shell chromium && \
    chmod -R a+rX /ms-playwright

# Drop build-only tooling and caches to keep runtime image lean
RUN rm -f /usr/local/bin/uv && rm -rf /root/.cache

# Copy application code and set ownership
COPY --chown=${USER_ID}:${GROUP_ID} . .

# Copy compiled CSS from css-builder stage
COPY --from=css-builder --chown=${USER_ID}:${GROUP_ID} /app/static/output.css /app/static/output.css

# Create data and deployment-owned advanced-shell volume mount points.
RUN mkdir -p \
        /app/data \
        /run/advanced-shell/client-identity \
        /run/advanced-shell/client-public \
        /run/advanced-shell/host-public \
    && chown -R ${USER_ID}:${GROUP_ID} /app/data /run/advanced-shell
RUN chmod 1777 \
    /run/advanced-shell/client-public \
    /run/advanced-shell/host-public

COPY --chown=${USER_ID}:${GROUP_ID} docker/bootstrap-advanced-shell-client.sh /usr/local/bin/bootstrap-advanced-shell-client
RUN chmod 0755 /usr/local/bin/bootstrap-advanced-shell-client

# Avoid writing .pyc files and ensure instant logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Expose configurable port (documentation only, actual port set by environment)
ARG API_PORT=8000
EXPOSE ${API_PORT}

# Use system Python with uvicorn module
USER appuser
CMD ["sh", "-c", "exec /usr/local/bin/bootstrap-advanced-shell-client python -m uvicorn main:app --host 0.0.0.0 --port ${API_PORT:-8000}"]

#######################################################################
## Metadata for better container management
#######################################################################
LABEL security.non-root-user="appuser"
LABEL security.configurable-uid-gid="true"
