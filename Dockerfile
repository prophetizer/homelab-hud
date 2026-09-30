# SPDX-License-Identifier: Apache-2.0
# syntax=docker/dockerfile:1.7
#
# Three stages: build the SPA, resolve Python deps with uv, copy both into a slim runtime.
# Runtime is non-root (uid 1000), expects a read-only rootfs, and writes only to /config,
# /data and /tmp (tmpfs) — see docker-compose.yml.

# ---------------------------------------------------------------- 1. frontend
FROM node:22-alpine@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------- 2. python deps
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS deps
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Deps only first, so source edits do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY hud/ ./hud/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------- 3. runtime
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS runtime
LABEL org.opencontainers.image.title="HUD" \
      org.opencontainers.image.description="Homelab Ultimate Dashboard" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HUD_CONFIG_DIR=/config \
    HUD_DATA_DIR=/data \
    HUD_STATIC_DIR=/app/web/dist \
    HUD_PORT=8080
# fontconfig's image cache never validates (layers keep whole-second times, the cache
# nanoseconds), so it rebuilds on the /tmp tmpfs instead of printing an error per report.
ENV XDG_CACHE_HOME=/tmp/.cache

# PDF reports (WeasyPrint): pango and harfbuzz lay the page out, per WeasyPrint's own
# install notes; slim ships no fonts, so DejaVu is the one font every report uses.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 hud \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hud \
 && mkdir -p /config /data \
 && chown 1000:1000 /config /data

WORKDIR /app
COPY --from=deps --chown=1000:1000 /app/.venv ./.venv
COPY --from=deps --chown=1000:1000 /app/hud ./hud
COPY --from=web  --chown=1000:1000 /web/dist ./web/dist
COPY --chown=1000:1000 alembic.ini LICENSE ./
# The bundled provider templates: the Connect a service wizard's catalog (not their fixtures).
COPY --chown=1000:1000 templates/providers/*.yaml ./templates/providers/

USER 1000:1000
VOLUME ["/config", "/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "hud.healthcheck"]
CMD ["python", "-m", "hud"]
