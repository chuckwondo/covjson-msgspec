"""Coverage ranges: the data values for each parameter.

A range is either an `NdArray` (inline values, optionally with a ``shape`` and
``axis_names``) or a `TiledNdArray` (the values are split across external tiles
referenced by URL).

`NdArray` is generic in its element type. Decoding without a type parameter
yields the permissive ``float | int | str`` element type; a caller who knows a
parameter's ``data_type`` can decode ``NdArray[float]`` (etc.) for precise typing.
"""

import sys
from typing import Generic, Literal

from covjson_msgspec._base import CovJSONStruct

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

# Default element type for a bare ``NdArray`` (matches the three ``dataType``s).
Scalar = float | int | str
T = TypeVar("T", default=Scalar)


class NdArray(CovJSONStruct, Generic[T], frozen=True, tag="NdArray"):
    """An N-dimensional array of values for one parameter.

    ``values`` is a flat, row-major tuple whose length is the product of
    ``shape``; ``None`` marks missing data. ``shape`` and ``axis_names`` may be
    omitted for a single (0-dimensional) value.

    Examples
    --------
    >>> import msgspec
    >>> arr = msgspec.json.decode(
    ...     b'{"type": "NdArray", "dataType": "float",'
    ...     b' "axisNames": ["y", "x"], "shape": [1, 2], "values": [1.5, null]}',
    ...     type=NdArray,
    ... )
    >>> arr.values
    (1.5, None)
    >>> arr.axis_names
    ('y', 'x')

    A caller who knows the element type can decode it precisely:

    >>> floats = msgspec.json.decode(
    ...     b'{"type": "NdArray", "dataType": "float", "values": [1.0, 2.0]}',
    ...     type=NdArray[float],
    ... )
    >>> floats.values
    (1.0, 2.0)
    """

    data_type: Literal["float", "integer", "string"]
    values: tuple[T | None, ...]
    shape: tuple[int, ...] = ()
    axis_names: tuple[str, ...] = ()


class TileSet(CovJSONStruct, frozen=True):
    """One tiling of a `TiledNdArray`: a tile shape and a URL template.

    Each ``tile_shape`` entry is the tile's size along the corresponding axis, or
    ``None`` where the axis is not subdivided. ``url_template`` is an RFC 6570
    URI template whose variables are the names of the subdivided axes.
    """

    tile_shape: tuple[int | None, ...]
    url_template: str


class TiledNdArray(CovJSONStruct, frozen=True, tag="TiledNdArray"):
    """An N-dimensional array whose values are split across external tiles.

    Unlike `NdArray`, the values are not inline: each `TileSet` in ``tile_sets``
    is an alternative tiling of the same array, with tiles fetched from the
    ``url_template``. ``shape`` and ``axis_names`` describe the full array.

    Examples
    --------
    >>> import msgspec
    >>> tiled = msgspec.json.decode(
    ...     b'{"type": "TiledNdArray", "dataType": "float",'
    ...     b' "axisNames": ["t", "y", "x"], "shape": [4, 100, 100],'
    ...     b' "tileSets": [{"tileShape": [1, 100, 100],'
    ...     b' "urlTemplate": "http://ex/{t}.covjson"}]}',
    ...     type=TiledNdArray,
    ... )
    >>> tiled.tile_sets[0].url_template
    'http://ex/{t}.covjson'

    Each tile shape must rank-match the array's shape:

    >>> TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("x",),
    ...     shape=(2,),
    ...     tile_sets=(TileSet(tile_shape=(1, 1), url_template="u"),),
    ... )
    Traceback (most recent call last):
        ...
    ValueError: each tileSet's tileShape must have the same length as shape
    """

    data_type: Literal["float", "integer", "string"]
    axis_names: tuple[str, ...]
    shape: tuple[int, ...]
    tile_sets: tuple[TileSet, ...]

    def __post_init__(self) -> None:
        # O(number of tilesets), all tiny -- safe to run on every path.
        for tile_set in self.tile_sets:
            if len(tile_set.tile_shape) != len(self.shape):
                raise ValueError(
                    "each tileSet's tileShape must have the same length as shape"
                )
