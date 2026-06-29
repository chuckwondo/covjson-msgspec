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

Spec: [Common Domain Types](https://github.com/covjson/specification/blob/master/domain-types.md).
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterator
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
    parameter groups reference known members, and (with ``check_values=True``)
    every value matches its range's ``dataType`` and categorical codes are
    defined. Reach for it when you need those spec-conformance guarantees, e.g.
    before publishing a document or when ingesting one from an untrusted source.

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

    A range whose name matches no parameter in scope has no parameter at all, a
    MUST violation, so it is an error:

    >>> from covjson_msgspec import Coverage, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"v": NdArray(data_type="float", values=(280.0,))},
    ...     parameters={},
    ... )
    >>> issue = validate(cov)[0]
    >>> (issue.code, issue.severity.value)
    ('coverage.range-without-parameter', 'error')
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
            if check_values:
                _check_value_data_types(obj, "", issues)
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


def _domain_issues(
    domain: Domain, domain_type: str, rule: DomainTypeRule, path: str
) -> Iterator[Issue]:
    """Yield every axis-rule violation for a domain of a known type.

    The four checks `_validate_domain` applies once it has resolved a
    `DomainTypeRule`, all error-severity: a missing required axis, a
    single-valued axis that carries more than one value, a ``composite`` axis
    with the wrong ``data_type``, and a surplus axis (one outside the
    required-or-optional set) that carries more than one value. A surplus
    single-valued axis is spec-conformant and yields nothing.

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
    """
    axes = domain.axes

    yield from (
        Issue(
            code="domain.missing-axis",
            message=f"{domain_type} domain requires a {name!r} axis",
            path=_ptr(path, "axes", name),
        )
        for name in rule.required_axes
        if name not in axes
    )

    yield from (
        Issue(
            code="domain.axis-not-single",
            message=f"{domain_type} domain requires a single {name!r} value",
            path=_ptr(path, "axes", name),
        )
        for name in rule.single_valued_axes
        if (axis := axes.get(name)) is not None and _axis_length(axis) != 1
    )

    if (
        rule.composite_data_type is not None
        and (composite := axes.get("composite")) is not None
        and composite.data_type != rule.composite_data_type
    ):
        yield Issue(
            code="domain.composite-data-type",
            message=(
                f"{domain_type} domain requires a "
                f"{rule.composite_data_type!r} composite axis"
            ),
            path=_ptr(path, "axes", "composite"),
        )

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
    yield from (
        Issue(
            code="domain.extra-axis-not-single",
            message=(
                f"{domain_type} domain may only add single-valued axes, "
                f"but {name!r} has multiple values"
            ),
            path=_ptr(path, "axes", name),
        )
        for name in axes
        if name not in allowed and _axis_length(axes[name]) != 1
    )


def _validate_domain(
    domain: Domain, domain_type: str | None, path: str, issues: list[Issue]
) -> None:
    """Check a domain's axes against its domain type's rules, appending any issues.

    Resolves ``domain_type`` to a `DomainTypeRule` and, when one applies, appends
    the violations `_domain_issues` finds. An absent ``domain_type`` or an
    unrecognized (e.g. custom URI) one carries no rules, so nothing is checked.

    Parameters
    ----------
    domain
        The domain to validate.
    domain_type
        The effective domain type (from the domain itself, or a coverage's own
        ``domainType``); ``None`` or unrecognized means no rules to apply.
    path
        The JSON Pointer to ``domain``, extended via `_ptr` for each issue.
    issues
        The accumulator that findings are appended to (mutated in place).
    """
    # The effective domain type may come from the Domain itself or, inside a
    # coverage, from the coverage's own domainType (passed in by the caller).
    if domain_type is None or (rule := DOMAIN_TYPE_RULES.get(domain_type)) is None:
        return

    issues.extend(_domain_issues(domain, domain_type, rule, path))


def _validate_ndarray(arr: NdArray, path: str, issues: list[Issue]) -> None:
    """Check an `NdArray`'s internal shape consistency, appending any issues.

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
    issues
        The accumulator that findings are appended to (mutated in place).

    Examples
    --------
    A value count that disagrees with ``shape`` yields one ``ndarray.value-count``
    issue:

    >>> arr = NdArray(
    ...     data_type="float", values=(1.0, 2.0), shape=(3,), axis_names=("x",)
    ... )
    >>> issues = []
    >>> _validate_ndarray(arr, "#/ranges/v", issues)
    >>> [issue.code for issue in issues]
    ['ndarray.value-count']

    A consistent array yields nothing:

    >>> issues = []
    >>> _validate_ndarray(
    ...     NdArray(data_type="float", values=(1.0,), shape=(1,), axis_names=("x",)),
    ...     "#/ranges/v",
    ...     issues,
    ... )
    >>> issues
    []
    """
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
    """Check a range's axes line up with the domain's, appending any issues.

    For each of the range's ``axisNames``: the name must be a real domain axis
    (else ``coverage.range-axis-not-in-domain``), and the range's size along that
    axis must equal the domain axis's length from `_axis_length` (else
    ``coverage.range-shape-mismatch``). Only callable with an inline `Domain`; a
    URL-reference domain skips this (its axes are unfetched).

    Parameters
    ----------
    arr
        The inline range to check.
    domain
        The coverage's (inline) domain.
    path
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.
    issues
        The accumulator that findings are appended to (mutated in place).
    """
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
    """Check a categorical range's values are defined codes, appending any issues.

    Only applies when the parameter is categorical (its observed property has
    ``categories``) and carries a ``category_encoding``; otherwise it is a no-op.
    The encoding's values (each a single code or a tuple of codes) are flattened
    into the set of valid codes, and every non-null range value must be an integer
    in that set (else ``range.invalid-category-code``). This is one of the value-
    scanning checks gated behind ``check_values=True``.

    Parameters
    ----------
    arr
        The inline range whose values are scanned.
    param
        The parameter the range belongs to, or ``None`` when unknown (skips).
    path
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.
    issues
        The accumulator that findings are appended to (mutated in place).
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

    issues.extend(
        Issue(
            code="range.invalid-category-code",
            message=f"value {value!r} is not a defined category code",
            path=_ptr(path, "values", i),
        )
        for i, value in enumerate(arr.values)
        if value is not None and (not isinstance(value, int) or value not in valid)
    )


def _check_value_data_types(arr: NdArray, path: str, issues: list[Issue]) -> None:
    """Check each value matches the declared ``dataType``, appending any issues.

    The spec requires an `NdArray`'s ``values`` to match its ``dataType``, but
    decoding does not reliably enforce this: a parameterized decode (e.g.
    ``NdArray[int]``) can silently accept an out-of-type value because msgspec's
    generic resolution is order-sensitive (see the Notes on
    `~covjson_msgspec.range.NdArray`). So the check is done here, deterministically.
    ``None`` is always allowed (missing data). The rules per ``dataType`` are:

    * ``"float"``: a real number, so a Python ``int`` or ``float`` (a JSON
      integer like ``5`` is a valid float value and decodes to a Python ``int``,
      so requiring ``float`` here would reject spec-valid data). ``bool`` excluded.
    * ``"integer"``: a Python ``int``, with ``bool`` excluded. A whole-valued
      float like ``1.0`` is rejected: its type is ``float`` and the ``dataType``
      declares integers.
    * ``"string"``: a Python ``str``.

    This is one of the value-scanning checks gated behind ``check_values=True``;
    it is O(number of values). An offending value yields one
    ``range.value-type-mismatch`` issue (ERROR).

    Parameters
    ----------
    arr
        The inline range whose values are scanned.
    path
        The JSON Pointer to ``arr``, extended via `_ptr` for each issue.
    issues
        The accumulator that findings are appended to (mutated in place).

    Examples
    --------
    A non-integer value in an ``"integer"`` range is reported, with its index in
    the JSON Pointer:

    >>> arr = NdArray(data_type="integer", values=(1, 1.5, None))
    >>> issues = []
    >>> _check_value_data_types(arr, "#/ranges/v", issues)
    >>> [(i.code, i.path) for i in issues]
    [('range.value-type-mismatch', '#/ranges/v/values/1')]

    A ``"float"`` range accepts integer-written values (no issues):

    >>> floats = NdArray(data_type="float", values=(5, 5.0))
    >>> issues = []
    >>> _check_value_data_types(floats, "#", issues)
    >>> issues
    []
    """
    data_type = arr.data_type

    for i, value in enumerate(arr.values):
        if value is None:
            continue

        if data_type == "float":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif data_type == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:  # "string"
            ok = isinstance(value, str)

        if not ok:
            issues.append(
                Issue(
                    code="range.value-type-mismatch",
                    message=f"value {value!r} is not a valid {data_type} value",
                    path=_ptr(path, "values", i),
                )
            )


def _validate_parameter_groups(
    coverage: Coverage,
    parameters: dict[str, Parameter],
    path: str,
    issues: list[Issue],
) -> None:
    """Check each parameter group references only known members, appending issues.

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
    issues
        The accumulator that findings are appended to (mutated in place).
    """
    for i, group in enumerate(coverage.parameter_groups or ()):
        issues.extend(
            Issue(
                code="parameter-group.unknown-member",
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
    issues: list[Issue],
    check_values: bool,
) -> None:
    """Validate each of a coverage's ranges, appending any issues.

    For each range: flag an error when it has no matching parameter in scope
    (``coverage.range-without-parameter``); and, for an inline `NdArray`, check
    its shape is self-consistent (`_validate_ndarray`), it aligns with an inline
    domain (`_check_range_against_domain`), and, when ``check_values``, its values
    match its ``dataType`` (`_check_value_data_types`) and its categorical codes
    are defined (`_check_categorical_codes`). The ``dataType`` check needs no
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
    issues
        The accumulator that findings are appended to (mutated in place).
    check_values
        Whether to run the value-scanning checks (value ``dataType`` match and
        categorical codes).
    """
    for key, range_ in coverage.ranges.items():
        range_path = _ptr(path, "ranges", key)

        if parameters is not None and key not in parameters:
            issues.append(
                Issue(
                    code="coverage.range-without-parameter",
                    message=f"range {key!r} has no matching parameter",
                    path=range_path,
                )
            )

        if isinstance(range_, NdArray):
            _validate_ndarray(range_, range_path, issues)

            if isinstance(domain, Domain):
                _check_range_against_domain(range_, domain, range_path, issues)

            if check_values:
                _check_value_data_types(range_, range_path, issues)

                if parameters is not None:
                    _check_categorical_codes(
                        range_, parameters.get(key), range_path, issues
                    )


def _validate_coverage(
    coverage: Coverage, path: str, issues: list[Issue], check_values: bool
) -> None:
    """Validate one coverage end to end, appending any issues.

    Orchestrates the per-coverage rules: validates an inline domain
    (`_validate_domain`), checks each parameter group's members
    (`_validate_parameter_groups`), and checks every range (`_validate_ranges`).
    A URL-reference domain skips the domain and range-vs-domain checks silently
    (it is unfetched yet spec-valid; see `validate`'s Notes).

    Parameters
    ----------
    coverage
        The coverage to validate.
    path
        The JSON Pointer to ``coverage``, extended via `_ptr` for each issue.
    issues
        The accumulator that findings are appended to (mutated in place).
    check_values
        Whether to run the value-scanning checks (categorical codes).
    """
    domain = coverage.domain
    parameters = coverage.parameters

    if isinstance(domain, Domain):
        _validate_domain(
            domain, coverage.effective_domain_type, _ptr(path, "domain"), issues
        )

    if parameters is not None:
        _validate_parameter_groups(coverage, parameters, path, issues)

    _validate_ranges(coverage, domain, parameters, path, issues, check_values)


def _validate_collection(
    collection: CoverageCollection,
    path: str,
    issues: list[Issue],
    check_values: bool,
) -> None:
    """Validate every member of a collection, appending any issues.

    Resolves the collection first so each member inherits the collection's
    parameters and ``domainType``, then runs `_validate_coverage` on each at a
    ``coverages/<i>`` path.

    Parameters
    ----------
    collection
        The collection to validate.
    path
        The JSON Pointer to ``collection``, extended via `_ptr` per member.
    issues
        The accumulator that findings are appended to (mutated in place).
    check_values
        Whether to run the value-scanning checks (passed through to each member).
    """
    # Resolve first so inherited parameters / domainType apply to each member.
    for i, coverage in enumerate(collection.resolved_coverages()):
        _validate_coverage(coverage, _ptr(path, "coverages", i), issues, check_values)
