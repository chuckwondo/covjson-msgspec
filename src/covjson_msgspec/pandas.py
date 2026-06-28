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
- Each multi-valued individual axis becomes an index level (the index is a
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

A `CoverageCollection` is converted by concatenating its resolved members into
one frame, prefixing each member's index with a leading ``coverage`` level that
identifies it (its ``id`` when set, otherwise its position).

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

from __future__ import annotations

# This bridge is internal glue over the dynamically-typed pandas library, whose
# stubs leave many call results partly unknown, so basedpyright's reportUnknown*
# rules are relaxed here. The public functions stay safe: their signatures are
# explicitly typed and mypy strict guards them, so those rules never fire on the
# user-facing surface.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec._bridging import (
    POLYGON_DOMAIN_TYPES,
    broadcast,
    maybe_datetime,
    range_column,
    require_inline_ndarray,
    temporal_coordinates,
)
from covjson_msgspec.coverage import Coverage, CoverageCollection
from covjson_msgspec.domain import Domain

if TYPE_CHECKING:
    import pandas as pd

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "pandas and numpy are required for this conversion; install covjson-msgspec[pandas]"
)


def to_pandas(obj: Coverage | CoverageCollection) -> pd.DataFrame:
    """Convert a `Coverage` or `CoverageCollection` to a tidy `pandas.DataFrame`.

    Requires the ``pandas`` extra. For a `Coverage`, each parameter range becomes
    a column and the domain's axes become the index and coordinate columns (see
    the module docstring for the full mapping). A `CoverageCollection` is its
    resolved members concatenated, each member's index prefixed with a leading
    ``coverage`` level (so inherited parameters and referencing are applied
    automatically).

    Parameters
    ----------
    obj
        The coverage or collection to convert. Each coverage's ``domain`` must be
        an inline `Domain` (not a URL reference) and every range an inline
        `NdArray`.

    Returns
    -------
    pandas.DataFrame
        For a coverage, a frame whose columns are its parameters (plus a column
        for each single-valued axis and each composite component) and whose index
        is the multi-valued axes. For a collection, those frames concatenated
        under a leading ``coverage`` index level.

    Raises
    ------
    ValueError
        If a domain is a URL reference, a domain type is a polygon type
        (use the geopandas bridge), or a range is not an inline `NdArray`.

    Examples
    --------
    Decode a CoverageJSON document and convert it via its `to_pandas` method (the
    module-level `to_pandas` function is equivalent). The varying ``t`` axis labels
    the rows (the index), each single-valued axis (``x`` / ``y``) becomes a
    constant column, and each range (``v``) becomes a value column:

    >>> from covjson_msgspec import decode_coverage
    >>> cov = decode_coverage('''
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

    A collection stacks its members under a leading ``coverage`` index level,
    keyed by each member's ``id``:

    >>> from covjson_msgspec import decode_coverage_collection
    >>> coll = decode_coverage_collection('''
    ... {
    ...   "type": "CoverageCollection",
    ...   "domainType": "PointSeries",
    ...   "coverages": [
    ...     {
    ...       "type": "Coverage",
    ...       "id": "a",
    ...       "domain": {
    ...         "type": "Domain",
    ...         "axes": {
    ...           "x": {"values": [1.0]},
    ...           "y": {"values": [2.0]},
    ...           "t": {"values": ["2020-01-01", "2020-01-02"]}
    ...         }
    ...       },
    ...       "ranges": {
    ...         "v": {
    ...           "type": "NdArray",
    ...           "dataType": "float",
    ...           "axisNames": ["t"],
    ...           "shape": [2],
    ...           "values": [280.0, 281.0]
    ...         }
    ...       }
    ...     },
    ...     {
    ...       "type": "Coverage",
    ...       "id": "b",
    ...       "domain": {
    ...         "type": "Domain",
    ...         "axes": {
    ...           "x": {"values": [3.0]},
    ...           "y": {"values": [4.0]},
    ...           "t": {"values": ["2020-01-01", "2020-01-02"]}
    ...         }
    ...       },
    ...       "ranges": {
    ...         "v": {
    ...           "type": "NdArray",
    ...           "dataType": "float",
    ...           "axisNames": ["t"],
    ...           "shape": [2],
    ...           "values": [290.0, 291.0]
    ...         }
    ...       }
    ...     }
    ...   ]
    ... }
    ... ''')
    >>> coll.to_pandas()
                           x    y      v
    coverage t
    a        2020-01-01  1.0  2.0  280.0
             2020-01-02  1.0  2.0  281.0
    b        2020-01-01  3.0  4.0  290.0
             2020-01-02  3.0  4.0  291.0
    """
    try:
        import pandas  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if isinstance(obj, CoverageCollection):
        return _collection_to_pandas(obj)

    return _coverage_to_pandas(obj)


def _coverage_to_pandas(coverage: Coverage) -> pd.DataFrame:
    """Convert a single `Coverage` to a tidy frame (the per-coverage core).

    The workhorse behind `to_pandas` for one coverage, and the per-member step of
    `_collection_to_pandas`. It sorts the domain's axes into roles via
    `_axis_layout`, then builds one column per composite component, per scalar
    axis, and per parameter range (each broadcast to the shared grid by
    `~covjson_msgspec._bridging.broadcast` / `range_column`), and indexes them
    with `_index`. The coverage's ``domain_type`` and ``id`` ride along in
    ``frame.attrs``.

    Parameters
    ----------
    coverage
        The coverage to convert; its ``domain`` must be an inline `Domain`.

    Returns
    -------
    pandas.DataFrame
        The tidy frame for this coverage (see `to_pandas` for the column/index
        mapping and a worked example).

    Raises
    ------
    ValueError
        If the domain is a URL reference, its effective domain type is a polygon
        type (use the geopandas bridge), or a range is not an inline `NdArray`.
    """
    import pandas as pd

    if not isinstance(domain := coverage.domain, Domain):
        msg = (
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to pandas"
        )
        raise ValueError(msg)

    domain_type = coverage.effective_domain_type

    if domain_type in POLYGON_DOMAIN_TYPES:
        msg = (
            f"{domain_type!r} is a polygon domain (vector geometry); use the "
            "geopandas bridge instead of pandas"
        )
        raise ValueError(msg)

    temporal = temporal_coordinates(domain)
    layout = _axis_layout(domain, temporal)

    # Lay every column source onto the shared grid: composite components, then
    # the constant scalar axes, then the parameter ranges.
    columns = (
        {
            name: broadcast(values, (dim,), layout.dims, layout.sizes)
            for name, dim, values in layout.composite_columns
        }
        | {
            name: broadcast(value, (), layout.dims, layout.sizes)
            for name, value in layout.scalars.items()
        }
        | {
            key: range_column(
                require_inline_ndarray(key, range_, "pandas"), layout.dims, layout.sizes
            )
            for key, range_ in coverage.ranges.items()
        }
    )

    frame = pd.DataFrame(columns, index=_index(layout))

    if domain_type is not None:
        frame.attrs["domain_type"] = domain_type

    if coverage.id is not None:
        frame.attrs["id"] = coverage.id

    return frame


def _collection_to_pandas(collection: CoverageCollection) -> pd.DataFrame:
    """Concatenate a collection's members into one frame under a ``coverage`` level.

    Members are resolved first so each inherits the collection's parameters and
    referencing (the referencing is what tags temporal axes for datetime
    parsing), then each is converted by `_coverage_to_pandas` and stacked with
    `pandas.concat`. The leading index level is keyed by each member's ``id``,
    falling back to its position so the key is always total.

    Parameters
    ----------
    collection
        The collection to convert.

    Returns
    -------
    pandas.DataFrame
        The members' frames concatenated under a leading ``coverage`` index
        level, or an empty frame when the collection has no coverages (see
        `to_pandas` for a worked example).
    """
    import pandas as pd

    # Resolve first so each member carries the collection's inherited parameters
    # and referencing (the latter is what tags temporal axes for datetime parsing).
    resolved = collection.resolved_coverages()

    if not resolved:
        return pd.DataFrame()

    # Key each member by its id when set, falling back to its position so the
    # leading level is always total.
    keys = [
        coverage.id if coverage.id is not None else index
        for index, coverage in enumerate(resolved)
    ]
    frame = pd.concat(
        [_coverage_to_pandas(coverage) for coverage in resolved],
        keys=keys,
        names=["coverage"],
    )

    if collection.domain_type is not None:
        frame.attrs["domain_type"] = collection.domain_type

    return frame


class _AxisLayout:
    """The axes of a domain sorted into the roles a `DataFrame` gives them.

    ``dims`` are the multi-valued axes (and composite axes) that become index
    levels; ``sizes`` is their length; ``values`` their index labels.
    ``scalars`` are the single-valued axes that become constant columns;
    ``composite_columns`` are the per-component columns of each composite axis.
    """

    def __init__(self) -> None:
        """Start with every role empty; `_axis_layout` fills them from a domain."""
        self.dims: list[str] = []
        self.sizes: dict[str, int] = {}
        self.values: dict[str, Any] = {}
        self.scalars: dict[str, Any] = {}
        self.composite_columns: list[tuple[str, str, Any]] = []


def _axis_layout(domain: Domain, temporal: set[str]) -> _AxisLayout:
    """Sort a domain's axes into the `_AxisLayout` roles a frame gives them.

    Each axis lands in exactly one role: a composite (``tuple``) axis becomes one
    index dim plus a column per component (the tuples transposed); a
    single-valued axis becomes a constant scalar column (its size-1 dimension
    dropped); any other multi-valued axis becomes an index dim. Temporal axes
    named in ``temporal`` have their values parsed to datetimes along the way.

    Parameters
    ----------
    domain
        The domain whose `~Domain.axes` are classified.
    temporal
        Coordinate identifiers to parse as datetimes (from
        `~covjson_msgspec._bridging.temporal_coordinates`).

    Returns
    -------
    _AxisLayout
        The axes bucketed into ``dims`` / ``sizes`` / ``values`` (index),
        ``scalars`` (constant columns), and ``composite_columns``.

    Raises
    ------
    ValueError
        If an axis is a ``polygon`` axis (vector geometry belongs in the
        geopandas bridge).

    Examples
    --------
    A single-valued ``x`` becomes a scalar column while a three-valued ``t``
    becomes an index dim:

    >>> from covjson_msgspec import Axis
    >>> domain = Domain.point_series(
    ...     x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), t=Axis.listed((10, 20, 30))
    ... )
    >>> layout = _axis_layout(domain, temporal=set())
    >>> layout.dims
    ['t']
    >>> sorted(layout.scalars)
    ['x', 'y']
    """
    layout = _AxisLayout()

    for key, axis in domain.axes.items():
        if axis.data_type == "polygon":
            msg = "polygon axes are not supported by the pandas bridge"
            raise ValueError(msg)

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
                    (component, key, maybe_datetime(column, component in temporal))
                )
        else:
            values = maybe_datetime(list(axis.coordinate_values), key in temporal)

            if len(values) == 1:
                # Single-valued axis: a scalar coordinate, kept as a constant
                # column (its size-1 dimension is dropped).
                layout.scalars[key] = values[0]
            else:
                layout.dims.append(key)
                layout.sizes[key] = len(values)
                layout.values[key] = values

    return layout


def _index(layout: _AxisLayout) -> pd.Index[Any]:
    """Build the frame index from a layout's varying dims.

    No varying axis gives a length-1 `~pandas.RangeIndex` (one row); a single dim
    gives a flat `~pandas.Index`; two or more give a `~pandas.MultiIndex` over
    their Cartesian product, in ``dims`` order (matching the row-major ravel that
    `~covjson_msgspec._bridging.broadcast` uses for the columns).

    Parameters
    ----------
    layout
        The axis layout from `_axis_layout`.

    Returns
    -------
    pandas.Index
        The row index for the frame.

    Examples
    --------
    >>> layout = _AxisLayout()
    >>> layout.dims = ["t"]
    >>> layout.values = {"t": [10, 20, 30]}
    >>> _index(layout).tolist()
    [10, 20, 30]
    """
    import pandas as pd

    if not layout.dims:
        return pd.RangeIndex(1)

    if len(layout.dims) == 1:
        name = layout.dims[0]
        # The label data is heterogeneous (dict[str, Any]), so pandas-stubs
        # widens the constructed index to Any; narrow it back to the return type.
        # mypy requires this cast (it widens pd.Index to Any here); basedpyright
        # disagrees and calls it redundant, so silence its lone complaint.
        return cast(  # pyright: ignore[reportUnnecessaryCast]
            "pd.Index[Any]", pd.Index(layout.values[name], name=name)
        )

    return cast(
        "pd.Index[Any]",
        pd.MultiIndex.from_product(
            [layout.values[dim] for dim in layout.dims], names=layout.dims
        ),
    )
