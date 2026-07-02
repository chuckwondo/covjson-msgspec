"""Behavioral tests for coverage subsetting (isel / sel)."""

from typing import Any

import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    Domain,
    GeographicCRS,
    NdArray,
    ObservedProperty,
    Parameter,
    ReferenceSystemConnection,
    TiledNdArray,
    TileSet,
    Unit,
    i18n,
    isel,
    sel,
    validate,
)

_REF = (ReferenceSystemConnection(coordinates=("x", "y"), system=GeographicCRS()),)


# --- isel -------------------------------------------------------------------


def test_isel_integer_drops_axis_slice_keeps_it() -> None:
    sub = isel(_grid(), y=0, x=slice(1, 3))

    # x sliced and kept; y dropped from the range, retained as a 1-value axis.
    assert _arr(sub, "v").axis_names == ("x",)
    assert _arr(sub, "v").shape == (2,)
    assert _arr(sub, "v").values == (1.0, 2.0)
    assert _axis(sub, "x").coordinate_values == (10.0, 20.0)
    assert _axis(sub, "y").coordinate_values == (0.0,)


def test_isel_slice_keeps_both_axes() -> None:
    sub = isel(_grid(), x=slice(1, 3))

    assert _arr(sub, "v").axis_names == ("y", "x")
    assert _arr(sub, "v").shape == (2, 2)
    assert _arr(sub, "v").values == (1.0, 2.0, 5.0, 6.0)


def test_isel_integer_on_first_axis_keeps_second() -> None:
    sub = isel(_grid(), y=slice(0, 2), x=2)

    assert _arr(sub, "v").axis_names == ("y",)
    assert _arr(sub, "v").shape == (2,)
    assert _arr(sub, "v").values == (2.0, 6.0)


def test_isel_negative_index() -> None:
    sub = isel(_grid(), x=-1)

    assert _arr(sub, "v").values == (3.0, 7.0)
    assert _axis(sub, "x").coordinate_values == (30.0,)


def test_isel_no_indexers_returns_same_instance() -> None:
    cov = _grid()
    assert isel(cov) is cov


def test_isel_mapping_and_kwargs_merge() -> None:
    sub = isel(_grid(), {"y": 0}, x=slice(0, 2))
    assert _arr(sub, "v").values == (0.0, 1.0)


def test_isel_result_is_valid() -> None:
    sub = isel(_grid(), y=0, x=slice(1, 3))
    assert validate(sub) == []


def test_isel_unknown_axis() -> None:
    with pytest.raises(ValueError, match="unknown axis 'z'"):
        isel(_grid(), z=0)


def test_isel_out_of_bounds() -> None:
    with pytest.raises(IndexError, match="out of bounds for axis of length 4"):
        isel(_grid(), x=4)


def test_isel_conflicting_indexer() -> None:
    with pytest.raises(ValueError, match="both positionally and as a keyword"):
        isel(_grid(), {"x": 0}, x=1)


def test_isel_url_reference_domain() -> None:
    cov = Coverage(domain="https://example.org/d.json", ranges={}, domain_type="Grid")
    with pytest.raises(ValueError, match="URL-reference domain"):
        isel(cov, x=0)


def test_isel_tiled_range_rejected() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)),
        ranges={
            "v": TiledNdArray(
                data_type="float",
                axis_names=("y", "x"),
                shape=(2, 4),
                tile_sets=(
                    TileSet(tile_shape=(1, None), url_template="t/{y}.covjson"),
                ),
            )
        },
    )
    with pytest.raises(ValueError, match="not an inline NdArray"):
        isel(cov, x=0)


def test_isel_composite_axis_unsupported() -> None:
    composite = Axis.tuple_(
        [("2020-01-01T00:00:00Z", 1.0, 2.0)], coordinates=("t", "x", "y")
    )
    cov = Coverage(
        domain=Domain.trajectory(composite),
        ranges={"v": NdArray(data_type="float", values=(1.0,))},
    )
    with pytest.raises(NotImplementedError, match="composite 'tuple' axis"):
        isel(cov, composite=0)


def test_isel_only_touches_ranges_that_vary_over_the_axis() -> None:
    # One range varies over x only; selecting y must leave it untouched.
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)),
        ranges={
            "full": NdArray(
                data_type="float",
                values=tuple(float(i) for i in range(8)),
                shape=(2, 4),
                axis_names=("y", "x"),
            ),
            "x_only": NdArray(
                data_type="float",
                values=(10.0, 11.0, 12.0, 13.0),
                shape=(4,),
                axis_names=("x",),
            ),
        },
    )
    sub = isel(cov, y=1)

    assert _arr(sub, "full").values == (4.0, 5.0, 6.0, 7.0)
    assert _arr(sub, "full").axis_names == ("x",)
    # Unchanged: the y indexer does not apply to a range that lacks a y axis.
    assert sub.ranges["x_only"] is cov.ranges["x_only"]


def test_isel_respects_range_axis_order() -> None:
    # Range stored as (x, y) while the domain lists y then x.
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)),
        ranges={
            "v": NdArray(
                data_type="float",
                # (x, y) row-major: x=0 -> (y0,y1)=(0,1), x=1 -> (2,3), ...
                values=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0),
                shape=(4, 2),
                axis_names=("x", "y"),
            )
        },
    )
    sub = isel(cov, y=1)

    # y=1 picks the second column of each x row: 1, 3, 5, 7.
    assert _arr(sub, "v").axis_names == ("x",)
    assert _arr(sub, "v").values == (1.0, 3.0, 5.0, 7.0)


def test_isel_slices_bounds() -> None:
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.listed((0.0, 10.0, 20.0), bounds=(-5.0, 5.0, 5.0, 15.0, 15.0, 25.0)),
            y=Axis.listed((0.0,)),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(0.0, 1.0, 2.0),
                shape=(1, 3),
                axis_names=("y", "x"),
            )
        },
    )
    sub = isel(cov, x=slice(1, 3))
    assert _axis(sub, "x").coordinate_values == (10.0, 20.0)
    assert _axis(sub, "x").bounds == (5.0, 15.0, 15.0, 25.0)


def test_isel_scalar_range_untouched() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)),
        ranges={"const": NdArray(data_type="float", values=(42.0,))},
    )
    sub = isel(cov, x=0)
    assert sub.ranges["const"] is cov.ranges["const"]


def test_isel_method_delegate() -> None:
    sub = _grid().isel(y=0, x=slice(1, 3))
    assert _arr(sub, "v").values == (1.0, 2.0)


# --- sel --------------------------------------------------------------------


def test_sel_exact_numeric() -> None:
    sub = sel(_grid(), x=20.0, y=0.0)
    assert _arr(sub, "v").axis_names == ()
    assert _arr(sub, "v").values == (2.0,)


def test_sel_nearest_numeric() -> None:
    sub = sel(_grid(), x=11.0, method="nearest")
    assert _axis(sub, "x").coordinate_values == (10.0,)
    assert _arr(sub, "v").values == (1.0, 5.0)


def test_sel_label_slice_inclusive() -> None:
    sub = sel(_grid(), x=slice(10.0, 20.0))
    assert _axis(sub, "x").coordinate_values == (10.0, 20.0)
    assert _arr(sub, "v").values == (1.0, 2.0, 5.0, 6.0)


def test_sel_mixes_slice_with_nearest_scalar() -> None:
    sub = sel(_grid(), x=11.0, y=slice(0.0, 10.0), method="nearest")
    assert _arr(sub, "v").axis_names == ("y",)
    assert _arr(sub, "v").values == (1.0, 5.0)


def test_sel_exact_time_string() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01", "2020-01-02", "2020-01-03")),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(10.0, 20.0, 30.0),
                shape=(3,),
                axis_names=("t",),
            )
        },
    )
    sub = sel(cov, t="2020-01-02")
    assert _arr(sub, "v").values == (20.0,)
    assert _axis(sub, "t").coordinate_values == ("2020-01-02",)


def test_sel_label_not_found() -> None:
    with pytest.raises(KeyError, match="not found on axis 'x'"):
        sel(_grid(), x=15.0)


def test_sel_empty_slice() -> None:
    with pytest.raises(KeyError, match="fall within"):
        sel(_grid(), x=slice(100.0, 200.0))


def test_sel_nearest_on_non_numeric_axis() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01", "2020-01-02")),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(10.0, 20.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    with pytest.raises(TypeError, match="numeric axis"):
        sel(cov, t="2020-01-01", method="nearest")


def test_sel_unsupported_method() -> None:
    # Route the bad value through Any so the negative test reaches the runtime
    # guard rather than being rejected statically by the type checkers.
    bad_method: Any = "pad"
    with pytest.raises(ValueError, match="unsupported method"):
        sel(_grid(), x=10.0, method=bad_method)


def test_sel_method_delegate() -> None:
    sub = _grid().sel(x=11.0, method="nearest")
    assert _arr(sub, "v").values == (1.0, 5.0)


# --- equivalence with xarray ------------------------------------------------


def test_isel_matches_xarray_roundtrip() -> None:
    pytest.importorskip("xarray")

    cov = _grid()
    sub = isel(cov, y=1, x=slice(0, 3))

    ds = cov.to_xarray().isel(y=1, x=slice(0, 3))
    assert _arr(sub, "v").values == tuple(ds["v"].values.ravel().tolist())
    assert _axis(sub, "x").coordinate_values == tuple(ds["x"].values.tolist())
    # xarray keeps y as a scalar coordinate; our domain keeps it as a 1-value axis.
    assert _axis(sub, "y").coordinate_values == (float(ds["y"].values),)


def _grid() -> Coverage:
    """A 2 (y) x 4 (x) grid whose single range holds 0..7 in row-major order.

    Fully spec-valid (carries referencing and a matching parameter) so that
    `test_isel_result_is_valid` can assert a subset of a valid coverage stays
    valid.
    """
    return Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 30.0, 4),
            y=Axis.regular(0.0, 10.0, 2),
            referencing=_REF,
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=tuple(float(i) for i in range(8)),
                shape=(2, 4),
                axis_names=("y", "x"),
            )
        },
        parameters={
            "v": Parameter.continuous(
                ObservedProperty(label=i18n("Value")), Unit(symbol="1")
            )
        },
    )


def _arr(coverage: Coverage, key: str) -> NdArray:
    """The named range, narrowed to `NdArray` for typed attribute access."""
    range_ = coverage.ranges[key]
    assert isinstance(range_, NdArray)
    return range_


def _axis(coverage: Coverage, name: str) -> Axis:
    """The named domain axis, narrowed past the `Domain | str` domain union."""
    domain = coverage.domain
    assert isinstance(domain, Domain)
    return domain.axes[name]
