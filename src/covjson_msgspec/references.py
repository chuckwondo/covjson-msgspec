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

from typing import Final, overload

import msgspec

from covjson_msgspec._fetch import Fetch, fetch_and_decode
from covjson_msgspec.coverage import Coverage, CoverageCollection, Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray, TiledNdArray

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
    match obj:
        case Coverage():
            return _resolve_coverage(obj, fetch)
        case CoverageCollection():
            coverages = tuple(_resolve_coverage(cov, fetch) for cov in obj.coverages)
            return msgspec.structs.replace(obj, coverages=coverages)


def _resolve_coverage(coverage: Coverage, fetch: Fetch) -> Coverage:
    """Inline one coverage's URL-string domain and range references.

    Fetches a URL-string ``domain`` as a `Domain` and each URL-string range as an
    `NdArray` / `TiledNdArray`, leaving inline members as they are. The coverage
    is returned unchanged (same instance) when it holds no references, mirroring
    `CoverageCollection.resolved_coverages`.

    Parameters
    ----------
    coverage
        The coverage to resolve.
    fetch
        A `Fetch` mapping a referenced document's URL to its raw bytes.

    Returns
    -------
    Coverage
        A coverage with its URL references inlined, or ``coverage`` itself when
        there were none.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray, encode
    >>> store = {"u": encode(NdArray(data_type="float", values=(1.0,)))}
    >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> cov = Coverage(domain=dom, ranges={"t": "u"})  # only the range is a reference
    >>> _resolve_coverage(cov, store.__getitem__).ranges["t"].values
    (1.0,)
    """
    changes: dict[str, object] = {}

    # A one-sided guard (act only when the domain is a URL string), so an `if`
    # reads better here than a match that would need a filler `case _`.
    if isinstance(domain := coverage.domain, str):
        changes["domain"] = fetch_and_decode(fetch, domain, _DOMAIN_DECODER)

    if any(isinstance(value, str) for value in coverage.ranges.values()):
        changes["ranges"] = {
            key: _resolve_range(value, fetch) for key, value in coverage.ranges.items()
        }

    return msgspec.structs.replace(coverage, **changes) if changes else coverage


def _resolve_range(value: Range, fetch: Fetch) -> NdArray | TiledNdArray:
    """Inline one range value: fetch a URL string, else return it unchanged.

    The per-range counterpart of the domain branch in `_resolve_coverage`: a
    URL-string range is fetched and decoded to an `NdArray` / `TiledNdArray`,
    while an already-inline range is passed through untouched (the same instance,
    so inline ranges keep their identity across resolution).

    Parameters
    ----------
    value
        A range: an inline `NdArray` / `TiledNdArray`, or a URL string.
    fetch
        A `Fetch` mapping a referenced document's URL to its raw bytes.

    Returns
    -------
    NdArray or TiledNdArray
        The fetched-and-decoded range, or ``value`` itself when already inline.

    Examples
    --------
    >>> from covjson_msgspec import NdArray, encode
    >>> store = {"u": encode(NdArray(data_type="float", values=(1.0,)))}
    >>> _resolve_range("u", store.__getitem__).values
    (1.0,)
    >>> inline = NdArray(data_type="float", values=(2.0,))
    >>> _resolve_range(inline, store.__getitem__) is inline
    True
    """
    match value:
        case str() as url:
            return fetch_and_decode(fetch, url, _RANGE_DECODER)
        case _:
            return value
