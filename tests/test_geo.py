"""Behavioral tests for the geo bridge (to_geopandas / to_geojson)."""

from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely import LineString, Point, Polygon

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    NdArray,
    ReferenceSystem,
    ReferenceSystemConnection,
    TiledNdArray,
    TileSet,
    to_geojson,
    to_geopandas,
    validate,
)


def test_point_is_single_point_feature() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )
    gdf = to_geopandas(cov)

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 1
    point = _point(gdf.geometry.iloc[0])
    assert (point.x, point.y) == (1.0, 2.0)
    assert gdf["v"].tolist() == [280.0]


def test_attrs_omit_domain_type_when_absent() -> None:
    # With no domainType to route on, to_geopandas falls back to point geometry,
    # and the absent type is left off the frame's attrs rather than stored as None.
    cov = Coverage(
        domain=Domain(axes={"x": Axis.listed((1.0,)), "y": Axis.listed((2.0,))}),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )
    gdf = to_geopandas(cov)

    assert "domain_type" not in gdf.attrs


def test_coverage_methods_delegate() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )

    assert _point(cov.to_geopandas().geometry.iloc[0]).x == 1.0
    assert cov.to_geojson()["type"] == "FeatureCollection"


def test_point_series_is_one_feature_per_time() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=ReferenceSystem.geographic(id="crs")
                ),
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
    gdf = to_geopandas(cov)

    assert len(gdf) == 2
    # "crs" is not a resolvable id, so the geographic system falls back to the
    # WGS84 lon/lat default (OGC:CRS84); the temporal one parses t.
    assert gdf.crs == "OGC:CRS84"
    assert gdf["t"].tolist() == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-02"),
    ]
    assert {(p.x, p.y) for p in map(_point, gdf.geometry)} == {(1.0, 2.0)}


def test_no_geographic_referencing_leaves_crs_unset() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={},
    )

    assert to_geopandas(cov).crs is None


def test_geographic_referencing_without_id_defaults_to_crs84() -> None:
    # No id: fall back to CoverageJSON's WGS84 lon/lat default (OGC:CRS84), whose
    # axis order matches the bridge's x / y geometry.
    crs = to_geopandas(_geographic_point(None)).crs

    assert crs == "OGC:CRS84"
    assert crs.to_authority() == ("OGC", "CRS84")


def test_geographic_referencing_with_unresolvable_id_falls_back() -> None:
    # A nominal / relative id that pyproj cannot resolve must not crash; it falls
    # back to the lon/lat default rather than being passed through.
    assert to_geopandas(_geographic_point("crs")).crs == "OGC:CRS84"


def test_geographic_referencing_passes_a_resolvable_id_through() -> None:
    # A resolvable geographic id is honored (mirroring the ProjectedCRS branch)
    # rather than flattened to the default: this EPSG geographic CRS resolves to
    # 4326 instead of collapsing to CRS84.
    cov = _geographic_point("http://www.opengis.net/def/crs/EPSG/0/4326")

    crs = to_geopandas(cov).crs
    assert crs is not None
    assert crs.to_epsg() == 4326


def test_projected_referencing_passes_its_id_through() -> None:
    # A projected system is identified by its id (here an OGC CRS URI); the
    # bridge passes it through and pyproj resolves it to the EPSG code.
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((400000.0,)),
            y=Axis.listed((100000.0,)),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"),
                    system=ReferenceSystem.projected(
                        id="http://www.opengis.net/def/crs/EPSG/0/27700"
                    ),
                ),
            ),
        ),
        ranges={},
    )

    crs = to_geopandas(cov).crs
    assert crs is not None
    assert crs.to_epsg() == 27700


def test_projected_referencing_without_id_leaves_crs_unset() -> None:
    cov = Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=ReferenceSystem.projected()
                ),
            ),
        ),
        ranges={},
    )

    assert to_geopandas(cov).crs is None


def test_trajectory_is_one_point_per_vertex() -> None:
    composite = Axis(
        data_type="tuple",
        coordinates=("t", "x", "y"),
        values=(
            ("2020-01-01", 1.0, 10.0),
            ("2020-01-02", 2.0, 20.0),
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
    gdf = to_geopandas(cov)

    assert [g.geom_type for g in gdf.geometry] == ["Point", "Point"]
    assert [(p.x, p.y) for p in map(_point, gdf.geometry)] == [(1.0, 10.0), (2.0, 20.0)]
    assert gdf["v"].tolist() == [5.0, 6.0]


def test_multipoint_is_one_point_per_member() -> None:
    # MultiPoint carries its positions in a composite (x, y) tuple axis; each
    # tuple becomes one point feature with its measurement.
    composite = Axis(
        data_type="tuple",
        coordinates=("x", "y"),
        values=((1.0, 10.0), (2.0, 20.0), (3.0, 30.0)),
    )
    cov = Coverage(
        domain=Domain(axes={"composite": composite}, domain_type="MultiPoint"),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(5.0, 6.0, 7.0),
                shape=(3,),
                axis_names=("composite",),
            )
        },
    )
    gdf = to_geopandas(cov)

    assert [g.geom_type for g in gdf.geometry] == ["Point", "Point", "Point"]
    assert [(p.x, p.y) for p in map(_point, gdf.geometry)] == [
        (1.0, 10.0),
        (2.0, 20.0),
        (3.0, 30.0),
    ]
    assert gdf["v"].tolist() == [5.0, 6.0, 7.0]
    # The composite axis is the geometry's source; its bare positional index
    # (0, 1, 2) must not leak into the feature columns / GeoJSON properties.
    assert "composite" not in gdf.columns
    assert "composite" not in to_geojson(cov)["features"][0]["properties"]


def test_grid_is_one_point_per_cell() -> None:
    cov = Coverage(
        domain=Domain.grid(x=Axis.listed((0.0, 1.0)), y=Axis.listed((10.0, 20.0))),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0, 3.0, 4.0),
                shape=(2, 2),
                axis_names=("y", "x"),
            )
        },
    )

    with pytest.warns(UserWarning, match="one point feature per"):
        gdf = to_geopandas(cov)

    # One feature per cell of the 2x2 grid, range values aligned by (y, x).
    assert len(gdf) == 4
    assert {(p.x, p.y) for p in map(_point, gdf.geometry)} == {
        (0.0, 10.0),
        (1.0, 10.0),
        (0.0, 20.0),
        (1.0, 20.0),
    }
    cells = zip(zip(gdf["x"], gdf["y"], strict=True), gdf["v"], strict=True)
    assert dict(cells) == {
        (0.0, 10.0): 1.0,
        (1.0, 10.0): 2.0,
        (0.0, 20.0): 3.0,
        (1.0, 20.0): 4.0,
    }


def test_polygon_is_single_polygon_feature() -> None:
    cov = Coverage(
        domain=Domain.polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)]),
        ranges={
            "v": NdArray(
                data_type="float", values=(9.0,), shape=(1,), axis_names=("composite",)
            )
        },
    )
    gdf = to_geopandas(cov)

    assert len(gdf) == 1
    polygon = _polygon(gdf.geometry.iloc[0])
    assert polygon.geom_type == "Polygon"
    assert list(polygon.exterior.coords) == [
        (0.0, 0.0),
        (2.0, 0.0),
        (2.0, 2.0),
        (0.0, 0.0),
    ]
    assert gdf["v"].tolist() == [9.0]


def test_polygon_carries_z_into_a_column() -> None:
    # A Polygon domain may carry a single-valued z axis; the polygon frame builder
    # broadcasts it into a z column alongside the geometry.
    cov = Coverage(
        domain=Domain.polygon(
            [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)],
            z=Axis.listed((15.0,)),
        ),
        ranges={},
    )
    gdf = to_geopandas(cov)

    assert gdf["z"].tolist() == [15.0]


def test_polygon_keeps_holes() -> None:
    exterior = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    hole = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 1.0)]
    cov = Coverage(
        domain=Domain.polygon(exterior, holes=[hole]),
        ranges={},
    )
    polygon = _polygon(to_geopandas(cov).geometry.iloc[0])

    assert len(polygon.interiors) == 1


def test_multipolygon_is_one_feature_per_polygon() -> None:
    square = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]
    triangle = [[(2.0, 2.0), (3.0, 2.0), (2.5, 3.0), (2.0, 2.0)]]
    cov = Coverage(
        domain=Domain.multipolygon([square, triangle]),
        ranges={
            "v": NdArray(
                data_type="float",
                values=(1.0, 2.0),
                shape=(2,),
                axis_names=("composite",),
            )
        },
    )
    gdf = to_geopandas(cov)

    assert [g.geom_type for g in gdf.geometry] == ["Polygon", "Polygon"]
    assert gdf["v"].tolist() == [1.0, 2.0]


def test_wrong_arity_polygon_is_reported_before_the_bridge_indexerrors() -> None:
    # Coordinates ["x", "y"] but one-component positions: the bridge reads
    # position[y_index=1] and raises IndexError. validate() reports the arity fault
    # first, so a caller who validates is warned before hitting that raw crash.
    ring = ((0.0,), (1.0,), (2.0,), (0.0,))  # four closed one-component positions
    axis = Axis(values=((ring,),), data_type="polygon", coordinates=("x", "y"))
    cov = Coverage(
        domain=Domain(
            axes={"composite": axis},
            domain_type="Polygon",
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=ReferenceSystem.geographic()
                ),
            ),
        ),
        ranges={},
    )

    codes = {issue.code for issue in validate(cov, check_values=True)}
    assert "axis.polygon-position-arity" in codes

    with pytest.raises(IndexError):
        to_geopandas(cov)


def test_polygon_series_repeats_geometry_over_time() -> None:
    base = Domain.polygon(
        [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)], t=Axis.listed(("a", "b"))
    )
    domain = Domain(axes=dict(base.axes), domain_type="PolygonSeries")
    cov = Coverage(
        domain=domain,
        ranges={
            "v": NdArray(
                data_type="float", values=(7.0, 8.0), shape=(2,), axis_names=("t",)
            )
        },
    )
    gdf = to_geopandas(cov)

    assert len(gdf) == 2
    assert gdf["t"].tolist() == ["a", "b"]
    assert gdf["v"].tolist() == [7.0, 8.0]
    # The one polygon is repeated for each time step.
    assert len({g.wkt for g in gdf.geometry}) == 1


def test_to_geojson_is_a_feature_collection() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )
    gj = to_geojson(cov)

    assert gj["type"] == "FeatureCollection"
    feature = gj["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [1.0, 2.0]}
    assert feature["properties"]["v"] == 280.0


def test_url_domain_is_rejected() -> None:
    cov = Coverage(domain="http://example/domain.json", ranges={})

    with pytest.raises(ValueError, match="URL reference"):
        to_geopandas(cov)


def test_missing_xy_is_rejected() -> None:
    cov = Coverage(
        domain=Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Point"),
        ranges={},
    )

    with pytest.raises(ValueError, match="x and y"):
        to_geopandas(cov)


def test_non_ndarray_polygon_range_is_rejected() -> None:
    cov = Coverage(
        domain=Domain.multipolygon(
            [[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]]
        ),
        ranges={
            "v": TiledNdArray(
                data_type="float",
                axis_names=("composite",),
                shape=(1,),
                tile_sets=(TileSet(tile_shape=(1,), url_template="http://ex/{i}"),),
            )
        },
    )

    with pytest.raises(ValueError, match="inline NdArray"):
        to_geopandas(cov)


def test_collection_concatenates_members_with_coverage_column() -> None:
    collection = CoverageCollection(
        coverages=(
            _point_member("a", 1.0, 2.0, 10.0),
            _point_member("b", 3.0, 4.0, 20.0),
        ),
        domain_type="Point",
    )
    gdf = to_geopandas(collection)

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 2
    assert next(iter(gdf.columns)) == "coverage"
    assert gdf["coverage"].tolist() == ["a", "b"]
    assert gdf["v"].tolist() == [10.0, 20.0]
    assert [(p.x, p.y) for p in map(_point, gdf.geometry)] == [(1.0, 2.0), (3.0, 4.0)]
    assert gdf.attrs["domain_type"] == "Point"


def test_collection_methods_delegate() -> None:
    collection = CoverageCollection(coverages=(_point_member("a", 1.0, 2.0, 10.0),))

    assert collection.to_geopandas()["coverage"].tolist() == ["a"]
    assert collection.to_geojson()["type"] == "FeatureCollection"


def test_collection_keys_unidentified_members_by_position() -> None:
    collection = CoverageCollection(
        coverages=(
            _point_member(None, 1.0, 2.0, 10.0),
            _point_member(None, 3.0, 4.0, 20.0),
        ),
    )

    assert to_geopandas(collection)["coverage"].tolist() == [0, 1]


def test_collection_inherits_referencing_for_crs() -> None:
    # The CRS lives on the collection's referencing; members declare none.
    collection = CoverageCollection(
        coverages=(_point_member("a", 1.0, 2.0, 10.0),),
        referencing=(
            ReferenceSystemConnection(
                coordinates=("x", "y"), system=ReferenceSystem.geographic(id="crs")
            ),
        ),
    )

    assert to_geopandas(collection).crs == "OGC:CRS84"


def test_collection_features_carry_coverage_property() -> None:
    collection = CoverageCollection(
        coverages=(
            _point_member("a", 1.0, 2.0, 10.0),
            _point_member("b", 3.0, 4.0, 20.0),
        ),
    )
    gj = to_geojson(collection)

    assert gj["type"] == "FeatureCollection"
    assert [f["properties"]["coverage"] for f in gj["features"]] == ["a", "b"]


def test_empty_collection_is_empty_frame() -> None:
    assert len(to_geopandas(CoverageCollection(coverages=()))) == 0


def test_empty_collection_to_geojson_is_empty_feature_collection() -> None:
    # The empty frame has no geometry column, so to_json would raise; the bridge
    # emits an empty FeatureCollection instead.
    gj = to_geojson(CoverageCollection(coverages=()))

    assert gj == {"type": "FeatureCollection", "features": []}


def test_vertical_profile_carries_z_into_point_geometry() -> None:
    cov = Coverage(
        domain=Domain.vertical_profile(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            z=Axis.listed((10.0, 20.0)),
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(5.0, 6.0), shape=(2,), axis_names=("z",)
            )
        },
    )
    gdf = to_geopandas(cov)

    assert all(g.has_z for g in gdf.geometry)
    assert [(p.x, p.y, p.z) for p in map(_point, gdf.geometry)] == [
        (1.0, 2.0, 10.0),
        (1.0, 2.0, 20.0),
    ]
    # z is also kept as a column.
    assert gdf["z"].tolist() == [10.0, 20.0]


def test_point_without_z_stays_2d() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={},
    )

    assert not _point(to_geopandas(cov).geometry.iloc[0]).has_z


def test_to_geojson_emits_3d_coordinates() -> None:
    cov = Coverage(
        domain=Domain.vertical_profile(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            z=Axis.listed((10.0,)),
        ),
        ranges={},
    )
    gj = to_geojson(cov)

    assert gj["features"][0]["geometry"]["coordinates"] == [1.0, 2.0, 10.0]


def test_polygon_carries_z_into_geometry() -> None:
    exterior = [
        (0.0, 0.0, 5.0),
        (2.0, 0.0, 5.0),
        (2.0, 2.0, 5.0),
        (0.0, 0.0, 5.0),
    ]
    cov = Coverage(
        domain=Domain.polygon(exterior, coordinates=("x", "y", "z")),
        ranges={},
    )
    polygon = _polygon(to_geopandas(cov).geometry.iloc[0])

    assert polygon.has_z
    assert list(polygon.exterior.coords) == exterior


def test_trajectory_as_linestring_is_one_feature() -> None:
    cov = _trajectory(
        "t",
        "x",
        "y",
        values=(("2020-01-01", 1.0, 10.0), ("2020-01-02", 2.0, 20.0)),
    )
    gdf = to_geopandas(cov, trajectory_as="linestring")

    assert len(gdf) == 1
    line = _linestring(gdf.geometry.iloc[0])
    assert line.geom_type == "LineString"
    assert list(line.coords) == [(1.0, 10.0), (2.0, 20.0)]
    # The path is geometry only: per-vertex measurements are dropped.
    assert "v" not in gdf.columns


def test_trajectory_linestring_carries_z() -> None:
    cov = _trajectory(
        "t",
        "x",
        "y",
        "z",
        values=(("2020-01-01", 1.0, 10.0, 5.0), ("2020-01-02", 2.0, 20.0, 6.0)),
    )
    line = _linestring(to_geopandas(cov, trajectory_as="linestring").geometry.iloc[0])

    assert line.has_z
    assert list(line.coords) == [(1.0, 10.0, 5.0), (2.0, 20.0, 6.0)]


def test_trajectory_as_points_is_the_default() -> None:
    cov = _trajectory(
        "t",
        "x",
        "y",
        values=(("2020-01-01", 1.0, 10.0), ("2020-01-02", 2.0, 20.0)),
    )

    assert [g.geom_type for g in to_geopandas(cov).geometry] == ["Point", "Point"]


def test_invalid_trajectory_as_is_rejected() -> None:
    cov = _trajectory("t", "x", "y", values=(("2020-01-01", 1.0, 10.0),))

    with pytest.raises(ValueError, match="trajectory_as must be"):
        to_geopandas(cov, trajectory_as="line")  # type: ignore[arg-type]


def test_single_vertex_trajectory_linestring_is_rejected() -> None:
    cov = _trajectory("t", "x", "y", values=(("2020-01-01", 1.0, 10.0),))

    with pytest.raises(ValueError, match="at least two vertices"):
        to_geopandas(cov, trajectory_as="linestring")


@pytest.mark.parametrize(
    ("domain_type", "expected"), [("Trajectory", "tuple"), ("Polygon", "polygon")]
)
def test_primitive_composite_axis_is_rejected(domain_type: str, expected: str) -> None:
    # The geometry builders read `composite`'s values as positions or rings, so a
    # primitive axis wearing the name must be rejected by name rather than fail
    # from inside shapely. validate() reports the same document as
    # `domain.composite-data-type`, but the bridge does not require a validated
    # one, so the check is repeated here at the boundary.
    cov = Coverage(
        domain=Domain(
            axes={"composite": Axis.listed((1.0, 2.0))}, domain_type=domain_type
        ),
        ranges={},
    )

    with pytest.raises(ValueError, match=f"requires a {expected!r} composite axis"):
        to_geopandas(cov, trajectory_as="linestring")


@pytest.mark.parametrize("domain_type", ["Trajectory", "Polygon"])
def test_geometry_domain_without_a_composite_axis_is_rejected(domain_type: str) -> None:
    # The geometry builders read `domain.axes["composite"]`; a domain typed for
    # geometry but missing that axis must raise a clear bridge error rather than
    # a bare KeyError from inside the builder. validate() reports the same as
    # `domain.missing-axis`, but the bridge does not require a validated document.
    cov = Coverage(
        domain=Domain(axes={"x": Axis.listed((1.0, 2.0))}, domain_type=domain_type),
        ranges={},
    )

    with pytest.raises(ValueError, match="requires a 'composite' axis"):
        to_geopandas(cov, trajectory_as="linestring")


@pytest.mark.parametrize("domain_type", ["Trajectory", "Polygon"])
def test_composite_axis_without_horizontal_coordinates_is_rejected(
    domain_type: str,
) -> None:
    # The geometry builders understand only x / y among `composite`'s
    # coordinates; coordinates naming neither must be rejected by name rather
    # than fail from inside shapely. validate() reports the same, but the bridge
    # does not require a validated document.
    data_type = "tuple" if domain_type == "Trajectory" else "polygon"
    values = (
        ((1.0, 10.0), (2.0, 20.0))
        if domain_type == "Trajectory"
        else (((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)),)
    )
    cov = Coverage(
        domain=Domain(
            axes={
                "composite": Axis(
                    data_type=data_type, values=values, coordinates=("a", "b")
                )
            },
            domain_type=domain_type,
        ),
        ranges={},
    )

    with pytest.raises(ValueError, match="needs x and y coordinates"):
        to_geopandas(cov, trajectory_as="linestring")


def test_collection_of_trajectories_as_linestrings() -> None:
    a = _trajectory("x", "y", values=((1.0, 10.0), (2.0, 20.0)))
    b = _trajectory("x", "y", values=((3.0, 30.0), (4.0, 40.0)))
    collection = CoverageCollection(coverages=(a, b))
    gdf = to_geopandas(collection, trajectory_as="linestring")

    assert len(gdf) == 2
    assert [g.geom_type for g in gdf.geometry] == ["LineString", "LineString"]
    assert gdf["coverage"].tolist() == [0, 1]


def test_to_geojson_trajectory_as_linestring() -> None:
    cov = _trajectory("x", "y", values=((1.0, 10.0), (2.0, 20.0)))
    gj = to_geojson(cov, trajectory_as="linestring")

    assert gj["features"][0]["geometry"]["type"] == "LineString"


def _point(geom: Any) -> Point:
    # geopandas types a geometry as the abstract BaseGeometry; assert the concrete
    # type so the Point coordinate accessors (x / y / z) type-check (and to guard
    # the test's assumption at runtime).
    assert isinstance(geom, Point)
    return geom


def _polygon(geom: Any) -> Polygon:
    assert isinstance(geom, Polygon)
    return geom


def _linestring(geom: Any) -> LineString:
    assert isinstance(geom, LineString)
    return geom


def _geographic_point(crs_id: str | None) -> Coverage:
    return Coverage(
        domain=Domain.point(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=ReferenceSystem.geographic(id=crs_id)
                ),
            ),
        ),
        ranges={},
    )


def _point_member(id_: str | None, x: float, y: float, v: float) -> Coverage:
    return Coverage(
        id=id_,
        domain=Domain.point(x=Axis.listed((x,)), y=Axis.listed((y,))),
        ranges={"v": NdArray(data_type="float", values=(v,))},
    )


def _trajectory(*coordinates: str, values: tuple[tuple[object, ...], ...]) -> Coverage:
    composite = Axis(data_type="tuple", coordinates=coordinates, values=values)
    return Coverage(
        domain=Domain.trajectory(composite),
        ranges={
            "v": NdArray(
                data_type="float",
                values=tuple(float(i) for i in range(len(values))),
                shape=(len(values),),
                axis_names=("composite",),
            )
        },
    )
