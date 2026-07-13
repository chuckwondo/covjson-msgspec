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

{{environment}}

## Versions

{{versions}}

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

{{document_set_table}}

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

{{decode_ladder_table}}

## Encode (median us/op)

{{encode_table}}

## Round-trip (median us/op)

The same asymmetry as decode: covjson-pydantic's round-trip validates and parses
datetimes on its decode half. `structural` does neither (the default read + write
cost); `full` adds validation and datetimes to match.

{{roundtrip_table}}

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

{{validation_parity_table}}

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

{{matched_a_table}}

Framing B, add covjson-msgspec's monotonic check to covjson-pydantic:

{{matched_b_table}}

## Capability probes (decode, median us/op)

Documents that expose a one-sided capability gap. A time (us/op) means the library
decoded it; `raises ...` is the exact exception the library throws instead,
verbatim (type and message), so the gap is a concrete failure rather than a
paraphrase.

{{capability_probes_table}}

## What each operation does

Every row in the tables above is one of these operations. This is exactly what
each one runs, so a timing is never a black box.

{{operations_glossary}}
