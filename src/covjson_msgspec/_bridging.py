"""Internals shared by the dataframe/array bridges (pandas, geopandas, xarray).

These helpers and constants are deliberately bridge-agnostic so the three
bridges stay consistent: they all reject the same polygon domain types, parse
the same standard calendars, guard ranges with the same message, and lay axis
data over a domain's grid with the same broadcasting rules. numpy / pandas are
imported lazily inside the helpers, so importing this module never pulls in an
optional dependency.

A few helpers here read a spec-defined fact off the model rather than serve a
bridge specifically (`coordinate_systems`, `temporal_coordinates`,
`coordinate_identifiers`); [`validation`][covjson_msgspec.validation] shares those,
so that each fact has one home rather than one per consumer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec.axis import Axis
from covjson_msgspec.coverage import Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import ResolvedReferenceSystem, TemporalRS

if TYPE_CHECKING:
    import numpy.typing as npt

# Polygon domains carry vector geometry, not a tidy table or a regular grid, so
# only the geopandas bridge handles them; pandas and xarray reject them.
POLYGON_DOMAIN_TYPES = frozenset(
    {"Polygon", "PolygonSeries", "MultiPolygon", "MultiPolygonSeries"}
)

# Calendars whose dates pandas / numpy can parse to datetime64; anything else
# stays as ISO strings (pandas) or needs cftime (xarray).
STANDARD_CALENDARS = frozenset({"gregorian", "standard", "proleptic_gregorian"})


def require_inline_ndarray(key: str, range_: Range, target: str) -> NdArray:
    """Return ``range_`` narrowed to `NdArray`, or raise a uniform `ValueError`.

    The bridges can only read inline values, so a URL-reference or
    `TiledNdArray` range cannot be converted. Centralizing the check here keeps
    the message identical across the three bridges and narrows the static type
    from `Range` to `NdArray` for the caller.

    Parameters
    ----------
    key
        The parameter/range key, quoted into the error message so the caller can
        tell which range failed.
    range_
        The range to narrow; any `Range` member is accepted.
    target
        The destination bridge name (``"pandas"`` / ``"geopandas"`` /
        ``"xarray"``), interpolated into the message.

    Returns
    -------
    NdArray
        ``range_`` unchanged, statically narrowed to `NdArray`.

    Raises
    ------
    ValueError
        If ``range_`` is not an inline `NdArray` (e.g. a `TiledNdArray` or a
        URL-string reference).

    Examples
    --------
    >>> arr = NdArray(data_type="float", values=(1.0,), shape=(1,), axis_names=("x",))
    >>> require_inline_ndarray("temperature", arr, "pandas") is arr
    True

    A tiled range cannot be read inline, so it is rejected with a message that
    names the key and the target bridge:

    >>> from covjson_msgspec.range import TileSet, TiledNdArray
    >>> tiled = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("x",),
    ...     shape=(2,),
    ...     tile_sets=(TileSet(tile_shape=(1,), url_template="t/{x}"),),
    ... )
    >>> require_inline_ndarray("temperature", tiled, "pandas")
    Traceback (most recent call last):
        ...
    ValueError: range 'temperature' is not an inline NdArray (got TiledNdArray);
    resolve URL ranges and assemble TiledNdArray tiles before converting to pandas
    """

    if not isinstance(range_, NdArray):
        msg = (
            f"range {key!r} is not an inline NdArray (got "
            f"{type(range_).__name__}); resolve URL ranges and assemble "
            f"TiledNdArray tiles before converting to {target}"
        )
        raise ValueError(msg)

    return range_


def range_column(
    range_: NdArray,
    dims: list[str],
    sizes: dict[str, int],
) -> npt.NDArray[Any]:
    """Lay a range's values over the canonical ``dims`` grid as a flat column.

    A coverage's ranges may each vary over a different subset of the domain's
    axes, in any order; a dataframe column, though, has to be a single flat
    sequence aligned to one shared coordinate grid. This reorders the range onto
    the canonical ``dims`` order, then broadcasts it up to the full grid so every
    range yields a column of the same length, ready to drop into a frame.

    Parameters
    ----------
    range_
        The inline range whose [`axis_names`][covjson_msgspec.NdArray.axis_names] say
        which dims it varies over (a subset of ``dims``, possibly in a different order).
    dims
        The canonical dimension order shared by every column in the frame.
    sizes
        The length of each dim in ``dims``.

    Returns
    -------
    numpy.ndarray
        A 1-D column of length ``prod(sizes[d] for d in dims)``, raveled in
        row-major (C) order to match `pandas.MultiIndex.from_product`. An
        integer range with masked entries is cast to ``float64`` with NaN gaps,
        since pandas has no general masked-integer column.

    Examples
    --------
    A range that varies over only ``y`` (not ``x``) is broadcast across ``x`` so
    its two values each repeat three times down the ``("y", "x")`` grid:

    >>> partial = NdArray(
    ...     data_type="float", values=(10.0, 20.0), shape=(2,), axis_names=("y",)
    ... )
    >>> range_column(partial, ["y", "x"], {"y": 2, "x": 3}).tolist()
    [10.0, 10.0, 10.0, 20.0, 20.0, 20.0]

    A range stored in ``("x", "y")`` order is transposed onto the canonical
    ``("y", "x")`` order before raveling:

    >>> swapped = NdArray(
    ...     data_type="float",
    ...     values=(1.0, 2.0, 3.0, 4.0),
    ...     shape=(2, 2),
    ...     axis_names=("x", "y"),
    ... )
    >>> range_column(swapped, ["y", "x"], {"y": 2, "x": 2}).tolist()
    [1.0, 3.0, 2.0, 4.0]
    """

    import numpy as np

    array = range_.to_numpy()

    if isinstance(array, np.ma.MaskedArray):
        # pandas has no general masked integer column, so a masked entry becomes
        # NaN; cast to float first since NaN cannot live in an integer array.
        array = np.ma.filled(array.astype(np.float64), np.nan)

    # Transpose the range's own axis order onto the canonical dim order, pushing
    # any axes that are not dims (single-valued, size 1) to the back.
    present = [dim for dim in dims if dim in range_.axis_names]
    rest = [index for index, name in enumerate(range_.axis_names) if name not in dims]
    order = [range_.axis_names.index(dim) for dim in present] + rest

    return broadcast(np.transpose(array, order), present, dims, sizes)


def broadcast(
    data: Any,
    present: tuple[str, ...] | list[str],
    dims: list[str],
    sizes: dict[str, int],
) -> npt.NDArray[Any]:
    """Broadcast ``data`` (varying only over ``present``) to the full ``dims`` grid.

    A lower-level step of `range_column`: it assumes ``data`` is already in
    canonical (``dims``) order over its ``present`` axes, with any absent axes
    represented by trailing size-1 dimensions. Inserting a length-1 axis for
    each absent dim and broadcasting to the full sizes repeats the data along
    the missing axes without copying until the final [`ravel`][numpy.ndarray.ravel].

    Parameters
    ----------
    data
        Array-like whose axes correspond, in order, to ``present`` followed by
        any size-1 trailing axes.
    present
        The dims that ``data`` actually varies over, in canonical order.
    dims
        The full canonical dimension order to broadcast up to.
    sizes
        The length of each dim in ``dims``.

    Returns
    -------
    numpy.ndarray
        A 1-D array of length ``prod(sizes[d] for d in dims)`` in row-major (C)
        order, or a length-1 array when ``dims`` is empty (a scalar range).

    Examples
    --------
    A single value spread over a 2 x 2 grid it does not vary across:

    >>> broadcast(5.0, [], ["y", "x"], {"y": 2, "x": 2}).tolist()
    [5.0, 5.0, 5.0, 5.0]

    Data varying only over ``x`` is repeated down ``y``:

    >>> broadcast([1.0, 2.0], ["x"], ["y", "x"], {"y": 2, "x": 2}).tolist()
    [1.0, 2.0, 1.0, 2.0]
    """

    import numpy as np

    array = np.asarray(data)

    if not dims:
        return array.reshape(1)

    # ``data`` is in canonical order over ``present`` with any trailing size-1
    # axes, so reshaping to the broadcast shape (1 where an axis is absent)
    # preserves element order.
    shape = tuple(sizes[dim] if dim in present else 1 for dim in dims)
    full = tuple(sizes[dim] for dim in dims)

    return np.broadcast_to(array.reshape(shape), full).ravel()


def is_standard_calendar(rs: TemporalRS) -> bool:
    """Whether a temporal system's calendar is one that maps to real datetimes.

    The calendar is matched on its final path segment, lower-cased, against
    `STANDARD_CALENDARS`, so both a bare ``"Gregorian"`` and a URI like
    ``".../calendars/Gregorian"`` are recognized. A non-standard calendar (e.g.
    ``"360_day"``) has no ``datetime64`` representation, and the stdlib
    [`resolve`][covjson_msgspec.temporal.resolve] understands only the Gregorian forms,
    so callers leave those coordinates as ISO strings and skip datetime comparison.

    Parameters
    ----------
    rs
        The temporal reference system whose ``calendar`` is tested.

    Returns
    -------
    bool
        ``True`` when the calendar's final path segment (lower-cased) is one of
        `STANDARD_CALENDARS`.

    Examples
    --------
    >>> from covjson_msgspec.referencing import TemporalRS
    >>> is_standard_calendar(TemporalRS(calendar="Gregorian"))
    True
    >>> is_standard_calendar(
    ...     TemporalRS(calendar="http://example.org/calendars/Gregorian")
    ... )
    True
    >>> is_standard_calendar(TemporalRS(calendar="360_day"))
    False
    """
    return rs.calendar.rsplit("/", 1)[-1].lower() in STANDARD_CALENDARS


def coordinate_systems(domain: Domain) -> dict[str, ResolvedReferenceSystem]:
    """Index a domain's referencing by coordinate identifier.

    Flattens the domain's reference-system connections (each of which ties one or
    more coordinates to a system) into a flat ``coordinate -> system`` lookup, so
    a caller can ask "which system governs ``t``?" in O(1). Each system is
    projected to its typed
    [`ResolvedReferenceSystem`][covjson_msgspec.ResolvedReferenceSystem] variant (via
    [`refine`][covjson_msgspec.ReferenceSystem.refine]), so callers dispatch on
    a precise kind.

    Parameters
    ----------
    domain
        The domain whose [`referencing`][covjson_msgspec.Domain.referencing] is indexed.

    Returns
    -------
    dict
        Each coordinate identifier mapped to its governing system's typed variant.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, ReferenceSystem
    >>> from covjson_msgspec.referencing import ReferenceSystemConnection
    >>> dom = Domain(
    ...     axes={"t": Axis.listed(("2020-01-01T00:00:00Z",))},
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("t",),
    ...             system=ReferenceSystem.temporal(calendar="Gregorian"),
    ...         )
    ...     ],
    ... )
    >>> coordinate_systems(dom)["t"]
    TemporalRS(calendar='Gregorian', time_scale=None)
    """
    return {
        coordinate: connection.system.refine()
        for connection in domain.referencing
        for coordinate in connection.coordinates
    }


def temporal_coordinates(domain: Domain) -> set[str]:
    """The coordinate identifiers governed by a standard-calendar temporal system.

    The bridges convert time axes to real datetimes only when their calendar is
    one pandas / numpy can parse (see `STANDARD_CALENDARS`); an exotic calendar
    (e.g. ``"360_day"``) has no datetime64 representation, so those coordinates
    stay as ISO strings. This scans the domain's
    [`referencing`][covjson_msgspec.Domain.referencing] and returns the coordinate
    identifiers safe to parse.

    The calendar is matched on its final path segment, lower-cased, so both a
    bare ``"Gregorian"`` and a URI like ``".../calendars/Gregorian"`` are
    recognized.

    Parameters
    ----------
    domain
        The domain whose referencing connections are inspected.

    Returns
    -------
    set of str
        Coordinate identifiers (e.g. ``{"t"}``) tied to a `TemporalRS` whose
        calendar is standard. Empty when there is no such system.

    Examples
    --------
    >>> from covjson_msgspec import Axis, ReferenceSystem
    >>> from covjson_msgspec.referencing import ReferenceSystemConnection
    >>> standard = Domain.grid(
    ...     x=Axis.regular(0.0, 10.0, 3),
    ...     y=Axis.listed((0.0, 1.0)),
    ...     t=Axis.listed(("2020-01-01T00:00:00Z",)),
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("t",),
    ...             system=ReferenceSystem.temporal(calendar="Gregorian"),
    ...         )
    ...     ],
    ... )
    >>> sorted(temporal_coordinates(standard))
    ['t']

    A non-standard calendar yields nothing, so the bridge leaves ``t`` as ISO
    strings:

    >>> exotic = Domain.grid(
    ...     x=Axis.regular(0.0, 10.0, 3),
    ...     y=Axis.listed((0.0, 1.0)),
    ...     t=Axis.listed(("2020-01-01T00:00:00Z",)),
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("t",),
    ...             system=ReferenceSystem.temporal(calendar="360_day"),
    ...         )
    ...     ],
    ... )
    >>> sorted(temporal_coordinates(exotic))
    []
    """

    coordinates: set[str] = set()

    for connection in domain.referencing:
        system = connection.system.refine()
        if isinstance(system, TemporalRS) and is_standard_calendar(system):
            coordinates.update(connection.coordinates)

    return coordinates


def coordinate_identifiers(axis: Axis, axis_name: str) -> tuple[str, ...]:
    """The coordinate identifiers an axis carries, with spec 6.1.1's default applied.

    Spec 6.1.1 makes ``coordinates`` optional: "If missing, the member
    ``"coordinates"`` defaults to a one-element array of the axis identifier and
    MUST NOT be included for that default case." The identifier lives in the
    [`axes`][covjson_msgspec.Domain.axes] mapping key rather than on the axis, so
    only a caller holding both can apply the default, which is why this takes
    ``axis_name`` alongside. It is the one place the default is applied: the
    bridges and [`validate`][covjson_msgspec.validate] all read it from here.

    Parameters
    ----------
    axis
        The axis whose coordinate identifiers are wanted.
    axis_name
        The axis's identifier: its key in [`axes`][covjson_msgspec.Domain.axes].

    Returns
    -------
    tuple of str
        ``axis.coordinates`` when present, else the one-element default naming
        the axis itself.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> composite = Axis.tuple_(
    ...     [("2020-01-01T00:00:00Z", 1.0, 2.0)], coordinates=("t", "x", "y")
    ... )
    >>> coordinate_identifiers(composite, "composite")
    ('t', 'x', 'y')

    An axis that omits ``coordinates`` relies on the default, which names the
    axis itself:

    >>> coordinate_identifiers(Axis.listed((1.0, 2.0)), "x")
    ('x',)
    """

    return axis.coordinates if axis.coordinates is not None else (axis_name,)


def composite_columns(axis: Axis, axis_name: str) -> tuple[tuple[str, list[Any]], ...]:
    """Transpose a ``"tuple"`` axis's values into one column per coordinate.

    A composite ``"tuple"`` axis stores one tuple (a position) per row; the
    dataframe/array bridges lay each component out as its own column. Spec 6.1.1
    requires every value to be a tuple whose size matches the coordinate
    identifier count (`coordinate_identifiers`), and
    [`validate`][covjson_msgspec.validate] reports a violation as
    ``axis.composite-value-shape`` / ``axis.composite-arity``. The bridges do not
    require a validated document, so the same malformation is rejected here with
    one clean `ValueError` rather than an index or typing failure surfacing from
    inside pandas / numpy. Centralizing it keeps that error identical across the
    pandas and xarray bridges, as `require_inline_ndarray` does for range narrowing.

    Parameters
    ----------
    axis
        The ``"tuple"`` axis to transpose.
    axis_name
        The axis's identifier: its key in [`axes`][covjson_msgspec.Domain.axes],
        used to resolve the coordinate default and to name the error.

    Returns
    -------
    tuple of (str, list)
        One ``(identifier, column)`` pair per coordinate, in identifier order;
        each column holds that component across the axis's positions.

    Raises
    ------
    ValueError
        If any value is not a tuple, or is a tuple whose length differs from the
        coordinate identifier count.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> axis = Axis.tuple_([(1.0, 10.0), (2.0, 20.0)], coordinates=("x", "y"))
    >>> composite_columns(axis, "composite")
    (('x', [1.0, 2.0]), ('y', [10.0, 20.0]))

    A value that is not a matching tuple is rejected with a clean message rather
    than left to fail inside the bridge:

    >>> bad = Axis(values=(1.0, 2.0), data_type="tuple", coordinates=("x", "y"))
    >>> composite_columns(bad, "composite")
    Traceback (most recent call last):
        ...
    ValueError: composite axis 'composite' needs 2-tuple values, but value 0 is 1.0
    """

    components = coordinate_identifiers(axis, axis_name)
    expected = len(components)
    rows = axis.values or ()

    for index, row in enumerate(rows):
        if not isinstance(row, tuple) or len(row) != expected:
            msg = (
                f"composite axis {axis_name!r} needs {expected}-tuple values, "
                f"but value {index} is {row!r}"
            )
            raise ValueError(msg)

    positions = cast("tuple[tuple[Any, ...], ...]", rows)

    return tuple(
        (component, [row[index] for row in positions])
        for index, component in enumerate(components)
    )


def maybe_datetime(values: list[Any], is_temporal: bool) -> Any:
    """Parse ``values`` to pandas datetimes when ``is_temporal``, else pass through.

    Paired with `temporal_coordinates`: a caller decides per axis whether its
    coordinate is a standard-calendar time (``is_temporal``) and passes that
    flag here. When set, the values are parsed to a tz-naive
    `pandas.DatetimeIndex`; otherwise they are returned untouched, so the same
    call site handles both time and non-time axes.

    Parameters
    ----------
    values
        The axis coordinate values.
    is_temporal
        Whether ``values`` are standard-calendar times to parse.

    Returns
    -------
    pandas.DatetimeIndex or list
        A [`DatetimeIndex`][pandas.DatetimeIndex] when ``is_temporal`` and parsing
        succeeds; otherwise ``values`` unchanged. Parsing that raises (a malformed time
        string) also falls back to ``values`` rather than propagating.

    Examples
    --------
    A trailing ``"Z"`` is stripped and a ``±hh:mm`` offset is applied, so the
    result is always tz-naive UTC (matching the xarray bridge, which treats naive
    times as UTC):

    >>> parsed = maybe_datetime(["2020-01-01T00:00:00Z"], True)
    >>> list(parsed)
    [Timestamp('2020-01-01 00:00:00')]
    >>> list(maybe_datetime(["2020-01-01T00:00:00+05:00"], True))
    [Timestamp('2019-12-31 19:00:00')]

    Non-temporal values pass straight through:

    >>> maybe_datetime(["a", "b"], False)
    ['a', 'b']
    """

    if not is_temporal:
        return values

    import pandas as pd

    # This bridge classifies by container range (pandas' datetime64[ns] window),
    # not via temporal.resolve(): the two are different functions with different
    # codomains, so resolve is deliberately not the decider here. See ADR-0015.
    #
    # ISO 8601 may carry a trailing "Z"; strip it so the result is tz-naive
    # (matching the xarray bridge, which treats naive times as UTC).
    cleaned = [
        value.removesuffix("Z") if isinstance(value, str) else value for value in values
    ]

    try:
        # ``utc=True`` applies any ``±hh:mm`` offset (a Spec 5.2 form) and yields a
        # UTC-aware index; ``tz_localize(None)`` then drops the zone to naive-UTC,
        # the same flatten the xarray bridge performs. ``format="ISO8601"`` also
        # lets a single axis mix naive and offset values without pandas raising on
        # the inferred format (it would otherwise fall through to the raw strings).
        return pd.to_datetime(cleaned, format="ISO8601", utc=True).tz_localize(None)
    except (ValueError, TypeError):
        # A malformed time string: leave the values raw rather than propagating.
        return values
