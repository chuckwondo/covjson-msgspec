"""Behavioral tests for the xarray bridge (to_xarray / from_xarray)."""

import numpy as np
import pytest
import xarray as xr

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
    ReferenceSystemConnection,
    TemporalRS,
    TiledNdArray,
    TileSet,
    Unit,
    VerticalCRS,
    from_datatree,
    from_xarray,
    i18n,
    to_datatree,
    to_xarray,
)


def _dom(coverage: Coverage) -> Domain:
    domain = coverage.domain
    assert isinstance(domain, Domain)
    return domain


def _nd(coverage: Coverage, key: str) -> NdArray:
    array = coverage.ranges[key]
    assert isinstance(array, NdArray)
    return array


def _params(coverage: Coverage) -> dict[str, Parameter]:
    assert coverage.parameters is not None
    return coverage.parameters


def _temperature() -> Parameter:
    return Parameter.continuous(
        ObservedProperty(
            label=i18n("Air temperature"),
            id="http://vocab.nerc.ac.uk/standard_name/air_temperature",
        ),
        Unit(symbol="K"),
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
    ds = to_xarray(cov)

    assert ds["t"].dtype == np.dtype("datetime64[ns]")
    assert str(ds["t"].values[0]) == "2020-01-01T00:00:00.000000000"


def test_temporal_non_standard_calendar_uses_cftime() -> None:
    import cftime

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
    ds = to_xarray(cov)

    assert isinstance(ds["t"].values[0], cftime.datetime)
    assert ds["t"].values[0].calendar == "360_day"


def test_geographic_referencing_sets_cf_attrs_and_grid_mapping() -> None:
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, 10.0, 2),
            y=Axis.regular(0.0, 5.0, 2),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"),
                    system=GeographicCRS(
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


def test_vertical_depth_sets_positive_down() -> None:
    cov = Coverage(
        domain=Domain.vertical_profile(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            z=Axis.listed((10.0, 20.0)),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("z",),
                    system=VerticalCRS(id="http://example/crs/sea_water_depth"),
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
                    system=GeographicCRS(
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
        c for c in _dom(back).referencing if isinstance(c.system, GeographicCRS)
    ]
    system = connection.system
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
                    coordinates=("x", "y"), system=ProjectedCRS(id=crs_id)
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
        c for c in _dom(back).referencing if isinstance(c.system, ProjectedCRS)
    ]
    system = connection.system
    assert isinstance(system, ProjectedCRS)
    assert connection.coordinates == ("x", "y")
    assert system.id == crs_id


def test_to_xarray_rejects_a_collection() -> None:
    with pytest.raises(TypeError, match="use to_datatree"):
        to_xarray(CoverageCollection(coverages=()))  # type: ignore[arg-type]


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
    assert back.coverages[0].parameters is not None
    assert back.coverages[0].parameters["t"].unit is not None
    assert back.coverages[0].parameters["t"].unit.symbol == "K"


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
