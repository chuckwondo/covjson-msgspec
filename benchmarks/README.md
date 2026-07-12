# Benchmarks: covjson-msgspec vs covjson-pydantic

Throughput numbers for the core codec operations, comparing this library against
[covjson-pydantic](https://github.com/KNMI/covjson-pydantic) (the established
Pydantic-based CoverageJSON library). This is issue #18; the results feed the
covjson-pydantic comparison documentation (issue #22).

The report is assembled from three pieces: `run.py` measures and writes
`results.json` (the machine-readable data); `results.template.md` holds the prose
with `{{placeholder}}` tokens; and rendering fills those placeholders from the
data to produce `results.md` (the human-readable report). `results.md` and
`results.json` are generated, committed artifacts. **Do not edit them by hand**:
edit the template for prose, or the generator for numbers.

## Running

covjson-pydantic is a benchmark-only dependency (never a runtime dependency of
the library), so it lives in the `bench` dependency group:

```sh
uv sync --group bench
uv run --group bench python benchmarks/run.py               # measure + render
uv run --group bench python benchmarks/run.py --quick       # fast smoke run
uv run --group bench python benchmarks/run.py --render-only  # re-render prose only
```

`--render-only` re-renders `results.md` from the committed `results.json` and the
template without measuring, so editing prose costs a second rather than a full
run. On a measured run both artifacts are written together, so they cannot
disagree.

## The document set (provenance)

The five documents the tables are measured against, and where they come from.
`results.md` describes what each one exercises and why its cost is what it is;
this records their source. Each is verified to parse on both libraries at setup,
since a document only one side accepts is not a fair timing cell.

| Cell | Source |
| --- | --- |
| `point-series (small)` | `example_py.json` |
| `grid (medium)` | `doc-example-coverage.json` |
| `tiled-ndarray` | `spec-tiled-ndarray.json` |
| `coverage-collection` | `doc-example-coverage-collection.json` |
| `grid-large (synthetic)` | generated at runtime |

The corpus cells come from `tests/corpus/covjson-pydantic/` (covjson-pydantic's
own vendored test data), used in preference to the `playground/` documents on
purpose: the playground documents carry timezone-naive temporal values, which
covjson-pydantic's `AwareDatetime` rejects, so they cannot be timed head-to-head.

The large cell is generated because the largest corpus document is only about
5 KB. It is built from the public builders and encoded to canonical CoverageJSON
that both libraries decode, so the large-array comparison is exercised without
shipping a large fixture.

## Capability probes

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

## How the comparison is constructed

The two libraries divide the work differently, and `results.md` explains that
split where you read it: covjson-pydantic's decode is one fused step (structural
decode, validation, and datetime parsing), while covjson-msgspec exposes those as
an opt-in ladder. See the **Decode and the validation ladder** and **Validation
parity** sections of `results.md` for how to read the rungs, the speedup markers,
and the ⚠️ that flags where covjson-pydantic skips a MUST check.

### Matched-work framings

The full ladder does more validation than covjson-pydantic, which flatters
covjson-pydantic on the cells where the extra work matters. The matched-work
tables neutralize the one ours-only check that is both removable from
covjson-msgspec and addable to covjson-pydantic, the monotonic axis scan:

- **Framing A** passes a no-op `axis_order_checker`, so covjson-msgspec does no
  more validation than covjson-pydantic's plain decode.
- **Framing B** bolts a manual monotonic scan onto a decoded covjson-pydantic
  model, so covjson-pydantic does covjson-msgspec's full validation.

`results.md` reads the outcome; the one subtlety worth recording here is the
`coverage-collection` Framing B row. It is like-for-like (both run the monotonic
check) yet leaves covjson-msgspec slightly behind, and the gap is not a skipped
check: covjson-msgspec's monotonic pass resolves temporal values from their
strings per member domain (roughly 35us across the two members), while
covjson-pydantic compares `datetime`s it already parsed during its fused decode
(roughly 9us). It is a separate-pass cost, and a candidate for the same kind of
native fast path the value scan now has.

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
