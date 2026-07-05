"""FastAPI response adapter: serve CoverageJSON with the correct media type.

`CovJSONResponse` is a thin ``fastapi.Response`` subclass that renders any
CoverageJSON document (`Coverage`, `CoverageCollection`, `Domain`, `NdArray`,
`TiledNdArray`) with the core `encode`, and labels it with the CoverageJSON media
type (``application/prs.coverage+json``, spec section 10) rather than the generic
``application/json``. It builds on the framework-agnostic helpers in
`covjson_msgspec.media_type`; the framework dependency lives only behind the
``[fastapi]`` extra and is imported here, so importing the rest of the package
never requires FastAPI.

Return an instance directly from a handler so FastAPI sends it unchanged::

    from covjson_msgspec.fastapi import CovJSONResponse

    @app.get("/coverage")
    def coverage() -> CovJSONResponse:
        return CovJSONResponse(build_coverage())

An optional ``profile`` advertises one or more RFC 6906 profile URIs via the
content type's ``profile`` parameter (reusing `~covjson_msgspec.media_type.media_type`).

Documenting such an endpoint in OpenAPI (so it shows a response schema in Swagger
/ Redoc) is the companion concern tracked separately; FastAPI cannot introspect
msgspec types for OpenAPI, so a `CovJSONResponse` endpoint serves correctly on
the wire but is undocumented until that bridge lands.

Spec: [Media Type and File Extension][spec-media-type]. The ``profile``
parameter follows [RFC 6906][rfc6906].

[spec-media-type]: https://github.com/covjson/specification/blob/master/spec.md#10-media-type-and-file-extension
[rfc6906]: https://www.rfc-editor.org/rfc/rfc6906
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from covjson_msgspec.coverage import CoverageJSON, encode
from covjson_msgspec.media_type import MEDIA_TYPE, media_type

_INSTALL_HINT = (
    "fastapi is required for this response class; install covjson-msgspec[fastapi]"
)

try:
    from fastapi import Response
except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
    raise ModuleNotFoundError(_INSTALL_HINT) from exc


class CovJSONResponse(Response):
    """A FastAPI response that encodes CoverageJSON with the right media type.

    Overrides just the two seams a `Response` subclass needs: the ``media_type``
    class attribute (which `~fastapi.Response.init_headers` turns into the
    ``Content-Type`` header) and `render` (which turns the returned document into
    the body bytes, via the core `encode`). An optional ``profile`` sets a more
    specific content type built by `~covjson_msgspec.media_type.media_type`.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> from covjson_msgspec.fastapi import CovJSONResponse
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> response = CovJSONResponse(cov)
    >>> response.media_type
    'application/prs.coverage+json'
    >>> response.body.startswith(b'{"type":"Coverage"')
    True
    >>> response.headers["content-type"]
    'application/prs.coverage+json'

    A profile URI rides along in the content type:

    >>> CovJSONResponse(cov, profile="urn:example").headers["content-type"]
    'application/prs.coverage+json; profile="urn:example"'

    ``None`` content renders an empty body (matching `Response`), not ``b"null"``:

    >>> CovJSONResponse(None).body
    b''
    """

    media_type = MEDIA_TYPE

    def __init__(
        self,
        content: CoverageJSON | None = None,
        *args: Any,
        profile: str | Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        profiles = (profile,) if isinstance(profile, str) else tuple(profile)

        if profiles:
            # Set the instance media type before super().__init__ builds the
            # Content-Type header from it (see Response.init_headers).
            self.media_type = media_type(*profiles)

        super().__init__(content, *args, **kwargs)

    def render(self, content: CoverageJSON | None) -> bytes:
        """Encode a CoverageJSON document into the response body bytes.

        Mirrors `Response.render`'s handling of ``None`` (an empty body, e.g.,
        a 204) rather than encoding it to the JSON literal ``null``; any other
        content is delegated to the core `encode`.

        Parameters
        ----------
        content
            The CoverageJSON document to serialize, or ``None`` for an empty body.

        Returns
        -------
        bytes
            The UTF-8 JSON body, or ``b""`` when ``content`` is ``None``.

        Examples
        --------
        >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
        >>> from covjson_msgspec.fastapi import CovJSONResponse
        >>> cov = Coverage(
        ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
        ... )
        >>> CovJSONResponse(cov).render(cov).startswith(b'{"type":"Coverage"')
        True
        >>> CovJSONResponse(cov).render(None)
        b''
        """
        return b"" if content is None else encode(content)
