# covjson-msgspec

Fast, fully-typed [CoverageJSON](https://covjson.org/) models built on
[msgspec](https://jcristharif.com/msgspec/).

An alternative to [covjson-pydantic](https://github.com/KNMI/covjson-pydantic)
that aims for:

- **Performance & a small footprint** — msgspec instead of pydantic.
- **Full CoverageJSON spec coverage** — every domain type, composite/tuple and
  polygon axes, tiled ranges, i18n, categorical parameters, and referencing.
- **Better ergonomics & type-checker support** — generic `NdArray[T]`, narrow
  named builders instead of wide mutually-exclusive constructors, and a public
  API verified across multiple type checkers.

## Design

The library follows a **thin core + opt-in bridges** architecture: the core
depends only on msgspec, while optional extras add recognized-shape bridges to
the rest of the Python geo ecosystem.

| Install | Adds |
| --- | --- |
| `covjson-msgspec` | core encode/decode/validate (msgspec only) |
| `covjson-msgspec[numpy]` | `NdArray` ↔ numpy |
| `covjson-msgspec[xarray]` | two-way, CF-aware `Coverage` ↔ xarray |
| `covjson-msgspec[pandas]` | point/series/trajectory → pandas |
| `covjson-msgspec[geo]` | polygon/point/trajectory → geopandas / GeoJSON |
| `covjson-msgspec[fastapi]` | response helper + media type |

A guiding principle is **dependency injection at the edges, data-in/data-out at
the core**: the core never reaches the network or imports a heavy framework — it
accepts a seam (a callable, a protocol, a plain return value) and lets the
caller wire in their choice.

> **Status:** early development. APIs are not yet stable.

## License

[MIT](LICENSE)
