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

The `[fastapi]` extra adds `CovJSONResponse`, a response class that sets the media
type for you, and `add_openapi_schemas`, which documents CoverageJSON endpoints in
the OpenAPI schema (so they render in Swagger UI):

```python
from fastapi import FastAPI

from covjson_msgspec import decode_coverage
from covjson_msgspec.fastapi import CovJSONResponse, add_openapi_schemas

app = FastAPI()
add_openapi_schemas(app)


@app.get("/coverage", response_class=CovJSONResponse)
def get_coverage() -> object:
    return build_a_coverage()
```

A Litestar adapter (msgspec-native) is planned, wrapping the same core helpers
above. The design principle throughout is dependency injection at the edges: the
core never imports a web framework, so you wire in your own.
