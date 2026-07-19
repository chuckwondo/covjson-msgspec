"""Behavioral tests for document-level validation."""

from collections.abc import Sequence
from typing import Literal, assert_never

import msgspec
import pytest

from covjson_msgspec import (
    Axis,
    Category,
    Concept,
    Coverage,
    CoverageCollection,
    CovJSONValidationError,
    Domain,
    Issue,
    NdArray,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    ReferenceSystemConnection,
    Severity,
    TiledNdArray,
    Unit,
    i18n,
    validate,
)
from covjson_msgspec.axis import AxisValue
from covjson_msgspec.range import TileSet
from covjson_msgspec.referencing import (
    OpaqueRS,
    ReferenceSystem,
    ResolvedReferenceSystem,
)
from covjson_msgspec.validation import (
    AxisBoundsLength,
    AxisCompositeArity,
    AxisCompositeValueShape,
    AxisCoordinatesNotOmitted,
    AxisNotMonotonic,
    AxisOrderChecker,
    AxisPolygonPositionArity,
    AxisPolygonRingNotClosed,
    AxisPolygonRingTooShort,
    CoverageDomainTypeConflict,
    CoverageDomainTypeNotOmitted,
    CoverageMissingParameters,
    CoverageRangeAxisNotInDomain,
    CoverageRangeShapeMismatch,
    CoverageRangeWithoutParameter,
    DomainAxisNotSingle,
    DomainCompositeCoordinates,
    DomainCompositeDataType,
    DomainExtraAxisNotSingle,
    DomainMissingAxis,
    DomainMissingDomainType,
    DomainMissingReferencing,
    I18nEmpty,
    I18nInvalidLanguageTag,
    IdentifierMissingTargetConcept,
    NdArrayShapeRank,
    NdArrayValueCount,
    ParameterGroupUnknownMember,
    RangeInvalidCategoryCode,
    RangeValueTypeMismatch,
    TemporalLexicalForm,
    TemporalMissingCalendar,
    TiledNdArrayShapeRank,
    TiledNdArrayTileShapeNotPositive,
    TiledNdArrayTileShapeTooLarge,
    TiledNdArrayUrlTemplateMissingVariable,
    TiledNdArrayUrlTemplateUnknownVariable,
    require_monotonic,
)

# A minimal valid referencing array. Domains and coverages built for the
# axis/range checks below carry it so they isolate the one issue under test
# rather than also tripping the spec-required referencing/parameters presence
# checks (see test_missing_referencing / test_missing_parameters for those).
_REF = (
    ReferenceSystemConnection(
        coordinates=("x", "y"), system=ReferenceSystem.geographic()
    ),
)


def test_valid_grid_has_no_issues() -> None:
    grid = Domain.grid(
        x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3), referencing=_REF
    )

    assert validate(grid) == []


def test_missing_required_axis() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,))}, domain_type="Grid", referencing=_REF
    )
    (issue,) = validate(domain)

    assert issue.code == "domain.missing-axis"
    assert issue.at == "/axes/y"
    assert issue.severity is Severity.ERROR


def test_axis_not_single_valued() -> None:
    # A Point domain requires single-valued x/y.
    domain = Domain(
        axes={"x": Axis.listed((1.0, 2.0)), "y": Axis.listed((3.0,))},
        domain_type="Point",
    )
    codes = {i.code for i in validate(domain)}

    assert "domain.axis-not-single" in codes


def test_composite_data_type_mismatch() -> None:
    # Trajectory needs a "tuple" composite axis; a polygon one is wrong.
    composite = Axis(
        values=((0.0, 1.0, 2.0),), data_type="polygon", coordinates=("t", "x", "y")
    )
    domain = Domain(axes={"composite": composite}, domain_type="Trajectory")
    codes = {i.code for i in validate(domain)}

    assert "domain.composite-data-type" in codes


def test_composite_coordinates_mismatch() -> None:
    # A Trajectory's composite identifiers must be ("t","x","y","z") or
    # ("t","x","y"); ("x","y") is a well-formed tuple axis that is still wrong.
    composite = Axis(values=((0.0, 1.0),), data_type="tuple", coordinates=("x", "y"))
    domain = Domain(
        axes={"composite": composite}, domain_type="Trajectory", referencing=_REF
    )
    (issue,) = [
        i for i in validate(domain) if isinstance(i, DomainCompositeCoordinates)
    ]

    assert issue.code == "domain.composite-coordinates"
    assert issue.at == "/axes/composite"
    assert issue.severity is Severity.ERROR
    assert issue.actual == ("x", "y")
    assert issue.expected == (("t", "x", "y", "z"), ("t", "x", "y"))


@pytest.mark.parametrize(
    ("domain_type", "coordinates"),
    [
        ("Trajectory", ("t", "x", "y", "z")),
        ("Trajectory", ("t", "x", "y")),
        ("MultiPoint", ("x", "y", "z")),
        ("MultiPoint", ("x", "y")),
        ("Section", ("t", "x", "y")),
    ],
)
def test_composite_coordinates_conformant_alternatives_not_reported(
    domain_type: str, coordinates: tuple[str, ...]
) -> None:
    # Every ordering the spec permits for a type (both of Trajectory's, both of
    # MultiPoint's) must pass. A single-tuple rule would falsely flag the longer
    # forms.
    composite = Axis(
        values=(tuple(float(i) for i in range(len(coordinates))),),
        data_type="tuple",
        coordinates=coordinates,
    )
    domain = Domain(axes={"composite": composite}, domain_type=domain_type)
    codes = {i.code for i in validate(domain)}

    assert "domain.composite-coordinates" not in codes


def test_composite_coordinates_gated_on_data_type() -> None:
    # A primitive "composite" axis is the wrong dataType for a Polygon, so it
    # draws the dataType finding. The identifier check is gated on the dataType
    # already matching, so it stays silent rather than piling a second,
    # consequential finding on the same axis.
    domain = Domain(axes={"composite": Axis.listed((1.0, 2.0))}, domain_type="Polygon")
    codes = {i.code for i in validate(domain)}

    assert "domain.composite-data-type" in codes
    assert "domain.composite-coordinates" not in codes


def test_surplus_multi_valued_axis_is_an_error() -> None:
    domain = Domain(
        axes={
            "x": Axis.regular(0, 1, 2),
            "y": Axis.regular(0, 1, 2),
            "bogus": Axis.listed((1.0, 2.0)),
        },
        domain_type="Grid",
        referencing=_REF,
    )
    (issue,) = validate(domain)

    assert issue.code == "domain.extra-axis-not-single"
    assert issue.severity is Severity.ERROR
    assert issue.at == "/axes/bogus"


def test_surplus_single_valued_axis_is_conformant() -> None:
    # The spec permits any number of additional one-coordinate axes.
    domain = Domain(
        axes={
            "x": Axis.regular(0, 1, 2),
            "y": Axis.regular(0, 1, 2),
            "extra": Axis.listed((1.0,)),
        },
        domain_type="Grid",
        referencing=_REF,
    )

    assert validate(domain) == []


def test_unknown_domain_type_is_not_checked() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,))},
        domain_type="http://example/Custom",
        referencing=_REF,
    )

    assert validate(domain) == []


def test_ndarray_value_count_mismatch() -> None:
    arr = NdArray(data_type="float", values=(1.0, 2.0), shape=(3,), axis_names=("x",))
    (issue,) = validate(arr)

    assert issue.code == "ndarray.value-count"
    assert issue.at == "/values"


def test_ndarray_shape_rank_mismatch() -> None:
    arr = NdArray(data_type="float", values=(1.0,), shape=(1, 1), axis_names=("x",))
    codes = {i.code for i in validate(arr)}

    assert "ndarray.shape-rank" in codes


def test_range_shape_mismatch_against_domain() -> None:
    domain = Domain.grid(x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 2))
    # Domain x has 3 values, y has 2 -> 6 cells; range claims 3x3.
    cov = Coverage(
        domain=domain,
        ranges={
            "t": NdArray(
                data_type="float",
                values=tuple(float(i) for i in range(9)),
                shape=(3, 3),
                axis_names=("y", "x"),
            )
        },
    )
    codes = {i.code for i in validate(cov)}

    assert "coverage.range-shape-mismatch" in codes


def test_range_axis_not_in_domain() -> None:
    domain = Domain.grid(x=Axis.regular(0, 10, 2), y=Axis.regular(0, 10, 2))
    cov = Coverage(
        domain=domain,
        ranges={
            "t": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("q",)
            )
        },
    )
    issues = validate(cov)

    assert any(i.code == "coverage.range-axis-not-in-domain" for i in issues)


def test_range_without_parameter_is_an_error() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"unknown": NdArray(data_type="float", values=(1.0,))},
        parameters={"t": temp},
    )
    errors = [i for i in validate(cov) if i.severity is Severity.ERROR]

    assert any(i.code == "coverage.range-without-parameter" for i in errors)


def test_pointer_escapes_special_characters_in_a_key() -> None:
    # A range key containing "/" or "~" must be escaped in the issue's JSON
    # Pointer (RFC 6901: "~" -> "~0", "/" -> "~1"), so it is not misread as extra
    # path segments. Exercises the escaping end to end, which no corpus document
    # does; the unit behavior is pinned by the `_escape` / `_ptr` doctests.
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"a/b~c": NdArray(data_type="float", values=(1.0,))},
        parameters={},
    )
    (issue,) = [
        i for i in validate(cov) if i.code == "coverage.range-without-parameter"
    ]

    assert issue.at == "/ranges/a~1b~0c"


def test_parameter_group_unknown_member() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(1.0,))},
        parameters={"t": temp},
        parameter_groups=(ParameterGroup(members=("t", "missing"), label=i18n("Grp")),),
    )
    codes = {i.code for i in validate(cov)}

    assert "parameter-group.unknown-member" in codes


def test_categorical_code_check_is_opt_in() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(Category(id="1", label=i18n("Water")),),
    )
    param = Parameter.categorical(land_cover, {"1": 1})
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"lc": NdArray(data_type="integer", values=(1, 99))},
        parameters={"lc": param},
    )

    # Off by default: the undefined code 99 is not scanned.
    assert all(i.code != "range.invalid-category-code" for i in validate(cov))

    # Opt in: the undefined code is flagged.
    issues = validate(cov, check_values=True)
    bad = [i for i in issues if i.code == "range.invalid-category-code"]

    assert len(bad) == 1
    assert bad[0].at == "/ranges/lc/values/1"


def test_value_data_type_check_is_opt_in() -> None:
    cov = _coverage_with_range(NdArray(data_type="integer", values=(1, 1.5)))

    # Off by default: the float in an integer range is not scanned.
    assert _value_type_paths(validate(cov)) == []

    # Opt in: the float is flagged, with its index in the path.
    issues = validate(cov, check_values=True)
    bad = [i for i in issues if i.code == "range.value-type-mismatch"]

    assert len(bad) == 1
    assert bad[0].at == "/ranges/v/values/1"
    assert bad[0].severity is Severity.ERROR


def test_integer_range_rejects_floats_including_whole_valued() -> None:
    # Strict: a fractional float AND a whole-valued float (1.0) are both flagged.
    cov = _coverage_with_range(NdArray(data_type="integer", values=(1, 1.0, 1.5)))
    paths = _value_type_paths(validate(cov, check_values=True))

    assert paths == ["/ranges/v/values/1", "/ranges/v/values/2"]


def test_float_range_accepts_int_and_float() -> None:
    # A JSON integer like 5 decodes to a Python int but is a valid float value.
    cov = _coverage_with_range(NdArray(data_type="float", values=(5, 5.0)))

    assert _value_type_paths(validate(cov, check_values=True)) == []


def test_float_range_rejects_string() -> None:
    cov = _coverage_with_range(NdArray(data_type="float", values=(1.0, "x")))
    paths = _value_type_paths(validate(cov, check_values=True))

    assert paths == ["/ranges/v/values/1"]


def test_string_range_accepts_str_rejects_number() -> None:
    cov = _coverage_with_range(NdArray(data_type="string", values=("a", 1)))
    paths = _value_type_paths(validate(cov, check_values=True))

    assert paths == ["/ranges/v/values/1"]


def test_bool_rejected_in_integer_and_float_ranges() -> None:
    # bool is an int subclass, so it must be excluded explicitly.
    for data_type in ("integer", "float"):
        cov = _coverage_with_range(NdArray(data_type=data_type, values=(True,)))
        paths = _value_type_paths(validate(cov, check_values=True))

        assert paths == ["/ranges/v/values/0"], data_type


def test_none_is_always_allowed() -> None:
    cov = _coverage_with_range(NdArray(data_type="integer", values=(1, None, 3)))

    assert _value_type_paths(validate(cov, check_values=True)) == []


def test_standalone_ndarray_value_types_checked() -> None:
    arr = NdArray(data_type="integer", values=(1, 1.5))

    # Off by default, on with check_values; path is relative to the array root.
    assert _value_type_paths(validate(arr)) == []
    assert _value_type_paths(validate(arr, check_values=True)) == ["/values/1"]


@pytest.mark.parametrize(
    ("data_type", "values"),
    [
        ("float", (1.5, 2, None, 3.0)),  # all valid (int, float, None)
        ("float", (1.0, "x", 2.0, "y")),  # two string mismatches
        ("float", (True, 1.0)),  # a bool, at index 0
        ("integer", (1, 2, 3, None)),  # all valid
        ("integer", (1, 1.0, 1.5)),  # whole-valued and fractional float
        ("integer", (1, "x", 2.0, True)),  # three mismatches, one per kind
        ("string", ("a", None, "b")),  # all valid
        ("string", ("a", 1, 2.0, True)),  # three mismatches
    ],
)
def test_value_screen_matches_reference_scan(
    data_type: Literal["float", "integer", "string"],
    values: tuple[float | int | str | None, ...],
) -> None:
    """The fast value screen flags exactly what an independent scan flags.

    The differential guard for #74: the native screen and its per-element
    fallback must agree with a from-scratch oracle on every shape. The
    multi-mismatch rows in particular prove the fallback enumerates *all*
    offenders, not just the first (which is where ``convert`` stops).
    """
    arr = NdArray(data_type=data_type, values=values)

    assert _value_type_paths(
        validate(arr, check_values=True)
    ) == _expected_value_type_paths(data_type, values)


def test_collection_validates_resolved_members() -> None:
    # The member inherits domainType="Point" from the collection, which then
    # makes its multi-valued x axis an error.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0, 2.0)), "y": Axis.listed((3.0,))},
        ),
        ranges={},
    )
    collection = CoverageCollection(coverages=(member,), domain_type="Point")
    issues = validate(collection)

    assert any(i.code == "domain.axis-not-single" for i in issues)
    assert all(i.at.startswith("/coverages/0/") for i in issues)


def test_missing_referencing_on_standalone_domain() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
        domain_type="Point",
    )
    (issue,) = validate(domain)

    assert issue.code == "domain.missing-referencing"
    assert issue.at == "/referencing"
    assert issue.severity is Severity.ERROR


def test_missing_domain_type_on_standalone_domain_is_a_warning() -> None:
    # A domain carrying referencing but no domainType isolates the SHOULD
    # (Spec 6.1 RECOMMENDS domainType): a warning, not an error.
    domain = Domain(axes={"x": Axis.listed((1.0,))}, referencing=_REF)
    (issue,) = validate(domain)

    assert isinstance(issue, DomainMissingDomainType)
    assert issue.at == "/domainType"
    assert issue.severity is Severity.WARNING


def test_empty_string_domain_type_is_treated_as_missing() -> None:
    # An empty-string domainType is present but meaningless, so it draws the same
    # recommended-member warning as an absent one.
    domain = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="", referencing=_REF)
    (issue,) = validate(domain)

    assert isinstance(issue, DomainMissingDomainType)
    assert issue.severity is Severity.WARNING


def test_coverage_domain_type_suppresses_the_inline_domain_warning() -> None:
    # The inline domain omits domainType, but the coverage supplies it, so the
    # effective type is known and no missing-domainType warning fires.
    coverage = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            referencing=_REF,
        ),
        domain_type="Point",
        ranges={},
        parameters={},
    )

    assert all(i.code != "domain.missing-domain-type" for i in validate(coverage))


def test_collection_member_repeating_domain_type_warns() -> None:
    # The collection provides domainType="Point"; a member restating it at the
    # coverage level SHOULD have omitted it (Spec 6.4): a warning.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            referencing=_REF,
        ),
        domain_type="Point",
        ranges={},
    )
    collection = CoverageCollection(
        coverages=(member,), domain_type="Point", parameters={}
    )

    (issue,) = [
        i for i in validate(collection) if isinstance(i, CoverageDomainTypeNotOmitted)
    ]
    assert issue.at == "/coverages/0/domainType"
    assert issue.domain_type == "Point"
    assert issue.severity is Severity.WARNING


def test_collection_member_conflicting_domain_type_is_an_error() -> None:
    # The collection indicates it holds only "Point" coverages; a member declaring
    # "Grid" falsifies that claim (Spec 6.5): an error, not a SHOULD-omit warning.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            referencing=_REF,
        ),
        domain_type="Grid",
        ranges={},
    )
    collection = CoverageCollection(
        coverages=(member,), domain_type="Point", parameters={}
    )

    (issue,) = [
        i for i in validate(collection) if isinstance(i, CoverageDomainTypeConflict)
    ]
    assert issue.at == "/coverages/0/domainType"
    assert issue.domain_type == "Grid"
    assert issue.collection_domain_type == "Point"
    assert issue.severity is Severity.ERROR


def test_collection_member_conflict_declared_on_inline_domain() -> None:
    # The member omits its coverage-level domainType but its inline domain declares
    # "Grid", conflicting with the "Point" collection. The conflict is caught from
    # the declared (effective) type, and the finding points at the domain.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            domain_type="Grid",
            referencing=_REF,
        ),
        ranges={},
    )
    collection = CoverageCollection(
        coverages=(member,), domain_type="Point", parameters={}
    )

    (issue,) = [
        i for i in validate(collection) if isinstance(i, CoverageDomainTypeConflict)
    ]
    assert issue.at == "/coverages/0/domain/domainType"
    assert issue.domain_type == "Grid"
    assert issue.collection_domain_type == "Point"
    assert issue.severity is Severity.ERROR


def test_collection_member_omitting_domain_type_is_not_flagged() -> None:
    # A member that omits domainType inherits the collection's; the raw-vs-resolved
    # split means it draws neither the not-omitted warning nor a missing-domainType
    # warning.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            referencing=_REF,
        ),
        ranges={},
    )
    collection = CoverageCollection(
        coverages=(member,), domain_type="Point", parameters={}
    )
    codes = {i.code for i in validate(collection)}

    assert "coverage.domain-type-not-omitted" not in codes
    assert "domain.missing-domain-type" not in codes


def test_temporal_lexical_form_check_is_opt_in() -> None:
    # A Gregorian time axis carrying a full-precision value, a reduced-precision
    # value, an unrepresentable-but-valid expanded year, and a malformed value.
    domain = Domain(
        axes={
            "t": Axis.listed(("2020-01-01T00:00:00Z", "2020", "+102020", "nope")),
        },
        referencing=(
            ReferenceSystemConnection(
                coordinates=("t",),
                system=ReferenceSystem.temporal(calendar="Gregorian"),
            ),
        ),
    )

    # Off by default: no value scanning.
    assert all(i.code != "temporal.lexical-form" for i in validate(domain))

    # Opt in: only the malformed value is flagged. The reduced-precision "2020"
    # and the unrepresentable "+102020" are legal forms and pass.
    issues = validate(domain, check_values=True)
    (issue,) = [i for i in issues if i.code == "temporal.lexical-form"]

    assert isinstance(issue, TemporalLexicalForm)
    assert issue.value == "nope"
    assert issue.at == "/axes/t/values/3"
    # Spec 5.2 makes the lexical forms a SHOULD, so this is a warning (ADR-0002).
    assert issue.severity is Severity.WARNING


def test_temporal_check_flags_malformed_year_zero_date() -> None:
    # A year-0000 date with an invalid month is malformed, not an
    # unrepresentable-but-valid form, so check_values must flag it (it would slip
    # through if resolve mislabeled it Unrepresentable).
    domain = Domain(
        axes={"t": Axis.listed(("0000-13-01",))},
        referencing=(
            ReferenceSystemConnection(
                coordinates=("t",),
                system=ReferenceSystem.temporal(calendar="Gregorian"),
            ),
        ),
    )

    issues = validate(domain, check_values=True)
    (issue,) = [i for i in issues if i.code == "temporal.lexical-form"]

    assert isinstance(issue, TemporalLexicalForm)
    assert issue.value == "0000-13-01"
    assert issue.at == "/axes/t/values/0"


def test_temporal_check_skips_non_gregorian_calendar() -> None:
    # "2020-02-30" is malformed under Gregorian but valid in a 360_day calendar;
    # temporal_coordinates excludes non-standard calendars, so it is never scanned.
    domain = Domain(
        axes={"t": Axis.listed(("2020-02-30",))},
        referencing=(
            ReferenceSystemConnection(
                coordinates=("t",), system=ReferenceSystem.temporal(calendar="360_day")
            ),
        ),
    )

    issues = validate(domain, check_values=True)

    assert all(i.code != "temporal.lexical-form" for i in issues)


def test_collection_referencing_is_inherited_into_member_domain() -> None:
    # The collection supplies referencing; the member's inline domain has none,
    # so resolution injects it and no missing-referencing issue is raised.
    member = Coverage(
        domain=Domain(
            axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
            domain_type="Point",
        ),
        ranges={},
        parameters={},
    )
    collection = CoverageCollection(coverages=(member,), referencing=_REF)

    assert validate(collection) == []


def test_missing_parameters_on_standalone_coverage() -> None:
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={},
    )
    (issue,) = validate(cov)

    assert issue.code == "coverage.missing-parameters"
    assert issue.at == "/parameters"
    assert issue.severity is Severity.ERROR


def test_empty_parameters_member_is_present_so_not_missing() -> None:
    # An empty (but present) parameters object satisfies the presence MUST.
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={},
        parameters={},
    )

    assert validate(cov) == []


def test_collection_parameters_are_inherited_by_member() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    member = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
    collection = CoverageCollection(coverages=(member,), parameters={"t": temp})
    codes = {i.code for i in validate(collection)}

    assert "coverage.missing-parameters" not in codes


def test_url_reference_domain_skips_referencing_but_not_parameters() -> None:
    # A URL-reference domain is unfetched, so its referencing cannot be checked;
    # the coverage's own parameters MUST is independent of the domain form.
    cov = Coverage(domain="https://example.org/domain.json", ranges={})
    codes = {i.code for i in validate(cov)}

    assert "domain.missing-referencing" not in codes
    assert "coverage.missing-parameters" in codes


def test_tiled_ndarray_tile_shape_too_large() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(5, None), url_template="{t}.covjson"),),
    )
    (issue,) = validate(arr)

    assert issue.code == "tiled-ndarray.tile-shape-too-large"
    assert issue.at == "/tileSets/0/tileShape/0"


def test_tiled_ndarray_url_template_missing_variable() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="tile.covjson"),),
    )
    (issue,) = validate(arr)

    assert issue.code == "tiled-ndarray.url-template-missing-variable"
    assert issue.at == "/tileSets/0/urlTemplate"


def test_tiled_ndarray_shape_rank_mismatch() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t",),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}.covjson"),),
    )
    codes = {i.code for i in validate(arr)}

    assert "tiled-ndarray.shape-rank" in codes


def test_tiled_ndarray_non_positive_tile_size() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(0, None), url_template="{t}.covjson"),),
    )
    (issue,) = validate(arr)

    assert issue.code == "tiled-ndarray.tile-shape-not-positive"
    assert issue.at == "/tileSets/0/tileShape/0"


def test_tiled_ndarray_url_template_unknown_variable() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}-{z}.covjson"),),
    )
    (issue,) = validate(arr)

    assert issue.code == "tiled-ndarray.url-template-unknown-variable"
    assert issue.at == "/tileSets/0/urlTemplate"


def test_tiled_ndarray_unknown_variable_suppressed_on_rank_mismatch() -> None:
    # With axisNames/shape misaligned, "which axes are subdivided" is unreliable,
    # so the reverse check is skipped to avoid false positives: only the
    # shape-rank issue is reported.
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t",),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}-{x}.covjson"),),
    )
    codes = {i.code for i in validate(arr)}

    assert "tiled-ndarray.url-template-unknown-variable" not in codes
    assert "tiled-ndarray.shape-rank" in codes


def test_tiled_ndarray_well_formed_is_clean() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="{t}.covjson"),),
    )

    assert validate(arr) == []


def test_tiled_ndarray_range_inside_coverage_is_validated() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={
            "t": TiledNdArray(
                data_type="float",
                axis_names=("t", "x"),
                shape=(4, 2),
                tile_sets=(TileSet(tile_shape=(5, None), url_template="{t}.covjson"),),
            )
        },
        parameters={"t": temp},
    )
    issue = next(
        i for i in validate(cov) if i.code == "tiled-ndarray.tile-shape-too-large"
    )

    assert issue.at == "/ranges/t/tileSets/0/tileShape/0"


def test_raise_mode_raises_on_error() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,))}, domain_type="Grid", referencing=_REF
    )

    with pytest.raises(CovJSONValidationError) as excinfo:
        validate(domain, mode="raise")

    assert excinfo.value.issues
    assert excinfo.value.issues[0].code == "domain.missing-axis"


# A test that ``mode="raise"`` returns warning-only issues without raising
# returns with #37, which reintroduces SHOULD-level (warning) checks. After #35
# the warning tier has no producer, so no real document can exercise that path.


def test_i18n_invalid_tag_in_parameter_label() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")),
        Unit(symbol="K"),
        label={"en_US": "Air temperature"},
    )
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={},
        parameters={"t": temp},
    )
    (issue,) = validate(cov)

    assert issue.code == "i18n.invalid-language-tag"
    assert issue.at == "/parameters/t/label/en_US"


def test_i18n_invalid_tag_in_category_label() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(Category(id="1", label={"en_US": "Water"}),),
    )
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={},
        parameters={"lc": Parameter.categorical(land_cover, {"1": 1})},
    )
    (issue,) = validate(cov)

    assert issue.code == "i18n.invalid-language-tag"
    assert issue.at == "/parameters/lc/observedProperty/categories/0/label/en_US"


def test_i18n_invalid_tag_in_identifier_rs_description() -> None:
    # Only an identifier RS carries a ``description`` (Spec 5.3); the CRS types do
    # not (Spec 5.1), so this is the reference-system ``description`` i18n path.
    ref = (
        ReferenceSystemConnection(
            coordinates=("x", "y"),
            system=ReferenceSystem.identifier(
                target_concept=Concept(label={"en": "land cover"}),
                description={"en_US": "WGS 84"},
            ),
        ),
    )
    domain = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=ref)
    (issue,) = validate(domain)

    assert issue.code == "i18n.invalid-language-tag"
    assert issue.at == "/referencing/0/system/description/en_US"


@pytest.mark.parametrize(
    ("system", "code", "member"),
    [
        (ReferenceSystem(type_="TemporalRS"), "temporal.missing-calendar", "calendar"),
        (
            ReferenceSystem(type_="IdentifierRS"),
            "identifier.missing-target-concept",
            "targetConcept",
        ),
    ],
)
def test_refine_and_validate_agree_on_a_malformed_known_rs(
    system: ReferenceSystem, code: str, member: str
) -> None:
    # One rule (missing_required_member) drives both sides, so they cannot
    # disagree: refine renders a malformed known type opaque (is_custom() False, a
    # malformed known type rather than a custom one), and validate reports the same
    # missing member. A custom domainType isolates the reference-system error from
    # any axis-rule checks.
    refined = system.refine()
    assert isinstance(refined, OpaqueRS)
    assert not refined.is_custom()

    domain = Domain(
        axes={"x": Axis.listed(("a",))},
        domain_type="http://example/Custom",
        referencing=(ReferenceSystemConnection(coordinates=("x",), system=system),),
    )
    (issue,) = validate(domain)

    assert issue.code == code
    assert issue.at == f"/referencing/0/system/{member}"
    assert issue.severity is Severity.ERROR


def test_malformed_identifier_rs_still_reports_i18n() -> None:
    # A missing required member does not suppress the i18n checks: a bad language
    # tag on a targetConcept-less identifier RS is reported alongside the structural
    # error, since i18n validity is independent of the missing member.
    domain = Domain(
        axes={"x": Axis.listed(("a",))},
        domain_type="http://example/Custom",
        referencing=(
            ReferenceSystemConnection(
                coordinates=("x",),
                system=ReferenceSystem(type_="IdentifierRS", label={"en_US": "x"}),
            ),
        ),
    )

    codes = {i.code for i in validate(domain)}

    assert codes == {"identifier.missing-target-concept", "i18n.invalid-language-tag"}


def test_i18n_invalid_tag_in_identifier_rs_identifiers() -> None:
    # A custom-URI domain_type satisfies the domainType SHOULD (else a
    # domain.missing-domain-type warning) without imposing the axis-rule checks a
    # well-known type like "Point" would, isolating the i18n check.
    ref = (
        ReferenceSystemConnection(
            coordinates=("x",),
            system=ReferenceSystem.identifier(
                target_concept=Concept(label=i18n("Land cover")),
                identifiers={"1": Concept(label={"en_US": "Water"})},
            ),
        ),
    )
    domain = Domain(
        axes={"x": Axis.listed((1.0,))},
        domain_type="http://example/Custom",
        referencing=ref,
    )
    (issue,) = validate(domain)

    assert issue.code == "i18n.invalid-language-tag"
    assert issue.at == "/referencing/0/system/identifiers/1/label/en_US"


def test_i18n_valid_tags_including_und_are_not_flagged() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature", en="Air temperature")),
        Unit(label={"en": "kelvin"}, symbol="K"),
    )
    # A custom-URI domain_type keeps the document clean (no missing-domainType
    # warning) without triggering axis rules, so this isolates the i18n check.
    domain = Domain(
        axes={"x": Axis.listed((1.0,))},
        domain_type="http://example/Custom",
        referencing=_REF,
    )
    cov = Coverage(domain=domain, ranges={}, parameters={"t": temp})

    assert validate(cov) == []


def test_i18n_invalid_tag_in_parameter_group_checked_even_without_parameters() -> None:
    domain = Domain(axes={"x": Axis.listed((1.0,))}, referencing=_REF)
    cov = Coverage(
        domain=domain,
        ranges={},
        parameter_groups=(ParameterGroup(members=("a",), label={"en_US": "grp"}),),
    )
    codes = {i.code for i in validate(cov)}

    assert "coverage.missing-parameters" in codes
    assert "i18n.invalid-language-tag" in codes


def test_i18n_empty_map_is_flagged() -> None:
    # Built via the raw constructor (bypassing the `i18n()` builder, which
    # itself rejects an empty map) to exercise validate()'s own check.
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")),
        Unit(label={}, symbol="K"),
    )
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), referencing=_REF
        ),
        ranges={},
        parameters={"t": temp},
    )
    (issue,) = validate(cov)

    assert issue.code == "i18n.empty"
    assert issue.at == "/parameters/t/unit/label"


def test_report_roundtrips_through_json() -> None:
    # A report encodes to JSON (each finding tagged by its `code`) and decodes
    # back to the exact concrete variants: the serialization payoff of the
    # tagged-union model.
    domain = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    report = validate(domain)

    restored = msgspec.json.decode(msgspec.json.encode(report), type=list[Issue])

    assert restored == report
    assert [type(i) for i in restored] == [type(i) for i in report]


def test_every_finding_kind_is_exhaustively_matchable() -> None:
    # `_describe` matches every `Issue` variant with an `assert_never` default,
    # so adding a finding kind without handling it is a (strict) type error.
    # This document produces two `domain.*` findings.
    domain = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")

    assert {_describe(i) for i in validate(domain)} == {"domain"}


def test_axis_monotonic_check_is_opt_in() -> None:
    domain = _axis_domain(Axis.listed((0.0, 2.0, 1.0)), ReferenceSystem.geographic())

    # Off by default: the value array is not scanned.
    assert not any(i.code == "axis.not-monotonic" for i in validate(domain))

    # Opt in: the reversal is flagged as an error, at the offending index.
    (issue,) = [
        i
        for i in validate(domain, check_values=True)
        if isinstance(i, AxisNotMonotonic)
    ]
    assert issue.axis == "x"
    assert issue.at == "/axes/x/values/2"
    assert issue.severity is Severity.ERROR


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((0.0, 1.0, 2.0), []),  # increasing
        ((2.0, 1.0, 0.0), []),  # decreasing is monotonic too
        ((0.0, 2.0, 1.0), ["/axes/x/values/2"]),  # reversal at index 2
        ((0.0, 1.0, 1.0, 3.0), []),  # equal-adjacent ok (non-strict default)
        ((5.0,), []),  # a single value is trivially ordered
    ],
)
def test_numeric_axis_monotonicity(
    values: tuple[float, ...], expected: list[str]
) -> None:
    domain = _axis_domain(Axis.listed(values), ReferenceSystem.geographic())

    assert _monotonic_paths(domain) == expected


def test_regular_axis_is_skipped() -> None:
    # A regular (start/stop/num) axis has `values is None`, so the check skips it
    # (it is monotonic by construction regardless). This guards that skip: without
    # it the checker would reach `enumerate(None)` and raise.
    domain = _axis_domain(Axis.regular(10.0, 0.0, 5), ReferenceSystem.geographic())

    assert _monotonic_paths(domain) == []


def test_numeric_axis_with_nan_is_not_falsely_flagged() -> None:
    # NaN is unordered and not equal to itself, so it is skipped rather than
    # corrupting the walk: an otherwise-increasing axis is not flagged, and a real
    # reversal elsewhere is still reported at its true index.
    increasing = _axis_domain(
        Axis.listed((1.0, 2.0, float("nan"), 3.0)), ReferenceSystem.geographic()
    )
    assert _monotonic_paths(increasing) == []

    reversal = _axis_domain(
        Axis.listed((float("nan"), 1.0, 3.0, 2.0)), ReferenceSystem.geographic()
    )
    assert _monotonic_paths(reversal) == ["/axes/x/values/3"]


def test_strict_checker_flags_equal_adjacent_values() -> None:
    domain = _axis_domain(Axis.listed((0.0, 0.0, 1.0)), ReferenceSystem.geographic())

    # The default is non-strict, so equal-adjacent values pass.
    assert _monotonic_paths(domain) == []

    # A strict checker treats the repeat as a break, at the second value.
    assert _monotonic_paths(domain, require_monotonic(strict=True)) == [
        "/axes/x/values/1"
    ]


def test_time_axis_reversal_is_flagged() -> None:
    domain = _axis_domain(
        Axis.listed(
            (
                "2020-01-01T00:00:00Z",
                "2020-01-03T00:00:00Z",
                "2020-01-02T00:00:00Z",
            )
        ),
        ReferenceSystem.temporal(calendar="Gregorian"),
        coord="t",
    )

    assert _monotonic_paths(domain) == ["/axes/t/values/2"]


def test_time_axis_ordered_by_instant_not_string() -> None:
    # The instants 05:00Z, 07:00Z, 09:00Z increase, though the raw strings
    # ("05", "02", "09") do not: proof the check compares resolved instants, not
    # the lexical values (which would flag a reversal at index 2).
    domain = _axis_domain(
        Axis.listed(
            (
                "2020-01-01T05:00:00Z",
                "2020-01-01T02:00:00-05:00",
                "2020-01-01T09:00:00Z",
            )
        ),
        ReferenceSystem.temporal(calendar="Gregorian"),
        coord="t",
    )

    assert _monotonic_paths(domain) == []


def test_non_standard_calendar_time_axis_is_skipped() -> None:
    domain = _axis_domain(
        Axis.listed(
            (
                "2020-01-01T00:00:00Z",
                "2020-01-03T00:00:00Z",
                "2020-01-02T00:00:00Z",
            )
        ),
        ReferenceSystem.temporal(calendar="360_day"),
        coord="t",
    )

    assert _monotonic_paths(domain) == []


def test_mixed_awareness_time_axis_is_skipped() -> None:
    # An aware second-precision moment and naive day-precision moments are not
    # comparable, so the axis is skipped rather than raising or fabricating a
    # zone; comparing a naive and an aware datetime would raise TypeError.
    domain = _axis_domain(
        Axis.listed(("2020-01-01", "2020-01-03T00:00:00Z", "2020-01-02")),
        ReferenceSystem.temporal(calendar="Gregorian"),
        coord="t",
    )

    assert _monotonic_paths(domain) == []


def test_malformed_temporal_value_is_not_double_reported() -> None:
    # A malformed value is the temporal.lexical-form check's finding; the
    # monotonic check compares only resolvable moments, so it is not re-reported.
    domain = _axis_domain(
        Axis.listed(("2020-01-01T00:00:00Z", "nope", "2020-01-02T00:00:00Z")),
        ReferenceSystem.temporal(calendar="Gregorian"),
        coord="t",
    )

    codes = {i.code for i in validate(domain, check_values=True)}

    assert "temporal.lexical-form" in codes
    assert "axis.not-monotonic" not in codes


def test_identifier_axis_with_integer_codes_is_not_flagged() -> None:
    # An identifier system defines no ordering, so integer codes in an arbitrary
    # order are conformant (keying on "the value is numeric" would false-positive).
    ids = ReferenceSystem.identifier(target_concept=Concept(label=i18n("class")))
    domain = _axis_domain(Axis.listed((3, 1, 2)), ids, coord="c")

    assert _monotonic_paths(domain) == []


def test_axis_without_reference_system_is_skipped() -> None:
    # The MUST is conditional on a reference system that defines an ordering, so a
    # numeric axis with no system in scope is not flagged.
    domain = Domain(axes={"x": Axis.listed((0.0, 2.0, 1.0))})

    assert _monotonic_paths(domain) == []


def test_custom_axis_order_checker_overrides_the_default() -> None:
    # The seam can flag what the default skips: here, an identifier axis, held to
    # a strictly-descending order by a caller-supplied policy.
    def strictly_descending(
        values: Sequence[AxisValue], system: ResolvedReferenceSystem | None
    ) -> int | None:
        for i in range(1, len(values)):
            previous, current = values[i - 1], values[i]

            if (
                isinstance(previous, (int, float))
                and isinstance(current, (int, float))
                and current >= previous
            ):
                return i

        return None

    ids = ReferenceSystem.identifier(target_concept=Concept(label=i18n("class")))
    domain = _axis_domain(Axis.listed((3, 2, 5)), ids, coord="c")

    assert _monotonic_paths(domain, strictly_descending) == ["/axes/c/values/2"]


def test_tuple_axis_arity_mismatch_is_reported() -> None:
    axis = Axis(
        values=((1.0, 2.0), (3.0, 4.0)), data_type="tuple", coordinates=("t", "x", "y")
    )

    issues = _composite_issues(axis)

    assert [i.code for i in issues] == ["axis.composite-arity"] * 2
    assert isinstance(issues[0], AxisCompositeArity)
    assert (issues[0].expected, issues[0].got) == (3, 2)
    assert issues[0].at == "/axes/composite/values/0"


def test_tuple_axis_surplus_arity_is_reported() -> None:
    # validate()'s arity check compares each value's size to the coordinate
    # identifier count: a 3-tuple against one identifier is `axis.composite-arity`.
    axis = Axis(values=((1.0, 2.0, 3.0),), data_type="tuple", coordinates=("t",))

    issues = _composite_issues(axis)

    assert isinstance(issues[0], AxisCompositeArity)
    assert (issues[0].expected, issues[0].got) == (1, 3)


def test_tuple_axis_matching_arity_is_silent() -> None:
    # A legal tuple axis whose values match its coordinate count draws nothing.
    axis = Axis(values=((1.0,), (2.0,)), data_type="tuple", coordinates=("x",))

    assert _composite_issues(axis) == []


def test_tuple_axis_string_value_is_reported_as_shape_not_arity() -> None:
    # A str satisfies len() and indexing, so `len("abc") == 3` passes an arity
    # check against three identifiers and the bridges then read 'a'/'b'/'c' as
    # the components. Shape gates arity, so shape alone is reported: an arity
    # check over an unshaped value certifies the garbage rather than catching it.
    axis = Axis(values=("abc",), data_type="tuple", coordinates=("t", "x", "y"))

    issues = _composite_issues(axis)

    assert [i.code for i in issues] == ["axis.composite-value-shape"]
    assert isinstance(issues[0], AxisCompositeValueShape)
    assert issues[0].data_type == "tuple"


def test_legal_polygon_decoded_from_the_wire_is_silent() -> None:
    # Decoded from bytes rather than built from literals, deliberately: only
    # `AxisValue`'s outermost level is annotated (`tuple[Any, ...]`), so a
    # decoded polygon is a tuple of *lists* of lists. A shape check expecting a
    # tuple at every depth fires on this legal document, and a Python-literal
    # fixture would hide that.
    axis = msgspec.json.decode(
        b'{"values": [[[[100.0, 0.0], [101.0, 0.0], [101.0, 1.0], [100.0, 0.0]]]],'
        b' "dataType": "polygon", "coordinates": ["x", "y"]}',
        type=Axis,
    )

    assert _composite_issues(axis) == []


# A ring with no positions, typed so strict pyright sees a known element type
# rather than `list[Unknown]`. Defined before its use in the parametrize below.
_EMPTY_RING: list[list[float]] = []


@pytest.mark.parametrize(
    "value",
    [
        pytest.param((), id="no-rings"),
        pytest.param((_EMPTY_RING,), id="empty-ring"),
    ],
)
def test_polygon_axis_with_an_empty_array_is_reported(value: AxisValue) -> None:
    # `all()` over an empty sequence is vacuously true, so a polygon with no
    # rings, or a ring with no positions, would slip through a naive shape check
    # and reach shapely as an unpack error or an empty geometry. RFC 7946 (which
    # 6.1.1 defers to) requires a non-empty array at each level.
    axis = Axis(values=(value,), data_type="polygon", coordinates=("x", "y"))

    issues = _composite_issues(axis)

    assert [i.code for i in issues] == ["axis.composite-value-shape"]


def test_polygon_axis_given_a_bare_ring_is_reported() -> None:
    # One nesting level short: a ring supplied where a polygon (a sequence of
    # rings) belongs, so the ring's positions are read as rings.
    axis = Axis(
        values=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),),
        data_type="polygon",
        coordinates=("x", "y"),
    )

    issues = _composite_issues(axis)

    assert [i.code for i in issues] == ["axis.composite-value-shape"]
    assert isinstance(issues[0], AxisCompositeValueShape)
    assert issues[0].data_type == "polygon"


@pytest.mark.parametrize("data_type", [None, "knmi:range"])
def test_non_composite_axis_draws_no_composite_issue(data_type: str | None) -> None:
    # Spec 6.1.1 defines no value structure for a custom dataType (it grants only
    # "Custom values MAY be used"), so no MUST constrains one and neither rule
    # applies. A primitive axis is likewise out of scope.
    assert _composite_issues(Axis(values=(1.0, 2.0), data_type=data_type)) == []


def test_composite_issues_are_gated_by_check_values() -> None:
    # Both rules scan every value, so they belong behind `check_values` (ADR-0002)
    # rather than in the default pass.
    axis = Axis(values=("abc",), data_type="tuple", coordinates=("t", "x", "y"))
    domain = Domain(axes={"composite": axis}, referencing=_REF)

    assert [i for i in validate(domain) if i.code.startswith("axis.composite-")] == []


# `_polygon_axis` is called at module-load time by the parametrize decorators
# below, so it precedes them (test helpers otherwise live after the tests).
def _polygon_axis(polygon: str, coords: str = '["x", "y"]') -> Axis:
    """Decode a one-value ``"polygon"`` axis from a wire polygon (a list of rings).

    Building from bytes rather than nested tuples mirrors production: only
    ``AxisValue``'s outermost level is annotated, so a decoded polygon is a tuple
    of *lists* of lists, the exact shape the depth-3 reads must handle.
    """
    wire = f'{{"values": [{polygon}], "dataType": "polygon", "coordinates": {coords}}}'

    return msgspec.json.decode(wire.encode(), type=Axis)


@pytest.mark.parametrize(
    ("axis", "codes"),
    [
        # A position longer than the two identifiers: surplus the bridge would drop.
        pytest.param(
            _polygon_axis(
                "[[[0.0,0.0,5.0],[1.0,0.0,5.0],[1.0,1.0,5.0],[0.0,0.0,5.0]]]"
            ),
            {"axis.polygon-position-arity"},
            id="position-too-long",
        ),
        # A position shorter than three identifiers: the bridge would IndexError.
        pytest.param(
            _polygon_axis(
                "[[[0.0,0.0],[1.0,0.0],[1.0,1.0],[0.0,0.0]]]", '["x", "y", "z"]'
            ),
            {"axis.polygon-position-arity"},
            id="position-too-short",
        ),
        # A ring of three positions (closed): fewer than the four a ring needs.
        pytest.param(
            _polygon_axis("[[[0.0,0.0],[1.0,0.0],[0.0,0.0]]]"),
            {"axis.polygon-ring-too-short"},
            id="ring-too-short",
        ),
        # Four positions but the first and last differ: an unclosed ring.
        pytest.param(
            _polygon_axis("[[[0.0,0.0],[1.0,0.0],[1.0,1.0],[0.0,1.0]]]"),
            {"axis.polygon-ring-not-closed"},
            id="ring-not-closed",
        ),
        # A three-position unclosed ring trips both ring rules (both are reported).
        pytest.param(
            _polygon_axis("[[[0.0,0.0],[1.0,0.0],[1.0,1.0]]]"),
            {"axis.polygon-ring-too-short", "axis.polygon-ring-not-closed"},
            id="too-short-and-unclosed",
        ),
    ],
)
def test_polygon_ring_violation_is_reported(axis: Axis, codes: set[str]) -> None:
    assert {i.code for i in _polygon_deep_issues(axis)} == codes


@pytest.mark.parametrize(
    "axis",
    [
        pytest.param(
            _polygon_axis("[[[0.0,0.0],[1.0,0.0],[1.0,1.0],[0.0,0.0]]]"), id="2d"
        ),
        # A 3D polygon: coordinates ["x","y","z"] and three-component positions.
        pytest.param(
            _polygon_axis(
                "[[[0.0,0.0,5.0],[1.0,0.0,5.0],[1.0,1.0,5.0],[0.0,0.0,5.0]]]",
                '["x", "y", "z"]',
            ),
            id="3d",
        ),
    ],
)
def test_conformant_polygon_draws_no_deep_issue(axis: Axis) -> None:
    assert _polygon_deep_issues(axis) == []


def test_shape_invalid_polygon_reaches_no_deep_check() -> None:
    # Shape gates the depth-3 reads: a value that is not a polygon array is a
    # `composite-value-shape` issue and never reaches an arity or ring rule.
    axis = _polygon_axis("[[0.0,0.0],[1.0,0.0]]")  # a bare ring, one level short

    assert _polygon_deep_issues(axis) == []
    assert [i.code for i in _composite_issues(axis)] == ["axis.composite-value-shape"]


def test_polygon_position_arity_reports_expected_got_and_position() -> None:
    # Arity points at the offending position (depth 3) and carries the counts.
    axis = _polygon_axis("[[[0.0,0.0],[1.0,0.0,9.0],[1.0,1.0],[0.0,0.0]]]")

    (issue,) = _polygon_deep_issues(axis)

    assert isinstance(issue, AxisPolygonPositionArity)
    assert (issue.expected, issue.got) == (2, 3)
    assert issue.at == "/axes/composite/values/0/0/1"  # value 0, ring 0, position 1


def test_polygon_ring_rule_points_at_the_ring() -> None:
    # A ring rule points at the ring (depth 2), one level above a position.
    axis = _polygon_axis("[[[0.0,0.0],[1.0,0.0],[0.0,0.0]]]")

    (issue,) = _polygon_deep_issues(axis)

    assert isinstance(issue, AxisPolygonRingTooShort)
    assert issue.got == 3
    assert issue.at == "/axes/composite/values/0/0"


def test_polygon_deep_checks_are_gated_by_check_values() -> None:
    # The depth-3 reads scan every vertex, so they sit behind `check_values`
    # (ADR-0002) like the other value-scans, not in the default pass.
    axis = _polygon_axis("[[[0.0,0.0],[1.0,0.0],[0.0,0.0]]]")
    domain = Domain(axes={"composite": axis}, referencing=_REF)

    assert [i for i in validate(domain) if i.code.startswith("axis.polygon-")] == []


@pytest.mark.parametrize(
    ("axis", "expected", "got"),
    [
        # A value-listing axis: `len` is the spec's own length of `values`.
        (Axis.listed((0.0, 1.0, 2.0), bounds=(-0.5, 0.5)), 6, 2),
        (Axis.listed((0.0, 1.0), bounds=(0.0, 1.0, 1.0, 2.0, 2.0, 3.0)), 4, 6),
        # A regular axis has no `values`, so `2 * num` is the derived length.
        (Axis.regular(0.0, 2.0, 3, bounds=(-0.5, 2.5)), 6, 2),
        # A composite (tuple) axis: `len` counts the tuples, so `expected` is
        # `2 * len(values)`, unrelated to the tuple width.
        (
            Axis(
                values=((1.0, 2.0), (3.0, 4.0)),
                data_type="tuple",
                coordinates=("x", "y"),
                bounds=(0.0, 1.0),
            ),
            4,
            2,
        ),
    ],
)
def test_wrong_length_bounds_is_reported(axis: Axis, expected: int, got: int) -> None:
    issues = _bounds_issues(axis)

    assert [i.code for i in issues] == ["axis.bounds-length"]
    assert (issues[0].expected, issues[0].got) == (expected, got)
    assert issues[0].at == "/axes/a/bounds"


@pytest.mark.parametrize(
    "axis",
    [
        Axis.listed((0.0, 1.0), bounds=(-0.5, 0.5, 0.5, 1.5)),  # 2 * len(values)
        Axis.regular(0.0, 2.0, 3, bounds=(-0.5, 0.5, 0.5, 1.5, 1.5, 2.5)),  # 2 * num
        Axis.listed((0.0, 1.0)),  # bounds absent
    ],
)
def test_correct_or_absent_bounds_is_silent(axis: Axis) -> None:
    assert _bounds_issues(axis) == []


def test_bounds_length_check_runs_without_check_values() -> None:
    # The test is O(1) per axis, so it is not gated by `check_values` the way the
    # value-scans are: a wrong-length `bounds` is caught in the default pass.
    axis = Axis.listed((0.0, 1.0, 2.0), bounds=(-0.5, 0.5))

    assert [i.code for i in _bounds_issues(axis, check_values=False)] == [
        "axis.bounds-length"
    ]


# Each case pairs an axis with the `name` it is filed under, so the reader sees at
# the case site whether `coordinates` restates that name. The rule fires exactly
# when `coordinates == (name,)`: the one-element default the spec forbids stating.
@pytest.mark.parametrize(
    ("name", "axis"),
    [
        # A primitive axis restating its own name (the one-element default).
        ("x", Axis(values=(0.0, 1.0, 2.0), coordinates=("x",))),
        # A custom dataType is in scope too: 6.1.1 states the default generally and
        # defines no coordinate structure for a custom type, so a one-element
        # `coordinates` naming the axis restates the default just the same.
        ("t", Axis(values=(0.0, 1.0, 2.0), data_type="ex:custom", coordinates=("t",))),
    ],
)
def test_stated_default_coordinates_is_reported(name: str, axis: Axis) -> None:
    issues = _coordinates_issues(name, axis)

    assert [i.code for i in issues] == ["axis.coordinates-not-omitted"]
    assert issues[0].axis == name
    assert issues[0].at == f"/axes/{name}/coordinates"


@pytest.mark.parametrize(
    ("name", "axis"),
    [
        # `coordinates` names a different identifier, not the axis's own name.
        ("x", Axis(values=(0.0, 1.0, 2.0), coordinates=("y",))),
        # `coordinates` omitted (the conformant form).
        ("x", Axis.listed((0.0, 1.0, 2.0))),
        # A tuple axis's one-element `coordinates` equal to the axis name is
        # load-bearing (it fixes arity), not a restated default #137 flags;
        # composites are excluded. A polygon's >= 2 identifiers can never equal
        # the one-element default, so only tuple can exercise the exclusion.
        ("x", Axis(values=((0.0,), (1.0,)), data_type="tuple", coordinates=("x",))),
    ],
)
def test_non_default_or_omitted_coordinates_is_silent(name: str, axis: Axis) -> None:
    assert _coordinates_issues(name, axis) == []


def test_coordinates_check_runs_without_check_values() -> None:
    # Like the bounds test, this is O(1) per axis and not gated by `check_values`:
    # a stated default is caught in the default pass.
    axis = Axis(values=(0.0, 1.0, 2.0), coordinates=("x",))

    assert [i.code for i in _coordinates_issues("x", axis, check_values=False)] == [
        "axis.coordinates-not-omitted"
    ]


def _composite_issues(axis: Axis) -> list[Issue]:
    """The ``axis.composite-*`` issues a one-axis domain's ``axis`` draws."""
    domain = Domain(axes={"composite": axis}, referencing=_REF)

    return [
        issue
        for issue in validate(domain, check_values=True)
        if issue.code.startswith("axis.composite-")
    ]


def _polygon_deep_issues(axis: Axis) -> list[Issue]:
    """The ``axis.polygon-*`` depth-3 issues a one-axis domain's ``axis`` draws."""
    domain = Domain(axes={"composite": axis}, referencing=_REF)

    return [
        issue
        for issue in validate(domain, check_values=True)
        if issue.code.startswith("axis.polygon-")
    ]


def _bounds_issues(axis: Axis, *, check_values: bool = True) -> list[AxisBoundsLength]:
    """The ``axis.bounds-length`` issues a one-axis domain's ``axis`` draws."""
    domain = Domain(axes={"a": axis}, referencing=_REF)

    return [
        issue
        for issue in validate(domain, check_values=check_values)
        if isinstance(issue, AxisBoundsLength)
    ]


def _coordinates_issues(
    name: str, axis: Axis, *, check_values: bool = True
) -> list[AxisCoordinatesNotOmitted]:
    """The ``axis.coordinates-not-omitted`` issues a domain's ``axis`` draws when
    filed under ``name``; ``name`` is the identifier the check compares
    ``coordinates`` against."""
    domain = Domain(axes={name: axis}, referencing=_REF)

    return [
        issue
        for issue in validate(domain, check_values=check_values)
        if isinstance(issue, AxisCoordinatesNotOmitted)
    ]


def _value_type_paths(issues: list[Issue]) -> list[str]:
    """Paths of the value-type-mismatch issues, in document order."""
    return [i.at for i in issues if i.code == "range.value-type-mismatch"]


def _expected_value_type_paths(
    data_type: Literal["float", "integer", "string"],
    values: tuple[float | int | str | None, ...],
) -> list[str]:
    """Independent oracle: which value indices a from-scratch scan flags.

    Restates the spec rule directly rather than calling the library's own
    matcher, so the differential test is a genuine check of the fast screen and
    not a tautology: a real number for ``"float"`` (``bool`` excluded), a Python
    ``int`` for ``"integer"`` (``bool`` and any ``float`` excluded), a ``str``
    for ``"string"``; ``None`` is always allowed.

    Examples
    --------
    >>> _expected_value_type_paths("integer", (1, 1.5, None, "x"))
    ['/values/1', '/values/3']
    """

    def ok(value: float | int | str | None) -> bool:
        if value is None:
            return True

        if isinstance(value, bool):
            return False

        if data_type == "float":
            return isinstance(value, (int, float))

        if data_type == "integer":
            return isinstance(value, int)

        return isinstance(value, str)

    return [f"/values/{i}" for i, value in enumerate(values) if not ok(value)]


def _coverage_with_range(arr: NdArray) -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": arr},
    )


def _describe(issue: Issue) -> str:
    """Group a finding by category via an exhaustive `match`.

    The `assert_never` default is the point: every `Issue` variant must be
    handled, so a new finding kind fails the strict type check until it is added
    here. This mirrors how a consumer groups findings by the *type*
    (compiler-checked) rather than by splitting the string ``code``.
    """
    match issue:
        case (
            DomainMissingAxis()
            | DomainAxisNotSingle()
            | DomainCompositeDataType()
            | DomainCompositeCoordinates()
            | DomainExtraAxisNotSingle()
            | DomainMissingReferencing()
            | DomainMissingDomainType()
        ):
            return "domain"
        case (
            AxisNotMonotonic()
            | AxisCompositeValueShape()
            | AxisCompositeArity()
            | AxisPolygonPositionArity()
            | AxisPolygonRingTooShort()
            | AxisPolygonRingNotClosed()
            | AxisBoundsLength()
            | AxisCoordinatesNotOmitted()
        ):
            return "axis"
        case NdArrayShapeRank() | NdArrayValueCount():
            return "ndarray"
        case (
            TiledNdArrayShapeRank()
            | TiledNdArrayTileShapeTooLarge()
            | TiledNdArrayTileShapeNotPositive()
            | TiledNdArrayUrlTemplateMissingVariable()
            | TiledNdArrayUrlTemplateUnknownVariable()
        ):
            return "tiled-ndarray"
        case (
            CoverageMissingParameters()
            | CoverageRangeWithoutParameter()
            | CoverageRangeAxisNotInDomain()
            | CoverageRangeShapeMismatch()
            | CoverageDomainTypeNotOmitted()
            | CoverageDomainTypeConflict()
        ):
            return "coverage"
        case RangeValueTypeMismatch() | RangeInvalidCategoryCode():
            return "range"
        case TemporalLexicalForm() | TemporalMissingCalendar():
            return "temporal"
        case IdentifierMissingTargetConcept():
            return "identifier"
        case ParameterGroupUnknownMember():
            return "parameter-group"
        case I18nInvalidLanguageTag() | I18nEmpty():
            return "i18n"
        case _:
            assert_never(issue)


def _axis_domain(axis: Axis, system: ReferenceSystem, coord: str = "x") -> Domain:
    """A one-axis domain wiring ``coord`` to ``system`` (isolates the axis check)."""
    return Domain(
        axes={coord: axis},
        referencing=(ReferenceSystemConnection(coordinates=(coord,), system=system),),
    )


def _monotonic_paths(
    domain: Domain, checker: AxisOrderChecker | None = None
) -> list[str]:
    """The ``at`` pointers of the domain's ``axis.not-monotonic`` issues."""
    issues = validate(domain, check_values=True, axis_order_checker=checker)
    return [i.at for i in issues if i.code == "axis.not-monotonic"]
