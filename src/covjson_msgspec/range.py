"""Coverage ranges: the data values for each parameter.

A range is either an `NdArray` (inline values, optionally with a ``shape`` and
``axis_names``) or a `TiledNdArray` (the values are split across external tiles
referenced by URL). Given a user-supplied fetcher, `TiledNdArray.assemble`
retrieves those tiles and stitches them back into a single inline `NdArray`.

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

from __future__ import annotations

import itertools
import math
import re
import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final, Generic, Literal

import msgspec

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec._fetch import Fetch, fetch_and_decode
from covjson_msgspec._ndindex import ravel_index, strides

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

if TYPE_CHECKING:
    import numpy.typing as npt

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
    >>> blob = '''
    ... {
    ...   "type": "NdArray",
    ...   "dataType": "float",
    ...   "axisNames": ["y", "x"],
    ...   "shape": [1, 2],
    ...   "values": [1.5, null]
    ... }
    ... '''
    >>> arr = msgspec.json.decode(blob, type=NdArray)
    >>> arr.values
    (1.5, None)
    >>> arr.axis_names
    ('y', 'x')

    A caller who knows the element type can decode it precisely:

    >>> blob = '{"type": "NdArray", "dataType": "float", "values": [1.0, 2.0]}'
    >>> floats = msgspec.json.decode(blob, type=NdArray[float])
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
    ) -> npt.NDArray[Any]:
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

        An ``"integer"`` range with missing data returns a masked array, since NaN
        cannot live in an integer array (pass ``as_float=True`` for NaN instead):

        >>> ints = NdArray(
        ...     data_type="integer", values=(1, None, 3), shape=(3,), axis_names=("x",)
        ... )
        >>> masked = ints.to_numpy()
        >>> masked.tolist()
        [1, None, 3]
        >>> ints.to_numpy(as_float=True).tolist()
        [1.0, nan, 3.0]
        """
        try:
            import numpy as np
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise ModuleNotFoundError(_NUMPY_HINT) from exc

        has_missing = any(value is None for value in self.values)

        if self.data_type == "string":
            strings = [None if v is None else str(v) for v in self.values]
            array: npt.NDArray[Any] = np.array(strings, dtype=object)
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

        # Fail with a clear message rather than numpy's cryptic "cannot reshape
        # array of size N into shape (...)": decoding is permissive, so a value
        # count inconsistent with shape only surfaces here. validate() reports
        # the same mismatch as an ndarray.value-count issue.
        expected = math.prod(self.shape)

        if array.size != expected:
            msg = (
                f"NdArray has {array.size} value(s) but shape {tuple(self.shape)} "
                f"needs {expected}; run validate() to locate the mismatch"
            )
            raise ValueError(msg)

        return array.reshape(self.shape)

    @classmethod
    def from_numpy(
        cls,
        array: npt.NDArray[Any],
        axis_names: Iterable[str],
        *,
        data_type: Literal["float", "integer", "string"] | None = None,
    ) -> NdArray[Scalar]:
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

        shape = tuple(map(int, array.shape))
        flat = np.ma.getdata(array).reshape(-1)
        mask = np.ma.getmaskarray(array).reshape(-1)
        values: list[Scalar | None] = []

        for value, missing in zip(flat, mask, strict=True):
            if missing:
                values.append(None)
            elif data_type == "float":
                values.append(number if math.isfinite(number := float(value)) else None)
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

    def _repr_html_(self) -> str:
        """Render an HTML summary of this array for Jupyter.

        Thin delegate to `covjson_msgspec._repr.ndarray_html`.
        """
        from covjson_msgspec._repr import ndarray_html

        return ndarray_html(self)


class TileSet(CovJSONStruct, frozen=True):
    """One tiling of a `TiledNdArray`: a tile shape and a URL template.

    Each ``tile_shape`` entry is the tile's size along the corresponding axis, or
    ``None`` where the axis is not subdivided. ``url_template`` is an RFC 6570
    URI template whose variables are the names of the subdivided axes.

    Examples
    --------
    Subdivide only the ``t`` axis (one time step per tile), leaving ``y`` / ``x``
    whole (``None``):

    >>> tile = TileSet(tile_shape=(1, None, None), url_template="tiles/{t}.covjson")
    >>> tile.tile_shape
    (1, None, None)
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
    >>> blob = '''
    ... {
    ...   "type": "TiledNdArray",
    ...   "dataType": "float",
    ...   "axisNames": ["t", "y", "x"],
    ...   "shape": [4, 100, 100],
    ...   "tileSets": [
    ...     {"tileShape": [1, 100, 100], "urlTemplate": "http://ex/{t}.covjson"}
    ...   ]
    ... }
    ... '''
    >>> tiled = msgspec.json.decode(blob, type=TiledNdArray)
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
        # Checked at construction (unlike NdArray, which defers its shape/value
        # consistency to validate()): a tileShape that does not rank-match shape
        # cannot be interpreted as a tiling at all, so it is a structural error.
        # O(number of tilesets), all tiny, so safe to run on every path.
        for tile_set in self.tile_sets:
            if len(tile_set.tile_shape) != len(self.shape):
                msg = "each tileSet's tileShape must have the same length as shape"
                raise ValueError(msg)

    def assemble(self, fetch: Fetch, tileset: int | None = None) -> NdArray:
        """Fetch this array's tiles and stitch them into a single inline `NdArray`.

        A `TiledNdArray`'s values live in external tile documents rather than
        inline. Given a fetcher, this retrieves every tile of one tile set and
        places it into a full-shape `NdArray`. Fetching is injected: ``fetch``
        maps a tile's URL to its raw bytes, so this performs no I/O of its own.

        Parameters
        ----------
        fetch
            A callable mapping a tile's URL to its raw bytes. All I/O (and any
            caching, auth, or retries) lives in this callable.
        tileset
            The index of the tile set to use. By default the tile set with the
            fewest tiles is chosen, minimizing the number of fetches.

        Returns
        -------
        NdArray
            An inline array of this array's full ``shape`` and ``axis_names`` with
            each tile's values placed at its position. Positions not covered by
            any tile are ``None``.

        Raises
        ------
        ValueError
            If there are no tile sets, if ``tileset`` is out of range, or if a
            fetched tile document is not a valid `NdArray`.

        Examples
        --------
        A two-element array split into two single-element tiles, fetched from a
        ``dict`` of canned documents keyed by URL:

        >>> from covjson_msgspec import encode
        >>> def tile(value):
        ...     arr = NdArray(
        ...         data_type="float", values=(value,), shape=(1,), axis_names=("x",)
        ...     )
        ...     return encode(arr)
        >>> store = {"0.covjson": tile(10.0), "1.covjson": tile(20.0)}
        >>> tiled = TiledNdArray(
        ...     data_type="float",
        ...     axis_names=("x",),
        ...     shape=(2,),
        ...     tile_sets=(TileSet(tile_shape=(1,), url_template="{x}.covjson"),),
        ... )
        >>> tiled.assemble(store.__getitem__).values
        (10.0, 20.0)
        """
        if not self.tile_sets:
            msg = "TiledNdArray has no tileSets to assemble from"
            raise ValueError(msg)

        if tileset is None:
            tile_set = min(
                self.tile_sets,
                key=lambda candidate: _tile_count(self.shape, candidate.tile_shape),
            )
        else:
            try:
                tile_set = self.tile_sets[tileset]
            except IndexError:
                msg = (
                    f"tileset index {tileset} is out of range; this TiledNdArray "
                    f"has {len(self.tile_sets)} tileSet(s)"
                )
                raise ValueError(msg) from None

        # Three phases, kept separate so an async variant can reuse phases 1 and 3
        # and differ only in the fetch: (1) lay out each tile's URL and offset,
        # (2) fetch and decode every tile, (3) place the tiles into the full array.
        layout = _tile_layout(self.shape, self.axis_names, tile_set)
        tiles = [
            (offsets, fetch_and_decode(fetch, url, _TILE_DECODER))
            for url, offsets in layout
        ]

        return _assemble_tiles(self.data_type, self.axis_names, self.shape, tiles)

    def _repr_html_(self) -> str:
        """Render an HTML summary of this tiled array for Jupyter.

        Thin delegate to `covjson_msgspec._repr.tiled_ndarray_html`.
        """
        from covjson_msgspec._repr import tiled_ndarray_html

        return tiled_ndarray_html(self)


# A fetched tile is a standalone NdArray document (the CoverageJSON root union
# decodes one on its own); the decoder is built once and reused.
_TILE_DECODER: Final[msgspec.json.Decoder[NdArray]] = msgspec.json.Decoder(NdArray)

# A single Level 1 RFC 6570 expression, e.g. ``{t}`` in a tile url template.
_TEMPLATE_VARIABLE = re.compile(r"\{([^{}]+)\}")


def _tile_count(shape: tuple[int, ...], tile_shape: tuple[int | None, ...]) -> int:
    """Return how many tiles a tile set partitions the array into.

    The product over the partitioned axes of how many tiles each is divided into
    (``ceil(size / tile_size)``); an axis with a ``None`` tile size is whole and
    contributes a single tile.

    Parameters
    ----------
    shape
        The full array shape.
    tile_shape
        A tile set's ``tile_shape`` (per-axis tile size, ``None`` where whole).

    Returns
    -------
    int
        The total number of tiles.

    Examples
    --------
    >>> _tile_count((2, 5, 10), (1, None, None))
    2
    >>> _tile_count((2, 5, 10), (None, 2, 3))
    12
    >>> _tile_count((2, 5, 10), (None, None, None))
    1
    """
    count = 1

    for size, tile_size in zip(shape, tile_shape, strict=True):
        if tile_size is not None:
            count *= -(-size // tile_size)

    return count


def _expand_url_template(template: str, variables: dict[str, int]) -> str:
    """Expand a Level 1 RFC 6570 URL template with integer tile indices.

    Substitutes each ``{name}`` in ``template`` with ``variables[name]``.
    CoverageJSON tile templates are Level 1 (simple ``{var}`` expansion only) and
    the values are non-negative tile ordinals, so no percent-encoding is needed.

    Parameters
    ----------
    template
        The url template, e.g. ``"tiles/{y}-{x}.covjson"``.
    variables
        The tile ordinal for each partitioned axis name.

    Returns
    -------
    str
        The expanded URL.

    Raises
    ------
    ValueError
        If the template references a variable absent from ``variables``.

    Examples
    --------
    >>> _expand_url_template("tiles/{y}-{x}.covjson", {"y": 0, "x": 3})
    'tiles/0-3.covjson'
    >>> _expand_url_template("tiles/{t}.covjson", {})
    Traceback (most recent call last):
        ...
    ValueError: url template 'tiles/{t}.covjson' references unknown variable 't'
    """

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)

        if name not in variables:
            msg = f"url template {template!r} references unknown variable {name!r}"
            raise ValueError(msg)

        return str(variables[name])

    return _TEMPLATE_VARIABLE.sub(_substitute, template)


def _tile_layout(
    shape: tuple[int, ...],
    axis_names: tuple[str, ...],
    tile_set: TileSet,
) -> list[tuple[str, tuple[int, ...]]]:
    """Lay out every tile of a tile set as a ``(url, offsets)`` pair.

    Each partitioned axis is divided into ``ceil(size / tile_size)`` tiles indexed
    by ordinal ``0, 1, ...``; an unpartitioned axis (``None`` tile size) spans the
    whole axis at offset 0. The cartesian product over axes enumerates the tiles;
    each tile's URL comes from expanding the template with the partitioned axes'
    ordinals, and its offsets are the per-axis start indices
    (``ordinal * tile_size``).

    Parameters
    ----------
    shape
        The full array shape.
    axis_names
        The axis names, aligned with ``shape``.
    tile_set
        The tile set to enumerate.

    Returns
    -------
    list of tuple
        One ``(url, offsets)`` pair per tile, where ``offsets`` is the tile's
        start index along each axis.

    Examples
    --------
    >>> tile_set = TileSet(tile_shape=(1,), url_template="{x}.covjson")
    >>> _tile_layout((2,), ("x",), tile_set)
    [('0.covjson', (0,)), ('1.covjson', (1,))]
    """
    per_axis: list[list[tuple[int, int | None]]] = []

    for size, tile_size in zip(shape, tile_set.tile_shape, strict=True):
        if tile_size is None:
            per_axis.append([(0, None)])
        else:
            count = -(-size // tile_size)
            per_axis.append([(o * tile_size, o) for o in range(count)])

    layout: list[tuple[str, tuple[int, ...]]] = []

    for combination in itertools.product(*per_axis):
        offsets = tuple(offset for offset, _ in combination)
        variables = {
            name: ordinal
            for name, (_, ordinal) in zip(axis_names, combination, strict=True)
            if ordinal is not None
        }
        layout.append((_expand_url_template(tile_set.url_template, variables), offsets))

    return layout


def _assemble_tiles(
    data_type: Literal["float", "integer", "string"],
    axis_names: tuple[str, ...],
    shape: tuple[int, ...],
    tiles: list[tuple[tuple[int, ...], NdArray]],
) -> NdArray:
    """Place fetched tiles into one full-shape `NdArray`.

    Each tile's row-major values are written into the full array at the tile's
    per-axis offset (``itertools.product`` walks the tile's destination indices in
    the same row-major order as its flat values). Positions not covered by any
    tile stay ``None`` (missing).

    Parameters
    ----------
    data_type
        The assembled array's ``dataType``.
    axis_names
        The assembled array's axis names.
    shape
        The assembled array's full shape.
    tiles
        ``(offsets, tile)`` pairs: each tile's start index per axis and its
        decoded `NdArray`.

    Returns
    -------
    NdArray
        The full array with every tile placed.

    Examples
    --------
    >>> a = NdArray(data_type="float", values=(1.0,), shape=(1,), axis_names=("x",))
    >>> b = NdArray(data_type="float", values=(2.0,), shape=(1,), axis_names=("x",))
    >>> _assemble_tiles("float", ("x",), (2,), [((0,), a), ((1,), b)]).values
    (1.0, 2.0)
    """
    full_strides = strides(shape)
    values: list[Scalar | None] = [None] * math.prod(shape)

    for offsets, tile in tiles:
        axis_ranges = [
            range(start, start + size)
            for start, size in zip(offsets, tile.shape, strict=True)
        ]

        for value, index in zip(
            tile.values, itertools.product(*axis_ranges), strict=True
        ):
            values[ravel_index(index, full_strides)] = value

    return NdArray(
        data_type=data_type,
        values=tuple(values),
        shape=shape,
        axis_names=axis_names,
    )
