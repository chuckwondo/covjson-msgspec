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
| point-series-large (synthetic) | 4.8 | 0 | 200 | ❌ non-compliant: skips monotonic axis order |
| vertical-profile (synthetic) | 1.3 | 0 | 0 | ❌ non-compliant: skips monotonic axis order |

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
| point-series (small) | 54.33 (+-6.09) ⚠️ | 3.71 (+-0.27)<br>*14.7x* ⬆️ | 20.60 (+-7.52)<br>*2.6x* ⬆️ | 32.36 (+-3.82)<br>*1.7x* ⬆️ | 34.64 (+-5.21)<br>*1.6x* ⬆️ |
| grid (medium) | 71.93 (+-40.77) ⚠️ | 6.62 (+-0.81)<br>*10.9x* ⬆️ | 23.38 (+-0.61)<br>*3.1x* ⬆️ | 40.47 (+-22.34)<br>*1.8x* ⬆️ | 52.55 (+-8.70)<br>*1.4x* ⬆️ |
| tiled-ndarray | 11.18 (+-0.60) ⚠️ | 2.51 (+-0.54)<br>*4.5x* ⬆️ | 20.26 (+-1.51)<br>0.6x ⬇️ | 23.17 (+-3.00)<br>0.5x ⬇️ | n/a (no-temporal-axis)<br>0.5x ⬇️ |
| coverage-collection | 147.35 (+-23.63) ⚠️ | 14.16 (+-1.97)<br>*10.4x* ⬆️ | 68.35 (+-4.72)<br>*2.2x* ⬆️ | 132.02 (+-21.93)<br>*1.1x* ⬆️ | 128.35 (+-20.51)<br>*1.1x* ⬆️ |
| grid-large (synthetic) | 7116.58 (+-498.20) | 1485.05 (+-140.14)<br>*4.8x* ⬆️ | 1731.45 (+-106.86)<br>*4.1x* ⬆️ | 1994.29 (+-150.71)<br>*3.6x* ⬆️ | n/a (no-temporal-axis)<br>*3.6x* ⬆️ |
| point-series-large (synthetic) | 79.08 (+-4.23) ⚠️ | 15.48 (+-1.02)<br>*5.1x* ⬆️ | 28.94 (+-2.50)<br>*2.7x* ⬆️ | 324.35 (+-36.20)<br>0.2x ⬇️ | 512.53 (+-49.69)<br>0.2x ⬇️ |
| vertical-profile (synthetic) | 156.15 (+-15.62) ⚠️ | 11.45 (+-0.60)<br>*13.6x* ⬆️ | 24.38 (+-2.86)<br>*6.4x* ⬆️ | 105.42 (+-8.37)<br>*1.5x* ⬆️ | n/a (no-temporal-axis)<br>*1.5x* ⬆️ |

## Encode (median us/op)

| cell | msgspec (us) | pydantic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 1.15 (+-0.04) | 17.12 (+-0.66) | *14.9x* ⬆️ |
| grid (medium) | 2.49 (+-0.15) | 55.90 (+-11.48) | *22.5x* ⬆️ |
| tiled-ndarray | 0.91 (+-0.20) | 4.95 (+-0.56) | *5.5x* ⬆️ |
| coverage-collection | 4.03 (+-0.29) | 46.90 (+-1.12) | *11.6x* ⬆️ |
| grid-large (synthetic) | 2735.70 (+-218.48) | 5018.32 (+-218.70) | *1.8x* ⬆️ |
| point-series-large (synthetic) | 6.13 (+-0.48) | 132.40 (+-11.34) | *21.6x* ⬆️ |
| vertical-profile (synthetic) | 16.36 (+-2.43) | 36.71 (+-3.26) | *2.2x* ⬆️ |

## Round-trip (median us/op)

The same asymmetry as decode: covjson-pydantic's round-trip validates and parses
datetimes on its decode half. `structural` does neither (the default read + write
cost); `full` adds validation and datetimes to match.

| cell | pydantic (us) | msgspec structural (us, x) | msgspec full (us, x) |
| :--- | ---: | ---: | ---: |
| point-series (small) | 83.53 (+-5.07) ⚠️ | 5.09 (+-0.79)<br>*16.4x* ⬆️ | 36.14 (+-1.89)<br>*2.3x* ⬆️ |
| grid (medium) | 139.91 (+-11.22) ⚠️ | 8.58 (+-0.36)<br>*16.3x* ⬆️ | 42.38 (+-1.81)<br>*3.3x* ⬆️ |
| tiled-ndarray | 16.55 (+-1.26) ⚠️ | 3.86 (+-0.47)<br>*4.3x* ⬆️ | 31.46 (+-15.08)<br>0.5x ⬇️ |
| coverage-collection | 243.76 (+-55.09) ⚠️ | 17.97 (+-2.14)<br>*13.6x* ⬆️ | 149.59 (+-8.95)<br>*1.6x* ⬆️ |
| grid-large (synthetic) | 11981.82 (+-1128.76) | 4492.25 (+-558.97)<br>*2.7x* ⬆️ | 5081.51 (+-594.30)<br>*2.4x* ⬆️ |
| point-series-large (synthetic) | 237.00 (+-20.18) ⚠️ | 22.34 (+-21.85)<br>*10.6x* ⬆️ | 585.10 (+-128.13)<br>0.4x ⬇️ |
| vertical-profile (synthetic) | 214.42 (+-27.55) ⚠️ | 26.81 (+-1.03)<br>*8.0x* ⬆️ | 117.65 (+-7.65)<br>*1.8x* ⬆️ |

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
| point-series (small) | 54.33 (+-6.09) | 32.12 (+-2.03) | *1.7x* ⬆️ |
| grid (medium) | 71.93 (+-40.77) | 36.79 (+-2.63) | *2.0x* ⬆️ |
| tiled-ndarray | 11.18 (+-0.60) ⚠️ | 20.69 (+-2.65) | 0.5x ⬇️ |
| coverage-collection | 147.35 (+-23.63) | 124.50 (+-4.16) | *1.2x* ⬆️ |
| grid-large (synthetic) | 7116.58 (+-498.20) | 1884.29 (+-74.66) | *3.8x* ⬆️ |
| point-series-large (synthetic) | 79.08 (+-4.23) | 475.76 (+-41.49) | 0.2x ⬇️ |
| vertical-profile (synthetic) | 156.15 (+-15.62) | 30.81 (+-2.88) | *5.1x* ⬆️ |

Framing B, add covjson-msgspec's monotonic check to covjson-pydantic:

| cell | msgspec matched-full (us) | pydantic decode+monotonic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 34.85 (+-6.01) | 60.79 (+-2.13) | *1.7x* ⬆️ |
| grid (medium) | 40.57 (+-1.78) | 85.79 (+-19.70) | *2.1x* ⬆️ |
| tiled-ndarray | 22.14 (+-3.02) | 18.07 (+-2.09) ⚠️ | 0.8x ⬇️ |
| coverage-collection | 141.05 (+-10.48) | 168.51 (+-31.35) | *1.2x* ⬆️ |
| grid-large (synthetic) | 2075.04 (+-394.19) | 7080.58 (+-204.80) | *3.4x* ⬆️ |
| point-series-large (synthetic) | 523.93 (+-34.30) | 212.09 (+-8.99) | 0.4x ⬇️ |
| vertical-profile (synthetic) | 98.07 (+-9.06) | 188.39 (+-28.80) | *1.9x* ⬆️ |

## Capability probes (decode, median us/op)

Documents that expose a one-sided capability gap. A time (us/op) means the library
decoded it; `raises ...` is the exact exception the library throws instead,
verbatim (type and message), so the gap is a concrete failure rather than a
paraphrase.

| probe | the gap | msgspec | pydantic |
| --- | --- | --- | --- |
| naive datetime | a full-form t value with no timezone | 3.79 (+-0.46) | raises ValidationError: Input should have timezone info |
| date-only t | a reduced-precision date (spec form YYYY-MM-DD) | 3.46 (+-0.13) | raises ValidationError: Input should have timezone info |
| year-month t | a reduced-precision month (spec form YYYY-MM) | 3.34 (+-0.22) | raises ValidationError: Input should be a valid datetime or date, input is too short |
| extra custom axis | a domain axis beyond the fixed x/y/z/t/composite slots | 2.83 (+-0.09) | raises ValidationError: Extra inputs are not permitted |
| mixed-type axis | one axis mixing numeric and string values | 2.38 (+-0.18) | raises ValidationError: Input should be a valid number, unable to parse string as a number |

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
