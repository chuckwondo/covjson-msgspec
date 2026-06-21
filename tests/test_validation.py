"""Behavioral tests for document-level validation."""

import pytest

from covjson_msgspec import (
    Axis,
    Category,
    Coverage,
    CoverageCollection,
    CovJSONValidationError,
    Domain,
    NdArray,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Severity,
    Unit,
    i18n,
    validate,
)


def test_valid_grid_has_no_issues() -> None:
    grid = Domain.grid(x=Axis.regular(0, 10, 3), y=Axis.regular(0, 10, 3))

    assert validate(grid) == []


def test_missing_required_axis() -> None:
    domain = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")
    (issue,) = validate(domain)

    assert issue.code == "domain.missing-axis"
    assert issue.path == "/axes/y"
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


def test_unexpected_axis_is_a_warning() -> None:
    domain = Domain(
        axes={
            "x": Axis.regular(0, 1, 2),
            "y": Axis.regular(0, 1, 2),
            "bogus": Axis.listed((1.0,)),
        },
        domain_type="Grid",
    )
    (issue,) = validate(domain)

    assert issue.code == "domain.unexpected-axis"
    assert issue.severity is Severity.WARNING
    assert issue.path == "/axes/bogus"


def test_unknown_domain_type_is_not_checked() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,))}, domain_type="http://example/Custom"
    )

    assert validate(domain) == []


def test_ndarray_value_count_mismatch() -> None:
    arr = NdArray(data_type="float", values=(1.0, 2.0), shape=(3,), axis_names=("x",))
    (issue,) = validate(arr)

    assert issue.code == "ndarray.value-count"
    assert issue.path == "/values"


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


def test_range_without_parameter_is_a_warning() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"unknown": NdArray(data_type="float", values=(1.0,))},
        parameters={"t": temp},
    )
    warnings = [i for i in validate(cov) if i.severity is Severity.WARNING]

    assert any(i.code == "coverage.range-without-parameter" for i in warnings)


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
    assert bad[0].path == "/ranges/lc/values/1"


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
    assert all(i.path.startswith("/coverages/0/") for i in issues)


def test_raise_mode_raises_on_error() -> None:
    domain = Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid")

    with pytest.raises(CovJSONValidationError) as excinfo:
        validate(domain, mode="raise")

    assert excinfo.value.issues
    assert excinfo.value.issues[0].code == "domain.missing-axis"


def test_raise_mode_returns_warnings_without_raising() -> None:
    domain = Domain(
        axes={
            "x": Axis.regular(0, 1, 2),
            "y": Axis.regular(0, 1, 2),
            "bogus": Axis.listed((1.0,)),
        },
        domain_type="Grid",
    )
    issues = validate(domain, mode="raise")

    assert [i.severity for i in issues] == [Severity.WARNING]
