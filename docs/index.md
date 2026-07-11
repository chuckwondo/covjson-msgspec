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
  pydantic, and depends only on msgspec and langcodes, both pure Python.
- **Fully typed and spec-complete.** Every domain type, composite and polygon
  axis, tiled range, referencing system, categorical parameter, and i18n string
  is modeled, and the public API is verified across several type checkers.
- **A thin core with opt-in bridges.** Nothing drags in numpy, xarray, pandas,
  or a web framework unless you ask: each bridge lives behind its own extra.
- **Byte-faithful.** Decoding preserves every spec-defined member exactly (raw
  ISO 8601 temporal strings stay strings, for instance); lossy conversions are
  confined to the opt-in bridges.
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

Decode a document, reach into its data, and hand it to the scientific stack:

```python
from covjson_msgspec import decode_coverage, to_xarray

cov = decode_coverage(document)         # `document` is bytes or str
cov.ranges["temperature"].values        # the range values, preserved as read
ds = to_xarray(cov)                     # a CF-aware xarray.Dataset (needs [xarray])
```

Or build one with the narrow, named builders and encode it back to CoverageJSON:

```python
from covjson_msgspec import Axis, Domain, NdArray, Coverage, encode

cov = Coverage(
    domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ranges={"t": NdArray(data_type="float", values=(280.0,))},
)
encode(cov)                             # b'{"type":"Coverage",...}'
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
