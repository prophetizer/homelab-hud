# HUD — Homelab Ultimate Dashboard

A self-hosted homelab dashboard with three things the category is missing:
a **reporting engine** that keeps history, a **wizard** that connects any JSON API
without writing YAML, and a **unified shell** for embedded apps.

> **Status: Phase 1 — core dashboard.** What exists today: declarative providers from
> YAML alone, two packaged Python plugins (`docker`, `sonarr`) plus drop-ins, a scheduler
> with jitter, timeouts and circuit breakers, a live cache that keeps last-known-good and
> says so, raw sample/event history in SQLite, and boards with `static`, `resource`,
> `list`, `metric` and `embed` widgets, and authentication with `local`, `forward` and
> `oidc` backends behind RBAC enforced at the data layer. No layout editor yet, no
> reports, no wizard. Nothing below claims otherwise.

Licensed under [Apache-2.0](LICENSE). No CLA.

## Run it

```bash
mkdir -p config
docker compose up --build
```

Then open <http://localhost:8080>. An empty `./config` gets a commented `settings.yaml`
written on first start. `GET /api/v1/health` reports the app version, config version
(a hash of every loaded YAML file), database size, uptime and per-provider health.

Three minutes to a live tile: copy `templates/providers/home-assistant.yaml` to
`./config/providers/`, set `HA_BASE_URL` in the container environment, put a long-lived
token at `./config/secrets.yaml` as `home_assistant_token: …` (or `HUD_SECRET_HOME_ASSISTANT_TOKEN`),
and drop a board in `./config/boards/`:

```yaml
apiVersion: hud/v1
kind: Board
metadata: { name: home, title: Home }
spec:
  widgets:
    - id: sensors
      type: list
      grid: { col: 1, row: 1, w: 2, h: 2 }
      source: { select: { kind: sensor } }
      display: { fields: [metric.value] }
```

No restart needed: providers and boards are hot-reloaded when the files change.

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
the only copy. Every file is an `apiVersion: hud/v1` document with a `kind`:
`Settings` (`settings.yaml`), `RBAC` (`rbac.yaml`), `Provider` (`providers/*.yaml`) and
`Board` (`boards/*.yaml`).

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
  token. `${VAR}` in a provider's transport or config is substituted from the environment.

### Authentication and access

There are no default credentials. With an empty `/config` the first visit shows a setup
screen that creates the admin account; that account lands in the `admins` group. Any subset
of three backends can be enabled, in precedence order:

```yaml
apiVersion: hud/v1
kind: Settings
spec:
  auth:
    backends: [local]            # any of local, forward, oidc
    session: { cookie_name: hud_session, lifetime: 7d }
    local:
      allow_registration: false  # self-service accounts, placed in no group
      min_password_length: 12
    forward:                     # identity headers from a proxy doing SSO
      trusted_proxies: ["172.20.0.0/16"]
      header_user: Remote-User
      header_groups: Remote-Groups
      header_email: Remote-Email
      groups_separator: ","
    oidc:                        # authorization code + PKCE against any discovery document
      discovery_url: https://id.example/.well-known/openid-configuration
      client_id: hud
      client_secret: ${secret:oidc_client_secret}
      groups_claim: groups       # dotted path into the ID token, then userinfo
```

* `local` — Argon2id hashes, server-side sessions in an HttpOnly `SameSite=Lax` cookie,
  a per-IP and per-username login limiter. Also the break-glass path when an IdP is down.
* `forward` — headers are honoured only from a peer inside `trusted_proxies` **and** when
  the user header is present; anything else is anonymous, never a guessed identity.
  Groups are re-read on every request. Startup refuses `forward` with no trusted proxies.
* `oidc` — nothing names an IdP; only the group claim path differs between them.
* `backends: []` refuses to start. Sessions and users live in `dashboard.db`; every login,
  logout, setup, user change and provider reload writes an `audit_log` row.

Permissions are strings granted to groups in `rbac.yaml`; the group names come from
whichever backend produced the identity. A missing `rbac.yaml` means the bundled default:
`admins: ["*"]`, `household: ["boards:view:*"]`, everyone else unmatched → `guests` → nothing.

```yaml
apiVersion: hud/v1
kind: RBAC
spec:
  groups:
    admins:    { permissions: ["*"] }
    household: { permissions: ["boards:view:media", "boards:view:home"] }
    ops:       { permissions: ["boards:view:*", "providers:view", "providers:reload"] }
  defaults: { unmatched_group: guests }
```

A board is visible when the caller holds `boards:view:<name>` (wildcards allowed) **or** one
of their groups is in the board's `metadata.visible_to`. Enforcement is server-side at the
data layer: a hidden board is absent from `/api/v1/boards`, its detail route answers 404,
and `/api/v1/resources` and `/api/v1/events` return only the uids the caller's boards
expose unless they hold `resources:view`. `/api/v1/providers` and the detailed `/health`
need `providers:view`; user administration needs `users:manage`; `*` grants everything.
Mutating API calls carry the session's CSRF token in `X-CSRF-Token` (from `GET /api/v1/auth/me`).

### Providers

Two tiers, one contract: both produce the same canonical `Resource` / `Metric` / `Event`
objects, so any widget works with any provider.

**Declarative** (`spec.transport` + `spec.resources[]`) — for any JSON-over-HTTP API, no
code. Each `resources[]` entry is one request, a JSONPath `select`, and a `map` block of
sandboxed Jinja expressions over `item`. `templates/providers/home-assistant.yaml` is the
worked example; every bundled template ships with a response fixture and a CI test.

**Plugin** (`spec.plugin` + `spec.config`) — Python, for anything the declarative tier
cannot express. Packaged: `docker` (via `linuxserver/socket-proxy`, never the socket;
`GET` only) and `sonarr`. Drop-ins go in `/config/plugins/<name>/provider.py`:

```yaml
apiVersion: hud/v1
kind: Provider
metadata: { name: docker }
spec:
  plugin: docker
  config: { base_url: ${DOCKER_HOST} }   # tcp://socket-proxy:2375 is rewritten to http://
```

```python
# /config/plugins/hello/provider.py
from hud.providers.sdk import PluginConfig, PluginProvider, Resource, State, register
from datetime import UTC, datetime

class Config(PluginConfig):
    greeting: str = "hi"

@register("hello", config_model=Config)
class Hello(PluginProvider):
    async def discover(self) -> list[Resource]:          # every 5 min
        return [Resource(uid=f"{self.name}:thing:one", provider=self.name, kind="thing",
                         name=self.config.greeting, state=State.UP, fetched_at=datetime.now(UTC))]
    # collect(resources) -> PollResult runs at spec.defaults.interval; default keeps state only
```

Each poll group runs under a timeout; three consecutive failures open a circuit breaker
that backs off to five minutes. A failing provider keeps its last-known-good resources,
flagged `stale`, with the error shown on its tiles and in `GET /api/v1/providers`.
`POST /api/v1/providers/{name}/reload` rebuilds one provider and polls it immediately.

### Boards

```yaml
apiVersion: hud/v1
kind: Board
metadata: { name: media, title: Media Stack }
spec:
  layout: { columns: { sm: 1, md: 2, lg: 4 }, gap: 12 }
  widgets:
    - { id: note,  type: static,   grid: { col: 1, row: 1 }, display: { text: Hello } }
    - { id: plex,  type: resource, grid: { col: 2, row: 1 }, source: { resource: "docker:container:plex" },
        display: { fields: [state, attrs.image, metric.cpu_pct] } }
    - { id: down,  type: list,     grid: { col: 3, row: 1, w: 2 },
        source: { select: { kind: container, state: [down, degraded] } } }
    - { id: queue, type: metric,   grid: { col: 1, row: 2 },
        source: { metric: queue_size, resource: "sonarr:service:main" },
        display: { sparkline: { range: 6h }, thresholds: [{ gte: 50, state: warn }] } }
    - { id: grafana, type: embed,  grid: { col: 2, row: 2, w: 3, h: 3 },
        source: { url: "https://grafana.lab/" } }
```

`grid` positions apply at the `lg` breakpoint; smaller screens reflow in order. An `embed`
whose target sends `X-Frame-Options` or a CSP `frame-ancestors` renders an honest card with
an *Open* button instead of a grey box. `chart`, `uptime`, `report`, `action` and
`composite` are recognised but render "arrives in Phase N" until that phase.

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
  config/            loader, schemas (Settings/Provider/Board), secrets, round-trip writer
  models/            canonical Resource / Metric / Event / Action, unit normalization
  providers/         registry, declarative engine, plugin SDK + loader, builtin plugins
  collector/         APScheduler poll loops, circuit breaker, live cache, store writer
  widgets/           widget engine, framing probe, sparkline sample reads
  store/             SQLAlchemy Core tables, engines, Alembic migrations
  reporting/         placeholder for Phase 2
web/                 React + Vite + TypeScript: router, board renderer, widgets
templates/providers/ bundled provider templates + response fixtures + expected mappings
tests/
```
