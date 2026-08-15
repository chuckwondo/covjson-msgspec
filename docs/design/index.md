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

### The pointer in a checker doctest

**Convention:** a validation checker's doctest passes a root path (`()`), and
asserts the finding's typed payload where that payload is what identifies the
flagged thing. It keeps `.at` only where the pointer is itself what the checker
worked out.

**Why:** a finding carries two different kinds of information. Its `at` is a
JSON Pointer to the offending location, assembled from the `path` the checker
was handed plus whatever segments the checker appends as it descends. Its
remaining fields are the typed payload: `DomainMissingAxis.axis`,
`AxisBoundsLength.got`, `RangeValueTypeMismatch.value`.

A doctest that calls a checker directly has to supply that incoming `path`
itself, and it has nothing real to supply: in an actual run, the `_validate_*`
walk builds the path from the document's own keys. So the example invents one,
and the invention lands in the expected output:

```python
>>> [i.at for i in _parameter_i18n_issues(temp, ("parameters", "t"))]
['/parameters/t/unit/label/en_US']
```

Nothing in the example anchors that `"t"`, so a reader cannot tell where it came
from. Passing `()` drops the invented prefix and leaves exactly the segments the
checker itself contributed. What the example should assert then turns on one
question: *can this assertion fail?* Three cases come up.

**The payload, where it selects one item out of several.** This is the case the
convention is written for, and the flagged thing is named directly:

```python
>>> [issue.axis for issue in _unexpected_axis_issues(dom, "Grid", rule, ())]
['bogus']
```

That example's domain carries `x`, `y` and `bogus`. Only `bogus` comes back, so
the assertion breaks if the checker flags the wrong axis, or flags all three.

**Not the payload, where it merely echoes the input.** An i18n map with a single
key is the trap:

```python
>>> [i.lang for i in _unit_i18n_issues(bad, ())]  # rejected
['en_US']
```

`bad` is built one line above as `Unit(label={"en_US": "kelvin"})`, so the
output restates a literal already on screen. Empty the check's body and this
still passes. Assert `.at` here instead, which at a root path reads
`['/label/en_US']` and at least shows the descent into `label`.

**The pointer, where the pointer is the computation.** Some checkers do their
work *in* the path. `_parameter_i18n_issues` walks `observedProperty`, then its
own label, then `unit`, so `/unit/label/en_US` is the only executable evidence
that the finding came down the unit branch (`['en_US']` reads the same whichever
branch produced it). `_axis_monotonic_issues` puts the first out-of-order
position in the pointer, `/axes/x/values/2`, and nowhere else. `_ptr` and
`_escape` have RFC 6901 assembly as their entire subject. In all three, `.at`
stays, root-anchored.

The check that settles a doubtful case is to break the checker on purpose:
delete the segment it appends, or empty its body, then re-run the doctest. If it
still passes, the example was documenting nothing.
