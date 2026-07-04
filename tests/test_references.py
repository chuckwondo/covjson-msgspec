"""Behavioral tests for resolve_references and the injected fetcher seam."""

import asyncio

import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    NdArray,
    TiledNdArray,
    TileSet,
    encode,
    resolve_references,
    resolve_references_async,
)
from fetchers import async_store_fetcher, store_fetcher


def test_resolves_url_domain() -> None:
    fetch = store_fetcher({"d": encode(_domain())})
    cov = Coverage(domain="d", ranges={})

    resolved = resolve_references(cov, fetch)

    assert isinstance(resolved.domain, Domain)
    assert resolved.domain.domain_type == "Point"


def test_resolves_url_range_to_ndarray() -> None:
    fetch = store_fetcher({"t": encode(_ndarray())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch)

    arr = resolved.ranges["t"]
    assert isinstance(arr, NdArray)
    assert arr.values == (280.0,)


def test_resolves_url_range_to_tiled_ndarray() -> None:
    fetch = store_fetcher({"t": encode(_tiled())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch)

    assert isinstance(resolved.ranges["t"], TiledNdArray)


def test_leaves_inline_members_untouched_and_resolves_mixed_ranges() -> None:
    inline = _ndarray()
    fetch = store_fetcher({"b": encode(NdArray(data_type="float", values=(9.0,)))})
    cov = Coverage(domain=_domain(), ranges={"a": inline, "b": "b"})

    resolved = resolve_references(cov, fetch)

    # The inline range is preserved as-is; only the URL range is fetched.
    assert resolved.ranges["a"] is inline
    resolved_b = resolved.ranges["b"]
    assert isinstance(resolved_b, NdArray)
    assert resolved_b.values == (9.0,)


def test_coverage_without_references_returns_same_instance() -> None:
    cov = Coverage(domain=_domain(), ranges={"t": _ndarray()})

    def _explode(url: str) -> bytes:  # must never be called
        msg = f"unexpected fetch of {url!r}"
        raise AssertionError(msg)

    assert resolve_references(cov, _explode) is cov


def test_resolves_each_member_of_a_collection() -> None:
    fetch = store_fetcher({"d": encode(_domain()), "t": encode(_ndarray())})
    collection = CoverageCollection(
        coverages=(
            Coverage(domain="d", ranges={}),
            Coverage(domain=_domain(), ranges={"t": "t"}),
        )
    )

    resolved = resolve_references(collection, fetch)

    assert isinstance(resolved, CoverageCollection)
    first, second = resolved.coverages
    assert isinstance(first.domain, Domain)
    assert isinstance(second.ranges["t"], NdArray)


def test_decode_failure_is_reported_against_the_url() -> None:
    fetch = store_fetcher({"d": b"not json"})
    cov = Coverage(domain="d", ranges={})

    with pytest.raises(ValueError, match=r"fetched from 'd'"):
        resolve_references(cov, fetch)


def test_fetcher_errors_propagate_unchanged() -> None:
    fetch = store_fetcher({})  # empty store -> KeyError
    cov = Coverage(domain="missing", ranges={})

    with pytest.raises(KeyError):
        resolve_references(cov, fetch)


def test_coverage_delegate_matches_the_function() -> None:
    fetch = store_fetcher({"t": encode(_ndarray())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    assert cov.resolve_references(fetch) == resolve_references(cov, fetch)


def test_collection_delegate_matches_the_function() -> None:
    fetch = store_fetcher({"t": encode(_ndarray())})
    collection = CoverageCollection(
        coverages=(Coverage(domain=_domain(), ranges={"t": "t"}),)
    )

    assert collection.resolve_references(fetch) == resolve_references(collection, fetch)


def test_async_resolve_matches_sync_for_a_collection() -> None:
    store = {"d": encode(_domain()), "t": encode(_ndarray())}
    collection = CoverageCollection(
        coverages=(
            Coverage(domain="d", ranges={}),
            Coverage(domain=_domain(), ranges={"t": "t"}),
        )
    )

    resolved = asyncio.run(
        resolve_references_async(collection, async_store_fetcher(store))
    )

    assert resolved == resolve_references(collection, store_fetcher(store))


def test_async_resolves_url_range_to_tiled_ndarray() -> None:
    fetch = async_store_fetcher({"t": encode(_tiled())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = asyncio.run(resolve_references_async(cov, fetch))

    # One level deep: the referenced TiledNdArray is inlined, not assembled.
    assert isinstance(resolved.ranges["t"], TiledNdArray)


def test_async_coverage_without_references_returns_same_instance() -> None:
    cov = Coverage(domain=_domain(), ranges={"t": _ndarray()})

    async def _explode(url: str) -> bytes:  # must never be awaited
        msg = f"unexpected fetch of {url!r}"
        raise AssertionError(msg)

    assert asyncio.run(resolve_references_async(cov, _explode)) is cov


def test_async_decode_failure_is_reported_against_the_url() -> None:
    fetch = async_store_fetcher({"d": b"not json"})
    cov = Coverage(domain="d", ranges={})

    with pytest.raises(ValueError, match=r"fetched from 'd'"):
        asyncio.run(resolve_references_async(cov, fetch))


def test_async_fetcher_errors_propagate_unchanged() -> None:
    fetch = async_store_fetcher({})  # empty store -> KeyError
    cov = Coverage(domain="missing", ranges={})

    with pytest.raises(KeyError):
        asyncio.run(resolve_references_async(cov, fetch))


def test_async_delegates_match_the_function() -> None:
    store = {"t": encode(_ndarray())}
    cov = Coverage(domain=_domain(), ranges={"t": "t"})
    collection = CoverageCollection(coverages=(cov,))

    assert asyncio.run(cov.resolve_references_async(async_store_fetcher(store))) == (
        resolve_references(cov, store_fetcher(store))
    )
    assert asyncio.run(
        collection.resolve_references_async(async_store_fetcher(store))
    ) == resolve_references(collection, store_fetcher(store))


def _domain() -> Domain:
    return Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))


def _ndarray() -> NdArray:
    return NdArray(data_type="float", values=(280.0,))


def _tiled() -> TiledNdArray:
    return TiledNdArray(
        data_type="float",
        axis_names=("x",),
        shape=(2,),
        tile_sets=(TileSet(tile_shape=(1,), url_template="tiles/{x}.covjson"),),
    )
