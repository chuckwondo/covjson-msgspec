# ADR-0016: Read-only `Mapping` annotations for frozen structs' mapping members

## Status

Accepted

## Context

Every model struct is `frozen=True`. Frozen protects the field *binding*: an
attribute cannot be rebound after construction. It does nothing for the *contents*
of a container a field holds. A mapping member typed `dict` therefore leaks
mutation: `coverage.ranges["x"] = ...`, `domain.axes.pop(...)`, or
`parameter.label["fr"] = ...` all mutate a supposedly-frozen value in place, with
no type error and no runtime guard.

Sequence members already sidestep this: they are modelled as `tuple`, immutable at
runtime. Mapping members had stayed `dict` on the rationale that "there is no
msgspec-decodable frozen mapping", true of the *runtime* type, but it conflated
a read-only *annotation* with an immutable *runtime object*. msgspec decodes an
abstract `collections.abc.Mapping[K, V]` to a plain `dict` at runtime (verified),
so the annotation and the runtime type are separable.

## Decision

Type mapping members as read-only `Mapping[K, V]`. The runtime is unchanged
(msgspec still builds a `dict`), but a static checker now rejects in-place
mutation of a frozen struct's mapping member.

The internationalized-string maps (`label` / `description`) flip at the single
`I18n` alias definition (`I18n = Mapping[str, str]`), converting every
`label` / `description` field at once; the five non-i18n members
(`Coverage.ranges` / `.parameters`, `CoverageCollection.parameters`,
`Domain.axes`, `IdentifierRS.identifiers`) change individually. Internal helpers
that *receive* one of these members widen their parameter type to `Mapping` to
match, aligning with the existing "parameters prefer read-only types" preference.

## Alternatives considered

**Keep `dict`.** Rejected: it is exactly the leak above, a frozen model whose
mapping contents are freely mutable, with the type system silent.

**Adopt a `frozendict` runtime now** ([PEP 814][pep-814], a genuinely immutable,
hashable mapping). Deferred, not refuted (#117). It is a Python 3.15 builtin; our
floor is 3.11 (titiler-gated, [ADR-0001](0001-python-3-11-floor.md)), and msgspec
has no native `frozendict` decode target. Because the members are annotated to the
abstract `Mapping`, adopting `frozendict` later is a drop-in runtime change under
the same annotations, so nothing here forecloses it.

**Decode to an immutable mapping via a `dec_hook`.** Rejected: a custom decode
path is a shadow codec kept in lockstep with the type structure forever, the same
standing cost [ADR-0012](0012-custom-members-dropped-on-decode.md) rejected for
extension capture. Not worth an immutability nicety.

**A `MappingProxyType` field.** Rejected: msgspec cannot decode to it, so it would
demand the same custom codec.

**The converse for sequences: annotate sequence members `Sequence[T]` rather than
`tuple[T, ...]`.** Rejected. msgspec decodes a variable-length `tuple` by building
a `list` and converting it, so a `Sequence[T]` member (which decodes to a plain
`list`, no conversion) is faster while the static type stays read-only: the same
annotation-versus-runtime split this ADR draws for mappings, applied the other
way. The payoff does not justify it. Measured on the real `NdArray.values` member
at the largest size benchmarked (40,000 floats, A/B in one session on one
machine): about 1495us to 1412us, or 1.07x, roughly 2ns per value; at a realistic
axis length (200) the saving is under a microsecond. Against that it would give up
two things permanently. It inverts the reasoning above: sequences are `tuple`
because they *can* be immutable at runtime, whereas mappings are `Mapping` only
because no decodable frozen mapping exists, so this trades a settled guarantee for
the compromise mappings are stuck with. And it forecloses half of #117.
`NdArray`, `Axis`, and `ReferenceSystemConnection` are hashable today precisely
because every member is a `tuple`; a `list` member makes them permanently
unhashable, so a `frozendict` runtime could no longer restore hashability across
the model. The mutability is real rather than theoretical: `frozen=True` blocks
rebinding but not `array.values[0] = 99.0`, which would falsify the design tenet's
promise that a value read from the model cannot be corrupted by a caller.

## Consequences

- Mutating a mapping member is a static error under mypy and basedpyright
  (`Unsupported target for indexed assignment`). Runtime behavior is byte-for-byte
  unchanged: decode, encode, equality, and construction (which still accepts a
  plain `dict`, since `dict` is a `Mapping`) are identical.
- Members stay unhashable, because the runtime `dict` is still mutable; this ADR
  does not change hashability. A `frozendict` runtime (#117) would.
- The change is annotation-only, so it carries no wire, data, or performance
  effect; it is a two-way door, reversible by relaxing the annotation.
- **Return types are not blanket-swept** (sharpened by the #119 amendment below,
  which does sweep returns to their read-only interface). A return is narrowed to
  read-only only when nothing that consumes it requires a concrete type. Returns
  that feed external plumbing stay concrete `dict` / `list`: the FastAPI
  `openapi()` hook merges its result in place, `xarray`'s `attrs=`, GeoJSON feature
  dicts. But a builder that returns a read-only *domain* type is consistent and
  retained. For example, `i18n()` returns `I18n` (a `Mapping`), matching how the
  value is typed in every field that stores it, so a just-built i18n object is
  immutable end to end.
- Revisit gate: adopt a `frozendict` runtime (#117) once the Python floor reaches
  3.15 and msgspec can decode it.

## Amendment (#119): the shape-based rule for parameters and returns

[#119](../../issues/119) extended "immutable by default" past the members this ADR
covers, to *parameters* and *return types*, and in doing so sharpened the
"return types are not blanket-swept" note above. The governing distinction is
**shape, not mutability**:

- A **variable-length homogeneous** run of values (whatever its concrete type:
  `list`, `tuple[X, ...]`, `set`) is annotated as the **read-only interface** in
  both parameter and return position: `Sequence[X]`, `Set[X]`, `Mapping[K, V]`. The
  runtime object is the strongest immutable available, so this ADR's per-kind
  honesty carries over: a `Sequence` return hands back a `tuple` and a `Set` return
  a `frozenset` (both genuinely immutable), while a `Mapping` return hands back a
  `dict` (annotation-only, until the #117 `frozendict`).
- A **fixed-arity product** (a fixed count of positional, possibly heterogeneous
  slots: `tuple[str, NdArray]`, the `(url, offsets)` tile pair,
  `tuple[int, int, int | None]`) stays a concrete `tuple`. It is its own identity,
  not a sequence.
- **Struct members** stay concrete `tuple[X, ...]` / `frozenset` even when
  variable-length: the one identity exception, since a member must be hashable and
  truly immutable for the frozen model (this ADR's Decision).

Parameters and returns get the *same* treatment here, though this ADR left sequence
members `tuple` and mapping members `Mapping`, for two reasons. A `Sequence` return
cannot feed a `tuple` parameter (a concrete `tuple` satisfies any interface
parameter, but not the reverse), so params and returns over the same data must
agree on the interface. And decoupling has more value on a return: widening a
parameter `tuple` to `Sequence` later is non-breaking, whereas changing a return's
concrete type breaks callers, so the interface is right for returns even where
`tuple` is the only immutable sequence and its "swap the container later"
flexibility is theoretical (for `dict` to `Mapping` it is not theoretical: that is
exactly what will make the #117 `frozendict` swap non-breaking).

This creates boundary conversions, all at construction sites: where a widened
`Sequence` value is stored into a `tuple` member, the constructor converts, e.g.
`NdArray(shape=tuple(shape), ...)` and `TileFailure(offsets=tuple(offsets))`.

Concrete types survive only for their true cause:

- A mapping an *internal* consumer **mutates** is the mutable *interface*
  `MutableMapping`, not a concrete `dict`: `_build_coords` returns one that
  `_build_variables` extends with a `crs` coordinate, and a `_Variable`'s attrs
  slot is `MutableMapping` because `grid_mapping` is `setdefault`-ed into it.
- A concrete `dict` survives in exactly one spot, and only because a
  **third-party framework** owns it: the nested `openapi()` hook in
  `add_openapi_schemas`, which FastAPI requires to return a `dict` and which we
  mutate in place (`setdefault` / `update` on FastAPI's own schema object). It is
  not on the public surface. Every other former dict return (`to_geojson`,
  `component_schemas`, `schema_ref`) returns a read-only `Mapping`: nothing
  consuming them needs a concrete `dict` (`dict.update` accepts any `Mapping`;
  `json.dumps` inspects the runtime object, still a `dict`), and the library's
  stance is to present read-only interfaces rather than invite mutation. A caller
  who wants a mutable structure builds one from the returned values.
- `validate()`'s return is *not* swept here. Its outcome model (a materialized
  report, a lazy stream, or a `ValidationReport` value) is a one-way public-API
  decision spun off to [#157](../../issues/157); `validation.py` is left as it
  stands.

The three-way rule, then: **parameters and returns take the read-only interface for
variable-length data (concrete `tuple` for fixed-arity products); struct members
stay concrete immutable.** This supersedes the "return types are not blanket-swept"
note above, which predated the shape framing.

[pep-814]: https://peps.python.org/pep-0814/
