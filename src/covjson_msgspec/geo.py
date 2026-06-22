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

import json
import warnings
from typing import TYPE_CHECKING, Any, Literal, cast

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
from covjson_msgspec.referencing import GeographicCRS, ProjectedCRS

if TYPE_CHECKING:
    import geopandas as gpd

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
) -> "gpd.GeoDataFrame":
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
        ``"points"`` or ``"linestring"``, or (in ``"linestring"`` mode) a
        Trajectory has fewer than two vertices or a ``composite`` axis without
        ``x`` / ``y``.

    Warns
    -----
    UserWarning
        If a domain is a Grid, which is degenerately emitted as one point feature
        per cell (the xarray bridge is the better fit for gridded data).
    """
    if trajectory_as not in _TRAJECTORY_AS:
        msg = f"trajectory_as must be 'points' or 'linestring'; got {trajectory_as!r}"
        raise ValueError(msg)

    # Surface the friendly install hint here; the helpers re-import geopandas
    # locally (a cached lookup) so they keep a precise gpd type for the checker.
    try:
        import geopandas  # noqa: F401
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
    coverage: Coverage | CoverageCollection,
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
    coverage
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
        non-inline range, or an invalid ``trajectory_as``).

    Warns
    -----
    UserWarning
        Propagated from `to_geopandas` when a domain is a Grid (emitted as one
        point feature per cell; the xarray bridge is the better fit).

    Examples
    --------
    >>> from covjson_msgspec import decode_coverage
    >>> cov = decode_coverage(
    ...     '{"type": "Coverage", "domain": {"type": "Domain",'
    ...     ' "domainType": "Point", "axes": {"x": {"values": [1.0]},'
    ...     ' "y": {"values": [2.0]}}}, "ranges": {}}'
    ... )
    >>> gj = cov.to_geojson()
    >>> gj["type"]
    'FeatureCollection'
    >>> gj["features"][0]["geometry"]
    {'type': 'Point', 'coordinates': [1.0, 2.0]}
    """
    # geopandas' to_json serializes geometry, columns, datetimes, and (unlike the
    # __geo_interface__ mapping) drops the row index, giving clean JSON output.
    gdf = to_geopandas(coverage, trajectory_as=trajectory_as)

    # An empty CoverageCollection yields a frame with no geometry column, on which
    # to_json would raise; emit an empty FeatureCollection directly instead.
    if "geometry" not in gdf.columns:
        return {"type": "FeatureCollection", "features": []}

    return cast("dict[str, Any]", json.loads(gdf.to_json()))


def _coverage_to_geopandas(
    coverage: Coverage, trajectory_as: TrajectoryAs
) -> "gpd.GeoDataFrame":
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
        frame, geometry = _trajectory_linestring_frame(coverage, domain)
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
) -> "gpd.GeoDataFrame":
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

    if collection.domain_type is not None:
        result.attrs["domain_type"] = collection.domain_type

    return result


def _point_frame(coverage: Coverage, domain: Domain) -> "tuple[Any, Any]":
    import pandas as pd
    import shapely

    from covjson_msgspec.pandas import to_pandas

    # The tidy frame puts x / y as columns for every point-like domain; promote
    # any index levels (t / z / composite) to columns so they survive to_json.
    frame = to_pandas(coverage)
    frame = (
        frame.reset_index(drop=True)
        if frame.index.name is None and not isinstance(frame.index, pd.MultiIndex)
        else frame.reset_index()
    )

    if "x" not in frame.columns or "y" not in frame.columns:
        msg = (
            "a point-like domain needs x and y coordinates to build geometry; "
            f"got columns {list(frame.columns)}"
        )
        raise ValueError(msg)

    # Carry a vertical coordinate into the geometry as a 3D point (POINT Z) when
    # the domain has one (a VerticalProfile, or a trajectory with a z component);
    # z also remains a column. GeoJSON allows the third coordinate.
    if "z" in frame.columns:
        geometry = shapely.points(
            frame["x"].to_numpy(), frame["y"].to_numpy(), frame["z"].to_numpy()
        )
    else:
        geometry = shapely.points(frame["x"].to_numpy(), frame["y"].to_numpy())

    return frame, geometry


def _trajectory_linestring_frame(
    coverage: Coverage, domain: Domain
) -> "tuple[Any, Any]":
    import pandas as pd
    import shapely

    composite = domain.axes["composite"]
    coords = composite.coordinates or ()

    if "x" not in coords or "y" not in coords:
        msg = (
            "a Trajectory needs x and y coordinates to build a LineString; "
            f"got composite coordinates {list(coords)}"
        )
        raise ValueError(msg)

    x_index = coords.index("x")
    y_index = coords.index("y")
    # A vertical component makes the path 3D (LINESTRING Z).
    z_index = coords.index("z") if "z" in coords else None
    positions: tuple[Any, ...] = composite.values or ()

    if len(positions) < 2:
        msg = (
            "a Trajectory needs at least two vertices to build a LineString "
            f"(got {len(positions)}); use the default points geometry instead"
        )
        raise ValueError(msg)

    def vertex(position: Any) -> tuple[float, ...]:
        if z_index is None:
            return (position[x_index], position[y_index])
        return (position[x_index], position[y_index], position[z_index])

    line = [vertex(position) for position in positions]

    # One feature for the whole path. Per-vertex measurements do not reduce to a
    # single row, so linestring mode keeps the geometry only (no range columns).
    return pd.DataFrame(index=[0]), [shapely.LineString(line)]


def _polygon_frame(coverage: Coverage, domain: Domain) -> "tuple[Any, Any]":
    import numpy as np
    import pandas as pd
    import shapely

    composite = domain.axes["composite"]
    coords = composite.coordinates or ("x", "y")
    x_index = coords.index("x") if "x" in coords else 0
    y_index = coords.index("y") if "y" in coords else 1
    # A vertical component in the ring positions becomes a 3D polygon (POLYGON Z).
    z_index = coords.index("z") if "z" in coords else None
    polygons = [
        _shapely_polygon(polygon, x_index, y_index, z_index, shapely)
        for polygon in (composite.values or ())
    ]

    # The element axes, in canonical (row-major) order: the polygons, then time
    # when it varies (a PolygonSeries / MultiPolygonSeries).
    dims = ["composite"]
    sizes = {"composite": len(polygons)}
    t_axis = domain.axes.get("t")
    times = list(t_axis.coordinate_values) if t_axis is not None else []

    if len(times) > 1:
        dims.append("t")
        sizes["t"] = len(times)

    columns: dict[str, Any] = {}

    for key, range_ in coverage.ranges.items():
        array = require_inline_ndarray(key, range_, "geopandas")
        columns[key] = range_column(array, dims, sizes)

    temporal = temporal_coordinates(domain)

    if times:
        present = ("t",) if "t" in dims else ()
        column = broadcast(np.asarray(times, dtype=object), present, dims, sizes)
        columns["t"] = maybe_datetime(list(column), "t" in temporal)

    z_axis = domain.axes.get("z")

    if z_axis is not None:
        z_values = list(z_axis.coordinate_values)
        columns["z"] = broadcast(np.asarray(z_values, dtype=object), (), dims, sizes)

    indices = broadcast(np.arange(len(polygons)), ("composite",), dims, sizes)
    geometry = np.array([polygons[index] for index in indices], dtype=object)

    return pd.DataFrame(columns), geometry


def _shapely_polygon(
    polygon: Any, x_index: int, y_index: int, z_index: int | None, shapely: Any
) -> Any:
    # A polygon is a sequence of rings; ring 0 is the exterior, the rest holes.
    # Include the vertical component per position when the axis carries one.
    def position_coords(position: Any) -> tuple[float, ...]:
        if z_index is None:
            return (position[x_index], position[y_index])
        return (position[x_index], position[y_index], position[z_index])

    rings = [[position_coords(position) for position in ring] for ring in polygon]
    shell, *holes = rings
    return shapely.Polygon(shell, holes)


def _crs(domain: Domain) -> str | None:
    # A horizontal reference system supplies the result CRS. A geographic system
    # honors a resolvable `id` and otherwise falls back to the lon/lat default
    # (see `_geographic_crs`). A projected system is identified by its `id` (an
    # EPSG / OGC CRS URI that pyproj resolves); pass it through, falling back to
    # unset when it carries none. Any other system leaves the CRS unset.
    for connection in domain.referencing:
        system = connection.system

        if isinstance(system, GeographicCRS):
            return _geographic_crs(system.id)

        if isinstance(system, ProjectedCRS) and system.id is not None:
            return system.id

    return None


def _geographic_crs(crs_id: str | None) -> str:
    # CoverageJSON's default geographic CRS is WGS84 longitude/latitude (OGC
    # CRS84), which is the lon/lat axis order the bridge builds x / y geometry
    # in. Honor an `id` that pyproj can resolve (mirroring the ProjectedCRS
    # branch); otherwise (no id, or an unresolvable placeholder like "crs") fall
    # back to that default rather than failing. Unlike EPSG:4326, whose authority
    # axis order is lat/lon, CRS84 matches the data, so it is the right fallback.
    if crs_id is not None:
        from pyproj import CRS
        from pyproj.exceptions import CRSError

        try:
            CRS.from_user_input(crs_id)
        except CRSError:
            pass
        else:
            return crs_id

    return _DEFAULT_GEOGRAPHIC_CRS
