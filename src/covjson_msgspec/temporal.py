"""Bridge-independent conversion of CoverageJSON temporal strings to ``datetime``.

CoverageJSON stores temporal coordinate values as raw ISO 8601 strings, and the
model never parses them (decode is byte-faithful). This module is the opt-in,
stdlib-only projection over that faithful core: it interprets a temporal string
against the Gregorian calendar's recommended lexical forms and returns a typed
`TemporalResult`, degrading gracefully for the values a `datetime` cannot hold.
It pulls in no optional dependency (no numpy / pandas / cftime): just `datetime`
and `re`.

Spec 5.2 (Temporal Reference Systems) says a value whose calendar is based on
years / months / days SHOULD use one of five ISO 8601 lexical forms:

- ``YYYY``: a year (e.g., ``"2018"``).
- ``±XYYYY``: an ISO 8601 expanded year (a sign then five or more digits, e.g.,
  ``"+102018"``), for years outside the four-digit range.
- ``YYYY-MM``: a year and month.
- ``YYYY-MM-DD``: a complete date.
- ``YYYY-MM-DDThh:mm:ss[.f]Z``: a date and time; the trailing ``Z`` (or a
  ``±hh:mm`` offset) is required, so a naive time is not one of the forms.

`resolve` classifies a string into one of three outcomes:

- `Moment`: a valid form representable as a `datetime`, filled to the start of
  the period for the reduced forms, with the detected `Precision` recorded. It is
  timezone-aware exactly when it carries a ``Z`` / offset (i.e., second precision);
  the date and reduced forms are naive, as they carry no zone to attach.
- `Unrepresentable`: a valid form a stdlib `datetime` cannot hold, namely a year
  outside ``1..9999`` (an expanded year, or ``"0000"``), or a leap second
  (``":60"``). The raw string is preserved; cftime or numpy ``datetime64[s]`` are
  the escape hatch for these.
- `Malformed`: a string matching none of the five forms.

`to_datetime` is the thin convenience for the common "just give me a datetime"
case; `resolve` is the full, information-preserving result. Both are pure
functions of the string alone: a ``timeScale`` (e.g., TAI) is not interpreted, and
the stored value is never mutated.

Spec: [CoverageJSON](https://github.com/covjson/specification/blob/master/spec.md)
(section 5.2, Temporal Reference Systems, defines the five Gregorian forms).
"""

from __future__ import annotations

import enum
import re
from datetime import datetime

import msgspec


class Precision(enum.StrEnum):
    """How much of a `Moment` its source string actually specified.

    The reduced forms are filled to the start of the period, so ``"2020"`` and
    ``"2020-01-01T00:00:00Z"`` can both yield a January 1st `datetime`; this
    records which one the value really pinned down.
    """

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    SECOND = "second"


class Moment(msgspec.Struct, frozen=True):
    """A temporal string that resolved to a representable `datetime`.

    Attributes
    ----------
    when
        The point in time, filled to the start of the period for the reduced
        forms. It is timezone-aware exactly when the source carried a ``Z`` /
        offset (i.e., `~Precision.SECOND`); the date and reduced forms are naive.
    precision
        How much of ``when`` the source string actually specified.
    """

    when: datetime
    precision: Precision


class Unrepresentable(msgspec.Struct, frozen=True):
    """A valid spec form that a stdlib `datetime` cannot represent.

    Either the year is outside `datetime`'s ``1..9999`` range (an expanded year
    like ``"+102020"``, or ``"0000"``) or the value names a leap second
    (``":60"``). The raw string is preserved verbatim so a caller can round-trip
    it or hand it to cftime / numpy ``datetime64[s]``.
    """

    value: str


class Malformed(msgspec.Struct, frozen=True):
    """A string matching none of the five spec forms (e.g., ``"2010-13-99"``)."""

    value: str


# The result of `resolve`: a closed union matched by concrete type (`match` /
# `assert_never` for exhaustiveness, or `isinstance` to read a variant's payload).
# It never crosses the CoverageJSON wire, so the arms are plain frozen structs
# with no tag.
TemporalResult = Moment | Unrepresentable | Malformed


# One anchored pattern per form family (the ``±XYYYY`` expanded year requires a
# sign then five or more digits; the datetime form requires a trailing ``Z`` or
# ``±hh:mm`` offset). ``re.ASCII`` keeps ``\d`` to ASCII 0-9, since ISO 8601 uses
# ASCII digits only (otherwise ``\d`` and ``int`` would accept non-ASCII digits,
# e.g., Arabic-Indic). Compiled once at import.
_YEAR = re.compile(r"\d{4}", re.ASCII)
_EXPANDED_YEAR = re.compile(r"[+-]\d{5,}", re.ASCII)
_YEAR_MONTH = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})", re.ASCII)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:(?P<sec>\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
    re.ASCII,
)


def resolve(value: str) -> TemporalResult:
    """Classify a CoverageJSON temporal string against the five Gregorian forms.

    Parameters
    ----------
    value
        A temporal coordinate value (a raw ISO 8601 string from a time axis).

    Returns
    -------
    TemporalResult
        `Moment` for a representable form, `Unrepresentable` for a valid form a
        `datetime` cannot hold, or `Malformed` for a string matching no form.

    Examples
    --------
    Full and reduced forms resolve to a filled `Moment`, the reduced ones
    carrying a coarser `Precision`:

    >>> resolve("2020-06") == Moment(datetime(2020, 6, 1), Precision.MONTH)
    True
    >>> resolve("2020-01-01T00:00:00Z").precision
    <Precision.SECOND: 'second'>

    A value is timezone-aware exactly when it is second precision:

    >>> resolve("2020-01-01T00:00:00Z").when.tzinfo is not None
    True
    >>> resolve("2020-01-01").when.tzinfo is None
    True

    Expanded and zero years, and leap seconds, are valid forms a `datetime`
    cannot hold:

    >>> resolve("+102020")
    Unrepresentable(value='+102020')
    >>> resolve("2016-12-31T23:59:60Z")
    Unrepresentable(value='2016-12-31T23:59:60Z')

    A naive time (no ``Z`` / offset) is not one of the forms, so it is malformed:

    >>> resolve("2020-01-01T00:00:00")
    Malformed(value='2020-01-01T00:00:00')
    >>> resolve("2010-13-99")
    Malformed(value='2010-13-99')

    Consumers match on the concrete type, with `~typing.assert_never` making the
    match exhaustive:

    >>> from typing import assert_never
    >>> def describe(value: str) -> str:
    ...     match resolve(value):
    ...         case Moment(precision=p):
    ...             return f"moment ({p.value})"
    ...         case Unrepresentable():
    ...             return "unrepresentable"
    ...         case Malformed():
    ...             return "malformed"
    ...         case other:
    ...             assert_never(other)
    >>> describe("2020"), describe("+102020"), describe("nope")
    ('moment (year)', 'unrepresentable', 'malformed')
    """
    if _YEAR.fullmatch(value) or _EXPANDED_YEAR.fullmatch(value):
        year = int(value)

        return (
            Moment(datetime(year, 1, 1), Precision.YEAR)
            if 1 <= year <= 9999
            else Unrepresentable(value)
        )

    if (m := _YEAR_MONTH.fullmatch(value)) is not None:
        month = int(m["month"])

        if not 1 <= month <= 12:
            return Malformed(value)

        year = int(m["year"])

        return (
            Unrepresentable(value)
            if year == 0
            else Moment(datetime(year, month, 1), Precision.MONTH)
        )

    if _DATE.fullmatch(value) is not None:
        return _from_isoformat(value, Precision.DAY)

    if (m := _DATETIME.fullmatch(value)) is not None:
        # A leap second is a valid form ``datetime`` rejects; seconds above 60
        # fail ``fromisoformat`` below and fall through to `Malformed`.
        return (
            Unrepresentable(value)
            if m["sec"] == "60"
            else _from_isoformat(value, Precision.SECOND)
        )

    return Malformed(value)


def to_datetime(value: str) -> datetime | None:
    """Convert a CoverageJSON temporal string to a `datetime`, or ``None``.

    The thin convenience over `resolve` for the common case: it returns the
    moment for a representable value and ``None`` for anything else (an
    `Unrepresentable` valid form or a `Malformed` string). A caller that needs to
    tell those apart, or wants the detected `Precision`, uses `resolve` instead.

    Parameters
    ----------
    value
        A temporal coordinate value.

    Returns
    -------
    datetime or None
        The filled moment (timezone-aware iff the value carried a ``Z`` /
        offset), or ``None`` when the value is not representable.

    Examples
    --------
    >>> to_datetime("2020-01-01T00:00:00Z").isoformat()
    '2020-01-01T00:00:00+00:00'
    >>> to_datetime("2020-06").isoformat()
    '2020-06-01T00:00:00'
    >>> to_datetime("+102020") is None
    True
    >>> to_datetime("2010-13-99") is None
    True
    """
    result = resolve(value)

    return result.when if isinstance(result, Moment) else None


def _from_isoformat(value: str, precision: Precision) -> TemporalResult:
    """Resolve a full date or datetime via `datetime.fromisoformat`.

    Used for the ``YYYY-MM-DD`` and ``YYYY-MM-DDThh:mm:ssZ`` forms, whose plain
    four-digit year lets ``fromisoformat`` do the month / day / leap-year / offset
    validation. A ``"0000"`` year is pre-empted to `Unrepresentable`
    (``fromisoformat`` raises on year 0); any other parse failure is a
    `Malformed` value.

    Examples
    --------
    >>> _from_isoformat("2020-01-01", Precision.DAY)
    Moment(when=datetime.datetime(2020, 1, 1, 0, 0), precision=<Precision.DAY: 'day'>)
    >>> _from_isoformat("2020-02-30", Precision.DAY)
    Malformed(value='2020-02-30')
    """
    if value.startswith("0000"):
        return Unrepresentable(value)

    try:
        return Moment(datetime.fromisoformat(value), precision)
    except ValueError:
        return Malformed(value)
