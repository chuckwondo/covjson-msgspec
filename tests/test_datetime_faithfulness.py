"""Byte-faithful datetime/calendar handling.

The core model never parses temporal values into Python ``datetime``: a time
axis keeps its raw strings (``AxisValue`` admits ``str`` but never
``datetime``), and ``TemporalRS.calendar`` is carried verbatim, never
interpreted. So a document survives a decode -> encode round trip without ever
mangling a timestamp: ``Z`` is not rewritten to ``+00:00`` (nor the reverse),
fractional seconds are not truncated, and dates outside numpy's
``datetime64[ns]`` window (~1678-2262) or on a non-Gregorian calendar pass
through untouched.

This deliberately avoids the covjson-pydantic bug class, where time values
typed as ``datetime`` get normalized on re-serialization. Datetime conversion
lives only in the opt-in, one-way export bridges (the pandas/xarray bridges),
never in the model itself.

See ``covjson_msgspec.axis.AxisValue`` and
``covjson_msgspec.referencing.TemporalRS``.
"""

import datetime

import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    Domain,
    NdArray,
    ReferenceSystemConnection,
    TemporalRS,
    decode_coverage,
    encode,
)

# Spec-valid ISO 8601 instants that a datetime round trip would silently alter.
# Mixing trailing ``Z`` with explicit ``+00:00`` in one axis proves neither is
# normalized to the other.
AWKWARD_TIMES: tuple[str, ...] = (
    "2020-01-01T00:00:00Z",  # trailing Z
    "2020-06-01T12:30:00+00:00",  # explicit +00:00 offset
    "2020-06-01T12:30:00.123456Z",  # sub-second precision
    "0001-01-01T00:00:00Z",  # year 1: far below datetime64[ns]'s ~1678 floor
    "2300-07-15T00:00:00Z",  # far future: above datetime64[ns]'s ~2262 ceiling
)

# Non-Gregorian calendar strings the model must carry verbatim, never validating
# or interpreting them. Two flavors prove the opacity is not URI-specific: the
# spec's "Gregorian or a URI" form, and a bare CF calendar name (which the spec
# does not sanction but the model still passes through untouched).
NON_GREGORIAN_CALENDAR = "http://example.com/calendars/non-gregorian"
CF_CALENDAR_NAME = "360_day"


def _series(calendar: str = NON_GREGORIAN_CALENDAR) -> Coverage:
    """A PointSeries coverage whose time axis lists the awkward instants."""
    domain = Domain.point_series(
        x=Axis.listed((1.0,)),
        y=Axis.listed((2.0,)),
        t=Axis.listed(AWKWARD_TIMES),
        referencing=[
            ReferenceSystemConnection(
                coordinates=("t",),
                system=TemporalRS(calendar=calendar),
            )
        ],
    )
    values = tuple(float(i) for i in range(len(AWKWARD_TIMES)))
    return Coverage(
        domain=domain,
        ranges={
            "v": NdArray(
                data_type="float",
                axis_names=("t",),
                shape=(len(values),),
                values=values,
            )
        },
    )


def test_decode_keeps_time_values_as_raw_strings() -> None:
    """Decoding never parses a temporal value: each stays the exact wire string."""
    blob = encode(_series())
    cov = decode_coverage(blob)

    assert isinstance(cov.domain, Domain)
    t_axis = cov.domain.axes["t"]
    assert t_axis.values == AWKWARD_TIMES


def test_encode_emits_time_values_verbatim() -> None:
    """Each awkward instant appears unchanged in the encoded bytes."""
    out = encode(_series())

    for moment in AWKWARD_TIMES:
        # No Z<->+00:00 rewrite, no fractional-second truncation, no clamping of
        # out-of-datetime64-range years.
        assert moment.encode() in out


@pytest.mark.parametrize("calendar", [NON_GREGORIAN_CALENDAR, CF_CALENDAR_NAME])
def test_non_gregorian_calendar_is_carried_verbatim(calendar: str) -> None:
    """``TemporalRS.calendar`` survives a round trip unparsed and unvalidated.

    The opacity holds for both the spec's URI form and a bare CF calendar name.
    """
    cov = decode_coverage(encode(_series(calendar)))

    assert isinstance(cov.domain, Domain)
    (rsc,) = cov.domain.referencing
    assert isinstance(rsc.system, TemporalRS)
    assert rsc.system.calendar == calendar
    assert calendar.encode() in encode(cov)


def test_decode_encode_is_idempotent_on_times() -> None:
    """Re-decoding the encoded output yields byte-identical output (stable)."""
    once = encode(decode_coverage(encode(_series())))
    twice = encode(decode_coverage(once))

    assert once == twice


def test_raw_blob_with_mixed_offsets_roundtrips_unchanged() -> None:
    """A hand-written document keeps both ``Z`` and ``+00:00`` distinct."""
    blob = (
        b'{"type":"Coverage",'
        b'"domain":{"type":"Domain","domainType":"PointSeries","axes":{'
        b'"x":{"values":[1.0]},"y":{"values":[2.0]},'
        b'"t":{"values":["2020-01-01T00:00:00Z","2020-01-01T00:00:00+00:00"]}}},'
        b'"ranges":{"v":{"type":"NdArray","dataType":"float",'
        b'"axisNames":["t"],"shape":[2],"values":[0.0,1.0]}}}'
    )
    cov = decode_coverage(blob)

    assert isinstance(cov.domain, Domain)
    assert cov.domain.axes["t"].values == (
        "2020-01-01T00:00:00Z",
        "2020-01-01T00:00:00+00:00",
    )
    out = encode(cov)
    assert b'"2020-01-01T00:00:00Z"' in out
    assert b'"2020-01-01T00:00:00+00:00"' in out


def test_decoded_time_values_are_plain_str() -> None:
    """The contract behind the rest: decoded instants are ``str``, not datetime."""
    cov = decode_coverage(encode(_series()))

    assert isinstance(cov.domain, Domain)
    values = cov.domain.axes["t"].values
    assert values is not None
    assert all(isinstance(value, str) for value in values)
    assert not any(isinstance(value, datetime.datetime) for value in values)
