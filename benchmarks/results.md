# covjson-msgspec vs covjson-pydantic: benchmark results

> [!NOTE]
> This file is generated; do not edit it by hand. Edit the prose in
> `benchmarks/results.template.md` or the numbers in `benchmarks/run.py`, then
> regenerate. To re-render prose changes without re-measuring:
>
> ```sh
> uv run --group bench python benchmarks/run.py --render-only
> ```
>
> See `benchmarks/README.md` for methodology and the interpretation of these
> numbers.

## Environment

- platform: macOS-12.7.6-x86_64-i386-64bit
- python: 3.11.10 (CPython), x86_64

## Versions

- covjson-msgspec: 0.1.0
- covjson-pydantic: 0.8.0
- msgspec: 0.21.1
- pydantic: 2.13.4
- pydantic-core: 2.46.4
- numpy: 2.4.6

## The document set

Five documents, smallest to largest, that every table below is measured against.
The table names each cell's three cost drivers, plus whether covjson-pydantic
meets spec compliance on it:

- **size**: the encoded byte length.
- **inline values**: how many range values `validate(check_values=True)` must
  scan. A cell with none (a URL-referenced or tiled range keeps its values
  elsewhere) exercises the structural and axis checks but no per-element value
  scan. `grid-large`, with 40,000, is where that scan dominates, but it runs as a
  single native pass, so even 40,000 values stay fast rather than paying a
  per-element Python cost.
- **temporal coords**: how many time strings the datetime and monotonic rungs
  resolve.
- **covjson-pydantic spec compliance**: whether covjson-pydantic performs the
  MUST-level checks this document exercises. A ❌ marks a document it accepts but
  the spec says MUST be rejected (see the warning under the ladder).

The documents themselves:

- **point-series (small)** is a PointSeries coverage: x/y at a single point, a
  one-step time axis, and one inline float range (1 value). The smallest
  realistic single-coverage read.
- **grid (medium)** is a Grid coverage: regular x/y axes, a one-step time axis,
  and a range supplied as a URL reference rather than inline. It exercises the
  domain and axis checks with no value scan at all.
- **tiled-ndarray** is a bare TiledNdArray: shape `[2, 5, 10]` over axes `t/y/x`,
  split by three URL-templated tile sets, with no inline values. Its validation
  cost is the per-tile-set consistency check: for each tile set, covjson-msgspec
  bounds every tile-shape element against its axis and parses the `urlTemplate`
  to confirm it names exactly the subdivided axes (the `tiled-ndarray.*` MUST
  checks). covjson-pydantic performs none of this, so on a document that is
  nothing but three tile sets, that check is the entire validation gap.
- **coverage-collection** is a collection of two temporal coverages, six inline
  float values in total. It exercises per-member validation and temporal
  resolution across members.
- **grid-large (synthetic)** is a Grid coverage with regular 200x200 x/y axes and
  one inline float range of 40,000 values. The large-array case: its cost is the
  per-element value scan.

| cell | size (KB) | inline values | temporal coords | covjson-pydantic spec compliance |
| :--- | ---: | ---: | ---: | :--- |
| point-series (small) | 0.8 | 1 | 1 | ❌ non-compliant: skips monotonic axis order |
| grid (medium) | 1.8 | 0 | 1 | ❌ non-compliant: skips monotonic axis order |
| tiled-ndarray | 0.7 | 0 | 0 | ❌ non-compliant: skips tile-set consistency |
| coverage-collection | 4.2 | 6 | 2 | ❌ non-compliant: skips monotonic axis order |
| grid-large (synthetic) | 302.0 | 40,000 | 0 | ✅ compliant |

## Decode and the validation ladder (median us/op)

covjson-pydantic exposes a single decode operation, `model_validate_json`, that is
**fused**: one call always does three things at once, whether or not you need
them, with no way to ask for less:

1. structural decode;
2. full spec validation;
3. parsing every temporal string into a `datetime`.

covjson-msgspec keeps those three concerns **unfused** and opt-in, as separate
steps you invoke only when you need them:

| step | covjson-pydantic | covjson-msgspec |
| :--- | :--- | :--- |
| structural decode | fused into decode | `decode` (always) |
| spec validation | fused into decode | `validate()` (opt-in) |
| datetime parsing | fused into decode | `to_datetime()` (opt-in) |

So the msgspec columns below are a **ladder**: each rung adds one concern on top of
the one before, and you choose how far up to climb. A proxy that just relays a
document stops at the first rung; a service that must trust and index the data
climbs to the top.

Reading the table:

- `pydantic decode` is the reference column. Every msgspec column carries a
  speedup against it: *italic* with a ⬆️ where covjson-msgspec is faster, plain
  with a ⬇️ where slower, and plain with a 🟰 within a rounded 1.0x. So you can
  read the gain at whatever fidelity you stop.
- The final `+ datetime` rung is the honest like-for-like, since only there do
  both libraries end with validated data whose temporal values are real
  `datetime`s.
- For a cell with no temporal axis the datetime rung does not apply, so its
  speedup is measured at the `+ validate(values)` rung, that cell's full-fidelity
  endpoint.

> [!WARNING]
> A ⚠️ on covjson-pydantic's time marks a rung whose number reflects **less
> work**: covjson-pydantic skips a MUST-level spec check that covjson-msgspec
> performs, so the comparison is not like-for-like. It validates neither monotonic
> axis order nor tile-set consistency, meaning it accepts documents the spec says
> MUST be rejected: a conformance failure, not a speed win. Read those rows
> accordingly: where covjson-msgspec is slower, that skipped validation is the
> reason; where covjson-msgspec is faster, it leads *while* enforcing conformance
> covjson-pydantic drops. The `covjson-pydantic spec compliance` column in The
> document set names the failing check per cell.

| cell | pydantic decode (us) | msgspec decode (us, x) | + validate (us, x) | + validate(values) (us, x) | + datetime (us, x) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| point-series (small) | 50.17 (+-2.22) ⚠️ | 3.64 (+-0.25)<br>*13.8x* ⬆️ | 18.92 (+-1.97)<br>*2.7x* ⬆️ | 28.92 (+-1.80)<br>*1.7x* ⬆️ | 31.70 (+-2.39)<br>*1.6x* ⬆️ |
| grid (medium) | 52.23 (+-1.99) ⚠️ | 6.23 (+-0.53)<br>*8.4x* ⬆️ | 20.67 (+-0.36)<br>*2.5x* ⬆️ | 36.09 (+-1.46)<br>*1.4x* ⬆️ | 37.89 (+-1.13)<br>*1.4x* ⬆️ |
| tiled-ndarray | 9.50 (+-0.13) ⚠️ | 2.33 (+-0.06)<br>*4.1x* ⬆️ | 18.60 (+-0.58)<br>0.5x ⬇️ | 18.69 (+-0.38)<br>0.5x ⬇️ | n/a (no-temporal-axis)<br>0.5x ⬇️ |
| coverage-collection | 140.04 (+-1.96) ⚠️ | 12.26 (+-0.78)<br>*11.4x* ⬆️ | 56.07 (+-1.10)<br>*2.5x* ⬆️ | 111.95 (+-4.28)<br>*1.3x* ⬆️ | 116.30 (+-2.49)<br>*1.2x* ⬆️ |
| grid-large (synthetic) | 6634.15 (+-99.82) | 1507.96 (+-23.16)<br>*4.4x* ⬆️ | 1519.61 (+-104.86)<br>*4.4x* ⬆️ | 1873.85 (+-82.72)<br>*3.5x* ⬆️ | n/a (no-temporal-axis)<br>*3.5x* ⬆️ |

## Encode (median us/op)

| cell | msgspec (us) | pydantic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 1.10 (+-0.08) | 18.39 (+-0.75) | *16.7x* ⬆️ |
| grid (medium) | 2.28 (+-0.07) | 48.38 (+-3.00) | *21.3x* ⬆️ |
| tiled-ndarray | 0.73 (+-0.05) | 4.87 (+-0.08) | *6.7x* ⬆️ |
| coverage-collection | 3.78 (+-0.08) | 52.94 (+-3.30) | *14.0x* ⬆️ |
| grid-large (synthetic) | 2615.03 (+-27.47) | 4886.06 (+-212.20) | *1.9x* ⬆️ |

## Round-trip (median us/op)

The same asymmetry as decode: covjson-pydantic's round-trip validates and parses
datetimes on its decode half. `structural` does neither (the default read + write
cost); `full` adds validation and datetimes to match.

| cell | pydantic (us) | msgspec structural (us, x) | msgspec full (us, x) |
| :--- | ---: | ---: | ---: |
| point-series (small) | 84.80 (+-5.82) ⚠️ | 4.60 (+-0.17)<br>*18.4x* ⬆️ | 34.16 (+-1.43)<br>*2.5x* ⬆️ |
| grid (medium) | 130.01 (+-9.07) ⚠️ | 8.41 (+-0.45)<br>*15.5x* ⬆️ | 42.20 (+-2.09)<br>*3.1x* ⬆️ |
| tiled-ndarray | 15.78 (+-0.65) ⚠️ | 3.02 (+-0.08)<br>*5.2x* ⬆️ | 20.26 (+-0.96)<br>0.8x ⬇️ |
| coverage-collection | 252.48 (+-14.57) ⚠️ | 16.23 (+-0.19)<br>*15.6x* ⬆️ | 122.65 (+-0.86)<br>*2.1x* ⬆️ |
| grid-large (synthetic) | 11742.83 (+-288.09) | 4135.10 (+-17.01)<br>*2.8x* ⬆️ | 4609.14 (+-244.24)<br>*2.5x* ⬆️ |

## Validation parity (decode-time checks)

A conformance scorecard for decode-time checks. The **expected** column is the
proportional response the spec calls for, by requirement level: a `MUST` violation
should error, a `SHOULD` violation should warn (the document stays loadable), and
a conformant input should be accepted.

covjson-msgspec matches on every row. covjson-pydantic **fails three MUST checks**:
it validates neither monotonic axis order, categorical codes, nor tile-set
consistency, so it silently accepts documents the spec requires be rejected. It
also over-enforces one SHOULD (raising on a malformed temporal value the spec lets
you load) and rejects one conformant input (a reduced-precision time).

| check | expected | msgspec full | pydantic decode |
| --- | --- | --- | --- |
| structure and field types | error (MUST) | ✅ error | ✅ error |
| value vs dataType | error (MUST) | ✅ error | ✅ error |
| value count vs shape product | error (MUST) | ✅ error | ✅ error |
| monotonic axis order | error (MUST) | ✅ error (default policy) | ❌ not checked |
| categorical code vs categories | error (MUST) | ✅ error | ❌ not checked |
| tile set consistency (shape, url template) | error (MUST) | ✅ error | ❌ not checked |
| malformed temporal (2010-13-99) | warning (SHOULD) | ✅ warning, preserved | ❌ error (overreach) |
| reduced-precision t (2020-06) | accept (valid) | ✅ accepted | ❌ rejects |

## Matched-work comparison (median us/op)

The validation rungs above are not like-for-like wherever covjson-pydantic skips a
MUST check. These two framings remove the one such check that is both removable
from covjson-msgspec and addable to covjson-pydantic, the monotonic scan, so the
two libraries do equal validation work:

- **Framing A** trims covjson-msgspec to covjson-pydantic's check set.
- **Framing B** adds an equivalent monotonic scan to covjson-pydantic.

These framings equalize monotonic ordering only, so a ⚠️ here still marks a cell
whose *other* skipped MUST (tile-set consistency) they do not neutralize.

The pattern these framings expose: wherever covjson-pydantic looks faster at the
validation rungs, it is faster because it validates less. Equalize the work and
covjson-msgspec's slower cells close up or move ahead, leaving only the tile-set
⚠️, where covjson-msgspec runs a MUST check covjson-pydantic performs not at all.

Even a like-for-like row (no ⚠️) carries a cost worth naming: covjson-msgspec's
monotonic scan resolves temporal values from their strings, per member domain, a
separate pass covjson-pydantic avoids by comparing `datetime`s it already parsed
during its fused decode. That extra pass is a real cost, not dropped validation,
and on the temporal `coverage-collection` it once left covjson-msgspec behind;
covjson-msgspec now finishes ahead there, and the repeated resolution remains a
candidate for folding into a single scan. `benchmarks/README.md` works the
`coverage-collection` result through.

Framing A, trim our extra so covjson-msgspec does no more than covjson-pydantic:

| cell | pydantic decode (us) | msgspec matched-trim (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 50.17 (+-2.22) | 28.61 (+-1.20) | *1.8x* ⬆️ |
| grid (medium) | 52.23 (+-1.99) | 32.88 (+-2.02) | *1.6x* ⬆️ |
| tiled-ndarray | 9.50 (+-0.13) ⚠️ | 19.75 (+-0.57) | 0.5x ⬇️ |
| coverage-collection | 140.04 (+-1.96) | 93.18 (+-2.93) | *1.5x* ⬆️ |
| grid-large (synthetic) | 6634.15 (+-99.82) | 1867.47 (+-68.74) | *3.6x* ⬆️ |

Framing B, add covjson-msgspec's monotonic check to covjson-pydantic:

| cell | msgspec matched-full (us) | pydantic decode+monotonic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 34.88 (+-2.60) | 62.07 (+-2.68) | *1.8x* ⬆️ |
| grid (medium) | 38.20 (+-2.22) | 66.73 (+-0.65) | *1.7x* ⬆️ |
| tiled-ndarray | 18.70 (+-0.88) | 17.00 (+-0.28) ⚠️ | 0.9x ⬇️ |
| coverage-collection | 115.62 (+-0.91) | 150.80 (+-4.52) | *1.3x* ⬆️ |
| grid-large (synthetic) | 2052.23 (+-178.07) | 6768.31 (+-256.56) | *3.3x* ⬆️ |

## Capability probes (decode, median us/op)

Documents that expose a one-sided capability gap. A time (us/op) means the library
decoded it; `raises ...` is the exact exception the library throws instead,
verbatim (type and message), so the gap is a concrete failure rather than a
paraphrase.

| probe | the gap | msgspec | pydantic |
| --- | --- | --- | --- |
| naive datetime | a full-form t value with no timezone | 3.21 (+-0.02) | raises ValidationError: Input should have timezone info |
| date-only t | a reduced-precision date (spec form YYYY-MM-DD) | 3.25 (+-0.04) | raises ValidationError: Input should have timezone info |
| year-month t | a reduced-precision month (spec form YYYY-MM) | 3.38 (+-0.15) | raises ValidationError: Input should be a valid datetime or date, input is too short |
| extra custom axis | a domain axis beyond the fixed x/y/z/t/composite slots | 2.72 (+-0.05) | raises ValidationError: Extra inputs are not permitted |
| mixed-type axis | one axis mixing numeric and string values | 2.40 (+-0.21) | raises ValidationError: Input should be a valid number, unable to parse string as a number |

Reverse direction: no probe was found that covjson-pydantic accepts and covjson-msgspec rejects. covjson-pydantic's advantage is static type precision and discoverability (see issue #22), not document acceptance.

## What each operation does

Every row in the tables above is one of these operations. This is exactly what
each one runs, so a timing is never a black box.

**covjson-msgspec**

- `decode`: structural decode only: builds the typed model and leaves every temporal value as its raw ISO 8601 string
- `encode`: serialize the model back to CoverageJSON bytes
- `roundtrip`: structural decode then encode; no validation or datetime parsing (the realistic default cost of a faithful read + write)
- `roundtrip(full)`: decode + validate(check_values=True) + to_datetime(t) + encode: the closest like-for-like with pydantic round-trip (see the validation parity table for the exact check overlap)
- `decode+validate`: decode + cross-cutting validate() (no value scan)
- `decode+validate(values)`: decode + validate(check_values=True): adds the O(n) value scans (value-vs-dataType, categorical, temporal lexical form, and monotonic axes, the last under the default, replaceable axis-order checker)
- `decode+validate(values)+datetime`: the row above plus to_datetime() over every temporal coordinate: the closest like-for-like with pydantic decode (see the validation parity table for the exact check overlap)
- `matched-full`: decode + validate(check_values=True) + to_datetime(t): full validation, including the monotonic and categorical checks pydantic omits
- `matched-trim`: decode + validate(check_values=True, monotonic disabled) + to_datetime(t): trimmed to pydantic's check set for a like-for-like

**covjson-pydantic**

- `decode`: model_validate_json: structural decode + validation + datetime parsing, fused and mandatory
- `encode`: model_dump_json: serialize the model to JSON bytes
- `roundtrip`: model_validate_json then model_dump_json
- `decode+monotonic`: model_validate_json + a manual monotonic-axis scan, so pydantic does the same validation as msgspec's full pass
