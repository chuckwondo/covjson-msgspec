# ADR-0007: Functional core, imperative shell; fetch errors as values

## Status

Accepted

## Context

Turning a CoverageJSON document's deferred bulk data into inline values means
fetching other documents: the tiles of a `TiledNdArray` (via
`TiledNdArray.assemble`), and the domain/range references of a coverage (via
`resolve_references`, issue #31). Both were all-or-nothing: the first failed
fetch aborted the whole batch, propagating the fetcher's exception unchanged.
That is the right default, but a tile set or a large `CoverageCollection` fans
out over dozens or hundreds of independent fetches, and callers reasonably want
the documents that loaded with the failures reported, plus finer control (stop
on an unrecoverable decode error, but tolerate transient ones).

This is the fetch-side instance of a design tenet the project already commits to
(CLAUDE.md, "a functional core with an imperative shell"): errors are values
first (a rich domain report) with an opt-in raise bridge, not exceptions
threaded through the core. ADR-0002 established opt-in tiered `validate()`, and
[ADR-0006](0006-validation-findings-sum-type.md) modelled validation findings as
`Issue` values with a `CovJSONValidationError` raise bridge. Best-effort fetching
is the same instinct applied to control flow, so its cross-cutting shape is worth
recording alongside that sibling: which pieces are values, where the raise lives,
and where the design boundary with concurrency sits.

## Decision

Model a fetch failure as a value and the batch's response to failures as a pure
reducer, in a domain-independent module `_best_effort.py`:

- **`FetchFailure`** (a frozen `msgspec.Struct`): the URL, a `FailureKind`, and a
  message. It is the shared base; the tile-assembly consumer subclasses it as
  `TileFailure` (adding the tile's `offsets`) and reference resolution as
  `ReferenceFailure`. This is *structural reuse* (a base carrying common fields
  so generic code programs against it), not a discriminated union: a given
  consumer only ever builds its own subtype, and they are never matched
  exhaustively in one place.
- **`FailureStrategy`** = `Callable[[tuple[F, ...], F], Verdict]`: a pure reducer
  that, given the failures collected so far and a new one, returns a `Verdict`
  (`COLLECT` or `HALT`). Canned strategies (`collect_all`,
  `halt_on_unrecoverable`, `stop_after`) cover the common policies; a caller can
  supply any pure function of the shape.
- **`collect` / `collect_async`**: the imperative shell. They fetch each item,
  turn a failure into a `FetchFailure`, fold the strategy over the failures, and
  on `HALT` raise a `FetchError` carrying the failures collected so far, chained
  from the original exception (its `__cause__`). All the
  fold/attempt/classification plumbing is file-local; consumers depend only on
  these two orchestration entry points plus the vocabulary.
- **`fail_fast`** is the default and an *ordinary* `FailureStrategy` that returns
  `HALT` on the first failure. It is not a distinguished sentinel: it folds like
  any other strategy and raises `FetchError` (chaining the underlying exception).

`assemble` / `assemble_async` gain a keyword-only `strategy=` (defaulting to
`fail_fast`) and **always return `AssembleResult`** (`result.array` plus
`result.failures`). Every failure path is uniform: the default raises a
`FetchError` on the first failed tile; a collecting strategy returns the array
with `None` holes and the failures reported.

## Alternatives considered

**Exceptions threaded through the core.** Rejected, as in ADR-0006. Threading a
partial result and a list of failures out through raised exceptions forces every
intermediate layer to catch, inspect, and re-raise; the report is a value, so it
should be returned as one, with the single raise confined to the shell.

**A mutable error accumulator passed down and appended to.** Rejected, as in
ADR-0006. It couples the checker to a shared side-channel and fights the
functional-core grain; the pure reducer threads no accumulator (the shell owns
the collected failures) and each strategy is trivially pure and testable in
isolation.

**Model `FailureKind` as a sum type (a struct per kind), mirroring `Issue`.**
Rejected. ADR-0006's rule is that a closed union of structs is warranted only
when the variants carry *distinct typed payloads* to destructure exhaustively.
`TRANSIENT` and `UNRECOVERABLE` are labels over one common shape (every kind sits
on the same `FetchFailure` fields), so it is the `Severity` case, which ADR-0006
kept an enum. The promote-to-union trigger is a kind gaining kind-specific data
and handling.

**A generic threaded reducer state `S`** (`Callable[[S, F], tuple[S, Verdict]]`).
Rejected as unused generality: every canned strategy's only state is "the
failures so far," which the shell already accumulates, and a generic `S` leaves
open who supplies its initial value. The signature takes the accumulated failures
directly.

**Attach the partial artifact to `FetchError` on `HALT`.** Rejected. It would
make `FetchError` domain-specific (an `NdArray` here, a `Coverage` in #31) and
fork the shared type. Instead, the raise-vs-return-a-partial decision *is* the
strategy choice (below), so a halting strategy carries only the failures.

**Distinguish `fail_fast` as a sentinel with a return-type split** (`fail_fast`
-> `NdArray`, a collecting strategy -> `AssembleResult`, via `@overload`, with
`fail_fast` re-raising the fetcher's original exception unchanged). Rejected. Its
one merit is real -- `fail_fast` can never produce a partial result, so a bare
`NdArray` (no vestigial empty `.failures`) makes an illegal state
unrepresentable. But it is outweighed: the sentinel is "a strategy that is not a
`FailureStrategy`," the overload/union return complicates the type surface, and
every bit of that machinery would duplicate into #31's `resolve_references`. The
uniform design keeps `fail_fast` an ordinary strategy and always returns the
result type; the raw-exception information is preserved via `FetchError.__cause__`
(chaining), so only the *caught type* changes, not the information. Since nothing
is released yet, there is no compatibility cost to the uniform default.

## Consequences

- **`fail_fast` is the default and an ordinary strategy**, so every path is
  uniform: `assemble` / `assemble_async` always return `AssembleResult`, and the
  default raises a `FetchError` on the first failure (chained from the fetcher's
  own exception, or a `ReferencedDocumentError` for an undecodable document, via
  `__cause__`). Best-effort is opt-in by passing a collecting strategy. The bare
  `NdArray` return is reached through `result.array`.
- **The raise-vs-return-a-partial choice is the strategy choice.** `HALT` means
  "this batch is poisoned, abort," so `FetchError` carries the failures and the
  partial artifact is discarded; a caller who wants the partial-with-holes uses
  `collect_all`, which never halts and returns an `AssembleResult`.
- **Sync folds lazily; async is eager.** `collect` fetches one item at a time, so
  a halting strategy stops fetching at the halt point. `collect_async` launches
  all fetches at once (via `asyncio.gather`) for concurrency, so it necessarily
  fetches the whole batch before folding. True early-abort of in-flight
  concurrent fetches is cooperative cancellation of the gather: the injected
  scheduler seam (issue #32), out of scope here. `THROTTLED` and any richer
  strategy state land there too; `collect_async` re-raises a child's
  `asyncio.CancelledError` rather than collecting it, so cancellation is never
  swallowed.
- **The enum-vs-sum-type rule** (distinct payload -> union like `Issue`; label
  over a common shape -> enum like `Severity` / `FailureKind`) is now applied
  twice, and is a reusable guideline for future finding/failure taxonomies.
- **Low coupling by a domain-independence boundary.** `_best_effort.py` imports no
  `NdArray` or `Coverage` and knows nothing about tiles; consumers depend on the
  two `collect` seams plus the vocabulary, never on the fold/outcome plumbing.
  This ADR therefore also covers the synchronous injected-fetcher seam as applied
  to reference resolution (#31), which reuses `collect` / `collect_async`
  verbatim; #31 needs no separate ADR. The async/concurrency layer on top of the
  seam is explicitly *not* covered here (it rides with #32 if picked up).
- A `ReferencedDocumentError` (a `ValueError` subclass) is raised at the shared
  fetch/decode seam so a *decode* failure is distinguishable from a *fetch*
  failure a caller's own fetcher raises; this is non-breaking for existing
  `except ValueError` handlers.
- **Reference resolution fetches per site, not per unique URL.** Applying the seam
  to `resolve_references` / `_async` (#31), each reference *site* (a coverage's
  domain, or one range, tagged with its `coverage_index`) is one fetch attempt,
  rather than collecting the URLs into a set and fetching each once. This pins
  every `ReferenceFailure` to an exact `(coverage_index, slot)` and lets a
  strategy count attempts uniformly (one site, one attempt -- the same shape as
  one tile, one attempt in assembly). The cost is that a URL shared across
  collection members is fetched once per member; that is sound because the
  injected fetcher owns caching -- a caller who shares, say, one domain document
  across every member wraps the fetcher in a cache to fetch it once, the same
  dependency-injection-at-the-edges tenet that keeps the core I/O-free.
  Resolution returns a `ResolveResult` (`result.value` plus `result.failures`),
  the reference-side twin of `AssembleResult`.
