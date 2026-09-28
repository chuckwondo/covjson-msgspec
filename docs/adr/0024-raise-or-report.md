# ADR-0024: Raise typed exceptions, report only answers and tolerated failures

## Status

Accepted

Supersedes the first of the two reasons [ADR-0008] gives for rejecting a raising
converter. The rest of ADR-0008 stands.

## Context

The design tenet read "Errors are values first, with an opt-in raise confined to
the edge" ([Design tenets], "A functional core with an imperative shell"). Most
of the public API did the opposite: `decode`, every bridge, `isel` / `sel`,
`values_as`, `to_numpy`, `assemble`, and `resolve_references` raised for
failures a correct program can hit on some input. Only `validate`,
`temporal.resolve`, and the per-item failures of a best-effort batch returned
values. [#253] settles which failures raise and which return values across the
whole API, before 0.1.0 makes the return types and the exception hierarchy a
compatibility commitment.

The exceptions themselves were the concrete defect:

- A bridge's `ValueError` meant an unmet precondition (a URL domain), a document
  the bridge cannot convert (a polygon domain into xarray), or a non-conformant
  document (a `shape` that disagrees with the domain). A caller could tell them
  apart only by parsing the message.
- `values_as` raised `msgspec.ValidationError` both for a value that breaks its
  `dataType` and for a valid integer too large for a Python `float`, so a range
  that `validate(check_values=True)` accepts raised the non-conformance error.
- `decode` raised bare msgspec exceptions and documented none of them.

A first design pass returned every document-dependent failure as a value, in a
bare union such as `Coverage | LabelNotFound | ...`, with a free `unwrap` as the
opt-in raise. Measuring it decided against it ("Values everywhere, with a free
`unwrap`" under Alternatives considered records why). The deeper cost is that
Python has no counterpart to Rust's `?` operator, so propagating a returned
failure takes three lines per call, while a raised exception reaches the caller
for free:

```python
cov = decode_coverage(raw)
if isinstance(cov, Failure):
    raise cov.exception()
```

## Decision

**Six rules decide every failure in the public API.** Only rules 1 and 2 return
a failure.

1. **Report when checking or classifying is the job.** A function whose job is
   to check or classify its input returns every outcome as a value, the bad ones
   included: `validate`, `temporal.resolve`, `to_datetime`, and
   `is_coverage_json_media_type`. Extracting and converting are not classifying,
   so `sel`, `isel`, and the bridges fall under rule 3.
2. **Report per-item failures only where the caller chose to tolerate them.** A
   call that takes a `strategy` returns the partial result with the tolerated
   failures beside it (`AssembleReport`, `ResolveReport`). A halting strategy
   raises `FetchError`.
3. **Otherwise raise.** No public function returns a union of a result and a
   failure, and none offers an opt-in value mode.
4. **The trigger decides the exception.** Holding the code fixed, if a different
   document could make the call succeed, the failure depends on the document. It
   raises a `CovJSONError` subclass that also subclasses the built-in a caller
   expects, carrying the failure's data as typed attributes. For a conversion,
   the object converted counts as the document. A failure no document could
   avoid (a wrong argument, protocol misuse) raises the plain built-in, a
   missing extra raises `ModuleNotFoundError`, and an unanticipated exception
   propagates unchanged.
5. **A report can offer a raise, never the reverse.** A raise is a strategy only
   when deciding early saves work, as a halting batch skips the fetches it has
   not made. Otherwise it is a method on the report:
   `ValidationReport.raise_on_errors()` returns the report, or raises
   `CovJSONValidationError` when `report.errors` is non-empty, and `validate`
   loses `mode=`.
6. **Advice about a successful result is a warning**, e.g., the Grid
   `UserWarning` from the geo bridges.

No function changes its return type. What changes is which exception each
failure raises, and the method that replaces `validate`'s `mode=`.

**The exception hierarchy.** A class stands for one way of handling a failure,
not one kind of data, because a caller catches by what it will do next. Kinds a
caller handles alike share a class and differ by `.code`, a member of the
class's own `StrEnum`, with per-kind detail in the message. A different
built-in, or payload a caller needs as data, earns its own class. Every name
ends in `Error`, following [PEP 8].

| Class | Also a | Carries |
| --- | --- | --- |
| `CovJSONDecodeError` | `ValueError` | `.code`: malformed JSON, schema mismatch, broken invariant |
| `UnsupportedMediaTypeError` | `ValueError` | `.content_type` |
| `CovJSONValidationError` | `ValueError` | `.issues`, at least one of them an error |
| `UnresolvedReferenceError` | `ValueError` | `.at` |
| `NotConvertibleError` | `ValueError` | `.code` |
| `ValueOverflowError` | `OverflowError` | `.index`, `.value`, `.target` |
| `LabelNotFoundError` | `KeyError` | `.axis`, `.label` |
| `PositionOutOfRangeError` | `IndexError` | `.axis`, `.index`, `.length` |
| `TileSetOutOfRangeError` | `IndexError` | `.index`, `.length` |
| `AxisNotFoundError` | `ValueError` | `.axis`, `.axes` |
| `NonNumericAxisError` | `TypeError` | `.axis` |
| `EmptySelectionError` | `KeyError` and `IndexError` | `.axis` |
| `NotSupportedError` | `NotImplementedError` | `.code` |
| `FetchError` | none | `.failures`, at least one |
| `ReferencedDocumentError` | `ValueError` | the message |

- **One source for non-conformance.** A conversion raises
  `CovJSONValidationError` only with issues from the checkers `validate` runs,
  so a document that `validate(check_values=True)` passes never raises it from a
  conversion. A conversion-side conformance check lives in code that `validate`
  shares, as the tiling rules already do.
- **`NotConvertibleError`** covers every conformant object a conversion cannot
  hold, in either direction: a polygon domain into xarray or pandas, a
  curvilinear grid out of xarray, a time value `to_xarray` cannot parse, and
  tiles whose URLs collide. The last is conformant because Section 6.3
  (`covjson/specification@2061005`) states, with MUST, only that "The URI
  template MUST contain a variable for each axis name whose corresponding
  element in `"tileShape"` is not null" and that "Each URI that can be generated
  from the URI template MUST resolve to an NdArray CoverageJSON document". It
  states no requirement that tiles get distinct URIs.
- **`UnresolvedReferenceError`** covers a URL domain and a non-inline range,
  because both recover the same way: fetch first, with `resolve_references` or
  `assemble`.
- **`EmptySelectionError` subclasses both `KeyError` and `IndexError`**, because
  an empty `sel` label slice raised `KeyError` and an empty `isel` slice raised
  `IndexError`. numpy's `AxisError(ValueError, IndexError)` is the precedent.
- **`NotSupportedError` is general**, so when #16 adds composite subsetting it
  retires a code and deletes no exported class.
- **`CovJSONError` defines `__str__`**, because `str(KeyError("msg"))` wraps the
  message in quotes. The base's method comes ahead of `KeyError`'s in each
  subclass's method resolution order.
- **Neither multi-item exception can be empty.** `CovJSONValidationError`
  requires an error-severity issue and `FetchError` a failure. The tenet's
  constant-time limit on `__post_init__` protects decoding speed, and exceptions
  are never decoded.
- **`ReferencedDocumentError` moves under `CovJSONError`**, keeping
  `ValueError`, because its docstring defines it as a failure in the document.
  Its role as the fetch path's classifier is left to the fetch seam's own
  design.
- **`FetchError` subclasses no built-in**, because a halt mixes network and
  document failures.

**The tenet.** "A functional core with an imperative shell" in the [Design
tenets] now states rules 1 to 3: a failure is a value where checking or
classifying is the job or where the caller chose to tolerate it, and everywhere
else the public entry point raises a typed exception carrying the same data.

## Alternatives considered

**Values everywhere, with a free `unwrap`.** Rejected.
`unwrap(result: T | Failure) -> T` does not type exactly. Under pyright 1.1.414
(the checker Pylance runs) and basedpyright 1.40.0 it leaks a failure type into
the result for `tuple`, `dict`, `ndarray`, `NdArray`, `GeoDataFrame`, and
`ResolveReport`, so `unwrap(values_as(float))[0]` is a type error. mypy 2.3.1
widens `decode`'s five-type result to `CovJSONStruct`. Both checkers reject an
`unwrap` overloaded per success type as overlapping. Even where it types
exactly, nothing makes an untyped caller handle the value. A failure is truthy,
and calling a method on one raises `AttributeError` one call late, without the
original message.

**Values everywhere, with `mode="raise"` overloads.** Rejected. It types exactly
in both checkers, but about 20 functions and their method forms would each gain
a parameter, two overloads, and two documented return types.

**Values everywhere, with an `Ok` / `Err` box.** Rejected. `.unwrap()` types
exactly, but a flat `match` over `decode`'s five success types wrapped in `Ok`
is not exhaustive to either checker, and `TemporalResult` already models its
outcomes as a bare union.

**Values only at decode.** Rejected. It needs a public `Failure` base, union
returns, and a runtime guard so that `encode(decode(raw))` cannot write a
failure out as JSON, all for four functions. Decode is also not where a document
becomes trusted, because decoding is permissive ([ADR-0002]). `validate`
establishes conformance, and it returns values under this decision too.

**One class per failure kind.** Rejected. The first pass had about 22 classes,
each with typed per-kind fields. A caller catches by what it will do, and
merging classes later breaks every `except` that references one, while splitting
a class out later breaks nothing. [PEP 3151] chose its `OSError` subclasses the
same way, from "a survey of existing exception matching practices".

**`.reason` or `.kind` for the case attribute.** Rejected. The standard library
uses `.reason` for readable text (`UnicodeDecodeError.reason`, and
`HTTPError.reason` beside `HTTPError.code`), and `FetchFailure.kind` already
means recoverability.

**Public predicates for structural preconditions.** Deferred. A predicate cannot
rule out a value-level failure, like `1.5` in an `"integer"` range, without
converting, and adding one later is additive. When one is added, it is a rule 1
classifier that shares one private check with the function that raises.

**Keeping `validate(mode="raise")`, alone or beside the method.** Rejected. The
raise branch reads only `report.errors`, a method also works on a report decoded
from storage, and keeping both gives one decision two forms.

## Consequences

- Type checkers no longer see most failures. They still flag an unhandled
  `to_datetime` `None` and a `match` over `temporal.resolve` that omits a case,
  but not an uncaught raise, and not a report's unread failures. A test enforces
  rule 4 instead: decoded documents, including ones `validate` passes with only
  warnings, run through every public conversion, and any exception other than a
  `CovJSONError`, a misuse built-in, or `ModuleNotFoundError` fails it. Two such
  documents surfaced during the design: a malformed time value that makes
  `to_xarray` raise numpy's bare `ValueError`, and colliding tile URLs that make
  `assemble` raise a plain `ValueError`.
- `except CovJSONError` catches exactly the failures some other document would
  avoid, which includes a hard-coded label typo passed to `sel`, and never a
  misuse.
- `CovJSONDecodeError` carries no location, because msgspec exposes a decode
  error's JSON path only in its message text. Adding `.at` once msgspec exposes
  it is additive.
- ADR-0008's first reason, that raising on a spec-legal value fights the
  functional-core tenet, no longer holds, because `ValueOverflowError` raises on
  a value `validate` accepts. Its decision stands on its second reason, that a
  raising converter "does not compose over an axis (one paleo value would abort
  a bulk `map`)".
- ADR-0002, ADR-0004, ADR-0007, ADR-0011, ADR-0020, and ADR-0023 describe
  `validate(mode="raise")`, `msgspec.ValidationError`, or `FetchError`'s base.
  Each is swept with the implementation, because none of their decisions
  changes.
- The hierarchy is a one-way door at 0.1.0. Revisit if a caller needs a merged
  kind's detail as data (split it into a subclass, which is additive).

[#253]: ../../issues/253
[ADR-0002]: 0002-opt-in-tiered-validation.md
[ADR-0008]: 0008-temporal-conversion-result-projection.md
[Design tenets]: ../design/tenets.md
[PEP 3151]: https://peps.python.org/pep-3151/
[PEP 8]: https://peps.python.org/pep-0008/#exception-names
