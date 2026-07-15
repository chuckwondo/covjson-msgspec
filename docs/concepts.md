# Core concepts

covjson-msgspec maps the CoverageJSON model onto a set of precise, immutable
Python types. This page explains that type design: which spec object becomes which
type, and how each maps the JSON to a spec-compliant msgspec struct. It stays
scoped to the type design; the [specification][spec], linked throughout, is the
authority on the format itself, and the [API reference](reference/coverage.md) has
the exhaustive members.

snake_case attribute names map to CoverageJSON's lowerCamelCase wire names
automatically (`data_type` is `dataType`, `domain_type` is `domainType`). You
write Python; the codec speaks CoverageJSON.

## The mapping

Each CoverageJSON object maps to a single library type:

| CoverageJSON object | Library type |
| --- | --- |
| [Coverage][spec-coverage] | [`Coverage`](reference/coverage.md) |
| [CoverageCollection][spec-collection] | [`CoverageCollection`](reference/coverage.md) |
| [Domain][spec-domain] | [`Domain`](reference/domain.md), with a builder per domain type |
| [Axis][spec-axis] | [`Axis`](reference/domain.md) |
| [NdArray][spec-ndarray] | [`NdArray`](reference/range.md) |
| [TiledNdArray][spec-tiled] | [`TiledNdArray`](reference/range.md) |
| [Reference systems][spec-refsystems] | [`ReferenceSystem`, its `refine()` variants, `Concept`](reference/referencing.md) |
| [Parameter][spec-parameter] | [`Parameter`, `ObservedProperty`, `Category`, `Unit`, `Symbol`](reference/parameter.md) |
| [ParameterGroup][spec-paramgroup] | [`ParameterGroup`](reference/parameter.md) |
| [i18n string][spec-i18n] | [`I18n`](reference/parameter.md) |

The five types at the document root (`Coverage`, `CoverageCollection`, and the
sub-documents `Domain`, `NdArray`, and `TiledNdArray`) are what a CoverageJSON
document can *be*; everything else is a value composed inside them (an axis, a
parameter, a reference system).

Most types transcribe their spec object field-for-field, with no notable modeling
decision, so the mapping table and the [API reference](reference/coverage.md) cover
them:

- **[`Domain`](reference/domain.md)** ([§6.1][spec-domain]): a container of a
  `domain_type`, a map of `axes`, and `referencing`; its interesting parts (the
  axes and reference systems it holds) are covered elsewhere, and its
  per-domain-type builders follow the same narrow-builder pattern as `Axis`.
- **[`TiledNdArray`](reference/range.md)** ([§6.3][spec-tiled]): the same range
  family as `NdArray`, but with `values` split across tile documents. The struct is
  straightforward; the interest is behavioral (assembling the tiles), covered in
  the assembly guide.
- **[Reference systems](reference/referencing.md)** ([§5][spec-refsystems]): a
  permissive `ReferenceSystem` core that decodes any system (including a custom
  §7.2 type), with an opt-in `refine()` projection to precise per-kind variants
  (`GeographicCRS`, `TemporalRS`, `IdentifierRS`, ..., or an opaque `OpaqueRS`)
  plus the categorical `Concept`.
- **[`Parameter`](reference/parameter.md)** ([§3][spec-parameter]) and its parts
  (`ObservedProperty`, `Category`, `CategoryEncoding`, `Unit`, `Symbol`): direct
  transcriptions; the one local invariant is that a categorical `ObservedProperty`
  must list its `categories`.
- **[`ParameterGroup`](reference/parameter.md)** ([§4][spec-paramgroup]): a direct
  transcription of the group's members.
- **[`I18n`](reference/parameter.md)** ([§2][spec-i18n]): a language-tag to string
  map; its one choice, langcode validation, is noted under Faithful by default.

The sections below walk the remaining types, where mapping the JSON to a struct
involved a real choice, showing the wire JSON alongside the struct.

## Coverage

On the wire, a `Coverage` ([§6.4][spec-coverage]) is a `domain` paired with its
`ranges`:

```json
{
  "type": "Coverage",
  "id": "https://example.org/coverages/1",
  "domain": {
    "type": "Domain",
    "domainType": "Point",
    "axes": {"x": {"values": [1]}, "y": {"values": [2]}}
  },
  "parameters": {
    "temperature": {
      "type": "Parameter",
      "observedProperty": {"label": {"en": "Air temperature"}}
    }
  },
  "ranges": {
    "temperature": {
      "type": "NdArray",
      "dataType": "float",
      "values": [280.0]
    }
  }
}
```

The spec requires `domain` and `ranges`, plus `parameters` unless the coverage
inherits it from an enclosing collection; the `domain` MAY be an inline object *or*
a URL string that references one, and `id`, the coverage-level `domainType`, and
`parameterGroups` are optional (the JSON above sets the common members). The struct
maps that shape directly:

```python
class Coverage(CovJSONStruct, frozen=True, tag="Coverage"):
    domain: Domain | str  # inline, or a URL to resolve
    ranges: dict[str, Range]  # NdArray | TiledNdArray
    id: str | None = None
    domain_type: str | UnsetType = UNSET  # wire: domainType
    parameters: dict[str, Parameter] | UnsetType = UNSET
    parameter_groups: tuple[ParameterGroup, ...] | UnsetType = UNSET
```

- `tag="Coverage"` supplies the `"type"` discriminator, so the root tagged union
  (`Coverage | CoverageCollection | Domain | NdArray | TiledNdArray`) decodes on
  it. `decode` returns the matching type; `decode_coverage` is the typed entry
  point when you know which you hold.
- `domain: Domain | str` mirrors the spec's "inline or referenced" allowance
  exactly: a decoded domain stays a URL string until you resolve it.
- The three inheritance members are `X | UnsetType`, not `X | None`, so an
  *omitted* member (inherit from the collection) stays distinct from a
  spec-forbidden `null`
  ([ADR-0013](adr/0013-unset-for-omittable-inheritance-members.md)).

!!! note "`UNSET` versus `None`, for msgspec newcomers"

    `UNSET` is [msgspec](https://jcristharif.com/msgspec/)'s sentinel for a member
    that was *absent* from the JSON. A field typed `X | UnsetType` decodes to
    `UNSET` when the key is missing, to the value when the key is present, and
    *rejects* an explicit `null`; on encode, an `UNSET` field is omitted again.
    That gives three distinguishable states (present, `null`, absent) where
    `X | None` gives only two (a bare `None` cannot tell "absent" from an explicit
    "null"). The library reserves `UNSET` for the few members where that
    difference is load-bearing, and keeps the idiomatic `None` everywhere else.

## CoverageCollection

A `CoverageCollection` ([§6.5][spec-collection]) groups coverages and may declare
shared members once:

```json
{
  "type": "CoverageCollection",
  "domainType": "Point",
  "parameters": {
    "temperature": {
      "type": "Parameter",
      "observedProperty": {"label": {"en": "T"}}
    }
  },
  "coverages": [
    {
      "type": "Coverage",
      "domain": {
        "type": "Domain",
        "domainType": "Point",
        "axes": {}
      },
      "ranges": {}
    }
  ]
}
```

When the collection declares `domainType` or `parameters`, each member coverage
inherits them (and a member's own value, if present, MUST match). The struct gives
those shared members the same `UNSET` treatment (and carries optional
`parameterGroups` and `referencing` as well), and `resolved_coverages()` applies
the inheritance:

```python
class CoverageCollection(CovJSONStruct, frozen=True, tag="CoverageCollection"):
    coverages: tuple[Coverage, ...]
    domain_type: str | UnsetType = UNSET  # declared once, inherited
    parameters: dict[str, Parameter] | UnsetType = UNSET  # inherited
    parameter_groups: tuple[ParameterGroup, ...] | UnsetType = UNSET
    referencing: tuple[ReferenceSystemConnection, ...] = ()
```

`UNSET` is what makes inheritance correct: a member that omits `parameters`
inherits the collection's, while a member that writes `"parameters": null` is
rejected at decode. `X | None` would collapse those two into one `None` and
silently graft inherited data onto the `null` member
([ADR-0013](adr/0013-unset-for-omittable-inheritance-members.md)).

## Axis

An `Axis` ([§6.1.1][spec-axis]) is the most interesting mapping: the spec allows
one axis object to take three different shapes. A *listed* axis gives explicit
values:

```json
{ "values": [50.0, 51.0, 52.0] }
```

a *regular* axis gives a compact evenly-spaced triple:

```json
{ "start": 0.0, "stop": 10.0, "num": 11 }
```

and a *composite* axis carries tuples with named `coordinates`:

```json
{
  "dataType": "tuple",
  "coordinates": ["x", "y"],
  "values": [[1, 2], [3, 4]]
}
```

The spec requires **exactly one** of `values` (a non-empty array) or the
`start` / `stop` / `num` triple; if `num` is 1 then `start` and `stop` MUST be
equal; a `tuple` or `polygon` axis MUST supply `coordinates`; and an optional
`bounds` array may accompany any form. All three forms map to one permissive
struct, because the forms share no `"type"` discriminator and msgspec cannot decode
an untagged union of structs:

```python
class Axis(CovJSONStruct, frozen=True):
    values: tuple[AxisValue, ...] | None = None
    start: float | None = None
    stop: float | None = None
    num: int | None = None
    data_type: str | None = None  # wire: dataType
    coordinates: tuple[str, ...] | None = None
    bounds: tuple[float | str, ...] | None = None

    def __post_init__(self) -> None:
        # Enforce the spec-6.1.1 MUSTs that leave an axis uninterpretable if
        # violated: exactly one of values / start-stop-num, non-empty values,
        # num == 1 implies start == stop, tuple/polygon requires coordinates.
        ...
```

- Every field is optional, and `__post_init__` enforces the "exactly one form"
  MUST (and the `num == 1 ⇒ start == stop` MUST) at construction. These are local,
  O(1) invariants that leave the axis uninterpretable if violated, so they belong
  at construction rather than in `validate()`
  ([ADR-0002](adr/0002-opt-in-tiered-validation.md) draws that line).
- You do not build an `Axis` by hand from raw fields: the named builders
  `Axis.listed`, `Axis.regular`, `Axis.tuple_`, and `Axis.polygon` construct a
  valid form directly, so an illegal combination is never expressible.
- One struct with precise construction, rather than a subclass per form, is the
  same "typed projection over a faithful core" choice made for `NdArray`
  ([ADR-0004](adr/0004-ndarray-single-non-generic-class.md)).

## NdArray

An `NdArray` ([§6.2][spec-ndarray]) is a parameter's dense data, flattened:

```json
{
  "type": "NdArray",
  "dataType": "float",
  "axisNames": ["y", "x"],
  "shape": [1, 2],
  "values": [1.5, null]
}
```

The spec requires a `dataType` of `float`, `integer`, or `string`; `values` is a
flat, row-major array whose length MUST equal the product of `shape`, with `null`
for a missing datum; `shape` and `axisNames` MAY be omitted for a single 0-d value.
The struct carries the element type as a *field*, not a subclass:

```python
class NdArray(CovJSONStruct, frozen=True, tag="NdArray"):
    data_type: Literal["float", "integer", "string"]  # wire: dataType
    values: tuple[float | int | str | None, ...]  # flat, row-major
    shape: tuple[int, ...] = ()
    axis_names: tuple[str, ...] = ()  # wire: axisNames
```

- One non-generic class with `data_type` as a field, rather than
  `NdArrayFloat` / `NdArrayInt` / `NdArrayStr` subclasses or a generic
  `NdArray[T]` ([ADR-0004](adr/0004-ndarray-single-non-generic-class.md)).
- Precision is opt-in, on the read side: the stored `values` union stays
  `float | int | str | None`, and [`values_as`](reference/range.md) projects it to
  a precise element tuple when you know the `dataType`. `values_as(float)` returns
  `tuple[float | None, ...]`, promoting integer-written values as the spec's
  `dataType` allows and raising fail-fast on a mismatch (where
  `validate(check_values=True)` instead *reports* the same mismatch). This is the
  "view you ask for" half of typed projection: faithful in storage, precise on
  demand.
- Decode enforces only what is local and cheap: the `float | int | str | None`
  union rejects a nested array or a boolean. The cross-cutting checks (the
  `values` count versus `shape`, the `shape` rank versus `axisNames`, the element
  type versus `dataType`) are inconsistent-but-loadable, so they are deferred to
  opt-in `validate(check_values=True)` rather than made decode errors
  ([ADR-0002](adr/0002-opt-in-tiered-validation.md)). A slightly-off array still
  loads.

## Faithful by default

Across all of these, decoding reproduces the spec members it models faithfully. The
recurring temptation is to parse on the way in, most visibly turning temporal
strings into `datetime`. The library resists it, because many valid CoverageJSON
instants do not fit `datetime` (a year `0000`, non-Gregorian calendars), so parsing
at decode would reject faithful data. Temporal strings stay raw, and `resolve()`
returns a faithful
[`TemporalResult` sum type](adr/0008-temporal-conversion-result-projection.md) on
request. Language-tagged text (`I18n` maps such as `{"en": "Air temperature"}`) is
validated [with langcodes](adr/0005-langcodes-core-dependency.md).

The deliberate, permanent exception is
[custom members](adr/0012-custom-members-dropped-on-decode.md)
([§7.1][spec-custom]): extension keys the spec permits but does not define, which
decode drops rather than captures. A modeled spec member survives a decode /
encode round trip; a custom member does not. To relay a document with its
extensions intact, forward its raw bytes instead of decoding and re-encoding.

The root JSON-LD [`@context`][spec-8] (§8) is a modeled member: it round-trips
faithfully (an IRI string, an inline context object, an array of those, or
`null`) rather than being dropped. Custom (URI) reference-system types
([§7.2][spec-72]) also load: a reference system decodes into a permissive
`ReferenceSystem`, which `refine()` projects to a precise per-kind variant (an
opaque one for an unrecognized `type`). Its `type` round-trips; any custom
members on it drop, as above.

The [design decisions](adr/README.md) hold the full rationale behind these choices.

[spec]: https://github.com/covjson/specification/blob/master/spec.md
[spec-coverage]: https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects
[spec-collection]: https://github.com/covjson/specification/blob/master/spec.md#65-coverage-collection-objects
[spec-domain]: https://github.com/covjson/specification/blob/master/spec.md#61-domain-objects
[spec-axis]: https://github.com/covjson/specification/blob/master/spec.md#611-axis-objects
[spec-ndarray]: https://github.com/covjson/specification/blob/master/spec.md#62-ndarray-objects
[spec-tiled]: https://github.com/covjson/specification/blob/master/spec.md#63-tiledndarray-objects
[spec-refsystems]: https://github.com/covjson/specification/blob/master/spec.md#5-reference-system-objects
[spec-parameter]: https://github.com/covjson/specification/blob/master/spec.md#3-parameter-objects
[spec-paramgroup]: https://github.com/covjson/specification/blob/master/spec.md#4-parametergroup-objects
[spec-i18n]: https://github.com/covjson/specification/blob/master/spec.md#2-i18n-objects
[spec-custom]: https://github.com/covjson/specification/blob/master/spec.md#71-custom-members
[spec-72]: https://github.com/covjson/specification/blob/master/spec.md#72-custom-types
[spec-8]: https://github.com/covjson/specification/blob/master/spec.md#8-json-ld
