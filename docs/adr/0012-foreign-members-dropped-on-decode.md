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

**A base-struct `extras` + a bespoke recursive encoder (complete capture).**
Deferred, not refuted. Putting `extras: dict[str, msgspec.Raw]` on
`CovJSONStruct` would capture foreign members uniformly at every node, closing
the leak. The cost is the encoder: `enc_hook` never fires for a `msgspec.Struct`
(msgspec encodes structs natively), so re-emitting captured members as siblings
requires a custom recursive walk (`to_builtins` per node, merge that node's
`extras`) kept in lockstep with the type structure forever. That standing
maintenance coupling is not worth paying for a hypothetical consumer. It is the
right design *only* for a caller that must decode a document, modify a typed
field, and re-encode while preserving foreign siblings, and no such consumer
exists today.

**Retain the raw parsed tree as the source of truth, the typed model as a
view.** Rejected. The model is `frozen`, so "modifying" a value means
constructing a new one; an overlay-on-encode step then cannot distinguish a
field the caller changed from one left alone, making the reconciliation
ambiguous.

## Consequences

- `decode -> encode` is lossy for foreign members. This is recorded as the one
  carve-out to the byte-faithful tenet (CLAUDE.md) and is the documented
  behavior of the corpus round-trip test.
- Lossless relay/proxy is a tool-choice, not a feature: forward raw bytes (or
  reformat the raw `dict[str, msgspec.Raw]` tree), never `decode -> encode`.
- [Section 5.1.4][spec-5.1.4] `cs` / `datum` inline CRS definitions stay
  unmodeled; CRSs are identified by `id`.
- Revisit gate: a concrete consumer that must decode, modify a typed field, and
  re-encode while retaining foreign siblings reopens the deferred base-struct
  `extras` design above. Absent that, the projection stays lossy on purpose.

[ADR-0002]: 0002-opt-in-tiered-validation.md
[spec-7]: https://github.com/covjson/specification/blob/master/spec.md#7-extensions
[spec-7.1]: https://github.com/covjson/specification/blob/master/spec.md#71-custom-members
[spec-3]: https://github.com/covjson/specification/blob/master/spec.md#3-parameter-objects
[spec-5.1.4]: https://github.com/covjson/specification/blob/master/spec.md#514-providing-inline-definitions-of-crss
