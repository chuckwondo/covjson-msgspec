"""Tests for bridge-independent temporal string conversion (temporal.py)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from covjson_msgspec import to_datetime
from covjson_msgspec.temporal import (
    Malformed,
    Moment,
    Precision,
    TemporalResult,
    Unrepresentable,
    resolve,
)

_UTC_PLUS_2 = timezone(timedelta(hours=2))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Full precision, timezone-aware (Z, offset, fractional seconds).
        (
            "2020-01-01T00:00:00Z",
            Moment(datetime(2020, 1, 1, tzinfo=UTC), Precision.SECOND),
        ),
        (
            "2020-01-01T00:00:00+02:00",
            Moment(datetime(2020, 1, 1, tzinfo=_UTC_PLUS_2), Precision.SECOND),
        ),
        (
            "2020-01-01T00:00:00.5Z",
            Moment(datetime(2020, 1, 1, 0, 0, 0, 500000, tzinfo=UTC), Precision.SECOND),
        ),
        # Date and reduced precision: naive, filled to the start of the period.
        ("2020-01-01", Moment(datetime(2020, 1, 1), Precision.DAY)),
        ("2020-06", Moment(datetime(2020, 6, 1), Precision.MONTH)),
        ("2020", Moment(datetime(2020, 1, 1), Precision.YEAR)),
        # A leap day in a leap year is valid.
        ("2020-02-29", Moment(datetime(2020, 2, 29), Precision.DAY)),
        # An in-range expanded year resolves like a plain year.
        ("+002020", Moment(datetime(2020, 1, 1), Precision.YEAR)),
        # Valid forms a stdlib datetime cannot hold.
        ("+102020", Unrepresentable("+102020")),
        ("-00100", Unrepresentable("-00100")),
        ("0000", Unrepresentable("0000")),
        ("0000-01-01", Unrepresentable("0000-01-01")),
        ("2016-12-31T23:59:60Z", Unrepresentable("2016-12-31T23:59:60Z")),
        # Malformed: matches none of the five forms.
        ("2010-13-99", Malformed("2010-13-99")),
        ("2020-13", Malformed("2020-13")),
        ("2020-02-30", Malformed("2020-02-30")),
        ("2021-02-29", Malformed("2021-02-29")),
        ("2020-01-01T00:00:00", Malformed("2020-01-01T00:00:00")),
        ("not-a-date", Malformed("not-a-date")),
        ("", Malformed("")),
    ],
)
def test_resolve(value: str, expected: TemporalResult) -> None:
    assert resolve(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2020-01-01T00:00:00Z", datetime(2020, 1, 1, tzinfo=UTC)),
        ("2020-01-01", datetime(2020, 1, 1)),
        ("2020-06", datetime(2020, 6, 1)),
        ("2020", datetime(2020, 1, 1)),
        ("+102020", None),
        ("0000", None),
        ("2016-12-31T23:59:60Z", None),
        ("2010-13-99", None),
        ("2020-01-01T00:00:00", None),
    ],
)
def test_to_datetime(value: str, expected: datetime | None) -> None:
    assert to_datetime(value) == expected


@pytest.mark.parametrize(
    ("value", "aware"),
    [
        ("2020-01-01T00:00:00Z", True),
        ("2020-01-01T00:00:00+02:00", True),
        ("2020-01-01", False),
        ("2020-06", False),
        ("2020", False),
    ],
)
def test_aware_iff_second_precision(value: str, aware: bool) -> None:
    result = resolve(value)

    assert isinstance(result, Moment)
    assert (result.when.tzinfo is not None) is aware
    assert (result.precision is Precision.SECOND) is aware
