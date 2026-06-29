# covjson-msgspec vs covjson-pydantic

**Status: pre-draft working notes.** This file accretes per-topic comparisons
that will feed the published comparison documentation (issue #22). It is a
contributor-facing scratchpad, not the final user-facing doc; expect it to be
restructured when #22 is written and the docs toolchain (#19) is chosen.

**Re-evaluate when the relevant open issues land.** Several judgments below turn
on work that is in flight. When those issues close, revisit the affected
sections rather than trusting these notes:

- **#36** (close MUST-coverage gaps) -- adds the `num == 1` => `start == stop`
  check, removing one row where covjson-pydantic is currently more correct.
- **#21** / typed-projection idiom -- if we add an opt-in typed projection for
  axis values, it narrows the "static type precision" gap that today favors
  covjson-pydantic.
- **#23** (drop the NdArray generic) and **#12** (opt-in temporal validation +
  bridge-independent `datetime` conversion) are adjacent: #23 is part of the
  same generic-shedding story referenced below, and #12 firms up the temporal
  comparison.

Versions compared: covjson-pydantic `domain.py` / `base_models.py` as of
2026-06-29 (fetched from `KNMI/covjson-pydantic`, default branch).

## Axes

### How each models an axis

**covjson-pydantic -- multiple types plus a fixed container.**

- `CompactAxis` (`start` / `stop` / `num`) and `ValuesAxis[ValuesT]` (`values` /
  `bounds`) are separate classes, unioned at each use site.
- `ValuesAxis` is generic over its element type: `ValuesAxis[float]`,
  `ValuesAxis[str]`, `ValuesAxis[AwareDatetime]`, `ValuesAxis[Tuple]`.
- A fixed `Axes` model pins both which axes may exist and the element type per
  slot: `x` / `y` / `z` are `ValuesAxis[float] | ValuesAxis[str] | CompactAxis`,
  `t` is `ValuesAxis[AwareDatetime]`, `composite` is `ValuesAxis[Tuple]`.
- Domain-type axis rules run as `model_validator`s that raise at construction
  (`check_axis` / `check_domain_consistent`).
- `CovJsonBaseModel` is `extra="forbid"`, and `Axes` does not override it.

**covjson-msgspec -- one permissive struct plus an open dict.**

- A single `Axis` struct models all three forms (value-listing, regular,
  composite); `__post_init__` enforces that exactly one form is present (a
  cheap, local, O(1) invariant).
- `Axis` is not generic: `values` is `tuple[AxisValue, ...]` where `AxisValue =
  float | int | str | tuple[Any, ...]`.
- `Domain.axes` is `dict[str, Axis]`, so any axis name is representable.
- Domain-type axis rules live in the opt-in, tiered `validate()` (see ADR-0002),
  not in a construction-time validator.

### Dimension-by-dimension

| Dimension | covjson-pydantic | covjson-msgspec | Better for our goals |
| --- | --- | --- | --- |
| Extra / custom axes | `Axes` is `extra="forbid"` with fixed slots, so a conformant document carrying an additional axis fails to decode | `dict[str, Axis]` admits any axis name | covjson-msgspec (faithfulness) |
| Temporal values | `t` parsed to `AwareDatetime`: lossy (`Z` vs `+00:00`, fractional seconds, out-of-range dates, non-Gregorian calendars) | raw ISO 8601 strings, byte-faithful round trip | covjson-msgspec |
| Validation placement | construction-time raise; cannot load a non-conformant document to inspect or repair | permissive decode plus opt-in `validate()` | covjson-msgspec (ADR-0002) |
| Composite axes | `ValuesAxis[Tuple]` stub with a "TODO: better support" | full `tuple` and `polygon` modeling, with builders | covjson-msgspec (completeness) |
| Static element-type precision | `axes.t.values` is typed `List[datetime]`; precise per slot | `axes["t"]` is a form-agnostic `Axis`; the caller narrows | covjson-pydantic |
| Discoverability | named `Axes` fields are self-documenting | a dict is opaque to the type system | covjson-pydantic |
| Per-form rules | `CompactAxis.single_value_case` reads cleanly in isolation | all forms share one `__post_init__` | slight edge to covjson-pydantic |
| Regular `num == 1` => `start == stop` | enforced in `CompactAxis.single_value_case` | not checked today; `coordinate_values` silently drops `stop` (tracked in #36) | covjson-pydantic (until #36) |

### Verdict

For a fast, faithful, spec-complete library, covjson-msgspec's design is the
better fit on the dimensions that matter most: it is open to extra and custom
axes, byte-faithful on time, permissive on decode, and complete on composite
axes. On three of those (open axes, raw-string time, permissive decode)
covjson-pydantic's choices actively prevent representing or round-tripping
conformant documents. Its generic-element approach is also the same machinery
that forced it to disable strict mode (covjson-pydantic issue #4) and that we
are independently shedding (#23).

Where covjson-pydantic is genuinely better is static type precision and
discoverability: `axes.t.values` typed as datetimes, and named slots a reader
can see in the type. That is not an oversight on our side; it is the deliberate
"faithful core, precision as an opt-in projection" tenet. The honest caveat is
that for axes we have not built that projection yet (#21), so today a user gets
less static type help than covjson-pydantic offers. The published doc should not
claim parity on ergonomics unless and until that projection exists.

### Notes for the published comparison (#22)

- **Adopt, not just contrast:** the `num == 1` => `start == stop` MUST check.
  covjson-pydantic is simply more correct here; tracked in #36.
- **Deliberate divergences, one subsection each:** open dict vs fixed `Axes`;
  raw-string vs parsed temporal; opt-in `validate()` vs construction-time raise;
  one permissive struct vs class-per-form plus a generic; full composite
  modeling vs the `Tuple` stub.
- **Be self-critical:** per-axis static typing and discoverability is the one
  place to frame as a trade (faithfulness over static precision, recovered via
  opt-in projection), tied to #21 and the typed-projection idiom.
