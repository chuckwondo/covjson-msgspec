"""Document-level validation beyond what decoding already guarantees.

CoverageJSON is validated in tiers:

1. structural and field-level checks, performed by msgspec on decode;
2. cheap per-object invariants, performed in each struct's ``__post_init__``
   (e.g. a `Unit` needs a label or symbol); and
3. cross-cutting, document-level rules, performed here by `validate`.

Tiers 1 and 2 stay deliberately local: they reject only what a single struct
cannot be valid without, so a document with merely cross-cutting problems still
decodes and round-trips (you can load an imperfect document to inspect or repair
it). The tier-3 rules span objects and need the whole-document view plus a
collect-all, error-or-warning result that a fail-fast ``__post_init__`` cannot
give, so they are an opt-in step rather than enforced at decode.

`validate` collects `Issue` findings rather than raising on the first problem, so
a caller sees every error and warning at once. `Issue` is a closed union of one
frozen struct per finding kind (`DomainMissingAxis`, `NdArrayValueCount`, ...),
each carrying its substitution values as typed fields, a human message via
``str(issue)``, a stable string ``code``, and a JSON Pointer ``at``. Match on the
concrete variant (``match`` / `~typing.assert_never` for exhaustiveness, or
``isinstance`` to read a variant's typed payload) and use ``code`` for stringly
work (aggregation, logging, the wire tag).

A ``code`` is ``<category>.<key>``, and the category names the object whose rule
was broken, not the object it was reached through. So ``axis.*`` covers the Spec
6.1.1 rules a single axis object must satisfy (its values ordered monotonically),
while ``domain.*`` covers the domain-type rules over a domain's axis *set* (a
required axis is absent, an axis the type wants single-valued carries several)
and the rules about the domain itself. An axis is only ever reached through a
domain, so what settles the category is what the rule is *about*: ``ndarray.*``,
``range.*``, ``i18n.*``, and ``parameter-group.*`` follow the same grain.

A rule spanning two objects belongs to the one whose invariant it is, which is
the container: ``coverage.range-shape-mismatch`` is a coverage rule (its range
and domain must agree) even though it reports on a range, while
``range.value-type-mismatch`` is intrinsic to a range and stays ``range.*``.
The ``axis.*`` / ``domain.*`` split is that same shape one level down.

Pass ``mode="raise"`` to raise a `CovJSONValidationError` instead when any
error-severity issue is found, and ``check_values=True`` to add the
value-scanning checks that are skipped by default (each value matching its
range's ``dataType``, and categorical codes being defined).

The per-domain-type axis rules live in the `DOMAIN_TYPE_RULES` registry, keyed by
`DomainType`. The registry is a plain dict: add or replace an entry to teach
`validate` about a custom domain type or to override a built-in rule.

Spec: [CoverageJSON](https://github.com/covjson/specification/blob/master/spec.md)
(section references in this module, e.g. "Spec 6.1", point here) and
[Common Domain Types](https://github.com/covjson/specification/blob/master/domain-types.md).
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cache
from itertools import chain, pairwise
from typing import Any, Literal, assert_never, cast

import langcodes
import msgspec
from msgspec import UNSET

from covjson_msgspec._bridging import (
    coordinate_identifiers,
    coordinate_systems,
    is_standard_calendar,
)
from covjson_msgspec._reference_invariants import missing_required_member
from covjson_msgspec.axis import Axis, AxisValue
from covjson_msgspec.coverage import (
    Coverage,
    CoverageCollection,
    CoverageJSON,
)
from covjson_msgspec.domain import Domain
from covjson_msgspec.i18n import I18n
from covjson_msgspec.parameter import (
    Category,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Unit,
)
from covjson_msgspec.range import NdArray, TiledNdArray, TileSet, template_variables
from covjson_msgspec.referencing import (
    Concept,
    GeographicCRS,
    IdentifierRS,
    OpaqueRS,
    ProjectedCRS,
    ReferenceSystem,
    ResolvedReferenceSystem,
    TemporalRS,
    VerticalCRS,
)
from covjson_msgspec.temporal import Malformed, Moment, TemporalResult, resolve


class Severity(enum.StrEnum):
    """How serious a validation `Issue` is."""

    ERROR = "error"
    WARNING = "warning"


class _Issue(
    msgspec.Struct, frozen=True, kw_only=True, omit_defaults=True, tag_field="code"
):
    """Shared base for every validation finding (private; not a union member).

    Each concrete finding is a frozen struct subclassing this base and pinning a
    msgspec ``tag``: its stable, machine-readable ``code``
    (``"domain.missing-axis"``). The base supplies the two fields every finding
    carries (``at`` and ``severity``), the `code` accessor, and the
    ``tag_field="code"`` config that makes a ``list[Issue]`` a *tagged union*:
    it encodes to a machine-readable report and decodes back to the exact
    concrete variants (the ``code`` field is the discriminant).

    Consumers have two matching styles, and the split is the whole point:

    * Reach for the **type** (``match`` / ``isinstance``) for anything the type
      checker should verify: exhaustiveness via `~typing.assert_never`, reading
      a variant's typed payload (``issue.code == ...`` does *not* narrow the
      type), and refactor-safety.
    * Reach for **`code`** only for stringly work that leaves the type system:
      aggregating (``Counter(i.code for i in issues)``), logging, the wire tag,
      or a loose ``code == "domain.missing-axis"`` match by a consumer that never
      imports the variant classes.

    Attributes
    ----------
    at
        A JSON Pointer (RFC 6901) to the offending location, built via `_ptr`.
    severity
        `Severity.ERROR` (the default) or `Severity.WARNING`. An error finding
        omits this on the wire (the base sets ``omit_defaults``), so a report
        reader treats an absent ``severity`` as ``error``.

    Examples
    --------
    Aggregate by kind with the string ``code`` (work that leaves the types):

    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> sorted(i.code for i in validate(dom))
    ['domain.missing-axis', 'domain.missing-referencing']

    Narrow with the type to read a variant's typed payload (the string ``code``
    cannot do this: it does not tell the checker which variant you hold):

    >>> issue = validate(dom)[0]
    >>> issue.axis if isinstance(issue, DomainMissingAxis) else None
    'y'

    A report round-trips through JSON, the ``code`` tag discriminating on decode:

    >>> import msgspec
    >>> report = validate(dom)
    >>> msgspec.json.decode(msgspec.json.encode(report), type=list[Issue]) == report
    True
    """

    at: str
    severity: Severity = Severity.ERROR

    @property
    def code(self) -> str:
        """The stable string discriminant for this finding kind (the ``tag``)."""
        return cast("str", type(self).__struct_config__.tag)

    def __str__(self) -> str:
        raise NotImplementedError


class DomainMissingAxis(_Issue, frozen=True, tag="domain.missing-axis"):
    """A required axis for the domain's type is absent."""

    domain_type: str
    axis: str

    def __str__(self) -> str:
        return f"{self.domain_type} domain requires a {self.axis!r} axis"


class DomainAxisNotSingle(_Issue, frozen=True, tag="domain.axis-not-single"):
    """An axis the domain type requires to be single-valued carries several."""

    domain_type: str
    axis: str

    def __str__(self) -> str:
        return f"{self.domain_type} domain requires a single {self.axis!r} value"


class DomainCompositeDataType(_Issue, frozen=True, tag="domain.composite-data-type"):
    """The ``composite`` axis declares the wrong ``dataType`` for the domain type."""

    domain_type: str
    expected: Literal["tuple", "polygon"]

    def __str__(self) -> str:
        return f"{self.domain_type} domain requires a {self.expected!r} composite axis"


class DomainExtraAxisNotSingle(_Issue, frozen=True, tag="domain.extra-axis-not-single"):
    """A surplus axis outside the domain type's set is multi-valued (MUST be single)."""

    domain_type: str
    axis: str

    def __str__(self) -> str:
        return (
            f"{self.domain_type} domain may only add single-valued axes, "
            f"but {self.axis!r} has multiple values"
        )


class DomainMissingReferencing(_Issue, frozen=True, tag="domain.missing-referencing"):
    """The domain carries no ``referencing`` in scope."""

    def __str__(self) -> str:
        return "domain must have a 'referencing' member"


class DomainMissingDomainType(_Issue, frozen=True, tag="domain.missing-domain-type"):
    """The domain declares no ``domainType`` and none is inherited in scope.

    Spec 6.1 RECOMMENDS a domain object carry ``domainType`` for
    interoperability, so this is a warning, not an error (per ADR-0002). The
    effective type may be supplied by the domain itself, a coverage's own
    ``domainType``, or a collection's; this fires only when none is in scope.
    """

    severity: Severity = Severity.WARNING

    def __str__(self) -> str:
        return "domain should have a 'domainType' member"


class AxisNotMonotonic(_Issue, frozen=True, tag="axis.not-monotonic"):
    """A primitive axis's ``values`` are not ordered monotonically.

    Spec 6.1.1 (Axis Objects): when an axis is ``primitive`` and its reference
    system defines a natural ordering, its ``values`` MUST be ordered
    monotonically (increasing or decreasing), so this is an error (per ADR-0002).
    Which systems order, and whether equal-adjacent values are permitted, is the
    default policy of `require_monotonic`, replaceable via `validate`'s
    ``axis_order_checker``.
    """

    axis: str

    def __str__(self) -> str:
        return f"axis {self.axis!r} values must be ordered monotonically"


class AxisCompositeValueShape(_Issue, frozen=True, tag="axis.composite-value-shape"):
    """A composite axis's value is not the array its ``dataType`` demands.

    Spec 6.1.1 (Axis Objects): for ``"tuple"``, each axis value MUST be "an array
    of fixed size of primitive values in a defined order"; for ``"polygon"``, "a
    GeoJSON Polygon coordinate array". A value that is not an array at all
    violates either MUST, so this is an error (per ADR-0002).

    Decode cannot catch it: `~covjson_msgspec.axis.AxisValue` is one union serving
    primitive and composite axes alike, so nothing in the type ties a value's
    shape to the axis's ``dataType``. A custom dataType is never reported: 6.1.1
    grants only "Custom values MAY be used" and defines no value structure for
    one, so no MUST constrains it.
    """

    axis: str
    data_type: Literal["tuple", "polygon"]

    def __str__(self) -> str:
        shape = (
            "an array of primitive values"
            if self.data_type == "tuple"
            else "a GeoJSON Polygon coordinate array"
        )

        return (
            f"axis {self.axis!r} is {self.data_type!r}, so each value must be {shape}"
        )


class AxisCompositeArity(_Issue, frozen=True, tag="axis.composite-arity"):
    """A ``"tuple"`` axis's values do not match its coordinate identifier count.

    Spec 6.1.1 (Axis Objects): for ``"tuple"``, "the tuple size corresponds to the
    number of coordinate identifiers", so a mismatch violates a MUST and is an
    error (per ADR-0002). Left unreported, the bridges zip each tuple against the
    identifiers and silently drop every surplus component.

    The identifier count is the *resolved* one
    (`~covjson_msgspec._bridging.coordinate_identifiers`): an axis omitting
    ``coordinates`` defaults to a one-element array naming itself, so its tuples
    must be 1-tuples.

    ``"polygon"`` is deliberately outside this rule: its ``coordinates`` give "the
    order of coordinates" *within* each GeoJSON position, so a polygon value's
    length is its ring count and bears no relation to the identifier count.
    """

    axis: str
    expected: int
    got: int

    def __str__(self) -> str:
        return (
            f"axis {self.axis!r} has {self.got}-tuple values but "
            f"{self.expected} coordinate identifiers"
        )


class AxisBoundsLength(_Issue, frozen=True, tag="axis.bounds-length"):
    """An axis's ``bounds`` array is not twice the axis length.

    Spec 6.1.1 (Axis Objects): "An axis object MAY have axis value bounds defined
    in the member ``"bounds"`` where the value is an array of values of length
    ``len*2`` with ``len`` being the length of the ``"values"`` array." The MAY
    governs whether ``bounds`` is *present*; once present, the spec *defines* its
    length (two per axis value, a lower and an upper), so a wrong-length array
    fails to be a bounds array at all. There is nothing to interpret and the one
    repair is to drop it, which makes it an error (per ADR-0002), exactly as
    `Axis.__post_init__` rejects an empty ``values`` on the equally keyword-free
    "The value of ``"values"`` is a non-empty array of axis values".

    The length is stated over ``values``, and the regular (``start`` / ``stop`` /
    ``num``) form inherits it by derivation: a regular axis has no ``values``
    array, so ``len`` is read as ``num`` (the axis length), making the expected
    length ``2 * num``. That reading is a sound derivation, not 6.1.1 text, and
    the code labels it as derived rather than presenting it as spec wording.
    """

    axis: str
    expected: int
    got: int

    def __str__(self) -> str:
        return (
            f"axis {self.axis!r} has {self.got} bounds but must have "
            f"{self.expected} (two per axis value)"
        )


class TemporalMissingCalendar(_Issue, frozen=True, tag="temporal.missing-calendar"):
    """A temporal RS carries no ``calendar``.

    Spec 5.2: a temporal RS object MUST have a ``calendar`` member
    (``"Gregorian"`` or a URI), so this is an error (per ADR-0002). Decode stays
    permissive: a temporal system missing ``calendar`` still loads and refines
    to `~covjson_msgspec.referencing.OpaqueRS`, and this check reports it.
    """

    def __str__(self) -> str:
        return "temporal reference system must have a 'calendar' member"


class IdentifierMissingTargetConcept(
    _Issue, frozen=True, tag="identifier.missing-target-concept"
):
    """An identifier RS carries no ``targetConcept``.

    Spec 5.3: an identifier RS object MUST have a ``targetConcept`` member, so
    this is an error (per ADR-0002). Decode stays permissive: an identifier
    system missing ``targetConcept`` still loads and refines to
    `~covjson_msgspec.referencing.OpaqueRS`, and this check reports it.
    """

    def __str__(self) -> str:
        return "identifier reference system must have a 'targetConcept' member"


class NdArrayShapeRank(_Issue, frozen=True, tag="ndarray.shape-rank"):
    """An `NdArray`'s ``shape`` and ``axisNames`` differ in length."""

    def __str__(self) -> str:
        return "shape and axisNames must have the same length"


class NdArrayValueCount(_Issue, frozen=True, tag="ndarray.value-count"):
    """An `NdArray`'s value count disagrees with the product of its ``shape``."""

    expected: int
    shape: tuple[int, ...]
    got: int

    def __str__(self) -> str:
        return (
            f"expected {self.expected} value(s) for shape {self.shape}, got {self.got}"
        )


class TiledNdArrayShapeRank(_Issue, frozen=True, tag="tiled-ndarray.shape-rank"):
    """A `TiledNdArray`'s ``shape`` and ``axisNames`` differ in length."""

    def __str__(self) -> str:
        return "shape and axisNames must have the same length"


class TiledNdArrayTileShapeTooLarge(
    _Issue, frozen=True, tag="tiled-ndarray.tile-shape-too-large"
):
    """A ``tileShape`` element exceeds the corresponding ``shape`` element."""

    tile_dim: int
    dim: int

    def __str__(self) -> str:
        return f"tileShape element {self.tile_dim} exceeds shape element {self.dim}"


class TiledNdArrayTileShapeNotPositive(
    _Issue, frozen=True, tag="tiled-ndarray.tile-shape-not-positive"
):
    """A ``tileShape`` element is not a positive integer."""

    tile_dim: int

    def __str__(self) -> str:
        return f"tileShape element {self.tile_dim} must be a positive integer"


class TiledNdArrayUrlTemplateMissingVariable(
    _Issue, frozen=True, tag="tiled-ndarray.url-template-missing-variable"
):
    """The ``urlTemplate`` omits a variable for a subdivided axis."""

    axis: str

    def __str__(self) -> str:
        return (
            f"urlTemplate must contain a variable for the subdivided {self.axis!r} axis"
        )


class TiledNdArrayUrlTemplateUnknownVariable(
    _Issue, frozen=True, tag="tiled-ndarray.url-template-unknown-variable"
):
    """The ``urlTemplate`` names a variable that is not a subdivided axis."""

    variable: str

    def __str__(self) -> str:
        return (
            f"urlTemplate references {self.variable!r}, which is not a subdivided axis"
        )


class CoverageMissingParameters(_Issue, frozen=True, tag="coverage.missing-parameters"):
    """The coverage carries no ``parameters`` in scope."""

    def __str__(self) -> str:
        return "coverage must have a 'parameters' member"


class CoverageRangeWithoutParameter(
    _Issue, frozen=True, tag="coverage.range-without-parameter"
):
    """A range name matches no parameter in scope."""

    key: str

    def __str__(self) -> str:
        return f"range {self.key!r} has no matching parameter"


class CoverageRangeAxisNotInDomain(
    _Issue, frozen=True, tag="coverage.range-axis-not-in-domain"
):
    """A range axis names no axis of the domain."""

    axis: str

    def __str__(self) -> str:
        return f"range axis {self.axis!r} is not a domain axis"


class CoverageRangeShapeMismatch(
    _Issue, frozen=True, tag="coverage.range-shape-mismatch"
):
    """A range's size along an axis differs from the domain axis's length."""

    axis: str
    range_size: int
    domain_size: int

    def __str__(self) -> str:
        return (
            f"range axis {self.axis!r} has size {self.range_size} "
            f"but the domain axis has {self.domain_size}"
        )


# kw_only restated: overriding the inherited `severity` default drops the base's
# kw_only, which msgspec needs to allow the required `domain_type` after a
# defaulted field.
class CoverageDomainTypeNotOmitted(
    _Issue, frozen=True, kw_only=True, tag="coverage.domain-type-not-omitted"
):
    """A collection member repeats a ``domainType`` its collection already sets.

    Spec 6.4 says that when a coverage is part of a collection carrying
    ``domainType``, that member SHOULD be omitted in the coverage, so this is a
    warning (per ADR-0002). The member's value equals the collection's here; a
    *differing* value falsifies the collection's type claim and is the error
    `CoverageDomainTypeConflict` instead.
    """

    domain_type: str
    severity: Severity = Severity.WARNING

    def __str__(self) -> str:
        return (
            f"coverage should omit domainType {self.domain_type!r}; "
            "its collection already provides it"
        )


class CoverageDomainTypeConflict(
    _Issue, frozen=True, tag="coverage.domain-type-conflict"
):
    """A collection member's declared ``domainType`` contradicts its collection's.

    A collection's ``domainType`` indicates it contains only coverages of that
    type (Spec 6.5); a member whose declared type (at the coverage level or on its
    inline domain) differs falsifies that claim, so this is an error, not a mere
    SHOULD-omit warning. An equal, redundant coverage-level value is the warning
    `CoverageDomainTypeNotOmitted` instead.
    """

    domain_type: str
    collection_domain_type: str

    def __str__(self) -> str:
        return (
            f"coverage domainType {self.domain_type!r} conflicts with its "
            f"collection's {self.collection_domain_type!r}"
        )


class RangeValueTypeMismatch(_Issue, frozen=True, tag="range.value-type-mismatch"):
    """A range value does not match the declared ``dataType``."""

    value: float | int | str
    data_type: str

    def __str__(self) -> str:
        return f"value {self.value!r} is not a valid {self.data_type} value"


class RangeInvalidCategoryCode(_Issue, frozen=True, tag="range.invalid-category-code"):
    """A categorical range value is not a defined category code."""

    value: float | int | str

    def __str__(self) -> str:
        return f"value {self.value!r} is not a defined category code"


# kw_only restated: overriding the inherited `severity` default drops the base's
# kw_only, which msgspec needs to allow the required `value` after a defaulted field.
class TemporalLexicalForm(
    _Issue, frozen=True, kw_only=True, tag="temporal.lexical-form"
):
    """A time-axis value uses none of the Gregorian calendar's recommended forms.

    Spec 5.2 says these values SHOULD use one of the five ISO 8601 lexical forms,
    so this is a warning, not an error (per ADR-0002).
    """

    value: str
    severity: Severity = Severity.WARNING

    def __str__(self) -> str:
        return (
            f"value {self.value!r} does not use a recommended Gregorian temporal form"
        )


class ParameterGroupUnknownMember(
    _Issue, frozen=True, tag="parameter-group.unknown-member"
):
    """A parameter group references a member absent from ``parameters``."""

    member: str

    def __str__(self) -> str:
        return f"parameter group references unknown member {self.member!r}"


class I18nInvalidLanguageTag(_Issue, frozen=True, tag="i18n.invalid-language-tag"):
    """An i18n object key is not a valid BCP 47 language tag."""

    lang: str

    def __str__(self) -> str:
        return f"{self.lang!r} is not a valid BCP 47 language tag"


class I18nEmpty(_Issue, frozen=True, tag="i18n.empty"):
    """A present i18n object has no language-tagged entries."""

    def __str__(self) -> str:
        return "i18n object must have at least one language-tagged entry"


# The closed set of validation findings. `validate` returns a ``list[Issue]``;
# a consumer matches on the concrete variant (`match` / `assert_never` for
# exhaustiveness, or `isinstance` to read a variant's typed payload) and uses
# `~_Issue.code` only for stringly work (aggregation, logging, the wire tag).
# The union is a msgspec tagged union keyed on ``code``, so a report encodes to
# JSON and decodes back to these exact types.
Issue = (
    DomainMissingAxis
    | DomainAxisNotSingle
    | DomainCompositeDataType
    | DomainExtraAxisNotSingle
    | DomainMissingReferencing
    | DomainMissingDomainType
    | AxisNotMonotonic
    | AxisCompositeValueShape
    | AxisCompositeArity
    | AxisBoundsLength
    | TemporalMissingCalendar
    | IdentifierMissingTargetConcept
    | NdArrayShapeRank
    | NdArrayValueCount
    | TiledNdArrayShapeRank
    | TiledNdArrayTileShapeTooLarge
    | TiledNdArrayTileShapeNotPositive
    | TiledNdArrayUrlTemplateMissingVariable
    | TiledNdArrayUrlTemplateUnknownVariable
    | CoverageMissingParameters
    | CoverageRangeWithoutParameter
    | CoverageRangeAxisNotInDomain
    | CoverageRangeShapeMismatch
    | CoverageDomainTypeNotOmitted
    | CoverageDomainTypeConflict
    | RangeValueTypeMismatch
    | RangeInvalidCategoryCode
    | TemporalLexicalForm
    | ParameterGroupUnknownMember
    | I18nInvalidLanguageTag
    | I18nEmpty
)


# An axis-ordering policy: given a primitive axis's ``values`` and the reference
# system governing them (or ``None``), return the index of the first value that
# breaks the required ordering, or ``None`` for no violation to report. `validate`
# applies `require_monotonic` by default; pass an ``axis_order_checker`` to
# override it (strict monotonicity, a non-standard calendar, or ordering the
# values the default leaves alone). See `require_monotonic` for the default policy.
AxisOrderChecker = Callable[
    [Sequence[AxisValue], ResolvedReferenceSystem | None], int | None
]


class CovJSONValidationError(Exception):
    """Raised by ``validate(..., mode="raise")`` when an error issue is found.

    The full list of error-severity `Issue` records is available on the
    ``issues`` attribute.
    """

    def __init__(self, issues: tuple[Issue, ...]) -> None:
        """Store the error issues and build a summary from the first one's message."""
        self.issues = tuple(issues)
        count = len(self.issues)
        summary = str(self.issues[0]) if self.issues else "validation failed"
        suffix = "" if count <= 1 else f" (and {count - 1} more)"
        super().__init__(f"{summary}{suffix}")


class DomainType(enum.StrEnum):
    """The well-known CoverageJSON domain types.

    ``domain_type`` stays a plain ``str`` on `Domain` (the spec allows custom
    URIs), so this enum is a convenience for the known values and the keys of
    `DOMAIN_TYPE_RULES`.
    """

    GRID = "Grid"
    VERTICAL_PROFILE = "VerticalProfile"
    POINT_SERIES = "PointSeries"
    POINT = "Point"
    MULTI_POINT_SERIES = "MultiPointSeries"
    MULTI_POINT = "MultiPoint"
    TRAJECTORY = "Trajectory"
    SECTION = "Section"
    POLYGON = "Polygon"
    POLYGON_SERIES = "PolygonSeries"
    MULTI_POLYGON = "MultiPolygon"
    MULTI_POLYGON_SERIES = "MultiPolygonSeries"


class DomainTypeRule(msgspec.Struct, frozen=True):
    """The axis constraints a domain type imposes.

    Attributes
    ----------
    required_axes
        Axis names that MUST be present.
    optional_axes
        Axis names that MAY be present; any other axis draws a warning.
    single_valued_axes
        Axis names that, when present, MUST carry exactly one coordinate value.
    composite_data_type
        If set, the ``"composite"`` axis MUST declare this ``dataType``.
    """

    required_axes: tuple[str, ...] = ()
    optional_axes: tuple[str, ...] = ()
    single_valued_axes: tuple[str, ...] = ()
    composite_data_type: Literal["tuple", "polygon"] | None = None


# Derived from the Common Domain Types specification (linked in the module
# docstring). The composite axis's coordinate identifiers (e.g. ["t","x","y"])
# are part of the spec but not yet enforced here. Override or extend by mutating
# this dict.
DOMAIN_TYPE_RULES: dict[str, DomainTypeRule] = {
    DomainType.GRID: DomainTypeRule(
        required_axes=("x", "y"),
        optional_axes=("z", "t"),
    ),
    DomainType.VERTICAL_PROFILE: DomainTypeRule(
        required_axes=("x", "y", "z"),
        optional_axes=("t",),
        single_valued_axes=("x", "y", "t"),
    ),
    DomainType.POINT_SERIES: DomainTypeRule(
        required_axes=("x", "y", "t"),
        optional_axes=("z",),
        single_valued_axes=("x", "y", "z"),
    ),
    DomainType.POINT: DomainTypeRule(
        required_axes=("x", "y"),
        optional_axes=("z", "t"),
        single_valued_axes=("x", "y", "z", "t"),
    ),
    DomainType.MULTI_POINT_SERIES: DomainTypeRule(
        required_axes=("composite", "t"),
        composite_data_type="tuple",
    ),
    DomainType.MULTI_POINT: DomainTypeRule(
        required_axes=("composite",),
        optional_axes=("t",),
        single_valued_axes=("t",),
        composite_data_type="tuple",
    ),
    DomainType.TRAJECTORY: DomainTypeRule(
        required_axes=("composite",),
        optional_axes=("z",),
        single_valued_axes=("z",),
        composite_data_type="tuple",
    ),
    DomainType.SECTION: DomainTypeRule(
        required_axes=("composite", "z"),
        composite_data_type="tuple",
    ),
    DomainType.POLYGON: DomainTypeRule(
        required_axes=("composite",),
        optional_axes=("z", "t"),
        single_valued_axes=("composite", "z", "t"),
        composite_data_type="polygon",
    ),
    DomainType.POLYGON_SERIES: DomainTypeRule(
        required_axes=("composite", "t"),
        optional_axes=("z",),
        single_valued_axes=("composite", "z"),
        composite_data_type="polygon",
    ),
    DomainType.MULTI_POLYGON: DomainTypeRule(
        required_axes=("composite",),
        optional_axes=("z", "t"),
        single_valued_axes=("z", "t"),
        composite_data_type="polygon",
    ),
    DomainType.MULTI_POLYGON_SERIES: DomainTypeRule(
        required_axes=("composite", "t"),
        optional_axes=("z",),
        single_valued_axes=("z",),
        composite_data_type="polygon",
    ),
}


def validate(
    obj: CoverageJSON,
    *,
    check_values: bool = False,
    axis_order_checker: AxisOrderChecker | None = None,
    mode: Literal["collect", "raise"] = "collect",
) -> list[Issue]:
    """Check a CoverageJSON document for cross-cutting, document-level problems.

    Decoding already yields a structurally valid, correctly typed object: msgspec
    enforces the JSON structure, field types, and ``type`` tags on decode, and
    each struct's ``__post_init__`` enforces its local invariants (e.g. a `Unit`
    needs a label or symbol). If you only need a well-formed object to read or
    transform, decoding alone is sufficient and you need not call this.

    `validate` adds the rules that span several fields or objects and so cannot be
    expressed at decode time: a domain's axes satisfy its ``domainType``'s
    requirements, each range lines up with the domain (axis names, shapes),
    parameter groups reference known members, a coverage and domain carry the
    ``parameters`` / ``referencing`` the spec requires (resolving collection-level
    inheritance first), a `TiledNdArray`'s tile sets are well-formed, an axis's
    ``bounds`` (when present) has the spec-defined length of twice the axis, and
    (with ``check_values=True``) every value matches its range's ``dataType``,
    categorical codes are defined, temporal values are well-formed, and each
    ordered primitive axis is monotonic. Reach for it when you need those
    spec-conformance guarantees, e.g. before publishing a document or when
    ingesting one from an untrusted source.

    Parameters
    ----------
    obj
        Any decoded CoverageJSON document.
    check_values
        Also run the checks that scan array values: every range value matches its
        range's ``dataType`` (``range.value-type-mismatch``), categorical codes
        are defined in the parameter's encoding (``range.invalid-category-code``),
        temporal values use a recommended lexical form
        (``temporal.lexical-form``), and each ordered primitive axis is monotonic
        (``axis.not-monotonic``). Off by default because it is O(number of
        values).
    axis_order_checker
        The policy deciding whether a primitive axis's ``values`` are correctly
        ordered (only consulted when ``check_values=True``). Defaults to
        `require_monotonic`, the spec's monotonic MUST read non-strictly over the
        ordered reference systems. Pass a different `AxisOrderChecker` to change it
        (e.g. ``require_monotonic(strict=True)`` to reject equal-adjacent values,
        or a custom callable to order a non-standard calendar). To silence the
        check while keeping the other value scans, pass a checker that always
        returns ``None``.
    mode
        ``"collect"`` (default) returns every issue found. ``"raise"`` raises a
        `CovJSONValidationError` if any error-severity issue is found, and
        otherwise returns the (warning-only) issues.

    Returns
    -------
    list of Issue
        Every issue found, in document order.

    Raises
    ------
    CovJSONValidationError
        If ``mode="raise"`` and at least one error-severity issue is found.

    Notes
    -----
    Validation is partial when a coverage's ``domain`` is a URL reference rather
    than an inline `Domain`: the domain itself and the range-vs-domain checks
    (axis names, shapes) cannot run on data that has not been fetched, so they are
    skipped silently. Such a document can return no issues while a chunk of
    validation never ran; resolve the reference to an inline `Domain` first for
    full coverage. (URL references are spec-valid and common in large collections,
    so this is not reported as an issue.)

    Examples
    --------
    >>> from covjson_msgspec import (
    ...     Axis, Coverage, Domain, NdArray, ReferenceSystem, ReferenceSystemConnection
    ... )
    >>> ref = ReferenceSystemConnection(
    ...     coordinates=("x", "y"), system=ReferenceSystem.geographic()
    ... )
    >>> grid = Domain.grid(
    ...     x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3), referencing=[ref]
    ... )
    >>> validate(grid)
    []

    A domain with no ``referencing`` in scope is a spec violation (an error):

    >>> bare = Domain.grid(x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3))
    >>> validate(bare)[0].code == "domain.missing-referencing"
    True

    A Grid domain missing its ``y`` axis yields an error. Match the string
    ``code``, or narrow with ``isinstance`` to read a variant's typed payload:

    >>> incomplete = Domain(
    ...     axes={"x": Axis.listed((1.0,))}, domain_type="Grid", referencing=[ref]
    ... )
    >>> issue = validate(incomplete)[0]
    >>> issue.code == "domain.missing-axis"
    True
    >>> issue.axis if isinstance(issue, DomainMissingAxis) else None
    'y'
    >>> issue.at
    '/axes/y'

    In ``"raise"`` mode the same document raises:

    >>> validate(incomplete, mode="raise")
    Traceback (most recent call last):
        ...
    covjson_msgspec.validation.CovJSONValidationError: Grid domain requires a 'y' axis

    A range whose name matches no parameter in scope has no parameter at all, a
    MUST violation, so it is an error:

    >>> point = Domain.point(
    ...     x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=[ref]
    ... )
    >>> cov = Coverage(
    ...     domain=point,
    ...     ranges={"v": NdArray(data_type="float", values=(280.0,))},
    ...     parameters={},
    ... )
    >>> issue = validate(cov)[0]
    >>> issue.code == "coverage.range-without-parameter"
    True
    >>> issue.severity.value
    'error'

    A coverage with no ``parameters`` member at all is likewise an error:

    >>> cov = Coverage(domain=point, ranges={})
    >>> validate(cov)[0].code == "coverage.missing-parameters"
    True
    """
    issues = list(_issues(obj, check_values, axis_order_checker))

    if mode == "raise" and (
        errors := tuple(i for i in issues if i.severity is Severity.ERROR)
    ):
        raise CovJSONValidationError(errors)

    return issues


def _issues(
    obj: CoverageJSON,
    check_values: bool,
    axis_order_checker: AxisOrderChecker | None = None,
) -> Iterator[Issue]:
    """Yield every issue for a document, dispatching on its concrete type.

    The pure core of `validate`: it threads no accumulator and performs no
    effects, so each branch is just the composition of the relevant checkers.
    `validate` is the shell that materializes this into a list and applies
    ``mode``. The ``check_values`` flag is forwarded to the value-scanning
    checkers (which are otherwise skipped), and ``axis_order_checker`` (the
    axis-ordering policy, defaulting to `require_monotonic`) with it.

    Parameters
    ----------
    obj
        Any decoded CoverageJSON document.
    check_values
        Whether to run the O(number of values) value-scanning checks.
    axis_order_checker
        The axis-ordering policy forwarded to the monotonic-axis check; ``None``
        uses `require_monotonic`.

    Yields
    ------
    Issue
        Every issue found, in document order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _issues(dom, False)] == [
    ...     "domain.missing-axis",
    ...     "domain.missing-referencing",
    ... ]
    True
    """
    match obj:
        case Domain():
            yield from _validate_domain(
                obj, obj.domain_type, (), check_values, axis_order_checker
            )
        case Coverage():
            yield from _validate_coverage(obj, (), check_values, axis_order_checker)
        case CoverageCollection():
            yield from _validate_collection(obj, (), check_values, axis_order_checker)
        case NdArray():
            yield from _validate_ndarray(obj, ())

            if check_values:
                yield from _check_value_data_types(obj, ())
        case TiledNdArray():
            yield from _validate_tiled_ndarray(obj, ())
        case _:
            # Exhaustiveness: a new CoverageJSON member would fail type checking
            # here until it is handled above.
            assert_never(obj)


def _ptr(path: tuple[str | int, ...], *parts: str | int) -> str:
    """Render a component ``path`` (and any extra ``parts``) as a JSON Pointer.

    A JSON Pointer (RFC 6901) is a run of ``/``-prefixed reference tokens, so the
    empty tuple is the whole-document pointer ``""`` and every token contributes
    its own leading ``/``. ``path`` carries the raw tokens threaded down the
    validation walk; ``parts`` are any extra tokens appended at the emitting
    site. Each token is escaped once here (`_escape`), so the pointer format
    lives in a single place and callers thread raw tokens only, materializing a
    string exactly when an issue is emitted.

    Parameters
    ----------
    path
        The reference tokens built so far (object keys as ``str``, array indices
        as ``int``); ``()`` is the document root.
    *parts
        Extra reference tokens to append at the point of emission.

    Returns
    -------
    str
        The JSON Pointer.

    Examples
    --------
    >>> _ptr((), "ranges", "temperature", "values", 0)
    '/ranges/temperature/values/0'

    The empty tuple is the whole-document pointer, and a ``/`` in a key is
    escaped so it is not read as a separator:

    >>> _ptr(())
    ''
    >>> _ptr((), "axes", "x/y")
    '/axes/x~1y'
    """
    return "".join(f"/{_escape(token)}" for token in (*path, *parts))


def _escape(token: str | int) -> str:
    """Escape one JSON Pointer reference token (RFC 6901).

    A literal ``~`` and ``/`` inside a string token are escaped to ``~0`` and
    ``~1`` so they are not mistaken for the path separator; an integer token (an
    array index) is stringified as-is. The two replacements are skipped when
    neither special character is present, which is the common case (axis names,
    field names), so a conformant document that emits few issues pays almost
    nothing here.

    Parameters
    ----------
    token
        One reference token: an object key (``str``) or an array index (``int``).

    Returns
    -------
    str
        The escaped token.

    Examples
    --------
    >>> _escape("description")
    'description'
    >>> _escape("x/y")
    'x~1y'
    >>> _escape("a~b")
    'a~0b'
    >>> _escape(0)
    '0'
    """
    if isinstance(token, int):
        return str(token)

    if "~" not in token and "/" not in token:
        return token

    return token.replace("~", "~0").replace("/", "~1")


def _missing_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a ``domain.missing-axis`` issue for each absent required axis.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    domain_type
        The (known) domain type, interpolated into each message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per missing required axis, in ``required_axes`` order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Grid"]
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.at for issue in _missing_axis_issues(dom, "Grid", rule, ())]
    ['/axes/y']
    """
    return (
        DomainMissingAxis(
            domain_type=domain_type, axis=name, at=_ptr(path, "axes", name)
        )
        for name in rule.required_axes
        if name not in domain.axes
    )


def _non_single_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a ``domain.axis-not-single`` issue for each over-valued single axis.

    A ``single_valued_axes`` entry that is present yet carries more than one
    coordinate (its ``len()``, O(1) in every axis form) violates the domain
    type.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    domain_type
        The (known) domain type, interpolated into each message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per over-valued single-valued axis, in ``single_valued_axes``
        order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Point"]
    >>> dom = Domain(
    ...     axes={"x": Axis.listed((1.0, 2.0)), "y": Axis.listed((3.0,))},
    ...     domain_type="Point",
    ... )
    >>> [issue.at for issue in _non_single_axis_issues(dom, "Point", rule, ())]
    ['/axes/x']
    """
    return (
        DomainAxisNotSingle(
            domain_type=domain_type, axis=name, at=_ptr(path, "axes", name)
        )
        for name in rule.single_valued_axes
        if (axis := domain.axes.get(name)) is not None and len(axis) != 1
    )


def _composite_data_type_issue(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: tuple[str | int, ...]
) -> Issue | None:
    """Return the ``domain.composite-data-type`` issue, or ``None`` if conformant.

    At most one finding: when the rule pins the ``composite`` axis's ``dataType``
    and the domain's ``composite`` axis declares a different one. A domain type
    without a ``composite_data_type`` rule, or one whose ``composite`` axis is
    absent or already correct, returns ``None``.

    Parameters
    ----------
    domain
        The domain whose ``composite`` axis is checked.
    domain_type
        The (known) domain type, interpolated into the message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The reference-token path to ``domain``, built via `_ptr` for the issue.

    Returns
    -------
    Issue or None
        The single composite-axis issue, or ``None`` when conformant.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Trajectory"]  # wants a "tuple" composite axis
    >>> composite = Axis(
    ...     values=((0.0, 1.0),), data_type="polygon", coordinates=("x", "y")
    ... )
    >>> dom = Domain(axes={"composite": composite}, domain_type="Trajectory")
    >>> issue = _composite_data_type_issue(dom, "Trajectory", rule, ())
    >>> issue.code == "domain.composite-data-type"
    True
    """
    composite = domain.axes.get("composite")

    if (
        rule.composite_data_type is not None
        and composite is not None
        and composite.data_type != rule.composite_data_type
    ):
        return DomainCompositeDataType(
            domain_type=domain_type,
            expected=rule.composite_data_type,
            at=_ptr(path, "axes", "composite"),
        )

    return None


def _unexpected_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a ``domain.extra-axis-not-single`` issue per surplus multi axis.

    An axis outside the required-or-optional set is permitted only if it is
    single-valued; a surplus multi-valued axis is a MUST violation.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    domain_type
        The (known) domain type, interpolated into each message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per surplus multi-valued axis, in ``axes`` order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Grid"]
    >>> dom = Domain(
    ...     axes={
    ...         "x": Axis.listed((1.0,)),
    ...         "y": Axis.listed((2.0,)),
    ...         "bogus": Axis.listed((3.0, 4.0)),
    ...     },
    ...     domain_type="Grid",
    ... )
    >>> [issue.at for issue in _unexpected_axis_issues(dom, "Grid", rule, ())]
    ['/axes/bogus']
    """
    allowed = set(rule.required_axes) | set(rule.optional_axes)

    # The spec permits surplus axes, but only single-valued ones: "A domain that
    # states conformance to one of the domain types in this specification MAY
    # have any number of additional one-coordinate axes not defined here." The
    # spec states this rule without a rationale; the reason is structural. An
    # axis's length is a factor in the range-array shape (see
    # `_check_range_against_domain`), so a length-1 axis adds no dimension: it is
    # pure positioning (a scalar coordinate, e.g. the fixed time or elevation of
    # a 2-D snapshot) and is transparent to the contract the domainType promises.
    # A multi-valued surplus axis adds a real dimension, silently redefining that
    # structure; the spec steers such data to a different domain type (or none).
    # So a surplus single-valued axis is conformant (no issue); a surplus
    # multi-valued one is a MUST violation (error).
    return (
        DomainExtraAxisNotSingle(
            domain_type=domain_type, axis=name, at=_ptr(path, "axes", name)
        )
        for name in domain.axes
        if name not in allowed and len(domain.axes[name]) != 1
    )


def _domain_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield every axis-rule violation for a domain of a known type.

    The composition of the four per-rule generators, all error-severity, in
    document/check order: a missing required axis, a single-valued axis that
    carries more than one value, a ``composite`` axis with the wrong
    ``dataType``, and a surplus multi-valued axis. A surplus single-valued axis
    is spec-conformant and yields nothing.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    domain_type
        The (known) domain type, interpolated into each message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per violation, in check order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Grid"]
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _domain_issues(dom, "Grid", rule, ())] == [
    ...     "domain.missing-axis"
    ... ]
    True
    """
    composite = _composite_data_type_issue(domain, domain_type, rule, path)

    return chain(
        _missing_axis_issues(domain, domain_type, rule, path),
        _non_single_axis_issues(domain, domain_type, rule, path),
        () if composite is None else (composite,),
        _unexpected_axis_issues(domain, domain_type, rule, path),
    )


# BCP 47 (RFC 5646) restricts a language tag to ASCII letters, digits, and
# hyphens as the subtag separator. `langcodes.tag_is_valid` is deliberately
# more lenient than that (its primary use case is locale matching, so it
# normalizes POSIX-style underscores, e.g. "en_US" parses the same as
# "en-US"), so this structural guard runs first and rejects anything
# `langcodes` would otherwise silently normalize away.
_BCP47_CHARSET_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


@cache
def _is_valid_language_tag(tag: str) -> bool:
    """Whether ``tag`` is a well-formed, registered BCP 47 language tag.

    Validity has two independent parts, checked as a conjunction:
    `_BCP47_CHARSET_RE` guards the wire-format character set (letters,
    digits, and ``-`` only), and `langcodes.tag_is_valid` checks the parsed
    tag against the actual IANA subtag registry (catching, for example,
    ``"jp"``, which is well-formed but not a real language code: a check no
    regex alone can do). Both are needed: `langcodes.tag_is_valid` alone would
    accept ``"en_US"`` (it normalizes the underscore before validating, since
    its primary purpose is locale matching, not strict wire-format
    validation). Cached: a document commonly repeats the same handful of tags
    (e.g. ``"en"``) across many i18n-bearing fields, and each `langcodes`
    lookup parses the tag against the registry data from scratch.

    Parameters
    ----------
    tag
        A single i18n object key.

    Returns
    -------
    bool
        Whether ``tag`` is both wire-format-conformant and registered.

    Examples
    --------
    >>> _is_valid_language_tag("en-US")
    True
    >>> _is_valid_language_tag("und")
    True
    >>> _is_valid_language_tag("en_US")  # underscore: not BCP 47 syntax
    False
    >>> _is_valid_language_tag("jp")  # well-formed, but not a real code
    False
    """
    return bool(_BCP47_CHARSET_RE.fullmatch(tag)) and langcodes.tag_is_valid(tag)


def _language_tag_issues(
    tags: I18n | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield an i18n object's ``i18n.empty`` or ``i18n.invalid-language-tag`` issues.

    Spec 2: an i18n object MUST have at least one entry, and every key MUST be
    a BCP 47 language tag (RFC 5646), or the special tag ``"und"`` (itself an
    ordinary 3-letter subtag, so it needs no special case). A present-but-empty
    map (``{}``) is reported once as ``i18n.empty``; otherwise each malformed
    key is reported via `_is_valid_language_tag`.

    Parameters
    ----------
    tags
        An i18n language map, or ``None`` when the member is absent (yields
        nothing); each key is checked. Accepting ``None`` here, rather than at
        every call site, is what lets a caller pass an optional ``label`` /
        ``description`` straight through without a guarding ternary.
    path
        The reference-token path to the i18n object, built via `_ptr` per key.

    Yields
    ------
    Issue
        One ``i18n.empty`` issue for a present-but-empty map, or one
        ``i18n.invalid-language-tag`` per malformed key, in map order.

    Examples
    --------
    ``"und"`` and well-formed, registered tags pass; ``None`` yields nothing:

    >>> list(_language_tag_issues({"und": "x", "en-US": "y"}, ("label",)))
    []
    >>> list(_language_tag_issues(None, ("label",)))
    []

    A present-but-empty map is reported once, at the map's own path:

    >>> issue = next(_language_tag_issues({}, ("label",)))
    >>> issue.code, issue.at
    ('i18n.empty', '/label')

    A malformed separator and an unregistered subtag are both reported:

    >>> [i.at for i in _language_tag_issues({"en_US": "x", "jp": "y"}, ("label",))]
    ['/label/en_US', '/label/jp']
    """
    if tags is None:
        return

    if not tags:
        yield I18nEmpty(at=_ptr(path))
        return

    yield from (
        I18nInvalidLanguageTag(lang=tag, at=_ptr(path, tag))
        for tag in tags
        if not _is_valid_language_tag(tag)
    )


def _label_description_i18n_issues(
    label: I18n | None, description: I18n | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield the language-tag issues for a ``label``/``description`` pair.

    The shared shape behind every ``label``/``description``-carrying struct in
    this module (`Unit`, `Category`, `ObservedProperty`, `Parameter`,
    `ParameterGroup`, `Concept`, `IdentifierRS`): both members are optional
    i18n maps, checked via `_language_tag_issues` in that order.

    Parameters
    ----------
    label
        The ``label`` i18n map, or ``None`` when absent.
    description
        The ``description`` i18n map, or ``None`` when absent.
    path
        The reference-token path to the enclosing object, built via `_ptr`.

    Yields
    ------
    Issue
        ``label`` issues first, then ``description`` issues.

    Examples
    --------
    >>> issues = _label_description_i18n_issues(
    ...     {"en_US": "x"}, {"jp": "y"}, ("parameters", "t")
    ... )
    >>> [i.at for i in issues]
    ['/parameters/t/label/en_US', '/parameters/t/description/jp']
    """
    yield from _language_tag_issues(label, (*path, "label"))
    yield from _language_tag_issues(description, (*path, "description"))


def _concept_i18n_issues(
    concept: Concept, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `Concept`'s language-tag issues (its ``label``/``description``).

    Parameters
    ----------
    concept
        The concept to check (an `IdentifierRS`'s ``target_concept`` or one of
        its ``identifiers`` values).
    path
        The reference-token path to ``concept``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        ``label`` issues first, then ``description`` issues, per `_ptr` order.

    Examples
    --------
    >>> bad = Concept(label={"en_US": "Water"})
    >>> [i.at for i in _concept_i18n_issues(bad, ("targetConcept",))]
    ['/targetConcept/label/en_US']
    """
    yield from _label_description_i18n_issues(concept.label, concept.description, path)


def _reference_system_issues(
    system: ReferenceSystem, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a reference system's required-member and language-tag violations.

    First the required-member MUSTs (Spec 5.2 / 5.3), then the i18n checks
    (Spec 2), in wire order. The required-member rule is shared with
    `~covjson_msgspec.referencing.ReferenceSystem.refine` via
    `~covjson_msgspec._reference_invariants.missing_required_member`, so a system
    that refines to `~covjson_msgspec.referencing.OpaqueRS` for a missing required
    member is exactly the one reported here.

    Parameters
    ----------
    system
        The reference system to check (a `ReferenceSystemConnection.system`).
    path
        The reference-token path to ``system``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The system's required-member and language-tag issues, in wire order.

    Examples
    --------
    >>> from covjson_msgspec.referencing import ReferenceSystem
    >>> issues = _reference_system_issues(ReferenceSystem(type_="TemporalRS"), ())
    >>> [i.code for i in issues]
    ['temporal.missing-calendar']
    """
    match missing_required_member(system):
        case "calendar":
            yield TemporalMissingCalendar(at=_ptr(path, "calendar"))

        case "targetConcept":
            yield IdentifierMissingTargetConcept(at=_ptr(path, "targetConcept"))

        case _:
            pass

    yield from _reference_system_i18n_issues(system, path)


def _reference_system_i18n_issues(
    system: ReferenceSystem, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a reference system's language-tag issues.

    Only an identifier RS carries i18n members (Spec 5.3): a `Concept` for
    ``target_concept`` (encoded first on the wire), then its own
    ``label`` / ``description``, then each ``identifiers`` value. The spatial CRSs
    and the temporal RS carry no i18n member. The checks read the core's fields
    directly (keyed on ``type_``), independent of whether the required
    ``targetConcept`` is present, so a bad language tag is still reported for a
    malformed identifier RS (one `refine` renders opaque).

    Parameters
    ----------
    system
        The reference system to check (a `ReferenceSystemConnection.system`).
    path
        The reference-token path to ``system``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The system's language-tag issues, in wire-field order.

    Examples
    --------
    >>> rs = ReferenceSystem.identifier(
    ...     target_concept=Concept(label={"en_US": "Land cover"})
    ... )
    >>> [i.at for i in _reference_system_i18n_issues(rs, ("referencing", 0, "system"))]
    ['/referencing/0/system/targetConcept/label/en_US']

    A temporal RS has no i18n member, so it yields nothing:

    >>> list(
    ...     _reference_system_i18n_issues(
    ...         ReferenceSystem.temporal(calendar="Gregorian"), ()
    ...     )
    ... )
    []
    """
    if system.type_ != "IdentifierRS":
        return

    if system.target_concept is not None:
        yield from _concept_i18n_issues(system.target_concept, (*path, "targetConcept"))

    yield from _label_description_i18n_issues(system.label, system.description, path)
    yield from chain.from_iterable(
        _concept_i18n_issues(concept, (*path, "identifiers", key))
        for key, concept in (system.identifiers or {}).items()
    )


def _validate_domain(
    domain: Domain,
    domain_type: str | None,
    path: tuple[str | int, ...],
    check_values: bool = False,
    axis_order_checker: AxisOrderChecker | None = None,
) -> Iterator[Issue]:
    """Yield a domain's domainType, axis-rule, and referencing violations.

    First, when the effective ``domain_type`` is absent, a
    `DomainMissingDomainType` warning (Spec 6.1 RECOMMENDS one). Then, resolves
    ``domain_type`` to a `DomainTypeRule` and, when one applies, yields the
    violations `_domain_issues` finds. Then, unconditionally (it is O(1) per
    axis), an ``axis.bounds-length`` error for each axis whose ``bounds`` length
    is not twice the axis length (`_axis_bounds_issues`). Then, when
    ``check_values``, the axis value-scans (a time-axis value outside the
    Gregorian lexical forms via `_temporal_form_issues`, and a non-monotonic
    ordered axis via `_axis_monotonic_issues`); then a missing-referencing issue
    when the domain carries no ``referencing`` in scope, and each reference
    system's language-tag issues (`_reference_system_i18n_issues`). An absent or
    unrecognized (e.g. custom URI) ``domain_type`` carries no axis rules, but the
    referencing checks still apply. Issues come in the domain's member order: a
    ``domainType`` issue first, then the ``axes`` issues (``bounds`` included),
    then the ``referencing`` issues.

    Parameters
    ----------
    domain
        The domain to validate.
    domain_type
        The effective domain type (from the domain itself, or a coverage's own
        ``domainType``); ``None`` or unrecognized means no axis rules to apply.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.
    check_values
        Whether to run the O(number of values) axis value-scans (temporal
        lexical-form and axis monotonicity).
    axis_order_checker
        The axis-ordering policy forwarded to `_axis_monotonic_issues`; ``None``
        uses `require_monotonic`.

    Yields
    ------
    Issue
        Axis-rule issues first (``axes`` precedes ``referencing`` on the wire),
        then the referencing issue if any, then each reference system's
        language-tag issues.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _validate_domain(dom, "Grid", ())] == [
    ...     "domain.missing-axis",
    ...     "domain.missing-referencing",
    ... ]
    True

    A domain with no effective ``domainType`` draws the recommended-member
    warning first (Spec 6.1), then the referencing error:

    >>> bare = Domain(axes={"x": Axis.listed((1.0,))})
    >>> [issue.code for issue in _validate_domain(bare, None, ())] == [
    ...     "domain.missing-domain-type",
    ...     "domain.missing-referencing",
    ... ]
    True
    """
    # Spec 6.1 RECOMMENDS a domainType for interoperability, so its absence is a
    # warning. `domainType` precedes `axes` and `referencing` on the wire, so it
    # comes first. The threaded `domain_type` is the effective one (the domain's
    # own, else a coverage's or collection's), so an inherited type suppresses
    # this. An empty string is present-but-meaningless, so treat it as absent.
    if not domain_type:
        yield DomainMissingDomainType(at=_ptr(path, "domainType"))

    # Axis-rule issues come before the referencing check so issues stay in
    # document order (`axes` precedes `referencing` on the wire). The effective
    # domain type may come from the Domain itself or, inside a coverage, from the
    # coverage's own domainType (passed in by the caller); an absent or
    # unrecognized one carries no axis rules.
    if (
        domain_type is not None
        and (rule := DOMAIN_TYPE_RULES.get(domain_type)) is not None
    ):
        yield from _domain_issues(domain, domain_type, rule, path)

    # The bounds-length test is O(1) per axis, so unlike the value-scans below it
    # runs unconditionally rather than under `check_values`. It sits under `axes`,
    # so it comes before the referencing checks to keep issues in document order.
    yield from _axis_bounds_issues(domain, path)

    # Axis values live under `axes`, so these value-scanning checks come before
    # the referencing checks to keep issues in document order. Resolve each
    # standard-calendar temporal axis once here and share the result with both
    # value-scans, so a temporal string is parsed once per domain, not twice.
    if check_values:
        yield from _axis_composite_issues(domain, path)
        systems = coordinate_systems(domain)
        resolved = _resolved_temporal_axes(domain, systems)
        yield from _temporal_form_issues(path, resolved)
        yield from _axis_monotonic_issues(
            domain, path, axis_order_checker, systems, resolved
        )

    # Spec 6.1: a domain MUST carry `referencing` unless it is a member of a
    # collection that supplies it. `_validate_collection` resolves each member
    # first, pushing the collection's referencing into an inline domain that has
    # none, so by the time we get here an empty `referencing` means none is in
    # scope. A URL-reference domain never reaches this function (it is unfetched),
    # so the check applies only where a referencing array could actually exist.
    if not domain.referencing:
        yield DomainMissingReferencing(at=_ptr(path, "referencing"))

    # Spec 5.2/5.3 required-member MUSTs, then Spec 2 i18n checks, per system.
    yield from chain.from_iterable(
        _reference_system_issues(connection.system, (*path, "referencing", i, "system"))
        for i, connection in enumerate(domain.referencing)
    )


def _resolved_temporal_axes(
    domain: Domain, systems: Mapping[str, ResolvedReferenceSystem]
) -> dict[str, tuple[TemporalResult | None, ...]]:
    """Resolve each standard-calendar temporal axis's values once.

    For every coordinate governed by a standard-calendar
    `~covjson_msgspec.referencing.TemporalRS` (found in ``systems``) that names an
    axis in ``domain``, each of the axis's ``coordinate_values`` is classified by
    `~covjson_msgspec.temporal.resolve`, keeping ``None`` for a non-string value so
    the result stays index-aligned with the axis. Computed once per domain and
    shared by both temporal value-scans (`_temporal_form_issues` and the default
    monotonic check), so a temporal string is parsed once, not twice.

    Parameters
    ----------
    domain
        The domain whose temporal axes are resolved.
    systems
        The coordinate-to-system index, from
        `~covjson_msgspec._bridging.coordinate_systems`.

    Returns
    -------
    dict
        Each standard-calendar temporal coordinate mapped to its resolved values
        (a `~covjson_msgspec.temporal.TemporalResult` per string, ``None`` per
        non-string), index-aligned with the axis.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> from covjson_msgspec import ReferenceSystem, ReferenceSystemConnection
    >>> dom = Domain(
    ...     axes={"t": Axis.listed(("2020", "nope"))},
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("t",),
    ...             system=ReferenceSystem.temporal(calendar="Gregorian"),
    ...         )
    ...     ],
    ... )
    >>> resolved = _resolved_temporal_axes(dom, coordinate_systems(dom))
    >>> [type(result).__name__ for result in resolved["t"]]
    ['Moment', 'Malformed']
    """
    return {
        coord: tuple(
            resolve(value) if isinstance(value, str) else None
            for value in domain.axes[coord].coordinate_values
        )
        for coord, system in systems.items()
        if coord in domain.axes and _ordering_kind(system) == "temporal"
    }


def _temporal_form_issues(
    path: tuple[str | int, ...],
    resolved: Mapping[str, tuple[TemporalResult | None, ...]],
) -> Iterator[Issue]:
    """Yield a warning for each time-axis value outside the recommended forms.

    ``resolved`` (from `_resolved_temporal_axes`) maps each standard-calendar
    temporal coordinate to its values already classified by
    `~covjson_msgspec.temporal.resolve`, index-aligned with ``None`` for a
    non-string value. Each value that resolved to
    `~covjson_msgspec.temporal.Malformed` should have used one of the recommended
    Gregorian lexical forms, so it is reported (a warning, per ADR-0002, since
    Spec 5.2 makes this a SHOULD); a valid but unrepresentable value (an expanded
    year, a leap second) is a legal form and is left alone. This is a
    value-scanning check, gated behind ``validate(check_values=True)``.

    Parameters
    ----------
    path
        The reference-token path to the domain, built via `_ptr` for each issue.
    resolved
        The per-coordinate resolved temporal values, from `_resolved_temporal_axes`.

    Yields
    ------
    Issue
        A `TemporalLexicalForm` per malformed value, in axis (sorted) then value
        order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> from covjson_msgspec import ReferenceSystem, ReferenceSystemConnection
    >>> dom = Domain(
    ...     axes={"t": Axis.listed(("2020-01-01T00:00:00Z", "nope"))},
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("t",),
    ...             system=ReferenceSystem.temporal(calendar="Gregorian"),
    ...         )
    ...     ],
    ... )
    >>> resolved = _resolved_temporal_axes(dom, coordinate_systems(dom))
    >>> [str(issue) for issue in _temporal_form_issues((), resolved)]
    ["value 'nope' does not use a recommended Gregorian temporal form"]
    """
    return (
        TemporalLexicalForm(
            value=result.value, at=_ptr(path, "axes", coord, "values", i)
        )
        for coord in sorted(resolved)
        for i, result in enumerate(resolved[coord])
        if isinstance(result, Malformed)
    )


def _axis_composite_issues(
    domain: Domain, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield the spec 6.1.1 value-shape issues for each composite axis.

    Dispatches per ``dataType`` rather than sharing one rule, because 6.1.1
    states two different requirements at two different depths: a ``"tuple"``
    value's size matches the coordinate identifier count, while a ``"polygon"``
    value's ``coordinates`` order the components *inside* each GeoJSON position
    (its length is the ring count, unrelated to the identifiers). A custom
    dataType yields nothing: 6.1.1 grants only "Custom values MAY be used" and
    defines no value structure for one.

    Parameters
    ----------
    domain
        The domain whose axes are scanned.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The composite issues, in ``axes`` order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(
    ...     axes={"composite": Axis(values=((1.0, 2.0),), data_type="tuple")},
    ...     domain_type="Trajectory",
    ... )
    >>> [issue.code for issue in _axis_composite_issues(dom, ())]
    ['axis.composite-arity']

    A custom dataType is left alone:

    >>> custom = Domain(axes={"composite": Axis.listed((1.0,), )})
    >>> list(_axis_composite_issues(custom, ()))
    []
    """

    for name, axis in domain.axes.items():
        if axis.data_type == "tuple":
            yield from _tuple_axis_issues(name, axis, path)
        elif axis.data_type == "polygon":
            yield from _polygon_axis_issues(name, axis, path)


def _tuple_axis_issues(
    name: str, axis: Axis, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield the shape and arity issues for one ``"tuple"`` axis.

    Spec 6.1.1: each value MUST be "an array of fixed size of primitive values in
    a defined order, where the tuple size corresponds to the number of coordinate
    identifiers". Shape gates arity: a value that is not an array has no
    meaningful size, and a ``str`` would otherwise pass an arity check by its
    character count (``len("abc") == 3``).

    The identifier count is the resolved one, so an axis omitting ``coordinates``
    is measured against 6.1.1's one-element default rather than skipped.

    Parameters
    ----------
    name
        The axis's identifier: its key in `~covjson_msgspec.domain.Domain.axes`.
    axis
        The composite axis to scan.
    path
        The reference-token path to the domain, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        An `AxisCompositeValueShape` or `AxisCompositeArity` per offending value.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> axis = Axis(values=("abc",), data_type="tuple", coordinates=("t", "x", "y"))
    >>> [issue.code for issue in _tuple_axis_issues("composite", axis, ())]
    ['axis.composite-value-shape']
    """

    expected = len(coordinate_identifiers(axis, name))

    for index, value in enumerate(axis.values or ()):
        at = _ptr(path, "axes", name, "values", index)

        if not isinstance(value, tuple):
            yield AxisCompositeValueShape(axis=name, data_type="tuple", at=at)
        elif len(value) != expected:
            yield AxisCompositeArity(
                axis=name, expected=expected, got=len(value), at=at
            )


def _polygon_axis_issues(
    name: str, axis: Axis, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield the shape issues for one ``"polygon"`` axis.

    Spec 6.1.1: each value MUST be "a GeoJSON Polygon coordinate array", meaning
    an array of linear rings, each an array of positions. The check stops there:
    it confirms each position is an array without reading its contents, leaving
    the position-vs-``coordinates`` length rule to its own check.

    Only the outermost level is annotated (`~covjson_msgspec.axis.AxisValue` is
    ``tuple[Any, ...]``, its interior deliberately ``Any``), so a decoded polygon
    arrives as a tuple of *lists* of lists. Rings and positions are therefore
    tested against both sequence types, while the value itself is tested against
    the ``tuple`` its annotation promises.

    Parameters
    ----------
    name
        The axis's identifier: its key in `~covjson_msgspec.domain.Domain.axes`.
    axis
        The composite axis to scan.
    path
        The reference-token path to the domain, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        An `AxisCompositeValueShape` per value that is not a Polygon array.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> axis = Axis(values=(1.0,), data_type="polygon", coordinates=("x", "y"))
    >>> [issue.code for issue in _polygon_axis_issues("composite", axis, ())]
    ['axis.composite-value-shape']
    """

    for index, value in enumerate(axis.values or ()):
        if not _is_polygon_array(value):
            yield AxisCompositeValueShape(
                axis=name,
                data_type="polygon",
                at=_ptr(path, "axes", name, "values", index),
            )


def _is_polygon_array(value: AxisValue) -> bool:
    """Whether ``value`` is a GeoJSON Polygon coordinate array: rings of positions.

    Only the outermost level is annotated
    (`~covjson_msgspec.axis.AxisValue` is ``tuple[Any, ...]``), so a decoded
    polygon is a tuple of *lists* of lists: the value is held to the ``tuple`` its
    annotation promises, while the rings beneath it are widened to ``object`` and
    narrowed by `_is_position_array` rather than left as `~typing.Any`.

    The array must be non-empty: a polygon with no rings is not a GeoJSON Polygon
    (RFC 7946), and it reaches shapely as an unpack error rather than a clean one.
    The rings' interiors are not read beyond nesting: whether a ring has enough
    positions, closes, or matches ``coordinates`` in length is a separate, deeper
    rule.

    Parameters
    ----------
    value
        One value of a ``"polygon"`` axis.

    Returns
    -------
    bool
        True when ``value`` is a non-empty array whose every element is a ring.

    Examples
    --------
    >>> _is_polygon_array(([[100.0, 0.0], [101.0, 0.0], [100.0, 0.0]],))
    True

    A bare ring, one nesting level short of a polygon, is not:

    >>> _is_polygon_array(([100.0, 0.0], [101.0, 0.0]))
    False

    Nor is a polygon with no rings at all:

    >>> _is_polygon_array(())
    False
    """

    if not isinstance(value, tuple) or not value:
        return False

    rings: tuple[object, ...] = value

    return all(map(_is_position_array, rings))


def _is_position_array(ring: object) -> bool:
    """Whether ``ring`` is an array of positions, each itself an array.

    A GeoJSON linear ring is a sequence of positions (spec 6.1.1 defers to
    GeoJSON for the structure). Position *contents* are not read: this answers
    only whether the nesting is right, and that the ring is non-empty (an empty
    ring is not a ring, and reaches shapely as an empty geometry).

    Parameters
    ----------
    ring
        A candidate linear ring, from a polygon axis value's interior. Typed
        ``object`` because that interior is `~typing.Any`: it may be any decoded
        JSON value, and narrowing it here beats propagating the ``Any``.

    Returns
    -------
    bool
        True when ``ring`` is a non-empty list or tuple whose every element is
        one too.

    Examples
    --------
    >>> _is_position_array([[100.0, 0.0], [101.0, 1.0]])
    True
    >>> _is_position_array([100.0, 0.0])
    False
    >>> _is_position_array("nope")
    False

    An empty ring holds no positions, so it is not a ring:

    >>> _is_position_array([])
    False
    """

    if not isinstance(ring, (list, tuple)) or not ring:
        return False

    # `isinstance` narrows the element type to Unknown rather than `object`, so
    # name it: every position is only ever read through `isinstance` below.
    positions = cast("Sequence[object]", ring)

    return all(isinstance(position, (list, tuple)) for position in positions)


def _axis_bounds_issues(
    domain: Domain, path: tuple[str | int, ...]
) -> Iterator[AxisBoundsLength]:
    """Yield an ``axis.bounds-length`` error per axis with wrong-length ``bounds``.

    Spec 6.1.1 defines a present ``bounds`` array as ``2 * len`` values (a lower
    and an upper per axis value), so an axis whose ``bounds`` length differs is
    reported. ``len`` is the axis length, which for a regular axis is ``num``
    (`Axis.__len__` never materializes a regular axis's values), so the test is
    O(1) per axis and its caller runs it unconditionally, not under
    ``check_values``. An axis without ``bounds`` is skipped.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    path
        The reference-token path to ``domain``, extended per issue via `_ptr`.

    Yields
    ------
    AxisBoundsLength
        One error per axis whose ``bounds`` length is not ``2 * len(axis)``,
        pointing at that axis's ``bounds`` array.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0, 2.0), bounds=(0.5, 1.5, 1.5))})
    >>> [issue.at for issue in _axis_bounds_issues(dom, ())]
    ['/axes/x/bounds']
    """
    return (
        AxisBoundsLength(
            at=_ptr(path, "axes", name, "bounds"),
            axis=name,
            expected=2 * len(axis),
            got=len(bounds),
        )
        for name, axis in domain.axes.items()
        if (bounds := axis.bounds) is not None and len(bounds) != 2 * len(axis)
    )


def _axis_monotonic_issues(
    domain: Domain,
    path: tuple[str | int, ...],
    axis_order_checker: AxisOrderChecker | None,
    systems: Mapping[str, ResolvedReferenceSystem],
    resolved: Mapping[str, tuple[TemporalResult | None, ...]],
) -> Iterator[Issue]:
    """Yield an error for each primitive axis whose ``values`` are not monotonic.

    For every primitive (non-composite) value-listing axis, the axis-ordering
    policy is asked where the axis's ``values`` first break their required
    ordering, given the reference system governing the axis (``systems``). A
    returned index becomes an `AxisNotMonotonic` error pointing at that value;
    ``None`` means nothing to report. Regular (``start``/``stop``/``num``) axes are
    monotonic by construction and skipped without materializing. This is a
    value-scanning check, gated behind ``validate(check_values=True)``.

    A custom ``axis_order_checker`` is called as ``(values, system)``; ``None``
    uses the default policy (`_default_break`, equivalent to `require_monotonic`),
    which reads each temporal axis's already-resolved values from ``resolved``
    rather than re-parsing them.

    Parameters
    ----------
    domain
        The domain whose primitive axes are scanned.
    path
        The reference-token path to ``domain``, built via `_ptr` for each issue.
    axis_order_checker
        The axis-ordering policy; ``None`` uses the default (`require_monotonic`).
    systems
        The coordinate-to-system index, from
        `~covjson_msgspec._bridging.coordinate_systems`.
    resolved
        The per-coordinate resolved temporal values, from `_resolved_temporal_axes`.

    Yields
    ------
    Issue
        An `AxisNotMonotonic` per offending axis, at its first breaking value.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> from covjson_msgspec.referencing import (
    ...     ReferenceSystem,
    ...     ReferenceSystemConnection,
    ... )
    >>> dom = Domain(
    ...     axes={"x": Axis.listed((0.0, 2.0, 1.0))},
    ...     referencing=[
    ...         ReferenceSystemConnection(
    ...             coordinates=("x",), system=ReferenceSystem.geographic()
    ...         )
    ...     ],
    ... )
    >>> systems = coordinate_systems(dom)
    >>> resolved = _resolved_temporal_axes(dom, systems)
    >>> issues = _axis_monotonic_issues(dom, (), None, systems, resolved)
    >>> [issue.at for issue in issues]
    ['/axes/x/values/2']
    """
    # Primitive value-listing axes only: a regular (start/stop/num) axis has no
    # ``values`` and is monotonic by construction, and a composite (tuple/polygon)
    # axis is outside the primitive-axis MUST, so both are filtered out here.
    scannable = [
        (name, values, systems.get(name), resolved.get(name))
        for name, axis in domain.axes.items()
        if (values := axis.values) is not None
        and axis.data_type not in ("tuple", "polygon")
    ]

    # Apply the ordering policy to each: the first index where the values break
    # their order, or ``None`` for an ordered axis.
    breaks = (
        (name, _axis_break(axis_order_checker, values, system, results))
        for name, values, system, results in scannable
    )

    # One error per axis that actually breaks, pointing at the breaking value.
    return (
        AxisNotMonotonic(at=_ptr(path, "axes", name, "values", break_index), axis=name)
        for name, break_index in breaks
        if break_index is not None
    )


def _axis_break(
    axis_order_checker: AxisOrderChecker | None,
    values: Sequence[AxisValue],
    system: ResolvedReferenceSystem | None,
    results: tuple[TemporalResult | None, ...] | None,
) -> int | None:
    """The first index where an axis's values break their ordering, or ``None``.

    Applies the active ordering policy to one axis: a custom ``axis_order_checker``
    is asked directly (``(values, system)``); ``None`` uses the default policy
    (`_default_break`), which reuses the axis's pre-resolved temporal ``results``
    instead of re-parsing them.

    Parameters
    ----------
    axis_order_checker
        The axis-ordering policy, or ``None`` for the default.
    values
        The primitive axis's coordinate values.
    system
        The reference system governing the axis, or ``None``.
    results
        The axis's pre-resolved temporal values, or ``None``. Used only by the
        default policy, and only on a temporal axis.

    Returns
    -------
    int or None
        The first breaking index, or ``None`` when the axis is ordered.

    Examples
    --------
    >>> from covjson_msgspec.referencing import GeographicCRS
    >>> _axis_break(None, (0.0, 2.0, 1.0), GeographicCRS(), None)
    2
    >>> _axis_break(None, (0.0, 1.0, 2.0), GeographicCRS(), None) is None
    True
    """
    if axis_order_checker is not None:
        return axis_order_checker(values, system)

    return _default_break(values, system, results, strict=False)


def require_monotonic(*, strict: bool = False) -> AxisOrderChecker:
    """Build the default `AxisOrderChecker`: the spec's monotonic-ordering MUST.

    The returned checker fires only for axes whose reference system defines a
    natural ordering: numeric axes under a geographic, projected, or vertical CRS
    (compared by value), and time axes under a standard-calendar
    `~covjson_msgspec.referencing.TemporalRS` (compared as resolved instants, via
    `~covjson_msgspec.temporal.resolve`). An axis under an identifier system, a
    non-standard calendar, or no system is left alone (a categorical or unordered
    axis has no ordering to violate).

    By default the check is non-strict: only a genuine direction reversal is a
    break, so equal-adjacent values pass (Spec 6.1.1 says "monotonically", not
    "strictly", and this is an error that aborts ``mode="raise"``). Pass
    ``strict=True`` to also reject equal-adjacent values.

    Parameters
    ----------
    strict
        Whether equal-adjacent values break the ordering. Default ``False``.

    Returns
    -------
    AxisOrderChecker
        A callable ``(values, system) -> int | None`` giving the first index that
        breaks the ordering, or ``None``.

    Examples
    --------
    >>> from covjson_msgspec.referencing import Concept, GeographicCRS, IdentifierRS
    >>> check = require_monotonic()
    >>> check((0.0, 1.0, 2.0), GeographicCRS()) is None  # increasing
    True
    >>> check((0.0, 2.0, 1.0), GeographicCRS())  # reversal at index 2
    2
    >>> check((0.0, 0.0, 1.0), GeographicCRS()) is None  # equal-adjacent: ok
    True
    >>> require_monotonic(strict=True)((0.0, 0.0, 1.0), GeographicCRS())
    1

    An identifier system defines no ordering, so its axis is never flagged, even
    with integer codes in an arbitrary order:

    >>> ids = IdentifierRS(target_concept=Concept(label={"en": "class"}))
    >>> check((3, 1, 2), ids) is None
    True
    """

    def check(
        values: Sequence[AxisValue], system: ResolvedReferenceSystem | None
    ) -> int | None:
        return _default_break(values, system, None, strict=strict)

    return check


def _default_break(
    values: Sequence[AxisValue],
    system: ResolvedReferenceSystem | None,
    results: tuple[TemporalResult | None, ...] | None,
    *,
    strict: bool,
) -> int | None:
    """Return the first index breaking the default monotonic-ordering policy.

    The single home for the spec's monotonic MUST, shared by the public
    `require_monotonic` (which passes ``results=None``, resolving temporal values
    inline) and `_axis_monotonic_issues` (which passes the axis's already-resolved
    values so a temporal string is parsed once per domain). `_ordering_kind`
    classifies the system: a numeric axis is keyed by value (`_numeric_keys`), a
    temporal axis by resolved instant (`_temporal_keys_from_resolved`, resolving
    the values inline when ``results`` is ``None``); an unordered system yields
    ``None``.

    Parameters
    ----------
    values
        The axis's coordinate values.
    system
        The reference system governing the axis, or ``None``.
    results
        The axis's pre-resolved temporal values (index-aligned), or ``None`` to
        resolve inline. Ignored for a numeric axis.
    strict
        Whether equal-adjacent keys break the ordering.

    Returns
    -------
    int or None
        The first index that breaks the ordering, or ``None`` when the axis is
        ordered (or its system defines no ordering).

    Examples
    --------
    >>> from covjson_msgspec.referencing import GeographicCRS
    >>> _default_break((0.0, 2.0, 1.0), GeographicCRS(), None, strict=False)
    2
    >>> _default_break((0.0, 1.0, 2.0), GeographicCRS(), None, strict=False) is None
    True
    """
    kind = _ordering_kind(system)

    if kind is None:
        return None

    if kind == "numeric":
        keyed: list[tuple[int, Any]] = _numeric_keys(values)
    else:
        temporal = _temporal_keys_from_resolved(
            results
            if results is not None
            else tuple(
                resolve(value) if isinstance(value, str) else None for value in values
            )
        )

        if temporal is None:  # resolved instants mix tz-awareness; skip
            return None

        keyed = temporal

    return _first_monotonic_break(keyed, strict=strict)


def _numeric_keys(values: Sequence[AxisValue]) -> list[tuple[int, Any]]:
    """The ``(index, value)`` keys for a numeric axis's orderable values.

    Compares the numeric values directly. A non-numeric value on a numeric axis is
    skipped rather than crash the comparison, and a NaN float is skipped too: NaN
    is unordered and not equal to itself, so leaving it in would corrupt the
    monotonic walk. `math.isnan` is guarded to floats so a large int never
    overflows converting to one.

    Parameters
    ----------
    values
        A primitive numeric axis's coordinate values.

    Returns
    -------
    list of (int, value)
        The orderable values keyed by original position.

    Examples
    --------
    >>> _numeric_keys((0.0, 2.0, 1.0))
    [(0, 0.0), (1, 2.0), (2, 1.0)]

    A NaN and a non-numeric value are dropped (their indices do not appear):

    >>> _numeric_keys((0.0, float("nan"), "x", 2))
    [(0, 0.0), (3, 2)]
    """
    return [
        (i, value)
        for i, value in enumerate(values)
        if isinstance(value, int)
        or (isinstance(value, float) and not math.isnan(value))
    ]


def _ordering_kind(
    system: ResolvedReferenceSystem | None,
) -> Literal["numeric", "temporal"] | None:
    """Classify a reference system by the kind of value ordering it defines.

    Spec 6.1.1 ties the monotonic-ordering MUST to a reference system that
    "defines a natural ordering". This is the single, total classifier of which
    systems the default check treats as ordered, and of what kind:

    * a geographic, projected, or vertical CRS orders its values numerically;
    * a standard-calendar `~covjson_msgspec.referencing.TemporalRS` orders its
      values as instants in time (`~covjson_msgspec._bridging.is_standard_calendar`);
    * an identifier system (categorical / coded), a non-standard-calendar temporal
      system, and an axis with no system in scope (``None``) define no ordering.

    The ``match`` is exhaustive over the
    `~covjson_msgspec.referencing.ResolvedReferenceSystem` union (closed with
    `~typing.assert_never`), so adding a reference-system variant forces a decision
    here rather than silently falling through to "unordered".

    Parameters
    ----------
    system
        The reference system governing an axis, or ``None`` if none is in scope.

    Returns
    -------
    {"numeric", "temporal"} or None
        The ordering kind, or ``None`` when the system defines no ordering.

    Examples
    --------
    >>> from covjson_msgspec.referencing import GeographicCRS, TemporalRS
    >>> _ordering_kind(GeographicCRS())
    'numeric'
    >>> _ordering_kind(TemporalRS(calendar="Gregorian"))
    'temporal'
    >>> _ordering_kind(TemporalRS(calendar="360_day")) is None
    True
    >>> _ordering_kind(None) is None
    True
    """
    match system:
        case GeographicCRS() | ProjectedCRS() | VerticalCRS():
            return "numeric"
        case TemporalRS():
            return "temporal" if is_standard_calendar(system) else None
        case IdentifierRS() | OpaqueRS() | None:
            return None
        case _:  # pragma: no cover - exhaustive over ResolvedReferenceSystem
            assert_never(system)


def _temporal_keys_from_resolved(
    results: tuple[TemporalResult | None, ...],
) -> list[tuple[int, Any]] | None:
    """The ``(index, instant)`` pairs for a time axis's resolved moments, or None.

    Each `~covjson_msgspec.temporal.Moment` in ``results`` contributes its
    `~datetime.datetime`, keyed by original position. A
    `~covjson_msgspec.temporal.Malformed` value (owned by the
    ``temporal.lexical-form`` check), an `~covjson_msgspec.temporal.Unrepresentable`
    one (a legal form with no ``datetime``), and a ``None`` (a non-string value)
    are skipped, so a reversal hinging on such a value between two moments is not
    caught here.

    `~covjson_msgspec.temporal.Moment.when` is timezone-aware only at second
    precision, so a naive and an aware instant are not comparable; when the
    resolved moments mix awareness, ``None`` is returned so the caller declines to
    order the axis rather than fabricate a zone.

    Parameters
    ----------
    results
        A primitive time axis's values, already classified by
        `~covjson_msgspec.temporal.resolve` (index-aligned, ``None`` per non-string).

    Returns
    -------
    list of (int, datetime) or None
        The resolvable instants keyed by index, or ``None`` when they mix
        timezone-awareness.

    Examples
    --------
    >>> from covjson_msgspec.temporal import resolve
    >>> aware = ("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")
    >>> [i for i, _ in _temporal_keys_from_resolved(tuple(map(resolve, aware)))]
    [0, 1]

    A malformed value is dropped (its index does not appear):

    >>> forms = ("2020-01-01T00:00:00Z", "nope")
    >>> [i for i, _ in _temporal_keys_from_resolved(tuple(map(resolve, forms)))]
    [0]

    Mixing an aware instant (second precision) with a naive one (day precision)
    yields ``None``:

    >>> mixed = ("2020-01-01T00:00:00Z", "2020-01-02")
    >>> _temporal_keys_from_resolved(tuple(map(resolve, mixed))) is None
    True
    """
    moments = [
        (i, result.when)
        for i, result in enumerate(results)
        if isinstance(result, Moment)
    ]

    if len({when.tzinfo is None for _, when in moments}) > 1:
        return None

    return moments


def _first_monotonic_break(
    keyed: Sequence[tuple[int, Any]], *, strict: bool
) -> int | None:
    """Return the index of the first key that breaks monotonic order, or ``None``.

    ``keyed`` pairs each comparison key with its original position in the axis,
    already filtered to mutually comparable keys (so a time axis passes only its
    resolvable, same-awareness instants). The ordering direction is set by the
    first strictly-unequal adjacent pair; a later pair that contradicts it breaks
    the order, and the *later* key's original index is returned (the coordinate to
    look at). Equal-adjacent keys are permitted unless ``strict``. Fewer than two
    keys is trivially ordered.

    Parameters
    ----------
    keyed
        ``(index, key)`` pairs in axis order, the keys mutually comparable.
    strict
        Whether equal-adjacent keys break the ordering.

    Returns
    -------
    int or None
        The original index of the first breaking key, or ``None`` if the keys are
        monotonic.

    Examples
    --------
    >>> _first_monotonic_break([(0, 1), (1, 2), (2, 3)], strict=False) is None
    True
    >>> _first_monotonic_break([(0, 3), (1, 2), (2, 1)], strict=False) is None
    True
    >>> _first_monotonic_break([(0, 1), (1, 3), (2, 2)], strict=False)
    2
    >>> _first_monotonic_break([(0, 1), (1, 1), (2, 2)], strict=False) is None
    True
    >>> _first_monotonic_break([(0, 1), (1, 1), (2, 2)], strict=True)
    1
    """
    direction = 0  # 0 = undetermined, 1 = increasing, -1 = decreasing

    for (_, previous), (index, current) in pairwise(keyed):
        if current == previous:
            if strict:
                return index

            continue

        step = 1 if current > previous else -1

        if direction == 0:
            direction = step
        elif step != direction:
            return index

    return None


def _validate_ndarray(arr: NdArray, path: tuple[str | int, ...]) -> Iterator[Issue]:
    """Yield an `NdArray`'s internal shape-consistency issues.

    Two self-contained checks (no domain needed): ``shape`` and ``axisNames``
    must have the same rank, and the number of ``values`` must equal the product
    of ``shape`` (``math.prod(()) == 1``, so a 0-dimensional array must hold
    exactly one value). Decoding is permissive about these: a rank or value-count
    mismatch is an *internally inconsistent* but still interpretable array, so per
    ADR-0002 it is reported here rather than rejected at construction, keeping a
    repairable document loadable.

    Parameters
    ----------
    arr
        The inline array to check.
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        A shape-rank issue and/or a value-count issue.

    Examples
    --------
    A value count that disagrees with ``shape`` yields one ``ndarray.value-count``
    issue:

    >>> arr = NdArray(
    ...     data_type="float", values=(1.0, 2.0), shape=(3,), axis_names=("x",)
    ... )
    >>> [issue.code for issue in _validate_ndarray(arr, ("ranges", "v"))] == [
    ...     "ndarray.value-count"
    ... ]
    True

    A consistent array yields nothing:

    >>> consistent = NdArray(
    ...     data_type="float", values=(1.0,), shape=(1,), axis_names=("x",)
    ... )
    >>> list(_validate_ndarray(consistent, ("ranges", "v")))
    []
    """
    if len(arr.axis_names) != len(arr.shape):
        yield NdArrayShapeRank(at=_ptr(path, "shape"))

    # math.prod(()) == 1, so a 0-dimensional array must hold a single value.
    expected = math.prod(arr.shape)

    if len(arr.values) != expected:
        yield NdArrayValueCount(
            expected=expected,
            shape=tuple(arr.shape),
            got=len(arr.values),
            at=_ptr(path, "values"),
        )


def _tile_set_issues(
    arr: TiledNdArray,
    ts: int,
    tile_set: TileSet,
    path: tuple[str | int, ...],
    *,
    rank_ok: bool,
) -> Iterator[Issue]:
    """Yield one tile set's issues: out-of-range tile sizes and template variables.

    The per-tile-set rules (see `_validate_tiled_ndarray` for the full set): each
    non-null ``tileShape`` element must be a positive integer
    (``tiled-ndarray.tile-shape-not-positive``) not exceeding its ``shape`` element
    (``tiled-ndarray.tile-shape-too-large``); the ``urlTemplate`` must carry a
    variable for each subdivided axis (``tiled-ndarray.url-template-missing-variable``)
    and, when ``rank_ok``, must not name a non-subdivided axis
    (``tiled-ndarray.url-template-unknown-variable``).

    Parameters
    ----------
    arr
        The tiled array the tile set belongs to (for its ``shape`` / ``axisNames``).
    ts
        The tile set's index, for the JSON Pointer.
    tile_set
        The tile set to check.
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.
    rank_ok
        Whether ``axisNames`` rank-matches ``shape``; the unknown-variable check
        is skipped when it does not (the axis/tile alignment is then unreliable,
        so the membership test would yield false positives).

    Yields
    ------
    Issue
        This tile set's rule violations, in check order.

    Examples
    --------
    >>> from covjson_msgspec.range import TileSet
    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(5, None), url_template="{t}.cov"),),
    ... )
    >>> tile_set = arr.tile_sets[0]
    >>> [i.code for i in _tile_set_issues(arr, 0, tile_set, (), rank_ok=True)] == [
    ...     "tiled-ndarray.tile-shape-too-large"
    ... ]
    True
    """
    # __post_init__ guarantees tileShape rank-matches shape, so this zip is exact.
    # A non-null tile size must be a positive integer (the tile layout divides each
    # axis by it) not exceeding the corresponding axis.
    yield from (
        TiledNdArrayTileShapeTooLarge(
            tile_dim=tile_dim, dim=dim, at=_ptr(path, "tileSets", ts, "tileShape", i)
        )
        for i, (tile_dim, dim) in enumerate(
            zip(tile_set.tile_shape, arr.shape, strict=True)
        )
        if tile_dim is not None and tile_dim > dim
    )

    yield from (
        TiledNdArrayTileShapeNotPositive(
            tile_dim=tile_dim, at=_ptr(path, "tileSets", ts, "tileShape", i)
        )
        for i, tile_dim in enumerate(tile_set.tile_shape)
        if tile_dim is not None and tile_dim < 1
    )

    present_names = template_variables(tile_set.url_template)
    present = set(present_names)

    # A subdivided axis (non-null tileShape) MUST have a template variable. When
    # axisNames does not rank-match shape, this zip is intentionally non-strict so
    # it cannot raise: validate() reports issues rather than raising.
    yield from (
        TiledNdArrayUrlTemplateMissingVariable(
            axis=name, at=_ptr(path, "tileSets", ts, "urlTemplate")
        )
        for name, tile_dim in zip(arr.axis_names, tile_set.tile_shape, strict=False)
        if tile_dim is not None and name not in present
    )

    # The reverse: a template variable that names no subdivided axis cannot be
    # expanded, so `assemble` would raise on it. Skipped on a rank mismatch, where
    # the set of subdivided axes is unreliable (see ``rank_ok``).
    if rank_ok:
        subdivided = {
            name
            for name, tile_dim in zip(arr.axis_names, tile_set.tile_shape, strict=True)
            if tile_dim is not None
        }
        yield from (
            TiledNdArrayUrlTemplateUnknownVariable(
                variable=name, at=_ptr(path, "tileSets", ts, "urlTemplate")
            )
            for name in dict.fromkeys(present_names)
            if name not in subdivided
        )


def _validate_tiled_ndarray(
    arr: TiledNdArray, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `TiledNdArray`'s tile-set issues against the spec.

    Several rules from the TiledNdArray spec, all error-severity (``tileShape``
    rank-matching ``shape`` is a separate hard structural error raised in
    `~covjson_msgspec.range.TiledNdArray.__post_init__`, so each ``tileShape``
    here already aligns with ``shape``):

    * ``shape`` and ``axisNames`` must have the same length, as for `NdArray`
      (else ``tiled-ndarray.shape-rank``);
    * each non-null ``tileShape`` element must be a positive integer (else
      ``tiled-ndarray.tile-shape-not-positive``) not exceeding the corresponding
      ``shape`` element (else ``tiled-ndarray.tile-shape-too-large``); and
    * the ``urlTemplate`` must contain a variable for each axis whose
      ``tileShape`` element is non-null -- the subdivided axes whose per-tile
      ordinals the template interpolates (else
      ``tiled-ndarray.url-template-missing-variable``); and
    * conversely, the ``urlTemplate`` must not reference a variable that names no
      subdivided axis (else ``tiled-ndarray.url-template-unknown-variable``):
      such a variable cannot be expanded, so `assemble` would raise on it. This
      reverse check is skipped once ``tiled-ndarray.shape-rank`` has fired, since
      the axis/tile alignment is then unreliable.

    Parameters
    ----------
    arr
        The tiled array to check.
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        Each tile-set rule violation, in document order across tile sets.

    Examples
    --------
    A tile larger than the array along an axis is flagged, with the offending
    tile set and axis in the JSON Pointer:

    >>> from covjson_msgspec.range import TileSet
    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(5, None), url_template="{t}.covjson"),),
    ... )
    >>> (issue,) = _validate_tiled_ndarray(arr, ())
    >>> issue.code == "tiled-ndarray.tile-shape-too-large"
    True
    >>> issue.at
    '/tileSets/0/tileShape/0'

    A subdivided axis whose ordinal the template omits is flagged:

    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(1, None), url_template="tile.covjson"),),
    ... )
    >>> (issue,) = _validate_tiled_ndarray(arr, ())
    >>> issue.code == "tiled-ndarray.url-template-missing-variable"
    True
    >>> issue.at
    '/tileSets/0/urlTemplate'

    A template variable that names no subdivided axis is flagged too:

    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}-{z}.cov"),),
    ... )
    >>> (issue,) = _validate_tiled_ndarray(arr, ())
    >>> issue.code == "tiled-ndarray.url-template-unknown-variable"
    True
    >>> issue.at
    '/tileSets/0/urlTemplate'
    """
    rank_ok = len(arr.axis_names) == len(arr.shape)

    # axisNames and shape must rank-match (as for NdArray). __post_init__ pins each
    # tileShape to shape's rank but not axisNames, so a mismatch surfaces here. The
    # per-tile-set rules are delegated to `_tile_set_issues`.
    shape_rank: tuple[Issue, ...] = (
        () if rank_ok else (TiledNdArrayShapeRank(at=_ptr(path, "shape")),)
    )

    return chain(
        shape_rank,
        chain.from_iterable(
            _tile_set_issues(arr, ts, tile_set, path, rank_ok=rank_ok)
            for ts, tile_set in enumerate(arr.tile_sets)
        ),
    )


def _range_axis_issue(
    arr: NdArray, domain: Domain, index: int, name: str, path: tuple[str | int, ...]
) -> Issue | None:
    """Return the at-most-one issue for one of a range's axes, else ``None``.

    The range axis ``name`` (at position ``index``) must be a real domain axis
    (else ``coverage.range-axis-not-in-domain``); when it is, the range's size
    along it must equal the domain axis's ``len()`` (else
    ``coverage.range-shape-mismatch``).

    Parameters
    ----------
    arr
        The inline range to check.
    domain
        The coverage's (inline) domain.
    index
        The axis's position in the range's ``axisNames`` / ``shape``.
    name
        The axis name at ``index``.
    path
        The reference-token path to ``arr``, built via `_ptr` for the issue.

    Returns
    -------
    Issue or None
        The single issue for this axis, or ``None`` when it lines up.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, NdArray
    >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.regular(0.0, 10.0, 2))
    >>> arr = NdArray(data_type="float", values=(1.0,), shape=(9,), axis_names=("x",))
    >>> issue = _range_axis_issue(arr, dom, 0, "x", ("ranges", "v"))
    >>> issue.code == "coverage.range-shape-mismatch"
    True
    """
    if name not in domain.axes:
        return CoverageRangeAxisNotInDomain(
            axis=name, at=_ptr(path, "axisNames", index)
        )

    if index < len(arr.shape):
        axis_len = len(domain.axes[name])

        if arr.shape[index] != axis_len:
            return CoverageRangeShapeMismatch(
                axis=name,
                range_size=arr.shape[index],
                domain_size=axis_len,
                at=_ptr(path, "shape", index),
            )

    return None


def _check_range_against_domain(
    arr: NdArray, domain: Domain, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield where a range's axes fail to line up with the domain's.

    Maps `_range_axis_issue` over each of the range's ``axisNames`` and keeps the
    non-``None`` results. Only meaningful with an inline `Domain`; a URL-reference
    domain skips this (its axes are unfetched).

    Parameters
    ----------
    arr
        The inline range to check.
    domain
        The coverage's (inline) domain.
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per misaligned range axis, in ``axisNames`` order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, NdArray
    >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 2), y=Axis.regular(0.0, 10.0, 2))
    >>> arr = NdArray(
    ...     data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("q",)
    ... )
    >>> [i.code for i in _check_range_against_domain(arr, dom, ("ranges", "v"))] == [
    ...     "coverage.range-axis-not-in-domain"
    ... ]
    True
    """
    return (
        issue
        for index, name in enumerate(arr.axis_names)
        if (issue := _range_axis_issue(arr, domain, index, name, path)) is not None
    )


def _check_categorical_codes(
    arr: NdArray, param: Parameter | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a categorical range's values that are not defined codes.

    Only applies when the parameter is categorical (its observed property has
    ``categories``) and carries a ``category_encoding``; otherwise it yields
    nothing. The encoding's values (each a single code or a tuple of codes) are
    flattened into the set of valid codes, and every non-null range value must be
    an integer in that set (else ``range.invalid-category-code``). This is one of
    the value-scanning checks gated behind ``check_values=True``.

    Parameters
    ----------
    arr
        The inline range whose values are scanned.
    param
        The parameter the range belongs to, or ``None`` when unknown (skips).
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One ``range.invalid-category-code`` per undefined code, in value order.
    """
    if param is None or param.observed_property.categories is None:
        return

    if (encoding := param.category_encoding) is None:
        return

    # Each encoding entry is a single code or a tuple of codes; normalize a bare
    # code to a 1-tuple so one comprehension flattens them all.
    valid = {
        code
        for entry in encoding.values()
        for code in (entry if isinstance(entry, tuple) else (entry,))
    }

    yield from (
        RangeInvalidCategoryCode(value=value, at=_ptr(path, "values", i))
        for i, value in enumerate(arr.values)
        if value is not None and (not isinstance(value, int) or value not in valid)
    )


def _matches_data_type(value: float | int | str, data_type: str) -> bool:
    """Whether a non-null range value is valid for a CoverageJSON ``dataType``.

    * ``"float"``: a real number, so a Python ``int`` or ``float`` (a JSON
      integer like ``5`` is a valid float value and decodes to a Python ``int``,
      so requiring ``float`` would reject spec-valid data); ``bool`` excluded.
    * ``"integer"``: a Python ``int``, with ``bool`` excluded. A whole-valued
      float like ``1.0`` is rejected: its type is ``float``.
    * ``"string"``: a Python ``str``.

    Parameters
    ----------
    value
        A non-null range value.
    data_type
        The range's ``dataType``.

    Returns
    -------
    bool
        Whether ``value`` is a valid instance of ``data_type``.

    Examples
    --------
    >>> _matches_data_type(1.5, "integer")
    False
    >>> _matches_data_type(5, "float")  # a JSON integer is a valid float value
    True
    >>> _matches_data_type("a", "string")
    True
    """
    if data_type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if data_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)

    return isinstance(value, str)  # "string"


# The narrow element type per ``dataType``, for the fast screen in
# `_check_value_data_types`. ``"float"`` keeps ``int`` (a JSON integer like ``5``
# is a spec-valid float value) and excludes ``str``; the three mirror
# `_matches_data_type` exactly (``bool`` is excluded by msgspec's strict
# ``convert``, ``None`` is always allowed as missing data).
_NARROW_VALUE_TYPE: dict[str, Any] = {
    "float": tuple[int | float | None, ...],
    "integer": tuple[int | None, ...],
    "string": tuple[str | None, ...],
}


def _check_value_data_types(
    arr: NdArray, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield each value that does not match the declared ``dataType``.

    The spec requires an `NdArray`'s ``values`` to match its ``dataType``, but
    decoding cannot enforce this: msgspec validates ``values`` against the
    ``float | int | str`` union but cannot distinguish the narrower ``dataType``
    within it (a ``float`` value passes even when ``dataType`` is ``"integer"``).
    So the check is done here, deterministically, via `_matches_data_type`
    (``None`` is always allowed: missing data).

    This is one of the value-scanning checks gated behind ``check_values=True``;
    it is O(number of values). An offending value yields one
    ``range.value-type-mismatch`` issue (ERROR). A fast path first screens the
    whole tuple with a single strict `msgspec.convert` (native, much cheaper than
    the per-element scan on large arrays); the per-element scan runs only when
    that screen finds a nonconforming value, enumerating every mismatch with its
    pointer, so the reported issues are identical either way.

    Parameters
    ----------
    arr
        The inline range whose values are scanned.
    path
        The reference-token path to ``arr``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        One ``range.value-type-mismatch`` per offending value, in value order.

    Examples
    --------
    A non-integer value in an ``"integer"`` range is reported, with its index in
    the JSON Pointer:

    >>> arr = NdArray(data_type="integer", values=(1, 1.5, None))
    >>> (issue,) = _check_value_data_types(arr, ("ranges", "v"))
    >>> issue.code == "range.value-type-mismatch"
    True
    >>> issue.at
    '/ranges/v/values/1'

    A ``"float"`` range accepts integer-written values (no issues):

    >>> floats = NdArray(data_type="float", values=(5, 5.0))
    >>> list(_check_value_data_types(floats, ()))
    []
    """
    data_type = arr.data_type

    # Screen the whole tuple in one native pass; on success (the common case)
    # there are no issues and the Python loop never runs. Only a nonconforming
    # array pays the per-element scan, which then reports every mismatch (convert
    # stops at the first) so the issue stream is identical to the scan alone.
    try:
        msgspec.convert(arr.values, _NARROW_VALUE_TYPE[data_type], strict=True)
    except msgspec.ValidationError:
        return (
            RangeValueTypeMismatch(
                value=value, data_type=data_type, at=_ptr(path, "values", i)
            )
            for i, value in enumerate(arr.values)
            if value is not None and not _matches_data_type(value, data_type)
        )

    return iter(())


def _unit_i18n_issues(
    unit: Unit | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `Unit`'s language-tag issues (its ``label``, if present).

    Parameters
    ----------
    unit
        The unit to check, or ``None`` when absent (yields nothing).
    path
        The reference-token path to ``unit``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The ``label``'s language-tag issues (`_language_tag_issues`).

    Examples
    --------
    >>> list(_unit_i18n_issues(Unit(symbol="K"), ("unit",)))
    []
    >>> bad = Unit(label={"en_US": "kelvin"})
    >>> [i.at for i in _unit_i18n_issues(bad, ("unit",))]
    ['/unit/label/en_US']
    """
    if unit is None:
        return

    yield from _language_tag_issues(unit.label, (*path, "label"))


def _category_i18n_issues(
    category: Category, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `Category`'s language-tag issues (``label``/``description``).

    Parameters
    ----------
    category
        The category to check.
    path
        The reference-token path to ``category``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        ``label`` issues first, then ``description`` issues.

    Examples
    --------
    >>> bad = Category(id="1", label={"en_US": "Water"})
    >>> [i.at for i in _category_i18n_issues(bad, ("categories", 0))]
    ['/categories/0/label/en_US']
    """
    yield from _label_description_i18n_issues(
        category.label, category.description, path
    )


def _observed_property_i18n_issues(
    op: ObservedProperty | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield an `ObservedProperty`'s language-tag issues, including its categories.

    Parameters
    ----------
    op
        The observed property to check, or ``None`` when absent (yields
        nothing).
    path
        The reference-token path to ``op``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        ``label`` issues, then ``description`` issues, then each
        ``categories[i]``'s issues (`_category_i18n_issues`), in order.

    Examples
    --------
    >>> land_cover = ObservedProperty(
    ...     label={"en": "Land cover"},
    ...     categories=(Category(id="1", label={"en_US": "Water"}),),
    ... )
    >>> issues = _observed_property_i18n_issues(land_cover, ("observedProperty",))
    >>> [i.at for i in issues]
    ['/observedProperty/categories/0/label/en_US']
    """
    if op is None:
        return

    yield from _label_description_i18n_issues(op.label, op.description, path)
    yield from chain.from_iterable(
        _category_i18n_issues(category, (*path, "categories", i))
        for i, category in enumerate(op.categories or ())
    )


def _parameter_i18n_issues(
    param: Parameter, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `Parameter`'s language-tag issues, including nested members.

    Checks the parameter's ``observedProperty`` (`_observed_property_i18n_issues`,
    encoded first on the wire), then its own ``label``/``description``, then
    its ``unit`` (`_unit_i18n_issues`).

    Parameters
    ----------
    param
        The parameter to check.
    path
        The reference-token path to ``param``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The parameter's issues, in wire-field order.

    Examples
    --------
    >>> from covjson_msgspec import i18n
    >>> temp = Parameter.continuous(
    ...     ObservedProperty(label=i18n("Air temperature")),
    ...     Unit(label={"en_US": "kelvin"}),
    ... )
    >>> [i.at for i in _parameter_i18n_issues(temp, ("parameters", "t"))]
    ['/parameters/t/unit/label/en_US']
    """
    yield from _observed_property_i18n_issues(
        param.observed_property, (*path, "observedProperty")
    )
    yield from _label_description_i18n_issues(param.label, param.description, path)
    yield from _unit_i18n_issues(param.unit, (*path, "unit"))


def _parameter_group_i18n_issues(
    group: ParameterGroup, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a `ParameterGroup`'s language-tag issues, including nested members.

    Checks the group's own ``label``/``description`` and, when present, its
    ``observedProperty`` (`_observed_property_i18n_issues`).

    Parameters
    ----------
    group
        The parameter group to check.
    path
        The reference-token path to ``group``, built via `_ptr` for each issue.

    Yields
    ------
    Issue
        The group's issues, in wire-field order.

    Examples
    --------
    >>> bad = ParameterGroup(members=("u", "v"), label={"en_US": "Wind"})
    >>> [i.at for i in _parameter_group_i18n_issues(bad, ("parameterGroups", 0))]
    ['/parameterGroups/0/label/en_US']
    """
    yield from _label_description_i18n_issues(group.label, group.description, path)
    yield from _observed_property_i18n_issues(
        group.observed_property, (*path, "observedProperty")
    )


def _validate_parameter_groups(
    coverage: Coverage,
    parameters: Mapping[str, Parameter],
    path: tuple[str | int, ...],
) -> Iterator[Issue]:
    """Yield each parameter group's references to unknown members.

    A parameter group bundles the keys of parameters it groups together; any
    member not present in the coverage's ``parameters`` is reported
    (``parameter-group.unknown-member``).

    Parameters
    ----------
    coverage
        The coverage whose `~covjson_msgspec.coverage.Coverage.parameter_groups`
        are checked.
    parameters
        The coverage's parameters (the set of valid member keys).
    path
        The reference-token path to ``coverage``, built via `_ptr` per group.

    Yields
    ------
    Issue
        One issue per unknown member, in group then member order.
    """
    for i, group in enumerate(coverage.parameter_groups or ()):
        yield from (
            ParameterGroupUnknownMember(
                member=member, at=_ptr(path, "parameterGroups", i)
            )
            for member in group.members
            if member not in parameters
        )


def _validate_ranges(
    coverage: Coverage,
    domain: Domain | str,
    parameters: Mapping[str, Parameter] | None,
    path: tuple[str | int, ...],
    check_values: bool,
) -> Iterator[Issue]:
    """Yield each of a coverage's range issues.

    For each range: flag an error when it has no matching parameter in scope
    (``coverage.range-without-parameter``); for an inline `NdArray`, check its
    shape is self-consistent (`_validate_ndarray`), it aligns with an inline
    domain (`_check_range_against_domain`), and, when ``check_values``, its values
    match its ``dataType`` (`_check_value_data_types`) and its categorical codes
    are defined (`_check_categorical_codes`); for an inline `TiledNdArray`, check
    its tile sets (`_validate_tiled_ndarray`). The ``dataType`` check needs no
    parameter, so it runs for every range; the categorical check runs only when
    ``parameters`` is set.

    Parameters
    ----------
    coverage
        The coverage whose `~covjson_msgspec.coverage.Coverage.ranges` are checked.
    domain
        The coverage's domain; the range-vs-domain check runs only when it is an
        inline `Domain` (a URL reference is unfetched yet spec-valid).
    parameters
        The coverage's parameters, or ``None`` when undescribed.
    path
        The reference-token path to ``coverage``, built via `_ptr` per range.
    check_values
        Whether to run the value-scanning checks (value ``dataType`` match and
        categorical codes).

    Yields
    ------
    Issue
        Each range's issues, in range then check order.
    """
    for key, range_ in coverage.ranges.items():
        range_path = (*path, "ranges", key)

        if parameters is not None and key not in parameters:
            yield CoverageRangeWithoutParameter(key=key, at=_ptr(range_path))

        if isinstance(range_, NdArray):
            yield from _validate_ndarray(range_, range_path)

            if isinstance(domain, Domain):
                yield from _check_range_against_domain(range_, domain, range_path)

            if check_values:
                yield from _check_value_data_types(range_, range_path)

                if parameters is not None:
                    yield from _check_categorical_codes(
                        range_, parameters.get(key), range_path
                    )
        elif isinstance(range_, TiledNdArray):
            yield from _validate_tiled_ndarray(range_, range_path)


def _validate_coverage(
    coverage: Coverage,
    path: tuple[str | int, ...],
    check_values: bool,
    axis_order_checker: AxisOrderChecker | None = None,
) -> Iterator[Issue]:
    """Yield one coverage's issues end to end.

    Composes the per-coverage rules: an inline domain's issues
    (`_validate_domain`), the coverage's ``parameters`` presence (a
    ``coverage.missing-parameters`` issue) or its parameter groups
    (`_validate_parameter_groups`), each parameter's and parameter group's
    language-tag issues (`_parameter_i18n_issues` / `_parameter_group_i18n_issues`,
    run unconditionally: a group's own label is checkable even when
    ``coverage.missing-parameters`` also fires), and every range
    (`_validate_ranges`). A URL-reference domain contributes no domain or
    range-vs-domain issues silently (it is unfetched yet spec-valid; see
    `validate`'s Notes).

    Parameters
    ----------
    coverage
        The coverage to validate.
    path
        The reference-token path to ``coverage``, built via `_ptr` for each issue.
    check_values
        Whether to run the value-scanning checks (categorical codes).
    axis_order_checker
        The axis-ordering policy forwarded to the domain's monotonic-axis check;
        ``None`` uses `require_monotonic`.

    Yields
    ------
    Issue
        The coverage's issues, in document order: domain, parameters (including
        parameter and parameter-group language-tag issues), ranges.
    """
    domain = coverage.domain
    parameters = coverage.parameters

    domain_issues: Iterable[Issue] = (
        _validate_domain(
            domain,
            coverage.effective_domain_type,
            (*path, "domain"),
            check_values,
            axis_order_checker,
        )
        if isinstance(domain, Domain)
        else ()
    )

    # Spec 6.4: a coverage MUST carry `parameters` unless it is a member of a
    # collection that supplies them. `_validate_collection` resolves each member
    # first (inheriting the collection's parameters), so an `UNSET` here means
    # none is in scope. Unlike referencing, this does not depend on the domain
    # form: a URL-reference domain still needs the coverage's own parameters.
    parameter_issues: Iterable[Issue] = (
        (CoverageMissingParameters(at=_ptr(path, "parameters")),)
        if parameters is UNSET
        else _validate_parameter_groups(coverage, parameters, path)
    )

    parameter_i18n_issues = chain.from_iterable(
        _parameter_i18n_issues(param, (*path, "parameters", key))
        for key, param in (parameters or {}).items()
    )
    parameter_group_i18n_issues = chain.from_iterable(
        _parameter_group_i18n_issues(group, (*path, "parameterGroups", i))
        for i, group in enumerate(coverage.parameter_groups or ())
    )

    # `_validate_ranges` wants `dict | None`. Only genuine absence (`UNSET`)
    # maps to None; a present empty `{}` must stay `{}` so that a range without a
    # matching parameter is still flagged (`range-without-parameter`).
    param_map = None if parameters is UNSET else parameters

    return chain(
        domain_issues,
        parameter_issues,
        parameter_i18n_issues,
        parameter_group_i18n_issues,
        _validate_ranges(coverage, domain, param_map, path, check_values),
    )


def _validate_collection(
    collection: CoverageCollection,
    path: tuple[str | int, ...],
    check_values: bool,
    axis_order_checker: AxisOrderChecker | None = None,
) -> Iterator[Issue]:
    """Yield every member's issues, in member order.

    Two views of each member are needed. The domainType placement check
    (`_member_domain_type_issues`) reads the *raw* member, whose own
    ``domain_type`` still distinguishes an omitted-and-inherited type from an
    explicitly-restated one; the rest run on the *resolved* member, which has the
    collection's parameters / ``domainType`` inherited. `zip` keeps the two
    aligned. Per member the placement issue precedes the member's other issues,
    holding document order (``domainType`` is an early Coverage member).

    Parameters
    ----------
    collection
        The collection to validate.
    path
        The reference-token path to ``collection``, built via `_ptr` per member.
    check_values
        Whether to run the value-scanning checks (passed through to each member).
    axis_order_checker
        The axis-ordering policy passed through to each member; ``None`` uses
        `require_monotonic`.

    Yields
    ------
    Issue
        Each member's issues, in member order.
    """
    return chain.from_iterable(
        chain(
            _member_domain_type_issues(
                raw, collection.domain_type or None, (*path, "coverages", i)
            ),
            _validate_coverage(
                resolved, (*path, "coverages", i), check_values, axis_order_checker
            ),
        )
        for i, (raw, resolved) in enumerate(
            zip(collection.coverages, collection.resolved_coverages(), strict=True)
        )
    )


def _member_domain_type_issues(
    coverage: Coverage, collection_domain_type: str | None, path: tuple[str | int, ...]
) -> Iterator[Issue]:
    """Yield a member coverage's ``domainType`` placement issue against its collection.

    When a collection sets ``domainType`` it indicates it contains only coverages
    of that type (Spec 6.5), and a member SHOULD omit its own (Spec 6.4). A member
    whose declared type differs from the collection's falsifies that claim and
    draws an error (`CoverageDomainTypeConflict`); one carrying a coverage-level
    ``domainType`` equal to the collection's SHOULD have omitted it and draws a
    warning (`CoverageDomainTypeNotOmitted`). A member that declares no type of its
    own, or a collection that sets none, yields nothing.

    The conflict compares the member's *declared* type: its inline domain's
    ``domainType`` if it sets one, else its coverage-level ``domainType``, read
    from the raw member before collection inheritance. So a type declared at either
    level is checked (not just the coverage-level member), while an omitted type
    inherited from the collection is not flagged.

    Parameters
    ----------
    coverage
        The raw member coverage (before collection inheritance is applied).
    collection_domain_type
        The collection's own ``domain_type``, or ``None`` when it sets none.
    path
        The reference-token path to the member coverage, built via `_ptr`.

    Yields
    ------
    Issue
        At most one issue, at ``<path>/domainType`` (or ``<path>/domain/domainType``
        for a conflicting type declared on the inline domain).

    Examples
    --------
    >>> from covjson_msgspec import Coverage
    >>> member = Coverage(
    ...     domain="https://example.org/d.covjson", domain_type="Grid", ranges={}
    ... )

    A value differing from the collection's is an error:

    >>> [str(i) for i in _member_domain_type_issues(member, "Point", ("coverages", 0))]
    ["coverage domainType 'Grid' conflicts with its collection's 'Point'"]

    An equal coverage-level value is the SHOULD-omit warning:

    >>> [i.code for i in _member_domain_type_issues(member, "Grid", ("coverages", 0))]
    ['coverage.domain-type-not-omitted']

    A collection that sets no ``domainType`` yields nothing:

    >>> list(_member_domain_type_issues(member, None, ("coverages", 0)))
    []

    A type declared on the member's inline domain is compared too, so a
    domain-level conflict is caught with the coverage-level member omitted (and the
    finding points at the domain):

    >>> from covjson_msgspec import Axis, Domain
    >>> inline = Coverage(
    ...     domain=Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid"),
    ...     ranges={},
    ... )
    >>> [
    ...     (i.code, i.at)
    ...     for i in _member_domain_type_issues(inline, "Point", ("coverages", 0))
    ... ]
    [('coverage.domain-type-conflict', '/coverages/0/domain/domainType')]

    An empty-string domain-level type resolves to the coverage-level type (as
    `effective_domain_type` does), so the finding's payload and pointer agree on
    the coverage level, not the empty domain member:

    >>> mixed = Coverage(
    ...     domain=Domain(axes={"x": Axis.listed((1.0,))}, domain_type=""),
    ...     domain_type="Grid",
    ...     ranges={},
    ... )
    >>> [
    ...     (i.code, i.domain_type, i.at)
    ...     for i in _member_domain_type_issues(mixed, "Point", ("coverages", 0))
    ... ]
    [('coverage.domain-type-conflict', 'Grid', '/coverages/0/domainType')]
    """
    if collection_domain_type is None:
        return

    # The member's own declared type: its inline domain's domainType if it sets
    # one, else its coverage-level domainType (pre-inheritance, via
    # `effective_domain_type`). Comparing the declared type -- not just the
    # coverage-level member -- means a type declared on the inline domain that
    # conflicts with the collection is caught too.
    declared_type = coverage.effective_domain_type

    if declared_type is None:
        return

    if declared_type != collection_domain_type:
        # Point the finding wherever the conflicting type is declared: the inline
        # domain if it sets one (it wins over the coverage level), else the
        # coverage-level member. The `domain.domain_type` truthiness check matches
        # how `effective_domain_type` resolved `declared_type` (`declared or ...`),
        # so an empty-string domain type is "absent" here as it is there.
        domain = coverage.domain
        at = (
            _ptr(path, "domain", "domainType")
            if isinstance(domain, Domain) and domain.domain_type
            else _ptr(path, "domainType")
        )
        yield CoverageDomainTypeConflict(
            domain_type=declared_type,
            collection_domain_type=collection_domain_type,
            at=at,
        )
    elif coverage.domain_type is not UNSET:
        # The type matches, but the coverage still carries its own domainType
        # member, which Spec 6.4 says it SHOULD omit.
        yield CoverageDomainTypeNotOmitted(
            domain_type=coverage.domain_type, at=_ptr(path, "domainType")
        )
