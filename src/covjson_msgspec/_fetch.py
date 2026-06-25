"""The fetcher seam shared by reference resolution and tile assembly.

CoverageJSON lets a document defer its bulk data to other documents: a coverage's
``domain`` or a parameter's range may be a URL string instead of inline values
(resolved by `covjson_msgspec.references.resolve_references`), and a
`TiledNdArray` splits its values across tiles fetched by URL. Turning either form
into inline data means fetching those URLs, which is I/O this library
deliberately does not perform itself (the "dependency injection at the edges"
design tenet: the core never reaches the network).

Instead, the caller supplies a `Fetch`: a plain callable mapping a URL to the raw
bytes of the document at that URL. The caller owns every policy this implies (the
HTTP client, authentication, caching, retries, timeouts, or reading from a local
mirror), so the library stays ignorant of the network. A fetcher backed by a
``dict`` of canned documents makes both features trivially testable offline.

Spec: [NdArray / range URL references][spec-ranges] and
[TiledNdArray objects][spec-tiled].

[spec-ranges]: https://github.com/covjson/specification/blob/master/spec.md#92-ranges-object
[spec-tiled]: https://github.com/covjson/specification/blob/master/spec.md#63-tiledndarray-objects
"""

from collections.abc import Callable
from typing import TypeVar

import msgspec

# A user-supplied callable mapping a referenced document's URL to its raw bytes.
# Synchronous by design (an async variant is a possible future addition); the
# caller is free to satisfy the request from the network, a cache, or a local
# store, so the core stays I/O-free and offline-testable.
Fetch = Callable[[str], bytes]

_T = TypeVar("_T")


def fetch_and_decode(fetch: Fetch, url: str, decoder: "msgspec.json.Decoder[_T]") -> _T:
    """Fetch the document at ``url`` and decode it with ``decoder``.

    The single choke point through which both `resolve_references` and tile
    assembly pull a referenced document, so the fetch-then-decode contract and
    its error reporting stay identical across them. Exceptions raised by
    ``fetch`` itself (network, auth, missing key) propagate unchanged, since they
    are the caller's domain; only a decode failure is rewrapped to name the
    offending URL.

    Parameters
    ----------
    fetch
        The caller's `Fetch`, mapping ``url`` to the document's raw bytes.
    url
        The URL of the referenced document.
    decoder
        A reusable `msgspec.json.Decoder` for the expected document type.

    Returns
    -------
    object
        The decoded document, of the decoder's type.

    Raises
    ------
    ValueError
        If the fetched bytes do not decode to the expected type.

    Examples
    --------
    >>> import msgspec
    >>> from covjson_msgspec.domain import Domain
    >>> decoder = msgspec.json.Decoder(Domain)
    >>> store = {
    ...     "u": b'{"type":"Domain","domainType":"Point","axes":{"x":{"values":[1.0]}}}'
    ... }
    >>> fetch_and_decode(store.__getitem__, "u", decoder).domain_type
    'Point'

    A document that does not match the expected type is reported against its URL:

    >>> fetch_and_decode({"u": b"not json"}.__getitem__, "u", decoder)
    Traceback (most recent call last):
        ...
    ValueError: document fetched from 'u' is not valid CoverageJSON: ...
    """
    raw = fetch(url)

    try:
        return decoder.decode(raw)
    except msgspec.DecodeError as exc:
        msg = f"document fetched from {url!r} is not valid CoverageJSON: {exc}"
        raise ValueError(msg) from exc
