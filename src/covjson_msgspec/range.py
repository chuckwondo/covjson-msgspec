"""Coverage ranges: the data values for each parameter.

A range is either an `NdArray` (inline values, optionally with a ``shape`` and
``axis_names``) or a `TiledNdArray` (the values are split across external tiles
referenced by URL).

`NdArray` is generic in its element type. Decoding without a type parameter
yields the permissive ``float | int | str`` element type; a caller who knows a
parameter's ``data_type`` can decode ``NdArray[float]`` (etc.) for precise typing.

With the ``numpy`` extra installed, `NdArray.to_numpy` and `NdArray.from_numpy`
convert to and from NumPy arrays, mapping CoverageJSON's ``null`` to NaN (float),
a masked entry (integer), or ``None`` (string), and back.

Spec: [NdArray objects][spec-ndarray] and [TiledNdArray objects][spec-tiled].

[spec-ndarray]: https://github.com/covjson/specification/blob/master/spec.md#62-ndarray-objects
[spec-tiled]: https://github.com/covjson/specification/blob/master/spec.md#63-tiledndarray-objects
"""

import math
import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Generic, Literal

from covjson_msgspec._base import CovJSONStruct

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

if TYPE_CHECKING:
    import numpy as np

# Raised (as the message) when a numpy bridge method is called without numpy.
_NUMPY_HINT = "NumPy is required for this conversion; install covjson-msgspec[numpy]"

# Element type for a bare ``NdArray`` (matches the three ``dataType``s).
#
# Both ``bound`` and ``default`` are set, and they do different jobs:
#
# * ``default`` is a PEP 696 default that the *type checker* substitutes when a
#   bare ``NdArray`` is used, so ``NdArray.values`` reads as a tuple of Scalars.
# * ``bound`` is what *msgspec* validates against at decode time for a bare
#   ``NdArray``: msgspec ignores the PEP 696 default at runtime (it would treat
#   an unparameterized ``T`` as ``Any``, accepting nested arrays, bools, etc.),
#   but it does honor the bound, so a bare decode still rejects non-Scalars.
#
# An explicit parameter (e.g. ``NdArray[float]``) overrides both for that decode.
#
# ``covariant=True`` is sound because NdArray is frozen and T appears only in
# read positions (``values``): it lets ``NdArray[float]`` (the type inferred when
# you build one from float values) satisfy an API expecting the bare
# ``NdArray`` (i.e. ``NdArray[Scalar]``), as in the CoverageJSON root union.
Scalar = float | int | str
T = TypeVar("T", bound=Scalar, default=Scalar, covariant=True)


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

    def to_numpy(
        self,
        *,
        fill_value: int | None = None,
        as_float: bool = False,
    ) -> "np.ndarray[Any, np.dtype[Any]]":
        """Convert to a NumPy array of this range's ``shape``.

        Requires the ``numpy`` extra. Missing values (``None``) become NaN for a
        ``"float"`` range, a masked entry for an ``"integer"`` range, and
        ``None`` in an object array for a ``"string"`` range.

        Parameters
        ----------
        fill_value
            For an ``"integer"`` range with missing values, the masked array's
            fill value. Ignored otherwise.
        as_float
            For an ``"integer"`` range, return a ``float64`` array with NaN for
            missing values instead of a masked integer array.

        Returns
        -------
        numpy.ndarray
            The values reshaped to ``shape`` (a 0-dimensional array when
            ``shape`` is empty). Integer ranges with missing values return a
            ``numpy.ma.MaskedArray`` unless ``as_float`` is set.

        Examples
        --------
        >>> arr = NdArray(
        ...     data_type="float", values=(1.5, None), shape=(2,), axis_names=("x",)
        ... )
        >>> arr.to_numpy().tolist()
        [1.5, nan]
        """
        try:
            import numpy as np
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise ModuleNotFoundError(_NUMPY_HINT) from exc

        has_missing = any(value is None for value in self.values)

        if self.data_type == "string":
            strings = [None if v is None else str(v) for v in self.values]
            array: np.ndarray[Any, np.dtype[Any]] = np.array(strings, dtype=object)
        elif self.data_type == "integer" and not as_float:
            if has_missing:
                masked = np.ma.MaskedArray(
                    data=[0 if v is None else int(v) for v in self.values],
                    mask=[v is None for v in self.values],
                    dtype=np.int64,
                )

                if fill_value is not None:
                    masked.fill_value = fill_value

                array = masked
            else:
                ints = [0 if v is None else int(v) for v in self.values]
                array = np.array(ints, dtype=np.int64)
        else:
            # float, or integer requested as float: NaN marks the gaps.
            floats = [math.nan if v is None else float(v) for v in self.values]
            array = np.array(floats, dtype=np.float64)

        return array.reshape(self.shape)

    @classmethod
    def from_numpy(
        cls,
        array: "np.ndarray[Any, np.dtype[Any]]",
        axis_names: Iterable[str],
        *,
        data_type: Literal["float", "integer", "string"] | None = None,
    ) -> "NdArray[Scalar]":
        """Build an `NdArray` from a NumPy array.

        Requires the ``numpy`` extra. Masked entries and non-finite floats (NaN,
        infinities) become ``None`` so the result is always JSON-encodable.

        Parameters
        ----------
        array
            The source array; its ``shape`` becomes the range's ``shape``.
        axis_names
            One name per dimension of ``array``.
        data_type
            The CoverageJSON ``dataType``. Inferred from the array's dtype when
            omitted (floating to ``"float"``, integer to ``"integer"``,
            otherwise ``"string"``).

        Returns
        -------
        NdArray
            A range holding the array's values in row-major order.

        Examples
        --------
        >>> import numpy as np
        >>> arr = NdArray.from_numpy(np.array([[1.0, np.nan]]), ("y", "x"))
        >>> arr.values
        (1.0, None)
        >>> arr.shape
        (1, 2)
        """
        try:
            import numpy as np
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise ModuleNotFoundError(_NUMPY_HINT) from exc

        if data_type is None:
            if np.issubdtype(array.dtype, np.floating):
                data_type = "float"
            elif np.issubdtype(array.dtype, np.integer):
                data_type = "integer"
            else:
                data_type = "string"

        shape = tuple(int(dim) for dim in array.shape)
        flat = np.ma.getdata(array).reshape(-1)
        mask = np.ma.getmaskarray(array).reshape(-1)
        values: list[Scalar | None] = []

        for value, missing in zip(flat, mask, strict=True):
            if missing:
                values.append(None)
            elif data_type == "float":
                number = float(value)
                values.append(number if math.isfinite(number) else None)
            elif data_type == "integer":
                values.append(int(value))
            else:
                values.append(None if value is None else str(value))

        # Build NdArray (not cls): the element type is the general Scalar union
        # determined here at runtime, not the caller's parameterized T.
        return NdArray(
            data_type=data_type,
            values=tuple(values),
            shape=shape,
            axis_names=tuple(axis_names),
        )


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
        # O(number of tilesets), all tiny, so safe to run on every path.
        for tile_set in self.tile_sets:
            if len(tile_set.tile_shape) != len(self.shape):
                raise ValueError(
                    "each tileSet's tileShape must have the same length as shape"
                )
