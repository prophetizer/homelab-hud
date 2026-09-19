# SPDX-License-Identifier: Apache-2.0
from datetime import UTC, datetime

import pytest

from hud.models import Metric, SourceUnit, Unit, canonical_unit, normalize

NOW = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "source", "expected", "unit"),
    [
        (512, SourceUnit.MB, 512_000_000.0, Unit.BYTES),
        (1, SourceUnit.MIB, 1_048_576.0, Unit.BYTES),
        (2, SourceUnit.GIB, 2_147_483_648.0, Unit.BYTES),
        (1.5, SourceUnit.TB, 1.5e12, Unit.BYTES),
        (0.25, SourceUnit.RATIO, 25.0, Unit.PCT),
        (1.0, SourceUnit.RATIO, 100.0, Unit.PCT),
        (42.5, SourceUnit.PCT, 42.5, Unit.PCT),
        (100, SourceUnit.MBPS, 100_000_000.0, Unit.BPS),
        (125, SourceUnit.BYTES_PER_SEC, 1000.0, Unit.BPS),
        (1500, SourceUnit.MILLISECONDS, 1.5, Unit.SECONDS),
        (2, SourceUnit.HOURS, 7200.0, Unit.SECONDS),
        (212, SourceUnit.FAHRENHEIT, 100.0, Unit.CELSIUS),
        (32, SourceUnit.FAHRENHEIT, 0.0, Unit.CELSIUS),
        (1.2, SourceUnit.KILOWATTS, 1200.0, Unit.WATTS),
        (7, SourceUnit.COUNT, 7.0, Unit.COUNT),
    ],
)
def test_normalize(value: float, source: SourceUnit, expected: float, unit: Unit) -> None:
    got_value, got_unit = normalize(value, source)
    assert got_unit is unit
    assert got_value == pytest.approx(expected)


def test_every_source_unit_has_a_canonical_target() -> None:
    for source in SourceUnit:
        assert isinstance(canonical_unit(source), Unit)
        _, unit = normalize(1.0, source)
        assert unit is canonical_unit(source)


def test_metric_from_source_normalizes_at_boundary() -> None:
    m = Metric.from_source(
        resource_uid="docker:container:sonarr",
        name="mem_bytes",
        value=256,
        source_unit=SourceUnit.MB,
        ts=NOW,
    )
    assert m.value == 256_000_000.0
    assert m.unit is Unit.BYTES


def test_metric_direct_construction_requires_canonical_unit() -> None:
    m = Metric(resource_uid="a:b:c", name="cpu_pct", value=50.0, unit=Unit.PCT, ts=NOW)
    assert m.unit is Unit.PCT
    with pytest.raises(ValueError, match="unit"):
        Metric(resource_uid="a:b:c", name="cpu_pct", value=50.0, unit="mb", ts=NOW)  # type: ignore[arg-type]
