# ADR-0017: Reference systems as a permissive core with a typed projection

## Status

Proposed

<!-- DRAFT for review (issue #113). Not yet accepted; not linked from the issue
until approved. -->

## Context

CoverageJSON [§7.2][spec-72] permits a custom (absolute- or compact-URI) value in
a reference system's `type` (e.g. `{"type": "uor:HEALPixRS"}`); a consumer MAY
ignore a type it does not understand but MUST still load the document. Our model
typed `system` as a **closed msgspec tagged union** of the five defined types, so
a custom `type` raised at decode and the **whole document failed to load**. That
is a conformance bug, since such a document is valid.

msgspec offers no cheap fix: a tagged union is closed (no unknown-tag slot), and a
union of tagged structs plus an untagged catch-all struct, or plus a `dict`, both
raise `TypeError`. Decode cannot express "known tags, else fallback"; the choice
is structural.

Two forces pull against each other. Permissive decode ([ADR-0002][adr2]) wants a
slightly-nonconformant or unfamiliar document to *load*, with conformance deferred
to opt-in `validate()`. "Make illegal states unrepresentable" wants each kind to
be a clean type, not a struct of mostly-absent optionals. A reference system is
also heterogeneous: three CRS types share `{id}`, `TemporalRS` requires
`calendar`, `IdentifierRS` requires `targetConcept`, and a custom type has no
defined members at all ([§5][spec-5], [§7.2][spec-72]).

This is the kind-union situation [ADR-0004][adr4] already resolved for `NdArray`
and `Axis`: several logical types behind one concrete type, with precision offered
as an opt-in projection rather than the stored representation.

## Decision

Model a reference system as a **permissive core struct plus a typed projection**.

- **`ReferenceSystem`** (stored) is one permissive struct: an open `type_: str`
  and the union of the defined types' members, all optional. It decodes any
  reference system in a single pass; unknown-tag documents load, and custom
  *members* drop per [ADR-0012][adr12].
- **`ReferenceSystem.refine() -> ResolvedReferenceSystem`** projects the core to a
  closed union of clean, narrow variants
  (`GeographicCRS | ProjectedCRS | VerticalCRS | TemporalRS | IdentifierRS |
  OpaqueRS`) for reading. It is *gated*: a known type that fails its
  required-member invariant projects to `OpaqueRS` (preserving `type_`), so a
  returned `TemporalRS` always has a `calendar`. `OpaqueRS` (`{type_}` plus
  `is_custom()`) is the catch-all for both a genuine custom type and a malformed
  known one. It is a method, not a free `resolve()`: that verb already names a
  temporal-value resolution (`temporal.resolve`) and a `$ref` resolution
  (`resolve_references`), so a method keeps this projection with its data and
  avoids the overloaded name.
- Construction is via builders on the core (`.geographic()`, `.temporal()`,
  `.identifier()`, ...); a custom type is just `ReferenceSystem(type_=...)`.
- A single required-member rule (`missing_required_member`), homed in a shared
  `_reference_invariants` module so `validation` can import it without reaching
  into `referencing`'s privates, is the sole source of those invariants; both
  `refine()`'s gate and `validate()`'s error consult it, so they cannot disagree.

Field sets follow §5 exactly: the three CRS variants are `{id}` (no
`description`; §5.1 grants CRS types only `id`); inline CRS definitions (§5.1.4,
`datum`/`cs`) are left unmodeled pending the spec; `OpaqueRS` is `{type_}` only
(§7.2 defines no other member for a custom type). `Concept` gains an optional
`id`, which appears in the §5.3 example.

## Alternatives considered

**Keep the closed tagged union (optionally user-extensible), strict default.** A
caller who needs a custom type extends the union themselves. Rejected as the
*default*: it rejects a conformant §7.2 document out of the box, contradicting
permissive decode, the bug this ADR fixes. It survives as the [#103][i103]
power-user path, re-expressed as "extend the permissive core."

**Sum-type-with-catch-all as the stored form (`Raw` boundary + eager
projection).** Make the clean variant union the stored type; decode `system` as
`msgspec.Raw` and project at the decode edge. Rejected: because the union cannot
decode custom tags, every container that holds a system (`Domain`, `Coverage`,
`CoverageCollection`) needs a decode-side shadow struct distinct from its public
form: a second source of truth for those shapes that drifts as fields are added,
and a post-decode rebuild that breaks single-pass decode. It also complicates
[#103][i103] capture (a user would extend a library boundary adapter rather than
subclass a struct). The clean-variant benefit it buys is delivered instead by
`refine()` as an opt-in projection, at no such cost. This is the idiom ADR-0004
already chose for `Axis`; diverging here for reference systems alone would
fragment it. Its one advantage (it loads a custom type whose member collides
with a known field name at an incompatible type, which the permissive core
rejects; see Consequences) did not justify the boundary-adapter cost for that
narrow, §7.1-discouraged input.

**`OpaqueRS` carrying `id`/`description`.** Rejected: §7.2 grants a custom type
only `type`; the example's other members (`uor:h`, ...) are §7.1 custom members
(dropped per ADR-0012), so `id`/`description` would be perpetually absent --
reintroducing the always-`None` grab-bag on the one variant meant to be simplest.
A consumer reads any incidentally-preserved `id` off the core.

**`description` on the CRS variants (status quo).** Rejected: §5.1 lists only `id`
for GeographicCRS/ProjectedCRS/VerticalCRS. The core still declares `description`
(IdentifierRS needs it), so a stray `description` on a CRS still round-trips; the
variant just does not surface it.

**Collapsing the three identical CRS variants into one `refine()` arm.**
Rejected: it re-tags `ProjectedCRS`/`VerticalCRS` as `GeographicCRS` on
round-trip. They share a base but keep distinct tags.

**`refine()` returning the variant for a malformed known type**
(`TemporalRS(calendar=None)`). Rejected: it makes the variant's `calendar: str` a
lie. Gating on the shared predicate keeps the variant honest; the malformed case
is `OpaqueRS` with `type_` preserved and the specific diagnosis in `validate()`.

## Consequences

- A custom §7.2 reference-system type now loads, reads opaquely (`OpaqueRS`, with
  `is_custom()` distinguishing a custom type from a malformed known one), and
  round-trips its spec surface (`type`/`id`/`description`); custom *members* drop
  per ADR-0012. **One narrow exception, pinned by a test:** a custom type whose
  member reuses a known field name (`calendar`, `id`, ...) with an incompatible
  JSON type still fails to decode, because the core enforces field *types* on its
  declared fields (`{"type":"uor:X","calendar":123}` raises at `$.calendar`). Such
  bare names violate §7.1's "custom member names SHOULD be compact URIs" (which
  never collide), so the reach is small; it is the one input the Raw-based
  alternative would have loaded, and it is documented and tested rather than
  designed around.
- **Decode becomes permissive for reference systems.** A `TemporalRS` without
  `calendar` (or `IdentifierRS` without `targetConcept`) previously failed at
  decode; it now loads and is reported by `validate()` via new
  `temporal.missing-calendar` / `identifier.missing-target-concept` errors. This
  is ADR-0002 applied consistently (a former decode error becomes a validate
  error), and is a visible behavior change.
- `refine()` and `validate()` are independent but consistent (shared
  `_reference_invariants`); a testable invariant ties them:
  `refine() -> OpaqueRS(is_custom() == False)` iff a required-member error
  exists. On a validated document, any `OpaqueRS` is unambiguously a custom type.
- Typed capture of custom *members* stays the [#103][i103] subclassing recipe,
  expressed for reference systems as "extend the permissive core" (add optional
  fields, re-type the ancestor chain, reuse `encode` unchanged). #103 gains a
  reference-system example.
- Public surface changes (pre-1.0): `ReferenceSystem` is now a struct, not a union
  alias; `GeographicCRS`/... become read-projection variants; `OpaqueRS`,
  `ResolvedReferenceSystem`, the `.refine()` method, and the builders are added;
  bridge and validation dispatch route through `.refine()`.
- Spec-fidelity corrections land: `description` off the CRS variants, `Concept.id`
  added, §5.1.4 inline CRS definitions documented as unmodeled. Docs asserting
  "spec-complete" / byte-faithfulness are updated.
- Extends ADR-0004 (a fourth instance of typed-projection-over-a-faithful-core)
  and applies ADR-0002, ADR-0003 (open `type_`), [ADR-0006][adr6], ADR-0012. A
  revisit is prompted if the spec defines §5.1.4 inline CRS structures (then model
  them) or adds an unknown-tag mechanism upstream.
- Aligning the other two ADR-0004 projection instances, `Axis` and `NdArray`, to
  this `refine()`-style shape is a consistency follow-up, out of scope here.
  `Axis` has no such whole-struct projection today; `NdArray`'s element-typed
  whole-struct projection was already weighed and deferred by ADR-0004 (its
  value-level `values_as` covers the common need). Tracked in #123, and settled
  by [ADR-0018][adr18]: neither gains one, because a projection earns its keep
  only where it recovers a guarantee nothing else enforces, and `Axis` already
  enforces its own at construction.

[spec-5]: https://github.com/covjson/specification/blob/master/spec.md#5-reference-system-objects
[spec-72]: https://github.com/covjson/specification/blob/master/spec.md#72-custom-types
[adr2]: 0002-opt-in-tiered-validation.md
[adr4]: 0004-ndarray-single-non-generic-class.md
[adr6]: 0006-validation-findings-sum-type.md
[adr12]: 0012-custom-members-dropped-on-decode.md
[adr18]: 0018-typed-projection-scope.md
[i103]: https://github.com/chuckwondo/covjson-msgspec/issues/103
