"""Tests for the Jupyter ``_repr_html_`` summaries.

These assert structure and escaping rather than exact markup: the builders emit
a self-contained ``<div class="cj-repr">`` with collapsible sections, and the
goal is that each displayable type renders without error, surfaces its key facts,
and never injects unescaped text.
"""

import pytest

from covjson_msgspec import (
    Axis,
    Category,
    Coverage,
    CoverageCollection,
    Domain,
    NdArray,
    ObservedProperty,
    Parameter,
    Symbol,
    TiledNdArray,
    TileSet,
    Unit,
    i18n,
)


def _temp_parameter() -> Parameter:
    """A continuous air-temperature parameter in kelvin."""
    return Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )


def _temp_range() -> NdArray:
    """A 2 (y) x 4 (x) float range holding 1..8 in row-major order."""
    return NdArray(
        data_type="float",
        values=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        shape=(2, 4),
        axis_names=("y", "x"),
    )


def _grid_coverage() -> Coverage:
    """A small Grid coverage with one continuous parameter and an NdArray range."""
    return Coverage(
        id="cov-1",
        domain=Domain.grid(
            x=Axis.regular(-180.0, 180.0, 4),
            y=Axis.listed((0.0, 1.0)),
        ),
        ranges={"t": _temp_range()},
        parameters={"t": _temp_parameter()},
    )


def test_coverage_repr_has_card_and_facts() -> None:
    html = _grid_coverage()._repr_html_()

    assert html.startswith('<div class="cj-repr">')
    assert html.endswith("</div>")
    # Identity, the domain type, the axes, the parameter, and the range key.
    assert "Coverage" in html
    assert "cov-1" in html
    assert "Grid" in html
    assert "Air temperature" in html
    assert ">t<" in html


def test_coverage_repr_summarizes_axes_without_materializing() -> None:
    # A huge regular axis must not blow up the repr: it is summarized from
    # start/stop/num, not expanded into a value list.
    big = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 1.0, 1_000_000), y=Axis.listed((0.0,))),
        ranges={},
    )
    html = big._repr_html_()

    assert "1000000" in html  # the x axis length
    assert "0.0 to 1.0" in html  # its extent, not a million values


def test_coverage_repr_escapes_dynamic_text() -> None:
    nasty = "<script>alert('x')</script>"
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={},
        parameters={
            "p": Parameter.continuous(
                ObservedProperty(label=i18n(nasty)), Unit(symbol="K")
            )
        },
    )
    html = cov._repr_html_()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_ndarray_string_values_are_escaped() -> None:
    # A string-typed range's values are arbitrary text and must be escaped in
    # the value preview, not injected as raw markup.
    arr = NdArray(
        data_type="string",
        values=("<script>alert(1)</script>", "a & b"),
        shape=(2,),
        axis_names=("x",),
    )
    html = arr._repr_html_()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_collection_missing_member_id_shows_placeholder() -> None:
    # A member with no id renders the "(none)" placeholder, not a blank cell.
    member = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={},
    )
    html = CoverageCollection(coverages=(member,), domain_type="Point")._repr_html_()

    assert "(none)" in html


def test_coverage_repr_with_url_reference_domain() -> None:
    cov = Coverage(
        domain="https://example.org/domain.json",
        ranges={"t": "https://example.org/range.json"},
        domain_type="Grid",
    )
    html = cov._repr_html_()

    assert "https://example.org/domain.json" in html
    # A URL-string range is labeled as a reference, not an inline array.
    assert "reference" in html


def test_collection_repr_lists_members() -> None:
    member = _grid_coverage()
    coll = CoverageCollection(
        coverages=(member, member),
        domain_type="Grid",
        parameters={"t": _temp_parameter()},
    )
    html = coll._repr_html_()

    assert "CoverageCollection" in html
    assert "Members (2)" in html
    assert "Air temperature" in html


def test_domain_repr_lists_axes() -> None:
    dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.listed((0.0, 1.0)))
    html = dom._repr_html_()

    assert "Domain" in html
    assert "Axes (2)" in html
    assert "0.0 to 10.0" in html


def test_ndarray_repr_shows_shape_and_preview() -> None:
    arr = NdArray(
        data_type="float",
        values=tuple(float(i) for i in range(10)),
        shape=(10,),
        axis_names=("x",),
    )
    html = arr._repr_html_()

    assert "NdArray" in html
    assert "(10,)" in html
    # Long value sequences are elided in the middle.
    assert "..." in html


def test_ndarray_repr_scalar_shape() -> None:
    arr = NdArray(data_type="float", values=(280.0,))
    html = arr._repr_html_()

    assert "scalar" in html
    assert "(none)" in html  # no axis names


def test_tiled_ndarray_repr_lists_tile_sets() -> None:
    tiled = TiledNdArray(
        data_type="float",
        axis_names=("t", "y", "x"),
        shape=(4, 100, 100),
        tile_sets=(
            TileSet(tile_shape=(1, None, None), url_template="http://ex/{t}.covjson"),
        ),
    )
    html = tiled._repr_html_()

    assert "TiledNdArray" in html
    assert "http://ex/{t}.covjson" in html
    assert "(4, 100, 100)" in html
    # Four time steps, one per tile, with y/x un-subdivided.
    assert ">4<" in html


def test_parameter_repr_continuous_shows_unit() -> None:
    param = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")),
        Unit(symbol=Symbol(value="Cel", type_="http://ex/Cel")),
    )
    html = param._repr_html_()

    assert "continuous" in html
    assert "Air temperature" in html
    assert "Cel" in html  # the Symbol's value, not its object repr


def test_parameter_repr_categorical_lists_categories() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(
            Category(id="1", label=i18n("Water")),
            Category(id="2", label=i18n("Forest")),
        ),
    )
    param = Parameter.categorical(land_cover, {"1": 1, "2": 2})
    html = param._repr_html_()

    assert "categorical" in html
    assert "Categories" in html
    assert "Water" in html
    assert "Forest" in html


@pytest.mark.parametrize(
    "obj",
    [
        _grid_coverage(),
        _grid_coverage().domain,
        _temp_range(),
        _temp_parameter(),
    ],
)
def test_repr_html_is_nonempty_str(obj: object) -> None:
    html = obj._repr_html_()  # type: ignore[attr-defined]

    assert isinstance(html, str)
    assert html.strip()
