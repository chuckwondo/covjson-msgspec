"""Behavioral tests for coverage domains."""

import msgspec

from covjson_msgspec import (
    Axis,
    Domain,
    GeographicCRS,
    ReferenceSystemConnection,
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
