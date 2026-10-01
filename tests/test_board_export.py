# SPDX-License-Identifier: Apache-2.0
"""Board export (PLAN §9.5): a board read as report sections by a fixed mapping, exported
now as PDF or CSV without writing anything, or saved once as a weekly report file that
validates, runs, and is never replaced."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from hud.collector import LiveCache
from hud.config.loader import parse_yaml
from hud.config.schemas.board import BoardDocument
from hud.config.schemas.report import ReportDocument, Section
from hud.main import create_app
from hud.models import Unit
from hud.reporting import board_export
from tests.conftest import sign_in_admin
from tests.test_api_health import _env
from tests.test_widgets_engine import metric, res

HOST = "glances:host:main"
BOARD = f"""\
# The lab board — hand-written.
apiVersion: hud/v1
kind: Board
metadata: {{ name: lab, title: Lab }}
spec:
  sections:
    - id: infra
      title: Infrastructure
      stats: [{{ resource: "{HOST}", metric: cpu_pct }}]
  widgets:
    - {{ id: cpu, type: metric, title: CPU, grid: {{ col: 1, row: 1 }},
        source: {{ resource: "{HOST}", metric: cpu_pct }} }}
    - {{ id: todo, type: list, title: Downloads, grid: {{ col: 2, row: 1 }} }}
    - {{ id: apps, type: list, title: Apps, grid: {{ col: 3, row: 1 }},
        source: {{ select: {{ provider: web }} }},
        display: {{ uptime: true, trend: response_seconds }} }}
    - id: host
      type: resource
      section: infra
      grid: {{ col: 1, row: 1 }}
      source: {{ resource: "{HOST}" }}
      display:
        style: hero
        stats: [{{ resource: "{HOST}", metric: mem_used_bytes, label: Memory }}]
    - {{ id: when, type: heatmap, section: infra, grid: {{ col: 2, row: 1 }},
        source: {{ resource: "{HOST}", metric: cpu_pct }} }}
    - {{ id: load, type: chart, title: Load, section: infra, grid: {{ col: 1, row: 2 }},
        source: {{ series: [{{ metric: cpu_pct, resource: "{HOST}" }}] }} }}
    - {{ id: busy, type: chart, section: infra, grid: {{ col: 2, row: 2 }},
        source: {{ select: {{ kind: container }}, metric: mem_pct, limit: 5 }} }}
    - {{ id: cpu-bars, type: bars, title: Containers, section: infra, grid: {{ col: 1, row: 3 }},
        source: {{ select: {{ provider: docker }}, metric: cpu_pct }} }}
    - {{ id: site, type: uptime, section: infra, grid: {{ col: 2, row: 3 }},
        source: {{ resource: "web:endpoint:good" }} }}
    - {{ id: status, type: status, section: infra, grid: {{ col: 1, row: 4 }},
        source: {{ select: {{ kind: endpoint }} }} }}
    - {{ id: status-again, type: status, title: Again, section: infra, grid: {{ col: 2, row: 4 }},
        source: {{ select: {{ kind: endpoint }} }} }}
    - {{ id: outages, type: incidents, section: infra, grid: {{ col: 1, row: 5 }},
        source: {{ select: {{ kind: endpoint }} }} }}
    - {{ id: disks, type: capacity, section: infra, grid: {{ col: 2, row: 5 }},
        source: {{ select: {{ kind: filesystem }} }} }}
    - {{ id: notes, type: static, section: infra, grid: {{ col: 1, row: 6 }} }}
    - {{ id: grafana, type: embed, title: Grafana, section: infra, grid: {{ col: 2, row: 6 }},
        source: {{ url: "https://grafana.example.invalid" }} }}
"""


def _board(text: str = BOARD) -> BoardDocument:
    return BoardDocument.model_validate(parse_yaml(text, Path("lab.yaml")))


def _fill(cache: LiveCache) -> None:
    cache.apply(
        "glances",
        "host",
        [res(HOST)],
        [
            metric(HOST, "cpu_pct", 12.0, Unit.PCT),
            metric(HOST, "mem_used_bytes", 8e9, Unit.BYTES),
        ],
    )
    cache.apply("web", "collect", [res("web:endpoint:good")], [])


def test_a_board_maps_to_sections_in_its_own_order() -> None:
    cache = LiveCache()
    _fill(cache)
    plan = board_export.plan(_board(), cache)
    got = [(s["kind"], s["title"]) for s in plan.sections]
    assert got == [
        ("trend_chart", "Lab"),
        ("uptime_table", "Apps"),  # a list that shows uptime and a trend
        ("top_n", "Apps · response_seconds"),
        ("trend_chart", "Infrastructure · percent"),
        ("trend_chart", "Infrastructure · bytes"),
        # "Load" plots exactly the section's cpu stat: one chart, not two.
        ("top_n", "Top mem_pct"),
        ("top_n", "Containers"),
        ("uptime_table", "Availability"),
        ("uptime_table", "Status"),  # "Again" selects the same: one table, not two
        ("uptime_table", "Incidents"),
        ("capacity", "Disks"),
    ]
    by_title = {s["title"]: s for s in plan.sections}
    # The heatmap repeats the section's cpu stat: one line, not two.
    assert by_title["Infrastructure · percent"]["series"] == [
        {"metric": "cpu_pct", "resource": HOST}
    ]
    assert by_title["Infrastructure · bytes"]["series"][0]["label"] == "Memory"
    assert by_title["Availability"]["resource"] == "web:endpoint:good"
    assert "select" not in by_title["Availability"]
    assert by_title["Incidents"]["sort"] == "-incident_count"
    assert by_title["Top mem_pct"]["limit"] == 5
    assert plan.left_out == ["Downloads (list)", "notes (static)", "Grafana (embed)"]
    TypeAdapter(list[Section]).validate_python(plan.sections)


def test_a_long_board_keeps_to_what_a_report_holds() -> None:
    widgets = "\n".join(
        f"    - {{ id: s{i}, type: status, grid: {{ col: 1, row: {i + 1} }},"
        f" source: {{ select: {{ provider: p{i} }} }} }}"
        for i in range(25)
    )
    head = "apiVersion: hud/v1\nkind: Board\nmetadata: { name: big }\nspec:\n  widgets:\n"
    text = f"{head}{widgets}\n"
    plan = board_export.plan(_board(text), LiveCache())
    assert len(plan.sections) == board_export.MAX_SECTIONS
    assert plan.left_out == ["5 more section(s): a report holds 20"]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    env = _env(tmp_path)
    (env.config_dir / "boards").mkdir(parents=True)
    (env.config_dir / "boards" / "lab.yaml").write_text(BOARD)
    (env.config_dir / "boards" / "empty.yaml").write_text(
        "apiVersion: hud/v1\nkind: Board\nmetadata: { name: empty }\nspec:\n  widgets:\n"
        "    - { id: n, type: static, grid: { col: 1, row: 1 } }\n"
    )
    with TestClient(create_app(env)) as c:
        _fill(c.app.state.cache)  # type: ignore[attr-defined]
        yield c


def _export(client: TestClient, **params: Any) -> httpx.Response:
    return client.get("/api/v1/reports/adhoc", params={"board": "lab", **params})


def test_export_renders_now_and_writes_nothing(client: TestClient) -> None:
    assert _export(client).status_code == 401
    sign_in_admin(client)
    reports_dir = client.app.state.reports.out_dir  # type: ignore[attr-defined]
    doc = _export(client, range="24h")
    assert doc.status_code == 200, doc.text
    assert doc.headers["content-type"] == "application/pdf"
    assert doc.headers["content-disposition"].startswith('inline; filename="lab-24h-')
    assert doc.content.startswith(b"%PDF-")
    plan = client.get("/api/v1/reports/adhoc/plan", params={"board": "lab"}).json()
    assert plan["sections"][:2] == ["Lab", "Apps"] and "Grafana (embed)" in plan["left_out"]
    sheet = _export(client, range="30d", format="csv")
    assert sheet.headers["content-disposition"].startswith('attachment; filename="lab-30d-')
    assert sheet.text.startswith("section,title,row,key,value")
    assert "Infrastructure · percent" in sheet.text
    assert not reports_dir.exists() or not any(reports_dir.iterdir())
    assert _export(client, range="90d").status_code == 422
    assert _export(client, format="docx").status_code == 422
    assert _export(client, board="nope").status_code == 404
    empty = _export(client, board="empty")
    assert empty.status_code == 422 and "history" in empty.json()["detail"]


def test_saving_writes_a_weekly_report_once(client: TestClient) -> None:
    sign_in_admin(client)
    r = client.post("/api/v1/reports/from-board", json={"board": "lab"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file"] == "reports/lab.yaml" and body["next_run"] is not None
    assert body["left_out"] == ["Downloads (list)", "notes (static)", "Grafana (embed)"]
    path = client.app.state.env.config_dir / "reports" / "lab.yaml"  # type: ignore[attr-defined]
    text = path.read_text()
    assert text.startswith("# Saved from the Lab board on ")
    assert "Downloads (list), notes (static), Grafana (embed)." in text
    assert "\n  sections:\n    - kind: trend_chart\n" in text
    doc = ReportDocument.model_validate(parse_yaml(text, path))
    assert doc.spec.schedule == "0 7 * * MON" and doc.spec.window.from_ == "-7d"
    assert [o.format for o in doc.spec.outputs] == ["html", "csv", "pdf"]
    assert doc.metadata.title == "Lab weekly"
    listed = [x["name"] for x in client.get("/api/v1/reports").json()["reports"]]
    assert listed == ["lab"]
    run = client.post("/api/v1/reports/lab/run").json()
    assert run["error"] is None and len(run["files"]) == 3
    again = client.post("/api/v1/reports/from-board", json={"board": "lab"})
    assert again.status_code == 409 and "edit that file" in again.json()["detail"]
    assert path.read_text() == text


def test_export_and_save_need_their_permissions(client: TestClient) -> None:
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
    assert _export(client).status_code == 403
    assert client.post("/api/v1/reports/from-board", json={"board": "lab"}).status_code == 403
