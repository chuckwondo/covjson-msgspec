"""Behavioral tests for resolve_references and the injected fetcher seam."""

from collections.abc import Callable

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
)


def _store_fetcher(store: dict[str, bytes]) -> Callable[[str], bytes]:
    """A Fetch backed by an in-memory dict of canned documents."""
    return store.__getitem__


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


def test_resolves_url_domain() -> None:
    fetch = _store_fetcher({"d": encode(_domain())})
    cov = Coverage(domain="d", ranges={})

    resolved = resolve_references(cov, fetch)

    assert isinstance(resolved.domain, Domain)
    assert resolved.domain.domain_type == "Point"


def test_resolves_url_range_to_ndarray() -> None:
    fetch = _store_fetcher({"t": encode(_ndarray())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch)

    arr = resolved.ranges["t"]
    assert isinstance(arr, NdArray)
    assert arr.values == (280.0,)


def test_resolves_url_range_to_tiled_ndarray() -> None:
    fetch = _store_fetcher({"t": encode(_tiled())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    resolved = resolve_references(cov, fetch)

    assert isinstance(resolved.ranges["t"], TiledNdArray)


def test_leaves_inline_members_untouched_and_resolves_mixed_ranges() -> None:
    inline = _ndarray()
    fetch = _store_fetcher({"b": encode(NdArray(data_type="float", values=(9.0,)))})
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
    fetch = _store_fetcher({"d": encode(_domain()), "t": encode(_ndarray())})
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
    fetch = _store_fetcher({"d": b"not json"})
    cov = Coverage(domain="d", ranges={})

    with pytest.raises(ValueError, match=r"fetched from 'd'"):
        resolve_references(cov, fetch)


def test_fetcher_errors_propagate_unchanged() -> None:
    fetch = _store_fetcher({})  # empty store -> KeyError
    cov = Coverage(domain="missing", ranges={})

    with pytest.raises(KeyError):
        resolve_references(cov, fetch)


def test_coverage_delegate_matches_the_function() -> None:
    fetch = _store_fetcher({"t": encode(_ndarray())})
    cov = Coverage(domain=_domain(), ranges={"t": "t"})

    assert cov.resolve_references(fetch) == resolve_references(cov, fetch)


def test_collection_delegate_matches_the_function() -> None:
    fetch = _store_fetcher({"t": encode(_ndarray())})
    collection = CoverageCollection(
        coverages=(Coverage(domain=_domain(), ranges={"t": "t"}),)
    )

    assert collection.resolve_references(fetch) == resolve_references(collection, fetch)
