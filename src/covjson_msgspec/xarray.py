"""xarray bridge: convert a `Coverage` into a CF-aware `xarray.Dataset`.

This bridge maps a coverage's domain and ranges onto xarray so the result can be
written straight to netCDF or Zarr (`Dataset.to_netcdf` / `Dataset.to_zarr`) and
read by the wider CF-aware ecosystem.

Mapping
-------
- Each parameter range becomes a data variable, with its ``axisNames`` as dims.
- An independent (multi-valued) primitive axis becomes a dimension coordinate;
  a single-valued axis becomes a scalar coordinate (the size-1 dimension is
  dropped, a documented round-trip loss).
- A composite ``tuple`` axis (e.g. a trajectory) becomes one dimension with one
  non-dimension coordinate per tuple component (the tuples are transposed).
- Referencing drives CF attributes: a temporal system parses the coordinate to
  ``datetime64`` (cftime for non-standard calendars), a geographic system tags
  longitude/latitude with their ``standard_name`` / ``units`` and adds a
  ``grid_mapping`` variable, and a vertical system sets ``positive`` up/down.
- A continuous parameter contributes ``units`` (and ``standard_name`` /
  ``long_name``); a categorical parameter contributes CF ``flag_values`` /
  ``flag_meanings``.

Polygon domains carry vector geometry rather than a grid, so they belong in the
geopandas bridge; `to_xarray` rejects them.

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec.coverage import Coverage
from covjson_msgspec.domain import Domain
from covjson_msgspec.i18n import I18n
from covjson_msgspec.parameter import Category, CategoryEncoding, Parameter, Unit
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import (
    GeographicCRS,
    ReferenceSystem,
    TemporalRS,
    VerticalCRS,
)

if TYPE_CHECKING:
    import numpy as np
    import xarray as xr

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "xarray, numpy, and cftime are required for this conversion; "
    "install covjson-msgspec[xarray]"
)

# Polygon domains are vector geometry, not gridded arrays: they route to the
# geopandas bridge instead of xarray.
_POLYGON_DOMAIN_TYPES = frozenset(
    {"Polygon", "PolygonSeries", "MultiPolygon", "MultiPolygonSeries"}
)

# Calendars whose dates fit numpy's datetime64; anything else needs cftime.
_STANDARD_CALENDARS = frozenset({"gregorian", "standard", "proleptic_gregorian"})

# The CoverageJSON ordering of a geographic system's coordinates.
_GEOGRAPHIC_ROLES = ("longitude", "latitude", "height")

# A coordinate/data-variable spec in xarray's ``(dims, data, attrs)`` form.
_Variable = tuple[Any, Any, dict[str, Any]]


def to_xarray(coverage: Coverage) -> "xr.Dataset":
    """Convert a `Coverage` to a CF-aware `xarray.Dataset`.

    Requires the ``xarray`` extra. Each parameter range becomes a data variable
    and each domain axis a coordinate (see the module docstring for the full
    mapping). A coverage taken from a `CoverageCollection` should be obtained via
    `CoverageCollection.resolved_coverages` first, so its parameters and
    referencing are populated.

    Parameters
    ----------
    coverage
        The coverage to convert. Its ``domain`` must be an inline `Domain` (not a
        URL reference) and every range an inline `NdArray`.

    Returns
    -------
    xarray.Dataset
        A dataset whose data variables are the coverage's parameters and whose
        coordinates are the domain's axes, annotated with CF attributes.

    Raises
    ------
    ValueError
        If the domain is a URL reference, the domain type is a polygon type
        (use the geopandas bridge), or a range is not an inline `NdArray`.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.grid(
    ...         x=Axis.regular(0.0, 10.0, 2), y=Axis.regular(0.0, 5.0, 2)
    ...     ),
    ...     ranges={
    ...         "t": NdArray(
    ...             data_type="float",
    ...             values=(1.0, 2.0, 3.0, 4.0),
    ...             shape=(2, 2),
    ...             axis_names=("y", "x"),
    ...         )
    ...     },
    ... )
    >>> ds = to_xarray(cov)
    >>> ds["t"].dims
    ('y', 'x')
    >>> ds["x"].values.tolist()
    [0.0, 10.0]
    >>> ds.attrs["domain_type"]
    'Grid'
    """
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if not isinstance(domain := coverage.domain, Domain):
        raise ValueError(
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to xarray"
        )

    domain_type = domain.domain_type or coverage.domain_type

    if domain_type in _POLYGON_DOMAIN_TYPES:
        raise ValueError(
            f"{domain_type!r} is a polygon domain (vector geometry); use the "
            "geopandas bridge instead of xarray"
        )

    systems = _coordinate_systems(domain)
    geo_roles = _geographic_roles(domain)

    coords = _build_coords(domain, systems, geo_roles)
    data_vars = {
        key: _data_variable(key, range_, coverage.parameters)
        for key, range_ in coverage.ranges.items()
    }

    # A geographic system contributes a CF grid-mapping variable that the data
    # variables point at via their ``grid_mapping`` attribute.
    if (crs := _crs_coordinate(domain)) is not None:
        coords["crs"] = crs

        for _dims, _data, var_attrs in data_vars.values():
            var_attrs.setdefault("grid_mapping", "crs")

    attrs: dict[str, Any] = {"Conventions": "CF-1.10"}

    if domain_type is not None:
        attrs["domain_type"] = domain_type

    if coverage.id is not None:
        attrs["id"] = coverage.id

    return xr.Dataset(data_vars=data_vars, coords=coords, attrs=attrs)


def _coordinate_systems(domain: Domain) -> dict[str, ReferenceSystem]:
    # Map each coordinate identifier to the reference system that governs it.
    return {
        coordinate: connection.system
        for connection in domain.referencing
        for coordinate in connection.coordinates
    }


def _geographic_roles(domain: Domain) -> dict[str, str]:
    # Map the coordinates of each geographic system to longitude/latitude/height
    # by their CoverageJSON ordering.
    return {
        coordinate: role
        for connection in domain.referencing
        if isinstance(connection.system, GeographicCRS)
        for coordinate, role in zip(
            connection.coordinates, _GEOGRAPHIC_ROLES, strict=False
        )
    }


def _build_coords(
    domain: Domain,
    systems: dict[str, ReferenceSystem],
    geo_roles: dict[str, str],
) -> dict[str, _Variable]:
    coords: dict[str, _Variable] = {}

    for key, axis in domain.axes.items():
        if axis.data_type == "polygon":
            raise ValueError("polygon axes are not supported by the xarray bridge")

        if axis.data_type == "tuple":
            # Composite axis: transpose the tuples into one non-dimension
            # coordinate per component, all along the single dimension ``key``.
            # A "tuple" axis holds tuple-valued coordinates by construction.
            components = axis.coordinates or ()
            rows = cast("tuple[tuple[Any, ...], ...]", axis.values or ())

            for index, coordinate in enumerate(components):
                column = [row[index] for row in rows]
                coords[coordinate] = _coordinate(
                    coordinate, key, column, systems, geo_roles, scalar=False
                )
        else:
            values = list(axis.coordinate_values)
            coords[key] = _coordinate(
                key, key, values, systems, geo_roles, scalar=len(values) == 1
            )

    return coords


def _coordinate(
    coordinate: str,
    dim: str,
    column: list[Any],
    systems: dict[str, ReferenceSystem],
    geo_roles: dict[str, str],
    *,
    scalar: bool,
) -> _Variable:
    import numpy as np

    system = systems.get(coordinate)
    role = geo_roles.get(coordinate)
    attrs: dict[str, Any] = {}

    if isinstance(system, TemporalRS):
        data = _parse_times(column, system.calendar)
    else:
        if role == "longitude":
            attrs.update(standard_name="longitude", units="degrees_east")
        elif role == "latitude":
            attrs.update(standard_name="latitude", units="degrees_north")
        elif role == "height":
            attrs.update(standard_name="height", positive="up")
        elif isinstance(system, VerticalCRS):
            attrs.update(_vertical_attrs(system))

        data = np.asarray(column)

    if scalar:
        # Drop the size-1 dimension: a single-valued axis becomes a scalar coord.
        return ((), data[0], attrs)

    return (dim, data, attrs)


def _parse_times(column: list[Any], calendar: str) -> "np.ndarray[Any, np.dtype[Any]]":
    import numpy as np

    normalized = calendar.rsplit("/", 1)[-1].lower()
    # ISO 8601 may carry a trailing "Z"; numpy treats naive times as UTC.
    cleaned = [
        None if value is None else str(value).removesuffix("Z") for value in column
    ]

    if normalized in _STANDARD_CALENDARS:
        try:
            return np.array(cleaned, dtype="datetime64[ns]")
        except (ValueError, OverflowError):
            # Dates outside numpy's nanosecond range fall through to cftime.
            pass

    parsed = [
        None if value is None else _to_cftime(value, normalized) for value in cleaned
    ]

    return np.array(parsed)


def _to_cftime(iso: str, calendar: str) -> Any:
    import cftime

    moment = datetime.fromisoformat(iso)

    return cftime.datetime(
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
        moment.microsecond,
        calendar=calendar,
    )


def _vertical_attrs(system: VerticalCRS) -> dict[str, str]:
    text = " ".join(filter(None, (system.id, _english(system.description)))).lower()

    if "depth" in text:
        return {"standard_name": "depth", "positive": "down"}

    if "height" in text or "altitude" in text:
        return {"standard_name": "height", "positive": "up"}

    return {}


def _crs_coordinate(domain: Domain) -> _Variable | None:
    for connection in domain.referencing:
        if isinstance(system := connection.system, GeographicCRS):
            attrs: dict[str, Any] = {"grid_mapping_name": "latitude_longitude"}

            if system.id is not None:
                attrs["reference_system_id"] = system.id

            return ((), 0, attrs)

    return None


def _data_variable(
    key: str,
    range_: Any,
    parameters: dict[str, Parameter] | None,
) -> _Variable:
    if not isinstance(range_, NdArray):
        raise ValueError(
            f"range {key!r} is not an inline NdArray (got "
            f"{type(range_).__name__}); resolve URL ranges and assemble "
            "TiledNdArray tiles before converting to xarray"
        )

    parameter = parameters.get(key) if parameters is not None else None

    return (range_.axis_names, range_.to_numpy(), _variable_attrs(parameter))


def _variable_attrs(parameter: Parameter | None) -> dict[str, Any]:
    if parameter is None:
        return {}

    observed = parameter.observed_property
    attrs: dict[str, Any] = {}

    if long_name := (_english(parameter.label) or _english(observed.label)):
        attrs["long_name"] = long_name

    if (standard_name := _standard_name(observed.id)) is not None:
        attrs["standard_name"] = standard_name

    if parameter.unit is not None and (units := _unit_symbol(parameter.unit)):
        attrs["units"] = units

    if observed.categories is not None and parameter.category_encoding is not None:
        values, meanings = _flags(observed.categories, parameter.category_encoding)

        if values:
            attrs["flag_values"] = values
            attrs["flag_meanings"] = meanings

    return attrs


def _standard_name(identifier: str | None) -> str | None:
    if identifier is None:
        return None

    # An observedProperty id is typically a URI; CF wants the bare term, so take
    # the last path or fragment segment.
    return identifier.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _unit_symbol(unit: Unit) -> str | None:
    if isinstance(unit.symbol, str):
        return unit.symbol

    if unit.symbol is not None:
        return unit.symbol.value

    return None


def _flags(
    categories: tuple[Category, ...],
    encoding: CategoryEncoding,
) -> tuple[tuple[int, ...], str]:
    values: list[int] = []
    meanings: list[str] = []

    for category in categories:
        code = encoding.get(category.id)

        if code is None:
            continue

        # CF flag_values are 1:1 with meanings; a multi-code category keeps its
        # first code (a documented simplification).
        values.append(code[0] if isinstance(code, tuple) else code)
        meanings.append(_flag_meaning(category.label))

    return tuple(values), " ".join(meanings)


def _flag_meaning(label: I18n) -> str:
    # CF flag_meanings are whitespace-delimited tokens, so collapse internal
    # whitespace in the label to underscores.
    return "_".join((_english(label) or "").split())


def _english(text: I18n | None) -> str | None:
    if not text:
        return None

    return text.get("en") or next(iter(text.values()))
