"""Behavioral tests for coverage domains."""

import msgspec

from covjson_msgspec import (
    Axis,
    Domain,
    GeographicCRS,
    ReferenceSystemConnection,
    validate,
)


def test_grid_builder_and_accessors() -> None:
    dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.listed((0.0, 1.0)))
    assert dom.domain_type == "Grid"

    x = dom.x
    assert x is not None
    assert x.coordinate_values == (0.0, 5.0, 10.0)

    assert dom.z is None
    assert dom.t is None


def test_grid_roundtrips_with_referencing() -> None:
    dom = Domain.grid(
        x=Axis.regular(0.0, 10.0, 3),
        y=Axis.listed((0.0, 1.0)),
        referencing=[
            ReferenceSystemConnection(
                coordinates=("x", "y"), system=GeographicCRS(id="crs")
            )
        ],
    )
    back = msgspec.json.decode(msgspec.json.encode(dom), type=Domain)
    assert back == dom


def test_vertical_profile_includes_z() -> None:
    dom = Domain.vertical_profile(
        x=Axis.listed((1.0,)), y=Axis.listed((2.0,)), z=Axis.listed((10.0, 20.0))
    )
    assert dom.domain_type == "VerticalProfile"

    z = dom.z
    assert z is not None
    assert z.coordinate_values == (10.0, 20.0)


def test_trajectory_uses_composite_axis() -> None:
    composite = Axis(
        data_type="tuple",
        coordinates=("t", "x", "y"),
        values=(("2020-01-01T00:00:00Z", 1.0, 2.0),),
    )
    dom = Domain.trajectory(composite)
    assert dom.domain_type == "Trajectory"
    assert "composite" in dom.axes


def test_multipoint_builder_validates_clean() -> None:
    composite = Axis.tuple([(1.0, 2.0), (3.0, 4.0)], coordinates=("x", "y"))
    dom = Domain.multipoint(composite, t=Axis.listed(("2020-01-01",)))

    assert dom.domain_type == "MultiPoint"
    assert set(dom.axes) == {"composite", "t"}
    assert validate(dom) == []
    assert msgspec.json.decode(msgspec.json.encode(dom), type=Domain) == dom


def test_multipoint_series_builder_validates_clean() -> None:
    composite = Axis.tuple([(1.0, 2.0)], coordinates=("x", "y"))
    dom = Domain.multipoint_series(composite, Axis.listed(("2020-01-01", "2020-01-02")))

    assert dom.domain_type == "MultiPointSeries"
    assert set(dom.axes) == {"composite", "t"}
    assert validate(dom) == []


def test_section_builder_validates_clean() -> None:
    composite = Axis.tuple(
        [("2020-01-01T00:00:00Z", 1.0, 2.0)], coordinates=("t", "x", "y")
    )
    dom = Domain.section(composite, Axis.listed((10.0, 20.0)))

    assert dom.domain_type == "Section"
    assert set(dom.axes) == {"composite", "z"}
    assert validate(dom) == []


def test_polygon_builder_holds_one_polygon() -> None:
    exterior = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)]
    dom = Domain.polygon(exterior)

    assert dom.domain_type == "Polygon"
    composite = dom.axes["composite"]
    assert composite.data_type == "polygon"
    assert composite.coordinates == ("x", "y")
    # A single polygon: one value, one (exterior) ring.
    assert composite.values is not None
    assert len(composite.values) == 1
    polygon = composite.values[0]
    assert polygon == (((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)),)


def test_polygon_builder_keeps_holes_and_extra_axes() -> None:
    exterior = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    hole = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 1.0)]
    dom = Domain.polygon(exterior, holes=[hole], t=Axis.listed(("2020-01-01",)))

    composite = dom.axes["composite"]
    assert composite.values is not None
    polygon = composite.values[0]
    assert isinstance(polygon, tuple)
    # Exterior ring first, then the hole.
    assert len(polygon) == 2
    assert dom.t is not None


def test_multipolygon_builder_holds_many_polygons() -> None:
    square = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]
    triangle = [[(2.0, 2.0), (3.0, 2.0), (2.5, 3.0), (2.0, 2.0)]]
    dom = Domain.multipolygon([square, triangle])

    assert dom.domain_type == "MultiPolygon"
    composite = dom.axes["composite"]
    assert composite.values is not None
    assert len(composite.values) == 2


def test_polygon_domain_roundtrips_on_the_wire() -> None:
    # The nested polygon interior decodes as lists (it is typed Any), so a decoded
    # Domain is not object-equal to the constructed one; the wire form is stable.
    dom = Domain.polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)])
    blob = msgspec.json.encode(dom)
    back = msgspec.json.decode(blob, type=Domain)

    assert msgspec.json.encode(back) == blob


def test_domain_decodes_axes() -> None:
    blob = (
        b'{"type": "Domain", "domainType": "Point",'
        b' "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}}'
    )
    dom = msgspec.json.decode(blob, type=Domain)
    assert dom.domain_type == "Point"

    x = dom.x
    assert x is not None
    assert x.coordinate_values == (1.0,)
