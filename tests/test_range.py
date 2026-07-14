"""Behavioral tests for coverage ranges (NdArray / TiledNdArray)."""

import asyncio
import itertools
from typing import Literal

import msgspec
import numpy as np
import pytest

from covjson_msgspec import (
    FailureKind,
    FetchError,
    NdArray,
    ReferencedDocumentError,
    TiledNdArray,
    TileSet,
    collect_all,
    encode,
    halt_on_unrecoverable,
    stop_after,
)
from fetchers import async_store_fetcher, store_fetcher


def _spec_tiled() -> TiledNdArray:
    """The canonical spec example: shape (2, 5, 10) with three tilings."""
    return TiledNdArray(
        data_type="float",
        axis_names=("t", "y", "x"),
        shape=(2, 5, 10),
        tile_sets=(
            TileSet(tile_shape=(None, None, None), url_template="a/all.covjson"),
            TileSet(tile_shape=(1, None, None), url_template="b/{t}.covjson"),
            TileSet(tile_shape=(None, 2, 3), url_template="c/{y}-{x}.covjson"),
        ),
    )


def test_ndarray_roundtrips() -> None:
    arr = NdArray(
        data_type="float", values=(1.0, None, 3.0), shape=(3,), axis_names=("x",)
    )
    back = msgspec.json.decode(msgspec.json.encode(arr), type=NdArray)
    assert back == arr
    assert back.values == (1.0, None, 3.0)


@pytest.mark.parametrize(
    "blob",
    [
        b'{"type":"NdArray","dataType":"float","values":[[1,2]]}',
        b'{"type":"NdArray","dataType":"float","values":[true]}',
    ],
)
def test_bare_ndarray_enforces_scalar_union_on_decode(blob: bytes) -> None:
    # Verifies that removing Generic[T] did not loosen element-type enforcement:
    # msgspec still enforces tuple[float | int | str | None, ...] directly, so
    # nested arrays and booleans are rejected at decode time.
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(blob, type=NdArray)


def test_ndarray_zero_dimensional_defaults() -> None:
    arr = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"float","values":[42.0]}', type=NdArray
    )
    assert arr.shape == ()
    assert arr.axis_names == ()


@pytest.mark.parametrize(
    ("data_type", "dtype", "values", "expected"),
    [
        # A "float" range promotes integer-written values (5 -> 5.0).
        ("float", float, (5, 6.5, None), (5.0, 6.5, None)),
        # A large-but-representable int converts: the overflow guard must not
        # over-trigger (only a truly out-of-range int, 10**309+, raises).
        ("float", float, (10**300,), (1e300,)),
        ("integer", int, (1, 2, None), (1, 2, None)),
        ("string", str, ("a", "b", None), ("a", "b", None)),
    ],
)
def test_values_as_projects_to_precise_type(
    data_type: Literal["float", "integer", "string"],
    dtype: type[float] | type[int] | type[str],
    values: tuple[float | int | str | None, ...],
    expected: tuple[float | int | str | None, ...],
) -> None:
    result = NdArray(data_type=data_type, values=values).values_as(dtype)
    assert result == expected
    # `== expected` alone cannot catch a missing int->float promotion, since
    # ``5 == 5.0``; assert the projected element type exactly.
    assert all(type(value) is dtype for value in result if value is not None)


@pytest.mark.parametrize(
    ("data_type", "dtype", "values"),
    [
        ("integer", int, (1, 1.5)),  # a fractional float is not an int
        ("integer", int, (1, 1.0)),  # even a whole-valued float is not an int
        ("string", float, ("a",)),  # a string is not a float
        # An int too large for a float is out of range, not a valid float value;
        # the C convert leaks an OverflowError/SystemError that this method
        # normalizes into the documented ValidationError (see values_as).
        ("float", float, (10**400,)),
    ],
)
def test_values_as_raises_msgspec_error_on_mismatch(
    data_type: Literal["float", "integer", "string"],
    dtype: type[float] | type[int] | type[str],
    values: tuple[float | int | str | None, ...],
) -> None:
    # The error contract is msgspec.ValidationError (the same error a bare decode
    # raises), deliberately not the library's CovJSONValidationError, which
    # validate(mode="raise") uses. The two doors, one for consuming and one for
    # reporting, keep distinct error types.
    with pytest.raises(msgspec.ValidationError):
        NdArray(data_type=data_type, values=values).values_as(dtype)


def test_tiled_ndarray_roundtrips() -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("t", "y", "x"),
        shape=(4, 100, 100),
        tile_sets=(
            TileSet(tile_shape=(1, 100, 100), url_template="http://ex/{t}.covjson"),
        ),
    )
    back = msgspec.json.decode(msgspec.json.encode(tiled), type=TiledNdArray)
    assert back == tiled
    assert back.tile_sets[0].tile_shape == (1, 100, 100)


def test_tiled_ndarray_rank_check() -> None:
    with pytest.raises(ValueError, match="same length as shape"):
        TiledNdArray(
            data_type="float",
            axis_names=("x",),
            shape=(2,),
            tile_sets=(TileSet(tile_shape=(1, 1), url_template="u"),),
        )


def test_tile_shape_allows_null() -> None:
    blob = b"""
{
  "type": "TiledNdArray",
  "dataType": "float",
  "axisNames": ["t", "y", "x"],
  "shape": [4, 100, 100],
  "tileSets": [
    {
      "tileShape": [null, 100, 100],
      "urlTemplate": "http://ex/{t}.covjson"
    }
  ]
}
"""
    tiled = msgspec.json.decode(blob, type=TiledNdArray)
    assert tiled.tile_sets[0].tile_shape == (None, 100, 100)


@pytest.mark.parametrize("index", range(len(_spec_tiled().tile_sets)))
def test_assemble_reconstructs_full_array_for_each_tileset(index: int) -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    store = _tile_store(full, tiled, index)
    result = tiled.assemble(store_fetcher(store), tileset=index).array

    assert result.shape == (2, 5, 10)
    assert result.axis_names == ("t", "y", "x")
    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_default_picks_the_fewest_tiles() -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    # Store ONLY tileset A's single tile; default selection must choose it (and
    # so never request a URL from the 2-tile or 12-tile sets).
    store = _tile_store(full, tiled, 0)

    result = tiled.assemble(store_fetcher(store)).array

    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_handles_remainder_tiles() -> None:
    full = np.arange(5, dtype=float)  # tile size 2 -> tiles [0,1], [2,3], [4]
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(5,),
        tile_sets=(TileSet(tile_shape=(2,), url_template="{x}.covjson"),),
    )
    store = {
        "0.covjson": encode(NdArray.from_numpy(full[0:2], ("x",))),
        "1.covjson": encode(NdArray.from_numpy(full[2:4], ("x",))),
        "2.covjson": encode(NdArray.from_numpy(full[4:5], ("x",))),
    }

    result = tiled.assemble(store_fetcher(store)).array

    assert result.shape == (5,)
    assert result.values == (0.0, 1.0, 2.0, 3.0, 4.0)


def test_assemble_without_tilesets_errors() -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached
    tiled = TiledNdArray(data_type="float", axis_names=("x",), shape=(2,), tile_sets=())

    with pytest.raises(ValueError, match="no tileSets"):
        tiled.assemble(store_fetcher(empty))


def test_assemble_tileset_index_out_of_range_errors() -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached

    with pytest.raises(ValueError, match="out of range"):
        _spec_tiled().assemble(store_fetcher(empty), tileset=5)


def test_assemble_invalid_tile_document_reports_url() -> None:
    tiled = _one_d_tiled(1)

    # An undecodable tile is an unrecoverable failure; fail_fast raises a
    # FetchError chained from the ReferencedDocumentError, naming the tile's URL.
    with pytest.raises(FetchError, match="not valid CoverageJSON") as excinfo:
        tiled.assemble(store_fetcher({"0.covjson": b"nope"}))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE
    assert excinfo.value.failures[0].url == "0.covjson"


@pytest.mark.parametrize("index", range(len(_spec_tiled().tile_sets)))
def test_assemble_async_matches_sync_for_each_tileset(index: int) -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    store = _tile_store(full, tiled, index)
    result = asyncio.run(
        tiled.assemble_async(async_store_fetcher(store), tileset=index)
    ).array

    assert result.shape == (2, 5, 10)
    assert result.axis_names == ("t", "y", "x")
    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_async_default_picks_the_fewest_tiles() -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    # Tile set 0 is the whole array in one tile; the default must reproduce it.
    store = _tile_store(full, tiled, 0)

    result = asyncio.run(tiled.assemble_async(async_store_fetcher(store))).array

    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_async_without_tilesets_errors() -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached
    tiled = TiledNdArray(data_type="float", axis_names=("x",), shape=(2,), tile_sets=())

    with pytest.raises(ValueError, match="no tileSets"):
        asyncio.run(tiled.assemble_async(async_store_fetcher(empty)))


def test_assemble_async_tileset_index_out_of_range_errors() -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached
    fetch = async_store_fetcher(empty)

    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(_spec_tiled().assemble_async(fetch, tileset=5))


def test_assemble_async_invalid_tile_document_reports_url() -> None:
    tiled = _one_d_tiled(1)
    fetch = async_store_fetcher({"0.covjson": b"nope"})

    with pytest.raises(FetchError, match="not valid CoverageJSON") as excinfo:
        asyncio.run(tiled.assemble_async(fetch))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE


def test_assemble_fail_fast_raises_fetcherror_chaining_cause() -> None:
    tiled = _one_d_tiled(3)
    store = {"0.covjson": _scalar_tile(0.0)}  # tiles 1 and 2 are missing

    # The default fail_fast halts on the first failed tile (here "1.covjson"),
    # raising a FetchError chained from the fetcher's own KeyError.
    with pytest.raises(FetchError) as excinfo:
        tiled.assemble(store_fetcher(store))

    assert isinstance(excinfo.value.__cause__, KeyError)
    assert len(excinfo.value.failures) == 1
    assert excinfo.value.failures[0].url == "1.covjson"


def test_assemble_collect_all_returns_partial_array_with_holes() -> None:
    tiled = _one_d_tiled(3)
    store = {"0.covjson": _scalar_tile(10.0), "2.covjson": _scalar_tile(30.0)}

    result = tiled.assemble(store_fetcher(store), strategy=collect_all)

    assert result.array.values == (10.0, None, 30.0)
    assert [failure.url for failure in result.failures] == ["1.covjson"]
    assert result.failures[0].kind is FailureKind.TRANSIENT
    assert result.failures[0].offsets == (1,)


def test_assemble_collect_all_clean_has_no_failures() -> None:
    tiled = _one_d_tiled(2)
    store = {"0.covjson": _scalar_tile(1.0), "1.covjson": _scalar_tile(2.0)}

    result = tiled.assemble(store_fetcher(store), strategy=collect_all)

    assert result.failures == ()
    assert result.array.values == (1.0, 2.0)


def test_assemble_halt_on_unrecoverable_raises_on_malformed_tile() -> None:
    tiled = _one_d_tiled(2)
    store = {"0.covjson": b"nope", "1.covjson": _scalar_tile(2.0)}

    with pytest.raises(FetchError) as excinfo:
        tiled.assemble(store_fetcher(store), strategy=halt_on_unrecoverable)

    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_assemble_stop_after_collects_exactly_limit(limit: int) -> None:
    tiled = _one_d_tiled(5)
    empty: dict[str, bytes] = {}  # every tile fails to fetch

    with pytest.raises(FetchError) as excinfo:
        tiled.assemble(store_fetcher(empty), strategy=stop_after(limit))

    assert len(excinfo.value.failures) == limit


def test_assemble_collect_all_classifies_fetch_vs_decode() -> None:
    tiled = _one_d_tiled(2)
    store = {"1.covjson": b"nope"}  # tile 0 missing (fetch), tile 1 malformed (decode)

    result = tiled.assemble(store_fetcher(store), strategy=collect_all)

    kinds = {failure.url: failure.kind for failure in result.failures}
    assert kinds == {
        "0.covjson": FailureKind.TRANSIENT,
        "1.covjson": FailureKind.UNRECOVERABLE,
    }


def test_assemble_is_lazy_and_stops_fetching_on_halt() -> None:
    tiled = _one_d_tiled(5)
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        raise KeyError(url)

    with pytest.raises(FetchError):
        tiled.assemble(fetch, strategy=stop_after(1))

    # Lazy: the halt on the first tile stops before the remaining four are fetched.
    assert len(seen) == 1


def test_assemble_async_collect_all_returns_partial_array_with_holes() -> None:
    tiled = _one_d_tiled(3)
    store = {"0.covjson": _scalar_tile(10.0), "2.covjson": _scalar_tile(30.0)}

    result = asyncio.run(
        tiled.assemble_async(async_store_fetcher(store), strategy=collect_all)
    )

    assert result.array.values == (10.0, None, 30.0)
    assert [failure.url for failure in result.failures] == ["1.covjson"]


def test_assemble_async_fail_fast_raises_fetcherror_chaining_cause() -> None:
    tiled = _one_d_tiled(2)

    async def fetch(url: str) -> bytes:
        raise KeyError(url)

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(tiled.assemble_async(fetch))

    assert isinstance(excinfo.value.__cause__, KeyError)


def test_assemble_async_does_not_swallow_cancellation() -> None:
    tiled = _one_d_tiled(2)

    async def fetch(url: str) -> bytes:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tiled.assemble_async(fetch, strategy=collect_all))


def _tile_store(
    full: "np.ndarray", tiled: TiledNdArray, tileset: int
) -> dict[str, bytes]:
    """Slice ``full`` into a tile set's tiles, keyed by their (independent) URLs.

    Built without the production layout/expander code so the assembly tests stay
    an independent check: each tile is the matching slice of the known full array.
    """
    tile_shape = tiled.tile_sets[tileset].tile_shape
    template = tiled.tile_sets[tileset].url_template

    per_axis: list[list[tuple[int | None, slice]]] = []

    for size, tile_size in zip(tiled.shape, tile_shape, strict=True):
        if tile_size is None:
            per_axis.append([(None, slice(0, size))])
        else:
            count = -(-size // tile_size)
            per_axis.append(
                [
                    (o, slice(o * tile_size, min((o + 1) * tile_size, size)))
                    for o in range(count)
                ]
            )

    store: dict[str, bytes] = {}

    for combination in itertools.product(*per_axis):
        url = template

        for name, (ordinal, _) in zip(tiled.axis_names, combination, strict=True):
            if ordinal is not None:
                url = url.replace("{" + name + "}", str(ordinal))

        slices = tuple(axis_slice for _, axis_slice in combination)
        store[url] = encode(NdArray.from_numpy(full[slices], tiled.axis_names))

    return store


def _one_d_tiled(size: int) -> TiledNdArray:
    """A 1-D float array of ``size`` single-element tiles keyed ``{x}.covjson``."""
    return TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(size,),
        tile_sets=(TileSet(tile_shape=(1,), url_template="{x}.covjson"),),
    )


def _scalar_tile(value: float) -> bytes:
    """Encode a single-element float tile, as `_one_d_tiled` expects per position."""
    tile = NdArray(data_type="float", values=(value,), shape=(1,), axis_names=("x",))

    return encode(tile)
