# ADR-0020: `validate()` returns a `ValidationReport` value

## Status

Accepted

## Context

`validate()` collected its findings and returned a bare `list[Issue]`. The
[immutability sweep (#119)](../../issues/119) deliberately left that return
untouched and [ADR-0016](0016-readonly-mapping-members.md) recorded it as a
carve-out, because the return *shape* is not a mechanical annotation choice: it
is a one-way, public-API decision about how validation *outcomes* are modeled.
[#157](../../issues/157) is the design pass that settles it. Three shapes were on
the table:

- **A**: an eager `Sequence[Issue]` plus a public lazy
  `iter_issues() -> Iterator[Issue]`.
- **B**: `validate() -> Iterator[Issue]`, a single lazy door.
- **C**: a frozen `ValidationReport` value bundling the issues with the
  verdict.

Three facts shaped the choice.

**"Is it valid?" is a real domain question with one definition, and it was not
modeled.** The library defines *valid* as carrying no error-severity `Issue`.
That definition existed in exactly one place, as an implementation detail inside
`mode="raise"` (`tuple(i for i in issues if i.severity is Severity.ERROR)`), and
nowhere on the public surface. Any caller wanting the verdict re-derived
`any(i.severity is Severity.ERROR for i in ...)` at its own call site.

**The verdict is not a lazy question (the impossibility result).** Issues are
yielded in document order with errors and warnings interleaved, so a valid or
warnings-only document is known valid only once the whole stream is exhausted; a
prefix cannot answer it. The only lazily-deliverable bool is "non-empty," which
is *not* the question (it flags valid-with-warnings documents). Two run-verified
facts pin this: `bool(iter([]))` is `True`, so a bare iterator can never answer
"any issues?" via truthiness; and `msgspec.json.encode` rejects any iterator
(generator and `list_iterator` alike), so an iterator return is no longer a
serializable report. A verdict-bearing return is therefore inherently
materialized: laziness and a real verdict cannot coexist.

**The codebase already names this shape.** [ADR-0007](0007-functional-core-errors-as-values.md)
established that a `*Report` is a frozen value carrier
([`ResolveReport`](../reference/references.md),
[`AssembleReport`](../reference/range.md)) while a `*Result` is a discriminated
union of outcome cases (`TemporalResult = Moment | Unrepresentable | Malformed`).
Validation's outcome bundles many findings plus a verdict; it is a value carrier,
not a sum of cases, so `Report` is the right suffix.

The change is free of compatibility cost: the library is pre-1.0 and unreleased,
so there are no external callers to migrate.

## Decision

`validate()` returns a frozen `ValidationReport` (Option C).

- `issues: tuple[Issue, ...]` holds every finding, in document order (the whole
  payload). Unlike its `*Report` siblings there is no recovered value to carry
  alongside, so the findings *are* the payload.
- `ok: bool`, `errors: tuple[Issue, ...]`, `warnings: tuple[Issue, ...]` are
  computed accessors. `errors` is the single home of the verdict definition
  ("error-severity issue"); `ok` is `not self.errors` and `warnings` is defined
  positively (`severity is Severity.WARNING`), each pinned to a specific severity
  so that adding a third `Severity` lands in neither and can neither flip `ok` nor
  be mislabelled a warning.
- No container protocol: the report is deliberately neither iterable nor sized.
  The report bundles three views (`issues`, `errors`, `warnings`), so an implicit
  `for x in report` or `len(report)` would silently pick one; instead both raise
  `TypeError` and the caller names the view it means.
- `__bool__` is annotated `-> NoReturn` and **raises** `ValueError`. `mode="raise"`
  reuses the verdict: `if report.errors: raise CovJSONValidationError(report.errors)`.

`__bool__` raises rather than being omitted. With no `__len__` to fall back on,
`bool(report)` would default to always-true, so `if validate(doc):` (or
`if not validate(doc):`, meant as "if valid") would silently take the wrong branch
regardless of the verdict. `validate` reads like a verdict, so that mistake is
easy to type. Raising converts it into a loud redirect to `.ok` / `.errors`. The
`ValueError` and the `-> NoReturn` annotation both match what numpy and pandas do
for an ambiguous truth value (both raise `ValueError`, and pandas annotates
`DataFrame.__bool__` as `NoReturn`). `NoReturn` also lifts the guard from runtime
to the type checker: `if report:` and `bool(report)` become checker errors
(unreachable), not merely runtime raises.

The report encodes to JSON as an object, `{"issues": [...]}`, rather than the
bare array a `list[Issue]` produced. This is a deliberate, one-way wire-shape
choice; the tagged-union `code` discriminant still round-trips each finding, now
under the `issues` key.

## Alternatives considered

**A: eager `Sequence[Issue]` plus a public `iter_issues()`.** Rejected. It
leaves the verdict unmodeled: "valid = no error issue" would live in
`mode="raise"` *and* in every verdict-wanting caller, so the one definition has
no home (the one-source-of-truth cost). The lazy `iter_issues()` half is
speculative: its only benefit is short-circuiting, which the impossibility result
shows does not apply to the verdict question, and no consumer needs streaming
(the internal lazy `_issues` pipeline is already there if one ever does).

**B: `validate() -> Iterator[Issue]`.** Rejected, and dominated. A raw iterator
models no verdict, is single-use, has `bool()` always `True` (the trap above with
no `__len__` to even make it meaningful), and is not directly serializable
(`msgspec.json.encode` rejects it), so it forecloses treating the result as a
report. It would also make `validate` the lone lazy-iterator return in an
otherwise value-returning public API.

**A `ValidationResult` name, or mirroring the `{value, failures}` sibling shape.**
Rejected. `*Result` is reserved for a sum of outcome cases, which this is not.
And the report deliberately does *not* copy the `{recovered_value, failures}`
partial-success shape of `ResolveReport` / `AssembleReport`: validation recovers
no value, so the findings stand alone as the payload. It is a `Report` because it
is a frozen value carrier, not because it shares that field layout.

**Exposing `__iter__` / `__len__` for ergonomics.** Rejected. A `ValidationReport`
is not a single collection; it bundles three views (all findings, the errors, the
warnings). An implicit `len(report)` or `for x in report` silently picks one (the
least-wrong "all"), inviting a caller who thinks of validation as "finding errors"
to read `len(report)` as the error count. Named accessors (`.issues`, `.errors`,
`.warnings`) make the cardinality explicit at every call site, and dropping the
protocols turns the ambiguous operation into a loud `TypeError` rather than a
documented gotcha. Their one honest merit was keeping existing call sites
unchanged, which is churn-avoidance, not a design factor pre-1.0. `__bool__` still
raises, because unlike iteration and length its omission is silent (always-true),
not a `TypeError`.

## Consequences

- The verdict has one public home. `report.ok` answers "is it valid?" and
  `mode="raise"` is defined in terms of `report.errors`, so the definition of
  *valid* exists once.
- Call sites reach the findings through a named accessor: `report.issues` (all),
  `report.errors`, or `report.warnings`. Iterating, unpacking, indexing, or
  `len`-ing the result goes through `.issues`; comparisons move from `== []` to
  `report.issues == ()`. A pre-1.0 migration with no external cost, and every call
  site now states which view it means.
- `if report:` is a type error (unreachable, via `NoReturn`) and raises at
  runtime, instead of silently answering the wrong question. A caller must ask
  `report.ok`, `report.errors`, or `report.issues`.
- The JSON form of a report changes from a bare array to an object keyed on
  `issues`. Any consumer decoding a stored report decodes with
  `type=ValidationReport`, not `type=list[Issue]`.
- Revisit if a genuine streaming consumer appears (add a lazy door over the
  existing `_issues` pipeline then, not speculatively), or if a third `Severity`
  is introduced (both `ok` and `warnings` already degrade safely: a new severity
  lands in neither `errors` nor `warnings`, so the only question is whether that
  severity wants its own accessor).
