# Conformance corpus

Real-world CoverageJSON documents, vendored (downloaded and committed) so the
test suite can exercise `decode` / `encode` / `validate` against documents this
project did not author. Each source is pinned to an upstream commit SHA and keeps
its original license alongside the files.

The harness (`tests/test_corpus.py`) runs two passes per document:

1. **Round-trip** — `decode -> encode -> decode` must be stable (the re-decoded
   object equals the first decode). This exercises the thin model path: the
   tagged-union dispatch, the camelCase wire rename, and tuple coercion.
2. **Validation** — `validate(check_values=True)` must report no error-severity
   issues for a positive (valid) document.

## Sources

| Directory | Upstream | Pinned SHA | License | Vendored |
|-----------|----------|-----------|---------|----------|
| `playground/` | [covjson/playground](https://github.com/covjson/playground) `public/coverages/` | `5fe9190274372e34b3b55a5b253e3ccdc2b773f8` | BSD-style (The University of Reading); see `playground/LICENSE` | 2026-06-22 |
| `covjson-pydantic/` | [KNMI/covjson-pydantic](https://github.com/KNMI/covjson-pydantic) `tests/test_data/` | `b928a3c206fdffa3c1415a5e7a1bbf2718204209` | Apache-2.0; see `covjson-pydantic/LICENSE` | 2026-06-22 |

`playground/` holds 28 end-to-end documents covering every root-union type:
`Coverage` (grid, categorical grid, tiled grid, polygon, polygon series,
trajectory, point, point series, vertical profile), `CoverageCollection` (point
and profile collections), `Domain` (grid, BNG/projected grid), and the bare
`NdArray` tiles under `grid-tiled/` (the external tiles a `TiledNdArray`
references). All are positive documents.

`covjson-pydantic/` holds 50 fixtures including the OGC spec examples (the
`spec-*.json` files) plus parity and negative cases. These were authored for
pydantic models, whose validation differs from ours, so their pass/fail labels do
not transfer: `manifest.toml` classifies each negative under this library's
semantics. Files absent from the manifest are positive; the listed ones are
either `structural_reject` (our `decode()` raises, because the fixture is a bare
sub-model fragment rather than a root document, or is a malformed root document)
or `validate_reject` (decodes, but `validate()` reports the recorded error codes).

### Updating a source

Re-download from the same upstream path at a new SHA, update the table above
(SHA + date) and the vendored `LICENSE`, then re-run the suite. Do not edit the
vendored documents by hand: they are a faithful copy of the upstream fixtures.
