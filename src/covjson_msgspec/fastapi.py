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

To also document such an endpoint in OpenAPI (so it shows a response schema in
Swagger / Redoc), register the CoverageJSON component schemas on the app with
`add_openapi_schemas` and point the route's response at one with
`~covjson_msgspec.schema.schema_ref`: FastAPI cannot introspect msgspec types, so
the schema is injected rather than inferred. See `add_openapi_schemas` for the
full recipe.

Spec: [Media Type and File Extension][spec-media-type]. The ``profile``
parameter follows [RFC 6906][rfc6906]. `add_openapi_schemas` customizes the app's
generated schema via FastAPI's [Extending OpenAPI][extending-openapi] override.

[spec-media-type]: https://github.com/covjson/specification/blob/master/spec.md#10-media-type-and-file-extension
[rfc6906]: https://www.rfc-editor.org/rfc/rfc6906
[extending-openapi]: https://fastapi.tiangolo.com/how-to/extending-openapi/
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from covjson_msgspec.coverage import CoverageJSON, encode
from covjson_msgspec.media_type import MEDIA_TYPE, media_type
from covjson_msgspec.schema import component_schemas

_INSTALL_HINT = (
    "fastapi is required for this response class; install covjson-msgspec[fastapi]"
)

try:
    from fastapi import FastAPI, Response
    from starlette.background import BackgroundTask
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
        status_code: int = 200,
        *,
        headers: Mapping[str, str] | None = None,
        background: BackgroundTask | None = None,
        profile: str | Sequence[str] = (),
    ) -> None:
        # Spell out the forwarded Starlette Response parameters rather than
        # accepting `**kwargs`: it documents the accepted surface, lets FastAPI
        # introspect `status_code` for its OpenAPI generation (an absent
        # `status_code` parameter makes that raise an UnboundLocalError), and
        # deliberately omits `media_type` -- the class owns that (the class
        # attribute plus `profile`), so a caller cannot override it and end up
        # serving non-CoverageJSON.
        profiles = (profile,) if isinstance(profile, str) else tuple(profile)

        if profiles:
            # Set the instance media type before super().__init__ builds the
            # Content-Type header from it (see Response.init_headers).
            self.media_type = media_type(*profiles)

        super().__init__(content, status_code, headers=headers, background=background)

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


def add_openapi_schemas(app: FastAPI) -> None:
    """Register the CoverageJSON component schemas on a FastAPI app's OpenAPI.

    Merges `~covjson_msgspec.schema.component_schemas` into the app's generated
    OpenAPI ``components.schemas`` so a `CovJSONResponse` endpoint can be fully
    described in Swagger / Redoc. Wraps the app's existing ``openapi`` callable
    rather than rebuilding the document, so any other customization is preserved;
    the wrapper runs on every ``app.openapi()`` call and merges in place, so it is
    order-independent (before or after the schema is first built) and idempotent.

    Point a route's response at a registered component with
    `~covjson_msgspec.schema.schema_ref` and the CoverageJSON media type. Declaring
    ``response_class=Response`` (the plain base) lets ``responses`` be the sole
    source of the documented schema. A FastAPI response class carries the media
    type and byte rendering, not a data schema, so using `CovJSONResponse` here
    leaves its raw-body placeholder, ``{"type": "string"}``, which FastAPI *merges
    with* (rather than replaces) our ``$ref``. Under OpenAPI 3.1 the sibling
    keywords beside a ``$ref`` all apply, so the response would be documented as a
    ``Coverage`` *and* a string: an unsatisfiable schema that a validating client or
    code generator would reject. The base `Response` contributes no placeholder, so
    the ``$ref`` stands alone. The handler still returns a `CovJSONResponse`, so the
    response on the wire is unchanged::

        from fastapi import Response
        from covjson_msgspec import Coverage
        from covjson_msgspec.fastapi import CovJSONResponse, add_openapi_schemas
        from covjson_msgspec.media_type import MEDIA_TYPE
        from covjson_msgspec.schema import schema_ref

        @app.get(
            "/coverage",
            response_class=Response,
            responses={
                200: {"content": {MEDIA_TYPE: {"schema": schema_ref(Coverage)}}},
            },
        )
        def coverage() -> CovJSONResponse:
            return CovJSONResponse(build_coverage())

        add_openapi_schemas(app)

    Parameters
    ----------
    app
        The FastAPI application whose OpenAPI document should carry the CoverageJSON
        component schemas.

    Examples
    --------
    >>> from fastapi import FastAPI
    >>> from covjson_msgspec.fastapi import add_openapi_schemas
    >>> app = FastAPI()
    >>> add_openapi_schemas(app)
    >>> "CoverageJSON.Coverage" in app.openapi()["components"]["schemas"]
    True

    Applying it more than once is harmless:

    >>> add_openapi_schemas(app)
    >>> "CoverageJSON.Parameter" in app.openapi()["components"]["schemas"]
    True
    """
    original = app.openapi
    schemas = component_schemas()

    def openapi() -> dict[str, Any]:
        schema = original()
        schema.setdefault("components", {}).setdefault("schemas", {}).update(schemas)

        return schema

    # Reassigning `app.openapi` is FastAPI's own documented way to customize the
    # generated schema (its "Extending OpenAPI" guide, linked in the module
    # docstring); the type-checker ignores are stub conservatism, not a red flag.
    app.openapi = openapi  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
