# CLAUDE.md

Guidance for working in this repository (for Claude Code and human
contributors alike). The README is the user-facing introduction; this file is
the contributor-facing source of truth for conventions, architecture, design
tenets, and workflow, so a fresh clone starts warm.

The canonical task list is GitHub issues, organized under the
`0.1.0: first public release` milestone. Run
`gh issue list --milestone "0.1.0: first public release"` to see the queue.

## What this is

`covjson-msgspec` is a fast, fully-typed [CoverageJSON](https://covjson.org/)
library built on [msgspec](https://jcristharif.com/msgspec/), positioned as an
alternative to [covjson-pydantic](https://github.com/KNMI/covjson-pydantic). See
the README for install extras and usage.

## Architecture

A **thin core + opt-in bridges** design. The core depends only on msgspec and
langcodes (both pure Python, no heavy or native transitive dependencies;
`langcodes` backs the BCP 47 language-tag check in `validation.py`, see
ADR-0005 for why a small, dependency-free-in-its-base-install package cleared
the bar a heavier one would not); each bridge lazy-imports its own (larger)
dependency inside helper bodies and raises an install hint if it is missing.

Core model (`src/covjson_msgspec/`):

- `_base.py` - `CovJSONStruct`, the shared base: `frozen=True`,
  `omit_defaults=True`, `rename="camel"` (snake_case attributes map to
  CoverageJSON's lowerCamelCase wire names). `frozen` is not inherited, so every
  concrete struct restates it. This base is the signal for a wire/codec type:
  subclass it only for types that cross the CoverageJSON wire. Internal value
  types that never serialize as CoverageJSON (`Issue`, `DomainTypeRule`)
  subclass bare `msgspec.Struct(frozen=True)` instead -- still a Struct (one
  immutable-record system, and msgspec is already a core dependency), but
  without the wire-facing `rename`/`omit_defaults`. Reserve plain `@dataclass`
  for cases that need to avoid msgspec entirely; we have none today.
- Spec structs: `axis.py`, `domain.py`, `range.py` (NdArray / TileSet /
  TiledNdArray + the numpy bridge methods), `coverage.py` (Coverage /
  CoverageCollection + the codec helpers), `referencing.py`, `parameter.py`,
  `i18n.py`.
- Tagged unions dispatch on the `"type"` field. The root union is
  `CoverageJSON = Coverage | CoverageCollection | Domain | NdArray |
  TiledNdArray`; `decode` / `decode_coverage` / `decode_coverage_collection` /
  `encode` are the codec entry points.
- `validation.py` - opt-in, tiered `validate()` returning `Issue` reports
  (never raises on its own unless `mode="raise"`); `check_values=True` adds the
  O(n) element checks.
- `_fetch.py` - the injected-fetcher seam: `Fetch = Callable[[str], bytes]`
  (and `AsyncFetch`). The core never performs I/O; callers inject a fetcher.
  Consumed by `references.py` (`resolve_references` / `_async`) and
  `TiledNdArray.assemble` / `assemble_async`.
- `media_type.py` - framework-agnostic HTTP helpers (`MEDIA_TYPE`,
  `encode_response`, `decode_response`, ...).
- `subset.py` - native `isel` / `sel` (reuses the stride math in `_ndindex.py`).
- `_bridging.py` - shared bridge helpers; `_repr.py` - `_repr_html_` builders.

Bridges (each behind an extra): `numpy` (NdArray <-> numpy, methods on NdArray),
`xarray.py` (two-way, CF-aware Coverage <-> xarray + DataTree), `pandas.py`
(one-way to pandas), `geo.py` (one-way to geopandas / GeoJSON).

## Design tenets

- **Dependency injection at the edges, data-in/data-out at the core.** The core
  never reaches the network or imports a web framework; it accepts a seam (a
  callable, a return value) and lets the caller wire in their choice. Optional
  dependencies are imported locally inside helpers, not as module-level imports.
- **A functional core with an imperative shell.** The core is pure functions
  over immutable data: helpers return values (a stream of `Issue`, a `Failure`)
  rather than mutating a shared accumulator or performing effects, and favor
  implicit iteration (comprehensions, `itertools.chain`, `functools.partial`)
  over explicit loops. Effects -- I/O, raising, sleeping, materializing a
  stream -- live in a thin shell at the edges: the codec entry points,
  `validate`'s `mode=`, the injected fetcher. Errors are values first: a rich
  domain report (`Issue`, and the planned `Failure`) with an opt-in raise
  bridge, not exceptions threaded through the core. This is the same instinct as
  dependency injection at the edges, applied to control flow.
- **Opt-in tiered `validate()`, not `__post_init__`, for cross-cutting checks.**
  `__post_init__` is only for local structural invariants (cheap, O(1), about
  one object); anything cross-cutting or data-scanning lives in `validate()` so
  decode stays permissive. Even among local invariants the tier splits again:
  reject in `__post_init__` only when a violation leaves the object
  *uninterpretable in isolation* (an `Axis` neither listed nor regular, an
  `ObservedProperty` categorical yet listing no categories); leave a merely
  *internally inconsistent* object (an `NdArray` whose `shape` and `axisNames`
  disagree) to `validate()`, so a repairable document still loads. See ADR-0002.
- **A byte-faithful model with lossy conversion confined to opt-in bridges.**
  Decode preserves every spec-defined member (for example temporal values stay
  raw ISO 8601 strings, never parsed to `datetime`); conversions that lose
  information happen only in the export bridges. The one carve-out is *foreign
  members* (custom extension keys the CoverageJSON spec does not define):
  msgspec drops them on decode, so `decode -> encode` is lossy for them. The
  spec permits extensions but does not require preserving them; the model
  deliberately does not capture them (ADR-0012), so relaying a document
  unchanged means forwarding its raw bytes, not round-tripping through the
  model.
- **Opt-in typed projection over a faithful core.** Where one concrete type
  encodes several logical types, expose precise typing as an explicit, opt-in
  projection (an accessor), never as the stored/decoded representation, and not
  via element-typed subclasses or type guards.

## Conventions

The canonical coding-style reference for every contributor, human or Claude. A
`CONTRIBUTING.md`, if added, should link here (`CLAUDE.md#conventions`) rather
than restate these. Grouped below: code, docstrings and doctests, tests, and
prose wrapping.

Code:

- `from __future__ import annotations` at the top of every module (after the
  docstring). Write bare annotations; quote only the strings inside `cast()`.
- Absolute imports only (relative imports are banned by ruff).
- Don't import another module's `_private` member. To share an internal helper
  across modules without widening the public API, give it a home in a
  `_`-prefixed module (`_bridging.py`, `_i18n.py`) and import its non-underscore
  name. The two underscores mark different boundaries: `_` on a member means
  "private to this module" (only that file uses it); `_` on a module means
  "internal to the package" (its non-underscore names are the intra-package API,
  off-limits to end users). This keeps every module-local `_helper` genuinely
  file-local: safe to rename or inline after grepping a single file. (Ruff's
  PLC2701 enforces the neighboring rule, banning imports of *another package's*
  privates, but not this intra-package case, which review must catch.)
- Place `_private` module functions after the public API. Private helpers still
  get full numpy-style docstrings with cheap, runnable examples.
- Prefer implicit iteration (comprehensions, generator expressions,
  `itertools.chain`) over explicit `for`/`while` where it stays readable; reach
  for an explicit loop only when the body has genuine imperative structure.
- Build behavior from small, single-purpose, composable functions. Let the
  full-docstring-with-example convention be the granularity test: if a candidate
  helper can't earn a real runnable example, it's too small to extract.
- Compose with the standard library (`itertools`, `functools`); prefer small
  named domain functions over point-free combinators that fight the type
  checkers.
- A checker/transform helper returns an iterable rather than taking and mutating
  a shared accumulator.
- Trust type contracts: no runtime `isinstance` check against a type the
  parameter's annotation already excludes.
- Assign an exception message to a `msg` variable before `raise X(msg)` (ruff
  EM).
- Bridge naming: name a bridge for its destination library or format
  (`to_pandas`, `to_xarray`, `to_geojson`); `to_datatree` is the type-named
  exception.
- snake_case attributes map to lowerCamelCase wire names via `rename="camel"`,
  so the ruff N815 warning is intentionally not applied.
- Put blank lines around block statements (if / for / while / with / try / def /
  class) to separate them from sibling statements; ruff format enforces the
  edge-trimming exceptions.
- Link the CoverageJSON spec from docstrings and comments where it teaches the
  wire format; centralize the spec and RFC links in the module docstring rather
  than repeating URLs.
- Prefer colons, parentheses, or a shorter sentence to em-dashes, reaching for
  one only where it genuinely earns its keep; then write it as a double hyphen
  (`--`), never the Unicode em-dash character. In a definition list (a term and
  its gloss), always use a colon. This governs prose in docs, docstrings, and
  comments alike.

Docstrings and doctests:

- Keep doctest lines within 88 characters (ruff E501 checks docstrings); wrap
  long byte blobs via implicit string concatenation.
- Multi-line JSON in docstrings uses `indent=2` with short arrays kept inline
  (one key per line; arrays like `[1, 2, 3]` stay on one line). Single-line JSON
  stays single-line. Do not reformat verbatim `msgspec.json.format(indent=2)`
  output, which is program output that must match exactly.

Tests:

- Place `_helper` functions after all `test_` functions (mirrors the source
  convention of private helpers after the public API). Exception: a helper
  called at module-load time (e.g., directly inside a `@pytest.mark.parametrize`
  decorator) must precede its first use; treat it like a module-level constant.
- Prefer `@pytest.mark.parametrize` over `for` loops inside test functions; each
  case becomes a separately reported, independently re-runnable test item.

Prose wrapping (three surfaces, three rules):

- Git commit messages: hard-wrap, standard conventions (~50-char subject, body
  wrapped at ~72).
- Repo Markdown files (this file, README, docs, ADRs): hard-wrap prose at 80
  columns. Do not wrap fenced code blocks, tables, or long URLs / link
  definitions.
- GitHub issue / PR / comment bodies: do not hard-wrap at all; let the renderer
  reflow. Use Markdown structure for layout, not newlines.

## Development workflow

This project uses [uv](https://docs.astral.sh/uv/).

```sh
uv sync                          # core + all bridges + dev tooling
uv sync --group typecheck        # also installs basedpyright (Node runtime)
uv sync --group bench            # also installs the benchmark deps

uv run pytest                    # tests + doctests (--doctest-modules is on)
uv run ruff check                # lint
uv run ruff format               # format
uv run mypy                      # strict; blocking in CI
uv run basedpyright              # strict; blocking in CI (needs typecheck group)
uvx ty check                     # informational
uvx pyrefly check                # informational
uv run prek run --all-files      # the pre-commit suite
```

Type checking runs four checkers: mypy (strict) and basedpyright (strict) are
blocking; ty and pyrefly are informational. basedpyright bundles a Node runtime
and may not build on every local machine; rely on CI when it cannot run
locally.

The Python floor is `>=3.11`, set deliberately to match titiler's floor (this
library is meant to slot into the titiler-covjson ecosystem). It is a coupling,
not a technical requirement; raising it is gated on titiler moving first.

## Working agreements

- **Do not `git commit` until the change has been reviewed**, even for
  pre-agreed work. Branch off `main`; do not commit directly to `main`.
- **Draft GitHub issues and present them for review before creating them**
  (before `gh issue create`, and before substantive `gh issue edit` body
  rewrites).
- **`design`-labeled issues evaluate for an ADR** at the end of their design
  pass; if the outcome warrants one, write it under `docs/adr/` and link it from
  the issue. ADRs capture cross-cutting decisions whose rationale a reader could
  not recover from the code alone. Format: Title, Status (`Accepted` or
  `Superseded by ADR-N`), Context, Decision, Alternatives considered,
  Consequences. ADRs are the detailed, append-only historical record; the
  (future) "Design decisions and tradeoffs" doc links out to individual ADRs
  rather than duplicating their content.

## Work order

The agreed sequence for remaining work: features -> benchmarking -> docs and the
covjson-pydantic comparison, cheapest-first within each track. The
`0.1.0: first public release` milestone holds the issues committed to the first
release; deferred or externally-gated items are intentionally left
milestone-less.
