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
        # Year 0 is a proleptic-Gregorian leap year, so its leap day is a valid
        # (if unrepresentable) date, not a malformed one.
        ("0000-02-29", Unrepresentable("0000-02-29")),
        ("2016-12-31T23:59:60Z", Unrepresentable("2016-12-31T23:59:60Z")),
        # Malformed: matches none of the five forms.
        ("2010-13-99", Malformed("2010-13-99")),
        ("2020-13", Malformed("2020-13")),
        ("2020-02-30", Malformed("2020-02-30")),
        # A malformed year-0000 date is malformed like any other year, not
        # misclassified as an unrepresentable-but-valid form.
        ("0000-13-01", Malformed("0000-13-01")),
        ("0000-02-30", Malformed("0000-02-30")),
        ("2021-02-29", Malformed("2021-02-29")),
        ("2020-01-01T00:00:00", Malformed("2020-01-01T00:00:00")),
        ("not-a-date", Malformed("not-a-date")),
        ("", Malformed("")),
        # Guard-boundary cases for the "T" fast path: a "T" that is not a
        # datetime, or a lowercase "t", falls to Malformed rather than to a wrong
        # form; a signed non-expanded value and length junk stay malformed via
        # the chain. (The non-ASCII digit case is test_non_ascii_digits_malformed.)
        ("2020T", Malformed("2020T")),
        ("2020-01-01t00:00:00Z", Malformed("2020-01-01t00:00:00Z")),
        ("+2020", Malformed("+2020")),
        ("20201", Malformed("20201")),
    ],
)
def test_resolve(value: str, expected: TemporalResult) -> None:
    assert resolve(value) == expected


def test_non_ascii_digits_malformed() -> None:
    """The ``[0-9]`` digit class rejects non-ASCII digits (``\\d`` would accept).

    The value is built with ``chr`` so the source stays ASCII; it is the
    fullwidth spelling of ``"2020"`` (``U+FF10`` is fullwidth zero).
    """
    fullwidth_year = "".join(chr(0xFF10 + int(digit)) for digit in "2020")

    assert resolve(fullwidth_year) == Malformed(fullwidth_year)


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


def test_datetime_uses_only_its_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """A datetime string is settled by ``_DATETIME`` alone.

    The ``"T"`` fast path routes straight to the datetime pattern, so the four
    reduced-form patterns must not run. Replacing each with one that raises on
    ``fullmatch`` proves the guard never falls through to the chain: if it did,
    resolving a datetime would raise instead of returning its `Moment`.
    """
    for name in ("_YEAR", "_EXPANDED_YEAR", "_YEAR_MONTH", "_DATE"):
        monkeypatch.setattr(f"covjson_msgspec.temporal.{name}", _RaisingPattern())

    assert resolve("2020-01-01T00:00:00Z") == Moment(
        datetime(2020, 1, 1, tzinfo=UTC), Precision.SECOND
    )


class _RaisingPattern:
    """A stand-in whose ``fullmatch`` raises, to prove a pattern is never run."""

    def fullmatch(self, string: str) -> object:
        msg = f"a reduced-form pattern must not run for {string!r}"
        raise AssertionError(msg)
