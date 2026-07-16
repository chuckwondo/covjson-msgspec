# ADR-0011: Monotonic-axis validation behind an injected ordering seam

## Status

Accepted

## Context

CoverageJSON's Axis Objects section states a MUST: if an axis's `dataType` is
`"primitive"` and its reference system "defines a natural ordering of values",
then the axis's `values`, if present, MUST be ordered monotonically (increasing
or decreasing). This is section 6.1.1 in the community `spec.md` this project
cites (the same content is clause 9.6.1.1 in the OGC 21-069r2 rendering). It is a
MUST, so [ADR-0002] maps it to an *error*, a different tier from the SHOULD-level
`domainType`/temporal warnings that shipped alongside it.

Implementing it forces several judgment calls, and not all of them are the
library's to make:

- **Strictness.** The spec says "monotonically", not "strictly". Whether
  equal-adjacent values (a repeated coordinate) are a violation is a reading of
  an ambiguous MUST, and this error aborts `validate(mode="raise")`, so
  over-flagging is costly.
- **Which systems define a natural ordering.** The spec ties the MUST to that
  condition without enumerating which systems satisfy it (its reference-system
  section is 6.1.2). A CRS orders its coordinates; an identifier (categorical)
  system does not.
- **How to compare, and what is comparable.** A time axis must be compared as
  instants, not raw strings ([ADR-0008]). Some spec-legal temporal values have no
  `datetime` (expanded years, leap seconds: `Unrepresentable`), and a caller with
  a non-standard calendar may know an ordering the library cannot.

The first is a policy a caller may want to change; the last two are knowledge a
caller may *have* that we do not. That is the same dependency-injection-at-the-
edges instinct behind `Fetch` and `FailureStrategy`, and the `parse_time=` seam
[ADR-0008] shaped `resolve` toward.

## Decision

**A conservative default behind an injected ordering seam.** `validate` gains an
`axis_order_checker` parameter, an `AxisOrderChecker`:

```python
AxisOrderChecker = Callable[[Sequence[AxisValue], ReferenceSystem | None], int | None]
```

Given a primitive axis's `values` and the reference system governing them, it
returns the index of the first value that breaks the required ordering, or `None`
for nothing to report. The check is value-scanning, so it runs only under
`check_values=True` ([ADR-0002]); the parameter defaults to `None`, resolved to
the exported default `require_monotonic()`, so the check runs by default when
values are scanned and the seam only *customizes* it. A finding is an
`AxisNotMonotonic` error pointing at the breaking value.

**The default, `require_monotonic(*, strict=False)`, encodes the conservative
reading:**

- Non-strict: only a direction reversal is a break; equal-adjacent values pass.
  `require_monotonic(strict=True)` opts in to rejecting them.
- `values` only. Bounds ordering is not the subject of this MUST.
- Ordered systems only, decided by one total classifier, `_ordering_kind`: a
  geographic, projected, or vertical CRS orders numerically; a standard-calendar
  `TemporalRS` orders as instants; an identifier system, a non-standard-calendar
  temporal system, and an axis with no system in scope define no ordering. The
  `match` is exhaustive over the `ReferenceSystem` union (`assert_never`), so a
  new reference-system type forces a decision here.
- Temporal comparison resolves each value and compares only the `Moment`
  subsequence; `Malformed` (owned by the `temporal.lexical-form` check) and
  `Unrepresentable` values are skipped, and an axis whose moments mix
  timezone-awareness is skipped rather than compared across a naive/aware
  boundary.

The classification of which systems order is our reasoned interpretation of the
spec's "natural ordering", not a verbatim enumeration the spec provides.

## Alternatives considered

**Hardcode every decision, no seam.** Rejected. Strictness is a caller policy,
and a non-standard calendar or an `Unrepresentable` value's ordering is caller
knowledge the library cannot have; baking in one answer forecloses it.

**A `strict=` flag plus an injected per-value key function (two knobs).**
Rejected in favor of one whole-axis predicate with a parameterized default
factory. The key-function contract leaks the naive-vs-aware comparability hazard
into every caller's function; the whole-axis predicate keeps that, the
skip rules, and strictness encapsulated in the default, and its "first break
index" return maps exactly onto the one-finding-per-axis pointer.

**A typed sum-type return (`Ordered | Breaks(index)`).** Rejected. The repo's sum
types ([ADR-0006], [ADR-0008]) are values the library *returns for callers to
consume*; this seam is the mirror case, a callable the caller *implements*, so a
trivial `int | None` keeps the extension point cheap to satisfy. Unlike the
`datetime | None` [ADR-0008] rejected, nothing is lost: the contract is "first
violation, if any", so `None` is the complete answer, and the
ordered-vs-not-applicable distinction is one no consumer reads.

**Pass the whole `Axis` to the seam.** Rejected on cohesion. The seam's one
concern is coordinate-value ordering, so it takes `values`, not `bounds` or the
regular form. A future bounds-ordering rule is a separate check, not a change to
this public signature (a one-way door kept deliberately narrow).

**More aggressive defaults** (strict monotonicity; also flag numeric-valued axes
with no declared reference system). Rejected as the *default*: strict over-reads
the MUST, and flagging unreferenced numeric axes would false-positive on an
undeclared identifier axis carrying integer codes. Both remain reachable through
a custom `AxisOrderChecker`.

## Consequences

- A new error code, `axis.not-monotonic`, runs by default under
  `check_values=True`, so a document that was silently non-conformant on this
  MUST now surfaces an error (the expected effect of adding a conformance check).
- `_ordering_kind` is the single home for "which systems define a natural
  ordering", shared by the code and this ADR; growing the `ReferenceSystem` union
  forces an ordering decision at compile time rather than silently defaulting a
  new type to "unordered".
- The `AxisOrderChecker` signature is public and hard to change; the narrow
  `(values, system)` shape is a deliberate one-way door.
- The default's first cut does not catch a reversal that hinges on an
  `Unrepresentable` value between two moments; a caller who can order such values
  supplies a checker. The upgrade path is a comparable key on `resolve`.
- This is a sibling of the deferred `parse_time=` seam ([ADR-0008], and #61/#62):
  an injected *ordering* policy, distinct from an injected *parsing* one.

[ADR-0002]: 0002-opt-in-tiered-validation.md
[ADR-0006]: 0006-validation-findings-sum-type.md
[ADR-0008]: 0008-temporal-conversion-result-projection.md
