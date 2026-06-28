# ADR-0002: Cross-cutting checks in opt-in `validate()`, not `__post_init__`

## Status

Accepted

## Context

CoverageJSON has correctness rules at different scopes. Some are local to a
single struct (an `Axis` needs exactly one form; a `Unit` needs a label or a
symbol; a `TiledNdArray` tile shape has a fixed rank). Others are cross-cutting
and only checkable with a whole-document view (a domain's axes against its
`domainType`, ranges aligned to their domain, parameter-group members against
the coverage's parameters, and -- when scanning values -- each value against its
range's `dataType` and categorical codes being defined).

The library validates in three tiers: (1) structural and field-level checks by
msgspec on decode; (2) cheap local per-struct invariants in each
`__post_init__`; (3) cross-cutting document-level rules in `validate`. A natural
question is why tier 3 is not simply folded into tier 2 -- run (almost) all
validation at construction, excluding only the costly range-value scan. The
answer is not about cost. The split is local vs. cross-cutting, and
`__post_init__` is the wrong instrument for cross-cutting checks for reasons
that have nothing to do with how expensive they are.

This ADR records the tiering decision and its rationale. How `validate` is
implemented internally (the shape of its traversal) is out of scope.

## Decision

Keep `__post_init__` for local, O(1), single-struct invariants only. Put every
cross-cutting, document-level rule in an opt-in `validate()` that returns a
collection of `Issue` records -- each with a stable `code`, a JSON Pointer
`path`, and a severity -- and never raises on its own.

Severity tracks the spec's own normative force. An *error* marks a MUST / MUST
NOT violation: the document is non-conformant, or not usable as the type it
claims to be. A *warning* marks a SHOULD / RECOMMENDED violation: the document
is still spec-conformant but does something the spec discourages -- for example
a domain missing the recommended `domainType`, or a temporal value outside
ISO 8601 lexical form. Retaining both severities is a deliberate choice; the
errors-only alternative is discussed below.

A caller who wants decode to enforce conformance composes the two explicit
steps; `validate` also offers a `mode="raise"` (which raises only on
error-severity findings) and a `check_values=True` for the costly value scan
that is skipped by default.

## Alternatives considered

**Fold the cross-cutting checks into `__post_init__` (validate at construction,
minus the range-value scan).** Rejected, because `__post_init__` cannot express
what these checks require:

- *It cannot surface a non-fatal finding (a warning).* SHOULD-level findings
  (e.g. a domain missing the recommended `domainType`) are non-fatal by
  definition: the document is still conformant. `__post_init__` has exactly one
  way to report anything: raise, which rejects the object. There is no channel
  for "construct this, but flag it," so folding the checks in would force every
  SHOULD violation to be fatal-or-silent, collapsing a deliberate distinction.
  (A constructor *can* collect several errors and raise them together -- that is
  not the obstacle; returning a finding without aborting is.)
- *Raising is all-or-nothing: it cannot hand back a usable-but-imperfect
  object.* `validate` returns its findings as `Issue` values -- each with a
  stable code and a JSON Pointer path -- alongside the decoded object, so a
  caller can inspect them programmatically, filter by severity, or act on some
  and ignore others while still holding the object. A raising constructor
  forecloses that: you get a fully conformant object or an exception, nothing in
  between. This is the same errors-as-values stance the core takes elsewhere.
- *It runs unconditionally on every decode and construct, with no opt-out.* The
  common path (load, then read or transform) should stay fast; even the
  structural cross-checks add per-object work a caller who never
  conformance-checks should not pay (e.g. a large `CoverageCollection`). The
  range-value scan is the most costly part, but the unconditional-cost objection
  applies to the structural cross-checks too.
- *It would forbid legitimate transient states.* Objects are supposed to pass
  through intermediate, not-yet-conformant states: `resolve_references` builds a
  coverage with a URL-string domain and only then fetches and replaces it;
  `isel`/`sel` deliberately keep `domain_type` unchanged when a dimension is
  dropped (a Grid reduced to a point still says "Grid"); the xarray and pandas
  bridges assemble partial structs while rebuilding documents. A hard-failing
  constructor would break all of these mid-operation.
- *Permissive loading is a feature.* The test corpus intentionally decodes and
  round-trips non-conformant fixtures so a user can load a broken document to
  inspect or repair it, with `validate` reporting the issues. A rejecting
  constructor would prevent even reading such a document.

The local invariants already in `__post_init__` belong there for the
mirror-image reason: a single struct's own rules are knowable from its own
fields and always true, so they are cheap and unconditional by nature.

**Drop the warning severity: report only errors (a pure conformance checker).**
Rejected. The argument for it is that conformance is binary per finding --
either the document violates the spec (an error) or it does not (nothing to
report) -- so a "warning" implies a third, incoherent "reportable yet
compliant" state. The
flaw is that the spec itself defines exactly that state: a SHOULD / RECOMMENDED
requirement is, by RFC 2119, one a conformant document may violate. A document
missing the recommended `domainType`, or carrying a temporal value outside the
recommended ISO 8601 lexical form, is spec-conformant yet worth surfacing -- it
is a warning, not an error, and not nothing. Collapsing to errors-only would
either drop those findings or wrongly brand conformant documents as broken. (The
finding that prompted this question was that the two checks initially filed as
warnings were in fact misclassified MUST violations; the fix is to reclassify
*those* as errors (#35), not to remove the severity that genuine SHOULD-level
checks (#37) need.)

## Consequences

- Decode stays permissive: a document with only cross-cutting problems still
  decodes and round-trips, and conformance checking is an explicit, opt-in step.
- "I want decode that enforces conformance" composes already as decode followed
  by `validate(..., mode="raise")`. If that two-step proves to be a common
  papercut, the layering-preserving move is a thin opt-in convenience (a
  `strict=`/`validate=` flag on `decode`, or a `decode_strict` helper), not
  making every decode pay the cost and lose permissive loading. This is noted as
  a possible future ergonomic, not built.
- This mirrors the byte-faithful model tenet: a faithful, permissive core, with
  strictness confined to an explicit opt-in layer.
