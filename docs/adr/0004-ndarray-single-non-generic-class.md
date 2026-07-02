# ADR-0004: `NdArray` as a single, non-generic class

## Status

Accepted

## Context

CoverageJSON range arrays carry a `dataType` field with three possible values:
`"float"`, `"integer"`, and `"string"`. A range also has a flat `values` tuple
whose elements should match that `dataType`. Two questions arose when modeling
this in Python:

1. **One class or three element-typed subclasses?** covjson-pydantic models
   this as `NdArrayFloat`, `NdArrayInt`, `NdArrayStr` (three subclasses, each
   with a concrete element type on `values`). That makes the element type a
   first-class Python type distinction.

2. **If one class, should `NdArray` be generic?** An initial implementation
   added `Generic[T]` so callers could write `NdArray[float]` to get precise
   `.values` typing when the `dataType` is known ahead of time. The `TypeVar`
   used a PEP 696 `default=` (so bare `NdArray` still typed `.values` as
   `tuple[Scalar | None, ...]`) and a `bound=` (so msgspec enforced
   `float | int | str` on bare decode). However, this introduced an upstream
   msgspec quirk: the TypeVar resolution is cached per struct and the cache is
   order-sensitive, so `NdArray[int]` could silently accept a non-integer value
   if the bare `NdArray` decoder had been built first in the same process. The
   quirk was documented but could not be eliminated while keeping the generic.

## Decision

`NdArray` is a **single, non-generic class** with
`values: tuple[float | int | str | None, ...]`. The `dataType` field is a
`Literal["float", "integer", "string"]` on the class itself. Element-vs-dataType
consistency is a cross-cutting check in opt-in `validate(check_values=True)`,
not in decode (see ADR-0002).

## Alternatives considered

**Three element-typed subclasses (covjson-pydantic style).** Rejected for two
structural reasons:

- *msgspec union dispatch requires a single tag field with a unique value per
  union member.* All three subclasses would share `tag="NdArray"` (a collision)
  and cannot be re-tagged on `dataType` because the `Range` union
  (`NdArray | TiledNdArray | str`) dispatches on `"type"`, not `"dataType"`.
  A faithful subclass port would require a custom `dec_hook` (decode-then-
  convert), adding machinery the single-class model avoids.
- *Spec fidelity.* CoverageJSON uses `"type"` as the object discriminator
  (NdArray vs TiledNdArray) and `"dataType"` as a data attribute, not a type
  discriminator. Modeling it as a field mirrors the spec; subclasses are a
  pydantic-shaped reinterpretation of the wire format.

Subclasses also enforce element type at decode time, coupling validation into
parsing: the pattern ADR-0002 rejects in favour of opt-in `validate()`.

**Generic `NdArray[T]` with a PEP 696 `TypeVar`.** This was implemented and
then removed. Rejected because:

- *The cache quirk cannot be fixed within the generic.* msgspec lazily resolves
  a struct's TypeVar and caches the result. Once the bare `NdArray` decoder
  (TypeVar resolved to its `Scalar` bound) is built, a later `NdArray[int]`
  decode can reuse that cached resolution, silently accepting a non-integer. The
  quirk is
  inherent to generic msgspec Structs; keeping the generic means keeping the
  unreliability, however well it is disclaimed.
- *The ergonomic gain is narrow.* The type parameter gave precise `.values`
  typing only for standalone or freshly-constructed `NdArray` objects. The
  common path (decoding a `Coverage` and reading `.ranges`) always yields a
  bare `NdArray` (the `Range = NdArray | TiledNdArray | str` union is bare), so
  `.values` was already `tuple[float | int | str | None, ...]` there regardless
  of the generic. The bridges, `validate()`, and the subset module never used
  the type parameter.
- *Dependency cost.* `NdArray` was the only PEP 696 `TypeVar(default=...)` user
  in the library, so the generic was the sole reason for the conditional
  `typing-extensions` dependency and the `sys.version_info` import dance.
  Dropping the generic removes the dependency entirely.

**Typed-value accessors (demand-gated, not implemented).** If precise element
typing proves wanted in the future, the intended restoration is small accessors
(`float_values()`, `int_values()`, `string_values()`) that trust `dataType` by
default (O(1), no element scan) with an opt-in `strict=True` that validates
and coerces (O(n), reusing the `validate(check_values=True)` logic). This is
out of scope until there is a demonstrated need.

## Consequences

- `NdArray.values` is always `tuple[float | int | str | None, ...]`. A caller
  who needs a specific element type narrows at the read site, keyed on
  `data_type` (a `cast`, an `isinstance` guard, or `validate(check_values=True)`
  for the full cross-cutting check).
- Bare decode deterministically enforces the `float | int | str` union (nested
  arrays and booleans are rejected). This was true with the TypeVar bound too,
  but is now enforced directly by the field annotation (no cache, no ordering
  dependency).
- The `typing-extensions` dependency is removed from `pyproject.toml`.
- The design-decisions doc (#21) and the covjson-pydantic comparison (#22)
  reflect both choices (single class and non-generic). The `NdArray` docstring
  and the `_check_value_data_types` helper in `validation.py` no longer carry
  the cache-quirk caveat.
