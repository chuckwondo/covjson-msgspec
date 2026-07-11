# covjson-msgspec

Fast, fully-typed [CoverageJSON](https://covjson.org/) models built on
[msgspec](https://jcristharif.com/msgspec/): a thin core (msgspec + langcodes,
both pure Python) with opt-in bridges to numpy, xarray, pandas, and geopandas.

- **[API reference](reference/coverage.md)**: the model, extracted from source
  via mkdocstrings. Signatures render exactly as written, including the
  optional-bridge return types (`to_xarray(coverage: Coverage) -> xr.Dataset`).
- **[Design decisions](adr/README.md)**: the architecture decision records
  that capture the cross-cutting choices behind the library.

This site is scaffolding for [issue #21](https://github.com/chuckwondo/covjson-msgspec/issues/21)
(the full documentation); the toolchain choice is recorded in
[ADR-0014](adr/0014-documentation-toolchain.md).
