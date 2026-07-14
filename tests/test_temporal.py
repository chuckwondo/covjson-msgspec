"""Tests for bridge-independent temporal string conversion (temporal.py)."""

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from covjson_msgspec import to_datetime
from covjson_msgspec.temporal import (
    Malformed,
    Moment,
    Precision,
    TemporalResult,
    Unrepresentable,
    _resolve_datetime_form,  # noqa: PLC2701  # pyright: ignore[reportPrivateUsage]
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
        # msgspec's native decoder accepts these, but the spec offset form (the
        # colon is required) does not, so the fast-path guard keeps them
        # Malformed: a lowercase "z", and a colon-less "+0500".
        ("2020-01-01T00:00:00z", Malformed("2020-01-01T00:00:00z")),
        ("2020-01-01T00:00:00+0500", Malformed("2020-01-01T00:00:00+0500")),
        # A fractional second with a colon offset is a conformant Moment.
        (
            "2020-01-01T00:00:00.5+02:00",
            Moment(
                datetime(2020, 1, 1, 0, 0, 0, 500000, tzinfo=_UTC_PLUS_2),
                Precision.SECOND,
            ),
        ),
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


def test_datetime_resolves_without_any_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid datetime is settled by ``msgspec.convert`` alone; no pattern runs.

    The ``"T"`` fast path parses through msgspec's native decoder, so none of the
    five compiled patterns (``_DATETIME`` included) is touched for a conformant
    value. Replacing each with one that raises on ``fullmatch`` proves it: if the
    fast path fell through to the regex chain, resolving a datetime would raise
    instead of returning its `Moment`.
    """
    for name in ("_YEAR", "_EXPANDED_YEAR", "_YEAR_MONTH", "_DATE", "_DATETIME"):
        monkeypatch.setattr(f"covjson_msgspec.temporal.{name}", _RaisingPattern())

    assert resolve("2020-01-01T00:00:00Z") == Moment(
        datetime(2020, 1, 1, tzinfo=UTC), Precision.SECOND
    )


def test_fast_path_matches_regex_oracle() -> None:
    """The msgspec fast path agrees with the regex form-classifier on every input.

    A seeded differential fuzz over datetime-form strings: `resolve`'s fast path
    (``msgspec.convert`` plus `_has_spec_timezone`) must return exactly what the
    regex oracle `_resolve_datetime_form` does. Since `resolve` falls back to that
    oracle whenever ``convert`` rejects, the discriminating power is entirely on
    the inputs ``convert`` accepts, so the corpus is built to exercise that branch
    (`_datetime_fuzz_value`); the `Moment` floor asserts it has not gone vacuous.
    """
    rng = random.Random(20260713)
    corpus = [_datetime_fuzz_value(rng) for _ in range(20000)]
    moments = sum(isinstance(resolve(value), Moment) for value in corpus)

    assert moments > len(corpus) // 4, "corpus went vacuous; check the generator"

    for value in corpus:
        assert resolve(value) == _resolve_datetime_form(value), value


def test_subsecond_fractional_rounds_to_moment() -> None:
    """A sub-microsecond fractional second (>= 7 digits) resolves to a rounded Moment.

    msgspec's decoder rounds to the microsecond where the old ``fromisoformat``
    path truncated (``...00.1234567Z`` yields ``...123457``, not ``...123456``);
    both discard precision a `datetime` cannot hold. This pins the accepted
    rounding so the difference stays intentional rather than a silent drift.
    """
    result = resolve("2020-01-01T00:00:00.1234567Z")

    assert result == Moment(
        datetime(2020, 1, 1, 0, 0, 0, 123457, tzinfo=UTC), Precision.SECOND
    )


def _datetime_fuzz_value(rng: random.Random) -> str:
    """Build a datetime-form fuzz string, weighted toward the accept branch.

    Every value carries a ``T``, since the invariant under test is exactly
    `resolve`'s ``"T"``-branch: a valid ``YYYY-MM-DDThh:mm:ss`` skeleton (with
    optional fractional up to six digits, the precision both parsers keep exactly)
    plus a time zone designator drawn from the full axis where msgspec and the
    spec form diverge: the conformant ``Z`` / ``±hh:mm``, msgspec's lenient
    lowercase ``z`` / colon-less ``±hhmm`` / hour-only ``±hh``, and the naive
    no-designator case. A minority are ``T``-bearing junk, so the regex fallback
    is exercised too.

    Examples
    --------
    >>> value = _datetime_fuzz_value(random.Random(0))
    >>> "T" in value
    True
    """
    digits = "0123456789"

    if rng.random() < 0.1:
        chars = [rng.choice(digits + ":+-.Z") for _ in range(rng.randint(0, 23))]
        chars.insert(rng.randint(0, len(chars)), "T")
        return "".join(chars)

    stamp = (
        f"{rng.randint(1, 9999):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        f"T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
    )

    if rng.random() < 0.5:
        stamp += "." + "".join(rng.choice(digits) for _ in range(rng.randint(1, 6)))

    hh, mm = rng.randint(0, 23), rng.randint(0, 59)
    designator = rng.choice(
        [
            "Z",
            "z",
            "",
            f"+{hh:02d}:{mm:02d}",
            f"-{hh:02d}:{mm:02d}",
            f"+{hh:02d}{mm:02d}",
            f"+{hh:02d}",
        ]
    )
    return stamp + designator


class _RaisingPattern:
    """A stand-in whose ``fullmatch`` raises, to prove a pattern is never run."""

    def fullmatch(self, string: str) -> object:
        msg = f"a reduced-form pattern must not run for {string!r}"
        raise AssertionError(msg)
