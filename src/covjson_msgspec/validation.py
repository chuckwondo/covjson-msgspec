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

`validate` collects `Issue` records rather than raising on the first problem, so
a caller sees every error and warning at once, each with a stable ``code`` and a
JSON Pointer ``path``. Pass ``mode="raise"`` to raise a `CovJSONValidationError`
instead when any error-severity issue is found, and ``check_values=True`` to add
the value-scanning checks that are skipped by default (each value matching its
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
from collections.abc import Iterable, Iterator
from itertools import chain
from typing import Literal, assert_never

import msgspec

from covjson_msgspec.axis import Axis
from covjson_msgspec.coverage import (
    Coverage,
    CoverageCollection,
    CoverageJSON,
)
from covjson_msgspec.domain import Domain
from covjson_msgspec.parameter import Parameter
from covjson_msgspec.range import NdArray, TiledNdArray, TileSet


class Severity(enum.StrEnum):
    """How serious a validation `Issue` is."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(enum.StrEnum):
    """The stable codes emitted by this library's validation checks.

    This library is the sole producer of validation codes -- there is no seam
    for an external check to emit one (the `DOMAIN_TYPE_RULES` registry only
    supplies axis-constraint *data*, which the built-in checks turn into these
    same codes). So the set is closed, and `Issue.code` is typed `IssueCode`
    rather than a bare ``str``. This is the opposite of `DomainType`, which
    stays a bare ``str`` on `Domain.domain_type` because the spec forces that
    field open (documents may carry custom domain-type URIs). A ``StrEnum``
    member is a ``str``, so consumers may still match a code with ``==`` against
    either the member or its plain-string literal.

    Each member name is its ``category.key`` value uppercased, with ``.`` and
    ``-`` replaced by ``_`` (so ``parameter-group.unknown-member`` becomes
    `PARAMETER_GROUP_UNKNOWN_MEMBER`).
    The leading ``category`` segment is a loose producer-side grouping, not a
    stable taxonomy: range-related findings currently live under both
    ``range.*`` (`RANGE_VALUE_TYPE_MISMATCH`, `RANGE_INVALID_CATEGORY_CODE`) and
    ``coverage.range-*`` (`COVERAGE_RANGE_WITHOUT_PARAMETER`,
    `COVERAGE_RANGE_SHAPE_MISMATCH`, `COVERAGE_RANGE_AXIS_NOT_IN_DOMAIN`).
    Match on a whole code; broad category matching is intentionally not offered
    until that overlap is reconciled (see ADR-0003). Use `Issue.path` for
    locality-based matching.
    """

    DOMAIN_MISSING_AXIS = "domain.missing-axis"
    DOMAIN_AXIS_NOT_SINGLE = "domain.axis-not-single"
    DOMAIN_COMPOSITE_DATA_TYPE = "domain.composite-data-type"
    DOMAIN_EXTRA_AXIS_NOT_SINGLE = "domain.extra-axis-not-single"
    DOMAIN_MISSING_REFERENCING = "domain.missing-referencing"
    NDARRAY_SHAPE_RANK = "ndarray.shape-rank"
    NDARRAY_VALUE_COUNT = "ndarray.value-count"
    TILED_NDARRAY_SHAPE_RANK = "tiled-ndarray.shape-rank"
    TILED_NDARRAY_TILE_SHAPE_TOO_LARGE = "tiled-ndarray.tile-shape-too-large"
    TILED_NDARRAY_TILE_SHAPE_NOT_POSITIVE = "tiled-ndarray.tile-shape-not-positive"
    TILED_NDARRAY_URL_TEMPLATE_MISSING_VARIABLE = (
        "tiled-ndarray.url-template-missing-variable"
    )
    TILED_NDARRAY_URL_TEMPLATE_UNKNOWN_VARIABLE = (
        "tiled-ndarray.url-template-unknown-variable"
    )
    COVERAGE_MISSING_PARAMETERS = "coverage.missing-parameters"
    COVERAGE_RANGE_WITHOUT_PARAMETER = "coverage.range-without-parameter"
    COVERAGE_RANGE_AXIS_NOT_IN_DOMAIN = "coverage.range-axis-not-in-domain"
    COVERAGE_RANGE_SHAPE_MISMATCH = "coverage.range-shape-mismatch"
    RANGE_VALUE_TYPE_MISMATCH = "range.value-type-mismatch"
    RANGE_INVALID_CATEGORY_CODE = "range.invalid-category-code"
    PARAMETER_GROUP_UNKNOWN_MEMBER = "parameter-group.unknown-member"


class Issue(msgspec.Struct, frozen=True):
    """One validation finding.

    Attributes
    ----------
    code
        A stable, machine-readable `IssueCode` (e.g.
        `IssueCode.DOMAIN_MISSING_AXIS`). A member is a ``str``, so it also
        compares ``==`` to its plain-string literal (``"domain.missing-axis"``).
    message
        A human-readable description.
    path
        A JSON Pointer (RFC 6901) to the offending location in the document.
    severity
        `Severity.ERROR` (the default) or `Severity.WARNING`.
    """

    code: IssueCode
    message: str
    path: str
    severity: Severity = Severity.ERROR


class CovJSONValidationError(Exception):
    """Raised by ``validate(..., mode="raise")`` when an error issue is found.

    The full list of error-severity `Issue` records is available on the
    ``issues`` attribute.
    """

    def __init__(self, issues: tuple[Issue, ...]) -> None:
        """Store the error issues and build a summary message from the first one."""
        self.issues = tuple(issues)
        count = len(self.issues)
        summary = self.issues[0].message if self.issues else "validation failed"
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
    inheritance first), a `TiledNdArray`'s tile sets are well-formed, and (with
    ``check_values=True``) every value matches its range's ``dataType`` and
    categorical codes are defined. Reach for it when you need those
    spec-conformance guarantees, e.g. before publishing a document or when
    ingesting one from an untrusted source.

    Parameters
    ----------
    obj
        Any decoded CoverageJSON document.
    check_values
        Also run the checks that scan range values: every value matches its
        range's ``dataType`` (``range.value-type-mismatch``), and categorical
        codes are defined in the parameter's encoding
        (``range.invalid-category-code``). Off by default because it is
        O(number of values).
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
    ...     Axis, Coverage, Domain, GeographicCRS, NdArray, ReferenceSystemConnection
    ... )
    >>> ref = ReferenceSystemConnection(
    ...     coordinates=("x", "y"), system=GeographicCRS()
    ... )
    >>> grid = Domain.grid(
    ...     x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3), referencing=[ref]
    ... )
    >>> validate(grid)
    []

    A domain with no ``referencing`` in scope is a spec violation (an error):

    >>> bare = Domain.grid(x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3))
    >>> validate(bare)[0].code == IssueCode.DOMAIN_MISSING_REFERENCING
    True

    A Grid domain missing its ``y`` axis yields an error issue. Built-in checks
    emit `IssueCode` members; a member is a ``str``, so match on either form:

    >>> incomplete = Domain(
    ...     axes={"x": Axis.listed((1.0,))}, domain_type="Grid", referencing=[ref]
    ... )
    >>> issue = validate(incomplete)[0]
    >>> issue.code == IssueCode.DOMAIN_MISSING_AXIS
    True
    >>> issue.code == "domain.missing-axis"
    True
    >>> issue.path
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
    >>> issue.code == IssueCode.COVERAGE_RANGE_WITHOUT_PARAMETER
    True
    >>> issue.severity.value
    'error'

    A coverage with no ``parameters`` member at all is likewise an error:

    >>> cov = Coverage(domain=point, ranges={})
    >>> validate(cov)[0].code == IssueCode.COVERAGE_MISSING_PARAMETERS
    True
    """
    issues = list(_issues(obj, check_values))

    if mode == "raise" and (
        errors := tuple(i for i in issues if i.severity is Severity.ERROR)
    ):
        raise CovJSONValidationError(errors)

    return issues


def _issues(obj: CoverageJSON, check_values: bool) -> Iterator[Issue]:
    """Yield every issue for a document, dispatching on its concrete type.

    The pure core of `validate`: it threads no accumulator and performs no
    effects, so each branch is just the composition of the relevant checkers.
    `validate` is the shell that materializes this into a list and applies
    ``mode``. The ``check_values`` flag is forwarded to the value-scanning
    checkers (which are otherwise skipped).

    Parameters
    ----------
    obj
        Any decoded CoverageJSON document.
    check_values
        Whether to run the O(number of values) value-scanning checks.

    Yields
    ------
    Issue
        Every issue found, in document order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _issues(dom, False)] == [
    ...     IssueCode.DOMAIN_MISSING_AXIS,
    ...     IssueCode.DOMAIN_MISSING_REFERENCING,
    ... ]
    True
    """
    match obj:
        case Domain():
            yield from _validate_domain(obj, obj.domain_type, "")
        case Coverage():
            yield from _validate_coverage(obj, "", check_values)
        case CoverageCollection():
            yield from _validate_collection(obj, "", check_values)
        case NdArray():
            yield from _validate_ndarray(obj, "")

            if check_values:
                yield from _check_value_data_types(obj, "")
        case TiledNdArray():
            yield from _validate_tiled_ndarray(obj, "")
        case _:
            # Exhaustiveness: a new CoverageJSON member would fail type checking
            # here until it is handled above.
            assert_never(obj)


def _ptr(prefix: str, *parts: str | int) -> str:
    """Join ``prefix`` and ``parts`` into a JSON Pointer for an `Issue.path`.

    Each part is appended as a ``/``-separated reference token. Per RFC 6901, a
    literal ``~`` and ``/`` inside a string token are escaped to ``~0`` and
    ``~1`` so they are not mistaken for the path separator; integer parts (array
    indices) are stringified as-is.

    Parameters
    ----------
    prefix
        The pointer built so far (e.g. ``"#"`` or a parent path).
    *parts
        Reference tokens to append: object keys (``str``) or array indices
        (``int``).

    Returns
    -------
    str
        The extended JSON Pointer.

    Examples
    --------
    >>> _ptr("#", "ranges", "temperature", "values", 0)
    '#/ranges/temperature/values/0'

    A ``/`` in a key is escaped so it is not read as a separator:

    >>> _ptr("#", "axes", "x/y")
    '#/axes/x~1y'
    """
    # Build a JSON Pointer, escaping "~" and "/" in string tokens (RFC 6901).
    escaped = (
        part.replace("~", "~0").replace("/", "~1")
        if isinstance(part, str)
        else str(part)
        for part in parts
    )

    return "/".join((prefix, *escaped))


def _axis_length(axis: Axis) -> int:
    """The number of coordinates an axis represents, in any of its forms.

    An axis is either a listed/tuple/polygon form (with explicit ``values``) or a
    regular form (a ``start`` / ``stop`` / ``num`` triple); `Axis.__post_init__`
    guarantees exactly one is populated. This returns ``len(values)`` for the
    former and ``num`` for the latter, so callers compare lengths without caring
    which form an axis uses.

    Parameters
    ----------
    axis
        The axis to measure.

    Returns
    -------
    int
        The coordinate count.

    Examples
    --------
    >>> _axis_length(Axis.listed((10.0, 20.0, 30.0)))
    3
    >>> _axis_length(Axis.regular(0.0, 10.0, 5))
    5
    """
    # __post_init__ guarantees exactly one form, so one of these is set.
    if axis.values is not None:
        return len(axis.values)

    assert axis.num is not None
    return axis.num


def _missing_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
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
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per missing required axis, in ``required_axes`` order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Grid"]
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.path for issue in _missing_axis_issues(dom, "Grid", rule, "")]
    ['/axes/y']
    """
    return (
        Issue(
            code=IssueCode.DOMAIN_MISSING_AXIS,
            message=f"{domain_type} domain requires a {name!r} axis",
            path=_ptr(path, "axes", name),
        )
        for name in rule.required_axes
        if name not in domain.axes
    )


def _non_single_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
) -> Iterator[Issue]:
    """Yield a ``domain.axis-not-single`` issue for each over-valued single axis.

    A ``single_valued_axes`` entry that is present yet carries more than one
    coordinate (per `_axis_length`) violates the domain type.

    Parameters
    ----------
    domain
        The domain whose axes are checked.
    domain_type
        The (known) domain type, interpolated into each message.
    rule
        The axis constraints for ``domain_type`` from `DOMAIN_TYPE_RULES`.
    path
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.

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
    >>> [issue.path for issue in _non_single_axis_issues(dom, "Point", rule, "")]
    ['/axes/x']
    """
    return (
        Issue(
            code=IssueCode.DOMAIN_AXIS_NOT_SINGLE,
            message=f"{domain_type} domain requires a single {name!r} value",
            path=_ptr(path, "axes", name),
        )
        for name in rule.single_valued_axes
        if (axis := domain.axes.get(name)) is not None and _axis_length(axis) != 1
    )


def _composite_data_type_issue(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
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
        The JSON Pointer to ``domain``, extended via `_ptr` for the issue.

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
    >>> issue = _composite_data_type_issue(dom, "Trajectory", rule, "")
    >>> issue.code == IssueCode.DOMAIN_COMPOSITE_DATA_TYPE
    True
    """
    composite = domain.axes.get("composite")

    if (
        rule.composite_data_type is not None
        and composite is not None
        and composite.data_type != rule.composite_data_type
    ):
        return Issue(
            code=IssueCode.DOMAIN_COMPOSITE_DATA_TYPE,
            message=(
                f"{domain_type} domain requires a "
                f"{rule.composite_data_type!r} composite axis"
            ),
            path=_ptr(path, "axes", "composite"),
        )

    return None


def _unexpected_axis_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
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
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.

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
    >>> [issue.path for issue in _unexpected_axis_issues(dom, "Grid", rule, "")]
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
        Issue(
            code=IssueCode.DOMAIN_EXTRA_AXIS_NOT_SINGLE,
            message=(
                f"{domain_type} domain may only add single-valued axes, "
                f"but {name!r} has multiple values"
            ),
            path=_ptr(path, "axes", name),
        )
        for name in domain.axes
        if name not in allowed and _axis_length(domain.axes[name]) != 1
    )


def _domain_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
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
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.

    Yields
    ------
    Issue
        One issue per violation, in check order.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> rule = DOMAIN_TYPE_RULES["Grid"]
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _domain_issues(dom, "Grid", rule, "")] == [
    ...     IssueCode.DOMAIN_MISSING_AXIS
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


def _validate_domain(
    domain: Domain, domain_type: str | None, path: str
) -> Iterator[Issue]:
    """Yield a domain's axis-rule and referencing violations, in document order.

    Resolves ``domain_type`` to a `DomainTypeRule` and, when one applies, yields
    the violations `_domain_issues` finds; then yields a missing-referencing issue
    when the domain carries no ``referencing`` in scope. An absent or unrecognized
    (e.g. custom URI) ``domain_type`` carries no axis rules, but the referencing
    check still applies.

    Parameters
    ----------
    domain
        The domain to validate.
    domain_type
        The effective domain type (from the domain itself, or a coverage's own
        ``domainType``); ``None`` or unrecognized means no axis rules to apply.
    path
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.

    Yields
    ------
    Issue
        Axis-rule issues first (``axes`` precedes ``referencing`` on the wire),
        then the referencing issue if any.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> [issue.code for issue in _validate_domain(dom, "Grid", "")] == [
    ...     IssueCode.DOMAIN_MISSING_AXIS,
    ...     IssueCode.DOMAIN_MISSING_REFERENCING,
    ... ]
    True
    """
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

    # Spec 6.1: a domain MUST carry `referencing` unless it is a member of a
    # collection that supplies it. `_validate_collection` resolves each member
    # first, pushing the collection's referencing into an inline domain that has
    # none, so by the time we get here an empty `referencing` means none is in
    # scope. A URL-reference domain never reaches this function (it is unfetched),
    # so the check applies only where a referencing array could actually exist.
    if not domain.referencing:
        yield Issue(
            code=IssueCode.DOMAIN_MISSING_REFERENCING,
            message="domain must have a 'referencing' member",
            path=_ptr(path, "referencing"),
        )


def _validate_ndarray(arr: NdArray, path: str) -> Iterator[Issue]:
    """Yield an `NdArray`'s internal shape-consistency issues.

    Two self-contained checks (no domain needed): ``shape`` and ``axisNames``
    must have the same rank, and the number of ``values`` must equal the product
    of ``shape`` (``math.prod(()) == 1``, so a 0-dimensional array must hold
    exactly one value). Decoding is permissive about these, so they surface here.

    Parameters
    ----------
    arr
        The inline array to check.
    path
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.

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
    >>> [issue.code for issue in _validate_ndarray(arr, "#/ranges/v")] == [
    ...     IssueCode.NDARRAY_VALUE_COUNT
    ... ]
    True

    A consistent array yields nothing:

    >>> consistent = NdArray(
    ...     data_type="float", values=(1.0,), shape=(1,), axis_names=("x",)
    ... )
    >>> list(_validate_ndarray(consistent, "#/ranges/v"))
    []
    """
    if len(arr.axis_names) != len(arr.shape):
        yield Issue(
            code=IssueCode.NDARRAY_SHAPE_RANK,
            message="shape and axisNames must have the same length",
            path=_ptr(path, "shape"),
        )

    # math.prod(()) == 1, so a 0-dimensional array must hold a single value.
    expected = math.prod(arr.shape)

    if len(arr.values) != expected:
        yield Issue(
            code=IssueCode.NDARRAY_VALUE_COUNT,
            message=(
                f"expected {expected} value(s) for shape {tuple(arr.shape)}, "
                f"got {len(arr.values)}"
            ),
            path=_ptr(path, "values"),
        )


# A single Level 1 RFC 6570 expression (e.g. ``{t}``) in a tile url template.
# Mirrors `covjson_msgspec.range._TEMPLATE_VARIABLE`; kept local so validation
# owns its own template parsing rather than importing another module's private.
_TEMPLATE_VARIABLE = re.compile(r"\{([^{}]+)\}")


def _tile_set_issues(
    arr: TiledNdArray, ts: int, tile_set: TileSet, path: str, *, rank_ok: bool
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
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.
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
    >>> [i.code for i in _tile_set_issues(arr, 0, tile_set, "#", rank_ok=True)] == [
    ...     IssueCode.TILED_NDARRAY_TILE_SHAPE_TOO_LARGE
    ... ]
    True
    """
    # __post_init__ guarantees tileShape rank-matches shape, so this zip is exact.
    # A non-null tile size must be a positive integer (the tile layout divides each
    # axis by it) not exceeding the corresponding axis.
    yield from (
        Issue(
            code=IssueCode.TILED_NDARRAY_TILE_SHAPE_TOO_LARGE,
            message=f"tileShape element {tile_dim} exceeds shape element {dim}",
            path=_ptr(path, "tileSets", ts, "tileShape", i),
        )
        for i, (tile_dim, dim) in enumerate(
            zip(tile_set.tile_shape, arr.shape, strict=True)
        )
        if tile_dim is not None and tile_dim > dim
    )

    yield from (
        Issue(
            code=IssueCode.TILED_NDARRAY_TILE_SHAPE_NOT_POSITIVE,
            message=f"tileShape element {tile_dim} must be a positive integer",
            path=_ptr(path, "tileSets", ts, "tileShape", i),
        )
        for i, tile_dim in enumerate(tile_set.tile_shape)
        if tile_dim is not None and tile_dim < 1
    )

    present_names = _TEMPLATE_VARIABLE.findall(tile_set.url_template)
    present = set(present_names)

    # A subdivided axis (non-null tileShape) MUST have a template variable. When
    # axisNames does not rank-match shape, this zip is intentionally non-strict so
    # it cannot raise -- validate() reports issues rather than raising.
    yield from (
        Issue(
            code=IssueCode.TILED_NDARRAY_URL_TEMPLATE_MISSING_VARIABLE,
            message=(
                f"urlTemplate must contain a variable for the subdivided {name!r} axis"
            ),
            path=_ptr(path, "tileSets", ts, "urlTemplate"),
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
            Issue(
                code=IssueCode.TILED_NDARRAY_URL_TEMPLATE_UNKNOWN_VARIABLE,
                message=(
                    f"urlTemplate references {name!r}, which is not a subdivided axis"
                ),
                path=_ptr(path, "tileSets", ts, "urlTemplate"),
            )
            for name in dict.fromkeys(present_names)
            if name not in subdivided
        )


def _validate_tiled_ndarray(arr: TiledNdArray, path: str) -> Iterator[Issue]:
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
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.

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
    >>> (issue,) = _validate_tiled_ndarray(arr, "#")
    >>> issue.code == IssueCode.TILED_NDARRAY_TILE_SHAPE_TOO_LARGE
    True
    >>> issue.path
    '#/tileSets/0/tileShape/0'

    A subdivided axis whose ordinal the template omits is flagged:

    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(1, None), url_template="tile.covjson"),),
    ... )
    >>> (issue,) = _validate_tiled_ndarray(arr, "#")
    >>> issue.code == IssueCode.TILED_NDARRAY_URL_TEMPLATE_MISSING_VARIABLE
    True
    >>> issue.path
    '#/tileSets/0/urlTemplate'

    A template variable that names no subdivided axis is flagged too:

    >>> arr = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("t", "x"),
    ...     shape=(4, 2),
    ...     tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}-{z}.cov"),),
    ... )
    >>> (issue,) = _validate_tiled_ndarray(arr, "#")
    >>> issue.code == IssueCode.TILED_NDARRAY_URL_TEMPLATE_UNKNOWN_VARIABLE
    True
    >>> issue.path
    '#/tileSets/0/urlTemplate'
    """
    rank_ok = len(arr.axis_names) == len(arr.shape)

    # axisNames and shape must rank-match (as for NdArray). __post_init__ pins each
    # tileShape to shape's rank but not axisNames, so a mismatch surfaces here. The
    # per-tile-set rules are delegated to `_tile_set_issues`.
    shape_rank: tuple[Issue, ...] = (
        ()
        if rank_ok
        else (
            Issue(
                code=IssueCode.TILED_NDARRAY_SHAPE_RANK,
                message="shape and axisNames must have the same length",
                path=_ptr(path, "shape"),
            ),
        )
    )

    return chain(
        shape_rank,
        chain.from_iterable(
            _tile_set_issues(arr, ts, tile_set, path, rank_ok=rank_ok)
            for ts, tile_set in enumerate(arr.tile_sets)
        ),
    )


def _range_axis_issue(
    arr: NdArray, domain: Domain, index: int, name: str, path: str
) -> Issue | None:
    """Return the at-most-one issue for one of a range's axes, else ``None``.

    The range axis ``name`` (at position ``index``) must be a real domain axis
    (else ``coverage.range-axis-not-in-domain``); when it is, the range's size
    along it must equal the domain axis's length from `_axis_length` (else
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
        The JSON Pointer to ``arr``, extended via `_ptr` for the issue.

    Returns
    -------
    Issue or None
        The single issue for this axis, or ``None`` when it lines up.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, NdArray
    >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.regular(0.0, 10.0, 2))
    >>> arr = NdArray(data_type="float", values=(1.0,), shape=(9,), axis_names=("x",))
    >>> issue = _range_axis_issue(arr, dom, 0, "x", "#/ranges/v")
    >>> issue.code == IssueCode.COVERAGE_RANGE_SHAPE_MISMATCH
    True
    """
    if name not in domain.axes:
        return Issue(
            code=IssueCode.COVERAGE_RANGE_AXIS_NOT_IN_DOMAIN,
            message=f"range axis {name!r} is not a domain axis",
            path=_ptr(path, "axisNames", index),
        )

    if index < len(arr.shape):
        axis_len = _axis_length(domain.axes[name])

        if arr.shape[index] != axis_len:
            return Issue(
                code=IssueCode.COVERAGE_RANGE_SHAPE_MISMATCH,
                message=(
                    f"range axis {name!r} has size {arr.shape[index]} but the "
                    f"domain axis has {axis_len}"
                ),
                path=_ptr(path, "shape", index),
            )

    return None


def _check_range_against_domain(
    arr: NdArray, domain: Domain, path: str
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
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.

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
    >>> [i.code for i in _check_range_against_domain(arr, dom, "#/ranges/v")] == [
    ...     IssueCode.COVERAGE_RANGE_AXIS_NOT_IN_DOMAIN
    ... ]
    True
    """
    return (
        issue
        for index, name in enumerate(arr.axis_names)
        if (issue := _range_axis_issue(arr, domain, index, name, path)) is not None
    )


def _check_categorical_codes(
    arr: NdArray, param: Parameter | None, path: str
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
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.

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
        Issue(
            code=IssueCode.RANGE_INVALID_CATEGORY_CODE,
            message=f"value {value!r} is not a defined category code",
            path=_ptr(path, "values", i),
        )
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


def _check_value_data_types(arr: NdArray, path: str) -> Iterator[Issue]:
    """Yield each value that does not match the declared ``dataType``.

    The spec requires an `NdArray`'s ``values`` to match its ``dataType``, but
    decoding cannot enforce this: msgspec validates ``values`` against the
    ``float | int | str`` union but cannot distinguish the narrower ``dataType``
    within it (a ``float`` value passes even when ``dataType`` is ``"integer"``).
    So the check is done here, deterministically, via `_matches_data_type`
    (``None`` is always allowed: missing data).

    This is one of the value-scanning checks gated behind ``check_values=True``;
    it is O(number of values). An offending value yields one
    ``range.value-type-mismatch`` issue (ERROR).

    Parameters
    ----------
    arr
        The inline range whose values are scanned.
    path
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.

    Yields
    ------
    Issue
        One ``range.value-type-mismatch`` per offending value, in value order.

    Examples
    --------
    A non-integer value in an ``"integer"`` range is reported, with its index in
    the JSON Pointer:

    >>> arr = NdArray(data_type="integer", values=(1, 1.5, None))
    >>> (issue,) = _check_value_data_types(arr, "#/ranges/v")
    >>> issue.code == IssueCode.RANGE_VALUE_TYPE_MISMATCH
    True
    >>> issue.path
    '#/ranges/v/values/1'

    A ``"float"`` range accepts integer-written values (no issues):

    >>> floats = NdArray(data_type="float", values=(5, 5.0))
    >>> list(_check_value_data_types(floats, "#"))
    []
    """
    data_type = arr.data_type

    return (
        Issue(
            code=IssueCode.RANGE_VALUE_TYPE_MISMATCH,
            message=f"value {value!r} is not a valid {data_type} value",
            path=_ptr(path, "values", i),
        )
        for i, value in enumerate(arr.values)
        if value is not None and not _matches_data_type(value, data_type)
    )


def _validate_parameter_groups(
    coverage: Coverage, parameters: dict[str, Parameter], path: str
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
        The JSON Pointer to ``coverage``, extended via `_ptr` per group.

    Yields
    ------
    Issue
        One issue per unknown member, in group then member order.
    """
    for i, group in enumerate(coverage.parameter_groups or ()):
        yield from (
            Issue(
                code=IssueCode.PARAMETER_GROUP_UNKNOWN_MEMBER,
                message=f"parameter group references unknown member {member!r}",
                path=_ptr(path, "parameterGroups", i),
            )
            for member in group.members
            if member not in parameters
        )


def _validate_ranges(
    coverage: Coverage,
    domain: Domain | str,
    parameters: dict[str, Parameter] | None,
    path: str,
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
        The JSON Pointer to ``coverage``, extended via `_ptr` per range.
    check_values
        Whether to run the value-scanning checks (value ``dataType`` match and
        categorical codes).

    Yields
    ------
    Issue
        Each range's issues, in range then check order.
    """
    for key, range_ in coverage.ranges.items():
        range_path = _ptr(path, "ranges", key)

        if parameters is not None and key not in parameters:
            yield Issue(
                code=IssueCode.COVERAGE_RANGE_WITHOUT_PARAMETER,
                message=f"range {key!r} has no matching parameter",
                path=range_path,
            )

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
    coverage: Coverage, path: str, check_values: bool
) -> Iterator[Issue]:
    """Yield one coverage's issues end to end.

    Composes the per-coverage rules: an inline domain's issues
    (`_validate_domain`), the coverage's ``parameters`` presence (a
    ``coverage.missing-parameters`` issue) or its parameter groups
    (`_validate_parameter_groups`), and every range (`_validate_ranges`). A
    URL-reference domain contributes no domain or range-vs-domain issues silently
    (it is unfetched yet spec-valid; see `validate`'s Notes).

    Parameters
    ----------
    coverage
        The coverage to validate.
    path
        The JSON Pointer to ``coverage``, extended via `_ptr` for each issue.
    check_values
        Whether to run the value-scanning checks (categorical codes).

    Yields
    ------
    Issue
        The coverage's issues, in document order: domain, parameters, ranges.
    """
    domain = coverage.domain
    parameters = coverage.parameters

    domain_issues: Iterable[Issue] = (
        _validate_domain(domain, coverage.effective_domain_type, _ptr(path, "domain"))
        if isinstance(domain, Domain)
        else ()
    )

    # Spec 6.4: a coverage MUST carry `parameters` unless it is a member of a
    # collection that supplies them. `_validate_collection` resolves each member
    # first (inheriting the collection's parameters), so a `None` here means none
    # is in scope. Unlike referencing, this does not depend on the domain form: a
    # URL-reference domain still needs the coverage's own parameters.
    parameter_issues: Iterable[Issue] = (
        (
            Issue(
                code=IssueCode.COVERAGE_MISSING_PARAMETERS,
                message="coverage must have a 'parameters' member",
                path=_ptr(path, "parameters"),
            ),
        )
        if parameters is None
        else _validate_parameter_groups(coverage, parameters, path)
    )

    return chain(
        domain_issues,
        parameter_issues,
        _validate_ranges(coverage, domain, parameters, path, check_values),
    )


def _validate_collection(
    collection: CoverageCollection, path: str, check_values: bool
) -> Iterator[Issue]:
    """Yield every member's issues.

    Resolves the collection first so each member inherits the collection's
    parameters and ``domainType``, then chains `_validate_coverage` over each at a
    ``coverages/<i>`` path.

    Parameters
    ----------
    collection
        The collection to validate.
    path
        The JSON Pointer to ``collection``, extended via `_ptr` per member.
    check_values
        Whether to run the value-scanning checks (passed through to each member).

    Yields
    ------
    Issue
        Each member's issues, in member order.
    """
    # Resolve first so inherited parameters / domainType apply to each member.
    return chain.from_iterable(
        _validate_coverage(coverage, _ptr(path, "coverages", i), check_values)
        for i, coverage in enumerate(collection.resolved_coverages())
    )
