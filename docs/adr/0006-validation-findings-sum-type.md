# ADR-0006: Validation findings as a closed sum type

## Status

Accepted

Supersedes [ADR-0003](0003-issue-code-enum.md) (the `IssueCode` enum) and the
`Issue.code` category-matching API tracked in issue #40.

## Context

`validate()` reported findings as a single flat `Issue` struct tagged by an
`IssueCode` enum, with the human message built inline as an f-string at ~21
construction sites. Three things that structure could not do:

1. **Typed payload.** A finding's substitution values (which axis, which tile
   dimension, which size) were interpolated into the message string and then
   discarded. A consumer that wanted "the axis that was missing" had to parse it
   back out of English.
2. **Exhaustiveness.** Consumers matched on `issue.code == member`. Nothing told
   a consumer (or the type checker) when it had failed to handle a kind of
   finding, and nothing failed when a new kind was added.
3. **Serialization.** `Issue` was an in-process report only; there was no
   machine-readable form a separate process could consume.

Modelling each finding kind as its own frozen struct (a closed union, the
Python transliteration of a Rust `Result`-style error enum) buys all three.
Two design questions had to be settled, and were, with the priorities (highest
first): **(P1)** typed, exhaustive findings; **(P2)** one source of truth per
finding; **(P3)** interoperable, serializable reports; **(P4)** correctness by
construction, *proportional to stakes*; **(P5)** dual-style consumption (match
by type *and* by string).

The two questions:

- **How to carry the code, and whether to keep serialization deferred.** A
  msgspec tagged union (`tag_field="code"`) makes a `list[Issue]` encode to a
  machine-readable report and decode back to the exact variants. The tag string
  *is* the code, so a separate `IssueCode` enum is redundant.
- **How to model the pointer `at`.** A JSON Pointer has an escaping invariant,
  so it is not a role-only value; the question was how much construction
  machinery a *diagnostic* pointer warrants.

## Decision

Model each finding kind as a frozen `msgspec.Struct` subclassing a private base
`_Issue`, unioned into the public `Issue`. `validate()` still returns
`list[Issue]`.

- **Base `_Issue`** (`frozen`, `kw_only`, `omit_defaults`, `tag_field="code"`)
  carries the two fields every finding shares (`at: str` and `severity`), plus
  a `code` **property** returning the class's msgspec `tag`, and an abstract
  `__str__`.
- **One struct per finding kind (21)**, each **pinning** its `tag` to the stable
  code string it already used (`tag="domain.missing-axis"`), carrying its
  substitution values as typed fields, and rendering its message via `__str__`
  (Display); the structural view stays msgspec's auto `repr` (Debug).
- The `IssueCode` enum and the `Issue.message` field are **removed**;
  `Issue.path` is **renamed** `at`. `CovJSONValidationError` builds its summary
  from `str(issue)`.
- `_ptr` is unchanged: it remains the single sanctioned builder of an `at`
  pointer (it takes tokens and escapes internally).

Because the union is a msgspec tagged union keyed on `code`, a report is
serializable and round-trips: the previously deferred capability, now free.

**The `code`-vs-type rule** (documented on `_Issue`): reach for the **type**
(`match` / `assert_never` / `isinstance`) for anything the checker should
verify: exhaustiveness, reading a variant's typed payload (`issue.code == ...`
does *not* narrow), refactor-safety; reach for **`code`** only for stringly work
that leaves the type system: aggregation, logging, the wire tag, a loose
`code == "..."` match by a consumer that never imports the variant classes.

## Alternatives considered

**Keep `IssueCode` as `code`'s typed return.** Rejected (P2). The variant
*types* already give typed, exhaustive discrimination, strictly more than the
enum, which never narrowed payload. Keeping the enum would maintain a second
list (every `tag` aligned with an enum member) that the sum type exists to
retire. `code` returns a plain `str`; the string does stringly work, the type
does typed work, and nothing is left in the middle for the enum to do.

**Brand `at` with `NewType` (the shape issue #53 first proposed).** Rejected. A
JSON Pointer has an escaping invariant, so `NewType("Pointer", str)` (which
adds a name, not a check) accepts any string, and on a *public* surface that is
the "false confidence" anti-pattern (a reviewer sees the refined type and stops
scrutinizing while the front door still takes garbage). See the *Correct by
Construction* guide, Points 1 and 6.

**Promote `at` to a `Pointer` value object (or `str` subclass, or a tokens
`Struct`).** Rejected (P3, P4). By the guide's proportionality rule (Point 6), a
validation pointer is a low-stakes *diagnostic string*, produced only internally
by the ~21 finding-construction sites (all via the one `_ptr` builder), so it
does not warrant a value object. It also collides with P3: msgspec will not
serialize a custom `Pointer` type without
`enc_hook`/`dec_hook`, and a tokens-carrying `Struct` serializes `at` as
`{"tokens":[...]}` rather than the standard RFC-6901 string that JSON-Pointer
tooling understands. `at: str` built through the single `_ptr` mint is the
proportionate choice and the one that keeps the report interoperable.

**Defer the msgspec tag / serialization (issue #53's original scope).**
Reversed. The tag is a one-line addition per variant that both delivers
serialization and makes the `IssueCode` enum redundant, so pulling it forward
pays for itself.

**Exception-style class hierarchy; an `issue()` decorator with import-time
template validation; an internal templating DSL with a `Formatter`-based path
parser; a tuple-valued `at` assembled in `__post_init__`.** Rejected earlier in
the issue's design pass as over-engineered: our findings are collected values,
not thrown; the message lives in a plain `__str__`; the path stays a `_ptr`
call.

## Consequences

- A `list[Issue]` encodes to a machine-readable report (`code` is the wire
  discriminant) and decodes back to the exact variants (a new capability).
- The 21 variant classes are public (a consumer needs them to `match` /
  `isinstance`), importable from `covjson_msgspec.validation`; the top-level
  package exports the `Issue` union, `Severity`, `validate`, and
  `CovJSONValidationError`, not the individual variants.
- `Issue.code` is now a `str` (the tag), not an enum member. String matching
  (`== "domain.missing-axis"`) and `{i.code}` aggregation are unchanged; the
  typed constant `IssueCode.DOMAIN_MISSING_AXIS` is gone (the variant type
  replaces it).
- Adding a finding kind is a new struct with a pinned `tag`; an exhaustive
  `match` that omits it fails the strict type check (mypy + basedpyright).
- The `range.*` / `coverage.range-*` category overlap that ADR-0003 deferred is
  now resolvable by matching on the variant type (a category grouping is a total
  `match`), should a consumer need it; no stored field is required.
- `severity` stays a field with an `ERROR` default on the base; a future
  warning-severity finding (issue #37) overrides it with a per-variant default,
  rather than passing it at each call site. Because the base sets
  `omit_defaults`, an error finding omits `severity` on the wire, so a report
  reader treats an absent `severity` as `error`.
