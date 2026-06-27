"""Resolve a coverage's URL-string references into inline documents.

CoverageJSON lets a `Coverage` defer parts of itself to separate documents: its
``domain`` may be a URL string instead of an inline `Domain`, and any entry in
its ``ranges`` may be a URL string instead of an inline `NdArray` /
`TiledNdArray`. `resolve_references` walks a coverage (or every member of a
`CoverageCollection`) and replaces each such URL with the document fetched and
decoded from it, returning a new, fully-inlined value.

Fetching is injected: the caller supplies a `Fetch` (see `covjson_msgspec._fetch`)
and this module performs no I/O of its own. Resolution is one level deep and
strictly about *URL strings*: it inlines a `TiledNdArray` that a range URL points
to, but it does not fetch and assemble that tiled array's tiles (that is the job
of tile assembly), and a resolved `Domain` has no further references to follow.

Spec: [ranges object][spec-ranges] (a range may be a URL string) and
[Coverage objects][spec-coverage] (a domain may be a URL string).

[spec-ranges]: https://github.com/covjson/specification/blob/master/spec.md#92-ranges-object
[spec-coverage]: https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Final, TypeVar, overload

import msgspec

from covjson_msgspec._fetch import (
    AsyncFetch,
    Fetch,
    fetch_and_decode,
    fetch_and_decode_async,
)
from covjson_msgspec.coverage import Coverage, CoverageCollection
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray, TiledNdArray

_T = TypeVar("_T")

# A range URL points to a standalone range document (an inline NdArray or a
# TiledNdArray); a domain URL points to a standalone Domain. Both decoders are
# built once and reused. The union-argument constructor returns Any, so the
# explicit Final[Decoder[...]] annotation restores the precise type.
_DOMAIN_DECODER: Final[msgspec.json.Decoder[Domain]] = msgspec.json.Decoder(Domain)
_RANGE_DECODER: Final[msgspec.json.Decoder[NdArray | TiledNdArray]] = (
    msgspec.json.Decoder(NdArray | TiledNdArray)
)


@overload
def resolve_references(obj: Coverage, fetch: Fetch) -> Coverage: ...


@overload
def resolve_references(obj: CoverageCollection, fetch: Fetch) -> CoverageCollection: ...


def resolve_references(
    obj: Coverage | CoverageCollection, fetch: Fetch
) -> Coverage | CoverageCollection:
    """Inline a coverage's (or collection's) URL-string domain and range references.

    Returns a new value of the same type with every URL-string ``domain`` and
    every URL-string entry in ``ranges`` replaced by the document fetched from it
    and decoded; inline domains and ranges are left untouched, and a value with
    no references is returned unchanged. For a `CoverageCollection`, every member
    is resolved (collection-level inheritance is not applied; call
    `CoverageCollection.resolved_coverages` first if you need that).

    This follows URL strings only. A range URL that points to a `TiledNdArray` is
    inlined as that tiled array, not assembled from its tiles.

    Parameters
    ----------
    obj
        The coverage or collection to resolve.
    fetch
        A `Fetch` mapping a referenced document's URL to its raw bytes. All I/O
        (and any caching, auth, or retries) lives in this callable.

    Returns
    -------
    Coverage or CoverageCollection
        A new value of the same type with its URL references inlined.

    Raises
    ------
    ValueError
        If a fetched document does not decode to the expected type (a `Domain`
        for a domain URL, an `NdArray` or `TiledNdArray` for a range URL).

    Examples
    --------
    Supply the referenced documents through a fetcher; a ``dict`` of canned bytes
    keyed by URL is the simplest one. Here both the domain and the range are
    URL references that get inlined:

    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray, encode
    >>> store = {
    ...     "https://ex/domain.json": encode(
    ...         Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    ...     ),
    ...     "https://ex/t.json": encode(NdArray(data_type="float", values=(280.0,))),
    ... }
    >>> cov = Coverage(
    ...     domain="https://ex/domain.json", ranges={"t": "https://ex/t.json"}
    ... )
    >>> resolved = resolve_references(cov, store.__getitem__)
    >>> resolved.domain.domain_type
    'Point'
    >>> resolved.ranges["t"].values
    (280.0,)
    """
    domain_urls, range_urls = _collect_refs(obj)
    domains = {u: fetch_and_decode(fetch, u, _DOMAIN_DECODER) for u in domain_urls}
    ranges = {u: fetch_and_decode(fetch, u, _RANGE_DECODER) for u in range_urls}

    return _rebuild(obj, domains, ranges)


@overload
async def resolve_references_async(obj: Coverage, fetch: AsyncFetch) -> Coverage: ...


@overload
async def resolve_references_async(
    obj: CoverageCollection, fetch: AsyncFetch
) -> CoverageCollection: ...


async def resolve_references_async(
    obj: Coverage | CoverageCollection, fetch: AsyncFetch
) -> Coverage | CoverageCollection:
    """Inline URL-string references, fetching them concurrently.

    The awaitable counterpart of `resolve_references` with identical semantics and
    return type; only the fetching differs. The independent references (a domain
    and every range of every member) are fetched concurrently via `asyncio.gather`,
    so this scales over a large `CoverageCollection` far better than awaiting each
    in turn. Like the sync version, this follows URL strings only: a range URL that
    points to a `TiledNdArray` is inlined as that tiled array, not assembled.

    Parameters
    ----------
    obj
        The coverage or collection to resolve.
    fetch
        An `AsyncFetch` awaitably mapping a referenced document's URL to its raw
        bytes. All I/O (and any caching, auth, or retries) lives in this callable.

    Returns
    -------
    Coverage or CoverageCollection
        A new value of the same type with its URL references inlined.

    Raises
    ------
    ValueError
        If a fetched document does not decode to the expected type.

    Notes
    -----
    There is no built-in concurrency cap: the unbounded fan-out is left to the
    injected `AsyncFetch`, which owns all I/O policy. To bound it, wrap the fetcher
    in a semaphore::

        sem = asyncio.Semaphore(8)

        async def limited(url: str) -> bytes:
            async with sem:
                return await fetch(url)

        await resolve_references_async(obj, limited)

    Examples
    --------
    >>> import asyncio
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray, encode
    >>> store = {
    ...     "https://ex/domain.json": encode(
    ...         Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    ...     ),
    ...     "https://ex/t.json": encode(NdArray(data_type="float", values=(280.0,))),
    ... }
    >>> async def fetch(url):
    ...     return store[url]
    >>> cov = Coverage(
    ...     domain="https://ex/domain.json", ranges={"t": "https://ex/t.json"}
    ... )
    >>> resolved = asyncio.run(resolve_references_async(cov, fetch))
    >>> resolved.domain.domain_type
    'Point'
    >>> resolved.ranges["t"].values
    (280.0,)
    """
    domain_urls, range_urls = _collect_refs(obj)
    domains, ranges = await asyncio.gather(
        _fetch_map_async(fetch, domain_urls, _DOMAIN_DECODER),
        _fetch_map_async(fetch, range_urls, _RANGE_DECODER),
    )

    return _rebuild(obj, domains, ranges)


def _coverages(obj: Coverage | CoverageCollection) -> tuple[Coverage, ...]:
    """The coverages to walk: the value itself, or every collection member.

    Examples
    --------
    >>> from covjson_msgspec import Coverage, CoverageCollection
    >>> cov = Coverage(domain="u", ranges={})
    >>> _coverages(cov) == (cov,)
    True
    >>> _coverages(CoverageCollection(coverages=(cov,))) == (cov,)
    True
    """
    match obj:
        case Coverage():
            return (obj,)
        case CoverageCollection():
            return obj.coverages


def _collect_refs(obj: Coverage | CoverageCollection) -> tuple[set[str], set[str]]:
    """Phase 1 (shared): the domain-URL and range-URL strings the value references.

    Walks the coverage (or every collection member) once, gathering the bare URL
    strings standing in for a `Domain` and for ranges. Two sets (not one) keep the
    domain and range documents on their own decoders. Shared by the sync and async
    drivers, which differ only in how they fetch the collected URLs.

    Parameters
    ----------
    obj
        The coverage or collection to inspect.

    Returns
    -------
    tuple of (set of str, set of str)
        The URL strings used as a ``domain`` and as a range, respectively.

    Examples
    --------
    >>> from covjson_msgspec import Coverage
    >>> cov = Coverage(domain="d", ranges={"t": "r", "u": "r"})
    >>> _collect_refs(cov) == ({"d"}, {"r"})
    True
    """
    coverages = _coverages(obj)
    domain_urls = {
        coverage.domain for coverage in coverages if isinstance(coverage.domain, str)
    }
    range_urls = {
        value
        for coverage in coverages
        for value in coverage.ranges.values()
        if isinstance(value, str)
    }

    return domain_urls, range_urls


async def _fetch_map_async(
    fetch: AsyncFetch, urls: Iterable[str], decoder: msgspec.json.Decoder[_T]
) -> dict[str, _T]:
    """Phase 2 (async): concurrently fetch and decode every URL into a dict.

    Fixes the URL order once, fans the fetches out with `asyncio.gather`, then zips
    the decoded documents back onto their URLs. An empty input yields an empty dict.

    Parameters
    ----------
    fetch
        The caller's `AsyncFetch`.
    urls
        The URL strings to fetch.
    decoder
        A reusable `msgspec.json.Decoder` for the expected document type.

    Returns
    -------
    dict
        Each URL mapped to its fetched-and-decoded document.

    Examples
    --------
    >>> import asyncio
    >>> from covjson_msgspec.domain import Domain
    >>> decoder = msgspec.json.Decoder(Domain)
    >>> store = {
    ...     "u": b'{"type":"Domain","domainType":"Point","axes":{"x":{"values":[1.0]}}}'
    ... }
    >>> async def fetch(url):
    ...     return store[url]
    >>> asyncio.run(_fetch_map_async(fetch, ["u"], decoder))["u"].domain_type
    'Point'
    """
    ordered = tuple(urls)
    # gather, not TaskGroup, so a fetcher's own exception propagates unchanged
    # (matching the sync seam); a TaskGroup would wrap it in an ExceptionGroup.
    docs = await asyncio.gather(
        *(fetch_and_decode_async(fetch, url, decoder) for url in ordered)
    )

    return dict(zip(ordered, docs, strict=True))


def _rebuild(
    obj: Coverage | CoverageCollection,
    domains: Mapping[str, Domain],
    ranges: Mapping[str, NdArray | TiledNdArray],
) -> Coverage | CoverageCollection:
    """Phase 3 (shared): rebuild the value with its URL references inlined.

    Replaces each URL-string ``domain`` and range with the matching fetched
    document from ``domains`` / ``ranges``. Shared by the sync and async drivers,
    which supply those maps from a sync or concurrent fetch. A range URL pointing
    to a `TiledNdArray` is inlined as that tiled array (its tiles are not
    assembled), so a rebuilt range is an `NdArray` or a `TiledNdArray`.

    Parameters
    ----------
    obj
        The coverage or collection to rebuild.
    domains
        Each domain URL mapped to its fetched `Domain`.
    ranges
        Each range URL mapped to its fetched `NdArray` / `TiledNdArray`.

    Returns
    -------
    Coverage or CoverageCollection
        A new value of the same type with its references inlined.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> arr = NdArray(data_type="float", values=(1.0,))
    >>> cov = Coverage(domain="d", ranges={"t": "r"})
    >>> rebuilt = _rebuild(cov, {"d": dom}, {"r": arr})
    >>> rebuilt.domain.domain_type, rebuilt.ranges["t"].values
    ('Point', (1.0,))
    """
    match obj:
        case Coverage():
            return _rebuild_coverage(obj, domains, ranges)
        case CoverageCollection():
            coverages = tuple(
                _rebuild_coverage(cov, domains, ranges) for cov in obj.coverages
            )
            return msgspec.structs.replace(obj, coverages=coverages)


def _rebuild_coverage(
    coverage: Coverage,
    domains: Mapping[str, Domain],
    ranges: Mapping[str, NdArray | TiledNdArray],
) -> Coverage:
    """Rebuild one coverage, inlining its URL-string domain and ranges.

    Looks each URL up in the fetched maps; an inline domain or range is left as is.
    The coverage is returned unchanged (same instance) when it holds no references,
    mirroring `CoverageCollection.resolved_coverages`.

    Parameters
    ----------
    coverage
        The coverage to rebuild.
    domains
        Each domain URL mapped to its fetched `Domain`.
    ranges
        Each range URL mapped to its fetched `NdArray` / `TiledNdArray`.

    Returns
    -------
    Coverage
        A coverage with its URL references inlined, or ``coverage`` itself when
        there were none.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> arr = NdArray(data_type="float", values=(1.0,))
    >>> cov = Coverage(domain=dom, ranges={"t": "r"})  # only the range is a reference
    >>> _rebuild_coverage(cov, {}, {"r": arr}).ranges["t"].values
    (1.0,)
    >>> inert = Coverage(domain=dom, ranges={})
    >>> _rebuild_coverage(inert, {}, {}) is inert  # no references: same instance
    True
    """
    changes: dict[str, object] = {}

    # A one-sided guard (act only when the domain is a URL string), so an `if`
    # reads better here than a match that would need a filler `case _`.
    if isinstance(domain := coverage.domain, str):
        changes["domain"] = domains[domain]

    if any(isinstance(value, str) for value in coverage.ranges.values()):
        changes["ranges"] = {
            key: ranges[value] if isinstance(value, str) else value
            for key, value in coverage.ranges.items()
        }

    return msgspec.structs.replace(coverage, **changes) if changes else coverage
