# covjson-msgspec

Fast, fully-typed [CoverageJSON](https://covjson.org/) models built on
[msgspec](https://jcristharif.com/msgspec/).

An alternative to [covjson-pydantic](https://github.com/KNMI/covjson-pydantic)
that aims for:

- **Performance & a small footprint**: msgspec instead of pydantic.
- **Full CoverageJSON spec coverage**: every domain type, composite/tuple and
  polygon axes, tiled ranges, i18n, categorical parameters, and referencing.
- **Better ergonomics & type-checker support**: narrow named builders instead
  of wide mutually-exclusive constructors, and a public API verified across
  multiple type checkers.

## Design

The library follows a **thin core + opt-in bridges** architecture: the core
depends only on msgspec, while optional extras add recognized-shape bridges to
the rest of the Python geo ecosystem.

| Install | Adds |
| --- | --- |
| `covjson-msgspec` | core encode/decode/validate + media type & HTTP helpers (msgspec only) |
| `covjson-msgspec[numpy]` | `NdArray` ↔ numpy |
| `covjson-msgspec[xarray]` | two-way, CF-aware `Coverage` ↔ xarray |
| `covjson-msgspec[pandas]` | point/series/trajectory → pandas |
| `covjson-msgspec[geo]` | polygon/point/trajectory → geopandas / GeoJSON |

_Framework adapters (behind their own extras, since they need a web-framework
dependency):_ `covjson-msgspec[fastapi]` ships `CovJSONResponse` (serve
CoverageJSON with the right media type) and `add_openapi_schemas` (document those
endpoints in OpenAPI / Swagger). A Litestar adapter (msgspec-native, so
first-class) is planned, wrapping the same core helpers below.

## Serving over HTTP

The core knows CoverageJSON's media type, `application/prs.coverage+json`, and
pairs it with the existing encode/decode (no web framework required):

```python
from covjson_msgspec import MEDIA_TYPE, decode_response, encode_response

# Outbound: body + the Content-Type to set on the response.
body, content_type = encode_response(coverage)

# Advertise an RFC 6906 profile via the media type's `profile` parameter:
body, content_type = encode_response(coverage, profile="https://example.com/p")

# Inbound: verify the declared Content-Type, then decode.
coverage = decode_response(request_body, content_type)
```

A guiding principle is **dependency injection at the edges, data-in/data-out at
the core**: the core never reaches the network or imports a heavy framework, but
accepts a seam (a callable, a protocol, a plain return value) and lets the
caller wire in their choice.

## Resolving references & assembling tiles

A CoverageJSON document may defer parts of itself to other URLs: a coverage's
`domain` or a range may be a URL string, and a `TiledNdArray` splits its values
across tile documents. Inlining those means fetching URLs, which is I/O the core
does not perform itself. Instead you inject a fetcher (a plain callable mapping a
URL to bytes), so caching, auth, retries, and throttling stay yours:

```python
# Sync: you supply Fetch = Callable[[str], bytes]
resolved = coverage.resolve_references(fetch).value  # inline URL-string domain/ranges
array = tiled.assemble(fetch).array                  # stitch a TiledNdArray's tiles

# Async: AsyncFetch = Callable[[str], Awaitable[bytes]], so independent fetches
# run concurrently via asyncio.gather (ideal under Starlette/FastAPI/litestar)
resolved = (await coverage.resolve_references_async(afetch)).value
array = (await tiled.assemble_async(afetch)).array
```

The async fan-out is unbounded by design: since the fetcher owns all I/O policy,
bound concurrency there (see `resolve_references_async` for an `asyncio.Semaphore`
example).

> **Status:** early development. APIs are not yet stable.

## License

[MIT](LICENSE)
