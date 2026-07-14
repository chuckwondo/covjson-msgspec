# ADR-0012: Foreign members dropped on decode; lossless relay forwards raw bytes

## Status

Accepted

## Context

msgspec sets no `forbid_unknown_fields` on our structs, so decode ignores any
member a struct does not declare. That keeps decode permissive ([ADR-0002]) and
byte-faithful for every spec-defined member, but any other member is silently
dropped: `decode -> encode` is lossy for it. The loss happens at the core,
before any export bridge, which is the tension with the byte-faithful tenet.

A *foreign member* is a JSON object member whose key is not one the matching
struct declares, so on decode msgspec has nowhere to put it and discards it. The
CoverageJSON spec calls these *custom members* and gives them a home in
[Section 7, Extensions][spec-7]; "foreign member" is the term GeoJSON (RFC 7946,
which CoverageJSON builds on) uses for the same idea, and the one this codebase
uses. Their one naming rule, [Section 7.1][spec-7.1], is that a custom member's
name SHOULD be a compact URI of the form `"prefix:suffix"`. Neither corpus
example obeys it (both `preferredColor` and `cs` are bare names), so the very
documents that motivate this ADR carry technically non-conformant extensions:
exactly why an implementation must tolerate rather than trust them.

Two such members appear in the vendored corpus, and they are two different
things:

- `preferredColor` on a `Category` is a plain custom member
  ([Section 7][spec-7]): a common de-facto extension, not a spec-defined field.
  [Section 3, Parameter objects][spec-3] defines a category as an `"id"` and a
  `"label"` member with an optional `"description"`, and nothing else.
- `cs` / `datum` on a `VerticalCRS` are
  [Section 5.1.4 inline CRS definitions][spec-5.1.4], which the spec calls "not
  yet fully defined" and are distinct from generic extensions. They apply to the
  three geospatial CRS types (`GeographicCRS`, `ProjectedCRS`, `VerticalCRS`),
  not to `TemporalRS` or `IdentifierRS`; we identify CRSs by `id` and do not
  model them (see the `covjson_msgspec.referencing` module docstring).

The load-bearing fact is [Section 7][spec-7]'s scope: extensions may appear on
**any** object, at any nesting depth, not on an enumerable set of types. The
spec restricts only their naming (the `"prefix:suffix"` SHOULD) and imposes no
requirement to preserve them on round-trip. Any preservation scheme scoped to
the types we happen to have observed is therefore incomplete by construction.

## Decision

**The typed model is a deliberately lossy projection of the document, not a
byte-preserving store; foreign members are dropped on decode and we add no
capture mechanism.** Decode stays permissive (accept and ignore), never
`forbid_unknown_fields` (accept and reject).

**To relay or proxy a document unchanged, forward its raw bytes; do not route
through `decode -> encode`.** Reaching for `decode` to preserve bytes is the
wrong tool: a caller that only needs to reformat can round-trip the raw tree
(`msgspec.json.decode(raw, type=dict[str, msgspec.Raw])` then re-encode) and
touch no typed struct, preserving every member at every depth. Decoding is for
understanding and transforming, which is exactly where a lossy projection is
the right shape.

## Alternatives considered

**`forbid_unknown_fields=True` (reject documents carrying foreign members).**
Rejected. The spec explicitly permits extensions; rejecting a valid document is
strictly worse for interoperability than accepting and ignoring it, and it
would make us reject the spec's own [Section 5.1.4][spec-5.1.4] examples.

**Per-type `extras` fields (on `Category` and the CRS types).** Rejected as
incomplete by construction. Because extensions may sit on any object, capturing
only the types seen in the corpus still silently drops a `foo:bar` on a
`Domain`, an `Axis`, or an `NdArray`. Chasing observed types never converges on
completeness.

**A base-struct `extras` + a bespoke recursive codec (complete capture).**
Deferred, not refuted. The intent is an `extras: dict[str, msgspec.Raw]` on
`CovJSONStruct` carrying every foreign member at every node, closing the leak.
The field alone does not achieve this: msgspec has no unknown-field catch-all
(no equivalent to pydantic's `extra="allow"`), so a declared `extras` member
captures only a wire key literally named `extras`, and every other unknown key
is still dropped on decode (verified on msgspec 0.21.1). Populating `extras`
takes a custom decode path, not just a struct field: decode the raw tree to
`dict[str, msgspec.Raw]` alongside the typed decode, diff each node's keys
against the struct's declared fields, and stash the leftovers, recursively.
Re-emission is a second custom walk, since `enc_hook` never fires for a
`msgspec.Struct` (msgspec encodes structs natively): writing captured members
back as siblings requires `to_builtins` per node merged with that node's
`extras`. The cost is thus a shadow codec on *both* ends, kept in lockstep with
the type structure forever. That standing maintenance coupling is not worth
paying for a hypothetical consumer. It is the right design *only* for a caller
that must decode a document, modify a typed field, and re-encode while
preserving foreign siblings, and no such consumer exists today.

**Retain the raw parsed tree as the source of truth, the typed model as a
view.** Rejected. The model is `frozen`, so "modifying" a value means
constructing a new one; an overlay-on-encode step then cannot distinguish a
field the caller changed from one left alone, making the reconciliation
ambiguous.

**Runtime `defstruct` subclass-threading (a user-side typed-capture helper).**
Explored with a working prototype, rejected for shipping. msgspec builds struct
subclasses at runtime (`msgspec.defstruct(bases=...)`), so a helper can take a
target struct plus the foreign fields to add, rebuild every ancestor along the
containment path (decode dispatches on the declared field type, so each parent
must be re-typed to reference the subclass one rung below), and return a custom
root union to decode against. The prototype does this in ~110 lines with no new
dependency, reusing the existing encoder unchanged (encode is structural). It is
rejected because the generated types are invisible to static analysis: a
`defstruct` result is `type[msgspec.Struct]` to a type checker, so the captured
members type as `Any` with no completion. For a library whose headline is
precise static typing, trading that away for terser capture is the wrong
bargain, and the caller who wants capture without typing already has the raw
`dict[str, msgspec.Raw]` relay above. A frozen-forcing metaclass over the base
(to drop the repeated `frozen=True`) does not rescue this path either: any
custom metaclass on the base breaks `defstruct(bases=...)` outright. The
supported typed path is instead manual subclassing (see the consequence below).

## Consequences

- `decode -> encode` is lossy for foreign members. This is recorded as the one
  carve-out to the byte-faithful tenet (CLAUDE.md) and is the documented
  behavior of the corpus round-trip test.
- `omit_defaults` (on the `CovJSONStruct` base) is a second, milder source of
  byte-level round-trip difference: normalization, not information loss. Encode
  drops any field still equal to its default, so an explicit `null` on a `None`
  optional, or an explicit `[]` on one of the empty-tuple-default fields
  (`NdArray.shape` and `axisNames`; `Domain` and `Coverage` `referencing`),
  re-encodes as an absent member. This loses nothing: the round-tripped object
  is unchanged (`decode(encode(x)) == x`), and CoverageJSON treats an absent
  optional and an explicit-default one as equivalent. It differs in kind from a
  dropped foreign member, whose value is absent from the decoded object itself
  and unrecoverable. The motive is the CoverageJSON wire idiom (optional
  members are omitted, never emitted as `null`), yielding one canonical,
  null-free encoding; reduced size is incidental.
- Lossless relay/proxy is a tool-choice, not a feature: forward raw bytes (or
  reformat the raw `dict[str, msgspec.Raw]` tree), never `decode -> encode`.
- [Section 5.1.4][spec-5.1.4] `cs` / `datum` inline CRS definitions stay
  unmodeled; CRSs are identified by `id`.
- The supported way to capture a foreign member *with full static typing* is to
  declare the subclass chain by hand: subclass the target and every ancestor on
  its path to a root, then build a `msgspec.json.Decoder` over a custom root
  union; the existing encoder needs no change. Real-world foreign members are
  shallow (`preferredColor` on a `Category` is the deepest observed, at four),
  so the chain is short, and the decoded types stay fully typed. A how-to
  documents this pattern. A codegen helper that *emits* that subclass source
  (keeping the types static while automating the boilerplate) is a deferred
  middle ground, worth building only if a genuinely deep foreign member makes
  the hand-written chain painful.
- Revisit gate: a concrete consumer that must decode, modify a typed field, and
  re-encode while retaining foreign siblings reopens the deferred base-struct
  `extras` design above. Absent that, the projection stays lossy on purpose.

[ADR-0002]: 0002-opt-in-tiered-validation.md
[spec-7]: https://github.com/covjson/specification/blob/master/spec.md#7-extensions
[spec-7.1]: https://github.com/covjson/specification/blob/master/spec.md#71-custom-members
[spec-3]: https://github.com/covjson/specification/blob/master/spec.md#3-parameter-objects
[spec-5.1.4]: https://github.com/covjson/specification/blob/master/spec.md#514-providing-inline-definitions-of-crss
