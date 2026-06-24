"""Internals shared by the dataframe/array bridges (pandas, geopandas, xarray).

These helpers and constants are deliberately bridge-agnostic so the three
bridges stay consistent: they all reject the same polygon domain types, parse
the same standard calendars, guard ranges with the same message, and lay axis
data over a domain's grid with the same broadcasting rules. numpy / pandas are
imported lazily inside the helpers, so importing this module never pulls in an
optional dependency.
"""

from typing import TYPE_CHECKING, Any

from covjson_msgspec.coverage import Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray
from covjson_msgspec.referencing import TemporalRS

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
    `TiledNdArray` range cannot be converted; ``target`` names the destination
    (``"pandas"`` / ``"geopandas"`` / ``"xarray"``) in the message.
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
) -> "npt.NDArray[Any]":
    """Lay a range's values over the canonical ``dims`` grid as a flat column."""
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
    present: "tuple[str, ...] | list[str]",
    dims: list[str],
    sizes: dict[str, int],
) -> "npt.NDArray[Any]":
    """Broadcast ``data`` (varying only over ``present``) to the full ``dims`` grid.

    The result is raveled in row-major (C) order, matching pandas'
    ``MultiIndex.from_product`` layout.
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


def temporal_coordinates(domain: Domain) -> set[str]:
    """The coordinate identifiers governed by a standard-calendar temporal system.

    Only these are parsed to datetimes; other calendars stay as ISO strings.
    """
    coordinates: set[str] = set()

    for connection in domain.referencing:
        if isinstance(system := connection.system, TemporalRS):
            calendar = system.calendar.rsplit("/", 1)[-1].lower()

            if calendar in STANDARD_CALENDARS:
                coordinates.update(connection.coordinates)

    return coordinates


def maybe_datetime(values: list[Any], is_temporal: bool) -> Any:
    """Parse ``values`` to pandas datetimes when ``is_temporal``, else pass through."""
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
