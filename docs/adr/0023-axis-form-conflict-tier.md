# ADR-0023: Both axis forms is a `validate()` error, not a decode rejection

## Status

Accepted

## Context

Section 6.1.1 states: "An axis object MUST have either a `"values"` member or,
as a compact notation for a regularly spaced numeric axis, all the members
`"start"`, `"stop"`, and `"num"`." It never says "exactly one", and never says
which form wins when both are present. Reading them as exclusive is this
library's inference, recorded in `axis.py` by #206 and now carried on
`AxisFormConflict`.

`Axis.__post_init__` enforced that inference as an XOR, rejecting an axis
carrying both at decode. [ADR-0018](0018-typed-projection-scope.md) placed it
there by its "name the repair" criterion, listing "both axis forms present"
among the invariants with "zero or ambiguous repairs". That verdict was
reasoned, not measured.

#208 then found the XOR incomplete: `has_regular` required all three members, so
`values` plus a *partial* triple satisfied neither side. It decoded, drew no
finding, and re-encoded with both forms intact.

Measuring the case across implementations gives a different picture than
ADR-0018 assumed. covjson-msgspec at `main` (a97878d), covjson-pydantic 0.8.0,
covjson-reader 0.16.3, and the covjson-validator schemas at `b28a86e`, each run
against the same documents; spec text from `covjson/specification@2061005`:

| `x` axis | msgspec | pydantic | covjson-reader | validator |
| --- | --- | --- | --- | --- |
| `{"values":[0,5]}` | `[0, 5]` | `[0, 5]` | `[0, 5]` | VALID |
| `{"start":0,"stop":900,"num":3}` | `[0, 450, 900]` | unexpanded | `[0, 450, 900]` | VALID |
| `{}` | rejected | rejected | crash | INVALID |
| `{"start":0,"num":3}` | rejected | rejected | crash | INVALID |
| `{"values":[0,5], "start":0,"stop":900,"num":3}` | rejected | `[0, 5]` | `[0, 450, 900]` | INVALID |
| `{"values":[0,450,900], "start":0,"stop":900,"num":3}` | rejected | `[0,450,900]` | `[0,450,900]` | INVALID |
| `{"values":[0,5], "num":99}` | `[0, 5]` | `[0, 5]` | `[0, 5]` | INVALID |
| `{"values":[0,5], "start":99}` | `[0, 5]` | `[0, 5]` | `[0, 5]` | INVALID |
| `{"values":[0,5], "start":0,"stop":900}` | `[0, 5]` | `[0, 5]` | `[0, 5]` | INVALID |

Readers diverge on exactly one shape: `values` contradicted by a *complete*
triple. Where the triple reproduces `values`, all three agree, and every partial
mixed form is read identically everywhere (covjson-reader's expansion is gated
on all three members being present, so a partial triple is ignored). The
validator objects to all of them.

No document in reach carries a mixed form: zero across 175 axes in 99 documents
(`tests/corpus/` plus the covjson playground coverages).

## Decision

`Axis.__post_init__` enforces only what section 6.1.1 states: an axis must carry
`values` or all three of `start`/`stop`/`num`. Neither form, and an incomplete
triple, yield no coordinate values at all, so no coherent axis of that shape
exists, which is [ADR-0002](0002-opt-in-tiered-validation.md)'s criterion for
rejecting at construction. This is also covjson-pydantic's boundary.

Their exclusivity moves to `validate()` as `axis.form-conflict`, an
error-severity finding covering `values` alongside *any* triple member, complete
or not. The finding names the colliding members, since a stray `num` and a full
triple are the same rule but not the same repair.

The regular-form rules that stay at construction (`num` greater than zero, and
`num` of 1 implying `start == stop`) are gated on the axis's form actually being
regular. Section 6.1.1 states both unconditionally, in a bullet of its own, so
this is a deliberate narrowing rather than a reading of the text: beside
`values` the triple is not the axis's form, and its only repair is deletion, so
validating a stray's value would tell a publisher to fix a member they should
drop.

The rule is deferred, not dropped. `{"values":[0,5],"num":0}` is an error the
whole time it is ambiguous, via `axis.form-conflict`; repair that by dropping
`values` and construction rejects the `num` immediately, while dropping `num`
means the violation never existed. Without the gate the same defect would land
in two tiers by the stray's magnitude (`{"values":[0,5],"num":0}` rejected,
`{"values":[0,5],"num":99}` reported), which is the "tier turns on an accident"
shape the alternatives below reject, and the message would name `num` when the
defect is that two forms are present at all.

`coordinate_values` already resolved a both-forms axis to `values`; that
tiebreak is now documented on the accessor rather than left implicit behind a
guard that made it unreachable.

The severity is an error, not a warning, on ADR-0002's second clause: a document
whose triple contradicts its `values` is read as different data by different
consumers, so it is "not usable as the type it claims to be" even though each
consumer forms a coherent axis.

This record supersedes ADR-0018's classification of "both axis forms present" as
an ambiguous repair belonging at construction. The rest of ADR-0018 stands,
including its criterion, which is what produced this result when applied to
measured behavior rather than to an assumption.

## Alternatives considered

**Keep the guard at construction and close #208's hole there.** Rejected. The
measurement shows ADR-0018's "ambiguous" is true of one sub-case out of three: a
partial triple has exactly one repair (no regular axis is buildable from it) and
every implementation applies it, and a triple that reproduces `values` has two
repairs yielding the same data. Only a contradicting complete triple is
genuinely ambiguous, and that is the one case construction cannot detect:
telling it apart means reconstructing `start + i * step` and comparing floats,
an O(num) scan, which ADR-0002 reserves for the opt-in pass. So construction can
only approximate the rule by rejecting a superset that includes shapes nothing
disagrees about. That is the over-strict guard ADR-0018 itself warns against
("An over-strict guard is not a conservative guard: it rejects conformant
documents while the malformation it was mistaken for walks past"), and #208 is
that warning coming true in the same struct.

**Split the tier by whether the triple is complete.** Rejected, though it tracks
the measured divergence most closely. The predicate that would make it
principled is consistency, not completeness, and consistency is the O(n) check
construction may not host. Splitting on completeness instead would make the tier
depend on which members a publisher happened to supply, which is the shape of
the bug #208 reports.

**Report it as a warning.** Rejected. A warning marks a document that is
conformant but discouraged. This one is read as different data by different
conformant readers, which is a correctness problem for any consumer downstream
of it, and `validate(mode="raise")` should catch it.

**Normalize on decode: drop the stray members and keep `values`.** Rejected. It
would agree with every reader's behavior, but decode dropping a spec-defined
member is exactly what the byte-faithful model tenet forbids. `num` is not a
custom member ([ADR-0012](0012-custom-members-dropped-on-decode.md)); silently
discarding it would make `decode` lossy and hide the non-conformance instead of
reporting it.

## Consequences

- A document carrying both forms now decodes and round-trips faithfully. A
  caller who never calls `validate` gets our reading, `values`, where
  covjson-reader would give another. The finding is error-severity, so
  `validate(mode="raise")` rejects it.
- Loosening decode is a compatible change; tightening it back would not be.
  Nothing published carries a mixed form (zero across 175 axes), and this lands
  before 0.1.0, so the walk-back cost is bounded to that window.
- The rule gains a machine-readable home. `AxisFormConflict` carries its
  derivation where mkdocstrings and the conformance matrix (#150) can reach it,
  which a construction-tier guard structurally could not offer.
- The distinction the measurement found, a contradicting triple versus a
  redundant one, is now expressible: `validate` can afford the O(num)
  reconstruction if the two ever warrant different severities. Not built; the
  gate to revisit is a real document relying on the redundant form.
- `ADR-0018`'s invariant table no longer lists "exactly one form" as
  construction-enforced, which strengthens rather than weakens its conclusion
  that `Axis` needs no typed projection: the guarantee now has a `validate`
  home instead of none.
