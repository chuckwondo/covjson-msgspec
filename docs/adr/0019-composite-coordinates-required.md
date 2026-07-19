# ADR-0019: Composite axes require explicit `coordinates`

## Status

Accepted

## Context

§6.1.1 makes `coordinates` optional and gives it a default: "If missing, the
member `coordinates` defaults to a one-element array of the axis identifier."
The axis identifier is the axis's key in `Domain.axes`. §6.1.1 also says that
`coordinates` are "coordinate identifiers corresponding to the order of the
coordinates defined by `dataType`."

The spec states this default **generally** and gives no explicit rule that a
composite (`tuple`/`polygon`) axis must supply `coordinates`, nor a minimum
count. But look at what the default actually *is* for a composite axis. In the
Common Domain Types, a composite axis is keyed **literally `"composite"`** in
`Domain.axes`: Trajectory, Polygon, MultiPolygon and the rest all carry
`axes: { "composite": { ... } }`. So for an omitted `coordinates`, the default
resolves to **`("composite",)`**: a lone coordinate identifier *named
`"composite"`*.

That is self-evidently nonsensical. `"composite"` is the **kind** of the axis,
not one of its coordinates: a polygon's position components are `x` / `y` (/
`z`); a trajectory tuple's are `t` / `x` / `y`. A coordinate identifier
`"composite"` names none of them; it refers to nothing. The default hands
`("composite",)` to the *one axis whose entire purpose is to bundle several
named coordinates*, asking the reader to accept the axis's own kind-label as its
sole coordinate. (For `polygon` it is doubly impossible:
[RFC 7946 §3.1.1](https://www.rfc-editor.org/rfc/rfc7946#section-3.1.1) requires
two or more numbers per position, so a one-component position cannot exist at
all.)

So this is an **interpretation gap**: the spec is silent on what an omitted (or
too-few) `coordinates` *means* for a composite axis, so we must infer it.
ADR-0002 already draws the tier line once meaning is known: a value that leaves
the object *uninterpretable in isolation* is rejected at construction; one that
leaves *a meaningful object whose parts merely disagree* is deferred to
`validate()`.

## Decision

We interpret the §6.1.1 default as sensible for `primitive` and custom
dataTypes (their values are single or structurally-unspecified, so the axis's
own identifier is a fine sole coordinate) but **nonsensical for
`tuple`/`polygon`**: their values are multi-component, and as shown above the
default `("composite",)` names the axis's kind rather than any of those
components.

It follows that a composite axis with an omitted `coordinates` has **no usable
coordinate identifiers** (`("composite",)` names nothing), which is equivalent
to a supplied-but-empty `[]`: the object is *uninterpretable in isolation*, so
per ADR-0002 it is rejected at **construction** (`Axis.__post_init__`), not
reported by `validate()`.

Concretely, a `tuple`/`polygon` axis MUST supply `coordinates` meeting a
per-dataType floor (**`tuple` ≥ 1, `polygon` ≥ 2**, the ≥2 from RFC 7946
§3.1.1), and omitted or below-floor `coordinates` fail to construct. `primitive`
and custom axes keep the permissive default: omission is fine.

The `polygon` floor of 2 is a construction check, not a `validate()` one, for a
reason independent of the omitted case. `validate()`'s position-arity scan
(#138) compares each position's length to the coordinate-identifier count, so a
*self-consistent* 1-D polygon, whose one-element positions match its single
identifier `("x",)`, passes it (1 == 1) while still violating
[RFC 7946 §3.1.1](https://www.rfc-editor.org/rfc/rfc7946#section-3.1.1). Only a
check on the identifier count itself, at construction, catches it.

This composes with the pre-existing empty-`[]` check into a single rule: *a
construction-time `coordinates` value must name at least one usable coordinate
identifier.*

| dataType | omitted (`None`) | empty (`[]`) |
| --- | --- | --- |
| `primitive` / custom | allowed: default (the axis's own name) is a usable sole coordinate | rejected |
| `tuple` / `polygon` | rejected: default `("composite",)` names nothing usable | rejected |

Empty is rejected everywhere (zero identifiers, no default applies); omission is
rejected only where the default itself is unusable, the composite case. It is
one test, *"is there a usable identifier?"*, applied to whatever value the
default produces, not a separate rule per dataType.

## Alternatives considered

**Report it in `validate()` (the initial #147 design).** Rejected: a composite
without usable coordinates is not "a meaningful object whose parts merely
disagree" (ADR-0002's `validate` case) but uninterpretable in isolation, since
no coherent composite of that shape exists. Deferring it would also let the
nonsensical default of 1 flood the value-scan with per-value / per-position
arity errors against a bogus count, requiring a suppression guard that vanishes
once the bad axis simply cannot be constructed.

**Read the general default literally (allow composite omission, taking the
1-element default).** Rejected: it yields a document that decodes but is
meaningless: a polygon whose ≥2-component positions map to one identifier; a
tuple whose components are named after the axis. No sensible reading supports
it, which is what makes the inference above the only coherent one.

**Reaffirm ADR-0018, which read omission as a single, interpretable repair.**
ADR-0018 applied ADR-0002's "name the repair" criterion to this exact case and
concluded the opposite: §6.1.1's default is a single spec-defined repair, so a
composite axis omitting `coordinates` stays interpretable and belongs below
construction. It called `Axis(values=((1.0,),), data_type="tuple")` "a legal
one-tuple axis" and credited #131 with removing the earlier construction guard.
Rejected, because the criterion turns on whether the repair is *usable*, and for
a composite it is not: the default resolves to `("composite",)`, the kind-label
every Common Domain Type keys the axis by, which names no component. ADR-0018
reached its conclusion without following the default to that resolved value;
applying the same criterion to the value it actually produces places the check
at construction. Nothing conformant is lost: no Common Domain Type, and no
fixture in this repo, omits `coordinates` on a composite axis, so the default is
never relied on in practice. This record supersedes that sub-decision of
ADR-0018 (its "Why this tightens decode where ADR-0017 loosened it" passage);
the rest of ADR-0018 stands.

**Amend ADR-0002 instead of writing a new record.** Rejected: ADR-0002 records
the tier *line*; this is a distinct spec-*interpretation* decision (inferring
implicit meaning where the spec is silent), with its own rejected alternatives,
and the same primitive/custom-vs-composite criterion already governs #137 (a
`validate` exclusion) and will govern #139. A criterion driving several
decisions warrants its own record, grounded in ADR-0002's line rather than
folded into it.

## Consequences

- A composite axis without usable `coordinates` fails to decode/construct (a
  `ValueError`, surfaced by msgspec as a `ValidationError`), exactly as an empty
  `[]` already does. A caller cannot load-and-inspect such a document: the
  deliberate ADR-0002 carve-out for uninterpretable-in-isolation objects,
  accepted here.
- #138's position-arity check (an O(n) value scan in `validate()`) no longer
  floods on such axes: they cannot construct, so the scan only ever runs on axes
  with valid `coordinates`. No suppression guard is needed.
- The primitive/custom-vs-composite interpretation is recorded once; #137, #147,
  and #139 all rest on it.
- Revisit if CoverageJSON later states the composite `coordinates` rule
  explicitly (this inference becomes a citation), or defines a composite
  dataType with single-component values (which would need its floor set).
