# ADR-0018: When a typed projection earns its keep

## Status

Accepted

## Context

[ADR-0004][adr4] named the shape "typed projection over a faithful core" and
applied it to `NdArray`. [ADR-0017][adr17] applied it again to reference systems,
as `ReferenceSystem.refine() -> ResolvedReferenceSystem`, and left a question
open: should `Axis` and `NdArray` gain the same `refine()`-style whole-struct
projection, so the three instances read alike ([#123][i123])?

They already differ. `ReferenceSystem` has a whole-struct `refine()`; `NdArray`
has a value-level `values_as(dtype)`; `Axis` has builders and accessors and no
projection type at all. Either that divergence is principled or it is drift, and
nothing recorded said which.

Four facts constrain the answer:

- **An axis `dataType` is open, exactly like a reference system's `type`.**
  [§7.2][spec-72] lists them in one sentence: "Custom types MAY be used with the
  following members: `"domainType"` in domain objects, `"dataType"` in axis
  objects, `"type"` in reference system objects." (#123 assumed axis kinds were
  closed; they are not.)
- **An `Axis` is a product; a reference system is a sum.** A reference system
  varies on one axis, its tag, so `OpaqueRS` is `{type_}` and nothing else. An
  `Axis` varies on two independent axes: the *form* (`values` XOR
  `start`/`stop`/`num`) and the *dataType* (primitive / tuple / polygon /
  custom). A custom-dataType axis still has a form, and both
  `{"dataType":"knmi:range","values":[...]}` and
  `{"dataType":"knmi:range","start":0,...}` must round-trip, so an `OpaqueAxis`
  would need `{data_type, values | start/stop/num, coordinates, bounds}`: the
  core, minus nothing.
- **ADR-0017's honesty rule bites.** It rejects a variant whose declared type is
  a lie. But `Axis.values` is `tuple[AxisValue, ...]`, and §6.1.1's "For
  `"tuple"`, each axis value MUST be an array of fixed size of primitive values"
  is O(n) to check, which [ADR-0002][adr2] keeps out of `__post_init__`. So a
  `TupleAxis.values: tuple[tuple[Any, ...], ...]` needs an O(n) gate on a struct
  whose `__len__` is deliberately O(1), or it is a lie.
- **ADR-0002 already draws a line** between what is checked at construction and
  what is deferred to `validate()`, and the three instances sit on different
  sides of it.

## Decision

**A typed projection earns its keep when it recovers a guarantee nothing else
enforces.** Where the core already enforces the guarantee, a projection only
re-states it for the type checker: a much weaker claim, priced against the union
it costs. The projection's *tier* follows the guarantee's tier.

Applied to all three instances, this justifies the existing divergence rather
than removing it:

| | Guarantee | Enforced at construction? | Projection |
|---|---|---|---|
| **ReferenceSystem** | a `TemporalRS` has a `calendar` | No, deliberately (ADR-0017 made it a `temporal.missing-calendar` validate error) | `refine()` is the **only** place the guarantee exists |
| **NdArray** | values match `dataType` | No: O(n), so ADR-0002 defers it | `values_as` is the **only** place |
| **Axis** | exactly one form; non-empty `values`; `num >= 1`; `num == 1` implies `start == stop`; composite implies `values` | **Yes**: `__post_init__` | would only re-state it |

So: **no `Axis.refine()`**, and **`NdArray` keeps `values_as` with no
whole-struct element-typed projection**, reaffirming ADR-0004's deferral.
`Axis` is not the shape's gap but its most complete instance: the tenet offers
"an accessor or a builder", and `Axis` has both (`regular` / `listed` / `tuple_`
/ `polygon`, and `coordinate_values` / `__len__`). `subset.py` shows the pair
working: read kind-agnostically through `coordinate_values`, write back through
`Axis.listed`.

One gap in the `Axis` row is closed here, because the decision depends on it: a
composite axis now requires `values`, so the regular form and a `"tuple"` /
`"polygon"` dataType can no longer be combined.

### Why this strictens decode where ADR-0017 loosened it

ADR-0017 moved a former decode error to a `validate()` error. The guard above
moves the other way: a document that used to load now raises. Both cite ADR-0002,
so the two look contradictory. They are not: they sit on **opposite sides of the
same line**. ADR-0002 reserves `__post_init__` for invariants that are local,
O(1), **and** whose violation leaves the object *uninterpretable in isolation*.
The operative test:

> **Name the repair.** Exactly one repair means the object is interpretable, so
> the check belongs in `validate()`. Zero or ambiguous repairs mean it is not,
> so the check belongs at construction.

A `TemporalRS` without a `calendar` has one reading: temporal, calendar unknown.
A `"tuple"` axis carrying `start`/`stop`/`num` has two incompatible ones -- the
producer mislabeled the `dataType` (repair: three numbers), or the producer lost
the tuples (repair: impossible) -- and nothing in the document chooses. The test
agrees with every existing placement in the library but one, and that one is a
bug it found rather than a counterexample:

| State | Repairs | Predicted | Actual |
|---|---|---|---|
| both axis forms present | ambiguous | construction | construction |
| `values` empty | none | construction | construction |
| `num == 1`, `start != stop` | ambiguous | construction | construction |
| **composite with the regular form** | **two, incompatible** | **construction** | **construction (this ADR)** |
| `bounds` length is not `2 * len(values)` | one: drop the bounds | `validate()` | unchecked ([#129][i129]) |
| a `TemporalRS` without `calendar` | one | `validate()` | `validate()` |
| values not matching `dataType` | one | `validate()` | `validate()` |
| composite without `coordinates` | **one: apply the documented default** | **not construction** | **not construction ([#131][i131])** |

That last row is the test earning its keep. 6.1.1 does not require a composite
axis to supply `coordinates`; it says "If missing, the member `"coordinates"`
defaults to a one-element array of the axis identifier". A missing `coordinates`
therefore has exactly one repair, the spec's own default, so the axis stays
interpretable and construction is the wrong tier. `__post_init__` rejected it
anyway, so `Axis(values=((1.0,),), data_type="tuple")` (a legal one-tuple axis)
did not load. The test found that as a bug rather than a counterexample, and
[#131][i131] removed the guard, which is why the row now reads as predicted.

Removing it cost nothing the guard was actually providing, which is the sharper
lesson. The guard checked that `coordinates` was *present*, never that it *fit*:
`Axis(values=((1.0, 2.0, 3.0),), data_type="tuple", coordinates=("composite",))`
satisfied it and still had three components against one identifier, and the
bridges silently dropped the surplus two. The real rule lives at the scanning
tier, over the *resolved* identifiers, as `validate`'s `axis.composite-arity`
([#127][i127]). An over-strict guard is not a conservative guard: it rejects
conformant documents while the malformation it was mistaken for walks past.

The `bounds` row is the separator, and it is why the rule is not "local and cheap
implies construction": `len(bounds) != 2 * len(axis)` is local *and* O(1), yet it
still belongs in `validate()`, because such an axis stays interpretable -- its
coordinates are fine and only `bounds` is junk. Cheapness alone does not earn
construction-tier; only uninterpretability does. (That rule is unimplemented;
[#129][i129].)

### Why a guard rather than an unrepresentable state

Making the state unrepresentable is unavailable, not skipped. `Axis` is one
permissive struct because msgspec cannot decode an untagged union of structs and
the forms share no `"type"` discriminator: the same wall ADR-0017 hit when it
rejected a sum type as the *stored* form for reference systems. The construction
path is already unrepresentable, via the builders -- `Axis.tuple_` cannot produce
this state. The guard covers only the decode path, which no type can reach.

## Alternatives considered

**`Axis.refine() -> RegularAxis | ListedAxis | TupleAxis | PolygonAxis`.** The
strongest case for it is real: `__post_init__` computes the kind (`has_values` /
`has_regular`) and discards it, so every reader re-derives it structurally and
`coordinate_values` re-asserts the invariant on every read (`assert self.start is
not None`, ...). In a library whose tenets say "immutable by default, statically
enforced", "the type checker cannot see it" is a weak dismissal. Rejected anyway,
on four counts:

- *The catch-all is the core.* Because `dataType` is open (§7.2) and `Axis` is a
  product, `OpaqueAxis` needs every member the core has. The alternatives are a
  catch-all identical to the core, a six-variant split
  (`OpaqueRegularAxis | OpaqueListedAxis | ...`), or dropping `data_type` on
  projection and losing the custom type: the fidelity loss ADR-0017 exists to
  prevent.
- *Consistency with ADR-0017 is what kills it.* An honest `TupleAxis` needs the
  O(n) element gate; a dishonest one is the exact thing ADR-0017 rejected.
- *`bounds` rebuilds the grab-bag.* §6.1.1 permits `bounds` on any axis,
  unrestricted by dataType, so every variant carries `bounds: ... | None`,
  including `PolygonAxis` where it is meaningless -- the failure ADR-0017 named
  when it rejected `OpaqueRS` carrying `id` / `description`.
- *The payoff is two call sites.* Only two places in the library distinguish
  regular from listed: `_repr.py`'s axis detail, which infers "regular" from an
  absent `values` and then reads `start` / `stop`, and `validation.py`'s
  monotonic-ordering filter, which selects the listed-primitive axes to scan.
  Every other consumer is either kind-agnostic (`coordinate_values` / `__len__`)
  or a two-way composite test, and the projection would not shorten those. Two
  sites, one of them a filter, do not carry four new public types plus a
  catch-all.

**Element-typed `NdArrayFloat | NdArrayInt | NdArrayStr`.** Rejected, reaffirming
ADR-0004: it reopens the multi-type shape that ADR retired and duplicates
`shape` / `axis_names` across variants. The demand signal never arrived --
`values_as` has no callers inside the library, the same evidence ADR-0004 read
when it deferred this.

**A "projection at the tier of the imprecision" rule.** Rejected as circular: it
describes what `values_as` happens to be rather than predicting anything, and for
`Axis` it assumes its conclusion (the optionals *are* at the whole-struct tier).
The recorded rule is grounded in ADR-0002's pre-existing line instead.

**Recording this by amending ADR-0004.** Rejected: it is Accepted, and an ADR is
an immutable record, not a living document. ADR-0017 already deferred this
question once, so the recurrence is demonstrated and warrants its own record.

## Consequences

- `Axis` and `NdArray` keep their current shapes. The three ADR-0004 instances
  differ **by rule**, not by drift, and a future reader has the test rather than
  three precedents to average.
- A composite axis with the regular form now raises at construction and at decode
  (`a 'tuple' axis requires 'values'`). This is a behavior change, pre-1.0: such
  a document used to decode clean, pass `validate()`, and convert to *zero rows*
  in the pandas and xarray bridges -- silent data loss with no error the caller
  could catch. Nothing in the library, tests, or docs constructed one.
- A custom §7.2 `dataType` is unaffected and keeps both forms; the guard names
  `"tuple"` and `"polygon"` explicitly rather than excluding "primitive", and a
  test pins it.
- The bridges' `axis.values or ()` fallbacks are dead and removed; the `cast` to
  `tuple[tuple[Any, ...], ...]` remains, and remains unprovable, until the O(n)
  rule below lands.
- **The core still does not gate every illegal `Axis` state, only the
  O(1)-decidable ones.** A `"tuple"` axis whose values are numbers, or whose
  arity does not match `coordinates`, still reaches the bridges as a raw
  `TypeError` / `IndexError` ([#127][i127]). That is not a hole in this decision
  but the substance of it: gating those is precisely what a `TupleAxis` variant
  would require, and it costs O(n).
- Revisit if msgspec gains untagged struct-union decode (the core could then be a
  sum type, and the question reopens on new terms), or if a real consumer needs
  element-typed narrowing that `values_as` cannot serve.

[adr2]: 0002-opt-in-tiered-validation.md
[adr4]: 0004-ndarray-single-non-generic-class.md
[adr17]: 0017-reference-systems-permissive-core-projection.md
[spec-72]: https://github.com/covjson/specification/blob/master/spec.md#72-custom-types
[i123]: https://github.com/chuckwondo/covjson-msgspec/issues/123
[i127]: https://github.com/chuckwondo/covjson-msgspec/issues/127
[i129]: https://github.com/chuckwondo/covjson-msgspec/issues/129
[i131]: https://github.com/chuckwondo/covjson-msgspec/issues/131
