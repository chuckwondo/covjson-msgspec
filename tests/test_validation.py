"""Behavioral tests for document-level validation."""

import pytest

from covjson_msgspec import (
    Axis,
    Category,
    Coverage,
    CoverageCollection,
    CovJSONValidationError,
    Domain,
    GeographicCRS,
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
from covjson_msgspec.range import TileSet

# A minimal valid referencing array. Domains and coverages built for the
# axis/range checks below carry it so they isolate the one issue under test
# rather than also tripping the spec-required referencing/parameters presence
# checks (see test_missing_referencing / test_missing_parameters for those).
_REF = (ReferenceSystemConnection(coordinates=("x", "y"), system=GeographicCRS()),)


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
    assert issue.path == "/axes/bogus"


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


def _value_type_paths(issues: list[Issue]) -> list[str]:
    """Paths of the value-type-mismatch issues, in document order."""
    return [i.path for i in issues if i.code == "range.value-type-mismatch"]


def _coverage_with_range(arr: NdArray) -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": arr},
    )


def test_value_data_type_check_is_opt_in() -> None:
    cov = _coverage_with_range(NdArray(data_type="integer", values=(1, 1.5)))

    # Off by default: the float in an integer range is not scanned.
    assert _value_type_paths(validate(cov)) == []

    # Opt in: the float is flagged, with its index in the path.
    issues = validate(cov, check_values=True)
    bad = [i for i in issues if i.code == "range.value-type-mismatch"]

    assert len(bad) == 1
    assert bad[0].path == "/ranges/v/values/1"
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


def test_missing_referencing_on_standalone_domain() -> None:
    domain = Domain(
        axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))},
        domain_type="Point",
    )
    (issue,) = validate(domain)

    assert issue.code == "domain.missing-referencing"
    assert issue.path == "/referencing"
    assert issue.severity is Severity.ERROR


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
    assert issue.path == "/parameters"
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
    assert issue.path == "/tileSets/0/tileShape/0"


def test_tiled_ndarray_url_template_missing_variable() -> None:
    arr = TiledNdArray(
        data_type="float",
        axis_names=("t", "x"),
        shape=(4, 2),
        tile_sets=(TileSet(tile_shape=(1, None), url_template="tile.covjson"),),
    )
    (issue,) = validate(arr)

    assert issue.code == "tiled-ndarray.url-template-missing-variable"
    assert issue.path == "/tileSets/0/urlTemplate"


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
    assert issue.path == "/tileSets/0/tileShape/0"


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

    assert issue.path == "/ranges/t/tileSets/0/tileShape/0"


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
