# HUD — Homelab Ultimate Dashboard

A self-hosted homelab dashboard with three things the category is missing:
a **reporting engine** that keeps history, a **wizard** that connects any JSON API
without writing YAML, and a **unified shell** for embedded apps.

> **Status: Phase 0 — foundation.** What exists today is a running container skeleton:
> configuration loading with hot reload and comment-preserving write-back, the canonical
> data model, the SQLite schema, a health endpoint and a one-tile React shell.
> No providers, widgets, reports or wizard yet. Nothing below claims otherwise.

Licensed under [Apache-2.0](LICENSE). No CLA.

## Run it

```bash
mkdir -p config
docker compose up --build
```

Then open <http://localhost:8080>. An empty `./config` gets a commented `settings.yaml`
written on first start. `GET /api/v1/health` reports the app version, config version
(a hash of every loaded YAML file), database size and uptime.

`./config` must exist before `up`: the container runs as uid 1000 with a read-only
root filesystem and cannot write into a directory Docker created as root.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `HUD_CONFIG_DIR` | `/config` | Authored YAML. Bind-mount it; `git init` it if you like. |
| `HUD_DATA_DIR` | `/data` | `dashboard.db` (state) and `metrics.db` (history, disposable). |
| `HUD_PORT` | `8080` | Single port for API and SPA. |
| `HUD_LOG_LEVEL` | `INFO` | |
| `HUD_CONFIG_POLL_INTERVAL` | `2.0` | Seconds between config change checks. |
| `HUD_SECRET_<NAME>` | — | One source for `${secret:name}` references (see below). |

### Configuration

Everything authored lives in `/config/*.yaml`; the UI is an editor for those files, never
the only copy. Every file is an `apiVersion: hud/v1` document with a `kind`. Phase 0 knows
one kind:

```yaml
apiVersion: hud/v1
kind: Settings
spec:
  title: HUD
  timezone: UTC
  theme: dark            # dark | light | auto
  retention:
    samples: 48h
    rollup_5m: 14d
    rollup_1h: 180d
    rollup_1d: forever
```

* A syntax or schema error refuses startup and names `file:line:col`. After a successful
  start, a bad edit keeps the last good configuration running and reports the error in
  `/api/v1/health` and on the dashboard.
* Unknown keys inside `spec` are preserved and logged as warnings, so rolling back to an
  older image never meets config it refuses.
* Secrets never go in YAML. Write `${secret:name}` and provide the value as a Docker secret
  at `/run/secrets/name`, the env var `HUD_SECRET_NAME`, or a key in `/config/secrets.yaml`
  (mode 0600, last resort). The validator hard-fails on anything that looks like a literal
  token.

## Develop

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy hud
uv run pytest -q
```

```bash
cd web && npm install && npm run build   # FastAPI serves web/dist
uv run python -m hud                     # http://localhost:8080
```

For a frontend dev loop, `npm run dev` serves Vite on :5173 and proxies `/api` to :8080.

Database migrations are Alembic, one environment per file:
`alembic -n dashboard revision -m "..."` or `alembic -n metrics revision -m "..."`.
They run automatically at startup; a non-trivial upgrade first copies the file to
`/data/backups/`.

## Layout

```
hud/                 backend package (FastAPI)
  api/               routers under /api/v1, SPA serving
  config/            loader, schemas, secrets, round-trip writer, manager
  models/            canonical Resource / Metric / Event / Action
  store/             SQLAlchemy Core tables, engines, Alembic migrations
  providers/ collector/ reporting/   placeholders for later phases
web/                 React + Vite + TypeScript shell
templates/providers/ bundled provider templates (empty until Phase 1)
tests/
```
