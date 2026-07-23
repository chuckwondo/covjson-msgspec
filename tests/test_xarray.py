"""Behavioral tests for the xarray bridge (to_xarray / from_xarray)."""

import warnings
from collections.abc import Mapping

import numpy as np
import pytest
import xarray as xr
from msgspec import UNSET

from covjson_msgspec import (
    Axis,
    Category,
    Coverage,
    CoverageCollection,
    Domain,
    GeographicCRS,
    NdArray,
    ObservedProperty,
    Parameter,
    ProjectedCRS,
    ReferenceSystem,
    ReferenceSystemConnection,
    TiledNdArray,
    TileSet,
    Unit,
    from_datatree,
    from_xarray,
    i18n,
    to_datatree,
    to_xarray,
    validate,
)


def test_grid_maps_ranges_to_data_variables() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 10.0, 2), y=Axis.regular(0.0, 5.0, 2)),
        ranges={
            "t": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["t"].dims == ("y", "x")
    assert ds["t"].values.tolist() == [[1.0, 2.0], [3.0, 4.0]]
    assert ds["x"].values.tolist() == [0.0, 10.0]
    assert ds.attrs["domain_type"] == "Grid"
    assert ds.attrs["Conventions"].startswith("CF-")


def test_coverage_method_delegates() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )

    assert cov.to_xarray()["t"].item() == 280.0


def test_single_valued_axis_becomes_scalar_coord() -> None:
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 10.0, 3),
            y=Axis.listed((45.0,)),
        ),
        ranges={
            "t": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0),
                shape=(3,),
                axis_names=("x",),
            )
        },
    )
    ds = to_xarray(cov)

    # The single-valued y axis is a scalar coordinate, not a dimension.
    assert ds["y"].dims == ()
    assert ds["y"].item() == 45.0
    assert "y" not in ds.dims


def test_temporal_axis_parsed_to_datetime64() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["t"].dtype == np.dtype("datetime64[ns]")
    assert str(ds["t"].values[0]) == "2020-01-01T00:00:00.000000000"


def test_temporal_non_standard_calendar_uses_cftime() -> None:
    import cftime  # pyright: ignore[reportMissingTypeStubs]

    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01", "2020-01-30")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="360_day"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    ds = to_xarray(cov)

    # cftime ships no type stubs, so its datetime member types as Unknown (the
    # ignore is scoped to that one access); the value itself is Any from xarray.
    t0 = ds["t"].values[0]
    assert isinstance(t0, cftime.datetime)  # pyright: ignore[reportUnknownMemberType]
    assert t0.calendar == "360_day"


@pytest.mark.parametrize(
    ("edge_value", "expected_unit"),
    [
        ("1677-11-01T00:00:00Z", "datetime64[ns]"),  # just inside the lower ns bound
        ("2262-03-01T00:00:00Z", "datetime64[ns]"),  # just inside the upper ns bound
        ("2262-06-01T00:00:00Z", "datetime64[us]"),  # just outside, same year
        ("2300-01-15T00:00:00Z", "datetime64[us]"),  # well outside
    ],
)
def test_temporal_standard_calendar_datetime64_unit_tracks_ns_window(
    edge_value: str, expected_unit: str
) -> None:
    # A standard-calendar time uses datetime64[ns] when the whole column fits
    # numpy's window and widens to [us] otherwise, a faithful native datetime
    # either way, never cftime and never an int64 wrap. The edge value is paired
    # with an in-range anchor, so an out-of-range value widening the column is
    # exercised too.
    t_values = ("2020-01-15T00:00:00Z", edge_value)
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(t_values),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["t"].dtype == np.dtype(expected_unit)
    assert str(ds["t"].values[1]).startswith(edge_value[:4])  # year kept, no wrap


def test_temporal_out_of_ns_range_round_trips_faithfully() -> None:
    # A spec-valid Gregorian date outside numpy's ns window must stay faithful
    # through to_xarray and back through from_xarray, not int64-wrap to a wrong
    # in-range date.
    t_values = ("2020-01-15T00:00:00Z", "2300-01-15T00:00:00Z")
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(t_values),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["t"].dtype == np.dtype("datetime64[us]")
    assert [str(v)[:4] for v in ds["t"].values] == ["2020", "2300"]

    back = from_xarray(ds)
    assert isinstance(back.domain, Domain)
    assert back.domain.axes["t"].values == t_values


def test_temporal_offset_flattens_to_naive_utc_without_warning() -> None:
    # A ±hh:mm offset (a Spec 5.2 form) is applied and flattened to naive-UTC,
    # the same result the Z / naive path produces (ADR-0015). numpy has no
    # timezone type, so it announces the flatten with a UserWarning; the bridge
    # suppresses it, so none leaks. The axis also mixes a Z and an offset value
    # to show a single column carries both.
    t_values = ("2020-01-15T00:00:00Z", "2020-01-15T00:00:00+05:00")
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(t_values),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )

    # Assert on any UserWarning, not just numpy's current wording: the production
    # filter keys on that exact message, so pinning the test to the same string
    # would let the warning silently leak again if numpy ever rewords it.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        ds = to_xarray(cov)

    # +05:00 at 00:00 is 19:00 the previous day in UTC; the Z value is unchanged.
    assert [str(v) for v in ds["t"].values] == [
        "2020-01-15T00:00:00.000000000",
        "2020-01-14T19:00:00.000000000",
    ]


def test_geographic_referencing_sets_cf_attrs_and_grid_mapping() -> None:
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 10.0, 2),
            y=Axis.regular(0.0, 5.0, 2),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"),
                    system=ReferenceSystem.geographic(
                        id="http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                    ),
                ),
            ),
        ),
        ranges={
            "t": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["x"].attrs["standard_name"] == "longitude"
    assert ds["x"].attrs["units"] == "degrees_east"
    assert ds["y"].attrs["standard_name"] == "latitude"
    assert ds["y"].attrs["units"] == "degrees_north"
    assert ds["crs"].attrs["grid_mapping_name"] == "latitude_longitude"
    assert ds["t"].attrs["grid_mapping"] == "crs"


def test_geographic_third_coordinate_is_height() -> None:
    # A geographic system lists its coordinates in CoverageJSON order (longitude,
    # latitude, then an optional height), so a third coordinate takes the height
    # role and its CF standard_name / positive attrs.
    cov = Coverage(
        domain=Domain(
            axes={
                "x": Axis.listed((1.0,)),
                "y": Axis.listed((2.0,)),
                "z": Axis.listed((100.0,)),
            },
            domain_type="Grid",
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y", "z"),
                    system=ReferenceSystem.geographic(),
                ),
            ),
        ),
        ranges={"v": NdArray(data_type="float", values=(9.0,))},
    )
    ds = to_xarray(cov)

    assert ds["z"].attrs["standard_name"] == "height"
    assert ds["z"].attrs["positive"] == "up"


def test_vertical_depth_sets_positive_down() -> None:
    cov = Coverage(
        domain=Domain.vertical_profile(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            z=Axis.listed((10.0, 20.0)),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("z",),
                    system=ReferenceSystem.vertical(
                        id="http://example/crs/sea_water_depth"
                    ),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("z",)
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["z"].attrs["positive"] == "down"
    assert ds["z"].attrs["standard_name"] == "depth"


def test_continuous_parameter_attrs() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
        parameters={"t": _temperature()},
    )
    ds = to_xarray(cov)

    assert ds["t"].attrs["units"] == "K"
    assert ds["t"].attrs["long_name"] == "Air temperature"
    assert ds["t"].attrs["standard_name"] == "air_temperature"


def test_categorical_parameter_sets_flag_attrs() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(
            Category(id="1", label=i18n("Open water")),
            Category(id="2", label=i18n("Forest")),
        ),
    )
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"lc": NdArray(data_type="integer", values=(2,))},
        parameters={
            "lc": Parameter.categorical(land_cover, {"1": 1, "2": 2}),
        },
    )
    ds = to_xarray(cov)

    assert ds["lc"].attrs["flag_values"] == (1, 2)
    assert ds["lc"].attrs["flag_meanings"] == "Open_water Forest"


def test_trajectory_composite_axis_becomes_non_dim_coords() -> None:
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
    ds = to_xarray(cov)

    assert ds["v"].dims == ("composite",)
    assert ds["x"].dims == ("composite",)
    assert ds["x"].values.tolist() == [1.0, 2.0, 3.0]
    assert ds["y"].values.tolist() == [10.0, 20.0, 30.0]


def test_url_domain_is_rejected() -> None:
    cov = Coverage(domain="http://example/domain.json", ranges={})

    with pytest.raises(ValueError, match="URL reference"):
        to_xarray(cov)


def test_polygon_domain_routes_to_geopandas() -> None:
    cov = Coverage(
        domain=Domain(axes={"composite": Axis.listed((1.0,))}, domain_type="Polygon"),
        ranges={},
    )

    with pytest.raises(ValueError, match="geopandas"):
        to_xarray(cov)


def test_polygon_axis_without_polygon_domain_type_is_rejected() -> None:
    # The geopandas routing keys on domainType, so a polygon-data-type axis in a
    # domain whose domainType is not a polygon type slips past it and reaches the
    # coordinate builder, whose own guard rejects the vector geometry the gridded
    # bridge cannot represent.
    ring = ((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0))
    axis = Axis(values=((ring,),), data_type="polygon", coordinates=("x", "y"))
    cov = Coverage(
        domain=Domain(axes={"composite": axis}, domain_type="Grid"),
        ranges={},
    )

    with pytest.raises(ValueError, match="polygon axes are not supported"):
        to_xarray(cov)


def test_attrs_omit_domain_type_when_absent() -> None:
    # A domain that declares no domainType leaves the attribute off the dataset
    # rather than storing None (the CF Conventions attr is always present).
    cov = Coverage(
        domain=Domain(axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))}),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
    ds = to_xarray(cov)

    assert "domain_type" not in ds.attrs


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
        to_xarray(cov)


def test_malformed_composite_axis_raises_a_clean_error() -> None:
    # A "tuple" axis whose values are not matching tuples used to index into a
    # float and raise a bare TypeError from inside numpy. The bridge does not
    # require a validated document (validate() reports axis.composite-value-shape),
    # so it rejects the same input here with a clear message.
    cov = Coverage(
        domain=Domain(
            axes={
                "composite": Axis(
                    values=(1.0, 2.0), data_type="tuple", coordinates=("x",)
                )
            },
            domain_type="Trajectory",
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0),
                shape=(2,),
                axis_names=("composite",),
            )
        },
    )

    with pytest.raises(ValueError, match="composite axis 'composite' needs 1-tuple"):
        to_xarray(cov)


def test_roundtrip_grid() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 20.0, 3)),
        ranges={
            "v": NdArray(
                data_type="float",
                values=tuple(float(i) for i in range(12)),
                shape=(3, 4),
                axis_names=("y", "x"),
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    assert _dom(back).domain_type == "Grid"
    assert _dom(back).axes["x"].coordinate_values == (0.0, 10.0, 20.0, 30.0)
    assert _dom(back).axes["y"].coordinate_values == (0.0, 10.0, 20.0)
    assert _nd(back, "v").axis_names == ("y", "x")
    assert _nd(back, "v").values == _nd(cov, "v").values


def test_roundtrip_point_scalar_axes() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((5.0,)), y=Axis.listed((6.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )
    back = from_xarray(to_xarray(cov))

    assert _dom(back).domain_type == "Point"
    assert _dom(back).axes["x"].coordinate_values == (5.0,)
    assert _dom(back).axes["y"].coordinate_values == (6.0,)
    assert _nd(back, "v").values == (280.0,)


def test_roundtrip_pointseries_time() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    assert _dom(back).domain_type == "PointSeries"
    assert _dom(back).axes["t"].coordinate_values == (
        "2020-01-01T00:00:00Z",
        "2020-01-02T00:00:00Z",
    )


def test_roundtrip_trajectory_composite() -> None:
    composite = Axis(
        data_type="tuple",
        coordinates=("t", "x", "y"),
        values=(
            ("2020-01-01T00:00:00Z", 1.0, 10.0),
            ("2020-01-02T00:00:00Z", 2.0, 20.0),
        ),
    )
    cov = Coverage(
        domain=Domain.trajectory(composite),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(5.0, 6.0),
                shape=(2,),
                axis_names=("composite",),
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    assert _dom(back).domain_type == "Trajectory"
    axis = _dom(back).axes["composite"]
    assert axis.data_type == "tuple"
    assert axis.coordinates == ("t", "x", "y")
    assert axis.values == composite.values
    assert _nd(back, "v").axis_names == ("composite",)


def test_roundtrip_recovers_geographic_referencing() -> None:
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 10.0, 2),
            y=Axis.regular(0.0, 5.0, 2),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"),
                    system=ReferenceSystem.geographic(
                        id="http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                    ),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    (connection,) = [
        c
        for c in _dom(back).referencing
        if isinstance(c.system.refine(), GeographicCRS)
    ]
    system = connection.system.refine()
    assert isinstance(system, GeographicCRS)
    assert connection.coordinates == ("x", "y")
    assert system.id == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"


def test_roundtrip_recovers_projected_referencing() -> None:
    # A projected system has no CF projection params here; the bridge records its
    # id (and that it is projected) on the crs variable so it round-trips as a
    # ProjectedCRS rather than collapsing to a GeographicCRS.
    crs_id = "http://www.opengis.net/def/crs/EPSG/0/27700"
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 10.0, 2),
            y=Axis.regular(0.0, 5.0, 2),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=ReferenceSystem.projected(id=crs_id)
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )
    ds = to_xarray(cov)

    assert ds["crs"].attrs["reference_system_type"] == "ProjectedCRS"

    back = from_xarray(ds)

    (connection,) = [
        c for c in _dom(back).referencing if isinstance(c.system.refine(), ProjectedCRS)
    ]
    system = connection.system.refine()
    assert isinstance(system, ProjectedCRS)
    assert connection.coordinates == ("x", "y")
    assert system.id == crs_id


def test_roundtrip_continuous_parameter() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
        parameters={
            "t": Parameter.continuous(
                ObservedProperty(
                    label=i18n("Air temperature"),
                    id="http://vocab/standard_name/air_temperature",
                ),
                Unit(symbol="K"),
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    parameter = _params(back)["t"]
    assert parameter.unit is not None
    assert parameter.unit.symbol == "K"
    # CF carries no language tag, so the reconstructed label is undetermined.
    assert parameter.observed_property.label == {"und": "Air temperature"}


def test_roundtrip_categorical_parameter() -> None:
    land_cover = ObservedProperty(
        label=i18n("Land cover"),
        categories=(
            Category(id="1", label=i18n("Open water")),
            Category(id="2", label=i18n("Forest")),
        ),
    )
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"lc": NdArray(data_type="integer", values=(2,))},
        parameters={"lc": Parameter.categorical(land_cover, {"1": 1, "2": 2})},
    )
    back = from_xarray(to_xarray(cov))

    parameter = _params(back)["lc"]
    assert parameter.category_encoding == {"1": 1, "2": 2}
    assert parameter.observed_property.categories is not None
    labels = {c.id: c.label["und"] for c in parameter.observed_property.categories}
    assert labels == {"1": "Open water", "2": "Forest"}


def test_compact_regular_disabled_keeps_listed_values() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.regular(0.0, 30.0, 4), y=Axis.listed((0.0,))),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(4,),
                axis_names=("x",),
            )
        },
    )
    back = from_xarray(to_xarray(cov), compact_regular=False)

    # Values kept explicitly rather than compacted to start/stop/num.
    axis = _dom(back).axes["x"]
    assert axis.values == (0.0, 10.0, 20.0, 30.0)
    assert axis.start is None


def test_from_external_dataset_detects_lon_lat() -> None:
    ds = xr.Dataset(
        {"temp": (("lat", "lon"), np.arange(6.0).reshape(3, 2), {"units": "K"})},
        coords={"lon": [0.0, 10.0], "lat": [0.0, 5.0, 10.0]},
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "Grid"
    # lon/lat are mapped onto the canonical x/y axis keys.
    assert _nd(cov, "temp").axis_names == ("y", "x")
    assert _dom(cov).axes["x"].coordinate_values == (0.0, 10.0)
    unit = _params(cov)["temp"].unit
    assert unit is not None
    assert unit.symbol == "K"


def test_override_seams_pin_roles_and_domain_type() -> None:
    ds = xr.Dataset(
        {"v": (("a", "b"), np.arange(6.0).reshape(3, 2))},
        coords={"b": [0.0, 1.0], "a": [0.0, 1.0, 2.0]},
    )
    cov = from_xarray(ds, x="b", y="a", domain_type="Grid")

    assert _dom(cov).domain_type == "Grid"
    assert _nd(cov, "v").axis_names == ("y", "x")


def test_coverage_from_xarray_method() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(9.0,))},
    )
    back = Coverage.from_xarray(cov.to_xarray())

    assert _nd(back, "v").values == (9.0,)


def test_roundtrip_recovers_coverage_id() -> None:
    cov = Coverage(
        id="urn:cov:42",
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(9.0,))},
    )
    back = from_xarray(to_xarray(cov))

    assert back.id == "urn:cov:42"


def test_from_xarray_rejects_missing_time_coordinate() -> None:
    # A NaT in a time coordinate has no CoverageJSON axis representation (a
    # coordinate position cannot be null), so conversion fails loudly rather than
    # emitting a null axis value.
    ds = xr.Dataset(
        {"v": ("t", [1.0, 2.0])},
        coords={"t": np.array(["2020-01-01", "NaT"], dtype="datetime64[ns]")},
    )

    with pytest.raises(ValueError, match="missing value"):
        from_xarray(ds)


def test_from_external_pointseries_infers_domain_type() -> None:
    # Scalar lon/lat with a time dimension and no vertical: an external dataset with
    # no domainType attribute infers PointSeries.
    ds = xr.Dataset(
        {"v": ("time", [1.0, 2.0])},
        coords={
            "lon": 1.0,
            "lat": 2.0,
            "time": np.array(["2020-01-01", "2020-01-02"], dtype="datetime64[ns]"),
        },
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "PointSeries"


def test_from_external_vertical_profile_infers_type_and_recovers_z() -> None:
    # Scalar lon/lat with a depth dimension and no time infers VerticalProfile; the
    # depth coordinate is recognized as the z role and recovered as the z axis.
    ds = xr.Dataset(
        {"v": ("depth", [1.0, 2.0])},
        coords={
            "lon": 1.0,
            "lat": 2.0,
            "depth": ("depth", [10.0, 20.0], {"positive": "down"}),
        },
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "VerticalProfile"
    assert _dom(cov).axes["z"].coordinate_values == (10.0, 20.0)


def test_from_external_trajectory_infers_domain_type() -> None:
    # lon, lat and time all varying along one shared dimension is a trajectory: the
    # three fold into one composite (tuple) axis and the domain type infers
    # Trajectory.
    ds = xr.Dataset(
        {"v": ("obs", [1.0, 2.0, 3.0])},
        coords={
            "lon": ("obs", [1.0, 2.0, 3.0]),
            "lat": ("obs", [10.0, 20.0, 30.0]),
            "time": (
                "obs",
                np.array(
                    ["2020-01-01", "2020-01-02", "2020-01-03"], dtype="datetime64[ns]"
                ),
            ),
        },
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "Trajectory"
    assert _dom(cov).axes["composite"].data_type == "tuple"


def test_from_external_x_y_with_both_z_and_t_dims_has_no_domain_type() -> None:
    # Scalar lon/lat but both a depth and a time dimension matches none of the common
    # domain types, so the domain type is left undetermined.
    ds = xr.Dataset(
        {"v": (("depth", "time"), [[1.0, 2.0], [3.0, 4.0]])},
        coords={
            "lon": 1.0,
            "lat": 2.0,
            "depth": ("depth", [10.0, 20.0]),
            "time": (
                "time",
                np.array(["2020-01-01", "2020-01-02"], dtype="datetime64[ns]"),
            ),
        },
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type is None


def test_from_external_non_dimension_horizontal_coords_build_a_grid() -> None:
    # lon/lat here are 1-D auxiliary coordinates (named "lon"/"lat" but varying along
    # dims "x"/"y"). This rectilinear grid converts losslessly to a Grid whose x/y
    # axes carry the coordinate values, each keyed by the dimension it varies along.
    ds = xr.Dataset(
        {"v": (("y", "x"), np.arange(6.0).reshape(3, 2), {"units": "K"})},
        coords={"lon": ("x", [0.0, 10.0]), "lat": ("y", [0.0, 5.0, 10.0])},
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "Grid"
    assert _dom(cov).axes["x"].coordinate_values == (0.0, 10.0)
    assert _dom(cov).axes["y"].coordinate_values == (0.0, 5.0, 10.0)
    assert validate(cov) == []


def test_from_external_aux_coords_keyed_by_differently_named_dimension() -> None:
    # The dimension-keying reframe in its own case: auxiliary lon/lat on dimensions
    # NOT named x/y (here i/j). The Grid's x/y axes carry the coordinate values, and
    # the range's (j, i) dims resolve through the DIMENSION to the role axes, a
    # distinction invisible when the dimension is named after the role.
    ds = xr.Dataset(
        {"v": (("j", "i"), np.arange(6.0).reshape(3, 2), {"units": "K"})},
        coords={"lon": ("i", [10.0, 20.0]), "lat": ("j", [0.0, 5.0, 10.0])},
    )
    cov = from_xarray(ds)

    assert _dom(cov).domain_type == "Grid"
    assert _dom(cov).axes["x"].coordinate_values == (10.0, 20.0)
    assert _dom(cov).axes["y"].coordinate_values == (0.0, 5.0, 10.0)
    assert _nd(cov, "v").axis_names == ("y", "x")
    assert validate(cov) == []


def test_from_external_leftover_dimension_with_coordinate_becomes_axis() -> None:
    # A dimension outside the x/y/z/t roles (here "band") becomes an axis under its
    # own name, taking its coordinate values when it has a coordinate variable.
    ds = xr.Dataset(
        {"v": (("lat", "lon", "band"), np.arange(8.0).reshape(2, 2, 2))},
        coords={
            "lon": ("lon", [0.0, 10.0]),
            "lat": ("lat", [0.0, 5.0]),
            "band": ("band", [11, 12]),
        },
    )
    cov = from_xarray(ds)

    assert _dom(cov).axes["band"].coordinate_values == (11, 12)


def test_from_external_leftover_dimension_without_coordinate_uses_index() -> None:
    # A dimension a range uses but that has no coordinate variable (here "band")
    # becomes an integer-index axis, so the range data is not orphaned.
    ds = xr.Dataset(
        {"v": (("lat", "lon", "band"), np.arange(8.0).reshape(2, 2, 2))},
        coords={"lon": ("lon", [0.0, 10.0]), "lat": ("lat", [0.0, 5.0])},
    )
    cov = from_xarray(ds)

    assert _dom(cov).axes["band"].coordinate_values == (0, 1)


def test_from_external_bounds_variable_is_not_a_range() -> None:
    # A CF bounds variable (name ending in _bnds / _bounds) describes cell edges, not
    # measured data, so it is skipped rather than turned into a coverage range, and
    # its vertex dimension ("nv") is not promoted to a domain axis.
    ds = xr.Dataset(
        {
            "v": (("lat", "lon"), np.arange(6.0).reshape(3, 2), {"units": "K"}),
            "lat_bnds": (("lat", "nv"), np.zeros((3, 2))),
        },
        coords={"lon": ("lon", [0.0, 10.0]), "lat": ("lat", [0.0, 5.0, 10.0])},
    )
    cov = from_xarray(ds)

    assert "v" in cov.ranges
    assert "lat_bnds" not in cov.ranges
    assert "nv" not in _dom(cov).axes
    assert validate(cov) == []


def test_from_external_bounds_declared_by_attribute_is_dropped() -> None:
    # A bounds variable named without a _bnds / _bounds suffix is still recognized via
    # the coordinate's CF `bounds` attribute: skipped as a range, and its vertex
    # dimension is not promoted to a domain axis.
    ds = xr.Dataset(
        {
            "v": (("lat", "lon"), np.arange(6.0).reshape(3, 2), {"units": "K"}),
            "lat_edges": (("lat", "nv"), np.zeros((3, 2))),
        },
        coords={
            "lon": ("lon", [0.0, 10.0]),
            "lat": ("lat", [0.0, 5.0, 10.0], {"bounds": "lat_edges"}),
        },
    )
    cov = from_xarray(ds)

    assert "lat_edges" not in cov.ranges
    assert "nv" not in _dom(cov).axes
    assert validate(cov) == []


def test_from_external_curvilinear_grid_raises() -> None:
    # 2-D latitude/longitude is a curvilinear (non-separable) grid; CoverageJSON axes
    # are 1-D, so there is no axis form for it. from_xarray rejects rather than
    # silently emitting a wrong Point coverage that drops the geographic data.
    lon2d = [[10.0, 11.0], [10.5, 11.5], [11.0, 12.0]]
    lat2d = [[50.0, 50.2], [51.0, 51.2], [52.0, 52.2]]
    ds = xr.Dataset(
        {"v": (("y", "x"), np.arange(6.0).reshape(3, 2))},
        coords={"lon": (("y", "x"), lon2d), "lat": (("y", "x"), lat2d)},
    )

    with pytest.raises(ValueError, match="curvilinear grid"):
        from_xarray(ds)


def test_from_external_two_roles_on_one_dimension_raises() -> None:
    # Two role coordinates on one dimension (longitude x(x) and depth(x)) cannot be
    # a single 1-D axis, so from_xarray rejects rather than silently binding the
    # range to whichever role is detected last.
    ds = xr.Dataset(
        {"v": ("x", np.arange(3.0))},
        coords={"x": ("x", [10.0, 20.0, 30.0]), "depth": ("x", [1.0, 2.0, 3.0])},
    )

    with pytest.raises(ValueError, match="hosts two role coordinates"):
        from_xarray(ds)


def test_from_external_dimension_named_like_a_role_axis_raises() -> None:
    # An auxiliary lon keys its axis "x", and a plain dataset dimension literally
    # named "x" would key an axis "x" too. Rather than silently overwriting the
    # longitude axis (losing its values), from_xarray rejects the collision.
    ds = xr.Dataset(
        {"v": (("west_east", "x"), np.arange(6.0).reshape(3, 2))},
        coords={"lon": ("west_east", [10.0, 20.0, 30.0])},
    )

    with pytest.raises(ValueError, match="axis key"):
        from_xarray(ds)


def test_non_standard_calendar_survives_from_xarray_roundtrip() -> None:
    # to_xarray renders a 360_day axis as cftime datetimes; from_xarray reads them
    # back via cftime's isoformat and recovers the calendar from the values.
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01", "2020-01-30")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="360_day"),
                ),
            ),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    back = from_xarray(to_xarray(cov))

    values = _dom(back).axes["t"].coordinate_values
    assert len(values) == 2
    assert isinstance(values[0], str)
    assert values[0].startswith("2020-01-01")


def test_collection_to_datatree_one_node_per_coverage() -> None:
    tree = to_datatree(_collection())

    assert list(tree.children) == ["coverage_0", "coverage_1"]
    assert tree["coverage_0"]["t"].item() == 280.0
    assert tree["coverage_1"]["t"].item() == 281.0
    # Inherited parameters land on each node as CF attributes.
    assert tree["coverage_0"]["t"].attrs["units"] == "K"


def test_collection_datatree_roundtrip() -> None:
    back = from_datatree(to_datatree(_collection()))

    assert len(back.coverages) == 2
    assert [c.id for c in back.coverages] == ["point-a", "point-b"]
    assert _nd(back.coverages[0], "t").values == (280.0,)
    # The flat result carries parameters per member rather than hoisting them.
    params = _params(back.coverages[0])
    assert params["t"].unit is not None
    assert params["t"].unit.symbol == "K"


def test_collection_method_delegates() -> None:
    collection = _collection()
    tree = collection.to_datatree()
    back = CoverageCollection.from_datatree(tree)

    assert len(back.coverages) == 2
    assert _nd(back.coverages[1], "t").values == (281.0,)


def test_from_datatree_single_node_root_data() -> None:
    ds = xr.Dataset(
        {"temp": (("lat", "lon"), np.arange(6.0).reshape(3, 2), {"units": "K"})},
        coords={"lon": [0.0, 10.0], "lat": [0.0, 5.0, 10.0]},
    )
    collection = from_datatree(xr.DataTree(dataset=ds))

    assert len(collection.coverages) == 1
    assert _dom(collection.coverages[0]).domain_type == "Grid"


def _dom(coverage: Coverage) -> Domain:
    domain = coverage.domain
    assert isinstance(domain, Domain)
    return domain


def _nd(coverage: Coverage, key: str) -> NdArray:
    array = coverage.ranges[key]
    assert isinstance(array, NdArray)
    return array


def _params(coverage: Coverage) -> Mapping[str, Parameter]:
    assert coverage.parameters is not UNSET
    return coverage.parameters


def _temperature() -> Parameter:
    return Parameter.continuous(
        ObservedProperty(
            label=i18n("Air temperature"),
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature",
        ),
        Unit(symbol="K"),
    )


def _collection() -> CoverageCollection:
    members = (
        Coverage(
            id="point-a",
            domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
            ranges={"t": NdArray(data_type="float", values=(280.0,))},
        ),
        Coverage(
            id="point-b",
            domain=Domain.point(x=Axis.listed((3.0,)), y=Axis.listed((4.0,))),
            ranges={"t": NdArray(data_type="float", values=(281.0,))},
        ),
    )

    return CoverageCollection(
        coverages=members,
        domain_type="Point",
        parameters={"t": _temperature()},
    )
