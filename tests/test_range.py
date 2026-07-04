"""Behavioral tests for coverage ranges (NdArray / TiledNdArray)."""

import asyncio
import itertools

import msgspec
import numpy as np
import pytest

from covjson_msgspec import NdArray, TiledNdArray, TileSet, encode
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
    result = tiled.assemble(store_fetcher(store), tileset=index)

    assert result.shape == (2, 5, 10)
    assert result.axis_names == ("t", "y", "x")
    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_default_picks_the_fewest_tiles() -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    # Store ONLY tileset A's single tile; default selection must choose it (and
    # so never request a URL from the 2-tile or 12-tile sets).
    store = _tile_store(full, tiled, 0)

    result = tiled.assemble(store_fetcher(store))

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

    result = tiled.assemble(store_fetcher(store))

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
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(1,),
        tile_sets=(TileSet(tile_shape=(1,), url_template="{x}.covjson"),),
    )

    with pytest.raises(ValueError, match="not valid CoverageJSON"):
        tiled.assemble(store_fetcher({"0.covjson": b"nope"}))


@pytest.mark.parametrize("index", range(len(_spec_tiled().tile_sets)))
def test_assemble_async_matches_sync_for_each_tileset(index: int) -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    store = _tile_store(full, tiled, index)
    result = asyncio.run(
        tiled.assemble_async(async_store_fetcher(store), tileset=index)
    )

    assert result.shape == (2, 5, 10)
    assert result.axis_names == ("t", "y", "x")
    assert result.values == tuple(full.ravel(order="C").tolist())


def test_assemble_async_default_picks_the_fewest_tiles() -> None:
    full = np.arange(100, dtype=float).reshape(2, 5, 10)
    tiled = _spec_tiled()
    # Tile set 0 is the whole array in one tile; the default must reproduce it.
    store = _tile_store(full, tiled, 0)

    result = asyncio.run(tiled.assemble_async(async_store_fetcher(store)))

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
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(1,),
        tile_sets=(TileSet(tile_shape=(1,), url_template="{x}.covjson"),),
    )

    with pytest.raises(ValueError, match="not valid CoverageJSON"):
        asyncio.run(tiled.assemble_async(async_store_fetcher({"0.covjson": b"nope"})))


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
