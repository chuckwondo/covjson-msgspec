# covjson-msgspec

Fast, fully-typed [CoverageJSON](https://covjson.org/) models built on
[msgspec](https://jcristharif.com/msgspec/).

[CoverageJSON](https://covjson.org/) is the OGC Community Standard for exchanging
coverage data as JSON: grids, time series, point collections, trajectories,
polygon series, and more. covjson-msgspec turns those documents into precise,
immutable Python types you can decode, validate, transform, and encode, without
giving up speed or spec fidelity.

## Why covjson-msgspec

Handling CoverageJSON in Python has usually meant a choice between untyped `dict`
wrangling and a heavier validation stack. covjson-msgspec aims for a different
point on the curve:

- **Fast, with a small footprint.** The core is built on msgspec rather than
  pydantic, and depends only on msgspec (a small, self-contained C extension that
  ships prebuilt wheels) and langcodes (pure Python), neither of which pulls in a
  heavy dependency stack.
- **Fully typed.** Every spec-defined type (domain types, composite and polygon
  axes, tiled ranges, reference systems, categorical parameters, i18n strings) is
  modeled precisely, and the public API is verified across several type checkers.
- **A thin core with opt-in bridges.** Nothing drags in numpy, xarray, pandas,
  or a web framework unless you ask: each bridge lives behind its own extra.
- **Byte-faithful.** Decoding preserves modeled spec members faithfully (raw
  ISO 8601 temporal strings stay strings, for instance), and lossy conversions are
  confined to the opt-in bridges. Foreign members (the spec's
  [custom members](https://github.com/covjson/specification/blob/master/spec.md#71-custom-members),
  extension keys it permits but does not define) are dropped by design; relaying a
  document's raw bytes forwards them unchanged. The root JSON-LD `@context` is
  preserved; one conformance edge is still in progress: accepting custom
  reference-system types.
- **Effects at the edges.** The core never reaches the network or imports a web
  framework. You inject a fetcher, so the same code serves sync and async
  services alike.

The [design decisions](adr/README.md) record the reasoning behind these choices;
a head-to-head with the established Pydantic library, covjson-pydantic, is on its
way.

## Install

| Install | Adds |
| --- | --- |
| `covjson-msgspec` | core encode / decode / validate, media type and HTTP helpers (msgspec only) |
| `covjson-msgspec[fastapi]` | `CovJSONResponse` and OpenAPI schema helpers |
| `covjson-msgspec[numpy]` | `NdArray` to and from numpy |
| `covjson-msgspec[xarray]` | two-way, CF-aware `Coverage` to and from xarray |
| `covjson-msgspec[pandas]` | point / series / trajectory to pandas |
| `covjson-msgspec[geo]` | polygon / point / trajectory to geopandas and GeoJSON |

## At a glance

Which side of the wire you are on shapes how you use the library. Either way, the
base install (`pip install covjson-msgspec`) covers the whole decode / build /
encode round trip; the scientific bridges and the FastAPI adapter are opt-in extras
layered on top.

### Consuming a coverage

Decode a document someone else produced, then read its data straight off the
typed, immutable model:

```python
from covjson_msgspec import decode_coverage

cov = decode_coverage(document)              # `document` is bytes or str
cov.domain.domain_type                       # the domain type (e.g., 'Grid')
cov.ranges["temperature"].values             # the values, preserved as read
```

With the `[xarray]` extra, hand that same coverage to the scientific stack as a
CF-aware dataset (the `[pandas]` and `[geo]` bridges follow the same shape):

```python
from covjson_msgspec import to_xarray

ds = to_xarray(cov)                          # an xarray.Dataset
```

### Producing a coverage

Build one with the narrow, named builders (rather than wide constructors with
mutually exclusive arguments), then encode it back to CoverageJSON bytes:

```python
from covjson_msgspec import Axis, Coverage, Domain, NdArray, encode

cov = Coverage(
    domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ranges={"temperature": NdArray(data_type="float", values=(280.0,))},
)
encode(cov)                                  # b'{"type":"Coverage",...}'
```

With the `[fastapi]` extra, serve it under the correct CoverageJSON media type by
returning a `CovJSONResponse` (see [serving over HTTP](guides/http.md)):

```python
from covjson_msgspec.fastapi import CovJSONResponse


@app.get("/coverage", response_class=CovJSONResponse)
def coverage() -> Coverage:
    return build_a_coverage()                # sent as application/prs.coverage+json
```

## Where to go next

- **[Getting started](getting-started.md)**: install, the extras, and a first
  decode / encode round trip, worked through in full.
- **[Core concepts](concepts.md)**: the CoverageJSON model in depth.
- **[API reference](reference/coverage.md)**: the typed surface, extracted from
  the numpy-style docstrings.
- **[Design decisions](adr/README.md)**: the architecture decision records
  behind the library.

The per-capability guides cover the bridges to [xarray](guides/xarray.md),
[pandas](guides/pandas.md), and [geo / GeoJSON](guides/geo.md), plus
[validation](guides/validation.md), [subsetting](guides/subsetting.md),
[reference resolution](guides/references.md), and
[serving over HTTP](guides/http.md).
