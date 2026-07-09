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

Some decisions are recorded in ADRs that land with their implementation rather
than here; see the issue tracker for the in-flight set.
