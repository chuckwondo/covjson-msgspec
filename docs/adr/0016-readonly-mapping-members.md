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
msgspec-decodable frozen mapping" -- true of the *runtime* type, but it conflated
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

**Keep `dict`.** Rejected: it is exactly the leak above -- a frozen model whose
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

## Consequences

- Mutating a mapping member is a static error under mypy and basedpyright
  (`Unsupported target for indexed assignment`). Runtime behavior is byte-for-byte
  unchanged: decode, encode, equality, and construction (which still accepts a
  plain `dict`, since `dict` is a `Mapping`) are identical.
- Members stay unhashable, because the runtime `dict` is still mutable; this ADR
  does not change hashability. A `frozendict` runtime (#117) would.
- The change is annotation-only, so it carries no wire, data, or performance
  effect; it is a two-way door, reversible by relaxing the annotation.
- **Return types are not blanket-swept.** A return is narrowed to read-only only
  when nothing that consumes it requires a concrete type. Returns that feed
  external plumbing stay concrete `dict` / `list`: the FastAPI `openapi()` hook
  merges its result in place, `xarray`'s `attrs=`, GeoJSON feature dicts. But a
  builder that returns a read-only *domain* type is consistent and retained. For
  example, `i18n()` returns `I18n` (a `Mapping`), matching how the value is typed
  in every field that stores it, so a just-built i18n object is immutable end to
  end.
- Revisit gate: adopt a `frozendict` runtime (#117) once the Python floor reaches
  3.15 and msgspec can decode it.

[pep-814]: https://peps.python.org/pep-0814/
