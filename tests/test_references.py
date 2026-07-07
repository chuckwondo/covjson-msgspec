"""Behavioral tests for resolve_references and the injected fetcher seam."""

import asyncio

import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    FailureKind,
    FetchError,
    NdArray,
    ReferencedDocumentError,
    TiledNdArray,
    TileSet,
    collect_all,
    encode,
    resolve_references,
    resolve_references_async,
)
from fetchers import async_store_fetcher, store_fetcher


def test_resolves_url_domain() -> None:
    fetch = store_fetcher({"d": encode(_domain())})
    cov = Coverage(domain="d", ranges={})

    resolved = resolve_references(cov, fetch).value

    assert isinstance(resolved.domain, Domain)
    assert resolved.domain.domain_type == "Point"


def test_resolves_url_range_to_ndarray() -> None:
    fetch = store_fetcher({"t": encode(_ndarray())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch).value

    arr = resolved.ranges["t"]
    assert isinstance(arr, NdArray)
    assert arr.values == (280.0,)


def test_resolves_url_range_to_tiled_ndarray() -> None:
    fetch = store_fetcher({"t": encode(_tiled())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch).value

    assert isinstance(resolved.ranges["t"], TiledNdArray)


def test_leaves_inline_members_untouched_and_resolves_mixed_ranges() -> None:
    inline = _ndarray()
    fetch = store_fetcher({"b": encode(NdArray(data_type="float", values=(9.0,)))})
    cov = Coverage(domain=_domain(), ranges={"a": inline, "b": "b"})

    resolved = resolve_references(cov, fetch).value

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

    assert resolve_references(cov, _explode).value is cov


def test_resolves_each_member_of_a_collection() -> None:
    fetch = store_fetcher({"d": encode(_domain()), "t": encode(_ndarray())})
    collection = CoverageCollection(
        coverages=(
            Coverage(domain="d", ranges={}),
            Coverage(domain=_domain(), ranges={"t": "t"}),
        )
    )

    resolved = resolve_references(collection, fetch).value

    assert isinstance(resolved, CoverageCollection)
    first, second = resolved.coverages
    assert isinstance(first.domain, Domain)
    assert isinstance(second.ranges["t"], NdArray)


def test_decode_failure_raises_fetch_error_chained_from_decode() -> None:
    fetch = store_fetcher({"d": b"not json"})
    cov = Coverage(domain="d", ranges={})

    with pytest.raises(FetchError) as excinfo:
        resolve_references(cov, fetch)

    # fail_fast raises FetchError chained from the decode error (only the caught
    # type changes; the underlying error is preserved via __cause__).
    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    # FetchError.failures is typed over the base FetchFailure; the slot /
    # coverage_index attribution is checked in the collect_all tests, whose
    # ResolveResult.failures is typed over ReferenceFailure.
    (failure,) = excinfo.value.failures
    assert failure.kind is FailureKind.UNRECOVERABLE
    assert failure.url == "d"


def test_fetcher_error_raises_fetch_error_chained_from_fetcher() -> None:
    fetch = store_fetcher({})  # empty store -> KeyError
    cov = Coverage(domain="missing", ranges={})

    with pytest.raises(FetchError) as excinfo:
        resolve_references(cov, fetch)

    assert isinstance(excinfo.value.__cause__, KeyError)
    (failure,) = excinfo.value.failures
    assert failure.kind is FailureKind.TRANSIENT


def test_collect_all_leaves_failed_domain_as_url_and_reports_it() -> None:
    fetch = store_fetcher({})  # domain URL missing -> transient failure
    cov = Coverage(domain="missing", ranges={})

    result = resolve_references(cov, fetch, strategy=collect_all)

    assert result.value.domain == "missing"  # unresolved: kept as its URL string
    (failure,) = result.failures
    assert failure.slot == "domain"
    assert failure.coverage_index == 0
    assert failure.kind is FailureKind.TRANSIENT
    assert failure.url == "missing"


def test_collect_all_leaves_failed_range_as_url_and_resolves_the_rest() -> None:
    fetch = store_fetcher({"d": encode(_domain())})  # range "t" missing
    inline = _ndarray()
    cov = Coverage(domain="d", ranges={"t": "t", "u": inline})

    result = resolve_references(cov, fetch, strategy=collect_all)

    assert isinstance(result.value.domain, Domain)  # resolved
    assert result.value.ranges["t"] == "t"  # unresolved: URL string
    assert result.value.ranges["u"] is inline  # inline untouched
    (failure,) = result.failures
    assert failure.slot == "t"
    assert failure.coverage_index == 0


def test_collect_all_reports_collection_member_index() -> None:
    fetch = store_fetcher({"d": encode(_domain())})  # member 1's range "t" missing
    collection = CoverageCollection(
        coverages=(
            Coverage(domain="d", ranges={}),
            Coverage(domain=_domain(), ranges={"t": "t"}),
        )
    )

    result = resolve_references(collection, fetch, strategy=collect_all)

    first, second = result.value.coverages
    assert isinstance(first.domain, Domain)  # member 0 fully resolved
    assert second.ranges["t"] == "t"  # member 1 unresolved
    (failure,) = result.failures
    assert failure.coverage_index == 1
    assert failure.slot == "t"


def test_range_keyed_domain_does_not_collide_with_the_domain() -> None:
    fetch = store_fetcher(
        {"the-domain": encode(_domain()), "the-range": encode(_ndarray())}
    )
    # A range whose key is literally "domain", alongside a real domain URL: the
    # two must not share a decoder or a resolved-map slot.
    cov = Coverage(domain="the-domain", ranges={"domain": "the-range"})

    resolved = resolve_references(cov, fetch).value

    assert isinstance(resolved.domain, Domain)  # decoded as a Domain
    range_named_domain = resolved.ranges["domain"]
    assert isinstance(range_named_domain, NdArray)  # decoded as a range, not swapped
    assert range_named_domain.values == (280.0,)


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

    resolved = asyncio.run(resolve_references_async(cov, fetch)).value

    # One level deep: the referenced TiledNdArray is inlined, not assembled.
    assert isinstance(resolved.ranges["t"], TiledNdArray)


def test_async_coverage_without_references_returns_same_instance() -> None:
    cov = Coverage(domain=_domain(), ranges={"t": _ndarray()})

    async def _explode(url: str) -> bytes:  # must never be awaited
        msg = f"unexpected fetch of {url!r}"
        raise AssertionError(msg)

    assert asyncio.run(resolve_references_async(cov, _explode)).value is cov


def test_async_decode_failure_raises_fetch_error_chained_from_decode() -> None:
    fetch = async_store_fetcher({"d": b"not json"})
    cov = Coverage(domain="d", ranges={})

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(resolve_references_async(cov, fetch))

    assert isinstance(excinfo.value.__cause__, ReferencedDocumentError)
    (failure,) = excinfo.value.failures
    assert failure.kind is FailureKind.UNRECOVERABLE


def test_async_fetcher_error_raises_fetch_error_chained_from_fetcher() -> None:
    fetch = async_store_fetcher({})  # empty store -> KeyError
    cov = Coverage(domain="missing", ranges={})

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(resolve_references_async(cov, fetch))

    assert isinstance(excinfo.value.__cause__, KeyError)


def test_async_collect_all_leaves_failed_range_as_url() -> None:
    fetch = async_store_fetcher({"d": encode(_domain())})  # range "t" missing
    cov = Coverage(domain="d", ranges={"t": "t"})

    result = asyncio.run(resolve_references_async(cov, fetch, strategy=collect_all))

    assert isinstance(result.value.domain, Domain)
    assert result.value.ranges["t"] == "t"
    (failure,) = result.failures
    assert failure.slot == "t"
    assert failure.coverage_index == 0


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
