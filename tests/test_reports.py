# SPDX-License-Identifier: Apache-2.0
"""Reports (PLAN §9): definitions validate as written, each section reads real history,
the runner writes HTML, CSV and PDF (saying what it could not produce), a hostile name is
escaped, the PDF fetches nothing, one broken section does not sink the report, and the API
is permission-gated."""

import urllib.request
from collections.abc import Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, insert

from hud.collector import LiveCache
from hud.config import ConfigManager
from hud.config.schemas.report import ReportDocument, ReportSpec
from hud.main import create_app
from hud.models import Unit
from hud.reporting import render
from hud.reporting.runner import ReportRunner
from hud.reporting.sections import Window, build
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import availability, events, samples, series
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import T0, metric, res

NOW = int(T0.timestamp())
DAY = 86_400
TEMPLATE = Path(__file__).parent.parent / "templates" / "reports" / "weekly-lab.yaml"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def _spec(sections: list[dict], **extra: object) -> ReportSpec:
    return ReportSpec.model_validate({"sections": sections, **extra})


def test_the_bundled_weekly_report_validates(config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "reports").mkdir()
    (config_dir / "reports" / "weekly-lab.yaml").write_text(TEMPLATE.read_text())
    snap = ConfigManager(config_dir).load()
    assert snap.quarantined == () and snap.warnings == ()
    (doc,) = [d.model for d in snap.documents if isinstance(d.model, ReportDocument)]
    assert [s.kind for s in doc.spec.sections] == [
        "uptime_table",
        "trend_chart",
        "top_n",
        "capacity",
        "events",
    ]


@pytest.mark.parametrize(
    ("extra", "sections"),
    [
        ({"schedule": "every monday"}, [{"kind": "events"}]),
        ({"window": {"from": "-500d"}}, [{"kind": "events"}]),
        ({"window": {"from": "7d"}}, [{"kind": "events"}]),  # not relative
        ({"outputs": [{"format": "html", "path": "../../etc/x.html"}]}, [{"kind": "events"}]),
        ({"outputs": [{"format": "webhook"}]}, [{"kind": "events"}]),  # no url
        ({}, [{"kind": "raw_table"}]),  # not in v1
        ({}, []),
        ({}, [{"kind": "uptime_table", "sort": "nonsense"}]),
    ],
)
def test_bad_definitions_are_refused(extra: dict, sections: list[dict]) -> None:
    with pytest.raises(ValidationError):
        _spec(sections, **extra)


def _cache() -> LiveCache:
    cache = LiveCache()
    cache.apply(
        "web",
        "collect",
        [
            res("web:endpoint:good"),
            res("web:endpoint:flaky"),
            res("web:endpoint:evil").model_copy(update={"name": "<script>alert(1)</script>"}),
        ],
        [],
    )
    cache.apply(
        "glances",
        "fs",
        [res("glances:filesystem:/mnt")],
        [metric("glances:filesystem:/mnt", "used_pct", 80.0, Unit.PCT)],
    )
    return cache


def _history(db: Engine) -> None:
    week = NOW - 7 * DAY
    with db.begin() as conn:
        conn.execute(
            insert(availability),
            [
                {
                    "resource_uid": "web:endpoint:good",
                    "state": "up",
                    "started_at": week,
                    "ended_at": None,
                },
                {
                    "resource_uid": "web:endpoint:flaky",
                    "state": "up",
                    "started_at": week,
                    "ended_at": NOW - 3 * DAY,
                },
                {
                    "resource_uid": "web:endpoint:flaky",
                    "state": "down",
                    "started_at": NOW - 3 * DAY,
                    "ended_at": NOW - 3 * DAY + 3600,
                },
                {
                    "resource_uid": "web:endpoint:flaky",
                    "state": "up",
                    "started_at": NOW - 3 * DAY + 3600,
                    "ended_at": NOW - DAY,
                },
                {
                    "resource_uid": "web:endpoint:flaky",
                    "state": "down",
                    "started_at": NOW - DAY,
                    "ended_at": NOW - DAY + 600,
                },
                {
                    "resource_uid": "web:endpoint:flaky",
                    "state": "up",
                    "started_at": NOW - DAY + 600,
                    "ended_at": None,
                },
                # Opened and closed at once, around a restart: not an outage.
                {
                    "resource_uid": "web:endpoint:good",
                    "state": "down",
                    "started_at": NOW - 2 * DAY,
                    "ended_at": NOW - 2 * DAY,
                },
            ],
        )
        sid = conn.execute(
            insert(series).values(
                provider="glances",
                resource_uid="glances:filesystem:/mnt",
                metric="used_pct",
                unit="pct",
                first_seen=week,
                last_seen=NOW,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(samples),
            [
                {"series_id": sid, "ts": week + 3600 * i, "value": 60 + i * 20 / 168}
                for i in range(168)
            ],
        )
        conn.execute(
            insert(events),
            [
                {
                    "resource_uid": "web:endpoint:flaky",
                    "type": "state_change",
                    "severity": "error",
                    "message": "flaky: up → down",
                    "ts": NOW - DAY,
                },
                {
                    "resource_uid": "web:endpoint:good",
                    "type": "state_change",
                    "severity": "info",
                    "message": "good: unknown → up",
                    "ts": NOW - DAY,
                },
            ],
        )


WINDOW = Window(NOW - 7 * DAY, NOW, ZoneInfo("America/Chicago"))


def test_uptime_ranks_the_worst_first_and_counts_outages(db: Engine) -> None:
    _history(db)
    spec = _spec(
        [
            {
                "kind": "uptime_table",
                "select": {"kind": "endpoint"},
                "highlight": {"uptime_pct": {"lt": 99.5}},
            }
        ]
    )
    s = build(spec.sections[0], db, _cache(), WINDOW)
    flaky, *_ = s.rows
    assert flaky["name"] == "flaky"
    assert flaky["incident_count"] == 2 and flaky["downtime_total"] == 4200
    assert flaky["longest_outage"] == 3600
    assert flaky["uptime_pct"] == pytest.approx(100 * (1 - 4200 / (7 * DAY)))
    assert flaky["highlight"] == {"uptime_pct": "warn"}
    good = next(r for r in s.rows if r["uid"] == "web:endpoint:good")
    assert good["incident_count"] == 0  # its zero-length down span is not an outage
    evil = next(r for r in s.rows if r["uid"] == "web:endpoint:evil")
    assert evil["uptime_pct"] is None  # never observed: no number, not 100 %


def test_capacity_projects_the_disk(db: Engine) -> None:
    _history(db)
    spec = _spec([{"kind": "capacity", "select": {"kind": "filesystem"}}])
    (row,) = build(spec.sections[0], db, _cache(), WINDOW).rows
    assert row["verdict"] == "filling" and row["pct_per_day"] == pytest.approx(20 / 7, rel=0.02)
    assert row["warn"] is True and row["reaches_warn_on"] is not None  # 85 % within 180d


def test_events_keep_only_the_asked_severities(db: Engine) -> None:
    _history(db)
    s = build(_spec([{"kind": "events"}]).sections[0], db, _cache(), WINDOW)
    assert [r["message"] for r in s.rows] == ["flaky: up → down"]


def test_a_broken_section_is_its_own_note(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from hud.reporting import sections  # noqa: PLC0415

    def boom(*_a: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sections, "events", boom)
    s = build(_spec([{"kind": "events"}]).sections[0], db, _cache(), WINDOW)
    assert s.note == "failed: RuntimeError: disk on fire"


def test_html_escapes_names_and_says_what_it_did_not_produce(db: Engine) -> None:
    _history(db)
    spec = _spec([{"kind": "uptime_table", "select": {"kind": "endpoint"}}])
    sections = [build(spec.sections[0], db, _cache(), WINDOW)]
    meta = render.ReportMeta("Weekly", WINDOW.since, WINDOW.until, WINDOW.tz, "0.0.1", ["pdf (x)"])
    page = render.html(meta, sections)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Not produced: pdf (x)" in page
    csv = render.csv_text(sections, WINDOW.tz)
    assert csv.splitlines()[0] == "section,title,row,key,value"
    assert "uptime_table,Service availability,flaky,incident_count,2" in csv


def test_the_pdf_is_the_page_and_fetches_nothing(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _history(db)
    spec = _spec([{"kind": "uptime_table", "select": {"kind": "endpoint"}}])
    sections = [build(spec.sections[0], db, _cache(), WINDOW)]
    meta = render.ReportMeta("Weekly", WINDOW.since, WINDOW.until, WINDOW.tz, "0.0.1")
    fetched: list[object] = []

    def refuse(_self: object, url: object, *_a: object, **_k: object) -> None:
        fetched.append(url)
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    doc = render.pdf(render.html(meta, sections))
    assert doc.startswith(b"%PDF-") and len(doc) > 1000
    # Anything outside the page is refused before it is opened.
    hostile = '<img src="http://example.invalid/x.png"><img src="file:///etc/hostname">'
    render.pdf(render.html(meta, sections).replace("<body>", "<body>" + hostile))
    assert fetched == []


def test_a_failed_pdf_is_reported_and_the_rest_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    (env.config_dir / "reports").mkdir(parents=True)
    (env.config_dir / "reports" / "weekly.yaml").write_text(REPORT)

    def broken(_page: str) -> bytes:
        raise OSError("cannot load library 'libpango-1.0-0'")

    monkeypatch.setattr(render, "pdf", broken)
    with TestClient(create_app(env)) as client:
        sign_in_admin(client)
        body = client.post("/api/v1/reports/weekly/run").json()
    assert [f.rsplit(".", 1)[1] for f in body["files"]] == ["html", "csv"]
    assert body["skipped"] == ["pdf (failed: OSError: cannot load library 'libpango-1.0-0')"]


REPORT = """\
apiVersion: hud/v1
kind: Report
metadata: { name: weekly, title: Weekly }
spec:
  schedule: "0 7 * * MON"
  sections:
    - { kind: events }
  outputs:
    - { format: html }
    - { format: csv }
    - { format: pdf }
"""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "reports").mkdir(parents=True)
    (env.config_dir / "reports" / "weekly.yaml").write_text(REPORT)
    (env.config_dir / "reports" / "weekly-lab.yaml").write_text(TEMPLATE.read_text())
    with TestClient(create_app(env)) as c:
        yield c


def test_reports_api_runs_lists_and_serves(client: TestClient) -> None:
    assert client.get("/api/v1/reports").status_code == 401
    sign_in_admin(client)
    listed = {r["name"]: r for r in client.get("/api/v1/reports").json()["reports"]}
    assert set(listed) == {"weekly", "weekly-lab"}
    assert listed["weekly"]["next_run"] is not None and listed["weekly"]["files"] == []
    run = client.post("/api/v1/reports/weekly/run")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["error"] is None and len(body["files"]) == 3
    assert body["skipped"] == []
    files = {
        f["format"]: f["name"] for f in client.get("/api/v1/reports").json()["reports"][0]["files"]
    }
    page = client.get(f"/api/v1/reports/weekly/files/{files['html']}")
    assert page.status_code == 200 and "sandbox" in page.headers["content-security-policy"]
    assert "script-src" not in page.headers["content-security-policy"]
    sheet = client.get(f"/api/v1/reports/weekly/files/{files['csv']}")
    assert sheet.headers["content-disposition"].startswith("attachment")
    doc = client.get(f"/api/v1/reports/weekly/files/{files['pdf']}")
    assert doc.headers["content-type"] == "application/pdf"
    assert doc.headers["content-disposition"].startswith("inline")
    assert doc.content.startswith(b"%PDF-")
    # Only this report's own files: not another's, not a path.
    assert client.get("/api/v1/reports/weekly-lab/files/" + files["html"]).status_code == 404
    assert client.get("/api/v1/reports/weekly/files/..%2Fdashboard.db").status_code == 404
    assert client.post("/api/v1/reports/nope/run").status_code == 404


def test_reports_need_their_own_permissions(client: TestClient) -> None:
    sign_in_admin(client)
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "viewer", "password": "a-long-enough-password", "groups": ["household"]},
    )
    assert r.status_code == 201, r.text
    client.post("/api/v1/auth/logout")
    r = client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "a-long-enough-password"}
    )
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/api/v1/reports").status_code == 403
    assert client.post("/api/v1/reports/weekly/run").status_code == 403


def test_a_file_name_that_matches_another_report_is_not_listed(tmp_path: Path) -> None:
    out = tmp_path / "reports"
    out.mkdir()
    for name in ("weekly-2026-09-28.html", "weekly-lab-2026-09-28.html", "weekly-notes.txt"):
        (out / name).write_text("x")
    runner = ReportRunner(None, LiveCache(), None, out)  # type: ignore[arg-type]
    assert [p.name for p in runner.files("weekly")] == ["weekly-2026-09-28.html"]
