# Getting started

## Install

covjson-msgspec targets Python 3.11 and newer. The core depends only on msgspec
(a compiled C extension that ships prebuilt wheels) and langcodes (pure Python),
so it installs without a build toolchain.

```sh
pip install covjson-msgspec          # or: uv add covjson-msgspec
```

The bridges to the scientific Python stack are opt-in extras, which keeps the
core lightweight:

```sh
pip install "covjson-msgspec[numpy]"    # NdArray to and from numpy
pip install "covjson-msgspec[xarray]"   # CF-aware Coverage to and from xarray
pip install "covjson-msgspec[pandas]"   # point / series / trajectory to pandas
pip install "covjson-msgspec[geo]"      # polygon / point / trajectory to geopandas, GeoJSON
```

Serving CoverageJSON over HTTP has its own extra, separate from the scientific
stack, for the FastAPI response class and OpenAPI schema helpers:

```sh
pip install "covjson-msgspec[fastapi]"  # CovJSONResponse + OpenAPI schema helpers
```

Each bridge imports its dependency lazily and raises a clear install hint if the
extra is missing, so importing the core never drags in numpy or xarray.

## Build a coverage

The model provides narrow, named builders (`Axis.listed`, `Domain.point`, and so
on) rather than wide constructors with mutually exclusive arguments:

```python
from covjson_msgspec import Axis, Domain, NdArray, Coverage

cov = Coverage(
    domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ranges={"temperature": NdArray(data_type="float", values=(280.0,))},
)
cov.ranges["temperature"].values    # (280.0,)
```

## Encode to CoverageJSON

`encode` returns the CoverageJSON bytes, mapping the snake_case attributes back
to the wire's lowerCamelCase names:

```python
from covjson_msgspec import encode

encode(cov)                     # b'{"type":"Coverage",...}'
```

## Decode from CoverageJSON

`decode_coverage` parses a coverage document (bytes or `str`) into a `Coverage`:

```python
from covjson_msgspec import decode_coverage

blob = '''
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "Point",
    "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
  },
  "ranges": {
    "temperature": {"type": "NdArray", "dataType": "float", "values": [280.0]}
  }
}
'''

cov = decode_coverage(blob)
cov.domain.domain_type              # 'Point'
cov.ranges["temperature"].values    # (280.0,)
```

Decode is byte-faithful: modeled spec members are preserved as read (for example,
temporal values stay raw ISO 8601 strings), and lossy conversions happen only in
the opt-in bridges.
[Custom members](https://github.com/covjson/specification/blob/master/spec.md#71-custom-members)
(extension keys the spec permits but does not define) are dropped by design; to
relay a document with its extensions intact, forward its raw bytes rather than
decoding and re-encoding. (The root JSON-LD `@context` is preserved; one spec edge is still in
progress: accepting custom reference-system types.) The
codec entry points are `decode` (for an untyped root), `decode_coverage`, and
`decode_coverage_collection`; the [API reference](reference/coverage.md) lists the
full set.

## Next steps

- Read [Core concepts](concepts.md) for the CoverageJSON model in depth.
- Convert a coverage to [xarray](guides/xarray.md), [pandas](guides/pandas.md), or
  [GeoJSON](guides/geo.md), [validate](guides/validation.md) it,
  [subset](guides/subsetting.md) it, or [serve it over HTTP](guides/http.md): see
  the capability guides.
- Browse the [API reference](reference/coverage.md) for the complete typed
  surface.
