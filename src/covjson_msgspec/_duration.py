"""ISO 8601 duration strings for NumPy ``timedelta64`` values.

CoverageJSON has no duration type: a range's ``dataType`` is one of ``"float"``,
``"integer"``, or ``"string"``, and an axis value is a number or a string. A
duration therefore crosses the wire as a string, and the standard spelling for
one is an ISO 8601 duration (``"P1D"``, ``"PT6H"``). This module is the single
home for that conversion, shared by the NumPy and xarray bridges so that a
duration is written the same way wherever it appears in a document. See
ADR-0021.

The unit the source array declares is preserved rather than normalized to
seconds, so ``timedelta64[Y]`` becomes ``"P1Y"`` rather than a fixed count of
days. NumPy itself refuses to convert years and months to seconds, for the same
reason: neither is a fixed span.

Spec: [NdArray objects][spec-ndarray] and [Axis objects][spec-axis].

[spec-ndarray]: https://github.com/covjson/specification/blob/master/spec.md#62-ndarray-objects
[spec-axis]: https://github.com/covjson/specification/blob/master/spec.md#611-axis-objects
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import numpy.typing as npt

# The ISO 8601 form for each NumPy duration unit that has its own designator. A
# date component takes a bare ``P``, a clock component ``PT``.
_ISO_FORMS: Final[Mapping[str, str]] = {
    "Y": "P{}Y",
    "M": "P{}M",
    "W": "P{}W",
    "D": "P{}D",
    "h": "PT{}H",
    "m": "PT{}M",
    "s": "PT{}S",
}

# ISO 8601 has no sub-second designator, so the finer NumPy units are written as
# a fractional number of seconds; the value is how many decimal places that
# unit needs.
_ISO_SUBSECOND_DIGITS: Final[Mapping[str, int]] = {
    "ms": 3,
    "us": 6,
    "ns": 9,
    "ps": 12,
    "fs": 15,
    "as": 18,
}


def to_iso_durations(array: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Convert a ``timedelta64`` array to ISO 8601 duration strings.

    The dtype supplies both the unit and its multiple, so a ``timedelta64[15m]``
    count of 1 is fifteen minutes rather than one of anything.

    Parameters
    ----------
    array
        A 1-D array whose dtype is some ``timedelta64`` unit.

    Returns
    -------
    numpy.ndarray
        A string array of the same length, one ISO 8601 duration per element.

    Raises
    ------
    ValueError
        If the dtype carries no unit (an unsized ``timedelta64``), leaving the
        counts with nothing to be counts of.

    Examples
    --------
    >>> import numpy as np
    >>> to_iso_durations(np.array([1, 2], dtype="timedelta64[D]"))
    array(['P1D', 'P2D'], dtype='<U3')

    The dtype's multiple is folded into the value:

    >>> to_iso_durations(np.array([1], dtype="timedelta64[15m]"))
    array(['PT15M'], dtype='<U5')

    An unsized dtype has no duration form:

    >>> to_iso_durations(np.array([1], dtype="timedelta64"))
    Traceback (most recent call last):
        ...
    ValueError: a timedelta64 with unit 'generic' has no ISO 8601 duration
    form; give the array a unit, e.g. array.astype("timedelta64[s]")
    """
    import numpy as np

    unit, step = np.datetime_data(array.dtype)

    if unit not in _ISO_FORMS and unit not in _ISO_SUBSECOND_DIGITS:
        msg = (
            f"a timedelta64 with unit {unit!r} has no ISO 8601 duration form; "
            'give the array a unit, e.g. array.astype("timedelta64[s]")'
        )
        raise ValueError(msg)

    # Scaling by ``step`` happens on the Python int, not on the array: NaT is
    # int64's minimum, which a multiplying dtype like timedelta64[15m] would
    # overflow. The nonsense duration NaT formats to is harmless because every
    # caller marks missing entries separately, while the array is still
    # timedelta64 and numpy.isfinite can still see them.
    return np.array(
        [_iso_duration(int(count) * step, unit) for count in array.astype("int64")],
        dtype=np.str_,
    )


def _iso_duration(count: int, unit: str) -> str:
    """Format a count of some NumPy duration ``unit`` as an ISO 8601 duration.

    Parameters
    ----------
    count
        How many of ``unit`` the duration spans; may be negative or zero.
    unit
        A NumPy duration unit code (``"Y"``, ``"D"``, ``"h"``, ``"ns"``, ...).

    Returns
    -------
    str
        The ISO 8601 duration.

    Examples
    --------
    >>> _iso_duration(1, "D")
    'P1D'
    >>> _iso_duration(6, "h")
    'PT6H'

    The sign leads the whole duration, which is where msgspec puts it too, and
    where ``P-3D`` would be unparseable:

    >>> _iso_duration(-3, "D")
    '-P3D'

    A sub-second unit becomes a fraction of a second, trailing zeros trimmed:

    >>> _iso_duration(1500, "ns")
    'PT0.0000015S'
    >>> _iso_duration(1_000_000_000, "ns")
    'PT1S'
    """
    sign = "-" if count < 0 else ""
    magnitude = abs(count)

    if (form := _ISO_FORMS.get(unit)) is not None:
        return sign + form.format(magnitude)

    digits = _ISO_SUBSECOND_DIGITS[unit]
    whole, fraction = divmod(magnitude, 10**digits)
    seconds = f"{whole}.{fraction:0{digits}d}".rstrip("0").rstrip(".")

    return f"{sign}PT{seconds}S"
