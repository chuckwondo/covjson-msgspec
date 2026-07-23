"""xarray bridge: convert a `Coverage` into a CF-aware `xarray.Dataset`.

This bridge maps a coverage's domain and ranges onto xarray so the result can be
written straight to netCDF or Zarr (`Dataset.to_netcdf` / `Dataset.to_zarr`) and
read by the wider CF-aware ecosystem. A `CoverageCollection` maps to an
`xarray.DataTree` (one child node per member) via `to_datatree` / `from_datatree`.

Mapping
-------
- Each parameter range becomes a data variable, with its ``axisNames`` as dims.
- An individual (multi-valued) primitive axis becomes a dimension coordinate;
  a single-valued axis becomes a scalar coordinate (the size-1 dimension is
  dropped, a documented round-trip loss).
- A composite ``tuple`` axis (e.g. a trajectory) becomes one dimension with one
  non-dimension coordinate per tuple component (the tuples are transposed).
- Referencing drives CF attributes: a temporal system parses the coordinate to
  ``datetime64`` (cftime for non-standard calendars), a geographic system tags
  longitude/latitude with their ``standard_name`` / ``units``, a horizontal
  (geographic or projected) system adds a ``grid_mapping`` variable carrying its
  ``id``, and a vertical system sets ``positive`` up/down.
- A continuous parameter contributes ``units`` (and ``standard_name`` /
  ``long_name``); a categorical parameter contributes CF ``flag_values`` /
  ``flag_meanings``.

Polygon domains carry vector geometry rather than a grid, so they belong in the
geopandas bridge; `to_xarray` rejects them.

Spec: [Coverage objects](https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects).
"""

from __future__ import annotations

# This bridge is internal glue over dynamically-typed third-party libraries
# (xarray / numpy / cftime) whose stubs are incomplete (cftime has none), so
# basedpyright's reportUnknown* and reportMissingTypeStubs rules are relaxed
# here. The public functions stay safe: their signatures are explicitly typed
# and mypy strict guards them, so those rules never fire on the user-facing
# surface.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false
import math
import re
from collections.abc import Iterator, Mapping, MutableMapping, Sequence, Set
from datetime import UTC, datetime
from itertools import chain, pairwise
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, NoReturn

from msgspec import UNSET

from covjson_msgspec._bridging import (
    POLYGON_DOMAIN_TYPES,
    STANDARD_CALENDARS,
    composite_columns,
    coordinate_systems,
    require_inline_ndarray,
)
from covjson_msgspec._i18n import display
from covjson_msgspec.axis import Axis
from covjson_msgspec.coverage import Coverage, CoverageCollection, Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.i18n import I18n, i18n
from covjson_msgspec.parameter import (
    Category,
    CategoryEncoding,
    ObservedProperty,
    Parameter,
    Symbol,
    Unit,
)
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import (
    GeographicCRS,
    ProjectedCRS,
    ReferenceSystem,
    ReferenceSystemConnection,
    ResolvedReferenceSystem,
    TemporalRS,
    VerticalCRS,
)

if TYPE_CHECKING:
    import numpy.typing as npt
    import xarray as xr

# Raised (as the message) when the bridge is used without its dependencies.
_INSTALL_HINT = (
    "xarray, numpy, and cftime are required for this conversion; "
    "install covjson-msgspec[xarray]"
)

# The CoverageJSON ordering of a geographic system's coordinates.
_GEOGRAPHIC_ROLES = ("longitude", "latitude", "height")

# A coordinate/data-variable spec in xarray's ``(dims, data, attrs)`` form: the
# dimension name(s) (a scalar coord uses the empty tuple), the array/scalar data
# (heterogeneous, hence Any), and the CF attributes.
_Variable = tuple[str | tuple[str, ...], Any, MutableMapping[str, Any]]


def to_xarray(coverage: Coverage) -> xr.Dataset:
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
        (use the geopandas bridge), a composite ``tuple`` axis has a value that
        is not a tuple matching its coordinate identifiers, or a range is not an
        inline `NdArray`.

    Examples
    --------
    Decode a CoverageJSON document and convert it via its `to_xarray` method (the
    module-level `to_xarray` function is equivalent). Each individual axis becomes
    a dimension coordinate, the range becomes a data variable over those
    dimensions, and the domain type is recorded in the attributes:

    >>> from covjson_msgspec import decode_coverage
    >>> cov = decode_coverage('''
    ... {
    ...   "type": "Coverage",
    ...   "domain": {
    ...     "type": "Domain",
    ...     "domainType": "Grid",
    ...     "axes": {
    ...       "x": {"start": 0.0, "stop": 10.0, "num": 2},
    ...       "y": {"start": 0.0, "stop": 5.0, "num": 2}
    ...     }
    ...   },
    ...   "ranges": {
    ...     "t": {
    ...       "type": "NdArray",
    ...       "dataType": "float",
    ...       "axisNames": ["y", "x"],
    ...       "shape": [2, 2],
    ...       "values": [1.0, 2.0, 3.0, 4.0]
    ...     }
    ...   }
    ... }
    ... ''')
    >>> cov.to_xarray()
    <xarray.Dataset> Size: ...B
    Dimensions:  (y: 2, x: 2)
    Coordinates:
      * y        (y) float64 ...B 0.0 5.0
      * x        (x) float64 ...B 0.0 10.0
    Data variables:
        t        (y, x) float64 ...B 1.0 2.0 3.0 4.0
    Attributes:
        Conventions:  CF-1.10
        domain_type:  Grid
    """
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if not isinstance(domain := coverage.domain, Domain):
        msg = (
            "coverage.domain is a URL reference; resolve it to a Domain before "
            "converting to xarray"
        )
        raise ValueError(msg)

    if (domain_type := coverage.effective_domain_type) in POLYGON_DOMAIN_TYPES:
        msg = (
            f"{domain_type!r} is a polygon domain (vector geometry); use the "
            "geopandas bridge instead of xarray"
        )
        raise ValueError(msg)

    coords, data_vars = _build_variables(coverage, domain)
    attrs: dict[str, Any] = {"Conventions": "CF-1.10"}

    if domain_type is not None:
        attrs["domain_type"] = domain_type

    if coverage.id is not None:
        attrs["id"] = coverage.id

    return xr.Dataset(data_vars=data_vars, coords=coords, attrs=attrs)


def from_xarray(
    dataset: xr.Dataset,
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
    CF bounds variables (a coordinate's ``bounds`` attribute, or a ``*_bnds`` /
    ``*_bounds`` suffix) are dropped and their vertex dimension is not an axis,
    and only ``units`` (continuous) or ``flag_values`` / ``flag_meanings``
    (categorical) are reconstructed into parameters. A curvilinear grid (2-D
    latitude/longitude) has no CoverageJSON axis form and is rejected.

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

    Raises
    ------
    ValueError
        If a horizontal coordinate is 2-D (a curvilinear grid), or the dataset is
        not a separable grid (a dimension hosting two role coordinates, or a
        dimension whose name collides with a role's axis key), which CoverageJSON
        has no axis form for.

    Examples
    --------
    Build a dataset directly and convert it. Here the scalar ``x`` / ``y``
    coordinates yield a Point domain, each scalar becoming a single-valued axis:

    >>> import msgspec
    >>> import xarray as xr
    >>> from covjson_msgspec import encode
    >>> ds = xr.Dataset({"v": 280.0}, coords={"x": 1.0, "y": 2.0})
    >>> ds
    <xarray.Dataset> Size: ...B
    Dimensions:  ()
    Coordinates:
        x        float64 ...B 1.0
        y        float64 ...B 2.0
    Data variables:
        v        float64 ...B 280.0

    The resulting coverage, as CoverageJSON (the wire form, unset fields omitted):

    >>> print(msgspec.json.format(encode(from_xarray(ds)), indent=2).decode())
    {
      "type": "Coverage",
      "domain": {
        "type": "Domain",
        "axes": {
          "x": {
            "values": [
              1.0
            ]
          },
          "y": {
            "values": [
              2.0
            ]
          }
        },
        "domainType": "Point",
        "referencing": [
          {
            "coordinates": [
              "x",
              "y"
            ],
            "system": {
              "type": "GeographicCRS"
            }
          }
        ]
      },
      "ranges": {
        "v": {
          "type": "NdArray",
          "dataType": "float",
          "values": [
            280.0
          ]
        }
      }
    }
    """
    try:
        import xarray  # noqa: F401  # pyright: ignore[reportUnusedImport]
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

    ranges, parameters = _build_ranges(dataset, dim_to_key)
    coverage_id = dataset.attrs.get("id")

    return Coverage(
        domain=domain,
        ranges=ranges,
        id=None if coverage_id is None else str(coverage_id),
        parameters=parameters or UNSET,
    )


def to_datatree(collection: CoverageCollection) -> xr.DataTree:
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
    Decode a CoverageJSON collection and convert it via its `to_datatree` method
    (the module-level `to_datatree` function is equivalent). Each member coverage
    becomes a child node (named in member order) holding the `Dataset` that
    `to_xarray` produces for it:

    >>> from covjson_msgspec import decode_coverage_collection
    >>> collection = decode_coverage_collection('''
    ... {
    ...   "type": "CoverageCollection",
    ...   "domainType": "Point",
    ...   "coverages": [
    ...     {
    ...       "type": "Coverage",
    ...       "domain": {
    ...         "type": "Domain",
    ...         "domainType": "Point",
    ...         "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
    ...       },
    ...       "ranges": {
    ...         "t": {"type": "NdArray", "dataType": "float", "values": [280.0]}
    ...       }
    ...     }
    ...   ]
    ... }
    ... ''')
    >>> collection.to_datatree()
    <xarray.DataTree>
    Group: /
    └── Group: /coverage_0
            Dimensions:  ()
            Coordinates:
                x        float64 ...B 1.0
                y        float64 ...B 2.0
            Data variables:
                t        float64 ...B 280.0
            Attributes:
                Conventions:  CF-1.10
                domain_type:  Point
    """
    _require_datatree()

    import xarray as xr

    nodes = {
        f"coverage_{index}": to_xarray(coverage)
        for index, coverage in enumerate(collection.resolved_coverages())
    }

    return xr.DataTree.from_dict(nodes)


def from_datatree(
    tree: xr.DataTree,
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
    Start from a tree and convert it:

    >>> import numpy as np
    >>> import xarray as xr
    >>> node = xr.Dataset(
    ...     {"t": ("x", np.array([280.0, 281.0]))},
    ...     coords={"x": [1.0, 2.0]},
    ... )
    >>> tree = xr.DataTree.from_dict({"coverage_0": node})
    >>> tree
    <xarray.DataTree>
    Group: /
    └── Group: /coverage_0
            Dimensions:  (x: 2)
            Coordinates:
              * x        (x) float64 ...B 1.0 2.0
            Data variables:
                t        (x) float64 ...B 280.0 281.0

    Each data-bearing child node becomes a member coverage, in child order. The
    resulting collection, as CoverageJSON (the wire form, unset fields omitted):

    >>> import msgspec
    >>> from covjson_msgspec import encode
    >>> print(msgspec.json.format(encode(from_datatree(tree)), indent=2).decode())
    {
      "type": "CoverageCollection",
      "coverages": [
        {
          "type": "Coverage",
          "domain": {
            "type": "Domain",
            "axes": {
              "x": {
                "start": 1.0,
                "stop": 2.0,
                "num": 2
              }
            }
          },
          "ranges": {
            "t": {
              "type": "NdArray",
              "dataType": "float",
              "values": [
                280.0,
                281.0
              ],
              "shape": [
                2
              ],
              "axisNames": [
                "x"
              ]
            }
          }
        }
      ]
    }
    """
    _require_datatree()

    def convert(dataset: xr.Dataset) -> Coverage:
        """Convert one node's dataset to a `Coverage`, applying the shared options."""
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
    """Ensure xarray (with [`DataTree`][xarray.DataTree]) is importable, else raise.

    The collection bridge needs `xarray.DataTree`, which arrived in
    xarray 2024.10. This raises a `ModuleNotFoundError` with an install / upgrade
    hint when xarray is missing or too old, so the failure is actionable rather
    than a bare `AttributeError` deeper in.

    Raises
    ------
    ModuleNotFoundError
        If xarray is not installed, or predates [`DataTree`][xarray.DataTree].
    """
    try:
        import xarray as xr
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(_INSTALL_HINT) from exc

    if not hasattr(xr, "DataTree"):  # pragma: no cover - version-dependent
        msg = (
            "xarray.DataTree is required for collection conversion; "
            "upgrade to xarray>=2024.10"
        )
        raise ModuleNotFoundError(msg)


def _build_variables(
    coverage: Coverage, domain: Domain
) -> tuple[Mapping[str, _Variable], Mapping[str, _Variable]]:
    """Build the xarray coordinate and data variables for a coverage.

    Turns the domain's axes into coordinate variables (`_build_coords`, annotated
    from the referencing) and each range into a data variable (`_data_variable`).
    A horizontal CRS adds a CF grid-mapping ``crs`` coordinate (`_crs_coordinate`)
    that every data variable then points at via its ``grid_mapping`` attribute.

    Parameters
    ----------
    coverage
        The coverage whose ranges and parameters become data variables.
    domain
        The coverage's (inline, non-polygon) domain.

    Returns
    -------
    tuple
        ``(coords, data_vars)``: the coordinate and data-variable maps, each in
        xarray ``(dims, data, attrs)`` form.
    """
    systems = coordinate_systems(domain)
    geo_roles = _geographic_roles(domain)

    coords = _build_coords(domain, systems, geo_roles)
    data_vars = {
        key: _data_variable(key, range_, coverage.parameters or None)
        for key, range_ in coverage.ranges.items()
    }

    # A geographic system contributes a CF grid-mapping variable that the data
    # variables point at via their ``grid_mapping`` attribute.
    if (crs := _crs_coordinate(domain)) is not None:
        coords["crs"] = crs

        for _dims, _data, var_attrs in data_vars.values():
            var_attrs.setdefault("grid_mapping", "crs")

    return coords, data_vars


def _geographic_roles(domain: Domain) -> Mapping[str, str]:
    """Map a geographic system's coordinates to longitude / latitude / height.

    A [`GeographicCRS`][covjson_msgspec.GeographicCRS] lists its coordinates in the
    CoverageJSON order (`_GEOGRAPHIC_ROLES`: longitude, latitude, then optional
    height), so position determines role. The result tells `_coordinate` which CF
    ``standard_name`` / ``units`` to attach to each horizontal coordinate.

    Parameters
    ----------
    domain
        The domain whose geographic connections are inspected.

    Returns
    -------
    mapping
        Each geographic coordinate identifier mapped to ``"longitude"`` /
        ``"latitude"`` / ``"height"``.
    """
    # Map the coordinates of each geographic system to longitude/latitude/height
    # by their CoverageJSON ordering. strict=False is deliberate: a 2D system
    # lists only (x, y) while _GEOGRAPHIC_ROLES carries the optional height, so
    # the zip stops at the shorter coordinates tuple.
    return {
        coordinate: role
        for connection in domain.referencing
        if isinstance(connection.system.refine(), GeographicCRS)
        for coordinate, role in zip(
            connection.coordinates, _GEOGRAPHIC_ROLES, strict=False
        )
    }


def _build_coords(
    domain: Domain,
    systems: Mapping[str, ResolvedReferenceSystem],
    geo_roles: Mapping[str, str],
) -> MutableMapping[str, _Variable]:
    """Turn a domain's axes into xarray coordinate variables.

    Each primitive axis becomes one coordinate (single-valued axes collapse to a
    scalar coordinate, dropping the size-1 dimension); a composite (``tuple``)
    axis is transposed into one non-dimension coordinate per component, all along
    the composite's single dimension. Each coordinate is built by `_coordinate`,
    which attaches CF attributes from ``systems`` / ``geo_roles``.

    Parameters
    ----------
    domain
        The domain whose [`axes`][covjson_msgspec.Domain.axes] become coordinates.
    systems
        Coordinate-to-system lookup from
        `coordinate_systems`.
    geo_roles
        Coordinate-to-geographic-role lookup from `_geographic_roles`.

    Returns
    -------
    mutable mapping
        Coordinate name mapped to an xarray ``(dims, data, attrs)`` `_Variable`.

    Raises
    ------
    ValueError
        If the domain has a ``polygon`` axis (vector geometry belongs in the
        geopandas bridge).
    """
    coords: dict[str, _Variable] = {}

    for key, axis in domain.axes.items():
        if axis.data_type == "polygon":
            msg = "polygon axes are not supported by the xarray bridge"
            raise ValueError(msg)

        if axis.data_type == "tuple":
            # Composite axis: transpose the tuples into one non-dimension
            # coordinate per component, all along the single dimension ``key``.
            # `composite_columns` raises a clean error if a value is not a
            # matching tuple, so a malformed axis fails here rather than deep
            # inside numpy.
            for coordinate, column in composite_columns(axis, key):
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
    column: Sequence[Any],
    systems: Mapping[str, ResolvedReferenceSystem],
    geo_roles: Mapping[str, str],
    *,
    scalar: bool,
) -> _Variable:
    """Build one coordinate `_Variable`, with CF attributes from its system / role.

    A temporal coordinate has its values parsed to ``datetime64`` / cftime by
    `_parse_times`; otherwise the geographic role (longitude / latitude / height)
    or a vertical system (`_vertical_attrs`) supplies CF ``standard_name`` /
    ``units`` / ``positive``. A ``scalar`` coordinate drops its dimension and
    keeps the single value.

    Parameters
    ----------
    coordinate
        The coordinate's name (used to look up its system and role).
    dim
        The dimension this coordinate varies along (``key`` for a primitive axis,
        the composite dimension for a component).
    column
        The coordinate's values.
    systems, geo_roles
        The lookups from
        `coordinate_systems` / `_geographic_roles`.
    scalar
        Whether this is a single-valued axis to collapse to a scalar coordinate.

    Returns
    -------
    tuple
        An xarray ``(dims, data, attrs)`` `_Variable`.
    """
    import numpy as np

    system = systems.get(coordinate)
    role = geo_roles.get(coordinate)
    attrs: dict[str, Any] = {}

    if isinstance(system, TemporalRS):
        data = _parse_times(column, system.calendar)
    else:
        match role:
            case "longitude":
                attrs.update(standard_name="longitude", units="degrees_east")
            case "latitude":
                attrs.update(standard_name="latitude", units="degrees_north")
            case "height":
                attrs.update(standard_name="height", positive="up")
            case _ if isinstance(system, VerticalCRS):
                attrs.update(_vertical_attrs(system))
            case _:
                # No CF attributes for an unrecognized role / non-vertical system.
                pass

        data = np.asarray(column)

    # Drop the size-1 dimension: a single-valued axis becomes a scalar coord.
    return ((), data[0], attrs) if scalar else (dim, data, attrs)


def _parse_times(column: Sequence[Any], calendar: str) -> npt.NDArray[Any]:
    """Parse ISO time strings into a numpy time array, picking datetime64 or cftime.

    A standard-calendar column is parsed to ``datetime64[ns]`` when it fits
    numpy's nanosecond range; a non-standard calendar (or dates outside that
    range) falls back to an object array of cftime datetimes (`_to_cftime`). A
    trailing ``"Z"`` is stripped first (numpy treats naive times as UTC) and
    ``None`` entries are preserved. On a standard calendar a ``±hh:mm`` offset is
    applied and the value flattened to naive-UTC; the cftime path drops it instead
    (`_to_cftime`).

    Parameters
    ----------
    column
        The coordinate's raw time values (ISO 8601 strings, or ``None``).
    calendar
        The [`TemporalRS`][covjson_msgspec.TemporalRS] calendar (a bare name or a
        URI whose final segment names the calendar).

    Returns
    -------
    numpy.ndarray
        For a standard calendar, a ``datetime64`` array: ``[ns]`` when the whole
        column fits numpy's nanosecond window, else the wider ``[us]`` (which
        holds any Gregorian year). For a non-standard calendar, an object array
        of cftime datetimes.
    """
    import numpy as np

    # This bridge classifies by calendar + container range (a standard calendar
    # stays datetime64, at the resolution that fits it; a non-standard calendar
    # goes to cftime), not via temporal.resolve(). The two are different
    # functions with different codomains: resolve has no cftime arm and cannot
    # see the calendar, so it is deliberately not the decider here. See ADR-0015.
    normalized = calendar.rsplit("/", 1)[-1].lower()
    # ISO 8601 may carry a trailing "Z"; numpy treats naive times as UTC.
    cleaned = [
        None if value is None else str(value).removesuffix("Z") for value in column
    ]

    if normalized in STANDARD_CALENDARS:
        # Parse to microseconds first: datetime64[us] holds any Gregorian year,
        # so a spec-valid far-past/future date is never int64-wrapped the way a
        # direct datetime64[ns] parse silently would (numpy#9956). Narrow to the
        # finer ns resolution only when the whole column fits its window; else
        # keep [us], which xarray preserves (the >= 2025.01.2 floor). A standard
        # date outside the ns window is a resolution/range matter, not a calendar
        # one, so it stays a native datetime64 rather than falling back to cftime.
        #
        # A ``±hh:mm`` offset (a Spec 5.2 form) is folded to naive-UTC here, before
        # numpy sees it (`_fold_offset`): numpy has no timezone type, so it would
        # both warn and (correctly) flatten. Doing the flatten ourselves yields the
        # identical result while emitting no warning and mutating no global state
        # (`warnings.catch_warnings()` edits a process-global filter and is not
        # thread-safe). Only offset-bearing values pay the per-value cost; a
        # common all-``Z`` / naive axis stays a single vectorized parse. See
        # ADR-0015.
        utc = [None if value is None else _fold_offset(value) for value in cleaned]
        wide = np.array(utc, dtype="datetime64[us]")

        return wide.astype("datetime64[ns]") if _fits_ns_window(wide) else wide

    parsed = [
        None if value is None else _to_cftime(value, normalized) for value in cleaned
    ]

    return np.array(parsed)


# A trailing ``±hh:mm`` (or colon-less ``±hhmm``) UTC offset on an ISO 8601 value.
_UTC_OFFSET = re.compile(r"[+-]\d{2}:?\d{2}$")


def _fold_offset(value: str) -> str:
    """Fold a trailing ``±hh:mm`` UTC offset into a naive-UTC ISO 8601 string.

    A value carrying a numeric offset (a Spec 5.2 form, e.g. ``+05:00``) is
    converted to the equivalent UTC instant with its zone dropped, so the
    standard-calendar path can hand numpy a naive string. numpy has no timezone
    type and would otherwise warn while performing this same flatten; folding it
    here keeps the result identical, emits no warning, and mutates no global
    warning state. A value with no offset (already ``Z``-stripped, naive, or a
    reduced form) is returned unchanged, so only offset-bearing values pay the
    per-value ``fromisoformat`` cost.

    Parameters
    ----------
    value
        An ISO 8601 datetime string (already stripped of any trailing ``"Z"``).

    Returns
    -------
    str
        The naive-UTC ISO 8601 string for an offset value, else ``value`` as-is.

    Examples
    --------
    >>> _fold_offset("2020-01-15T00:00:00+05:00")
    '2020-01-14T19:00:00'
    >>> _fold_offset("2020-01-15T00:00:00")
    '2020-01-15T00:00:00'
    """
    if _UTC_OFFSET.search(value):
        aware = datetime.fromisoformat(value)

        return aware.astimezone(UTC).replace(tzinfo=None).isoformat()

    return value


def _fits_ns_window(times: npt.NDArray[Any]) -> bool:
    """Whether every non-``NaT`` value fits numpy's ``datetime64[ns]`` window.

    numpy's nanosecond datetime spans roughly 1677-09-21 to 2262-04-11; a value
    outside it int64-*wraps* on conversion (numpy#9956) rather than raising, so
    `_parse_times` tests the range explicitly before narrowing a wider array to
    ``ns``. The bounds are taken one second inside the true limits so the test is
    safe against sub-second rounding: a date in the first or last second of the
    ~585-year window is treated as out-of-range and kept at the wider resolution,
    a negligible and always-faithful loss.

    Parameters
    ----------
    times
        A ``datetime64`` array wide enough not to have already overflowed (e.g.
        ``datetime64[us]``). ``NaT`` entries are ignored.

    Returns
    -------
    bool
        ``True`` when every non-``NaT`` value lies within the ``ns`` window, so
        the array narrows to ``datetime64[ns]`` losslessly; else ``False``.

    Examples
    --------
    >>> import numpy as np
    >>> ok = np.array(["2020-01-15", "2020-06-01"], dtype="datetime64[us]")
    >>> _fits_ns_window(ok)
    True
    >>> out = np.array(["2020-01-15", "2300-01-15"], dtype="datetime64[us]")
    >>> _fits_ns_window(out)
    False
    >>> _fits_ns_window(np.array(["NaT", "2020-01-15"], dtype="datetime64[us]"))
    True
    """
    import numpy as np

    # One second inside numpy's true ns limits (1677-09-21T00:12:43.145224193 /
    # 2262-04-11T23:47:16.854775807), rounded conservatively so no out-of-range
    # value slips through into an int64-wrapping ns conversion.
    lo = np.datetime64("1677-09-21T00:12:44", "us")
    hi = np.datetime64("2262-04-11T23:47:16", "us")
    finite = times[~np.isnat(times)]

    return bool(np.all((lo <= finite) & (finite <= hi)))


def _to_cftime(iso: str, calendar: str) -> Any:
    """Convert one ISO 8601 string to a cftime datetime in the given calendar.

    Used by `_parse_times` for non-standard calendars (e.g. ``"360_day"``,
    ``"noleap"``) that numpy's ``datetime64`` cannot represent.

    A ``±hh:mm`` offset is dropped, keeping the wall-clock fields. cftime could
    do the arithmetic, but a non-standard calendar has no civil UTC for an offset
    to reference: the idealized ones (``360_day`` and the like) are model time,
    and ``julian`` covers eras that predate civil offsets, so an offset here is
    ill-defined input rather than data to preserve. Applying it would fabricate a
    shift the calendar does not define, so it is dropped. (The standard-calendar
    path, by contrast, flattens an offset to naive-UTC, a real instant.)

    Parameters
    ----------
    iso
        An ISO 8601 datetime string (already stripped of any trailing ``"Z"``).
    calendar
        The cftime calendar name.

    Returns
    -------
    cftime.datetime
        The moment in the requested calendar.

    Examples
    --------
    >>> moment = _to_cftime("2020-01-15T00:00:00", "360_day")
    >>> (moment.day, moment.calendar)
    (15, '360_day')
    """
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


def _vertical_attrs(system: VerticalCRS) -> Mapping[str, str]:
    """Infer CF vertical attributes from a [`VerticalCRS`][covjson_msgspec.VerticalCRS].

    CoverageJSON does not say whether a vertical axis points up or down, so this
    sniffs the system's ``id`` (the only member a vertical CRS carries, per
    Spec 5.1.3): ``"depth"`` implies ``positive="down"``, ``"height"`` /
    ``"altitude"`` imply ``positive="up"``. An unrecognized system yields no
    attributes (the coordinate is left direction-agnostic).

    Parameters
    ----------
    system
        The vertical reference system.

    Returns
    -------
    mapping
        CF ``standard_name`` / ``positive`` attributes, or empty when undetermined.

    Examples
    --------
    >>> _vertical_attrs(VerticalCRS(id="http://example.com/ocean/depth"))
    {'standard_name': 'depth', 'positive': 'down'}
    >>> _vertical_attrs(VerticalCRS(id="http://example.com/altitude"))
    {'standard_name': 'height', 'positive': 'up'}
    >>> _vertical_attrs(VerticalCRS(id="http://example.com/pressure"))
    {}
    """
    text = (system.id or "").lower()

    if "depth" in text:
        return {"standard_name": "depth", "positive": "down"}

    if "height" in text or "altitude" in text:
        return {"standard_name": "height", "positive": "up"}

    return {}


def _crs_coordinate(domain: Domain) -> _Variable | None:
    """Build the CF grid-mapping coordinate for a domain's horizontal CRS, if any.

    A geographic or projected system becomes a scalar ``crs`` variable (the CF
    grid-mapping convention). Because CoverageJSON identifies a CRS only by
    ``id`` (no projection parameters), the variable also records
    ``reference_system_type`` and ``reference_system_id`` so `_build_referencing`
    can rebuild the right class on the round-trip.

    Parameters
    ----------
    domain
        The domain whose [`referencing`][covjson_msgspec.Domain.referencing] is scanned
        for a horizontal CRS.

    Returns
    -------
    tuple or None
        A scalar grid-mapping `_Variable`, or ``None`` when no horizontal CRS is
        present.
    """
    # The horizontal CRS becomes a CF grid-mapping variable. ``reference_system_type``
    # records which CoverageJSON system it was so the round-trip can rebuild the
    # right class (CF has no projection params for a CRS identified only by id).
    crss = (
        system
        for connection in domain.referencing
        if isinstance(
            system := connection.system.refine(), (GeographicCRS, ProjectedCRS)
        )
    )

    if (crs := next(crss, None)) is None:
        return None

    attrs = {"reference_system_type": type(crs).__name__}

    if isinstance(crs, GeographicCRS):
        attrs["grid_mapping_name"] = "latitude_longitude"

    if crs.id is not None:
        attrs["reference_system_id"] = crs.id

    return ((), 0, attrs)


def _data_variable(
    key: str,
    range_: Range,
    parameters: Mapping[str, Parameter] | None,
) -> _Variable:
    """Build a data-variable `_Variable` from one parameter range.

    The range's ``axisNames`` become the variable's dims and its values the data
    ([`to_numpy`][covjson_msgspec.NdArray.to_numpy]); CF attributes come from the
    matching parameter via `_variable_attrs`.

    Parameters
    ----------
    key
        The range key (also used to find its parameter).
    range_
        The range; must be an inline [`NdArray`][covjson_msgspec.NdArray].
    parameters
        The coverage's parameters, or ``None`` when undescribed.

    Returns
    -------
    tuple
        An xarray ``(dims, data, attrs)`` `_Variable`.

    Raises
    ------
    ValueError
        If ``range_`` is not an inline [`NdArray`][covjson_msgspec.NdArray].
    """
    array = require_inline_ndarray(key, range_, "xarray")
    parameter = parameters.get(key) if parameters is not None else None

    return (array.axis_names, array.to_numpy(), _variable_attrs(parameter))


def _variable_attrs(parameter: Parameter | None) -> MutableMapping[str, Any]:
    """Build the CF attributes for a data variable from its parameter.

    Maps the parameter's metadata to CF: ``long_name`` from the parameter /
    observed-property label, ``standard_name`` from the observed-property id
    (`_standard_name`), ``units`` from the unit (`_unit_symbol`), and, for a
    categorical parameter, ``flag_values`` / ``flag_meanings`` (`_flags`). An
    absent or undescribed parameter yields no attributes.

    Parameters
    ----------
    parameter
        The parameter describing the variable, or ``None``.

    Returns
    -------
    mutable mapping
        The CF attributes (possibly empty).

    Examples
    --------
    >>> from covjson_msgspec import Category, ObservedProperty, Parameter, Unit

    An unlabeled parameter contributes no ``long_name``:

    >>> temp = Parameter.continuous(ObservedProperty(label={}), Unit(symbol="K"))
    >>> _variable_attrs(temp)
    {'units': 'K'}

    A category encoding that names none of the categories yields no flag attrs:

    >>> prop = ObservedProperty(
    ...     label={"en": "Land cover"},
    ...     categories=(Category(id="1", label={"en": "Water"}),),
    ... )
    >>> sorted(_variable_attrs(Parameter.categorical(prop, {"99": 5})))
    ['long_name']
    """
    if parameter is None:
        return {}

    observed = parameter.observed_property
    attrs: dict[str, Any] = {}

    if long_name := (display(parameter.label) or display(observed.label)):
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
    """Reduce an observed-property URI to a bare CF ``standard_name`` term.

    An ``observedProperty`` ``id`` is typically a URI, but CF wants just the term,
    so this takes the last path or fragment segment. ``None`` passes through.

    Parameters
    ----------
    identifier
        The observed-property id (a URI, a bare term, or ``None``).

    Returns
    -------
    str or None
        The final path / fragment segment, or ``None``.

    Examples
    --------
    >>> _standard_name("http://vocab.nerc.ac.uk/standard_names/air_temperature/")
    'air_temperature'
    >>> _standard_name("http://example.com/props#sea_water_salinity")
    'sea_water_salinity'
    >>> _standard_name(None) is None
    True
    """
    # An observedProperty id is typically a URI; CF wants the bare term, so take
    # the last path or fragment segment.
    return (
        None
        if identifier is None
        else identifier.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    )


def _unit_symbol(unit: Unit) -> str | None:
    """Extract a CF ``units`` string from a [`Unit`][covjson_msgspec.Unit].

    A unit's ``symbol`` is either a bare string or a
    [`parameter`][covjson_msgspec.parameter] ``Symbol`` object (a value plus a type
    URI); this returns the string in either case, or ``None`` when the unit has only a
    label.

    Parameters
    ----------
    unit
        The parameter's unit.

    Returns
    -------
    str or None
        The unit symbol, or ``None`` when there is none.

    Examples
    --------
    >>> from covjson_msgspec import Symbol, Unit
    >>> _unit_symbol(Unit(symbol=Symbol(value="Cel", type_="http://ex/Cel")))
    'Cel'
    >>> _unit_symbol(Unit(symbol="K"))
    'K'
    """
    match unit.symbol:
        case Symbol(value, _):
            return value
        case symbol:
            return symbol


def _flags(
    categories: tuple[Category, ...],
    encoding: CategoryEncoding,
) -> tuple[Sequence[int], str]:
    """Build CF ``flag_values`` / ``flag_meanings`` from categories and their codes.

    Pairs each category (that has an encoded code) with its label: the codes
    become ``flag_values`` and the underscore-joined labels (`_flag_meaning`)
    become the space-joined ``flag_meanings`` string, kept 1:1. A category encoded
    with multiple codes keeps its first (a documented simplification).

    Parameters
    ----------
    categories
        The observed property's categories.
    encoding
        The parameter's category-to-code(s) encoding.

    Returns
    -------
    tuple
        ``(flag_values, flag_meanings)``: the integer codes and the space-joined
        meanings string.
    """
    # CF flag_values are 1:1 with meanings; build the pairs together so they
    # cannot drift, then split them. A multi-code category keeps its first code
    # (a documented simplification).
    pairs = [
        (code[0] if isinstance(code, tuple) else code, _flag_meaning(category.label))
        for category in categories
        if (code := encoding.get(category.id)) is not None
    ]

    return tuple(value for value, _ in pairs), " ".join(meaning for _, meaning in pairs)


def _flag_meaning(label: I18n) -> str:
    """Turn a category label into a single CF ``flag_meanings`` token.

    CF ``flag_meanings`` is a whitespace-delimited list, so each meaning must be
    one token; this takes the display label ([`display`][covjson_msgspec.i18n.display])
    and joins its words with underscores.

    Parameters
    ----------
    label
        The category's localized label.

    Returns
    -------
    str
        A single underscore-joined token (empty when the label is empty).

    Examples
    --------
    >>> from covjson_msgspec.i18n import i18n
    >>> _flag_meaning(i18n("sea ice"))
    'sea_ice'
    """
    # CF flag_meanings are whitespace-delimited tokens, so collapse internal
    # whitespace in the label to underscores.
    return "_".join(display(label).split())


# Coordinate names commonly used for each role, lower-cased, as a detection
# fallback when CF attributes are absent.
_ROLE_NAMES: dict[str, frozenset[str]] = {
    "x": frozenset({"x", "lon", "longitude"}),
    "y": frozenset({"y", "lat", "latitude"}),
    "z": frozenset({"z", "depth", "height", "altitude", "level", "elevation"}),
    "t": frozenset({"t", "time"}),
}


def _detect_roles(
    dataset: xr.Dataset,
    *,
    x: str | None,
    y: str | None,
    z: str | None,
    t: str | None,
) -> Mapping[str, str | None]:
    """Decide which dataset coordinate fills each x / y / z / t role.

    Explicit overrides (``x`` / ``y`` / ``z`` / ``t`` arguments) win; the rest are
    inferred from each coordinate's CF attributes and common names (`_role_of`).
    A coordinate is never assigned to two roles, and an already-taken name is
    skipped, so detection is stable.

    Parameters
    ----------
    dataset
        The dataset whose coordinates are classified.
    x, y, z, t
        Explicit coordinate-name overrides per role (``None`` to auto-detect).

    Returns
    -------
    mapping
        Each role mapped to a coordinate name, or ``None`` when unfilled.
    """
    # Explicit overrides win; remaining roles are filled from CF attributes and
    # common coordinate names, never reusing a coordinate already assigned.
    roles = {"x": x, "y": y, "z": z, "t": t}
    taken = {name for name in roles.values() if name is not None}

    for name, coord in dataset.coords.items():
        str_name = str(name)
        role = _role_of(str_name, coord)

        if role is not None and roles[role] is None and str_name not in taken:
            roles[role] = str_name
            taken.add(str_name)

    return roles


def _role_of(name: str, coord: xr.DataArray) -> Literal["x", "y", "z", "t"] | None:
    """Guess a coordinate's x / y / z / t role from its CF attributes and name.

    Checks, in order, longitude (``standard_name`` / ``degrees_east`` units /
    common names from `_ROLE_NAMES`), latitude, vertical (``depth`` / ``height`` /
    ``altitude`` / a ``positive`` attribute), and time (`_is_time` or a time-like
    name). Returns the first match, else ``None``.

    Parameters
    ----------
    name
        The coordinate's name.
    coord
        The coordinate array (for its attributes and dtype).

    Returns
    -------
    Literal["x", "y", "z", "t"] or None
        ``"x"`` / ``"y"`` / ``"z"`` / ``"t"``, or ``None`` when unrecognized.
    """
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

    return "t" if _is_time(coord) or lowered in _ROLE_NAMES["t"] else None


def _is_time(coord: xr.DataArray) -> bool:
    """Whether a coordinate holds datetimes (numpy ``datetime64`` or cftime).

    True for a ``datetime64`` dtype, or an object array whose first element has a
    ``calendar`` attribute (a cftime datetime). Used both to detect the ``t`` role
    and to route a coordinate's values through `_time_to_iso`.

    Parameters
    ----------
    coord
        The coordinate array to test.

    Returns
    -------
    bool
        Whether the coordinate is time-valued.
    """
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
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
) -> tuple[str | None, Set[str]]:
    """Detect a composite (trajectory-style) axis: roles sharing one dimension.

    A composite axis appears as two or more role coordinates that are
    *non-dimension* coordinates along a single shared dimension (e.g. ``x(i)`` and
    ``y(i)`` both indexed by ``i``). This returns that dimension and the roles on
    it, so `_build_axes` can transpose them into one ``tuple`` axis.

    Parameters
    ----------
    dataset
        The dataset whose coordinates are inspected.
    roles
        The role-to-name mapping from `_detect_roles`.

    Returns
    -------
    tuple
        ``(dimension, roles)`` for the first such shared dimension, or
        ``(None, frozenset())`` when there is no composite axis.
    """
    # A composite (e.g. trajectory) axis shows up as several role coordinates
    # that are non-dimension coordinates sharing one dimension.
    groups: dict[str, set[str]] = {}

    for role, name in roles.items():
        if name is None or name not in dataset.coords:
            continue

        coord = dataset.coords[name]

        if coord.ndim == 1 and str(coord.dims[0]) != name:
            groups.setdefault(str(coord.dims[0]), set()).add(role)

    return next(
        (
            (dim, frozenset(grouped))
            for dim, grouped in groups.items()
            if len(grouped) >= 2
        ),
        (None, frozenset()),
    )


class _AxisEntry(NamedTuple):
    """A built axis, its key, and the dataset dimension it represents.

    ``dim`` is the dataset dimension the axis maps in ``dim_to_key`` (so a range's
    dims resolve to axis keys), or ``None`` for a scalar axis that maps no
    dimension. The three axis generators (`_composite_axis`, `_role_axes`,
    `_leftover_axes`) yield these, and `_build_axes` assembles them.
    """

    key: str
    axis: Axis
    dim: str | None


def _composite_axis(
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
    composite_dim: str | None,
    composite_roles: Set[str],
) -> Iterator[_AxisEntry]:
    """Yield the single ``tuple`` axis for a composite (trajectory) domain, if any.

    The composite roles' 1-D coordinates (which share ``composite_dim``) are
    transposed into one ``tuple`` axis whose values are ``(t, x, y, z)`` points in
    that fixed order. Yields nothing when there is no composite dimension.

    Parameters
    ----------
    dataset
        The source dataset.
    roles
        The role-to-name mapping from `_detect_roles`.
    composite_dim, composite_roles
        The shared dimension and roles from `_detect_composite`.

    Yields
    ------
    _AxisEntry
        A single entry whose key is ``"composite"``, or nothing when there is no
        composite dimension.

    Examples
    --------
    Two role coordinates sharing one dimension (``x`` and ``y`` along ``i``)
    transpose into a ``tuple`` axis of ``(x, y)`` points:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"v": ("i", np.zeros(2))},
    ...     coords={"x": ("i", [1.0, 2.0]), "y": ("i", [3.0, 4.0])},
    ... )
    >>> roles = {"x": "x", "y": "y", "z": None, "t": None}
    >>> entries = list(_composite_axis(ds, roles, "i", frozenset({"x", "y"})))
    >>> key, axis, dim = entries[0]
    >>> key, dim, axis.coordinates, axis.values
    ('composite', 'i', ('x', 'y'), ((1.0, 3.0), (2.0, 4.0)))
    """
    if composite_dim is None:
        return

    order = tuple(role for role in ("t", "x", "y", "z") if role in composite_roles)
    columns = [_coord_values(dataset[roles[role]]) for role in order]
    axis = Axis(
        data_type="tuple",
        coordinates=order,
        values=tuple(zip(*columns, strict=True)),
    )

    yield _AxisEntry(key="composite", axis=axis, dim=composite_dim)


def _role_axis(
    dataset: xr.Dataset, role: str, name: str, compact_regular: bool
) -> _AxisEntry:
    """Build one x / y / z / t role's axis entry from its coordinate.

    A 0-D coordinate yields a single-valued listed axis that maps no dimension; a
    1-D coordinate (a dimension coordinate, or an auxiliary one whose name differs
    from its dimension) an axis keyed by its dimension, so a range's dims resolve
    through that dimension. A 2-D coordinate is a curvilinear (non-separable) grid,
    which CoverageJSON's 1-D axes cannot represent, so it raises.

    Parameters
    ----------
    dataset
        The source dataset.
    role
        The axis role (``"x"`` / ``"y"`` / ``"z"`` / ``"t"``), also the axis key.
    name
        The name of the coordinate filling ``role``.
    compact_regular
        Whether to emit an evenly-spaced axis in the compact regular form
        (`_axis_from_coord`).

    Returns
    -------
    _AxisEntry
        The entry; its ``dim`` is the coordinate's dimension, or ``None`` for a
        scalar (0-D) coordinate.

    Raises
    ------
    ValueError
        If the coordinate is 2-D (a curvilinear grid).

    Examples
    --------
    A 1-D coordinate yields an axis keyed by its dimension:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(coords={"lon": ("x", [10.0, 20.0])})
    >>> key, axis, dim = _role_axis(ds, "x", "lon", True)
    >>> key, dim, axis.coordinate_values
    ('x', 'x', (10.0, 20.0))

    A 2-D coordinate is a curvilinear grid, which has no 1-D axis form:

    >>> curv = xr.Dataset(coords={"lon": (("y", "x"), np.zeros((2, 2)))})
    >>> _role_axis(curv, "x", "lon", True)
    Traceback (most recent call last):
        ...
    ValueError: cannot convert a curvilinear grid: the 'x'-axis coordinate ...
    """
    coord = dataset[name]

    if coord.ndim == 0:
        return _AxisEntry(key=role, axis=Axis.listed((_scalar(coord),)), dim=None)

    if coord.ndim == 1:
        return _AxisEntry(
            key=role,
            axis=_axis_from_coord(coord, compact_regular),
            dim=str(coord.dims[0]),
        )

    dims = tuple(str(d) for d in coord.dims)
    msg = (
        f"cannot convert a curvilinear grid: the {role!r}-axis coordinate "
        f"{name!r} is {coord.ndim}-D (varies along {dims}). CoverageJSON "
        "axes are 1-D, so a non-separable 2-D lat/lon grid has no axis form"
    )
    raise ValueError(msg)


def _role_axes(
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
    composite_roles: Set[str],
    compact_regular: bool,
) -> Iterator[_AxisEntry]:
    """Yield an axis entry for each present, non-composite x / y / z / t role.

    Each role coordinate's entry is built by `_role_axis` (which raises on a 2-D
    curvilinear coordinate). A role with no coordinate, or one already consumed by
    the composite axis, is skipped.

    Parameters
    ----------
    dataset
        The source dataset.
    roles
        The role-to-name mapping from `_detect_roles`.
    composite_roles
        The roles already consumed by a composite axis (`_composite_axis`), skipped
        here.
    compact_regular
        Whether to emit evenly-spaced axes in the compact regular form
        (`_axis_from_coord`).

    Yields
    ------
    _AxisEntry
        An entry per role (`_role_axis`).

    Raises
    ------
    ValueError
        If a role coordinate is 2-D (a curvilinear grid).

    Examples
    --------
    An auxiliary ``lon(x)`` / ``lat(y)`` grid: each role's axis is keyed by the
    dimension it varies along (not the coordinate name):

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"v": (("y", "x"), np.zeros((3, 2)))},
    ...     coords={"lon": ("x", [10.0, 20.0]), "lat": ("y", [0.0, 5.0, 10.0])},
    ... )
    >>> roles = {"x": "lon", "y": "lat", "z": None, "t": None}
    >>> [(key, dim) for key, _axis, dim in _role_axes(ds, roles, frozenset(), True)]
    [('x', 'x'), ('y', 'y')]
    """
    yield from (
        _role_axis(dataset, role, name, compact_regular)
        for role in ("x", "y", "z", "t")
        if (name := roles[role]) is not None and role not in composite_roles
    )


def _leftover_axis(dataset: xr.Dataset, key: str, compact_regular: bool) -> Axis:
    """The axis for a leftover dimension: its coordinate values, or an integer index.

    A dimension with a coordinate variable takes that coordinate's values
    (`_axis_from_coord`); a bare index dimension (no coordinate) gets a plain
    integer-index axis so its range data is not orphaned.

    Parameters
    ----------
    dataset
        The source dataset.
    key
        The dimension name (also the axis key).
    compact_regular
        Whether to emit an evenly-spaced coordinate axis in the compact regular
        form (`_axis_from_coord`).

    Returns
    -------
    Axis
        The dimension's axis.

    Examples
    --------
    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"v": (("lat", "band"), np.zeros((2, 3)))},
    ...     coords={"lat": ("lat", [0.0, 5.0])},
    ... )
    >>> _leftover_axis(ds, "lat", True).coordinate_values
    (0.0, 5.0)
    >>> _leftover_axis(ds, "band", True).coordinate_values
    (0, 1, 2)
    """
    if key in dataset.coords:
        return _axis_from_coord(dataset[key], compact_regular)

    return Axis.listed(tuple(range(dataset.sizes[key])))


def _leftover_axes(
    dataset: xr.Dataset,
    mapped_dims: Set[str],
    compact_regular: bool,
) -> Iterator[_AxisEntry]:
    """Yield an axis for each kept-range dimension not already mapped to an axis.

    A coverage axis is a dimension some kept range rides on (`_kept_range_dims`);
    one that a role or composite axis has not already claimed becomes an axis under
    its own name (`_leftover_axis`: its coordinate values, or an integer index). A
    dimension living only in skipped variables (a bounds variable's vertex
    dimension) is not among the kept dims, so it is never promoted.

    Parameters
    ----------
    dataset
        The source dataset.
    mapped_dims
        The dimensions already mapped to an axis by `_composite_axis` /
        `_role_axes`, skipped here.
    compact_regular
        Whether to emit evenly-spaced axes in the compact regular form
        (`_axis_from_coord`).

    Yields
    ------
    _AxisEntry
        An entry per promoted dimension; its key and dim are both the dimension
        name.

    Examples
    --------
    A dimension a range uses that has no coordinate (``band``) becomes an
    integer-index axis; an already-mapped dimension (``lat``) is skipped:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"v": (("lat", "band"), np.zeros((2, 3)))},
    ...     coords={"lat": ("lat", [0.0, 5.0])},
    ... )
    >>> [(key, dim) for key, _axis, dim in _leftover_axes(ds, {"lat"}, True)]
    [('band', 'band')]
    """
    kept_dims = _kept_range_dims(dataset)
    keys = map(str, dataset.sizes)

    yield from (
        _AxisEntry(key=key, axis=_leftover_axis(dataset, key, compact_regular), dim=key)
        for key in keys
        if key not in mapped_dims and key in kept_dims
    )


def _build_axes(
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
    composite_dim: str | None,
    composite_roles: Set[str],
    compact_regular: bool,
) -> tuple[Mapping[str, Axis], Mapping[str, str]]:
    """Build CoverageJSON axes from a dataset's coordinates and dimensions.

    Assembles three groups of axes, each contributing `_AxisEntry` records: the
    composite ``tuple`` axis (`_composite_axis`), the primitive x / y / z / t role
    axes (`_role_axes`), and one axis for every remaining kept-range dimension
    (`_leftover_axes`). Each entry's ``dim`` (when not ``None``) records which
    dataset dimension the axis represents, so a range's dims resolve to axis keys.

    Parameters
    ----------
    dataset
        The source dataset.
    roles
        The role-to-name mapping from `_detect_roles`.
    composite_dim, composite_roles
        The shared dimension and roles from `_detect_composite`.
    compact_regular
        Whether to emit evenly-spaced axes in the compact regular form
        (`_axis_from_coord`).

    Returns
    -------
    tuple
        ``(axes, dim_to_key)``: the axis map, and a lookup from each dataset
        dimension to the axis key it became (used to map range dims).

    Raises
    ------
    ValueError
        If a role coordinate is 2-D (a curvilinear grid), or the dataset is not a
        separable grid (two role coordinates on one dimension, or a dimension whose
        name collides with a role's axis key), which CoverageJSON has no axis form
        for.

    Examples
    --------
    An auxiliary ``lon(x)`` / ``lat(y)`` grid: the axes are keyed by role, and each
    dataset dimension maps to its axis so the ranges' dims resolve:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"v": (("y", "x"), np.zeros((3, 2)))},
    ...     coords={"lon": ("x", [10.0, 20.0]), "lat": ("y", [0.0, 5.0, 10.0])},
    ... )
    >>> roles = {"x": "lon", "y": "lat", "z": None, "t": None}
    >>> axes, dim_to_key = _build_axes(ds, roles, None, frozenset(), True)
    >>> sorted(axes), sorted(dim_to_key.items())
    (['x', 'y'], [('x', 'x'), ('y', 'y')])
    """
    axes: dict[str, Axis] = {}
    # Maps a dataset dimension to the CoverageJSON axis key it became.
    dim_to_key: dict[str, str] = {}

    def add(entry: _AxisEntry) -> None:
        # Each axis key and each dataset dimension names exactly one axis. A
        # collision means the dataset is not a separable grid (two role coordinates
        # on one dimension, or a dimension whose name is already a role's axis key),
        # so reject rather than silently binding a range to the wrong axis.
        if entry.key in axes:
            msg = (
                f"cannot convert to a grid: the axis key {entry.key!r} is claimed "
                f"by two dimensions (a role coordinate, and a dataset dimension "
                f"named {entry.key!r})."
            )
            raise ValueError(msg)

        if entry.dim is not None and entry.dim in dim_to_key:
            msg = (
                f"cannot convert to a grid: dimension {entry.dim!r} hosts two role "
                f"coordinates (axes {dim_to_key[entry.dim]!r} and {entry.key!r}), so "
                f"it is not a separable 1-D axis."
            )
            raise ValueError(msg)

        axes[entry.key] = entry.axis

        if entry.dim is not None:
            dim_to_key[entry.dim] = entry.key

    for entry in chain(
        _composite_axis(dataset, roles, composite_dim, composite_roles),
        _role_axes(dataset, roles, composite_roles, compact_regular),
    ):
        add(entry)

    # The leftover sweep needs the dimensions the role/composite axes already claimed.
    for entry in _leftover_axes(dataset, frozenset(dim_to_key), compact_regular):
        add(entry)

    return axes, dim_to_key


def _axis_from_coord(coord: xr.DataArray, compact_regular: bool) -> Axis:
    """Build a primitive [`Axis`][covjson_msgspec.Axis] from a 1-D coordinate.

    A time coordinate becomes a listed axis of ISO strings (`_time_to_iso`). A
    numeric coordinate becomes a compact regular axis (start / stop / num) when
    ``compact_regular`` is set and the values are evenly spaced (`_is_regular`),
    otherwise a listed axis.

    Parameters
    ----------
    coord
        The 1-D coordinate array.
    compact_regular
        Whether to prefer the regular form for evenly-spaced values.

    Returns
    -------
    Axis
        The coordinate as a listed or regular axis.
    """
    if _is_time(coord):
        return Axis.listed(tuple(_time_to_iso(coord)))

    import numpy as np

    values = np.atleast_1d(coord.values).tolist()

    if compact_regular and _is_regular(values):
        return Axis.regular(float(values[0]), float(values[-1]), len(values))

    return Axis.listed(tuple(values))


def _is_regular(values: Sequence[Any]) -> bool:
    """Whether numeric ``values`` are evenly spaced (a constant non-zero step).

    Decides if a coordinate can use the compact regular axis form. Needs at least
    two numeric values with a non-zero first step, and every consecutive
    difference must match that step within a small tolerance. Non-numeric values
    are not regular.

    Parameters
    ----------
    values
        The candidate coordinate values.

    Returns
    -------
    bool
        Whether the values form a regular sequence.

    Examples
    --------
    >>> _is_regular([0.0, 2.5, 5.0, 7.5])
    True
    >>> _is_regular([0.0, 1.0, 4.0])
    False
    >>> _is_regular([1.0])
    False
    >>> _is_regular(["a", "b"])
    False
    >>> _is_regular([3.0, 3.0, 5.0])
    False
    """
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
        math.isclose(b - a, step, rel_tol=1e-9, abs_tol=1e-12)
        for a, b in pairwise(numbers)
    )


def _coord_values(coord: xr.DataArray) -> Sequence[Any]:
    """Read a coordinate's values into a sequence (time as ISO strings).

    A time coordinate is rendered as ISO strings (`_time_to_iso`); any other
    coordinate is converted straight to a tuple. Used to gather a composite
    axis's component columns in `_build_axes`.

    Parameters
    ----------
    coord
        The coordinate array to read.

    Returns
    -------
    sequence
        The coordinate's values (ISO strings for time, native Python values
        otherwise).
    """
    if _is_time(coord):
        return _time_to_iso(coord)

    import numpy as np

    return tuple(np.atleast_1d(coord.values).tolist())


def _scalar(coord: xr.DataArray) -> Any:
    """Read a 0-dimensional coordinate's single value (time as an ISO string).

    Parameters
    ----------
    coord
        The scalar coordinate array.

    Returns
    -------
    Any
        The lone value: an ISO string for a time coordinate, else the native
        Python scalar.
    """
    return _time_to_iso(coord)[0] if _is_time(coord) else coord.values.item()


def _time_to_iso(coord: xr.DataArray) -> Sequence[str]:
    """Render a time coordinate's values as ISO 8601 strings for a CoverageJSON axis.

    A ``datetime64`` value is narrowed to microsecond resolution (datetime's
    limit) and suffixed with ``"Z"``; a cftime value uses its own
    ``isoformat``. A missing value (NaT or ``None``) has no faithful axis
    representation, so it raises via `_raise_missing_time`.

    Parameters
    ----------
    coord
        The time coordinate array.

    Returns
    -------
    sequence of str
        One ISO 8601 string per coordinate value.

    Raises
    ------
    ValueError
        If the coordinate contains a missing value (a CoverageJSON axis cannot
        hold a null coordinate).

    Examples
    --------
    >>> import numpy as np
    >>> import xarray as xr

    Any remaining object value is stringified as a last resort (a real time
    coordinate is ``datetime64`` or cftime; those paths are exercised by the
    bridge round-trip tests):

    >>> _time_to_iso(xr.DataArray(np.array(["2001", "2002"], dtype=object)))
    ('2001', '2002')

    A ``None`` has no coordinate representation, so it is rejected:

    >>> _time_to_iso(xr.DataArray(np.array([None], dtype=object)))
    Traceback (most recent call last):
        ...
    ValueError: time coordinate 'None' has a missing value...
    """
    import numpy as np

    result: list[str] = []

    for value in np.atleast_1d(coord.values):
        if isinstance(value, np.datetime64):
            if np.isnat(value):
                _raise_missing_time(coord)

            # datetime64[ns] exceeds datetime's microsecond resolution, so narrow
            # before converting to a Python datetime.
            moment = value.astype("datetime64[us]").astype(datetime)
            result.append(f"{moment.isoformat()}Z")
        elif value is None:
            _raise_missing_time(coord)
        elif hasattr(value, "isoformat"):
            # A cftime datetime (non-standard calendar).
            result.append(value.isoformat())
        else:
            result.append(str(value))

    return tuple(result)


def _raise_missing_time(coord: xr.DataArray) -> NoReturn:
    """Raise a clear error for a missing value in a time coordinate.

    Factored out of `_time_to_iso` (it is reached from two branches: NaT and
    ``None``) to keep one message.

    Parameters
    ----------
    coord
        The offending time coordinate (named in the message).

    Raises
    ------
    ValueError
        Always: a CoverageJSON axis cannot hold a null coordinate.
    """
    # A CoverageJSON axis lists coordinate positions, which cannot be null, so a
    # NaT / None in a time coordinate has no faithful representation.
    msg = (
        f"time coordinate {str(coord.name)!r} has a missing value (NaT); a "
        "CoverageJSON axis cannot hold a null coordinate"
    )
    raise ValueError(msg)


def _calendar(coord: xr.DataArray) -> str:
    """Read the calendar name from a time coordinate, defaulting to Gregorian.

    A cftime coordinate carries its calendar on each element; a numpy
    ``datetime64`` coordinate has none, so it is reported as the standard
    ``"Gregorian"`` calendar for the rebuilt
    [`TemporalRS`][covjson_msgspec.TemporalRS].

    Parameters
    ----------
    coord
        The time coordinate.

    Returns
    -------
    str
        The cftime calendar name, or ``"Gregorian"``.
    """
    import numpy as np

    values = np.atleast_1d(coord.values)

    if values.size > 0 and hasattr(values.flat[0], "calendar"):
        return str(values.flat[0].calendar)

    return "Gregorian"


def _build_referencing(
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
) -> tuple[ReferenceSystemConnection, ...]:
    """Rebuild CoverageJSON referencing from a dataset's roles and grid mapping.

    Emits up to three connections: a horizontal system over ``(x, y)`` (its class
    and ``id`` recovered from the CF ``crs`` grid-mapping variable when present,
    written by `_crs_coordinate`; geographic by default), a
    [`VerticalCRS`][covjson_msgspec.VerticalCRS] over ``z``, and a
    [`TemporalRS`][covjson_msgspec.TemporalRS] over ``t`` (its calendar from
    `_calendar`). A role that is absent contributes no connection.

    Parameters
    ----------
    dataset
        The source dataset (for the ``crs`` variable and the time coordinate).
    roles
        The role-to-name mapping from `_detect_roles`.

    Returns
    -------
    tuple
        The reference-system connections for the rebuilt domain.
    """
    connections: list[ReferenceSystemConnection] = []

    if roles["x"] is not None and roles["y"] is not None:
        crs_id = None
        crs_type = "GeographicCRS"

        if "crs" in dataset.coords:
            crs_attrs = dataset["crs"].attrs
            crs_id = crs_attrs.get("reference_system_id")
            crs_type = crs_attrs.get("reference_system_type", crs_type)

        horizontal: ReferenceSystem = (
            ReferenceSystem.projected(id=crs_id)
            if crs_type == "ProjectedCRS"
            else ReferenceSystem.geographic(id=crs_id)
        )
        connections.append(
            ReferenceSystemConnection(coordinates=("x", "y"), system=horizontal)
        )

    if roles["z"] is not None:
        connections.append(
            ReferenceSystemConnection(
                coordinates=("z",), system=ReferenceSystem.vertical()
            )
        )

    if roles["t"] is not None:
        connections.append(
            ReferenceSystemConnection(
                coordinates=("t",),
                system=ReferenceSystem.temporal(
                    calendar=_calendar(dataset[roles["t"]])
                ),
            )
        )

    return tuple(connections)


def _infer_domain_type(
    dataset: xr.Dataset,
    roles: Mapping[str, str | None],
    composite_dim: str | None,
) -> str | None:
    """Infer a CoverageJSON domain type from which roles are present and gridded.

    A composite axis means ``"Trajectory"``. Otherwise, a role counts only when its
    coordinate varies along a dimension (a 1-D coordinate, not a scalar): ``x`` and
    ``y`` both gridded give ``"Grid"``; with point-like ``x`` / ``y`` it is
    ``"PointSeries"``
    (``t`` varies), ``"VerticalProfile"`` (``z`` varies), or ``"Point"`` (neither).
    Anything else is left unset (``None``).

    Parameters
    ----------
    dataset
        The source dataset (to tell dimensions from scalar coordinates).
    roles
        The role-to-name mapping from `_detect_roles`.
    composite_dim
        The composite dimension from `_detect_composite`, or ``None``.

    Returns
    -------
    str or None
        The inferred domain type, or ``None`` when it cannot be determined.
    """
    if composite_dim is not None:
        return "Trajectory"

    def is_dim(role: str) -> bool:
        """Whether ``role``'s coordinate varies along a dimension (grids an axis).

        Mirrors `_build_axes`: a 1-D role coordinate (dimension or auxiliary)
        becomes a dimension axis, a scalar (0-D) coordinate a single-valued one, so
        gridded-ness follows the coordinate's dimensionality rather than whether its
        name is a dimension. A 2-D coordinate has already raised in `_build_axes`.
        """
        name = roles[role]
        return name is not None and dataset[name].ndim >= 1

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


def _is_grid_mapping(variable: xr.DataArray) -> bool:
    """Whether a variable is a CF grid-mapping container (not real range data).

    A grid-mapping variable carries a ``grid_mapping_name`` attribute and holds no
    measurements, so the decoder skips it rather than turning it into a parameter
    range.

    Parameters
    ----------
    variable
        The data variable to test.

    Returns
    -------
    bool
        Whether the variable is a grid-mapping container.
    """
    return "grid_mapping_name" in variable.attrs


def _bounds_variable_names(dataset: xr.Dataset) -> frozenset[str]:
    """Names of a dataset's CF bounds variables (cell-edge arrays, not data).

    A bounds variable is named in a coordinate's ``bounds`` attribute (CF 7.1);
    a ``_bnds`` / ``_bounds`` name suffix is a fallback for datasets that omit the
    attribute. Bounds variables hold no measurements, and their extra vertex
    dimension is not a coverage axis, so both range-building and axis-building
    skip them.

    Parameters
    ----------
    dataset
        The dataset whose variables are classified.

    Returns
    -------
    frozenset of str
        The names of the bounds variables.

    Examples
    --------
    A bounds variable is found by the ``bounds`` attribute even without a
    ``_bnds`` / ``_bounds`` suffix:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {"lat_edges": (("lat", "nv"), np.zeros((2, 2)))},
    ...     coords={"lat": ("lat", [0.0, 1.0], {"bounds": "lat_edges"})},
    ... )
    >>> sorted(_bounds_variable_names(ds))
    ['lat_edges']
    """
    declared = {
        str(variable.attrs["bounds"])
        for variable in dataset.variables.values()
        if "bounds" in variable.attrs
    }
    suffixed = {
        str(name)
        for name in dataset.data_vars
        if str(name).endswith(("_bnds", "_bounds"))
    }

    return frozenset(declared | suffixed)


def _kept_range_dims(dataset: xr.Dataset) -> frozenset[str]:
    """The dataset dimensions that a kept range rides on.

    A coverage axis is a dimension some real data variable varies along.
    Grid-mapping containers (`_is_grid_mapping`) and bounds variables
    (`_bounds_variable_names`) are not ranges, so a dimension living only in them
    (a bounds variable's vertex dimension) is not an axis. Axis-building consults
    this to bound itself to real axes instead of every dimension in the dataset.

    Parameters
    ----------
    dataset
        The dataset whose data variables are inspected.

    Returns
    -------
    frozenset of str
        The names of the dimensions used by at least one kept range.

    Examples
    --------
    The bounds variable's vertex dimension (``nv``) is excluded; the data
    variable's own dimensions are kept:

    >>> import numpy as np
    >>> import xarray as xr
    >>> ds = xr.Dataset(
    ...     {
    ...         "v": (("lat", "lon"), np.zeros((2, 2))),
    ...         "lat_bnds": (("lat", "nv"), np.zeros((2, 2))),
    ...     },
    ...     coords={"lat": ("lat", [0.0, 1.0], {"bounds": "lat_bnds"})},
    ... )
    >>> sorted(_kept_range_dims(ds))
    ['lat', 'lon']
    """
    bounds = _bounds_variable_names(dataset)

    return frozenset(
        str(dim)
        for name, variable in dataset.data_vars.items()
        if not _is_grid_mapping(variable) and str(name) not in bounds
        for dim in variable.dims
    )


def _parameter_from_variable(name: str, variable: xr.DataArray) -> Parameter | None:
    """Build a CoverageJSON [`Parameter`][covjson_msgspec.Parameter] from a data
    variable.

    Inverts `_variable_attrs`: CF ``flag_values`` / ``flag_meanings`` yield a
    categorical parameter, a ``units`` attribute yields a continuous parameter,
    and the label comes from ``long_name`` / ``standard_name`` / the variable name.
    A variable with neither flags nor units has nothing to put in a (unit-required)
    continuous parameter, so it gets no parameter and the range is emitted bare.

    Parameters
    ----------
    name
        The variable's name (the fallback label).
    variable
        The data variable whose attributes describe the parameter.

    Returns
    -------
    Parameter or None
        The reconstructed parameter, or ``None`` when the variable carries no
        describable metadata.
    """
    import numpy as np

    attrs = variable.attrs
    label = str(attrs.get("long_name") or attrs.get("standard_name") or name)

    if "flag_values" in attrs and "flag_meanings" in attrs:
        codes = [int(code) for code in np.atleast_1d(attrs["flag_values"]).tolist()]
        meanings = str(attrs["flag_meanings"]).split()
        # strict=False tolerates CF data in the wild where flag_values and
        # flag_meanings disagree in length: pair up to the shorter of the two
        # rather than raising on a malformed source attribute.
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


def _build_ranges(
    dataset: xr.Dataset,
    dim_to_key: Mapping[str, str],
) -> tuple[Mapping[str, Range], Mapping[str, Parameter]]:
    """Build a coverage's ranges and parameters from a dataset's data variables.

    Each data variable becomes an [`NdArray`][covjson_msgspec.NdArray] range, its dims
    remapped to CoverageJSON axis keys via ``dim_to_key``, and (when its CF
    attributes describe one) a [`Parameter`][covjson_msgspec.Parameter] via
    `_parameter_from_variable`. Grid-mapping containers (`_is_grid_mapping`) and CF
    bounds variables (`_bounds_variable_names`: a coordinate's ``bounds`` attribute,
    or a ``*_bnds`` / ``*_bounds`` suffix) are skipped: they hold no measurements.

    Parameters
    ----------
    dataset
        The source dataset.
    dim_to_key
        The dataset-dimension-to-axis-key lookup from `_build_axes`.

    Returns
    -------
    tuple
        ``(ranges, parameters)``: the range map, and the parameters for those
        variables that described one (a subset of the ranges' keys).
    """
    ranges: dict[str, Range] = {}
    parameters: dict[str, Parameter] = {}
    bounds = _bounds_variable_names(dataset)

    for name, variable in dataset.data_vars.items():
        key = str(name)

        if _is_grid_mapping(variable) or key in bounds:
            continue

        axis_names = tuple(dim_to_key.get(str(dim), str(dim)) for dim in variable.dims)
        ranges[key] = NdArray.from_numpy(variable.values, axis_names)

        if (parameter := _parameter_from_variable(key, variable)) is not None:
            parameters[key] = parameter

    return ranges, parameters
