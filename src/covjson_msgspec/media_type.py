"""The CoverageJSON media type and framework-agnostic HTTP helpers.

CoverageJSON registers its own media (content) type, ``application/prs.coverage
+json``, so that a server emitting a coverage can label it precisely (rather than
the generic ``application/json``) and a CoverageJSON-aware client can recognize it
during content negotiation. `MEDIA_TYPE` is that string, named once so callers
never hand-type it.

The helpers here pair that constant with the existing `encode` / `decode` for the
two HTTP boundaries, while staying free of any web-framework dependency:
`encode_response` produces the body and content type to send, `decode_response`
checks an incoming content type before decoding, and `is_coverage_json_media_type`
is the predicate both inbound checks share. The ``[litestar]`` / ``[fastapi]``
``Response`` adapters (a separate, deferred extra) build on these.

Spec: [Media Type and File Extension][spec-media-type]. The optional ``profile``
parameter follows [RFC 6906][rfc6906], the JSON encoding rules (always UTF-8, no
``charset``) are [RFC 8259][rfc8259], and case-insensitive media-type matching is
[RFC 9110][rfc9110].

[spec-media-type]: https://github.com/covjson/specification/blob/master/spec.md#10-media-type-and-file-extension
[rfc6906]: https://www.rfc-editor.org/rfc/rfc6906
[rfc8259]: https://www.rfc-editor.org/rfc/rfc8259
[rfc9110]: https://www.rfc-editor.org/rfc/rfc9110
"""

from collections.abc import Sequence
from typing import Final

from covjson_msgspec.coverage import CoverageJSON, decode, encode

#: The CoverageJSON media (content) type, per spec section 10. A document served
#: over HTTP SHALL carry this as its ``Content-Type``; an optional ``profile``
#: parameter may follow (RFC 6906), which `is_coverage_json_media_type` tolerates.
#: `media_type` builds the profiled form.
MEDIA_TYPE: Final = "application/prs.coverage+json"


def media_type(*profiles: str) -> str:
    """Build the CoverageJSON content type, optionally carrying profile URIs.

    With no arguments this is just `MEDIA_TYPE`. Given one or more URIs it appends
    the spec's optional ``profile`` parameter (RFC 6906): a quoted, space-separated
    list identifying the conventions a document follows. Handy for an outbound
    ``Content-Type`` (see `encode_response`) or an ``Accept`` header. ``charset`` is
    intentionally not supported: JSON media types define none, and the output is
    always UTF-8.

    Parameters
    ----------
    *profiles
        Zero or more profile URIs to advertise.

    Returns
    -------
    str
        `MEDIA_TYPE`, with a ``profile`` parameter appended when URIs are given.

    Examples
    --------
    >>> media_type()
    'application/prs.coverage+json'
    >>> media_type("https://example.com/profileA")
    'application/prs.coverage+json; profile="https://example.com/profileA"'
    >>> media_type("urn:a", "urn:b")
    'application/prs.coverage+json; profile="urn:a urn:b"'
    """
    if not profiles:
        return MEDIA_TYPE

    return f'{MEDIA_TYPE}; profile="{" ".join(profiles)}"'


def is_coverage_json_media_type(value: str) -> bool:
    """Report whether a content-type string denotes CoverageJSON.

    Compares only the type/subtype (case-insensitively, per RFC 9110), ignoring
    any parameters such as ``; charset=utf-8`` or the spec's optional
    ``; profile=...``. Use it to validate an incoming ``Content-Type`` header.

    Parameters
    ----------
    value
        A content-type string, e.g. the value of an HTTP ``Content-Type`` header.

    Returns
    -------
    bool
        ``True`` if ``value`` denotes the CoverageJSON media type.

    Notes
    -----
    Only the type/subtype is compared; everything from the first ``;`` onward is
    discarded before matching. This is deliberately lenient: although JSON media
    types define no ``charset`` parameter (RFC 8259) and CoverageJSON defines only
    ``profile`` (spec section 10), real-world senders still attach
    ``; charset=utf-8`` and similar. Stripping every parameter lets such
    improperly constructed ``Content-Type`` values still be recognized rather than
    rejected on a technicality.

    Examples
    --------
    >>> is_coverage_json_media_type("application/prs.coverage+json")
    True
    >>> is_coverage_json_media_type("application/prs.coverage+json; charset=utf-8")
    True
    >>> is_coverage_json_media_type("  APPLICATION/PRS.Coverage+JSON  ")
    True
    >>> is_coverage_json_media_type("application/json")
    False
    """
    return value.split(";", 1)[0].strip().lower() == MEDIA_TYPE


def encode_response(
    obj: CoverageJSON, *, profile: str | Sequence[str] = ()
) -> tuple[bytes, str]:
    """Encode a CoverageJSON document into an HTTP response body and content type.

    A thin pairing of `encode` with the content type from `media_type`, so an
    outbound handler sets the correct ``Content-Type`` without restating the
    literal. Framework-agnostic: spread the result into whatever response object
    the framework expects.

    Parameters
    ----------
    obj
        Any CoverageJSON document.
    profile
        One profile URI, or a sequence of them, to advertise via the content
        type's ``profile`` parameter (RFC 6906). Defaults to none.

    Returns
    -------
    tuple of (bytes, str)
        The JSON-encoded body and the content type to send with it.

    Notes
    -----
    ``profile`` is the only parameter accepted because it is the only one the
    CoverageJSON media type defines (spec section 10, via RFC 6906). A ``charset``
    parameter is deliberately not offered: JSON media types define none
    (RFC 8259), and `encode` always emits UTF-8, so there is no encoding for such
    a parameter to select. A caller who must emit some other parameter for a
    nonconforming client can append it to the returned content type by hand.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> body, content_type = encode_response(cov)
    >>> body.startswith(b'{"type":"Coverage"')
    True
    >>> content_type
    'application/prs.coverage+json'

    A profile URI is carried through to the content type:

    >>> _, content_type = encode_response(cov, profile="urn:example")
    >>> content_type
    'application/prs.coverage+json; profile="urn:example"'
    """
    profiles = (profile,) if isinstance(profile, str) else tuple(profile)

    return encode(obj), media_type(*profiles)


def decode_response(data: bytes | str, content_type: str | None = None) -> CoverageJSON:
    """Decode an HTTP request body as CoverageJSON, optionally checking its type.

    When ``content_type`` is given, it must denote CoverageJSON (per
    `is_coverage_json_media_type`) or a `ValueError` is raised before decoding;
    pass ``None`` to skip the check. On success this delegates to `decode`, so the
    return type is dispatched on the document's ``type`` member.

    Parameters
    ----------
    data
        The request body, as ``bytes`` or ``str``.
    content_type
        The declared ``Content-Type``, or ``None`` to decode without checking.

    Returns
    -------
    Coverage or CoverageCollection or Domain or NdArray or TiledNdArray
        The decoded document.

    Raises
    ------
    ValueError
        If ``content_type`` is given and does not denote CoverageJSON.

    Examples
    --------
    >>> body = b'{"type":"Domain","domainType":"Point","axes":{"x":{"values":[1.0]}}}'
    >>> decode_response(body, "application/prs.coverage+json").domain_type
    'Point'
    >>> decode_response(body).domain_type  # no content-type check
    'Point'
    >>> decode_response(body, "application/json")
    Traceback (most recent call last):
        ...
    ValueError: expected 'application/prs.coverage+json' content type, got ...
    """
    if content_type is not None and not is_coverage_json_media_type(content_type):
        msg = f"expected {MEDIA_TYPE!r} content type, got {content_type!r}"
        raise ValueError(msg)

    return decode(data)
