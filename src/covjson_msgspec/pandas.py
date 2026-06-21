"""pandas bridge: convert a `Coverage` into a tidy `pandas.DataFrame`.

A `DataFrame` is the right tool for the tabular domain types (Point, PointSeries,
VerticalProfile, Trajectory, MultiPoint, ...), where each coverage element is a
row.

The result is *tidy* in the conventional sense: each row is one coverage element
(one position in the domain), each column is one variable (a parameter value or a
coordinate), and each cell holds a single value. Concretely, every parameter
range becomes its own column, the varying axes label the rows (via the index),
and the constant or composite coordinates ride alongside as columns. This is the
long form that pandas (and the wider ``groupby`` / ``pivot`` / plotting
ecosystem) expects, and what `DataFrame.set_index` / `reset_index` reshape
around.

Mapping
-------
- Each parameter range becomes a column.
- Each multi-valued independent axis becomes an index level (the index is a
  ``MultiIndex`` when more than one axis varies); a single-valued axis becomes a
  constant column (its size-1 dimension is dropped, a documented round-trip loss).
- A composite ``tuple`` axis (e.g. a trajectory) becomes one index level holding
  the row position, with one column per tuple component (the tuples are
  transposed).
- A temporal axis governed by a standard-calendar `TemporalRS` is parsed to
  pandas datetimes; a non-standard calendar stays as ISO strings.

A multi-dimensional domain (e.g. Grid) is flattened to long form with a
``MultiIndex`` over its axes; for gridded data the xarray bridge is usually the
better fit. Polygon domains carry vector geometry, so they belong in the
geopandas bridge; `to_pandas` rejects them.

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec.coverage import Coverage
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import TemporalRS

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "pandas and numpy are required for this conversion; install covjson-msgspec[pandas]"
)

# Polygon domains are vector geometry, not tabular: they route to the geopandas
# bridge instead of pandas.
_POLYGON_DOMAIN_TYPES = frozenset(
    {"Polygon", "PolygonSeries", "MultiPolygon", "MultiPolygonSeries"}
)

# Calendars whose dates pandas can parse to datetime64; anything else stays as
# the original ISO strings.
_STANDARD_CALENDARS = frozenset({"gregorian", "standard", "proleptic_gregorian"})


def to_pandas(coverage: Coverage) -> "pd.DataFrame":
    """Convert a `Coverage` to a tidy `pandas.DataFrame`.

    Requires the ``pandas`` extra. Each parameter range becomes a column and the
    domain's axes become the index and coordinate columns (see the module
    docstring for the full mapping). A coverage taken from a `CoverageCollection`
    should be obtained via `CoverageCollection.resolved_coverages` first, so its
    parameters and referencing are populated.

    Parameters
    ----------
    coverage
        The coverage to convert. Its ``domain`` must be an inline `Domain` (not a
        URL reference) and every range an inline `NdArray`.

    Returns
    -------
    pandas.DataFrame
        A frame whose columns are the coverage's parameters (plus a column for
        each single-valued axis and each composite component) and whose index is
        the multi-valued axes.

    Raises
    ------
    ValueError
        If the domain is a URL reference, the domain type is a polygon type
        (use the geopandas bridge), or a range is not an inline `NdArray`.

    Examples
    --------
    Decode a CoverageJSON document and convert it via its `to_pandas` method (the
    module-level `to_pandas` function is equivalent). The varying ``t`` axis labels
    the rows (the index), each single-valued axis (``x`` / ``y``) becomes a
    constant column, and each range (``v``) becomes a value column:

    >>> from covjson_msgspec import decode_coverage
    >>> cov = decode_coverage(b'''
    ... {
    ...   "type": "Coverage",
    ...   "domain": {
    ...     "type": "Domain",
    ...     "domainType": "PointSeries",
    ...     "axes": {
    ...       "x": {"values": [1.0]},
    ...       "y": {"values": [2.0]},
    ...       "t": {"values": ["2020-01-01", "2020-01-02", "2020-01-03"]}
    ...     }
    ...   },
    ...   "ranges": {
    ...     "v": {
    ...       "type": "NdArray",
    ...       "dataType": "float",
    ...       "axisNames": ["t"],
    ...       "shape": [3],
    ...       "values": [280.0, 281.0, 282.0]
    ...     }
    ...   }
    ... }
    ... ''')
    >>> cov.to_pandas()
                  x    y      v
    t
    2020-01-01  1.0  2.0  280.0
    2020-01-02  1.0  2.0  281.0
    2020-01-03  1.0  2.0  282.0
    """
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if not isinstance(domain := coverage.domain, Domain):
        raise ValueError(
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to pandas"
        )

    domain_type = domain.domain_type or coverage.domain_type

    if domain_type in _POLYGON_DOMAIN_TYPES:
        raise ValueError(
            f"{domain_type!r} is a polygon domain (vector geometry); use the "
            "geopandas bridge instead of pandas"
        )

    temporal = _temporal_coordinates(domain)
    layout = _axis_layout(domain, temporal)

    columns: dict[str, Any] = {}

    for name, dim, values in layout.composite_columns:
        columns[name] = _broadcast(values, (dim,), layout.dims, layout.sizes)

    for name, value in layout.scalars.items():
        columns[name] = _broadcast(value, (), layout.dims, layout.sizes)

    for key, range_ in coverage.ranges.items():
        if not isinstance(range_, NdArray):
            raise ValueError(
                f"range {key!r} is not an inline NdArray (got "
                f"{type(range_).__name__}); resolve URL ranges and assemble "
                "TiledNdArray tiles before converting to pandas"
            )

        columns[key] = _range_column(range_, layout.dims, layout.sizes)

    frame = pd.DataFrame(columns, index=_index(layout))

    if domain_type is not None:
        frame.attrs["domain_type"] = domain_type

    if coverage.id is not None:
        frame.attrs["id"] = coverage.id

    return frame


class _AxisLayout:
    """The axes of a domain sorted into the roles a `DataFrame` gives them.

    ``dims`` are the multi-valued axes (and composite axes) that become index
    levels; ``sizes`` is their length; ``values`` their index labels.
    ``scalars`` are the single-valued axes that become constant columns;
    ``composite_columns`` are the per-component columns of each composite axis.
    """

    def __init__(self) -> None:
        self.dims: list[str] = []
        self.sizes: dict[str, int] = {}
        self.values: dict[str, Any] = {}
        self.scalars: dict[str, Any] = {}
        self.composite_columns: list[tuple[str, str, Any]] = []


def _axis_layout(domain: Domain, temporal: set[str]) -> _AxisLayout:
    layout = _AxisLayout()

    for key, axis in domain.axes.items():
        if axis.data_type == "polygon":
            raise ValueError("polygon axes are not supported by the pandas bridge")

        if axis.data_type == "tuple":
            # Composite axis: one index level (the row position) plus one column
            # per component, transposing the tuples into columns. A "tuple" axis
            # holds tuple-valued coordinates by construction.
            rows = cast("tuple[tuple[Any, ...], ...]", axis.values or ())
            components = axis.coordinates or ()
            layout.dims.append(key)
            layout.sizes[key] = len(rows)
            layout.values[key] = range(len(rows))

            for index, component in enumerate(components):
                column = [row[index] for row in rows]
                layout.composite_columns.append(
                    (component, key, _maybe_datetime(column, component in temporal))
                )
        else:
            values = _maybe_datetime(list(axis.coordinate_values), key in temporal)

            if len(values) == 1:
                # Single-valued axis: a scalar coordinate, kept as a constant
                # column (its size-1 dimension is dropped).
                layout.scalars[key] = values[0]
            else:
                layout.dims.append(key)
                layout.sizes[key] = len(values)
                layout.values[key] = values

    return layout


def _index(layout: _AxisLayout) -> "pd.Index[Any]":
    import pandas as pd

    if not layout.dims:
        return pd.RangeIndex(1)

    if len(layout.dims) == 1:
        name = layout.dims[0]
        return pd.Index(layout.values[name], name=name)

    return pd.MultiIndex.from_product(
        [layout.values[dim] for dim in layout.dims], names=layout.dims
    )


def _range_column(
    range_: NdArray,
    dims: list[str],
    sizes: dict[str, int],
) -> "np.ndarray[Any, np.dtype[Any]]":
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

    return _broadcast(np.transpose(array, order), present, dims, sizes)


def _broadcast(
    data: Any,
    present: "tuple[str, ...] | list[str]",
    dims: list[str],
    sizes: dict[str, int],
) -> "np.ndarray[Any, np.dtype[Any]]":
    # Lay ``data`` (varying only over ``present``) over the full grid of ``dims``
    # in row-major (C) order, matching pandas' MultiIndex.from_product layout.
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


def _temporal_coordinates(domain: Domain) -> set[str]:
    # The coordinate identifiers governed by a standard-calendar temporal system;
    # only these are parsed to datetimes (other calendars stay as ISO strings).
    coordinates: set[str] = set()

    for connection in domain.referencing:
        if isinstance(system := connection.system, TemporalRS):
            calendar = system.calendar.rsplit("/", 1)[-1].lower()

            if calendar in _STANDARD_CALENDARS:
                coordinates.update(connection.coordinates)

    return coordinates


def _maybe_datetime(values: list[Any], is_temporal: bool) -> Any:
    if not is_temporal:
        return values

    import pandas as pd

    # ISO 8601 may carry a trailing "Z"; strip it so the result is tz-naive
    # (matching the xarray bridge, which treats naive times as UTC).
    cleaned = [
        value.removesuffix("Z") if isinstance(value, str) else value for value in values
    ]

    try:
        return pd.to_datetime(cleaned)
    except (ValueError, TypeError):  # pragma: no cover - malformed time strings
        return values
