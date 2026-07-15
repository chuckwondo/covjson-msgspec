# Serving over HTTP

The core knows CoverageJSON's media type, `application/prs.coverage+json`, and
pairs it with encode/decode, so you can serve and receive coverages from any web
framework without the core depending on one.

## The framework-agnostic helpers

`encode_response` returns the body and the `Content-Type` to set; `decode_response`
verifies an incoming `Content-Type` and decodes the body:

```python
from covjson_msgspec import MEDIA_TYPE, decode_response, encode_response

# Outbound: body plus the Content-Type to set on the response.
body, content_type = encode_response(coverage)

# Inbound: verify the declared Content-Type, then decode.
coverage = decode_response(request_body, content_type)
```

An [RFC 6906](https://www.rfc-editor.org/rfc/rfc6906) profile can be advertised
through the media type's `profile` parameter:

```python
body, content_type = encode_response(coverage, profile="https://example.com/p")
```

`is_coverage_json_media_type` and `media_type` help you inspect or build the media
type string when you are routing requests yourself. See the
[HTTP, media type & schema reference](../reference/media-type.md) for the full set.

## FastAPI

The `[fastapi]` extra adds `CovJSONResponse`, a response class that sets the
CoverageJSON media type and encodes the body for you. Return one from a handler and
FastAPI sends it unchanged:

```python
from fastapi import FastAPI

from covjson_msgspec.fastapi import CovJSONResponse

app = FastAPI()


@app.get("/coverage", response_class=CovJSONResponse)
def get_coverage() -> object:
    return build_a_coverage()   # sent as application/prs.coverage+json
```

### Documenting the response in OpenAPI

`CovJSONResponse` gets the bytes on the wire right, but FastAPI cannot introspect
msgspec structs, so on its own the endpoint shows no response schema in Swagger UI
or Redoc. `add_openapi_schemas` closes that gap: it registers the CoverageJSON
component schemas on the app (`Coverage`, `Domain`, `Parameter`, and the rest), and
`schema_ref` points a route's documented response at one of them:

```python
from fastapi import FastAPI, Response

from covjson_msgspec import MEDIA_TYPE, Coverage, schema_ref
from covjson_msgspec.fastapi import CovJSONResponse, add_openapi_schemas

app = FastAPI()
add_openapi_schemas(app)


@app.get(
    "/coverage",
    response_class=Response,
    responses={200: {"content": {MEDIA_TYPE: {"schema": schema_ref(Coverage)}}}},
)
def get_coverage() -> Response:
    return CovJSONResponse(build_a_coverage())
```

Two details make this work:

- **`add_openapi_schemas` wraps the app's `openapi()` callable** rather than
  rebuilding the document, so it composes with any other customization, runs on
  every `app.openapi()` call, and is idempotent.
- **The documented route declares `response_class=Response`, the plain base, not
  `CovJSONResponse`.** A response class carries a media type and byte rendering, not
  a data schema, and `CovJSONResponse` leaves a raw-body placeholder
  (`{"type": "string"}`). Under OpenAPI 3.1 the keywords beside a `$ref` all apply,
  so that placeholder would merge with the `schema_ref` into a schema that is a
  `Coverage` *and* a string at once: unsatisfiable, and rejected by a validating
  client or code generator. The plain `Response` contributes no placeholder, so the
  `$ref` stands alone. The handler still returns a `CovJSONResponse`, so the bytes on
  the wire are unchanged.

A Litestar adapter (msgspec-native) is planned, wrapping the same core helpers
above. The design principle throughout is dependency injection at the edges: the
core never imports a web framework, so you wire in your own.
