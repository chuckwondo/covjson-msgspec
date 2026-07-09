# Benchmarks: covjson-msgspec vs covjson-pydantic

Throughput numbers for the core codec operations, comparing this library against
[covjson-pydantic](https://github.com/KNMI/covjson-pydantic) (the established
Pydantic-based CoverageJSON library). This is issue #18; the results feed the
covjson-pydantic comparison documentation (issue #22).

`results.md` is the human-readable table; `results.json` is the machine-readable
record. Both are generated, committed artifacts. **Do not edit them by hand.**

## Running

covjson-pydantic is a benchmark-only dependency (never a runtime dependency of
the library), so it lives in the `bench` dependency group:

```sh
uv sync --group bench
uv run --group bench python benchmarks/run.py            # full run
uv run --group bench python benchmarks/run.py --quick    # fast smoke run
```

Both artifacts are rendered from one in-memory result set, so they cannot
disagree; regenerate them together with the command above.

## What is measured

The core operations on a representative document set: **decode** (JSON bytes to
model), **encode** (model to JSON bytes), and **round-trip** (decode then
encode).

### The document set (benchmark cells)

Five documents spanning sizes and shapes. Each is verified to parse on both
libraries at setup; a document only one side accepts is not a fair timing cell
(see "Capability gaps" below).

| Cell | Source | Shape |
| --- | --- | --- |
| `point-series (small)` | `example_py.json` | PointSeries coverage (temporal) |
| `grid (medium)` | `doc-example-coverage.json` | Grid coverage (temporal) |
| `tiled-ndarray` | `spec-tiled-ndarray.json` | bare TiledNdArray |
| `coverage-collection` | `doc-example-coverage-collection.json` | multi-member collection (temporal) |
| `grid-large (synthetic)` | generated at runtime | large Grid coverage, ~40k floats |

The corpus cells come from `tests/corpus/covjson-pydantic/` (covjson-pydantic's
own vendored test data). They are used in preference to the `playground/`
documents on purpose: the playground documents carry timezone-naive temporal
values, which covjson-pydantic's `AwareDatetime` rejects, so they cannot be
timed head-to-head at all.

The large NdArray cell is generated because the largest corpus document is only
about 5 KB. It is built from the public builders and encoded to canonical
CoverageJSON bytes that both libraries decode, so the "large array" comparison
is exercised without shipping a large fixture.

### Capability probes

A timing table can only compare documents both libraries accept. To keep the
one-sided gaps visible instead of silently dropping them, `run.py` also decodes
a handful of **capability probes**: small documents that are spec-valid (or
spec-faithful) for covjson-msgspec but trip a covjson-pydantic limitation. The
side that accepts the document reports a real decode time; the side that rejects
it shows `raises <Type>: <message>`, the exact exception it throws (type and
verbatim message), so the gap reads as a concrete failure rather than a blank.

The probes are run in both directions. The current set is entirely
"covjson-msgspec accepts, covjson-pydantic rejects" (naive datetimes,
reduced-precision temporal forms, extra axes, mixed-type axes); no document was
found that covjson-pydantic accepts and covjson-msgspec rejects, and the
rendered section states that explicitly rather than leaving it implied. This is
a decode-*acceptance* comparison only; the fuller capability and correctness
story stays with issue #22 (see below).

## Fairness: the coupling-aware ladder

The libraries divide the work differently, and hiding that would make the
numbers dishonest. covjson-pydantic's decode is **one fused, mandatory step**:
structural decode, validation, and parsing every temporal value to a
`datetime`. covjson-msgspec keeps those concerns **separate and opt-in**: decode
is structural only and leaves temporal values as strings; validation and
`datetime` conversion are things you ask for when you need them.

So the msgspec side is measured as a **cumulative ladder**, each rung a superset
of the one before, and each adjacent step isolating one cost:

1. `decode`: structural only; temporal values stay strings (what you pay by
   default).
2. `decode + validate`: adds the cross-cutting spec checks (no value scan).
3. `decode + validate(check_values=True)`: adds the O(n) value scans
   (value-vs-dataType, categorical codes, temporal lexical form, monotonic
   axes).
4. `decode + validate(check_values=True) + to_datetime(t)`: adds a `datetime`
   for every temporal coordinate.

Rung 4 is the **only honest like-for-like** with covjson-pydantic's decode: both
end with a validated model whose temporal values are real `datetime` objects.
The `+ datetime` column is skipped for a cell with no temporal axis (the bare
TiledNdArray and the synthetic Grid), shown as `n/a (no-temporal-axis)`.

Read the asymmetry precisely (the results file has a **validation parity**
scorecard, and the numbers below are from the harness's own probing). Each row's
**expected** behavior is the proportional response the spec calls for, graded by
requirement level: a `MUST` violation should error, a `SHOULD` violation should
warn (the document stays loadable), and a conformant input should be accepted. A
check or cross on each library then shows whether its response was proportional.
Both libraries error on **value-vs-dataType** and **value-count-vs-shape** (MUST
violations), so those overlap. covjson-msgspec also errors on **categorical
codes** and **monotonic axis order** (both MUST; the latter via an axis-order
checker that `check_values=True` applies by default but callers can replace or
disable, which the matched-work section below relies on); covjson-pydantic checks
neither, missing two MUST violations. The remaining two rows both go against
covjson-pydantic: on a **malformed** temporal value (`"2010-13-99"`, a Spec 5.2
SHOULD) covjson-msgspec warns and keeps the document while covjson-pydantic
raises, over-enforcing a SHOULD on a document the spec lets you load; and on the
**spec-valid** reduced-precision form (`"2020-06"`) covjson-pydantic rejects a
conformant document outright.

So covjson-msgspec's response is proportional on every row, and its detection is a
**superset** of covjson-pydantic's legitimate checks: at rung 4 it wins (where it
wins) while validating at least as thoroughly, and where covjson-pydantic's fused
decode comes out ahead, the table shows that plainly rather than hiding it.

### Matched-work comparison

Because rung 4 does more validation than covjson-pydantic, comparing it directly
can flatter covjson-pydantic on the cells where the extra work matters. The
**matched-work** section removes that confound by isolating the one ours-only
check that is both removable from our side and addable to theirs: the monotonic
axis scan (covjson-pydantic cannot be tuned, but we can pass a no-op
`axis_order_checker`, and we can bolt a manual monotonic scan onto a decoded
pydantic model). Two framings:

- **A, trim ours:** `validate` with the monotonic scan disabled, versus
  covjson-pydantic's plain decode.
- **B, add theirs:** our full `validate` versus covjson-pydantic decode plus a
  manual monotonic scan.

On the mid-size cells this **flips the result** (for example the coverage
collection goes from roughly parity to covjson-msgspec ahead), confirming the
monotonic scan was the reason covjson-msgspec looked slower. It does **not**
rescue `tiled-ndarray` or `grid-large`, and the section says so: those reflect
covjson-msgspec running validation as a **separate pass** over decoded objects
(a fixed per-call cost on the tiny document, a pure-Python per-element cost on
the 40k-element one) rather than the fused, native validation covjson-pydantic
performs during decode. That is a real architectural difference, not extra work
-- and a candidate for a future optimization, out of scope for this
measurement-only issue.

The **round-trip** table carries the same asymmetry (pydantic validates and
parses datetimes on its decode half), so it is reported with two msgspec
anchors, not one: a `structural` round-trip (`decode + encode`, the realistic
default cost of a faithful read and write) and a `full` round-trip (`decode +
validate(check_values=True) + to_datetime(t) + encode`, the like-for-like with
pydantic's fused round-trip). The encode table needs no such split: encoding is
symmetric, since both libraries simply serialize a model in hand.

## Methodology

- Standalone and stdlib-only: `timeit` for timing, `statistics` for the summary.
  No third-party benchmark harness.
- Each operation is warmed up once, then `timeit` autoranges the iteration count
  so a single timing sits well above clock resolution.
- The autoranged block is repeated (9 times for a full run, 5 for `--quick`);
  we report the **median** and the **interquartile range** (IQR) across repeats,
  in microseconds per operation.
- covjson-pydantic has no root union, so the decode class is chosen once, at
  setup, from each document's top-level `type` (never inside a timed loop).
- The environment (OS, CPU, Python) and the exact versions of covjson-msgspec,
  covjson-pydantic, msgspec, pydantic, pydantic-core, and numpy are recorded in
  both artifacts, so any number is traceable to the code that produced it.

Numbers are environment-sensitive. Compare ratios within a single run, not
absolute microseconds across machines; regenerate on your own hardware before
drawing conclusions.

## `results.json` schema

A stable contract the comparison documentation (issue #22) can consume:

```text
{
  "env":      {platform, python, implementation, machine, processor},
  "versions": {"<package>": "<version>", ...},
  "semantics": {"<lib>:<op>": "<what that operation actually does>", ...},
  "results": [
    {"cell", "lib", "op", "status": "measured",
     "median_us", "iqr_us", "n", "reason": null},
    {"cell", "lib", "op", "status": "skipped",
     "median_us": null, "iqr_us": null, "n": null, "reason": "<why>"},
    ...
  ],
  "probes": [ {"cell", "lib", "op": "decode", "status", ...}, ... ],
  "probe_notes": {"<probe name>": "<the gap it exposes>", ...}
}
```

Every operation is a row. A skipped operation is still a row with an explicit
`reason`, never an omitted line, so "not applicable, and why" is distinguishable
from "measured, fast". For fair cells the reason is a controlled value
(`no-temporal-axis`, `dual-parse-failed`, or `lib-unsupported`); for a probe it
is the deciding library's own raised exception, as `<Type>: <message>`. The
`probes` rows and `probe_notes` map back the capability-probe table.

## What this benchmark does NOT settle (see issue #22)

The capability probes above surface decode-*acceptance* gaps, but performance
and acceptance are still only part of the comparison. The broader functional and
spec-compliance story is a separate, first-class aspect of the covjson-pydantic
comparison (issue #22), of which this benchmark is only a slice. It owns the
things a timing table structurally cannot show:

- **Silent corruption of accepted values.** The nastiest gap is not a rejection
  but a wrong answer: covjson-pydantic *accepts* `"2020"` and decodes it to
  `1970-01-01T00:33:40Z` (a Unix-timestamp misparse), and drifts `Z` vs
  `+00:00` and sub-second precision on the full form. Both libraries "succeed",
  so nothing is raised to show; only a correctness comparison exposes it.
- **Composite axes**: a `Tuple` stub in covjson-pydantic vs full modeling here.
- **Slightly-malformed documents**: covjson-pydantic raises at construction;
  covjson-msgspec decodes permissively and reports issues via opt-in
  `validate()`, so you can load a document to inspect or repair it.
- **Static type precision and discoverability**: covjson-pydantic's genuine
  edge, and not something a throughput or acceptance table measures.

See [`docs/comparison/covjson-pydantic.md`](../docs/comparison/covjson-pydantic.md)
for the comparison notes these feed.

## Non-goals

- Not gated in CI (the numbers are environment-sensitive and noisy); regenerate
  manually.
- A one-shot snapshot, not a time series tracked across commits.
- Memory and import-time metrics are out of scope for now (an easy follow-up).
- Not a micro-optimization exercise: the goal is an honest, documented baseline.
