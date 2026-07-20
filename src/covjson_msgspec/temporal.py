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
        offset (i.e., [`SECOND`][covjson_msgspec.temporal.Precision]); the date and
        reduced forms are naive.
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
# ``±hh:mm`` offset). Digits use ``[0-9]`` to stay ASCII only, as ISO 8601
# requires (bare ``\d`` would also match non-ASCII digits, e.g., Arabic-Indic).
# ``[0-9]`` is preferred over the equivalent ``\d`` + ``re.ASCII`` for speed: it
# is measurably faster, a range op rather than a Unicode-category lookup.
# Compiled once at import.
_YEAR = re.compile(r"[0-9]{4}")
_EXPANDED_YEAR = re.compile(r"[+-][0-9]{5,}")
_YEAR_MONTH = re.compile(r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:(?P<sec>[0-9]{2})"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
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

    Consumers match on the concrete type, with [`assert_never`][typing.assert_never]
    making the match exhaustive:

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
    # Fast path: only the datetime form carries a ``T`` (uppercase, as
    # ``_DATETIME`` requires), so ``msgspec.convert`` (the native C RFC 3339
    # decoder msgspec already ships) settles it in one call, several times
    # faster than the pure-Python regex + ``fromisoformat``. It is more lenient
    # than the spec form: the rare inputs it rejects (a leap second, year 0000,
    # a malformed ``T``-string) fall back to `_resolve_datetime_form`, and the
    # ones it wrongly accepts (a naive time, a lowercase ``z``, a colon-less
    # ``+0500`` offset) are rejected by `_has_spec_timezone`. One benign
    # difference from the old ``fromisoformat`` path: a sub-microsecond fractional
    # second (seven or more digits) rounds here rather than truncating, precision
    # a ``datetime`` cannot hold either way.
    if "T" in value:
        try:
            parsed = msgspec.convert(value, datetime)
        except msgspec.ValidationError:
            return _resolve_datetime_form(value)

        return (
            Moment(parsed, Precision.SECOND)
            if _has_spec_timezone(value)
            else Malformed(value)
        )

    # The reduced forms, tried cheapest-first. A ``T``-less string can never
    # match ``_DATETIME``, so that arm is not retried here.
    if _YEAR.fullmatch(value) or _EXPANDED_YEAR.fullmatch(value):
        return (
            Moment(datetime(year, 1, 1), Precision.YEAR)
            if 1 <= (year := int(value)) <= 9999
            else Unrepresentable(value)
        )

    if (m := _YEAR_MONTH.fullmatch(value)) is not None:
        if not 1 <= (month := int(m["month"])) <= 12:
            return Malformed(value)

        return (
            Unrepresentable(value)
            if (year := int(m["year"])) == 0
            else Moment(datetime(year, month, 1), Precision.MONTH)
        )

    if _DATE.fullmatch(value) is not None:
        return _from_isoformat(value, Precision.DAY)

    return Malformed(value)


def to_datetime(value: str) -> datetime | None:
    """Convert a CoverageJSON temporal string to a `datetime`, or ``None``.

    The thin convenience over `resolve` for the common case: it returns the
    moment for a representable value and ``None`` for anything else (an
    `Unrepresentable` valid form or a `Malformed` string). A caller that needs to
    tell those apart, or wants the detected `Precision`, uses `resolve` instead.

    This is also the faithful path for a ``±hh:mm`` offset: the result keeps the
    offset (it is timezone-aware), whereas the export bridges (`to_xarray`,
    `to_pandas`) flatten a standard-calendar offset to naive-UTC. So when the zone
    matters, resolve the (Gregorian) axis values with this rather than reading the
    tz off a bridge's output. This parses the Gregorian forms only, so it does not
    serve a non-standard calendar (its cftime path handles those).

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

    An offset is preserved, so a whole axis keeps its zone where a bridge would
    flatten it:

    >>> [to_datetime(v).isoformat() for v in ("2020-01-15T00:00:00+05:00",
    ...                                       "2020-01-15T00:00:00-08:00")]
    ['2020-01-15T00:00:00+05:00', '2020-01-15T00:00:00-08:00']
    """
    result = resolve(value)

    return result.when if isinstance(result, Moment) else None


def _from_isoformat(value: str, precision: Precision) -> TemporalResult:
    """Resolve a full date or datetime via `datetime.fromisoformat`.

    Used for the ``YYYY-MM-DD`` and ``YYYY-MM-DDThh:mm:ssZ`` forms, whose plain
    four-digit year lets ``fromisoformat`` do the month / day / leap-year / offset
    validation. ``fromisoformat`` rejects year ``0000`` outright, so a ``"0000"``
    value is validated under a substituted in-range leap year (``2000``, which
    shares year 0's proleptic-Gregorian leap status, so ``"0000-02-29"`` stays
    valid): a well-formed year-0000 value is then `Unrepresentable` (a valid form
    a `datetime` cannot hold), while a malformed one (an out-of-range month or
    day) is `Malformed`, exactly as for any other year. Any other parse failure
    is likewise `Malformed`.

    Examples
    --------
    >>> _from_isoformat("2020-01-01", Precision.DAY)
    Moment(when=datetime.datetime(2020, 1, 1, 0, 0), precision=<Precision.DAY: 'day'>)
    >>> _from_isoformat("2020-02-30", Precision.DAY)
    Malformed(value='2020-02-30')

    A year-0000 value splits on well-formedness: a real date is unrepresentable,
    an invalid month or day is malformed (not silently unrepresentable):

    >>> _from_isoformat("0000-06-15", Precision.DAY)
    Unrepresentable(value='0000-06-15')
    >>> _from_isoformat("0000-13-01", Precision.DAY)
    Malformed(value='0000-13-01')
    """
    is_year_zero = value.startswith("0000")
    probe = f"2000{value[4:]}" if is_year_zero else value

    try:
        parsed = datetime.fromisoformat(probe)
    except ValueError:
        return Malformed(value)

    return Unrepresentable(value) if is_year_zero else Moment(parsed, precision)


def _resolve_datetime_form(value: str) -> TemporalResult:
    """Classify a datetime-form string with the ``_DATETIME`` regex.

    The pure-Python classifier for the ``YYYY-MM-DDThh:mm:ss`` form, kept as the
    fallback for the rare inputs `resolve`'s fast path routes here: those
    ``msgspec.convert`` rejects. A leap second is a valid form a ``datetime``
    cannot hold, so it is `Unrepresentable`; anything the pattern does not match,
    or that ``fromisoformat`` then rejects (an out-of-range month, say), is
    `Malformed`. Named for its mechanism, not its strictness: the fast path
    enforces the identical spec form, so this is not the "strict" one.

    Examples
    --------
    >>> _resolve_datetime_form("2016-12-31T23:59:60Z")
    Unrepresentable(value='2016-12-31T23:59:60Z')
    >>> _resolve_datetime_form("2020-13-01T00:00:00Z")
    Malformed(value='2020-13-01T00:00:00Z')
    """
    if (m := _DATETIME.fullmatch(value)) is not None:
        # A leap second is a valid form ``datetime`` rejects; seconds above 60
        # fail ``fromisoformat`` and fall through to `Malformed`.
        return (
            Unrepresentable(value)
            if m["sec"] == "60"
            else _from_isoformat(value, Precision.SECOND)
        )

    return Malformed(value)


def _has_spec_timezone(value: str) -> bool:
    """Whether ``value`` ends with a spec-conformant time zone designator.

    Spec 5.2 writes the datetime form ending in ``Z`` or a ``±hh:mm`` offset (the
    colon is part of the form). This mirrors the trailing
    ``(?:Z|[+-][0-9]{2}:[0-9]{2})`` of the `_DATETIME` pattern (one rule with two
    homes, kept in agreement by `resolve`'s fuzz differential test), as a cheap
    check for the fast path, which must reject what ``msgspec.convert`` accepts
    but the spec does not: a naive time (no designator), a lowercase ``z``, and a
    colon-less offset such as ``+0500``. It runs only after ``convert`` has
    parsed a valid datetime, so the offset alternative just confirms the colon
    form: a ``±`` six from the end and a ``:`` three from the end (``+0500`` has
    neither, its ``+`` sitting five from the end).

    Examples
    --------
    >>> _has_spec_timezone("2020-01-01T00:00:00Z")
    True
    >>> _has_spec_timezone("2020-01-01T00:00:00+05:00")
    True
    >>> _has_spec_timezone("2020-01-01T00:00:00+0500")  # colon-less offset
    False
    >>> _has_spec_timezone("2020-01-01T00:00:00")  # naive
    False
    """
    return value.endswith("Z") or (
        len(value) >= 6 and value[-6] in "+-" and value[-3] == ":"
    )
