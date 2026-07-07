# ADR-0008: Temporal conversion as a faithful Result projection

## Status

Accepted

## Context

The model stores temporal coordinate values as raw ISO 8601 strings and never
parses them, so decode is byte-faithful ([ADR-0002] establishes that decode
stays permissive). That fidelity left two gaps:

1. **No stdlib path to a `datetime`.** The only way to turn a temporal string
   into a real `datetime` was through an export bridge (pandas, or xarray +
   numpy + cftime), whose per-element parsing is triplicated across
   `maybe_datetime`, `_parse_times`, and `_to_cftime`. A caller who just wanted
   a `datetime` had to install a bridge extra and inherit that bridge's range
   and timezone semantics.
2. **No validation of temporal lexical forms.** A time axis accepted any
   string: `"2010-13-99"` passed silently.

The cautionary counter-example is covjson-pydantic, which types `t` values as
`datetime` at the model boundary. [KNMI/covjson-pydantic#34] documents the cost:
reduced-precision values are corrupted or rejected, and out-of-range years
cannot round-trip. Storing the string is the right foundation; the open
questions were how to *project* it on demand.

Three questions had to be settled:

- **How to represent a conversion outcome.** The spec's five Gregorian forms
  (Spec 5.2, Temporal Reference Systems) include values a stdlib `datetime`
  cannot hold: expanded years (`±XYYYY`), year `0000`, and leap seconds
  (`":60"`). An outcome type has to distinguish "representable," "valid but
  unrepresentable," and "malformed" without conflating the last two, and
  without losing the reason.
- **Whether to fail as a value or an exception**, given the functional-core
  tenet ([ADR-0007]): errors are values first, with raising confined to an
  opt-in shell.
- **Where the validation lives**: decode, `__post_init__`, or `validate()`.

## Decision

**Conversion returns a closed sum type; it never raises.** A new stdlib-only
leaf module `temporal.py` defines the result union (matched by concrete type,
the same idiom as the `Issue` union in [ADR-0006]):

```python
TemporalResult = Moment | Unrepresentable | Malformed
```

- `Moment(when, precision)`: a valid form representable as a `datetime`,
  filled to the start of the period for the reduced forms, with the detected
  `Precision` recorded. It is timezone-aware exactly when it carries a `Z` /
  offset (second precision); the date and reduced forms are naive.
- `Unrepresentable(value)`: a valid form a stdlib `datetime` cannot hold (a
  year outside `1..9999`, or a leap second), keeping the raw string.
- `Malformed(value)`: a string matching none of the five forms.

`resolve(value)` produces the union; `to_datetime(value) -> datetime | None` is
the thin convenience for the common case (`Moment.when`, else `None`). The
`T`-time form is **strict**: it requires a `Z` or `±hh:mm` offset, so a naive
time is `Malformed`.

**Validation is opt-in and reuses `resolve`.** A `temporal.lexical-form`
finding (*warning* severity: Spec 5.2 makes the lexical forms a SHOULD, and
ADR-0002 maps a SHOULD violation to a warning) is added to `validate()`, gated
behind `check_values=True`. It flags only `Malformed` values on
standard-calendar temporal axes; `Unrepresentable` and `Moment` are legal
forms and pass. `resolve` is the single source of truth shared by `to_datetime`
and the validator.

**Public surface stays minimal.** The top-level package exports only
`to_datetime`; `resolve`, the three arms, and `Precision` are imported from
`covjson_msgspec.temporal`, mirroring how the `Issue` variants are reached from
`covjson_msgspec.validation`.

## Alternatives considered

**`datetime` as the stored/decoded type (covjson-pydantic).** Rejected. It
corrupts reduced precision, rejects out-of-range years, and breaks byte-
faithfulness ([KNMI/covjson-pydantic#34]). We can implement the convenience
*correctly* precisely because the stored type is a string.

**`to_datetime -> datetime | None` as the primary (an option type).** Rejected.
A bare `None` conflates "valid but unrepresentable" with "malformed" and
discards the reason and the detected precision, and it is the un-idiomatic
choice in a codebase that models findings as a sum type ([ADR-0006],
[ADR-0007]). The option type survives only as the thin `to_datetime`
convenience built over the union.

**A raising converter.** Rejected. Raising on a spec-legal document value
fights the functional-core tenet ([ADR-0007]) and does not compose over an axis
(one paleo value would abort a bulk `map`). Raising stays in
`validate(mode="raise")`, the sanctioned shell.

**Validating in `__post_init__`.** Rejected on tier discipline ([ADR-0002]).
The check is non-local (temporal-ness is defined by the domain's `referencing`,
which an `Axis` constructor cannot see), it is O(number of values)
data-scanning, and rejecting at construction would break decode permissiveness.

**An injected `parse_time=` seam on the bridges now.** Deferred. The win would
be *openness*, not dedup (the bridges' container assembly stays specific to
`DatetimeIndex` / `datetime64` / cftime), a per-element injected callable
pessimizes their vectorized parsing, and there is one implementation today.
`resolve` is shaped as a bare `str -> TemporalResult` callable so it can become
that default without an interface change.

## Consequences

- `to_datetime` needs no optional extra. Full-precision in-range values yield a
  timezone-aware `datetime` (matching covjson-pydantic's one useful
  capability); reduced-precision and out-of-range values degrade gracefully
  rather than corrupting or rejecting, exceeding it.
- `Moment.when` is timezone-aware iff it is second precision; a naive
  (date/reduced) `Moment` and an aware (second) one are not directly
  comparable, which is honest to the data (forcing naive forms to UTC would
  fabricate zone information).
- Decode stays permissive and byte-faithful; the lexical-form check is opt-in
  (`check_values=True`) and adds the `temporal.lexical-form` issue code.
- The export bridges keep their own parsing for now; `resolve` can later serve
  as their classify-then-route decider (tracked as a follow-up), the near-term
  benefit being that `validate()` now surfaces malformed times the bridges
  otherwise swallow silently.

[ADR-0002]: 0002-opt-in-tiered-validation.md
[ADR-0006]: 0006-validation-findings-sum-type.md
[ADR-0007]: 0007-functional-core-errors-as-values.md
[KNMI/covjson-pydantic#34]: https://github.com/KNMI/covjson-pydantic/issues/34
