# Design decisions

This section records the durable, cross-cutting design decisions behind
covjson-msgspec: the recurring [design tenets](tenets.md) and the individual
[architecture decision records](../adr/README.md) (ADRs) that apply them.

## The tenets

The [design tenets](tenets.md) are the principles that recur across the library:
dependency injection at the edges, a functional core with an imperative shell,
immutable data by default, opt-in tiered validation, a byte-faithful model, and
typed projection over a faithful core.
[Tenets in practice](tenets-in-practice.md) illustrates each with a concrete
decision from the code.

## Where the decisions live

**The type model** (how each CoverageJSON object maps to a spec-compliant struct,
and why) is worked through in [Core concepts](../concepts.md): the single
non-generic `NdArray`, the one-struct `Axis`, `UNSET` for omittable inheritance
members, and the permissive-decode line.

**The ADRs** are the append-only detailed record; [the ADR index](../adr/README.md)
lists them all. A few of the load-bearing ones:

- [ADR-0002](../adr/0002-opt-in-tiered-validation.md): cross-cutting checks live in
  opt-in `validate()`, not `__post_init__`.
- [ADR-0004](../adr/0004-ndarray-single-non-generic-class.md): `NdArray` as a single
  non-generic class, element typing via `validate`.
- [ADR-0007](../adr/0007-functional-core-errors-as-values.md): best-effort fetching
  as a functional core, failures as values.
- [ADR-0008](../adr/0008-temporal-conversion-result-projection.md): temporal
  conversion as a faithful result projection.
- [ADR-0012](../adr/0012-custom-members-dropped-on-decode.md): custom members
  dropped on decode.
- [ADR-0013](../adr/0013-unset-for-omittable-inheritance-members.md): `UNSET` for
  omittable inheritance members.

## Format

Each ADR follows a lightweight template (Context, Decision, Alternatives
considered, Consequences). See [the ADR index](../adr/README.md) for the numbering
and conventions.

## Conventions, explained

Most of the coding conventions in `CONTRIBUTING.md` are self-evident one-liners. A
few carry reasoning worth spelling out; this is that reasoning.

### The two-underscore boundary

**Convention:** do not import another module's `_private` member; to share an
internal helper across modules, give it a home in a `_`-prefixed module and import
its non-underscore name.

**Why:** the two underscores mark different boundaries. A `_` on a *member* means
"private to this module" (only that file uses it); a `_` on a *module* means
"internal to the package" (its non-underscore names are the intra-package API,
off-limits to end users). Keeping them distinct means every module-local `_helper`
stays genuinely file-local: safe to rename or inline after grepping a single file.
Ruff's PLC2701 enforces the neighboring rule (it bans importing *another package's*
privates), but not this intra-package case, so review has to catch it.
