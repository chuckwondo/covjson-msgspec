"""Behavioral tests for the geo bridge (to_geopandas / to_geojson)."""

import geopandas as gpd
import pandas as pd
import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    Domain,
    GeographicCRS,
    NdArray,
    ReferenceSystemConnection,
    TemporalRS,
    TiledNdArray,
    TileSet,
    to_geojson,
    to_geopandas,
)


def test_point_is_single_point_feature() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )
    gdf = to_geopandas(cov)

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 1
    point = gdf.geometry.iloc[0]
    assert (point.x, point.y) == (1.0, 2.0)
    assert gdf["v"].tolist() == [280.0]


def test_coverage_methods_delegate() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"v": NdArray(data_type="float", values=(280.0,))},
    )

    assert cov.to_geopandas().geometry.iloc[0].x == 1.0
    assert cov.to_geojson()["type"] == "FeatureCollection"


def test_point_series_is_one_feature_per_time() -> None:
    cov = Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z")),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=GeographicCRS(id="crs")
                ),
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
    gdf = to_geopandas(cov)

    assert len(gdf) == 2
    # The geographic reference system sets the CRS; the temporal one parses t.
    assert gdf.crs == "EPSG:4326"
    assert gdf["t"].tolist() == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-02"),
    ]
    assert {(g.x, g.y) for g in gdf.geometry} == {(1.0, 2.0)}


def test_no_geographic_referencing_leaves_crs_unset() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
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
    assert [(g.x, g.y) for g in gdf.geometry] == [(1.0, 10.0), (2.0, 20.0)]
    assert gdf["v"].tolist() == [5.0, 6.0]


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
    polygon = gdf.geometry.iloc[0]
    assert polygon.geom_type == "Polygon"
    assert list(polygon.exterior.coords) == [
        (0.0, 0.0),
        (2.0, 0.0),
        (2.0, 2.0),
        (0.0, 0.0),
    ]
    assert gdf["v"].tolist() == [9.0]


def test_polygon_keeps_holes() -> None:
    exterior = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    hole = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 1.0)]
    cov = Coverage(
        domain=Domain.polygon(exterior, holes=[hole]),
        ranges={},
    )
    polygon = to_geopandas(cov).geometry.iloc[0]

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
