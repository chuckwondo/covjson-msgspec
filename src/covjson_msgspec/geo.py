"""geo bridge: convert a `Coverage` into vector geometry.

For the vector domain types (Point, MultiPoint, PointSeries, VerticalProfile,
Trajectory, and the Polygon family) a coverage is naturally a table of features:
one geometry per coverage element, with the parameter values as attributes. This
bridge produces a `geopandas.GeoDataFrame` (`to_geopandas`) or the equivalent
GeoJSON ``FeatureCollection`` mapping (`to_geojson`).

Mapping
-------
- A point-like domain reuses the tidy `to_pandas` frame (where ``x`` / ``y`` are
  always columns) and attaches a ``Point`` geometry built from them; each row
  becomes one feature. A trajectory is therefore emitted as one point feature per
  vertex (preserving each vertex's measurements) by default; pass
  ``trajectory_as="linestring"`` to emit a single ``LineString`` for the path
  instead (geometry only, dropping the per-vertex measurements).
- A Polygon / PolygonSeries domain becomes one feature (repeated over ``t`` for a
  series); a MultiPolygon / MultiPolygonSeries domain becomes one feature per
  polygon. The ``composite`` axis supplies the ``Polygon`` geometry.
- A vertical (``z``) coordinate is carried into point geometry as a third
  dimension (a ``POINT Z``) and also kept as a column. For a polygon it reaches
  the geometry (a ``POLYGON Z``) only when ``z`` is one of the ``composite`` ring
  coordinates; a standalone ``z`` axis on a polygon stays a column only.
- A geographic reference system tags the result with its ``id`` when pyproj can
  resolve it, else with CoverageJSON's default geographic CRS, ``OGC:CRS84``
  (WGS84 longitude/latitude); a projected reference system tags it with that
  system's ``id`` (an EPSG / OGC CRS URI).

A multi-dimensional gridded domain (Grid) is degenerately emitted as one point
feature per cell (with a `UserWarning`, since the xarray bridge is the better fit
for gridded data).

A `CoverageCollection` is converted by concatenating its resolved members into one
frame, with a leading ``coverage`` column identifying each member (its ``id`` when
set, otherwise its position). Unlike the pandas bridge's index level, this is a
plain column so it survives ``to_json`` into each feature's ``properties``.

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

from __future__ import annotations

# This bridge is internal glue over dynamically-typed third-party libraries
# (geopandas / shapely / pandas) whose stubs leave many call results partly
# unknown, so basedpyright's reportUnknown* rules are relaxed here. The public
# functions stay safe: their signatures are explicitly typed and mypy strict
# guards them, so those rules never fire on the user-facing surface.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
import contextlib
import json
import warnings
from typing import TYPE_CHECKING, Any, Literal, cast

from covjson_msgspec._bridging import (
    POLYGON_DOMAIN_TYPES,
    broadcast,
    coordinate_identifiers,
    maybe_datetime,
    range_column,
    require_inline_ndarray,
    temporal_coordinates,
)
from covjson_msgspec.axis import Axis, AxisValue
from covjson_msgspec.coverage import Coverage, CoverageCollection
from covjson_msgspec.domain import Domain
from covjson_msgspec.referencing import GeographicCRS, ProjectedCRS

if TYPE_CHECKING:
    import geopandas as gpd
    import numpy as np
    import numpy.typing as npt
    import pandas as pd

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "geopandas and shapely are required for this conversion; "
    "install covjson-msgspec[geo]"
)

# CoverageJSON's default geographic CRS: WGS84 longitude/latitude (OGC CRS84).
# CRS84 is lon/lat, matching how the bridge builds x / y geometry; EPSG:4326
# names the same datum but in lat/lon authority order, so CRS84 is the right tag.
_DEFAULT_GEOGRAPHIC_CRS = "OGC:CRS84"

# How a Trajectory's vertices map to geometry: one ``Point`` feature per vertex
# (keeping each vertex's measurements), or a single ``LineString`` for the path.
TrajectoryAs = Literal["points", "linestring"]
_TRAJECTORY_AS = frozenset({"points", "linestring"})

# A Grid is gridded data, not vector features; we degenerately emit one point per
# cell, but the xarray bridge is the better fit, so warn rather than do it silently.
_GRID_WARNING = (
    "converting a Grid domain to vector geometry emits one point feature per "
    "cell, which is rarely what you want; consider to_xarray for gridded data"
)


def to_geopandas(
    obj: Coverage | CoverageCollection,
    *,
    trajectory_as: TrajectoryAs = "points",
) -> gpd.GeoDataFrame:
    """Convert a `Coverage` or `CoverageCollection` to a `geopandas.GeoDataFrame`.

    Requires the ``geo`` extra. For a `Coverage`, each coverage element becomes
    one feature: a point-like domain yields ``Point`` geometry from its ``x`` /
    ``y`` coordinates and the Polygon family yields ``Polygon`` geometry from its
    ``composite`` axis (see the module docstring for the full mapping). A
    `CoverageCollection` is its resolved members concatenated, each member's rows
    tagged with a leading ``coverage`` column (so inherited parameters and
    referencing are applied automatically).

    Parameters
    ----------
    obj
        The coverage or collection to convert. Each coverage's ``domain`` must be
        an inline `Domain` (not a URL reference) and every range an inline
        `NdArray`.
    trajectory_as
        How a Trajectory domain maps to geometry. ``"points"`` (the default)
        emits one ``Point`` feature per vertex, preserving each vertex's
        measurements; ``"linestring"`` emits a single ``LineString`` feature for
        the whole path (geometry only, since per-vertex measurements do not
        reduce to one row). Other domain types ignore this option.

    Returns
    -------
    geopandas.GeoDataFrame
        A frame of the parameter and coordinate columns with a ``geometry``
        column; its CRS is the geographic system's resolvable ``id`` (else
        ``OGC:CRS84``, the WGS84 lon/lat default), or the projected system's
        ``id`` for a projected one. For a collection, the member frames
        concatenated under a leading ``coverage`` column.

    Raises
    ------
    ValueError
        If a domain is a URL reference, a point-like domain lacks ``x`` / ``y``
        coordinates, a range is not an inline `NdArray`, ``trajectory_as`` is not
        ``"points"`` or ``"linestring"``, a geometry-bearing domain's
        ``composite`` axis declares the wrong ``dataType`` or resolves to
        coordinates without ``x`` / ``y``, or (in ``"linestring"`` mode) a
        Trajectory has fewer than two vertices.

    Warns
    -----
    UserWarning
        If a domain is a Grid, which is degenerately emitted as one point feature
        per cell (the xarray bridge is the better fit for gridded data).

    Examples
    --------
    A point-like domain yields one ``Point`` feature, with the single-valued
    ``x`` / ``y`` coordinates kept as columns alongside the parameter values:

    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"v": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> gdf = to_geopandas(cov)
    >>> list(gdf.columns)
    ['x', 'y', 'v', 'geometry']
    >>> gdf.geometry.iloc[0]
    <POINT (1 2)>
    >>> gdf["v"].tolist()
    [280.0]
    """
    if trajectory_as not in _TRAJECTORY_AS:
        msg = f"trajectory_as must be 'points' or 'linestring'; got {trajectory_as!r}"
        raise ValueError(msg)

    # Surface the friendly install hint here; the helpers re-import geopandas
    # locally (a cached lookup) so they keep a precise gpd type for the checker.
    try:
        import geopandas  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    # Warn from this public entry point (not the helpers) so stacklevel=2 lands on
    # the caller's code rather than a private frame; a collection warns once if any
    # member is a Grid.
    members = (
        obj.resolved_coverages() if isinstance(obj, CoverageCollection) else (obj,)
    )

    if any(member.effective_domain_type == "Grid" for member in members):
        warnings.warn(_GRID_WARNING, UserWarning, stacklevel=2)

    if isinstance(obj, CoverageCollection):
        return _collection_to_geopandas(obj, trajectory_as)

    return _coverage_to_geopandas(obj, trajectory_as)


def to_geojson(
    obj: Coverage | CoverageCollection,
    *,
    trajectory_as: TrajectoryAs = "points",
) -> dict[str, Any]:
    """Convert a `Coverage` or `CoverageCollection` to a GeoJSON mapping.

    Requires the ``geo`` extra. Thin wrapper over `to_geopandas`: each coverage
    element becomes a GeoJSON ``Feature`` whose ``properties`` are the parameter
    and coordinate values and whose ``geometry`` is the element's point or polygon
    (see the module docstring for the full mapping). A `CoverageCollection`'s
    features carry a ``coverage`` property identifying their source member.

    Parameters
    ----------
    obj
        The coverage or collection to convert (same requirements as
        `to_geopandas`).
    trajectory_as
        How a Trajectory domain maps to geometry; see `to_geopandas`.

    Returns
    -------
    dict
        A GeoJSON ``FeatureCollection`` as a plain (JSON-compatible) mapping.

    Raises
    ------
    ValueError
        Propagated from `to_geopandas` (URL domain, missing ``x`` / ``y``, a
        non-composite ``composite`` axis, a non-inline range, or an invalid
        ``trajectory_as``).

    Warns
    -----
    UserWarning
        Propagated from `to_geopandas` when a domain is a Grid (emitted as one
        point feature per cell; the xarray bridge is the better fit).

    Examples
    --------
    >>> from covjson_msgspec import decode_coverage
    >>> cov = decode_coverage('''
    ... {
    ...   "type": "Coverage",
    ...   "domain": {
    ...     "type": "Domain",
    ...     "domainType": "Point",
    ...     "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
    ...   },
    ...   "ranges": {}
    ... }
    ... ''')
    >>> gj = cov.to_geojson()
    >>> gj["type"]
    'FeatureCollection'
    >>> gj["features"][0]["geometry"]
    {'type': 'Point', 'coordinates': [1.0, 2.0]}
    """
    # geopandas' to_json serializes geometry, columns, datetimes, and (unlike the
    # __geo_interface__ mapping) drops the row index, giving clean JSON output.
    gdf = to_geopandas(obj, trajectory_as=trajectory_as)

    # An empty CoverageCollection yields a frame with no geometry column, on which
    # to_json would raise; emit an empty FeatureCollection directly instead.
    if "geometry" not in gdf.columns:
        return {"type": "FeatureCollection", "features": []}

    return cast("dict[str, Any]", json.loads(gdf.to_json()))


def _coverage_to_geopandas(
    coverage: Coverage, trajectory_as: TrajectoryAs
) -> gpd.GeoDataFrame:
    """Convert a single `Coverage` to a `GeoDataFrame` (per-coverage core).

    The workhorse behind `to_geopandas` for one coverage, and the per-member step
    of `_collection_to_geopandas`. It dispatches on the coverage's effective
    domain type to the matching frame builder: `_polygon_frame` for the Polygon
    family, `_trajectory_linestring_frame` for a Trajectory in ``"linestring"``
    mode, else `_point_frame`. The builder returns a plain frame plus a geometry
    array, which are combined with the CRS from `_crs`; ``domain_type`` and
    ``id`` ride along in ``gdf.attrs``.

    Parameters
    ----------
    coverage
        The coverage to convert; its ``domain`` must be an inline `Domain`.
    trajectory_as
        How a Trajectory maps to geometry (see `to_geopandas`); ignored for other
        domain types.

    Returns
    -------
    geopandas.GeoDataFrame
        One feature per coverage element (see `to_geopandas` for the mapping).

    Raises
    ------
    ValueError
        If the domain is a URL reference, or a builder cannot find the
        coordinates it needs (e.g. no ``x`` / ``y``).
    """
    import geopandas as gpd

    if not isinstance(domain := coverage.domain, Domain):
        msg = (
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to geopandas"
        )
        raise ValueError(msg)

    # The Grid UserWarning is emitted at the public boundary (to_geopandas), which
    # also covers each member when converting a collection.
    domain_type = coverage.effective_domain_type

    if domain_type in POLYGON_DOMAIN_TYPES:
        frame, geometry = _polygon_frame(coverage, domain)
    elif domain_type == "Trajectory" and trajectory_as == "linestring":
        frame, geometry = _trajectory_linestring_frame(domain)
    else:
        frame, geometry = _point_frame(coverage, domain)

    gdf = gpd.GeoDataFrame(frame, geometry=geometry, crs=_crs(domain))

    if domain_type is not None:
        gdf.attrs["domain_type"] = domain_type

    if coverage.id is not None:
        gdf.attrs["id"] = coverage.id

    return gdf


def _collection_to_geopandas(
    collection: CoverageCollection, trajectory_as: TrajectoryAs
) -> gpd.GeoDataFrame:
    """Concatenate a collection's members into one frame keyed by a ``coverage`` column.

    Members are resolved first so each inherits the collection's parameters and
    referencing (which tags temporal axes and sets the CRS), then each is
    converted by `_coverage_to_geopandas`. Each member gets a leading
    ``coverage`` column (its ``id``, falling back to its position): a plain
    column, not an index level, so it survives `to_json`
    into each feature's properties. The members are concatenated as plain frames,
    then geometry is rebuilt once with the shared CRS (a collection's members
    share referencing, so it is uniform).

    Parameters
    ----------
    collection
        The collection to convert.
    trajectory_as
        How Trajectory members map to geometry (see `to_geopandas`).

    Returns
    -------
    geopandas.GeoDataFrame
        The members' features concatenated, each tagged with a ``coverage``
        column, or an empty frame when the collection has no coverages.
    """
    import geopandas as gpd
    import pandas as pd

    # Resolve first so each member carries the collection's inherited parameters
    # and referencing (the latter is what tags temporal axes and sets the CRS).
    resolved = collection.resolved_coverages()

    if not resolved:
        return gpd.GeoDataFrame()

    frames = []

    for index, coverage in enumerate(resolved):
        gdf = _coverage_to_geopandas(coverage, trajectory_as)
        # Key each member by its id when set, falling back to its position. A
        # leading plain column (not an index level) survives to_json into each
        # feature's properties.
        gdf.insert(0, "coverage", coverage.id if coverage.id is not None else index)
        frames.append(gdf)

    # Concatenate as plain frames, then rebuild geometry with a single explicit
    # CRS (members of a collection share referencing, so it is uniform).
    combined = pd.concat([pd.DataFrame(frame) for frame in frames], ignore_index=True)
    crs = next((frame.crs for frame in frames if frame.crs is not None), None)
    result = gpd.GeoDataFrame(combined, geometry="geometry", crs=crs)

    if collection.domain_type:
        result.attrs["domain_type"] = collection.domain_type

    return result


def _point_frame(
    coverage: Coverage, domain: Domain
) -> tuple[pd.DataFrame, npt.NDArray[np.object_]]:
    """Build a point-per-element frame and ``Point`` geometry from ``x`` / ``y``.

    The default builder for every point-like domain (Point, PointSeries,
    VerticalProfile, MultiPoint, a Trajectory in ``"points"`` mode, and a Grid
    degenerately). It reuses the tidy [`to_pandas`][covjson_msgspec.to_pandas] frame
    (where ``x`` / ``y`` are always columns), promotes any index levels
    (``t`` / ``z`` / composite position) to columns so they survive
    ``to_json``, drops the bare composite position level (its components already
    ride as columns), then builds one ``Point`` per row. A ``z`` column is
    carried into the geometry as a third dimension (``POINT Z``) and also kept as
    a column.

    Parameters
    ----------
    coverage
        The coverage whose ranges and coordinates fill the frame.
    domain
        The coverage's domain, used to spot a composite (``tuple``) axis whose
        bare position level should be dropped.

    Returns
    -------
    tuple of (pandas.DataFrame, numpy.ndarray)
        The per-row attribute frame and a matching object array of ``Point``
        geometries.

    Raises
    ------
    ValueError
        If the frame has no ``x`` / ``y`` columns to build geometry from.
    """
    import pandas as pd

    from covjson_msgspec.pandas import to_pandas

    # The tidy frame puts x / y as columns for every point-like domain; promote
    # any index levels (t / z / composite) to columns so they survive to_json.
    frame = to_pandas(coverage)
    frame = (
        frame.reset_index(drop=True)
        if frame.index.name is None and not isinstance(frame.index, pd.MultiIndex)
        else frame.reset_index()
    )

    # A composite ("tuple") axis becomes a bare positional index level (0, 1,
    # 2 ...) in the tidy frame; its x / y / z components already ride as their
    # own columns, so drop the position level rather than leak it into each
    # feature's properties.
    if leaked := [
        key
        for key, axis in domain.axes.items()
        if axis.data_type == "tuple" and key in frame.columns
    ]:
        frame = frame.drop(columns=leaked)

    return frame, _point_geometry(frame)


def _point_geometry(frame: pd.DataFrame) -> npt.NDArray[np.object_]:
    """Build the per-row ``Point`` geometry from a point frame's ``x`` / ``y`` columns.

    A ``z`` column is carried into the geometry as a third dimension (``POINT Z``;
    GeoJSON allows it) and also stays a column.

    Parameters
    ----------
    frame
        The point frame; must have ``x`` and ``y`` columns (and optionally ``z``).

    Returns
    -------
    numpy.ndarray
        An object array of ``Point`` geometries, one per row.

    Raises
    ------
    ValueError
        If the frame has no ``x`` / ``y`` columns to build geometry from.
    """
    import shapely

    if "x" not in frame.columns or "y" not in frame.columns:
        msg = (
            "a point-like domain needs x and y coordinates to build geometry; "
            f"got columns {list(frame.columns)}"
        )
        raise ValueError(msg)

    if "z" in frame.columns:
        geometry = shapely.points(
            frame["x"].to_numpy(), frame["y"].to_numpy(), frame["z"].to_numpy()
        )
    else:
        geometry = shapely.points(frame["x"].to_numpy(), frame["y"].to_numpy())

    # With array inputs shapely.points always yields an object array of Points
    # (its overloads also admit a scalar Point for scalar inputs, which cannot
    # arise here); pin the array type so the geometry column is precisely typed.
    return cast("npt.NDArray[np.object_]", geometry)


def _require_composite_axis(
    domain: Domain, domain_type: str, expected: Literal["tuple", "polygon"]
) -> Axis:
    """Return the ``composite`` axis, or raise if it is absent or the wrong shape.

    The geometry builders read the ``composite`` axis's values as positions or
    rings, which only a composite axis holds.  [`validate`][covjson_msgspec.validate]
    reports the same malformations as ``domain.missing-axis`` and
    ``domain.composite-data-type``, but the bridges do not require a validated
    document, so without this an unvalidated one reaches shapely and fails from
    inside a third-party library. The wrong-``dataType`` message mirrors that
    issue's wording.

    Parameters
    ----------
    domain
        The domain whose ``composite`` axis is wanted.
    domain_type
        The effective domain type, for the message.
    expected
        The ``dataType`` this domain type's geometry requires.

    Returns
    -------
    Axis
        The ``composite`` axis, known to declare ``expected``.

    Raises
    ------
    ValueError
        If the ``composite`` axis is absent, or declares any other ``dataType``.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> domain = Domain(axes={"composite": Axis.listed((1.0, 2.0))})
    >>> _require_composite_axis(domain, "Polygon", "polygon")
    Traceback (most recent call last):
        ...
    ValueError: a Polygon domain requires a 'polygon' composite axis; got dataType None

    A domain with no ``composite`` axis is rejected the same way:

    >>> bare = Domain(axes={"x": Axis.listed((1.0,))})
    >>> _require_composite_axis(bare, "Polygon", "polygon")
    Traceback (most recent call last):
        ...
    ValueError: a Polygon domain requires a 'composite' axis, but the domain has none
    """

    if (axis := domain.axes.get("composite")) is None:
        msg = (
            f"a {domain_type} domain requires a 'composite' axis, "
            "but the domain has none"
        )
        raise ValueError(msg)

    if axis.data_type != expected:
        msg = (
            f"a {domain_type} domain requires a {expected!r} composite axis; "
            f"got dataType {axis.data_type!r}"
        )
        raise ValueError(msg)

    return axis


def _horizontal_indices(
    coords: tuple[str, ...], domain_type: str, geometry: str
) -> tuple[int, int, int | None]:
    """Locate the ``x`` / ``y`` (and optional ``z``) components in ``coords``.

    A composite axis names its position components in ``coordinates`` (ADR-0019
    requires them), but the geometry builders understand only ``x`` / ``y``:
    coordinates naming neither cannot place a position. Rather than guess at
    ``("x", "y")``, that is reported as the error it is.

    Parameters
    ----------
    coords
        The resolved coordinate identifiers, from
        `coordinate_identifiers`.
    domain_type
        The effective domain type, for the message.
    geometry
        The geometry being built, for the message (e.g. ``"LineString"``).

    Returns
    -------
    tuple of (int, int, int or None)
        The positions of ``x``, ``y``, and ``z`` (``None`` when 2D).

    Raises
    ------
    ValueError
        If ``coords`` lacks ``x`` or ``y``.

    Examples
    --------
    >>> _horizontal_indices(("t", "x", "y"), "Trajectory", "LineString")
    (1, 2, None)

    Coordinates naming neither ``x`` nor ``y`` place nothing:

    >>> _horizontal_indices(("t", "a", "b"), "Trajectory", "LineString")
    Traceback (most recent call last):
        ...
    ValueError: a Trajectory needs x and y coordinates to build a LineString; ...
    """

    if "x" not in coords or "y" not in coords:
        msg = (
            f"a {domain_type} needs x and y coordinates to build a {geometry}; "
            f"got composite coordinates {list(coords)}"
        )
        raise ValueError(msg)

    return (
        coords.index("x"),
        coords.index("y"),
        coords.index("z") if "z" in coords else None,
    )


def _trajectory_linestring_frame(
    domain: Domain,
) -> tuple[pd.DataFrame, npt.NDArray[np.object_]]:
    """Collapse a Trajectory's vertices into a single ``LineString`` feature.

    The builder for ``trajectory_as="linestring"``. It reads the path from the
    ``composite`` axis's ordered ``(x, y[, z])`` positions and emits one feature
    for the whole path; a ``z`` component makes it 3D (``LINESTRING Z``). Because
    a path's per-vertex measurements do not reduce to a single row, the geometry
    is kept alone, with a one-row frame and no range columns: this is why it
    takes only ``domain`` (no coverage), unlike `_point_frame` / `_polygon_frame`.

    Parameters
    ----------
    domain
        The Trajectory domain whose ``composite`` axis holds the vertices.

    Returns
    -------
    tuple of (pandas.DataFrame, numpy.ndarray)
        A one-row frame and a length-1 object array holding the ``LineString``.

    Raises
    ------
    ValueError
        If the ``composite`` axis is not a ``"tuple"`` axis, lacks ``x`` / ``y``
        coordinates, or has fewer than two vertices (too few for a line; use the
        default points geometry).
    """
    # Linestring mode reads only the composite axis; unlike _point_frame and
    # _polygon_frame it needs nothing from the coverage (the per-vertex range
    # values are dropped when collapsing the path to one geometry).
    import numpy as np
    import pandas as pd
    import shapely

    composite = _require_composite_axis(domain, "Trajectory", "tuple")
    coords = coordinate_identifiers(composite, "composite")
    # A vertical component makes the path 3D (LINESTRING Z).
    x_index, y_index, z_index = _horizontal_indices(coords, "Trajectory", "LineString")
    # A "tuple" composite holds one tuple (position) per vertex by construction.
    positions = cast("tuple[tuple[Any, ...], ...]", composite.values or ())

    if len(positions) < 2:
        msg = (
            "a Trajectory needs at least two vertices to build a LineString "
            f"(got {len(positions)}); use the default points geometry instead"
        )
        raise ValueError(msg)

    def vertex(position: tuple[Any, ...]) -> tuple[float, ...]:
        """Pull ``(x, y)`` (or ``(x, y, z)``) out of one composite position tuple."""
        if z_index is None:
            return (position[x_index], position[y_index])
        return (position[x_index], position[y_index], position[z_index])

    line = [vertex(position) for position in positions]

    # One feature for the whole path. Per-vertex measurements do not reduce to a
    # single row, so linestring mode keeps the geometry only (no range columns).
    # An object array (like the point / polygon helpers) keeps the geometry slot
    # uniformly typed across the three builders.
    return pd.DataFrame(index=[0]), np.array([shapely.LineString(line)], dtype=object)


def _polygon_frame(
    coverage: Coverage, domain: Domain
) -> tuple[pd.DataFrame, npt.NDArray[np.object_]]:
    """Build ``Polygon`` geometry and an attribute frame for the Polygon family.

    The builder for Polygon / PolygonSeries / MultiPolygon / MultiPolygonSeries.
    Each value of the ``composite`` axis is a polygon (a sequence of rings) turned
    into a shapely ``Polygon`` by `_shapely_polygon`; a ``z`` ring component makes
    it 3D (``POLYGON Z``). The element axes are the polygons, plus ``t`` when it
    varies (a *Series), in canonical row-major order; each parameter range and the
    ``t`` / ``z`` coordinates are broadcast across that grid so every column (and
    the repeated geometry) lines up row for row.

    Parameters
    ----------
    coverage
        The coverage whose ranges fill the attribute columns.
    domain
        The domain whose ``composite`` axis supplies the polygons (and optional
        ``t`` / ``z`` axes).

    Returns
    -------
    tuple of (pandas.DataFrame, numpy.ndarray)
        The attribute frame and a matching object array of ``Polygon`` geometries
        (one row per polygon, repeated over ``t`` for a series).

    Raises
    ------
    ValueError
        If the ``composite`` axis is not a ``"polygon"`` axis or lacks ``x`` /
        ``y`` coordinates, or a range is not an inline `NdArray`.
    """
    import numpy as np
    import pandas as pd

    domain_type = coverage.effective_domain_type or "Polygon"
    composite = _require_composite_axis(domain, domain_type, "polygon")
    coords = coordinate_identifiers(composite, "composite")
    # A vertical component in the ring positions becomes a 3D polygon (POLYGON Z).
    x_index, y_index, z_index = _horizontal_indices(coords, domain_type, "Polygon")
    polygons = [
        _shapely_polygon(polygon, x_index, y_index, z_index)
        for polygon in (composite.values or ())
    ]

    # The element axes, in canonical (row-major) order: the polygons, then time
    # when it varies (a PolygonSeries / MultiPolygonSeries).
    dims = ["composite"]
    sizes = {"composite": len(polygons)}
    t_axis = domain.axes.get("t")
    times: list[AxisValue] = (
        list(t_axis.coordinate_values) if t_axis is not None else []
    )

    if len(times) > 1:
        dims.append("t")
        sizes["t"] = len(times)

    columns: dict[str, Any] = {
        key: range_column(require_inline_ndarray(key, range_, "geopandas"), dims, sizes)
        for key, range_ in coverage.ranges.items()
    }

    temporal = temporal_coordinates(domain)

    if times:
        present = ("t",) if "t" in dims else ()
        column = broadcast(np.asarray(times, dtype=object), present, dims, sizes)
        columns["t"] = maybe_datetime(list(column), "t" in temporal)

    if (z_axis := domain.axes.get("z")) is not None:
        z_values = list(z_axis.coordinate_values)
        columns["z"] = broadcast(np.asarray(z_values, dtype=object), (), dims, sizes)

    indices = broadcast(np.arange(len(polygons)), ("composite",), dims, sizes)
    geometry = np.array([polygons[index] for index in indices], dtype=object)

    return pd.DataFrame(columns), geometry


def _shapely_polygon(
    polygon: Any, x_index: int, y_index: int, z_index: int | None
) -> Any:
    """Turn one CoverageJSON polygon (a sequence of rings) into a shapely ``Polygon``.

    Each ring is a sequence of positions; ring 0 is the exterior shell and any
    remaining rings are holes. The ``*_index`` arguments say which slot of each
    position holds ``x`` / ``y`` (and ``z`` when present, yielding a 3D polygon).

    Parameters
    ----------
    polygon
        The rings of one polygon (each a sequence of position tuples).
    x_index, y_index
        The position-tuple indices of the horizontal coordinates.
    z_index
        The position-tuple index of the vertical coordinate, or ``None`` for 2D.

    Returns
    -------
    shapely.Polygon
        The shell-and-holes polygon.
    """
    import shapely

    # A polygon is a sequence of rings; ring 0 is the exterior, the rest holes.
    # Include the vertical component per position when the axis carries one.
    def position_coords(position: Any) -> tuple[float, ...]:
        """Pull ``(x, y)`` (or ``(x, y, z)``) out of one ring position."""
        if z_index is None:
            return (position[x_index], position[y_index])
        return (position[x_index], position[y_index], position[z_index])

    rings = [[position_coords(position) for position in ring] for ring in polygon]
    shell, *holes = rings
    return shapely.Polygon(shell, holes)


def _crs(domain: Domain) -> str | None:
    """Pick the horizontal CRS tag for the result frame from a domain's referencing.

    Scans the domain's reference-system connections for the first horizontal
    system: a [`GeographicCRS`][covjson_msgspec.GeographicCRS] resolves via
    `_geographic_crs` (honoring a usable ``id``, else the lon/lat default), and a
    [`ProjectedCRS`][covjson_msgspec.ProjectedCRS] contributes its ``id`` (an
    EPSG / OGC CRS URI) directly. Vertical / temporal / identifier systems (and a
    projected system with no ``id``) supply no horizontal CRS.

    Parameters
    ----------
    domain
        The domain whose [`referencing`][covjson_msgspec.Domain.referencing] is scanned.

    Returns
    -------
    str or None
        A CRS string geopandas understands, or ``None`` when no horizontal
        system is present.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> from covjson_msgspec import ReferenceSystem, ReferenceSystemConnection
    >>> domain = Domain.point(
    ...     x=Axis.listed((1.0,)),
    ...     y=Axis.listed((2.0,)),
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("x", "y"), system=ReferenceSystem.geographic()
    ...         )
    ...     ],
    ... )
    >>> _crs(domain)
    'OGC:CRS84'
    >>> _crs(Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))) is None
    True
    """
    # A horizontal reference system supplies the result CRS. A geographic system
    # honors a resolvable `id` and otherwise falls back to the lon/lat default
    # (see `_geographic_crs`). A projected system is identified by its `id` (an
    # EPSG / OGC CRS URI that pyproj resolves); pass it through, falling back to
    # unset when it carries none. Any other system leaves the CRS unset.
    for connection in domain.referencing:
        match connection.system.refine():
            case GeographicCRS(id=crs_id):
                return _geographic_crs(crs_id)
            case ProjectedCRS(id=crs_id) if crs_id is not None:
                return crs_id
            case _:
                # A vertical / temporal / identifier system (or a projected one
                # with no id) does not supply the horizontal CRS; keep looking.
                pass

    return None


def _geographic_crs(crs_id: str | None) -> str:
    """Resolve a geographic system's ``id`` to a CRS string, defaulting to lon/lat.

    Honors ``crs_id`` when pyproj can resolve it (mirroring the
    [`ProjectedCRS`][covjson_msgspec.ProjectedCRS] branch of `_crs`); otherwise (no
    ``id``, or an unresolvable placeholder like ``"crs"``) falls back to
    `_DEFAULT_GEOGRAPHIC_CRS` (``OGC:CRS84``). CRS84 is lon/lat, matching how the
    bridge lays out ``x`` / ``y`` geometry; ``EPSG:4326`` names the same datum in
    lat/lon authority order, so it is deliberately *not* the fallback.

    Parameters
    ----------
    crs_id
        The geographic system's ``id``, or ``None``.

    Returns
    -------
    str
        ``crs_id`` when resolvable, else ``OGC:CRS84``.

    Examples
    --------
    >>> _geographic_crs(None)
    'OGC:CRS84'
    >>> _geographic_crs("crs")  # unresolvable placeholder
    'OGC:CRS84'
    >>> _geographic_crs("EPSG:4326")
    'EPSG:4326'
    """
    # CoverageJSON's default geographic CRS is WGS84 longitude/latitude (OGC
    # CRS84), which is the lon/lat axis order the bridge builds x / y geometry
    # in. Honor an `id` that pyproj can resolve (mirroring the ProjectedCRS
    # branch); otherwise (no id, or an unresolvable placeholder like "crs") fall
    # back to that default rather than failing. Unlike EPSG:4326, whose authority
    # axis order is lat/lon, CRS84 matches the data, so it is the right fallback.
    if crs_id is not None:
        from pyproj import CRS
        from pyproj.exceptions import CRSError

        with contextlib.suppress(CRSError):
            CRS.from_user_input(crs_id)
            return crs_id

    return _DEFAULT_GEOGRAPHIC_CRS
