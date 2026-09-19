# SPDX-License-Identifier: Apache-2.0
"""Unit normalization at the provider boundary (PLAN.md §5).

Providers report in whatever their API uses; the collector calls :func:`normalize` once
and everything downstream sees canonical :class:`Unit` values only.
"""

from enum import StrEnum

from hud.models.enums import Unit


class SourceUnit(StrEnum):
    """Units a provider may declare. Each maps to exactly one canonical :class:`Unit`."""

    # percentages
    PCT = "pct"  # already 0-100
    RATIO = "ratio"  # 0-1
    # bytes (SI and IEC)
    BYTES = "bytes"
    KB = "kb"
    MB = "mb"
    GB = "gb"
    TB = "tb"
    KIB = "kib"
    MIB = "mib"
    GIB = "gib"
    TIB = "tib"
    # bandwidth
    BPS = "bps"
    KBPS = "kbps"
    MBPS = "mbps"
    GBPS = "gbps"
    BYTES_PER_SEC = "bytes_per_sec"
    # time
    SECONDS = "seconds"
    MILLISECONDS = "ms"
    MINUTES = "minutes"
    HOURS = "hours"
    # temperature
    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"
    # power
    WATTS = "watts"
    KILOWATTS = "kw"
    # dimensionless
    COUNT = "count"
    NONE = "none"


# (canonical unit, multiplicative factor). Fahrenheit is the one affine case, handled below.
_LINEAR: dict[SourceUnit, tuple[Unit, float]] = {
    SourceUnit.PCT: (Unit.PCT, 1.0),
    SourceUnit.RATIO: (Unit.PCT, 100.0),
    SourceUnit.BYTES: (Unit.BYTES, 1.0),
    SourceUnit.KB: (Unit.BYTES, 1e3),
    SourceUnit.MB: (Unit.BYTES, 1e6),
    SourceUnit.GB: (Unit.BYTES, 1e9),
    SourceUnit.TB: (Unit.BYTES, 1e12),
    SourceUnit.KIB: (Unit.BYTES, 1024.0),
    SourceUnit.MIB: (Unit.BYTES, 1024.0**2),
    SourceUnit.GIB: (Unit.BYTES, 1024.0**3),
    SourceUnit.TIB: (Unit.BYTES, 1024.0**4),
    SourceUnit.BPS: (Unit.BPS, 1.0),
    SourceUnit.KBPS: (Unit.BPS, 1e3),
    SourceUnit.MBPS: (Unit.BPS, 1e6),
    SourceUnit.GBPS: (Unit.BPS, 1e9),
    SourceUnit.BYTES_PER_SEC: (Unit.BPS, 8.0),
    SourceUnit.SECONDS: (Unit.SECONDS, 1.0),
    SourceUnit.MILLISECONDS: (Unit.SECONDS, 1e-3),
    SourceUnit.MINUTES: (Unit.SECONDS, 60.0),
    SourceUnit.HOURS: (Unit.SECONDS, 3600.0),
    SourceUnit.CELSIUS: (Unit.CELSIUS, 1.0),
    SourceUnit.WATTS: (Unit.WATTS, 1.0),
    SourceUnit.KILOWATTS: (Unit.WATTS, 1e3),
    SourceUnit.COUNT: (Unit.COUNT, 1.0),
    SourceUnit.NONE: (Unit.NONE, 1.0),
}


def canonical_unit(source: SourceUnit) -> Unit:
    """The canonical unit a source unit normalizes to."""
    if source is SourceUnit.FAHRENHEIT:
        return Unit.CELSIUS
    return _LINEAR[source][0]


def normalize(value: float, source: SourceUnit) -> tuple[float, Unit]:
    """Convert ``value`` expressed in ``source`` to its canonical unit.

    >>> normalize(512, SourceUnit.MB)
    (512000000.0, <Unit.BYTES: 'bytes'>)
    >>> normalize(0.25, SourceUnit.RATIO)
    (25.0, <Unit.PCT: 'pct'>)
    """
    if source is SourceUnit.FAHRENHEIT:
        return (value - 32.0) * 5.0 / 9.0, Unit.CELSIUS
    unit, factor = _LINEAR[source]
    return value * factor, unit
