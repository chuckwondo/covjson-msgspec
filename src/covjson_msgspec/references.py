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

Failures are handled by a pluggable best-effort ``strategy`` (see
`covjson_msgspec._best_effort`): by default the first failed reference aborts the
whole resolution, but a collecting strategy leaves each failed reference as its
URL string and reports it, returning a `ResolveReport`. References are fetched per
site (not deduplicated), so a caller who shares one URL across many collection
members and wants to fetch it once wraps the fetcher in a cache -- all caching is
the fetcher's to own.

Spec: [ranges object][spec-ranges] (a range may be a URL string) and
[Coverage objects][spec-coverage] (a domain may be a URL string).

[spec-ranges]: https://github.com/covjson/specification/blob/master/spec.md#92-ranges-object
[spec-coverage]: https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Final, Generic, NamedTuple, TypeVar, cast

import msgspec

from covjson_msgspec._best_effort import (
    FailureKind,
    FailureStrategy,
    FetchFailure,
    collect,
    collect_async,
    fail_fast,
)
from covjson_msgspec._fetch import (
    AsyncFetch,
    Fetch,
    fetch_and_decode,
    fetch_and_decode_async,
)
from covjson_msgspec.coverage import Coverage, CoverageCollection
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray, TiledNdArray

# The input and output coverage type, preserved through resolution. Bound (not
# constrained) so it can bind to the ``Coverage | CoverageCollection`` union that
# `_rebuild` produces before the cast narrows it back.
_CovT = TypeVar("_CovT", bound=Coverage | CoverageCollection)

# A range URL points to a standalone range document (an inline NdArray or a
# TiledNdArray); a domain URL points to a standalone Domain. Both decoders are
# built once and reused. The union-argument constructor returns Any, so the
# explicit Final[Decoder[...]] annotation restores the precise type.
_DOMAIN_DECODER: Final[msgspec.json.Decoder[Domain]] = msgspec.json.Decoder(Domain)
_RANGE_DECODER: Final[msgspec.json.Decoder[NdArray | TiledNdArray]] = (
    msgspec.json.Decoder(NdArray | TiledNdArray)
)


class ReferenceFailure(FetchFailure, frozen=True, kw_only=True):
    """A domain or range reference that failed to fetch or decode.

    Extends `FetchFailure` (the URL, [`FailureKind`][covjson_msgspec.FailureKind], and
    message) with where in the coverage the reference sat: ``slot`` is ``"domain"`` for
    a coverage's domain or the range key for a range, and ``coverage_index`` is the
    member's position in a `CoverageCollection` (``0`` for a lone `Coverage`).
    Collected by `resolve_references` when a best-effort strategy tolerates the
    failure; see `ResolveReport`.

    Because ``slot`` is just the range key for a range, a range whose key is
    literally ``"domain"`` reports the same ``slot`` as a coverage's domain; the
    two are still fetched and placed correctly (they never share a decoder or a
    slot internally), only the report cannot tell them apart.

    Attributes
    ----------
    slot
        ``"domain"`` for a coverage's domain, else the range key.
    coverage_index
        The member's position in a collection (``0`` for a lone `Coverage`).

    Examples
    --------
    >>> from covjson_msgspec import FailureKind
    >>> failure = ReferenceFailure(
    ...     url="https://ex/t.covjson",
    ...     slot="t",
    ...     coverage_index=0,
    ...     kind=FailureKind.TRANSIENT,
    ...     message="timed out",
    ... )
    >>> failure.slot, failure.coverage_index
    ('t', 0)
    >>> str(failure)
    'transient fetching https://ex/t.covjson: timed out'
    """

    slot: str
    coverage_index: int


class ResolveReport(msgspec.Struct, Generic[_CovT], frozen=True):
    """A resolution's (partial) value plus any references a strategy tolerated.

    Returned by `resolve_references` and
    [`resolve_references_async`][covjson_msgspec.resolve_references_async]. ``value`` is
    a new `Coverage` or `CoverageCollection` of the same type as the input, with
    every successfully fetched URL reference inlined; a reference that failed
    under a collecting strategy keeps its original URL string (still a legal
    document), and ``failures`` reports it. Under the default
    [`fail_fast`][covjson_msgspec.fail_fast] strategy ``failures`` is empty: the first
    failed reference raises a [`FetchError`][covjson_msgspec.FetchError] instead of
    being collected.

    Like [`AssembleReport`][covjson_msgspec.AssembleReport], this is a plain value
    carrier, not a CoverageJSON wire type.

    Attributes
    ----------
    value
        The resolved coverage or collection, of the same type as the input;
        unresolved references remain URL strings.
    failures
        The references that failed, one `ReferenceFailure` each (empty under
        [`fail_fast`][covjson_msgspec.fail_fast]).
    """

    value: _CovT
    failures: tuple[ReferenceFailure, ...]


def resolve_references(
    obj: _CovT,
    fetch: Fetch,
    *,
    strategy: FailureStrategy[ReferenceFailure] = fail_fast,
) -> ResolveReport[_CovT]:
    """Inline a coverage's (or collection's) URL-string domain and range references.

    Returns a `ResolveReport` whose ``value`` is a new value of the same type as
    ``obj`` with every URL-string ``domain`` and every URL-string entry in
    ``ranges`` replaced by the document fetched from it and decoded; inline
    domains and ranges are left untouched. For a `CoverageCollection`, every
    member is resolved (collection-level inheritance is not applied; call
    `CoverageCollection.resolved_coverages` first if you need that).

    This follows URL strings only. A range URL that points to a `TiledNdArray` is
    inlined as that tiled array, not assembled from its tiles.

    How a failed reference is handled is the ``strategy``. The default
    [`fail_fast`][covjson_msgspec.fail_fast] aborts on the first failure, raising a
    [`FetchError`][covjson_msgspec.FetchError] chained from the underlying exception; a
    collecting strategy ([`collect_all`][covjson_msgspec.collect_all], ...) instead
    leaves each failed reference as its URL string in ``report.value`` and reports it in
    ``report.failures``.

    References are fetched **per site**, not deduplicated: a URL used by several
    collection members is fetched once per member. All caching is the fetcher's
    to own (see `covjson_msgspec._fetch`), so wrap it to fetch each URL once:

        from functools import lru_cache

        @lru_cache(maxsize=None)
        def cached(url: str) -> bytes:
            return fetch(url)

        resolve_references(collection, cached)

    Parameters
    ----------
    obj
        The coverage or collection to resolve.
    fetch
        A `Fetch` mapping a referenced document's URL to its raw bytes. All I/O
        (and any caching, auth, or retries) lives in this callable.
    strategy
        How to respond to a reference that fails to fetch or decode. The default
        [`fail_fast`][covjson_msgspec.fail_fast] aborts on the first failure; a
        collecting strategy ([`collect_all`][covjson_msgspec.collect_all],
        [`halt_on_unrecoverable`][covjson_msgspec.halt_on_unrecoverable],
        [`stop_after`][covjson_msgspec.stop_after], or any
        [`FailureStrategy`][covjson_msgspec.FailureStrategy]) reports failures instead.

    Returns
    -------
    ResolveReport
        ``report.value`` is a new value of the same type as ``obj`` with its URL
        references inlined (unresolved ones, under a collecting strategy, left as
        URL strings). ``report.failures`` lists the references that failed (empty
        unless a collecting strategy tolerated one).

    Raises
    ------
    FetchError
        When the ``strategy`` halts on a failure (the default
        [`fail_fast`][covjson_msgspec.fail_fast] halts on the first), chained from the
        underlying fetch or
        [`ReferencedDocumentError`][covjson_msgspec.ReferencedDocumentError] decode
        exception.

    Examples
    --------
    Supply the referenced documents through a fetcher; a ``dict`` of canned bytes
    keyed by URL is the simplest one. Here both the domain and the range are URL
    references that get inlined:

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
    >>> report = resolve_references(cov, store.__getitem__)
    >>> report.value.domain.domain_type
    'Point'
    >>> report.value.ranges["t"].values
    (280.0,)
    >>> report.failures
    ()

    With a collecting strategy and a reference missing from the store, the value
    keeps the unresolved URL string and the failure is reported:

    >>> from covjson_msgspec import collect_all
    >>> partial_store = {"https://ex/t.json": store["https://ex/t.json"]}
    >>> partial = resolve_references(
    ...     cov, partial_store.__getitem__, strategy=collect_all
    ... )
    >>> partial.value.domain  # unresolved: still the URL string
    'https://ex/domain.json'
    >>> partial.value.ranges["t"].values  # resolved
    (280.0,)
    >>> [(f.slot, f.coverage_index) for f in partial.failures]
    [('domain', 0)]
    """
    sites = _reference_sites(obj)

    def fetch_one(
        site: _RefSite,
    ) -> tuple[_RefSite, Domain | NdArray | TiledNdArray]:
        if site.key is None:
            return site, fetch_and_decode(fetch, site.url, _DOMAIN_DECODER)

        return site, fetch_and_decode(fetch, site.url, _RANGE_DECODER)

    payloads, failures = collect(sites, fetch_one, _reference_failure, strategy)
    resolved = {(site.coverage_index, site.key): doc for site, doc in payloads}

    return ResolveReport(value=_rebuild(obj, resolved), failures=tuple(failures))


async def resolve_references_async(
    obj: _CovT,
    fetch: AsyncFetch,
    *,
    strategy: FailureStrategy[ReferenceFailure] = fail_fast,
) -> ResolveReport[_CovT]:
    """Inline URL-string references, fetching them concurrently.

    The awaitable counterpart of `resolve_references` with identical semantics and
    return type (including the ``strategy`` best-effort options); only the
    fetching differs. The reference sites (a domain and every range of every
    member) are fetched concurrently via `asyncio.gather`, so this scales over a
    large `CoverageCollection` far better than awaiting each in turn. Like the
    sync version, this follows URL strings only: a range URL that points to a
    `TiledNdArray` is inlined as that tiled array, not assembled.

    Parameters
    ----------
    obj
        The coverage or collection to resolve.
    fetch
        An `AsyncFetch` awaitably mapping a referenced document's URL to its raw
        bytes. All I/O (and any caching, auth, or retries) lives in this callable.
    strategy
        How to respond to a reference that fails to fetch or decode; see
        `resolve_references`.

    Returns
    -------
    ResolveReport
        As for `resolve_references`: ``report.value`` with unresolved references
        (under a collecting strategy) left as URL strings, and
        ``report.failures``.

    Raises
    ------
    FetchError
        When the ``strategy`` halts on a failure (the default
        [`fail_fast`][covjson_msgspec.fail_fast] halts on the first), chained from the
        underlying fetch or
        [`ReferencedDocumentError`][covjson_msgspec.ReferencedDocumentError] decode
        exception.

    Notes
    -----
    There is no built-in concurrency cap: the unbounded fan-out is left to the
    injected `AsyncFetch`, which owns all I/O policy. To bound it, wrap the fetcher
    in a semaphore:

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
    >>> report = asyncio.run(resolve_references_async(cov, fetch))
    >>> report.value.domain.domain_type
    'Point'
    >>> report.value.ranges["t"].values
    (280.0,)
    """
    sites = _reference_sites(obj)

    async def fetch_one(
        site: _RefSite,
    ) -> tuple[_RefSite, Domain | NdArray | TiledNdArray]:
        if site.key is None:
            return site, await fetch_and_decode_async(fetch, site.url, _DOMAIN_DECODER)

        return site, await fetch_and_decode_async(fetch, site.url, _RANGE_DECODER)

    payloads, failures = await collect_async(
        sites, fetch_one, _reference_failure, strategy
    )
    resolved = {(site.coverage_index, site.key): doc for site, doc in payloads}

    return ResolveReport(value=_rebuild(obj, resolved), failures=tuple(failures))


class _RefSite(NamedTuple):
    """One place a URL reference occurs: which coverage, which slot, and the URL.

    ``key`` is ``None`` for a coverage's ``domain`` and the range key for a range;
    since a range key is always a string, ``None`` unambiguously marks the domain,
    so the domain and a range keyed ``"domain"`` never collide. ``coverage_index``
    is the member's position (``0`` for a lone `Coverage`).

    Examples
    --------
    >>> _RefSite(coverage_index=0, key=None, url="https://ex/d.json").key is None
    True
    """

    coverage_index: int
    key: str | None
    url: str


# Each ``(coverage_index, key)`` (a ``None`` key marks the domain) mapped to its
# fetched-and-decoded document, ready to inline back into the coverage.
_Resolved = Mapping[tuple[int, str | None], Domain | NdArray | TiledNdArray]


def _coverages(obj: Coverage | CoverageCollection) -> Sequence[Coverage]:
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


def _url_slots(coverage: Coverage) -> Iterator[tuple[str | None, str]]:
    """The URL-reference slots of one coverage: the domain, then each range.

    Yields ``(None, url)`` when the ``domain`` is a URL string, then ``(key, url)``
    for each range whose value is a URL string; inline domains and ranges are
    skipped. ``None`` marks the domain slot (a range key is always a string).

    Parameters
    ----------
    coverage
        The coverage to inspect.

    Yields
    ------
    tuple of (str or None, str)
        The slot key (``None`` for the domain, else the range key) and its URL.

    Examples
    --------
    >>> from covjson_msgspec import Coverage
    >>> cov = Coverage(domain="d", ranges={"t": "r", "u": "r"})
    >>> list(_url_slots(cov))
    [(None, 'd'), ('t', 'r'), ('u', 'r')]
    """
    if isinstance(coverage.domain, str):
        yield None, coverage.domain

    for key, value in coverage.ranges.items():
        if isinstance(value, str):
            yield key, value


def _reference_sites(obj: Coverage | CoverageCollection) -> Sequence[_RefSite]:
    """Every URL-reference site of the value, tagged with its member index.

    Walks the coverage (or each collection member) via `_url_slots`, pairing each
    slot with the member's index so a `ReferenceFailure` can name where it
    occurred. Each site is fetched independently (no URL deduplication), so a
    strategy counts one attempt per site.

    Parameters
    ----------
    obj
        The coverage or collection to inspect.

    Returns
    -------
    sequence of _RefSite
        One entry per URL reference, in member-then-(domain-before-ranges) order.

    Examples
    --------
    >>> from covjson_msgspec import Coverage, CoverageCollection
    >>> collection = CoverageCollection(
    ...     coverages=(Coverage(domain="d", ranges={"t": "r"}),)
    ... )
    >>> [(s.coverage_index, s.key, s.url) for s in _reference_sites(collection)]
    [(0, None, 'd'), (0, 't', 'r')]
    """
    return tuple(
        _RefSite(coverage_index=index, key=key, url=url)
        for index, coverage in enumerate(_coverages(obj))
        for key, url in _url_slots(coverage)
    )


def _rebuild(obj: _CovT, resolved: _Resolved) -> _CovT:
    """Rebuild the value with its resolved references inlined.

    Replaces each URL-string ``domain`` and range with the matching document from
    ``resolved`` (keyed by ``(coverage_index, key)``; ``key`` is ``None`` for the
    domain). A slot absent from ``resolved`` -- one whose fetch failed under a
    collecting strategy -- keeps its URL string. Shared by the sync and async
    drivers. A range URL pointing to a `TiledNdArray` is inlined as that tiled
    array (its tiles are not assembled).

    Parameters
    ----------
    obj
        The coverage or collection to rebuild.
    resolved
        Each ``(coverage_index, key)`` mapped to its fetched document (``key`` is
        ``None`` for a domain).

    Returns
    -------
    Coverage or CoverageCollection
        A new value of the same type with its resolved references inlined.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> arr = NdArray(data_type="float", values=(1.0,))
    >>> cov = Coverage(domain="d", ranges={"t": "r"})
    >>> rebuilt = _rebuild(cov, {(0, None): dom, (0, "t"): arr})
    >>> rebuilt.domain.domain_type, rebuilt.ranges["t"].values
    ('Point', (1.0,))
    """
    # `_rebuild_coverage` and `replace` yield obj's runtime type, but through the
    # `Coverage | CoverageCollection` match the checker only sees the union, so
    # the cast restores `_CovT` (sound: each branch returns obj's own type).
    match obj:
        case Coverage():
            return cast("_CovT", _rebuild_coverage(0, obj, resolved))
        case CoverageCollection():
            coverages = tuple(
                _rebuild_coverage(index, coverage, resolved)
                for index, coverage in enumerate(obj.coverages)
            )
            return cast("_CovT", msgspec.structs.replace(obj, coverages=coverages))


def _rebuild_coverage(index: int, coverage: Coverage, resolved: _Resolved) -> Coverage:
    """Rebuild one coverage, inlining its resolved domain and ranges.

    Looks each URL slot up in ``resolved`` by ``(index, key)`` (a ``None`` key for
    the domain); an inline slot, or one whose fetch failed (absent from
    ``resolved``), is left as is -- so a failed reference stays its URL string.
    The coverage is returned unchanged (same instance) when nothing is inlined,
    mirroring `CoverageCollection.resolved_coverages`.

    Parameters
    ----------
    index
        The coverage's position (``0`` for a lone `Coverage`), matching the keys
        in ``resolved``.
    coverage
        The coverage to rebuild.
    resolved
        Each ``(coverage_index, key)`` mapped to its fetched document.

    Returns
    -------
    Coverage
        A coverage with its resolved references inlined, or ``coverage`` itself
        when none were.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> arr = NdArray(data_type="float", values=(1.0,))
    >>> cov = Coverage(domain=dom, ranges={"t": "r"})  # only the range is a ref
    >>> _rebuild_coverage(0, cov, {(0, "t"): arr}).ranges["t"].values
    (1.0,)
    >>> inert = Coverage(domain=dom, ranges={})
    >>> _rebuild_coverage(0, inert, {}) is inert  # nothing to inline: same object
    True
    """
    changes: dict[str, object] = {}

    if isinstance(coverage.domain, str) and (
        (domain := resolved.get((index, None))) is not None
    ):
        changes["domain"] = domain

    if any(isinstance(value, str) for value in coverage.ranges.values()):
        changes["ranges"] = {
            key: resolved.get((index, key), value) if isinstance(value, str) else value
            for key, value in coverage.ranges.items()
        }

    return msgspec.structs.replace(coverage, **changes) if changes else coverage


def _reference_failure(
    site: _RefSite, exc: Exception, kind: FailureKind
) -> ReferenceFailure:
    """Build a `ReferenceFailure` for a reference that failed to fetch or decode.

    Adapts a `_RefSite`, the raised exception, and its classified
    [`FailureKind`][covjson_msgspec.FailureKind] into the failure value that best-effort
    resolution collects. Passed to the best-effort ``collect`` helpers as the
    per-reference failure builder. ``slot`` is derived from the site's ``key``
    (``"domain"`` for the domain, else the range key).

    Parameters
    ----------
    site
        The reference site (its coverage index, slot key, and URL).
    exc
        The exception the reference's fetch or decode raised.
    kind
        The classified failure kind.

    Returns
    -------
    ReferenceFailure
        The failure value for the reference.

    Examples
    --------
    >>> from covjson_msgspec import FailureKind
    >>> site = _RefSite(coverage_index=1, key=None, url="d")
    >>> failure = _reference_failure(site, ValueError("boom"), FailureKind.TRANSIENT)
    >>> failure.slot, failure.coverage_index, failure.message
    ('domain', 1, 'boom')
    """
    slot = "domain" if site.key is None else site.key

    return ReferenceFailure(
        url=site.url,
        slot=slot,
        coverage_index=site.coverage_index,
        kind=kind,
        message=str(exc),
    )
