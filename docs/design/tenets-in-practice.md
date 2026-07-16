# Tenets in practice

The [design tenets](tenets.md) state the library's recurring principles in the
abstract. This page shows each one at work: the concrete decisions it drove, the
mechanism in the code, the [ADR](../adr/README.md) that records the full rationale,
and, where a decision tracks a rule of the format, the section of the
[CoverageJSON specification][spec] it conforms to. It is illustrative, not
exhaustive; the tenets page is the canonical statement of the principle, and the
ADRs are the detailed record.

## Dependency injection at the edges

Reference resolution never calls an HTTP client. It takes an injected `Fetch` (or
`AsyncFetch`) callable, the seam defined in `_fetch.py`:

```python
Fetch = Callable[[str], bytes]
AsyncFetch = Callable[[str], Awaitable[bytes]]
```

`Coverage.resolve_references(fetch)` walks the document, hands each referenced URL
to `fetch`, and rebuilds the coverage with the results (a coverage's
[`domain` member MAY be a URL string][spec-coverage] instead of an inline object).
Because the network call is the injected seam, the *same* resolution logic backs
both the sync `resolve_references(fetch)` and the async
`resolve_references_async(async_fetch)`: the library commits to neither an HTTP
client nor a concurrency model, and ships no HTTP dependency of its own.

The same rule shows up at two more edges:

- **Serving over HTTP stays framework-agnostic by trading in values, not framework
  objects.** `encode_response(coverage)` returns a `(body, content_type)` pair,
  labelled with CoverageJSON's [media type][spec-media]
  (`application/prs.coverage+json`), and `decode_response(body, content_type)` takes
  one back, so any framework wires them in. The one framework the library adapts,
  FastAPI, lives entirely behind the `[fastapi]` extra: `CovJSONResponse` imports
  `fastapi` locally, never at module scope.
- **The export bridges import their heavy dependency inside the function body.**
  `to_xarray`, `to_pandas`, and `to_geopandas` reach for numpy, xarray, pandas, or
  geopandas only when called, and re-raise a `ModuleNotFoundError` with a precise
  hint (`install covjson-msgspec[xarray]`) if it is missing. So
  `import covjson_msgspec` costs only msgspec and langcodes, and you pay for the
  scientific stack only where you use it.

## A functional core with an imperative shell

`validate()` yields a lazy stream of typed issue values and never raises on its
own. The issues are not strings: each is a frozen struct in a closed
[`Issue` sum type](../adr/0006-validation-findings-sum-type.md) tagged by rule
(`ndarray.value-count`, `range.value-type-mismatch`, `i18n.invalid-language-tag`,
and so on), and carries an `at` field locating the fault as a
[JSON Pointer][rfc6901]. A consumer can `match` on the variant to read its typed
payload, or read the string `code` for stringly work (logging, counting); and
because the discriminant is a field, a whole report round-trips through JSON. The
caller decides at the edge what to do with the stream: iterate and report, or ask
`validate()` to raise the first error via `mode="raise"`.

Best-effort reference resolution takes the pattern further: both the fetcher *and*
the policy for how a batch reacts to failures are injected values.
`resolve_references(fetch, strategy=collect_all)` returns a `ResolveReport` carrying
the resolved coverage alongside a tuple of typed `FetchFailure` records, and never
raises. A `FailureStrategy` is a pure reducer, `(failures_so_far, new_failure) ->
Verdict`, and the library ships `fail_fast` (the default), `collect_all`,
`stop_after(n)`, and `halt_on_unrecoverable`. The effectful driver is the only
imperative shell; the strategy, the failures, and the report are all data
([ADR-0007](../adr/0007-functional-core-errors-as-values.md)). A collection where
three of five references resolve yields those three in the report alongside two
failure records, and the caller chose up front whether the fourth failure should
stop the batch.

## Immutable by default, statically enforced

A `CovJSONStruct` is `frozen=True`, which stops an attribute being rebound. But
`frozen` says nothing about the *contents* of a container a field holds, so a
`dict` member leaked mutation: `coverage.ranges["x"] = ...` altered a
supposedly-frozen coverage with no error. Every mapping member is therefore
typed as a read-only `Mapping`
([ADR-0016](../adr/0016-readonly-mapping-members.md)): msgspec still builds a
runtime `dict`, so decode, encode, and equality are byte-for-byte unchanged, but
`coverage.ranges["x"] = ...`, `domain.axes.pop(...)`, and
`parameter.label["fr"] = ...` are now type errors. Sequence members were already
immutable at runtime as `tuple` (`NdArray.values` / `shape` / `axisNames`,
`Domain.referencing`, `Coverage.parameterGroups`), and the constant lookup
tables are `frozenset`.

The rule is one principle across every mutable builtin, statically enforced
rather than trusted:

| Mutable | Immutable member / value | Read-only parameter |
| --- | --- | --- |
| `list` | `tuple` | `Sequence` / `Iterable` |
| `dict` | `Mapping` (a `frozendict` runtime is deferred to [#117](https://github.com/chuckwondo/covjson-msgspec/issues/117)) | `Mapping` |
| `set` | `frozenset` | `AbstractSet` |
| `bytes` | already immutable | `bytes` |

Two escape hatches keep the rule honest. A mutable builtin is fine as a *local
accumulator* inside a function, where nothing outside ever sees it; and a
*return handed to external plumbing* stays concrete because the consumer
requires it (the FastAPI `openapi()` hook merges its dict in place, `xarray`'s
`attrs=` wants a real dict, GeoJSON features are dicts). The distinction is
consumer-driven, not reflexive: a builder that returns a read-only *domain* type
is kept, so `i18n("Air temperature", fr="Température")` hands back an `I18n` (a
`Mapping`) that is immutable from the moment it is built through every field that
stores it.

## Opt-in tiered validation, not `__post_init__`

Two rules land at construction, in `__post_init__`, because a violation leaves the
object meaningless in isolation: an `Axis` that supplies neither or both of
`values` and the `start`/`stop`/`num` triple ([exactly one form is
required][spec-axis]), and a categorical `ObservedProperty` that omits its
[`categories`][spec-parameter]. Both are local and O(1).

Everything cross-cutting or data-scanning waits for `validate()`, which is tiered
along two axes, cost and severity:

- **Structural checks always run** and are cheap: a
  [missing required axis][spec-domain], a
  [`shape` whose rank disagrees with `axisNames`][spec-ndarray], a
  [coverage missing its `parameters`][spec-coverage].
- **The value scan is opt-in** behind `check_values=True`, because it is O(number
  of values): the
  [element type of every datum against the range's `dataType`][spec-ndarray],
  category codes, and [axis monotonicity][spec-axis] (a MUST when the reference
  system defines a natural ordering). Skipping it lets a slightly-off array still
  decode and still pass the structural checks.
- **Severity, not just presence, is graded.** Each finding is an error or a
  warning. `mode="raise"` aborts only on an error; a SHOULD-level lapse such as a
  temporal value outside the [recommended ISO 8601 lexical forms][spec-temporal] is
  reported as a warning that a strict caller can act on and a lenient one can
  ignore.

Even a single validation rule can be an injected seam: the axis-monotonicity check
takes an `axis_order_checker`, consulted only under `check_values`, so a caller can
supply their own ordering policy
([ADR-0011](../adr/0011-axis-ordering-checker-seam.md)).

## A byte-faithful model, lossy only in bridges

Decode keeps numbers at their JSON type and precision: a value written `5` stays an
`int`, a value written `5.0` stays a `float`, with no coercion on the way in.
Temporal instants stay raw strings for the same reason. The spec's
[Temporal Reference Systems][spec-temporal] encode a calendar value as an ISO 8601
string, and many valid instants cannot be represented as a `datetime` at all (a
year `0000`, a non-Gregorian calendar), so parsing at decode would reject faithful
data. On the way back out, encode omits `UNSET` and defaulted members rather than
inventing `null`s, so a decode / encode round trip reproduces the spec-defined
input.

The lossy conversions are named and isolated. `resolve()` projects an instant to a
`datetime` only on request, returning a `TemporalResult` sum type that *reports* the
instants it could not convert instead of raising
([ADR-0008](../adr/0008-temporal-conversion-result-projection.md)). `to_xarray`
materializes a temporal axis to a concrete numpy `datetime64`, which is exactly the
information-losing step, and it happens in the bridge, not the core: a year-`0000`
instant survives the round trip above but cannot survive `to_xarray`. The one loss
on decode itself is a [custom member][spec-custom] (an extension key the spec
permits on any object), which decode drops
([ADR-0012](../adr/0012-custom-members-dropped-on-decode.md)); to relay a document
with its extensions intact, forward its raw bytes instead of decoding and
re-encoding.

## Typed projection over a faithful core

`NdArray` keeps its element type in a `data_type` field on one non-generic class,
not an `NdArray[float]` generic or an `NdArrayFloat` subclass. The spec's three
[`dataType` values][spec-ndarray] (`float`, `integer`, `string`) are data, not
distinct types. When a caller knows the `dataType` and wants statically-typed
elements, they ask for a projection:

```python
arr.values_as(float)   # -> tuple[float | None, ...]
```

`values_as` narrows the view without changing what was stored, and raises fail-fast
on a value that does not match (where `validate(check_values=True)` instead reports
the same mismatch); `to_numpy` is its `[numpy]`-backed sibling over the same
faithful values. `Axis` makes the trade on the write side: the spec lets
[one axis object take three shapes][spec-axis] (listed, regular, composite), so
storage is one permissive struct and `Axis.listed`, `Axis.regular`, `Axis.tuple_`,
and `Axis.polygon` are the typed builders that construct a valid one
([ADR-0004](../adr/0004-ndarray-single-non-generic-class.md)).

The tenet offers "an accessor or a builder", and that choice is deliberate per
instance rather than a shape each type should converge on. `ReferenceSystem` adds
a third form, a whole-struct `refine()`
([ADR-0017](../adr/0017-reference-systems-permissive-core-projection.md)) --
because a custom `type` must load, so its core *cannot* enforce that a
`TemporalRS` has a `calendar`, and `refine()` is the only place that guarantee
exists. `Axis` gets no `refine()` precisely because its core already enforces its
own invariants at construction, leaving a projection nothing to recover
([ADR-0018](../adr/0018-typed-projection-scope.md)).

The same shape recurs beyond ranges. A `Coverage.domain` is typed `Domain | str`,
mirroring the spec's allowance of
[an inline object or a URL that references one][spec-coverage]: a URL stays a string
in storage, and `resolve_references()` is the projection that fetches and inlines
it, returning a coverage whose `domain` is a resolved `Domain` (through the injected
fetcher above). Faithful union in storage, precise value when you ask.

[spec]: https://github.com/covjson/specification/blob/master/spec.md
[spec-media]: https://github.com/covjson/specification/blob/master/spec.md#10-media-type-and-file-extension
[spec-coverage]: https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects
[spec-domain]: https://github.com/covjson/specification/blob/master/spec.md#61-domain-objects
[spec-axis]: https://github.com/covjson/specification/blob/master/spec.md#611-axis-objects
[spec-ndarray]: https://github.com/covjson/specification/blob/master/spec.md#62-ndarray-objects
[spec-parameter]: https://github.com/covjson/specification/blob/master/spec.md#3-parameter-objects
[spec-temporal]: https://github.com/covjson/specification/blob/master/spec.md#52-temporal-reference-systems
[spec-custom]: https://github.com/covjson/specification/blob/master/spec.md#71-custom-members
[rfc6901]: https://www.rfc-editor.org/rfc/rfc6901
