"""xarray bridge: convert a `Coverage` into a CF-aware `xarray.Dataset`.

This bridge maps a coverage's domain and ranges onto xarray so the result can be
written straight to netCDF or Zarr (`Dataset.to_netcdf` / `Dataset.to_zarr`) and
read by the wider CF-aware ecosystem. A `CoverageCollection` maps to an
`xarray.DataTree` (one child node per member) via `to_datatree` / `from_datatree`.

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

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from covjson_msgspec.axis import Axis
from covjson_msgspec.coverage import Coverage, CoverageCollection, Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.i18n import I18n, i18n
from covjson_msgspec.parameter import (
    Category,
    CategoryEncoding,
    ObservedProperty,
    Parameter,
    Unit,
)
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import (
    GeographicCRS,
    ReferenceSystem,
    ReferenceSystemConnection,
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


def from_xarray(
    dataset: "xr.Dataset",
    *,
    domain_type: str | None = None,
    x: str | None = None,
    y: str | None = None,
    z: str | None = None,
    t: str | None = None,
    compact_regular: bool = True,
) -> Coverage:
    """Build a `Coverage` from an `xarray.Dataset`.

    Requires the ``xarray`` extra. This is the inverse of `to_xarray`; a dataset
    produced by `to_xarray` round-trips back to an equivalent coverage. The axis
    roles (x / y / z / t) are detected from CF attributes and common coordinate
    names, and can be pinned explicitly when detection is wrong or ambiguous.

    Some mappings are inherently lossy or heuristic: a scalar coordinate becomes
    a single-valued axis (the original size-1 dimension is not recovered), an
    evenly spaced numeric axis is compacted to ``start`` / ``stop`` / ``num``
    when ``compact_regular`` is set, the domain type is inferred when not given,
    and only ``units`` (continuous) or ``flag_values`` / ``flag_meanings``
    (categorical) are reconstructed into parameters.

    Parameters
    ----------
    dataset
        The dataset to convert. Its data variables become ranges and its
        coordinates become domain axes.
    domain_type
        The coverage's domain type. Inferred from the dataset (its
        ``domain_type`` attribute, then the axis layout) when omitted.
    x, y, z, t
        Names of the coordinates playing each role, overriding detection.
    compact_regular
        Compact an evenly spaced numeric axis to the regular ``start`` /
        ``stop`` / ``num`` form. Set ``False`` to always keep explicit values.

    Returns
    -------
    Coverage
        A coverage whose domain and ranges mirror the dataset.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> source = Coverage(
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
    >>> back = from_xarray(source.to_xarray())
    >>> back.domain.domain_type
    'Grid'
    >>> back.ranges["t"].values
    (1.0, 2.0, 3.0, 4.0)
    >>> back.domain.x.coordinate_values
    (0.0, 10.0)
    """
    try:
        import xarray  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    roles = _detect_roles(dataset, x=x, y=y, z=z, t=t)
    composite_dim, composite_roles = _detect_composite(dataset, roles)

    axes, dim_to_key = _build_axes(
        dataset, roles, composite_dim, composite_roles, compact_regular
    )

    effective_type = (
        domain_type
        or dataset.attrs.get("domain_type")
        or _infer_domain_type(dataset, roles, composite_dim)
    )
    domain = Domain(
        axes=axes,
        domain_type=effective_type,
        referencing=_build_referencing(dataset, roles),
    )

    ranges: dict[str, Range] = {}
    parameters: dict[str, Parameter] = {}

    for name, variable in dataset.data_vars.items():
        key = str(name)

        if _is_grid_mapping(variable) or key.endswith(("_bnds", "_bounds")):
            continue

        axis_names = tuple(dim_to_key.get(str(dim), str(dim)) for dim in variable.dims)
        ranges[key] = NdArray.from_numpy(variable.values, axis_names)

        if (parameter := _parameter_from_variable(key, variable)) is not None:
            parameters[key] = parameter

    coverage_id = dataset.attrs.get("id")

    return Coverage(
        domain=domain,
        ranges=ranges,
        id=None if coverage_id is None else str(coverage_id),
        parameters=parameters or None,
    )


def to_datatree(collection: CoverageCollection) -> "xr.DataTree":
    """Convert a `CoverageCollection` to an `xarray.DataTree`.

    Requires the ``xarray`` extra (and xarray with ``DataTree`` support). Each
    member coverage becomes a child node holding the `Dataset` that `to_xarray`
    would produce for it, named ``coverage_0``, ``coverage_1``, and so on in
    member order. Inheritance is applied first (via
    `CoverageCollection.resolved_coverages`) so every node is self-contained.

    Parameters
    ----------
    collection
        The collection to convert. Every member's domain must be an inline
        `Domain` and every range an inline `NdArray` (see `to_xarray`).

    Returns
    -------
    xarray.DataTree
        A tree whose child nodes are the member coverages as datasets.

    Raises
    ------
    ValueError
        If any member cannot be converted by `to_xarray` (e.g. a polygon
        domain, a URL-reference domain, or a non-inline range).

    Examples
    --------
    >>> from covjson_msgspec import (
    ...     Axis, Coverage, CoverageCollection, Domain, NdArray
    ... )
    >>> collection = CoverageCollection(
    ...     coverages=(
    ...         Coverage(
    ...             domain=Domain.point(
    ...                 x=Axis.listed((1.0,)), y=Axis.listed((2.0,))
    ...             ),
    ...             ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ...         ),
    ...     ),
    ...     domain_type="Point",
    ... )
    >>> tree = to_datatree(collection)
    >>> list(tree.children)
    ['coverage_0']
    >>> tree["coverage_0"]["t"].item()
    280.0
    """
    _require_datatree()

    import xarray as xr

    nodes = {
        f"coverage_{index}": to_xarray(coverage)
        for index, coverage in enumerate(collection.resolved_coverages())
    }

    return xr.DataTree.from_dict(nodes)


def from_datatree(
    tree: "xr.DataTree",
    *,
    domain_type: str | None = None,
    x: str | None = None,
    y: str | None = None,
    z: str | None = None,
    t: str | None = None,
    compact_regular: bool = True,
) -> CoverageCollection:
    """Build a `CoverageCollection` from an `xarray.DataTree`.

    Requires the ``xarray`` extra. This is the inverse of `to_datatree`: each
    child node holding data variables becomes a member coverage via
    `from_xarray`, in child order. A single-node tree whose root itself holds
    data is treated as a one-member collection.

    The result is *flat*: each coverage carries its own parameters and
    referencing rather than hoisting the shared fields onto the collection, so a
    round-trip preserves the data without reconstructing the original
    inheritance. The keyword seams are forwarded unchanged to `from_xarray` and
    apply to every node.

    Parameters
    ----------
    tree
        The tree to convert. Its data-bearing nodes become member coverages.
    domain_type
        The domain type for every member, inferred per node when omitted.
    x, y, z, t
        Names of the coordinates playing each role, overriding detection for
        every node.
    compact_regular
        Compact evenly spaced numeric axes to the regular form.

    Returns
    -------
    CoverageCollection
        A collection whose members mirror the tree's data-bearing nodes.

    Examples
    --------
    >>> from covjson_msgspec import (
    ...     Axis, Coverage, CoverageCollection, Domain, NdArray
    ... )
    >>> source = CoverageCollection(
    ...     coverages=(
    ...         Coverage(
    ...             domain=Domain.point(
    ...                 x=Axis.listed((1.0,)), y=Axis.listed((2.0,))
    ...             ),
    ...             ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ...         ),
    ...     ),
    ...     domain_type="Point",
    ... )
    >>> back = from_datatree(to_datatree(source))
    >>> back.coverages[0].ranges["t"].values
    (280.0,)
    """
    _require_datatree()

    def convert(dataset: "xr.Dataset") -> Coverage:
        return from_xarray(
            dataset,
            domain_type=domain_type,
            x=x,
            y=y,
            z=z,
            t=t,
            compact_regular=compact_regular,
        )

    coverages = [
        convert(dataset)
        for node in tree.children.values()
        if (dataset := node.to_dataset()).data_vars
    ]

    # A degenerate single-node tree carries its data on the root, not a child.
    if not coverages and (root := tree.to_dataset()).data_vars:
        coverages.append(convert(root))

    return CoverageCollection(coverages=tuple(coverages))


def _require_datatree() -> None:
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if not hasattr(xr, "DataTree"):  # pragma: no cover - version-dependent
        raise ModuleNotFoundError(
            "xarray.DataTree is required for collection conversion; "
            "upgrade to xarray>=2024.10"
        )


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


# Coordinate names commonly used for each role, lower-cased, as a detection
# fallback when CF attributes are absent.
_ROLE_NAMES: dict[str, frozenset[str]] = {
    "x": frozenset({"x", "lon", "longitude"}),
    "y": frozenset({"y", "lat", "latitude"}),
    "z": frozenset({"z", "depth", "height", "altitude", "level", "elevation"}),
    "t": frozenset({"t", "time"}),
}


def _detect_roles(
    dataset: "xr.Dataset",
    *,
    x: str | None,
    y: str | None,
    z: str | None,
    t: str | None,
) -> dict[str, str | None]:
    # Explicit overrides win; remaining roles are filled from CF attributes and
    # common coordinate names, never reusing a coordinate already assigned.
    roles: dict[str, str | None] = {"x": x, "y": y, "z": z, "t": t}
    taken = {name for name in roles.values() if name is not None}

    for name, coord in dataset.coords.items():
        role = _role_of(str(name), coord)

        if role is not None and roles[role] is None and str(name) not in taken:
            roles[role] = str(name)
            taken.add(str(name))

    return roles


def _role_of(name: str, coord: "xr.DataArray") -> str | None:
    standard_name = str(coord.attrs.get("standard_name", "")).lower()
    units = str(coord.attrs.get("units", "")).lower()
    lowered = name.lower()

    if (
        standard_name == "longitude"
        or units.startswith("degrees_e")
        or lowered in _ROLE_NAMES["x"]
    ):
        return "x"

    if (
        standard_name == "latitude"
        or units.startswith("degrees_n")
        or lowered in _ROLE_NAMES["y"]
    ):
        return "y"

    if (
        standard_name in {"depth", "height", "altitude"}
        or "positive" in coord.attrs
        or lowered in _ROLE_NAMES["z"]
    ):
        return "z"

    if _is_time(coord) or lowered in _ROLE_NAMES["t"]:
        return "t"

    return None


def _is_time(coord: "xr.DataArray") -> bool:
    import numpy as np

    if np.issubdtype(coord.dtype, np.datetime64):
        return True

    values = np.atleast_1d(coord.values)

    return (
        values.dtype == object
        and values.size > 0
        and hasattr(values.flat[0], "calendar")
    )


def _detect_composite(
    dataset: "xr.Dataset",
    roles: dict[str, str | None],
) -> tuple[str | None, set[str]]:
    # A composite (e.g. trajectory) axis shows up as several role coordinates
    # that are non-dimension coordinates sharing one dimension.
    groups: dict[str, set[str]] = {}

    for role, name in roles.items():
        if name is None or name not in dataset.coords:
            continue

        coord = dataset.coords[name]

        if coord.ndim == 1 and str(coord.dims[0]) != name:
            groups.setdefault(str(coord.dims[0]), set()).add(role)

    for dim, grouped in groups.items():
        if len(grouped) >= 2:
            return dim, grouped

    return None, set()


def _build_axes(
    dataset: "xr.Dataset",
    roles: dict[str, str | None],
    composite_dim: str | None,
    composite_roles: set[str],
    compact_regular: bool,
) -> tuple[dict[str, Axis], dict[str, str]]:
    axes: dict[str, Axis] = {}
    # Maps a dataset dimension to the CoverageJSON axis key it became.
    dim_to_key: dict[str, str] = {}

    if composite_dim is not None:
        order = tuple(role for role in ("t", "x", "y", "z") if role in composite_roles)
        columns = [_coord_to_list(dataset[roles[role]]) for role in order]
        axes["composite"] = Axis(
            data_type="tuple",
            coordinates=order,
            values=tuple(zip(*columns, strict=True)),
        )
        dim_to_key[composite_dim] = "composite"

    for role in ("x", "y", "z", "t"):
        name = roles[role]

        if name is None or role in composite_roles:
            continue

        coord = dataset[name]

        if coord.ndim == 0:
            axes[role] = Axis.listed((_scalar(coord),))
        elif coord.ndim == 1 and str(coord.dims[0]) == name:
            axes[role] = _axis_from_coord(coord, compact_regular)
            dim_to_key[name] = role

    # Any remaining dimension becomes an axis under its own name so no range
    # data is orphaned.
    for dim in dataset.sizes:
        key = str(dim)

        if key in dim_to_key:
            continue

        if key in dataset.coords:
            axes[key] = _axis_from_coord(dataset[key], compact_regular)
        else:
            axes[key] = Axis.listed(tuple(range(dataset.sizes[dim])))

        dim_to_key[key] = key

    return axes, dim_to_key


def _axis_from_coord(coord: "xr.DataArray", compact_regular: bool) -> Axis:
    if _is_time(coord):
        return Axis.listed(tuple(_time_to_iso(coord)))

    import numpy as np

    values = np.atleast_1d(coord.values).tolist()

    if compact_regular and _is_regular(values):
        return Axis.regular(float(values[0]), float(values[-1]), len(values))

    return Axis.listed(tuple(values))


def _is_regular(values: list[Any]) -> bool:
    if len(values) < 2:
        return False

    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError):
        return False

    step = numbers[1] - numbers[0]

    if step == 0:
        return False

    return all(
        math.isclose(numbers[i + 1] - numbers[i], step, rel_tol=1e-9, abs_tol=1e-12)
        for i in range(len(numbers) - 1)
    )


def _coord_to_list(coord: "xr.DataArray") -> list[Any]:
    if _is_time(coord):
        return _time_to_iso(coord)

    import numpy as np

    return list(np.atleast_1d(coord.values).tolist())


def _scalar(coord: "xr.DataArray") -> Any:
    if _is_time(coord):
        return _time_to_iso(coord)[0]

    return coord.values.item()


def _time_to_iso(coord: "xr.DataArray") -> list[Any]:
    import numpy as np

    result: list[Any] = []

    for value in np.atleast_1d(coord.values):
        if isinstance(value, np.datetime64):
            if np.isnat(value):
                result.append(None)
            else:
                # datetime64[ns] exceeds datetime's microsecond resolution, so
                # narrow before converting to a Python datetime.
                moment = value.astype("datetime64[us]").astype(datetime)
                result.append(moment.isoformat() + "Z")
        elif hasattr(value, "isoformat"):
            # A cftime datetime (non-standard calendar).
            result.append(value.isoformat())
        else:
            result.append(None if value is None else str(value))

    return result


def _calendar(coord: "xr.DataArray") -> str:
    import numpy as np

    values = np.atleast_1d(coord.values)

    if values.size > 0 and hasattr(values.flat[0], "calendar"):
        return str(values.flat[0].calendar)

    return "Gregorian"


def _build_referencing(
    dataset: "xr.Dataset",
    roles: dict[str, str | None],
) -> tuple[ReferenceSystemConnection, ...]:
    connections: list[ReferenceSystemConnection] = []

    if roles["x"] is not None and roles["y"] is not None:
        crs_id = None

        if "crs" in dataset.coords:
            crs_id = dataset["crs"].attrs.get("reference_system_id")

        connections.append(
            ReferenceSystemConnection(
                coordinates=("x", "y"), system=GeographicCRS(id=crs_id)
            )
        )

    if roles["z"] is not None:
        connections.append(
            ReferenceSystemConnection(coordinates=("z",), system=VerticalCRS())
        )

    if roles["t"] is not None:
        connections.append(
            ReferenceSystemConnection(
                coordinates=("t",),
                system=TemporalRS(calendar=_calendar(dataset[roles["t"]])),
            )
        )

    return tuple(connections)


def _infer_domain_type(
    dataset: "xr.Dataset",
    roles: dict[str, str | None],
    composite_dim: str | None,
) -> str | None:
    if composite_dim is not None:
        return "Trajectory"

    def is_dim(role: str) -> bool:
        name = roles[role]
        return name is not None and name in dataset.sizes

    if is_dim("x") and is_dim("y"):
        return "Grid"

    if roles["x"] is not None and roles["y"] is not None:
        if is_dim("t") and not is_dim("z"):
            return "PointSeries"

        if is_dim("z") and not is_dim("t"):
            return "VerticalProfile"

        if not is_dim("t") and not is_dim("z"):
            return "Point"

    return None


def _is_grid_mapping(variable: "xr.DataArray") -> bool:
    return "grid_mapping_name" in variable.attrs


def _parameter_from_variable(name: str, variable: "xr.DataArray") -> Parameter | None:
    import numpy as np

    attrs = variable.attrs
    label = str(attrs.get("long_name") or attrs.get("standard_name") or name)

    if "flag_values" in attrs and "flag_meanings" in attrs:
        codes = [int(code) for code in np.atleast_1d(attrs["flag_values"]).tolist()]
        meanings = str(attrs["flag_meanings"]).split()
        categories = tuple(
            Category(id=str(code), label=i18n(meaning.replace("_", " ")))
            for code, meaning in zip(codes, meanings, strict=False)
        )
        observed = ObservedProperty(label=i18n(label), categories=categories)

        return Parameter.categorical(observed, {str(code): code for code in codes})

    if (units := attrs.get("units")) is not None:
        observed = ObservedProperty(label=i18n(label))

        return Parameter.continuous(observed, Unit(symbol=str(units)))

    # Without a unit there is nothing to put in a (unit-required) continuous
    # parameter, so the range is emitted without a parameter description.
    return None
