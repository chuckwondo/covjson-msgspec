# ADR-0003: `IssueCode` enum, closed because the library owns the codes

## Status

Accepted

## Context

`Issue.code` is the stable, machine-readable handle a consumer matches on:
filter, suppress, route, or assert in a test. It is deliberately decoupled from
the human-rewordable `message` and the location-bearing `path`. Originally each
built-in check passed a bare string literal (`code="domain.missing-axis"`), so
the set of codes, their spelling, and their `category.key` shape were enforced
only by convention -- not discoverable, not typo-safe, not autocompletable.

Two questions surfaced while reviewing the validation codes:

1. Should the known codes be an enum, so the contract is a discoverable,
   checkable artifact rather than a scattering of string literals?
2. The codes encode two things (a `category` and a `key`, e.g.
   `domain.missing-axis`). Should consumers be able to match broadly on the
   category without string-prefix hacks?

A tempting precedent was `DomainType`, a `StrEnum` of the well-known domain
types whose *field* (`Domain.domain_type`) stays a bare `str`. But that pairing
is shaped by a force absent here. `domain_type` must stay open because the
CoverageJSON spec permits custom domain-type URIs: real documents in the wild
carry values the library cannot enumerate, so the field has to accept any
string and the enum is merely a convenience for the known ones.

Validation codes have no such external producer. The library is the sole author
of every code. The one extension seam, `DOMAIN_TYPE_RULES`, supplies only
axis-constraint *data* (`DomainTypeRule`: required/optional/single-valued axes);
the built-in check functions interpret that data and emit the library's own
codes. Registering a custom domain-type rule reuses those functions and so
yields existing codes (`domain.missing-axis`, ...) -- it cannot introduce a new
one. The set of codes is therefore closed in fact, not merely by convention.

(`Issue` is not a CoverageJSON wire type; it is an in-process validation report.
How it might serialize is out of scope for this decision.)

## Decision

Add `IssueCode(StrEnum)` enumerating every validation code, emit members from
every check, and type the field **`Issue.code: IssueCode`** -- closed, because
the library owns the entire set. A `StrEnum` member is a `str`, so consumers may
match a code with `==` against either the member (`IssueCode.DOMAIN_MISSING_AXIS`)
or its plain-string literal (`"domain.missing-axis"`), and the closed type
additionally lets a consumer match exhaustively (a `match` over the members,
checked).

Do **not** add a stored category field or a `category`/`key` accessor now.
Document the `category.key` convention on `IssueCode`, and leave broad matching
to whole-code equality plus `path` for locality.

## Alternatives considered

**Type the field `code: str` (open), as `DomainType`/`domain_type` does.**
Rejected. That pairing is a response to spec-forced openness, which validation
codes do not face: there is no external producer, so an open field would type
the contract less precisely than the library can actually guarantee, and would
forgo consumer-side exhaustiveness. Keeping it open would only be justified as a
*hedge* for a hypothetical future seam where third-party check callables emit
their own codes -- a capability that does not exist, is not on the roadmap, and
would in any case be a deliberate, versioned API expansion (widening
`IssueCode` back to `str`) rather than something to pre-pay for now.

**Promote `category` to a second stored field on `Issue`.** Rejected for now.
The leading segment is a fuzzy producer-side grouping, not a clean taxonomy:
range-related findings live under *both* `range.*`
(`range.value-type-mismatch`, `range.invalid-category-code`) and
`coverage.range-*` (`coverage.range-without-parameter`,
`coverage.range-shape-mismatch`, `coverage.range-axis-not-in-domain`), because
the prefix tracks the check's *locus* (the coverage-level cross-check vs. the
range-value scan) rather than its *subject*. A stored field would reify that
fuzziness into a stable API. Reconciling the overlap is left as a separate
question; a category view is only worth adding once the categories mean
something, and no consumer needs broad matching yet (YAGNI). This is consistent
with the "opt-in typed projection over a faithful core" tenet: expose grouping
as a derived view if and when it earns its place, not as stored state.

**Add a `category`/`key` accessor (split on the first `.`) without a stored
field.** Deferred for the same reason: it would advertise a grouping the codes
do not yet cleanly support. `path` already covers locality-based matching, so
nothing is blocked by waiting.

## Consequences

- The code contract is now a single discoverable, typo-safe, autocompletable,
  closed artifact. Built-in checks reference members, so a misspelled code fails
  fast at import; consumers get the full set at the type level and can match
  exhaustively.
- Matching is unaffected by the close: a `StrEnum` member is a `str`, so
  `issue.code == "domain.missing-axis"`, `== IssueCode.DOMAIN_MISSING_AXIS`, and
  set membership all keep working.
- Adding a new code is a member addition (backward-compatible). The reverse --
  ever needing to admit a code the library does not define -- would mean
  widening the field away from `IssueCode`, a deliberate breaking change gated
  on a real custom-check-plugin design.
- Broad category matching and the `range.*` / `coverage.range-*` taxonomy
  overlap are explicitly deferred, not solved. If a real need arises, the
  layering-preserving move is a derived accessor over a reconciled taxonomy, not
  a second stored field.
