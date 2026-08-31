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
        '{"type":"NdArray","dataType":"float","values":[[1,2]]}',
        '{"type":"NdArray","dataType":"float","values":[true]}',
    ],
)
def test_bare_ndarray_enforces_scalar_union_on_decode(blob: str) -> None:
    # Verifies that removing Generic[T] did not loosen element-type enforcement:
    # msgspec still enforces tuple[float | int | str | None, ...] directly, so
    # nested arrays and booleans are rejected at decode time.
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(blob, type=NdArray)


def test_ndarray_zero_dimensional_defaults() -> None:
    arr = msgspec.json.decode(
        '{"type":"NdArray","dataType":"float","values":[42.0]}', type=NdArray
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


def test_tiled_ndarray_requires_non_empty_shape() -> None:
    # Spec 6.3: `shape` MUST be a non-empty array of integers.
    with pytest.raises(ValueError, match="`shape` must be non-empty"):
        TiledNdArray(
            data_type="float",
            axis_names=(),
            shape=(),
            tile_sets=(TileSet(tile_shape=(), url_template="u"),),
        )


def test_tiled_ndarray_requires_non_empty_tile_sets() -> None:
    # Spec 6.3: `tileSets` MUST be a non-empty array of TileSet objects.
    with pytest.raises(ValueError, match="`tileSets` must be non-empty"):
        TiledNdArray(data_type="float", axis_names=("x",), shape=(2,), tile_sets=())


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        (
            """
            {
              "type": "TiledNdArray",
              "dataType": "float",
              "axisNames": [],
              "shape": [],
              "tileSets": [{"tileShape": [], "urlTemplate": "t"}]
            }
            """,
            "`shape` must be non-empty",
        ),
        (
            """
            {
              "type": "TiledNdArray",
              "dataType": "float",
              "axisNames": ["x"],
              "shape": [1],
              "tileSets": []
            }
            """,
            "`tileSets` must be non-empty",
        ),
    ],
    ids=("empty-shape", "empty-tile-sets"),
)
def test_tiled_ndarray_non_empty_rules_apply_on_decode(
    blob: str, expected: str
) -> None:
    # Both guards live in __post_init__, which msgspec runs during decoding, so
    # each must reject a wire document and not only a direct construction.
    with pytest.raises(ValueError, match=expected):
        msgspec.json.decode(blob, type=TiledNdArray)


def test_tile_shape_allows_null() -> None:
    blob = """
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


@pytest.mark.parametrize(
    "index", range(-len(_spec_tiled().tile_sets), len(_spec_tiled().tile_sets))
)
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


@pytest.mark.parametrize("index", (5, -5))
def test_assemble_tileset_index_out_of_range_errors(index: int) -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached

    with pytest.raises(IndexError, match="out of range"):
        _spec_tiled().assemble(store_fetcher(empty), tileset=index)


def test_assemble_invalid_tile_document_reports_url() -> None:
    tiled = _one_d_tiled(1)

    # An undecodable tile is an unrecoverable failure; fail_fast raises a
    # FetchError chained from the ReferencedDocumentError, naming the tile's URL.
    with pytest.raises(FetchError, match="not valid CoverageJSON") as excinfo:
        tiled.assemble(store_fetcher({"0.covjson": b"nope"}))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE
    assert excinfo.value.failures[0].url == "0.covjson"


@pytest.mark.parametrize(
    "index", range(-len(_spec_tiled().tile_sets), len(_spec_tiled().tile_sets))
)
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


@pytest.mark.parametrize("index", (5, -5))
def test_assemble_async_tileset_index_out_of_range_errors(index: int) -> None:
    empty: dict[str, bytes] = {}  # fetch is never reached
    fetch = async_store_fetcher(empty)

    with pytest.raises(IndexError, match="out of range"):
        asyncio.run(_spec_tiled().assemble_async(fetch, tileset=index))


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


@pytest.mark.parametrize(
    ("values", "shape", "data_type", "axis_names", "detail"),
    [
        # Wider than its slot: placed, this would overwrite its neighbor's cell.
        ((10.0, 99.0), (2,), "float", ("x",), "expected shape (1,), got (2,)"),
        # Wider still: placed, this would run off the end of the array.
        ((10.0, 20.0, 30.0), (3,), "float", ("x",), "expected shape (1,), got (3,)"),
        # The right shape, but not the values to fill it.
        ((10.0, 20.0), (1,), "float", ("x",), "expected 1 value(s) for shape (1,)"),
        # The wrong rank entirely.
        ((10.0,), (1, 1), "float", ("x", "y"), "expected axisNames ('x',)"),
        # Spec 6.3 requires each tile's dataType to be the array's ...
        ((10,), (1,), "integer", ("x",), "expected dataType 'float'"),
        # ... and likewise its axisNames.
        ((10.0,), (1,), "float", ("y",), "expected axisNames ('x',), got ('y',)"),
    ],
)
def test_assemble_rejects_tile_that_does_not_match_its_slot(
    values: tuple[float | int, ...],
    shape: tuple[int, ...],
    data_type: Literal["float", "integer", "string"],
    axis_names: tuple[str, ...],
    detail: str,
) -> None:
    tiled = _one_d_tiled(2)
    store = {
        "0.covjson": _tile_bytes(
            values, shape, data_type=data_type, axis_names=axis_names
        ),
        "1.covjson": _scalar_tile(20.0),
    }

    # A tile that does not match its slot is unrecoverable, exactly as an
    # undecodable one is: fail_fast raises a FetchError naming the tile's URL,
    # rather than placing the tile and corrupting the array around it.
    with pytest.raises(FetchError) as excinfo:
        tiled.assemble(store_fetcher(store))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE
    assert excinfo.value.failures[0].url == "0.covjson"
    assert detail in excinfo.value.failures[0].message


def test_assemble_rejects_tile_shorter_than_its_slot() -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(5,),
        tile_sets=(TileSet(tile_shape=(2,), url_template="{x}.covjson"),),
    )
    # fail_fast halts on the first tile, so only "0.covjson" is ever fetched.
    # Edge truncation end to end is test_assemble_handles_remainder_tiles.
    store = {"0.covjson": _tile_bytes((0.0,), (1,))}  # this slot holds two cells

    # Spec 6.3 lets a tile be short only at an edge. Tolerating a short tile
    # elsewhere would leave a hole indistinguishable from one whose tile never
    # loaded, so it is reported like any other nonconformant tile.
    with pytest.raises(FetchError) as excinfo:
        tiled.assemble(store_fetcher(store))

    assert excinfo.value.failures[0].url == "0.covjson"
    assert "expected shape (2,), got (1,)" in excinfo.value.failures[0].message


def test_assemble_collect_all_reports_tile_that_does_not_match_its_slot() -> None:
    tiled = _one_d_tiled(2)
    store = {
        "0.covjson": _tile_bytes((10.0, 99.0), (2,)),  # would overwrite tile 1
        "1.covjson": _scalar_tile(20.0),
    }

    result = tiled.assemble(store_fetcher(store), strategy=collect_all)

    # The oversized tile is dropped rather than placed, so tile 1's value stands
    # and the assembled array no longer depends on the order the tiles arrive in.
    assert result.array.values == (None, 20.0)
    assert [failure.url for failure in result.failures] == ["0.covjson"]
    assert result.failures[0].kind is FailureKind.UNRECOVERABLE
    assert result.failures[0].offsets == (0,)


def test_assemble_reports_only_the_shape_for_a_wrong_sized_tile() -> None:
    tiled = _one_d_tiled(2)
    store = {
        "0.covjson": _tile_bytes((10.0, 99.0), (2,)),  # two values, shape (2,)
        "1.covjson": _scalar_tile(20.0),
    }

    result = tiled.assemble(store_fetcher(store), strategy=collect_all)

    # This tile's own shape and values agree; it is simply sized for a slot two
    # cells wide. The value count is measured against the slot, so checking it
    # here as well would accuse the tile of a count it never claimed. Pinned with
    # `endswith` so a second phrase cannot creep in behind the shape.
    assert result.failures[0].message.endswith("expected shape (1,), got (2,)")


def test_assemble_async_rejects_tile_that_does_not_match_its_slot() -> None:
    tiled = _one_d_tiled(2)
    store = {
        "0.covjson": _tile_bytes((10.0, 99.0), (2,)),
        "1.covjson": _scalar_tile(20.0),
    }

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(tiled.assemble_async(async_store_fetcher(store)))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    assert excinfo.value.failures[0].kind is FailureKind.UNRECOVERABLE
    assert excinfo.value.failures[0].url == "0.covjson"


@pytest.mark.parametrize(
    ("axis_names", "tile_shape", "template", "detail"),
    [
        (("x", "y"), (1,), "{x}.covjson", "axisNames has 2 name(s) but shape has 1"),
        (("x",), (0,), "{x}.covjson", "tileShape (0,) has a non-positive entry"),
        (("x",), (-1,), "{x}.covjson", "tileShape (-1,) has a non-positive entry"),
        (("x",), (1,), "{z}.covjson", "references unknown variable(s) 'z'"),
        # Every unknown is named, so one pass repairs the template.
        (
            ("x",),
            (1,),
            "{b}-{a}-{x}.covjson",
            "references unknown variable(s) 'b', 'a'",
        ),
    ],
)
def test_assemble_rejects_a_tiling_it_cannot_lay_out(
    axis_names: tuple[str, ...],
    tile_shape: tuple[int | None, ...],
    template: str,
    detail: str,
) -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=axis_names,
        shape=(2,),
        tile_sets=(TileSet(tile_shape=tile_shape, url_template=template),),
    )

    # These decode (validate reports them as tiled-ndarray.shape-rank,
    # tiled-ndarray.tile-shape-not-positive and
    # tiled-ndarray.url-template-unknown-variable), but no tiling follows from
    # any of them: one cannot name an axis for its ordinal, one has no tile
    # count, and one has no ordinal to expand its variable with. All are caught
    # before any fetch, so no strategy can turn them into failures. A negative
    # tile size would otherwise assemble to a hole-filled array with nothing
    # reported at all.
    with pytest.raises(ValueError) as excinfo:
        tiled.assemble(store_fetcher({}))

    assert detail in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(tiled.assemble_async(async_store_fetcher({})))

    assert detail in str(excinfo.value)


def test_assemble_rejects_an_uncountable_tile_set_chosen_explicitly() -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(2,),
        tile_sets=(
            TileSet(tile_shape=(1,), url_template="a{x}.covjson"),
            TileSet(tile_shape=(0,), url_template="b{x}.covjson"),
        ),
    )

    # An explicit index never counts a tile set, so `_select_tile_set` cannot
    # reject this one; the guard that does is `_tile_layout`'s. Without a case
    # that picks a bad tile set by index, deleting that guard leaves the suite
    # green while assembly falls back to enumerating zero tiles.
    with pytest.raises(ValueError, match="non-positive entry"):
        tiled.assemble(store_fetcher({}), 1)


@pytest.mark.parametrize(
    ("axis_names", "url_template"),
    [
        # x is subdivided but its ordinal is never interpolated.
        (("t", "x"), "{t}.covjson"),
        # Both ordinals are interpolated, but into the same variable, so the
        # substitution mapping collapses and half the URLs repeat.
        (("x", "x"), "{x}.covjson"),
    ],
    ids=("no-variable-for-axis", "duplicate-axis-name"),
)
def test_assemble_rejects_a_template_that_repeats_a_tile(
    axis_names: tuple[str, ...], url_template: str
) -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=axis_names,
        shape=(2, 2),
        tile_sets=(TileSet(tile_shape=(1, 1), url_template=url_template),),
    )

    # Four slots but only two URLs, and each fetched tile matches its slot's
    # expected shape (1, 1), so `_check_tile` would pass all four and the array
    # would silently read as two documents each standing in for two cells.
    with pytest.raises(ValueError, match="distinct URL"):
        tiled.assemble(store_fetcher({}))


@pytest.mark.parametrize("index", range(len(_spec_tiled().tile_sets)))
def test_assemble_fetches_a_distinct_document_per_slot(index: int) -> None:
    tiled = _spec_tiled()
    store = _tile_store(np.arange(100, dtype=float).reshape(2, 5, 10), tiled, index)
    requested: list[str] = []

    def fetch(url: str) -> bytes:
        requested.append(url)

        return store[url]

    tiled.assemble(fetch, index)

    # The property the layout guard exists for, asserted directly rather than
    # through a template that happens to violate it: every slot resolves to its
    # own document, so no URL is requested twice and every tile is used once.
    assert len(set(requested)) == len(requested)
    assert set(requested) == set(store)


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


@pytest.mark.parametrize(
    ("blob", "typ"),
    [
        (
            """
            {
              "type": "NdArray",
              "@context": "https://ex/ctx",
              "dataType": "float",
              "values": [1.0]
            }
            """,
            NdArray,
        ),
        (
            """
            {
              "type": "TiledNdArray",
              "@context": "https://ex/ctx",
              "dataType": "float",
              "axisNames": ["x"],
              "shape": [1],
              "tileSets": [
                {
                  "tileShape": [1],
                  "urlTemplate": "t/{x}"
                }
              ]
            }
            """,
            TiledNdArray,
        ),
    ],
    ids=("ndarray", "tiled-ndarray"),
)
def test_standalone_range_root_preserves_context(
    blob: str, typ: type[NdArray] | type[TiledNdArray]
) -> None:
    # NdArray / TiledNdArray may stand alone as a document root (spec section 6),
    # so each carries the root JSON-LD @context (section 8).
    root = msgspec.json.decode(blob, type=typ)

    assert root.context == "https://ex/ctx"
    assert msgspec.json.decode(msgspec.json.encode(root), type=typ) == root


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
    return _tile_bytes((value,), (1,))


def _tile_bytes(
    values: tuple[float | int, ...],
    shape: tuple[int, ...],
    *,
    data_type: Literal["float", "integer", "string"] = "float",
    axis_names: tuple[str, ...] = ("x",),
) -> bytes:
    """Encode an arbitrary tile document, including a deliberately malformed one.

    The general form of `_scalar_tile`: every member a tile set constrains is a
    parameter, so a test can hand `assemble` a tile that does not match its slot.
    """
    tile = NdArray(
        data_type=data_type, values=values, shape=shape, axis_names=axis_names
    )

    return encode(tile)
