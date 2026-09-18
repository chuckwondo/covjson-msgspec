"""Behavioral tests for the geo bridge (to_geopandas / to_geojson)."""

import contextlib
import pathlib
import warnings
from collections.abc import Callable
from typing import Any

import geopandas as gpd
import msgspec
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
    decode,
    to_geojson,
    to_geopandas,
    validate,
)
from samples import gregorian_series


def _corpus_coverages() -> tuple[tuple[str, Coverage | CoverageCollection], ...]:
    """The conformant corpus documents that decode to a coverage, by file name.

    The geo bridge takes a `Coverage` / `CoverageCollection`, so the bare Domain
    and NdArray documents (and the structural rejects test_corpus.py pins) drop
    out here. The ``negative/`` tree is excluded deliberately: what a bridge does
    with a deliberately malformed document is not a contract.

    Called at module load to parametrize the sweep, so it precedes its first use.
    Decoding here rather than in the test keeps the parametrized type narrow.
    """
    corpus = pathlib.Path(__file__).parent / "corpus"
    # The same globs test_corpus.py uses: playground nests (grid-tiled/a, ...),
    # the covjson-pydantic fixtures are flat.
    paths = (
        *(corpus / "playground").rglob("*.covjson"),
        *(corpus / "covjson-pydantic").glob("*.json"),
    )
    kept: list[tuple[str, Coverage | CoverageCollection]] = []

    for path in sorted(paths):
        with contextlib.suppress(msgspec.ValidationError):
            obj = decode(path.read_bytes())

            if isinstance(obj, Coverage | CoverageCollection):
                kept.append((path.name, obj))

    return tuple(kept)


_CORPUS_COVERAGES = _corpus_coverages()


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
    # WGS84 lon/lat default (OGC:CRS84). The temporal one parses t.
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
    # A nominal / relative id that pyproj cannot resolve must not crash. It falls
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
    # A projected system is identified by its id (here an OGC CRS URI). The
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
    # MultiPoint carries its positions in a composite (x, y) tuple axis. Each
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
    # The composite axis is the geometry's source. Its bare positional index
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
    # A Polygon domain may carry a single-valued z axis. The polygon frame builder
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

    codes = {issue.code for issue in validate(cov, check_values=True).issues}
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
    # The CRS lives on the collection's referencing. Members declare none.
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
    # The empty frame has no geometry column, so to_json would raise. The bridge
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

    missing_z = [i for i, g in enumerate(gdf.geometry) if not g.has_z]
    assert not missing_z, "every geometry must have a Z coordinate"
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


@pytest.mark.parametrize("trajectory_as", ["line", [], {"points": 1}])
def test_invalid_trajectory_as_is_rejected(trajectory_as: object) -> None:
    # The unhashable cases are the reason the guard compares against a tuple: a
    # frozenset would hash the argument first and raise TypeError instead of the
    # ValueError the docstring promises.
    cov = _trajectory("t", "x", "y", values=(("2020-01-01", 1.0, 10.0),))

    with pytest.raises(ValueError, match="trajectory_as must be"):
        to_geopandas(cov, trajectory_as=trajectory_as)  # type: ignore[arg-type]


@pytest.mark.parametrize("convert", [to_geopandas, to_geojson])
def test_an_invalid_call_is_rejected_before_the_grid_warning(
    convert: Callable[..., object],
) -> None:
    # A Grid domain warns, but only once the call is known to be viable: an
    # invalid argument must be reported as itself, not preceded by advice about a
    # conversion that is not going to happen. Promoting the warning to an error
    # is what pins the order, since both orderings otherwise pass.
    cov = Coverage(
        domain=Domain.grid(x=Axis.listed((0.0, 1.0)), y=Axis.listed((10.0, 20.0))),
        ranges={},
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)

        with pytest.raises(ValueError, match="trajectory_as must be"):
            convert(cov, trajectory_as="line")


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
    # The geometry builders read `domain.axes["composite"]`. A domain typed for
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
    # coordinates. Coordinates naming neither must be rejected by name rather
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


@pytest.mark.parametrize(
    "source",
    ["2013", "2013-06", "2013-06-15", "2013-06-15T11:12:20Z", "+102013"],
)
def test_to_geojson_keeps_every_spec_lexical_form(source: str) -> None:
    # The five Spec 5.2 Gregorian forms. Four of them parse to an instant, so
    # reconstructing a string from the parsed value would render "2013" and
    # "2013-01-01" identically. The fifth (an expanded year) is unrepresentable
    # and never parses at all. GeoJSON carries whichever form the document used.
    cov = gregorian_series((source, "2020-01-01T00:00:00Z"))
    gj = to_geojson(cov)

    assert [f["properties"]["t"] for f in gj["features"]] == [
        source,
        "2020-01-01T00:00:00Z",
    ]


def test_to_geopandas_still_parses_temporal_coordinates() -> None:
    # to_geojson asks for raw strings. The typed frame must not follow it there.
    # Without this, the GeoJSON change could silently flatten to_geopandas too.
    gdf = to_geopandas(gregorian_series(("2013", "2014")))

    assert gdf["t"].tolist() == [pd.Timestamp("2013-01-01"), pd.Timestamp("2014-01-01")]


def test_to_geopandas_times_raw_keeps_the_document_value() -> None:
    gdf = to_geopandas(gregorian_series(("2013", "2014")), times="raw")

    assert gdf["t"].tolist() == ["2013", "2014"]


def test_to_geopandas_times_raw_makes_the_frame_json_serializable() -> None:
    # The reason times= belongs on this bridge and not only on to_geojson:
    # gdf.to_json() is the geopandas idiom, and the default parsed frame hits the
    # same stdlib json gap (no encoder for a Timestamp) that #235 repaired.
    cov = gregorian_series(("2013", "2014"))

    # GeoDataFrame.to_json's stub leaves **kwargs unknown, as the bridge modules
    # note when they relax the same rule at module scope.
    with pytest.raises(TypeError, match="Timestamp is not JSON serializable"):
        to_geopandas(cov).to_json()  # pyright: ignore[reportUnknownMemberType]

    raw = to_geopandas(cov, times="raw").to_json()  # pyright: ignore[reportUnknownMemberType]

    assert "2013" in raw


def test_to_geopandas_times_reaches_a_collection_member() -> None:
    collection = CoverageCollection(
        coverages=(gregorian_series(("2013",)), gregorian_series(("2014",)))
    )

    assert to_geopandas(collection, times="raw")["t"].tolist() == ["2013", "2014"]


@pytest.mark.parametrize("times", ["iso", [], {"raw": 1}])
def test_to_geopandas_rejects_an_unknown_times_on_a_polygon_domain(
    times: object,
) -> None:
    # The point path would be caught downstream by to_pandas, but the polygon
    # builder only ever compares times == "datetime", so an unrecognized value
    # would silently behave like "raw". This is the case the shared guard exists
    # for, so it is the case that pins it.
    cov = Coverage(
        domain=Domain.polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)]),
        ranges={},
    )

    with pytest.raises(ValueError, match="times must be 'datetime' or 'raw'"):
        to_geopandas(cov, times=times)  # type: ignore[arg-type]


def test_to_geojson_keeps_a_polygon_series_time_raw() -> None:
    # The polygon builder reaches maybe_datetime on its own line, not through
    # to_pandas, so it needs its own coverage of the raw path.
    base = Domain.polygon(
        [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)],
        t=Axis.listed(("2013", "2014")),
        referencing=(
            ReferenceSystemConnection(
                coordinates=("t",),
                system=ReferenceSystem.temporal(calendar="Gregorian"),
            ),
        ),
    )
    cov = Coverage(
        domain=Domain(
            axes=dict(base.axes),
            referencing=base.referencing,
            domain_type="PolygonSeries",
        ),
        ranges={
            "v": NdArray(
                data_type="float", values=(7.0, 8.0), shape=(2,), axis_names=("t",)
            )
        },
    )

    assert [f["properties"]["t"] for f in to_geojson(cov)["features"]] == [
        "2013",
        "2014",
    ]
    assert to_geopandas(cov)["t"].tolist() == [
        pd.Timestamp("2013-01-01"),
        pd.Timestamp("2014-01-01"),
    ]


def test_geo_corpus_is_present() -> None:
    # Guards against a silently empty parametrization, as test_corpus.py does for
    # its own globs. An exact count would have to be hand-maintained here and in
    # test_corpus.py's two counts every time a document is vendored in.
    assert _CORPUS_COVERAGES


@pytest.mark.parametrize(
    "obj",
    [obj for _, obj in _CORPUS_COVERAGES],
    ids=[name for name, _ in _CORPUS_COVERAGES],
)
def test_corpus_coverage_emits_serializable_geojson(
    obj: Coverage | CoverageCollection,
) -> None:
    # The gap that let the Timestamp TypeError ship: the bridges had never seen a
    # corpus document. Every real-world coverage must reach JSON or say why not.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # a Grid warns, not the point

        try:
            geojson = to_geojson(obj)
        except ValueError as exc:
            # Two documents reference their ranges by URL. Needing them inline is
            # documented behavior, so assert the reason rather than skipping:
            # a document that stops converting for any other reason fails here.
            assert "is not an inline NdArray" in str(exc)
            return

    # Reaching here is the serialization check: to_geojson serializes the frame
    # internally (json.loads of gdf.to_json), so the TypeError under repair raises
    # inside that call. Re-running json.dumps on the result would prove nothing,
    # since a value decoded from JSON is JSON-serializable by construction.
    assert geojson["type"] == "FeatureCollection"


@pytest.mark.parametrize("convert", [to_geopandas, to_geojson])
def test_geo_bridges_propagate_a_range_value_error(
    convert: Callable[[Coverage], object],
) -> None:
    # to_numpy projects rather than coerces, so a value that does not match its
    # dataType raises out through both bridges, as each documents. A point
    # domain keeps the Grid warning out of the way.
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=("1.5",))},
    )

    with pytest.raises(msgspec.ValidationError):
        convert(cov)


def _point(geom: Any) -> Point:
    # geopandas types a geometry as the abstract BaseGeometry. Assert the concrete
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
