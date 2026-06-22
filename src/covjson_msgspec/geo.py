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
  vertex (preserving each vertex's measurements), not a single ``LineString``.
- A Polygon / PolygonSeries domain becomes one feature (repeated over ``t`` for a
  series); a MultiPolygon / MultiPolygonSeries domain becomes one feature per
  polygon. The ``composite`` axis supplies the ``Polygon`` geometry.
- A geographic reference system tags the result as ``EPSG:4326`` (CoverageJSON's
  default geographic CRS is longitude/latitude on WGS84).

A multi-dimensional gridded domain (Grid) is degenerately emitted as one point
feature per cell; the xarray bridge is the better fit for gridded data.

A `CoverageCollection` is converted by concatenating its resolved members into one
frame, with a leading ``coverage`` column identifying each member (its ``id`` when
set, otherwise its position). Unlike the pandas bridge's index level, this is a
plain column so it survives ``to_json`` into each feature's ``properties``.

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

import json
from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec.coverage import Coverage, CoverageCollection
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import GeographicCRS

if TYPE_CHECKING:
    import geopandas as gpd

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "geopandas and shapely are required for this conversion; "
    "install covjson-msgspec[geo]"
)

# Domain types whose geometry comes from the composite polygon axis.
_POLYGON_DOMAIN_TYPES = frozenset(
    {"Polygon", "PolygonSeries", "MultiPolygon", "MultiPolygonSeries"}
)


def to_geopandas(obj: Coverage | CoverageCollection) -> "gpd.GeoDataFrame":
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

    Returns
    -------
    geopandas.GeoDataFrame
        A frame of the parameter and coordinate columns with a ``geometry``
        column; ``EPSG:4326`` when the domain has a geographic reference system.
        For a collection, the member frames concatenated under a leading
        ``coverage`` column.

    Raises
    ------
    ValueError
        If a domain is a URL reference, a point-like domain lacks ``x`` / ``y``
        coordinates, or a range is not an inline `NdArray`.
    """
    try:
        import geopandas as gpd
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if isinstance(obj, CoverageCollection):
        return _collection_to_geopandas(obj, gpd)

    return _coverage_to_geopandas(obj, gpd)


def to_geojson(coverage: Coverage | CoverageCollection) -> dict[str, Any]:
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

    Returns
    -------
    dict
        A GeoJSON ``FeatureCollection`` as a plain (JSON-compatible) mapping.

    Raises
    ------
    ValueError
        Propagated from `to_geopandas` (URL domain, missing ``x`` / ``y``, or a
        non-inline range).

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
    return cast("dict[str, Any]", json.loads(to_geopandas(coverage).to_json()))


def _coverage_to_geopandas(coverage: Coverage, gpd: Any) -> "gpd.GeoDataFrame":
    if not isinstance(domain := coverage.domain, Domain):
        msg = (
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to geopandas"
        )
        raise ValueError(msg)

    domain_type = domain.domain_type or coverage.domain_type

    if domain_type in _POLYGON_DOMAIN_TYPES:
        frame, geometry = _polygon_frame(coverage, domain)
    else:
        frame, geometry = _point_frame(coverage, domain)

    gdf = gpd.GeoDataFrame(frame, geometry=geometry, crs=_crs(domain))

    if domain_type is not None:
        gdf.attrs["domain_type"] = domain_type

    if coverage.id is not None:
        gdf.attrs["id"] = coverage.id

    return gdf


def _collection_to_geopandas(
    collection: CoverageCollection, gpd: Any
) -> "gpd.GeoDataFrame":
    import pandas as pd

    # Resolve first so each member carries the collection's inherited parameters
    # and referencing (the latter is what tags temporal axes and sets the CRS).
    resolved = collection.resolved_coverages()

    if not resolved:
        return gpd.GeoDataFrame()

    frames = []

    for index, coverage in enumerate(resolved):
        gdf = _coverage_to_geopandas(coverage, gpd)
        # Key each member by its id when set, falling back to its position. A
        # leading plain column (not an index level) survives to_json into each
        # feature's properties.
        gdf.insert(0, "coverage", coverage.id if coverage.id is not None else index)
        frames.append(gdf)

    # Concatenate as plain frames, then rebuild geometry with a single explicit
    # CRS (members of a collection share referencing, so it is uniform).
    combined = pd.concat(
        [pd.DataFrame(frame) for frame in frames], ignore_index=True
    )
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

    geometry = shapely.points(frame["x"].to_numpy(), frame["y"].to_numpy())
    return frame, geometry


def _polygon_frame(coverage: Coverage, domain: Domain) -> "tuple[Any, Any]":
    import numpy as np
    import pandas as pd
    import shapely

    from covjson_msgspec.pandas import (
        _broadcast,
        _maybe_datetime,
        _range_column,
        _temporal_coordinates,
    )

    composite = domain.axes["composite"]
    coords = composite.coordinates or ("x", "y")
    x_index = coords.index("x") if "x" in coords else 0
    y_index = coords.index("y") if "y" in coords else 1
    polygons = [
        _shapely_polygon(polygon, x_index, y_index, shapely)
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
        if not isinstance(range_, NdArray):
            msg = (
                f"range {key!r} is not an inline NdArray (got "
                f"{type(range_).__name__}); resolve URL ranges and assemble "
                "TiledNdArray tiles before converting to geopandas"
            )
            raise ValueError(msg)

        columns[key] = _range_column(range_, dims, sizes)

    temporal = _temporal_coordinates(domain)

    if times:
        present = ("t",) if "t" in dims else ()
        column = _broadcast(np.asarray(times, dtype=object), present, dims, sizes)
        columns["t"] = _maybe_datetime(list(column), "t" in temporal)

    z_axis = domain.axes.get("z")

    if z_axis is not None:
        z_values = list(z_axis.coordinate_values)
        columns["z"] = _broadcast(np.asarray(z_values, dtype=object), (), dims, sizes)

    indices = _broadcast(np.arange(len(polygons)), ("composite",), dims, sizes)
    geometry = np.array([polygons[index] for index in indices], dtype=object)

    return pd.DataFrame(columns), geometry


def _shapely_polygon(polygon: Any, x_index: int, y_index: int, shapely: Any) -> Any:
    # A polygon is a sequence of rings; ring 0 is the exterior, the rest holes.
    rings = [
        [(position[x_index], position[y_index]) for position in ring]
        for ring in polygon
    ]
    shell, *holes = rings
    return shapely.Polygon(shell, holes)


def _crs(domain: Domain) -> str | None:
    # CoverageJSON's default geographic CRS is longitude/latitude on WGS84; map a
    # geographic reference system to EPSG:4326 and leave anything else unset.
    for connection in domain.referencing:
        if isinstance(connection.system, GeographicCRS):
            return "EPSG:4326"

    return None
