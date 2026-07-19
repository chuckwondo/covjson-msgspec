# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs): the detailed,
append-only historical record of cross-cutting decisions whose rationale a
reader could not recover from the code alone. CLAUDE.md (Working agreements) is
the source of truth for when an ADR is warranted; this README covers only the
mechanics of the directory.

## Numbering

ADRs are numbered with a zero-padded, plain-sequential `NNNN` prefix
(`0001-title.md`, `0002-title.md`, ...), assigned in the order they are
accepted, regardless of topic. The number is permanent: a superseded ADR keeps
its file and number and is marked `Superseded by ADR-NNNN` rather than deleted
or renumbered.

`template.md` is the copyable starting point and is intentionally not numbered,
so it never consumes a sequence number.

## What "append-only" protects

The decision and its rationale, not the vocabulary. An accepted ADR is never
renumbered or deleted, and a decision that is later reversed is superseded
rather than rewritten, so the record of what was chosen (and why) survives even
once it stops being true.

Everything else is maintenance. An ADR is amended in place to sweep a rename, so
the record still resolves to names that exist (#115 renamed
[ADR-0012](0012-custom-members-dropped-on-decode.md), file included, and
rewrote its Context to adopt the spec's "custom member" over "foreign member"),
to sharpen its reasoning as a decision proves itself (#76 added a paragraph to
[ADR-0002](0002-opt-in-tiered-validation.md) distinguishing which local
invariants belong at construction), or to record a rejected alternative
surfaced afterwards.

The test: does the edit change what the ADR decided, or why? If not, it is
maintenance; make it. If so, supersede instead. Renaming a code an ADR cites
leaves its decision untouched, so sweep it; a stale identifier only makes the
record a dead reference.

## Format

Each ADR follows the lightweight template in [template.md](template.md):

- **Title**: `# ADR-NNNN: <decision>`.
- **Status**: `Accepted` or `Superseded by ADR-NNNN`.
- **Context**: the forces at play; what made this a decision worth recording.
- **Decision**: what we chose, stated plainly.
- **Alternatives considered**: the real rejected options and why they lost.
- **Consequences**: what follows, including the costs we accept.

Keep each ADR self-contained, and do not restate conventions already in
CLAUDE.md.

## Index

- [ADR-0001](0001-python-3-11-floor.md): Python 3.11 floor, coupled to titiler
- [ADR-0002](0002-opt-in-tiered-validation.md): Cross-cutting checks live in
  opt-in `validate()`, not `__post_init__`
- [ADR-0003](0003-issue-code-enum.md): `IssueCode` enum, closed because the
  library owns the codes; category matching deferred (superseded by ADR-0006)
- [ADR-0004](0004-ndarray-single-non-generic-class.md): `NdArray` as a
  single, non-generic class; element typing via `validate(check_values=True)`
- [ADR-0005](0005-langcodes-core-dependency.md): `langcodes` cleared the bar
  for a core dependency, backing the BCP 47 language-tag check
- [ADR-0006](0006-validation-findings-sum-type.md): validation findings as a
  closed sum type (typed variants + tagged union); replaces the `IssueCode` enum
- [ADR-0007](0007-functional-core-errors-as-values.md): best-effort fetching as
  a functional core; failures are `FetchFailure` values, a pure strategy reducer,
  a `FetchError` raise bridge
- [ADR-0008](0008-temporal-conversion-result-projection.md): temporal string
  conversion as a faithful `TemporalResult` sum type + opt-in lexical `validate()`
  check; `to_datetime` the stdlib convenience
- [ADR-0009](0009-openapi-schema-bridge.md): OpenAPI schema bridging from the
  msgspec types; a pure `schema.py` generator plus a thin FastAPI adapter,
  components namespaced under `CoverageJSON.` to avoid host collisions
- [ADR-0010](0010-dependency-floor-policy.md): dependency floor policy: floors
  are the lowest wheeled version providing the APIs used, tested at both
  `lowest-direct` and `highest`, raised only deliberately (never by Dependabot)
- [ADR-0011](0011-axis-ordering-checker-seam.md): the monotonic-axis MUST behind
  an injected `AxisOrderChecker` seam with a conservative `require_monotonic`
  default; a single total classifier decides which reference systems order
- [ADR-0012](0012-custom-members-dropped-on-decode.md): custom members
  (spec extensions) are dropped on decode and not captured; the typed model is a
  lossy projection, and lossless relay forwards raw bytes rather than
  round-tripping
- [ADR-0013](0013-unset-for-omittable-inheritance-members.md): the five
  collection-inheritance members use `X | UnsetType = UNSET`, rejecting a
  spec-forbidden `null` at decode and separating "omitted" from "null"; narrow by
  design, reconciled with the ADR-0002 permissive-decode stance
- [ADR-0014](0014-documentation-toolchain.md): documentation via ProperDocs +
  mkdocstrings, chosen for static (griffe) API extraction that renders the
  `TYPE_CHECKING`-only bridge signatures faithfully; the engine is a reversible
  swap over a portable Markdown + `objects.inv` substrate
- [ADR-0015](0015-bridge-temporal-classification.md): the export bridges
  classify temporal values by calendar + container range, not via `resolve`;
  the three paths have different codomains, so there is no single classifier to
  unify (resolves the ADR-0008 follow-up as "will not route")
- [ADR-0016](0016-readonly-mapping-members.md): frozen structs' mapping members
  are typed read-only `Mapping` (runtime stays `dict`), so a checker rejects
  in-place mutation at zero runtime cost; a `frozendict` runtime is deferred to
  #117, and return types are deliberately not swept
- [ADR-0018](0018-typed-projection-scope.md): a typed projection earns its keep
  only where it recovers a guarantee nothing else enforces, so neither `Axis` nor
  `NdArray` gains a `refine()`; the three ADR-0004 instances differ by rule, and
  the "name the repair" test places a check at construction or in `validate()`
- [ADR-0019](0019-composite-coordinates-required.md): a `tuple`/`polygon` axis
  must supply `coordinates` at construction (tuple ≥1, polygon ≥2) because the
  default (the axis's kind-name `"composite"`) names no real coordinate; it fits
  only primitive/custom axes

Some decisions are recorded in ADRs that land with their implementation rather
than here; see the issue tracker for the in-flight set.
