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
| point-series (small) | 50.50 (+-1.00) ⚠️ | 3.54 (+-0.16)<br>*14.3x* ⬆️ | 17.65 (+-2.04)<br>*2.9x* ⬆️ | 30.77 (+-3.03)<br>*1.6x* ⬆️ | 35.98 (+-0.91)<br>*1.4x* ⬆️ |
| grid (medium) | 54.69 (+-2.15) ⚠️ | 6.16 (+-0.83)<br>*8.9x* ⬆️ | 22.50 (+-0.81)<br>*2.4x* ⬆️ | 43.79 (+-2.84)<br>*1.2x* ⬆️ | 41.64 (+-1.72)<br>*1.3x* ⬆️ |
| tiled-ndarray | 9.77 (+-0.23) ⚠️ | 2.35 (+-0.07)<br>*4.1x* ⬆️ | 19.59 (+-0.46)<br>0.5x ⬇️ | 19.76 (+-0.50)<br>0.5x ⬇️ | n/a (no-temporal-axis)<br>0.5x ⬇️ |
| coverage-collection | 143.22 (+-10.80) ⚠️ | 13.26 (+-0.65)<br>*10.8x* ⬆️ | 64.09 (+-5.66)<br>*2.2x* ⬆️ | 123.37 (+-4.86)<br>*1.2x* ⬆️ | 128.20 (+-4.47)<br>*1.1x* ⬆️ |
| grid-large (synthetic) | 6741.59 (+-85.45) | 1523.21 (+-84.91)<br>*4.4x* ⬆️ | 1579.27 (+-30.73)<br>*4.3x* ⬆️ | 2056.68 (+-55.60)<br>*3.3x* ⬆️ | n/a (no-temporal-axis)<br>*3.3x* ⬆️ |
| point-series-large (synthetic) | 77.40 (+-4.06) ⚠️ | 16.32 (+-1.11)<br>*4.7x* ⬆️ | 26.82 (+-1.22)<br>*2.9x* ⬆️ | 418.28 (+-11.88)<br>0.2x ⬇️ | 723.91 (+-46.41)<br>0.1x ⬇️ |
| vertical-profile (synthetic) | 138.92 (+-3.86) ⚠️ | 11.14 (+-0.22)<br>*12.5x* ⬆️ | 22.88 (+-2.21)<br>*6.1x* ⬆️ | 101.22 (+-8.78)<br>*1.4x* ⬆️ | n/a (no-temporal-axis)<br>*1.4x* ⬆️ |

## Encode (median us/op)

| cell | msgspec (us) | pydantic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 1.13 (+-0.04) | 18.19 (+-4.58) | *16.0x* ⬆️ |
| grid (medium) | 2.35 (+-0.04) | 45.70 (+-1.31) | *19.5x* ⬆️ |
| tiled-ndarray | 0.78 (+-0.02) | 4.44 (+-0.15) | *5.7x* ⬆️ |
| coverage-collection | 4.23 (+-0.27) | 48.32 (+-1.92) | *11.4x* ⬆️ |
| grid-large (synthetic) | 2675.55 (+-141.61) | 5001.31 (+-252.78) | *1.9x* ⬆️ |
| point-series-large (synthetic) | 6.10 (+-0.21) | 132.50 (+-7.43) | *21.7x* ⬆️ |
| vertical-profile (synthetic) | 16.81 (+-0.19) | 36.26 (+-1.61) | *2.2x* ⬆️ |

## Round-trip (median us/op)

The same asymmetry as decode: covjson-pydantic's round-trip validates and parses
datetimes on its decode half. `structural` does neither (the default read + write
cost); `full` adds validation and datetimes to match.

| cell | pydantic (us) | msgspec structural (us, x) | msgspec full (us, x) |
| :--- | ---: | ---: | ---: |
| point-series (small) | 83.67 (+-6.68) ⚠️ | 4.73 (+-0.13)<br>*17.7x* ⬆️ | 38.05 (+-0.81)<br>*2.2x* ⬆️ |
| grid (medium) | 125.89 (+-11.12) ⚠️ | 8.61 (+-0.55)<br>*14.6x* ⬆️ | 46.34 (+-0.96)<br>*2.7x* ⬆️ |
| tiled-ndarray | 15.34 (+-0.40) ⚠️ | 3.19 (+-0.23)<br>*4.8x* ⬆️ | 20.51 (+-0.23)<br>0.7x ⬇️ |
| coverage-collection | 225.45 (+-9.64) ⚠️ | 16.77 (+-0.23)<br>*13.4x* ⬆️ | 136.43 (+-8.89)<br>*1.7x* ⬆️ |
| grid-large (synthetic) | 12005.82 (+-160.65) | 4180.99 (+-46.57)<br>*2.9x* ⬆️ | 4594.25 (+-227.91)<br>*2.6x* ⬆️ |
| point-series-large (synthetic) | 222.45 (+-8.13) ⚠️ | 21.48 (+-1.30)<br>*10.4x* ⬆️ | 720.51 (+-46.95)<br>0.3x ⬇️ |
| vertical-profile (synthetic) | 189.95 (+-22.52) ⚠️ | 27.29 (+-2.27)<br>*7.0x* ⬆️ | 115.60 (+-4.66)<br>*1.6x* ⬆️ |

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
| point-series (small) | 50.50 (+-1.00) | 33.74 (+-1.32) | *1.5x* ⬆️ |
| grid (medium) | 54.69 (+-2.15) | 38.85 (+-1.17) | *1.4x* ⬆️ |
| tiled-ndarray | 9.77 (+-0.23) ⚠️ | 19.50 (+-0.40) | 0.5x ⬇️ |
| coverage-collection | 143.22 (+-10.80) | 112.45 (+-4.70) | *1.3x* ⬆️ |
| grid-large (synthetic) | 6741.59 (+-85.45) | 1918.71 (+-156.73) | *3.5x* ⬆️ |
| point-series-large (synthetic) | 77.40 (+-4.06) | 645.48 (+-39.38) | 0.1x ⬇️ |
| vertical-profile (synthetic) | 138.92 (+-3.86) | 31.06 (+-1.95) | *4.5x* ⬆️ |

Framing B, add covjson-msgspec's monotonic check to covjson-pydantic:

| cell | msgspec matched-full (us) | pydantic decode+monotonic (us) | speedup |
| :--- | ---: | ---: | ---: |
| point-series (small) | 39.74 (+-5.08) | 61.74 (+-4.21) | *1.6x* ⬆️ |
| grid (medium) | 42.55 (+-1.10) | 75.03 (+-3.25) | *1.8x* ⬆️ |
| tiled-ndarray | 19.37 (+-0.53) | 16.91 (+-0.45) ⚠️ | 0.9x ⬇️ |
| coverage-collection | 130.01 (+-3.10) | 153.74 (+-3.93) | *1.2x* ⬆️ |
| grid-large (synthetic) | 1946.90 (+-53.99) | 6835.67 (+-142.96) | *3.5x* ⬆️ |
| point-series-large (synthetic) | 721.45 (+-35.75) | 209.46 (+-7.75) | 0.3x ⬇️ |
| vertical-profile (synthetic) | 121.84 (+-30.34) | 172.78 (+-8.96) | *1.4x* ⬆️ |

## Capability probes (decode, median us/op)

Documents that expose a one-sided capability gap. A time (us/op) means the library
decoded it; `raises ...` is the exact exception the library throws instead,
verbatim (type and message), so the gap is a concrete failure rather than a
paraphrase.

| probe | the gap | msgspec | pydantic |
| --- | --- | --- | --- |
| naive datetime | a full-form t value with no timezone | 3.28 (+-0.10) | raises ValidationError: Input should have timezone info |
| date-only t | a reduced-precision date (spec form YYYY-MM-DD) | 3.30 (+-0.13) | raises ValidationError: Input should have timezone info |
| year-month t | a reduced-precision month (spec form YYYY-MM) | 3.29 (+-0.20) | raises ValidationError: Input should be a valid datetime or date, input is too short |
| extra custom axis | a domain axis beyond the fixed x/y/z/t/composite slots | 2.77 (+-0.10) | raises ValidationError: Extra inputs are not permitted |
| mixed-type axis | one axis mixing numeric and string values | 2.38 (+-0.06) | raises ValidationError: Input should be a valid number, unable to parse string as a number |

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
