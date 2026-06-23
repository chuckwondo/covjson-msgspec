"""Document-level validation beyond what decoding already guarantees.

CoverageJSON is validated in tiers:

1. structural and field-level checks, performed by msgspec on decode;
2. cheap per-object invariants, performed in each struct's ``__post_init__``
   (e.g. a `Unit` needs a label or symbol); and
3. cross-cutting, document-level rules, performed here by `validate`.

`validate` collects `Issue` records rather than raising on the first problem, so
a caller sees every error and warning at once, each with a stable ``code`` and a
JSON Pointer ``path``. Pass ``mode="raise"`` to raise a `CovJSONValidationError`
instead when any error-severity issue is found, and ``check_values=True`` to add
the value-scanning checks that are skipped by default.

The per-domain-type axis rules live in the `DOMAIN_TYPE_RULES` registry, keyed by
`DomainType`. The registry is a plain dict: add or replace an entry to teach
`validate` about a custom domain type or to override a built-in rule.

Spec: [Common Domain Types](https://github.com/covjson/specification/blob/master/domain-types.md).
"""

import enum
import math
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
from covjson_msgspec.range import NdArray, TiledNdArray


class Severity(enum.StrEnum):
    """How serious a validation `Issue` is."""

    ERROR = "error"
    WARNING = "warning"


class Issue(msgspec.Struct, frozen=True):
    """One validation finding.

    Attributes
    ----------
    code
        A stable, machine-readable identifier (e.g. ``"domain.missing-axis"``).
    message
        A human-readable description.
    path
        A JSON Pointer (RFC 6901) to the offending location in the document.
    severity
        `Severity.ERROR` (the default) or `Severity.WARNING`.
    """

    code: str
    message: str
    path: str
    severity: Severity = Severity.ERROR


class CovJSONValidationError(Exception):
    """Raised by ``validate(..., mode="raise")`` when an error issue is found.

    The full list of error-severity `Issue` records is available on the
    ``issues`` attribute.
    """

    def __init__(self, issues: tuple[Issue, ...]) -> None:
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

    Parameters
    ----------
    obj
        Any decoded CoverageJSON document.
    check_values
        Also run the checks that scan range values (currently: categorical codes
        must be defined in the parameter's encoding). Off by default because it
        is O(number of values).
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
    >>> from covjson_msgspec import Axis, Domain
    >>> grid = Domain.grid(x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3))
    >>> validate(grid)
    []

    A Grid domain missing its ``y`` axis yields an error issue:

    >>> incomplete = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    >>> issue = validate(incomplete)[0]
    >>> issue.code
    'domain.missing-axis'
    >>> issue.path
    '/axes/y'

    In ``"raise"`` mode the same document raises:

    >>> validate(incomplete, mode="raise")
    Traceback (most recent call last):
        ...
    covjson_msgspec.validation.CovJSONValidationError: Grid domain requires a 'y' axis

    Not every issue is an error. Here a range has no matching parameter, which is
    a warning; ``"collect"`` mode returns it and ``"raise"`` mode would not raise:

    >>> from covjson_msgspec import Coverage, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"v": NdArray(data_type="float", values=(280.0,))},
    ...     parameters={},
    ... )
    >>> issue = validate(cov)[0]
    >>> (issue.code, issue.severity.value)
    ('coverage.range-without-parameter', 'warning')
    """
    issues: list[Issue] = []

    match obj:
        case Domain():
            _validate_domain(obj, obj.domain_type, "", issues)
        case Coverage():
            _validate_coverage(obj, "", issues, check_values)
        case CoverageCollection():
            _validate_collection(obj, "", issues, check_values)
        case NdArray():
            _validate_ndarray(obj, "", issues)
        case TiledNdArray():
            # Its only document-level rule (tileShape rank) is already enforced
            # in __post_init__, so there is nothing extra to check here.
            pass
        case _:
            # Exhaustiveness: a new CoverageJSON member would fail type checking
            # here until it is handled above.
            assert_never(obj)

    if mode == "raise" and (
        errors := tuple(i for i in issues if i.severity is Severity.ERROR)
    ):
        raise CovJSONValidationError(errors)

    return issues


def _ptr(prefix: str, *parts: str | int) -> str:
    # Build a JSON Pointer, escaping "~" and "/" in string tokens (RFC 6901).
    escaped = (
        part.replace("~", "~0").replace("/", "~1")
        if isinstance(part, str)
        else str(part)
        for part in parts
    )

    return "/".join((prefix, *escaped))


def _axis_length(axis: Axis) -> int:
    # __post_init__ guarantees exactly one form, so one of these is set.
    if axis.values is not None:
        return len(axis.values)

    assert axis.num is not None
    return axis.num


def _validate_domain(
    domain: Domain, domain_type: str | None, path: str, issues: list[Issue]
) -> None:
    # The effective domain type may come from the Domain itself or, inside a
    # coverage, from the coverage's own domainType (passed in by the caller).
    if domain_type is None:
        return

    rule = DOMAIN_TYPE_RULES.get(domain_type)

    # An unrecognized (e.g. custom URI) domain type carries no rules to check.
    if rule is None:
        return

    axes = domain.axes

    issues.extend(
        Issue(
            code="domain.missing-axis",
            message=f"{domain_type} domain requires a {name!r} axis",
            path=_ptr(path, "axes", name),
        )
        for name in rule.required_axes
        if name not in axes
    )

    for name in rule.single_valued_axes:
        if (axis := axes.get(name)) is not None and _axis_length(axis) != 1:
            issues.append(
                Issue(
                    code="domain.axis-not-single",
                    message=f"{domain_type} domain requires a single {name!r} value",
                    path=_ptr(path, "axes", name),
                )
            )

    if (
        rule.composite_data_type is not None
        and (composite := axes.get("composite")) is not None
        and composite.data_type != rule.composite_data_type
    ):
        issues.append(
            Issue(
                code="domain.composite-data-type",
                message=(
                    f"{domain_type} domain requires a "
                    f"{rule.composite_data_type!r} composite axis"
                ),
                path=_ptr(path, "axes", "composite"),
            )
        )

    allowed = set(rule.required_axes) | set(rule.optional_axes)

    issues.extend(
        Issue(
            code="domain.unexpected-axis",
            message=f"{domain_type} domain has an unexpected {name!r} axis",
            path=_ptr(path, "axes", name),
            severity=Severity.WARNING,
        )
        for name in axes
        if name not in allowed
    )


def _validate_ndarray(arr: NdArray, path: str, issues: list[Issue]) -> None:
    if len(arr.axis_names) != len(arr.shape):
        issues.append(
            Issue(
                code="ndarray.shape-rank",
                message="shape and axisNames must have the same length",
                path=_ptr(path, "shape"),
            )
        )

    # math.prod(()) == 1, so a 0-dimensional array must hold a single value.
    expected = math.prod(arr.shape)

    if len(arr.values) != expected:
        issues.append(
            Issue(
                code="ndarray.value-count",
                message=(
                    f"expected {expected} value(s) for shape {tuple(arr.shape)}, "
                    f"got {len(arr.values)}"
                ),
                path=_ptr(path, "values"),
            )
        )


def _check_range_against_domain(
    arr: NdArray, domain: Domain, path: str, issues: list[Issue]
) -> None:
    for i, name in enumerate(arr.axis_names):
        if name not in domain.axes:
            issues.append(
                Issue(
                    code="coverage.range-axis-not-in-domain",
                    message=f"range axis {name!r} is not a domain axis",
                    path=_ptr(path, "axisNames", i),
                )
            )
        elif i < len(arr.shape):
            axis_len = _axis_length(domain.axes[name])

            if arr.shape[i] != axis_len:
                issues.append(
                    Issue(
                        code="coverage.range-shape-mismatch",
                        message=(
                            f"range axis {name!r} has size {arr.shape[i]} but the "
                            f"domain axis has {axis_len}"
                        ),
                        path=_ptr(path, "shape", i),
                    )
                )


def _check_categorical_codes(
    arr: NdArray, param: Parameter | None, path: str, issues: list[Issue]
) -> None:
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

    issues.extend(
        Issue(
            code="range.invalid-category-code",
            message=f"value {value!r} is not a defined category code",
            path=_ptr(path, "values", i),
        )
        for i, value in enumerate(arr.values)
        if value is not None and (not isinstance(value, int) or value not in valid)
    )


def _validate_coverage(
    coverage: Coverage, path: str, issues: list[Issue], check_values: bool
) -> None:
    domain = coverage.domain

    # A URL-reference domain is validated only where it is inline: the domain and
    # the range-vs-domain checks below are skipped (you cannot check unfetched
    # data) without an issue, since a URL reference is spec-valid. See validate()'s
    # Notes on this partial validation.
    if isinstance(domain, Domain):
        _validate_domain(
            domain, coverage.effective_domain_type, _ptr(path, "domain"), issues
        )

    parameters = coverage.parameters

    if coverage.parameter_groups is not None and parameters is not None:
        for i, group in enumerate(coverage.parameter_groups):
            issues.extend(
                Issue(
                    code="parameter-group.unknown-member",
                    message=(f"parameter group references unknown member {member!r}"),
                    path=_ptr(path, "parameterGroups", i),
                )
                for member in group.members
                if member not in parameters
            )

    for key, range_ in coverage.ranges.items():
        range_path = _ptr(path, "ranges", key)

        if parameters is not None and key not in parameters:
            issues.append(
                Issue(
                    code="coverage.range-without-parameter",
                    message=f"range {key!r} has no matching parameter",
                    path=range_path,
                    severity=Severity.WARNING,
                )
            )

        if isinstance(range_, NdArray):
            _validate_ndarray(range_, range_path, issues)

            if isinstance(domain, Domain):
                _check_range_against_domain(range_, domain, range_path, issues)

            if check_values and parameters is not None:
                _check_categorical_codes(
                    range_, parameters.get(key), range_path, issues
                )


def _validate_collection(
    collection: CoverageCollection,
    path: str,
    issues: list[Issue],
    check_values: bool,
) -> None:
    # Resolve first so inherited parameters / domainType apply to each member.
    for i, coverage in enumerate(collection.resolved_coverages()):
        _validate_coverage(coverage, _ptr(path, "coverages", i), issues, check_values)
