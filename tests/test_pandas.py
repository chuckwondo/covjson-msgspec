"""Behavioral tests for the pandas bridge (to_pandas)."""

import numpy as np
import pandas as pd
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
    ReferenceSystemConnection,
    TemporalRS,
    TiledNdArray,
    TileSet,
    Unit,
    i18n,
    to_pandas,
)


def _point_series_member(
    coverage_id: str | None, x: float, values: tuple[float, ...]
) -> Coverage:
    return Coverage(
        id=coverage_id,
        domain=Domain.point_series(
            x=Axis.listed((x,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b")),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=values, shape=(2,), axis_names=("t",)
            )
        },
    )


def test_point_is_single_row_with_scalar_columns() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
    df = to_pandas(cov)

    assert len(df) == 1
    assert df["x"].tolist() == [1.0]
    assert df["y"].tolist() == [2.0]
    assert df["t"].tolist() == [280.0]


def test_coverage_method_delegates() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )

    assert cov.to_pandas()["t"].tolist() == [280.0]


def test_point_series_indexed_by_axis_with_constant_columns() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b", "c")),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0),
                shape=(3,),
                axis_names=("t",),
            )
        },
    )
    df = to_pandas(cov)

    assert df.index.name == "t"
    assert df.index.tolist() == ["a", "b", "c"]
    assert df["v"].tolist() == [1.0, 2.0, 3.0]
    # The single-valued x / y axes become constant columns.
    assert df["x"].tolist() == [1.0, 1.0, 1.0]
    assert df["y"].tolist() == [2.0, 2.0, 2.0]


def test_standard_calendar_time_axis_parsed_to_datetime() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",), system=TemporalRS(calendar="Gregorian")
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    df = to_pandas(cov)

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index[0] == pd.Timestamp("2020-01-01T00:00:00")


def test_non_standard_calendar_time_stays_strings() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01", "2020-01-30")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",), system=TemporalRS(calendar="360_day")
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    df = to_pandas(cov)

    assert df.index.tolist() == ["2020-01-01", "2020-01-30"]


def test_vertical_profile_indexed_by_z() -> None:
    cov = Coverage(
        domain=Domain.vertical_profile(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            z=Axis.listed((10.0, 20.0, 30.0)),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(5.0, 6.0, 7.0),
                shape=(3,),
                axis_names=("z",),
            )
        },
    )
    df = to_pandas(cov)

    assert df.index.name == "z"
    assert df.index.tolist() == [10.0, 20.0, 30.0]
    assert df["v"].tolist() == [5.0, 6.0, 7.0]


def test_trajectory_composite_axis_becomes_component_columns() -> None:
    composite = Axis(
        data_type="tuple",
        coordinates=("t", "x", "y"),
        values=(
            ("2020-01-01", 1.0, 10.0),
            ("2020-01-02", 2.0, 20.0),
            ("2020-01-03", 3.0, 30.0),
        ),
    )
    cov = Coverage(
        domain=Domain.trajectory(composite),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(5.0, 6.0, 7.0),
                shape=(3,),
                axis_names=("composite",),
            )
        },
    )
    df = to_pandas(cov)

    assert df.index.name == "composite"
    assert df["x"].tolist() == [1.0, 2.0, 3.0]
    assert df["y"].tolist() == [10.0, 20.0, 30.0]
    assert df["t"].tolist() == ["2020-01-01", "2020-01-02", "2020-01-03"]
    assert df["v"].tolist() == [5.0, 6.0, 7.0]


def test_grid_flattens_to_long_form_multiindex() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 10.0, 2), y=Axis.regular(0.0, 5.0, 2)),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )
    df = to_pandas(cov)

    assert isinstance(df.index, pd.MultiIndex)
    assert df.index.names == ["x", "y"]
    # Row-major over (x, y): the (y, x) range data is transposed to match.
    assert df.loc[(0.0, 0.0), "v"] == 1.0
    assert df.loc[(0.0, 5.0), "v"] == 3.0
    assert df.loc[(10.0, 0.0), "v"] == 2.0
    assert df.loc[(10.0, 5.0), "v"] == 4.0


def test_missing_float_values_become_nan() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b", "c")),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, None, 3.0),
                shape=(3,),
                axis_names=("t",),
            )
        },
    )
    df = to_pandas(cov)

    assert df["v"].tolist()[0] == 1.0
    assert np.isnan(df["v"].tolist()[1])


def test_missing_integer_values_become_nan() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b")),
        ),
        ranges={
            "lc": NdArray(
                data_type="integer",
                values=(2, None),
                shape=(2,),
                axis_names=("t",),
            )
        },
    )
    df = to_pandas(cov)

    assert df["lc"].tolist()[0] == 2.0
    assert np.isnan(df["lc"].tolist()[1])


def test_categorical_codes_are_columns() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(
            Category(id="1", label=i18n("Open water")),
            Category(id="2", label=i18n("Forest")),
        ),
    )
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b")),
        ),
        ranges={
            "lc": NdArray(
                data_type="integer", values=(1, 2), shape=(2,), axis_names=("t",)
            )
        },
        parameters={"lc": Parameter.categorical(land_cover, {"1": 1, "2": 2})},
    )
    df = to_pandas(cov)

    assert df["lc"].tolist() == [1, 2]


def test_multiple_parameters_become_columns() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b")),
        ),
        ranges={
            "temp": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            ),
            "humidity": NdArray(
                data_type="float", values=(3.0, 4.0), shape=(2,), axis_names=("t",)
            ),
        },
    )
    df = to_pandas(cov)

    assert df["temp"].tolist() == [1.0, 2.0]
    assert df["humidity"].tolist() == [3.0, 4.0]


def test_attrs_carry_domain_type_and_id() -> None:
    cov = Coverage(
        id="urn:cov:1",
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
    df = to_pandas(cov)

    assert df.attrs["domain_type"] == "Point"
    assert df.attrs["id"] == "urn:cov:1"


def test_url_domain_is_rejected() -> None:
    cov = Coverage(domain="http://example/domain.json", ranges={})

    with pytest.raises(ValueError, match="URL reference"):
        to_pandas(cov)


def test_polygon_domain_routes_to_geopandas() -> None:
    cov = Coverage(
        domain=Domain(axes={"composite": Axis.listed((1.0,))}, domain_type="Polygon"),
        ranges={},
    )

    with pytest.raises(ValueError, match="geopandas"):
        to_pandas(cov)


def test_non_ndarray_range_is_rejected() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={
            "v": TiledNdArray(
                data_type="float",
                axis_names=("x",),
                shape=(2,),
                tile_sets=(TileSet(tile_shape=(1,), url_template="http://ex/{x}"),),
            )
        },
    )

    with pytest.raises(ValueError, match="inline NdArray"):
        to_pandas(cov)


def test_string_parameter_values_preserved() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("a", "b")),
        ),
        ranges={
            "label": NdArray(
                data_type="string",
                values=("low", "high"),
                shape=(2,),
                axis_names=("t",),
            )
        },
        parameters={
            "label": Parameter.continuous(
                ObservedProperty(label=i18n("Level")), Unit(symbol="1")
            )
        },
    )
    df = to_pandas(cov)

    assert df["label"].tolist() == ["low", "high"]


def test_collection_concatenates_members_under_coverage_level() -> None:
    collection = CoverageCollection(
        coverages=(
            _point_series_member("a", 1.0, (1.0, 2.0)),
            _point_series_member("b", 3.0, (3.0, 4.0)),
        ),
        domain_type="PointSeries",
    )
    df = to_pandas(collection)

    assert df.index.names == ["coverage", "t"]
    assert df.loc[("a", "a"), "v"] == 1.0
    assert df.loc[("b", "b"), "v"] == 4.0
    # Inherited per-member columns survive the concat.
    assert df.loc[("a", "b"), "x"] == 1.0
    assert df.loc[("b", "a"), "x"] == 3.0


def test_collection_method_delegates() -> None:
    collection = CoverageCollection(
        coverages=(_point_series_member("a", 1.0, (1.0, 2.0)),),
    )

    assert collection.to_pandas().loc[("a", "b"), "v"] == 2.0


def test_collection_keys_fall_back_to_position_without_id() -> None:
    collection = CoverageCollection(
        coverages=(
            _point_series_member(None, 1.0, (1.0, 2.0)),
            _point_series_member(None, 3.0, (3.0, 4.0)),
        ),
    )
    df = to_pandas(collection)

    assert df.index.get_level_values("coverage").unique().tolist() == [0, 1]


def test_collection_inherits_parameters_and_referencing() -> None:
    member = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    collection = CoverageCollection(
        coverages=(member,),
        domain_type="PointSeries",
        referencing=(
            ReferenceSystemConnection(
                coordinates=("t",), system=TemporalRS(calendar="Gregorian")
            ),
        ),
    )
    df = to_pandas(collection)

    # The collection's referencing tags t as temporal, so it parses to datetimes.
    times = df.index.get_level_values("t")
    assert isinstance(times, pd.DatetimeIndex)
    assert times[0] == pd.Timestamp("2020-01-01T00:00:00")
    assert df.attrs["domain_type"] == "PointSeries"


def test_empty_collection_is_empty_frame() -> None:
    assert to_pandas(CoverageCollection(coverages=())).empty
