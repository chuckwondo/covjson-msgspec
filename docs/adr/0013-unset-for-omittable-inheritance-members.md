# ADR-0013: `UNSET` for omittable inheritance members; reject `null` at decode

## Status

Accepted

## Context

CoverageJSON marks many optional members "MAY be omitted", and the spec never
permits JSON `null` for them. The library modeled such members as
`X | None = None`. For most members that is harmless, but it has two flaws that
matter for one specific group:

1. It silently accepts a spec-forbidden `"member": null`, decoding it to `None`.
2. It overloads `None` as the absent sentinel, so "omitted" and "explicit null"
   become the same value.

The group where both flaws bite is the five structural members that participate
in `CoverageCollection` inheritance: `domainType`, `parameters`, and
`parameterGroups`, on both `Coverage` and `CoverageCollection`. The spec (§6.4,
§6.5) types these as a string, an object, and an array respectively; makes them
optional (with `parameters` conditionally required: "A coverage object MUST have
a `parameters` member if the coverage object is not part of a coverage
collection or if the coverage collection does not have a `parameters` member");
and inherits them from the collection to a member that omits its own ("If a
coverage collection object has the member `domainType`, then this member is
inherited to all included coverages"). Nowhere does the spec permit any of these
members to be `null`.

`CoverageCollection._resolve` implements inheritance by treating a member's
absence as the trigger. With `X | None`, absence and `null` are both `None`, so
the graft fires on a member that explicitly wrote `null`:

```json
{
  "type": "CoverageCollection",
  "parameters": {"temp": {"type": "Parameter", "observedProperty": {"label": {"en": "T"}}}},
  "coverages": [
    {"type": "Coverage", "domain": {"type": "Domain"}, "ranges": {}, "parameters": null}
  ]
}
```

With `X | None`, the member's `"parameters": null` decodes to `None`,
`_resolve` fires, and `resolved_coverages()[0].parameters` silently becomes
the collection's `{"temp": ...}`: inherited data the member never declared.
This is not a conformance blemish but a quiet data defect, and the harm has
two compounding parts. The absent-vs-null distinction drives a
*content-changing* branch, so a misread `null` alters what the resolved
coverage contains rather than merely failing a nicety. And that branch is
*unguarded*: no `__post_init__` invariant catches a `null`-triggered graft,
and no `validate()` rule can, because `validate()` runs after decode, where
`null` has already become `None` and is indistinguishable from absence.
Rejecting `null` at the type, on decode, is the only thing that closes the
gap: with `UNSET`, decoding the document above raises a `ValidationError` at
`$.coverages[0].parameters`, the malformed `null` never reaches `_resolve`,
and inheritance fires only on genuine omission.

That sorts every omittable member into three kinds, and only the first adopts
`UNSET`:

- **Harmful and unguarded** (the five inheritance members): a misread `null`
  drives the content-changing graft above, silently giving the member fields it
  never declared, and nothing else catches the mistake. `UNSET` at decode is the
  only thing that closes the gap.
- **Inert** (pure scalars such as `id`, `label`, `unit`): `null` and absence
  both mean "not present" and nothing downstream diverges, so `UNSET` would buy
  only the rejection of a harmless `null`.
- **Already guarded** (the discriminators): a stray `null` is rejected at
  construction (see Alternatives), so `UNSET`'s marginal value is small.

`msgspec.UNSET` models the three states exactly: an absent member decodes to
`UNSET`, `"member": null` raises a `ValidationError` at decode, `"member": {}`
decodes to an empty object, and an `UNSET` field is omitted on encode.

## Decision

Type the five inheritance members `X | UnsetType = UNSET`, and read their
absence as `is UNSET` (in `_resolve` and in the `parameters`-required check).
This rejects a spec-forbidden `null` at decode and separates "omitted" (the
inheritance trigger) from a written `null`.

Reject `null` at decode is a tier-1 field-type check, not a relaxation of the
permissive-decode stance in ADR-0002. ADR-0002 keeps cross-cutting *semantic*
rules out of decode and refuses to reject *interpretable* objects; a `null`
where the spec types an object, string, or array is neither. It is the same
class as `"parameters": 5`, which msgspec already rejects at decode. Rejecting
it is also the byte-faithful choice: the model never reinvents "absent" from a
value the producer actually wrote as `null`.

Scope is deliberately narrow, following the three-kind sort above: adopt `UNSET`
only for the harmful-and-unguarded members, and leave every other member at its
current default.

- Pure scalars (`id`, `label`, `description`, `unit`, `symbol`, ...) keep
  `None`.
- Discriminators (`Axis.values` / `coordinates`, `ObservedProperty.categories`)
  keep `None`.
- Always-iterable members (`referencing`, `NdArray.shape`, `axisNames`) keep
  their `()` defaults, since empty is a valid representation and the field stays
  iterable.

## Alternatives considered

**Stay maximally permissive: treat `null` as absent, defer to `validate()`.**
Rejected. It preserves a blanket "decode never rejects for conformance" rule,
but at the cost of silently reinterpreting a written `null` as absence and then
grafting inherited data onto it. It is lossy (against the byte-faithful tenet)
and, as noted above, cannot be recovered later: the distinction is gone by the
time `validate()` runs.

**Uniform: `UNSET` for every omittable member the spec forbids `null` on.**
Rejected. It is principled and uniform, but null-rejection is a decode-time type
decision with no cheaper deferred home, so applying it model-wide replaces the
universally-idiomatic `None` with `UnsetType` across the public read surface (a
permanent ergonomic cost paid on every field access) to reject a `null` that, on
inert members, harms nothing. `UNSET` earns its keep only where a `null` is
harmful and unguarded.

**Also convert the discriminators.** Rejected. Their forms are already guarded
at construction. An `Axis` requires exactly one of `values` or the
`start`/`stop`/`num` triple, so `"values": null` is rejected by `__post_init__`
whenever no regular triple is present; it slips through only as a redundant
no-op, when a valid regular triple already makes the axis interpretable.
`ObservedProperty` guards `categories` against its categorical flag likewise.
So `UNSET` there buys only the rejection of a redundant `null` on an
already-interpretable object: the same inert-null trade as the scalars, on a few
more fields.

## Consequences

- Decode is stricter for exactly these five members: `"member": null` now raises
  rather than decoding to `None`. Every other member is unchanged, and decode
  stays permissive for interpretable-but-nonconformant documents.
- **Invariant: the five members are never runtime-`None`.** Their domain is a
  concrete value (present) or `UNSET` (absent). `msgspec.Struct.__init__` does
  not runtime-type-check construction, so a `None` forced in past the annotation
  would slip past the `is UNSET` reads that replaced the old `is None` reads
  (for example, into the parameter-group validator, which expects an object).
  `None` is not made runtime-unrepresentable (a msgspec limitation); the
  invariant is enforced by strict mypy/basedpyright, which forbid every
  internal `None`-construction, and decode and `_resolve` cannot produce one.
  The temptation to write `is UNSET or is None` is declined: it would re-merge
  the two states this decision exists to separate.
- **Containment: `UNSET` lives on the wire structs.** It is normalized to `None`
  (or absorbed by an existing projection) at the first internal or bridge
  boundary (`Coverage.effective_domain_type` and the two bridge read sites), so
  `UnsetType` never reaches a consumer that expects `dict | None`.
- A revisit is warranted only if the spec adds `null` as a permitted value for
  any of these members, which would undo the premise for rejecting it at decode.
