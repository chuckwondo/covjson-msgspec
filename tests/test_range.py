"""Behavioral tests for coverage ranges (NdArray / TiledNdArray)."""

import msgspec
import pytest

from covjson_msgspec import NdArray, TiledNdArray, TileSet


def test_ndarray_roundtrips() -> None:
    arr = NdArray(
        data_type="float", values=(1.0, None, 3.0), shape=(3,), axis_names=("x",)
    )
    back = msgspec.json.decode(msgspec.json.encode(arr), type=NdArray)
    assert back == arr
    assert back.values == (1.0, None, 3.0)


def test_ndarray_typed_decode_and_rejection() -> None:
    blob = b'{"type":"NdArray","dataType":"float","values":[1.0,2.0]}'
    typed = msgspec.json.decode(blob, type=NdArray[float])
    assert typed.values == (1.0, 2.0)

    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(
            b'{"type":"NdArray","dataType":"integer","values":[1.5]}',
            type=NdArray[int],
        )


def test_bare_ndarray_enforces_scalar_bound_on_decode() -> None:
    # msgspec ignores the TypeVar PEP 696 *default* at runtime but honors its
    # *bound*, so a bare NdArray still rejects non-scalar element values.
    for blob in (
        b'{"type":"NdArray","dataType":"float","values":[[1,2]]}',
        b'{"type":"NdArray","dataType":"float","values":[true]}',
    ):
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
    blob = (
        b'{"type":"TiledNdArray","dataType":"float","axisNames":["t","y","x"],'
        b'"shape":[4,100,100],"tileSets":[{"tileShape":[null,100,100],'
        b'"urlTemplate":"http://ex/{t}.covjson"}]}'
    )
    tiled = msgspec.json.decode(blob, type=TiledNdArray)
    assert tiled.tile_sets[0].tile_shape == (None, 100, 100)
