# ADR-0015: Bridge temporal classification is calendar + container-aware, not routed through `resolve`

## Status

Accepted

## Context

[ADR-0008] made `resolve` the single source of truth for classifying a temporal
*string* into a `Moment | Unrepresentable | Malformed` sum type, and left one
follow-up open in its Consequences: fold `resolve` in as the export bridges'
classify-then-route decider, for "semantic consistency, one classifier of
record, not merely dedup." The three export parse paths look like triplicated
logic ripe for exactly that consolidation:

- `maybe_datetime` (`_bridging.py`), used by the pandas and geo bridges.
- `_parse_times` + `_to_cftime` (`xarray.py`).

Each strips a trailing `Z` and parses temporal strings independently, never
calling `resolve`. Issue #62 set out to route them through it. Two code traces
found the premise does not hold, so the follow-up is resolved here as "will not
route," with a reason [ADR-0008] could not foresee.

CoverageJSON Spec 5.2 (Temporal Reference Systems) is the governing authority.
It states values SHOULD (not MUST) use one of five ISO 8601 lexical forms, the
datetime form being `YYYY-MM-DDThh:mm:ss[.f]Z` "where Z is either 'Z' or a time
scale offset +|-HH:MM." So a numeric offset is a spec form and a naive time
(no designator) is not.

## Decision

**The bridges classify by calendar + container range, not by `resolve`'s string
verdict; `resolve` is not their decider.** [ADR-0008]'s follow-up is resolved as
"will not route."

The load-bearing reason is not a cost tradeoff: **the three paths are different
functions with different codomains, so there is no single classification to
unify.** The apparent triplication is surface similarity ("temporal string in,
time out"), not a shared decision derived in three places.

| Path | Codomain (a sum type) | On `2020-01-01T00:00:00` (naive), then `+102020` |
|------|-----------------------|--------------------------------------------------|
| `resolve` (`temporal.py`) | `Moment` / `Unrepresentable` / `Malformed` | `Malformed`, then `Unrepresentable` (a valid form) |
| `maybe_datetime` (`_bridging.py`) | `DatetimeIndex` / raw strings | parsed to a `Timestamp`, then raw-string fallback |
| `_parse_times` (`xarray.py`) | `datetime64` (`ns`, or `us` outside the ns window) / cftime object array | parsed to `datetime64[ns]`, then `datetime64[us]` |

Because the codomains differ, `resolve` cannot serve as the decider. That single
fact manifests as three concrete blockers:

1. **Calendar-blindness.** `resolve` classifies only Gregorian lexical forms;
   whether a value is a `360_day` versus a standard calendar is domain metadata
   (`TemporalRS.calendar`), not derivable from the string. The bridges route on
   it (xarray to cftime, pandas / geo leave raw strings), and `resolve`'s
   codomain has no cftime arm to express that.
2. **Timezone semantics.** The bridges strip trailing `Z` and treat naive as
   UTC; `resolve` keeps the offset and rejects a naive time as `Malformed`.
   Routing through it would flip currently-accepted naive input to rejected, an
   output regression.
3. **Vectorization.** `pd.to_datetime` and `np.array(..., dtype="datetime64
   [ns]")` parse a whole axis in one C call; `resolve` is per-element Python, so
   using it per value pessimizes a large time axis.

The consistency [ADR-0008] wanted already exists: `validate(check_values=True)`
is the classifier of record, flagging exactly the naive, no-designator strings
the bridges swallow. That disagreement is confined to non-spec input (Spec 5.2's
SHOULD requires a `Z` or `±hh:mm` designator, so a naive time is not a spec
form). Spec-compliant values are accepted by both paths; they differ only in the
tz-awareness of the *result* (the bridges strip `Z` and flatten a `±hh:mm` offset
to naive-UTC), a deliberate representation choice [ADR-0008] already documents,
not a classification disagreement.

## Alternatives considered

**Route classification through `resolve` (the [ADR-0008] follow-up).** Rejected.
The three codomains differ, which manifests as the three blockers above:
calendar-blindness (no cftime arm), a naive-to-`Malformed` output regression,
and per-element vectorization loss. The semantic consistency it promised is
already delivered by `validate(check_values=True)`, so the routing buys nothing
the caller cannot already get, at the cost of output changes and speed.

**Extract just the shared trailing-`Z` normalization into one helper.**
Rejected. The two strip-`Z` comprehensions have different type contracts:
`maybe_datetime` guards `isinstance(value, str)` and passes a non-string through
unchanged, while `_parse_times` guards `None` then `str()`-coerces. A single
helper would either take a parameter or silently change one bridge's handling,
drift risk on a three-line, well-commented one-liner for no behavioral gain.

## Consequences

- The surface duplication is intentional and bounded. Each parse site carries a
  short comment pointing here, so a future reader does not "consolidate" three
  different functions back into one.
- The two `str -> datetime` paths can still disagree on a naive, no-designator
  string: `resolve` / `to_datetime` reject it (`Malformed` / `None`) while the
  bridges parse it leniently. This is documented and pinned by a regression test
  (a contributor who "fixes" a bridge to reject naive input trips it), not a
  bug. `validate(check_values=True)` remains the strict verdict for callers who
  want one.
- The xarray standard-calendar path narrows to `datetime64[ns]` only when the
  whole column fits numpy's ns window (~1677 to 2262), and otherwise keeps the
  wider `datetime64[us]`, which holds any Gregorian year. cftime stays reserved
  for calendars numpy cannot represent (`360_day` and the like), not for standard
  dates that merely exceed the ns window: that is a resolution matter, not a
  calendar one. This resolves #109, where the earlier `suppress(ValueError,
  OverflowError)` guard was a no-op: numpy int64-*wraps* an out-of-range value
  instead of raising (numpy#9956), silently corrupting such a date to a wrong
  in-range one. Preserving a non-ns `datetime64` requires xarray >= 2025.01.2
  (earlier releases coerce it back to ns, raising `OutOfBoundsDatetime`), which
  is the bridge's floor. The routing decision here was never implicated:
  `resolve` would classify such a value as a `Moment` and could not prevent the
  wrap either.
- A `±hh:mm` offset (a Spec 5.2 form) flattens to naive-UTC in both bridges: the
  xarray path folds the offset to naive-UTC before parsing (`_fold_offset`), so
  numpy (which has no timezone type) never sees a zone, and the pandas path parses
  with `utc=True` then `tz_localize(None)`. This extends the `Z` / naive rule to
  the offset case, so the two bridges agree and only `resolve` / `to_datetime`
  keeps an offset tz-aware (a `Moment` at second precision, [ADR-0008]).
  Previously the pandas path returned a tz-aware `Timestamp` for an offset,
  silently disagreeing with xarray's naive-UTC result; a mixed naive+offset axis
  also made pandas raise and fall back to raw strings. Both are pinned by
  regression tests. This resolves #153.
- The xarray fold converts each offset value with `datetime.fromisoformat`, and
  only offset-bearing values: a common all-`Z` / naive axis stays a single
  vectorized parse. It deliberately does not suppress numpy's warning with
  `warnings.catch_warnings()`, whose filter edit is process-global and not
  thread-safe. If a large, mostly-offset axis ever makes the per-value conversion
  a bottleneck, the vectorized alternative is to route the standard-calendar parse
  through pandas' `to_datetime(..., format="ISO8601", utc=True).tz_localize(None)`
  (xarray already depends on pandas), which applies offsets in one C call. That is
  deferred because it would raise the bridge's pandas floor to `pandas>=3.0`: 3.0
  is the first release whose `to_datetime` widens an out-of-`ns` date to
  `datetime64[us]` rather than raising `OutOfBoundsDatetime` (verified: the whole
  2.x line raises, through the final 2.3.3), whereas the current numpy
  `datetime64[us]` construction
  holds any Gregorian year on every supported numpy. The declared floor today is
  `pandas>=2.0` (pandas / geo extras; the xarray bridge gets pandas transitively
  via xarray), so pandas 3.0 would be a real narrowing, gated on a verified
  `lowest-direct` leg.
- Revisit gate: a concrete need for one classifier of record across the bridges
  (a caller-facing guarantee that, say, `to_pandas` and `to_datetime` never
  disagree) would reopen this, most likely via the injected `parse_time=` seam
  (#61) rather than by hard-coding `resolve` as the decider.

[ADR-0008]: 0008-temporal-conversion-result-projection.md
