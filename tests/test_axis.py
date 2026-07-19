"""Behavioral tests for domain axes."""

import msgspec
import pytest

from covjson_msgspec import Axis


def test_regular_axis_materializes() -> None:
    assert Axis.regular(0.0, 10.0, 5).coordinate_values == (0.0, 2.5, 5.0, 7.5, 10.0)


def test_listed_axis_roundtrips() -> None:
    ax = Axis.listed((10, 20, 30), bounds=(5, 15, 15, 25, 25, 35))
    back = msgspec.json.decode(msgspec.json.encode(ax), type=Axis)
    assert back == ax
    assert back.coordinate_values == (10, 20, 30)


def test_axis_rejects_both_forms() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Axis(values=(1, 2), start=0.0, stop=1.0, num=2)


def test_axis_rejects_neither_form() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Axis()


def test_axis_rejects_empty_values() -> None:
    # Spec 6.1.1: `values` is a non-empty array. With num >= 1 also enforced,
    # every axis has at least one coordinate, so len(axis) >= 1 and a valid
    # Axis never evaluates falsy.
    with pytest.raises(ValueError, match="non-empty"):
        Axis(values=())


def test_axis_rejects_empty_coordinates() -> None:
    # Spec 6.1.1: `coordinates`, when given, is a non-empty array. A composite
    # axis with no named components is uninterpretable, so it is rejected at
    # construction (ADR-0002), like `values`.
    with pytest.raises(ValueError, match="non-empty"):
        Axis(
            values=(("2020-01-01T00:00:00Z", 1.0),),
            data_type="tuple",
            coordinates=(),
        )


def test_axis_rejects_empty_coordinates_on_decode() -> None:
    # The same guard fires when the empty array arrives via decode.
    blob = (
        b'{"dataType": "tuple", "coordinates": [], '
        b'"values": [["2020-01-01T00:00:00Z", 1.0]]}'
    )
    with pytest.raises((msgspec.ValidationError, ValueError), match="non-empty"):
        msgspec.json.decode(blob, type=Axis)


def test_axis_len_never_materializes_and_is_never_zero() -> None:
    assert len(Axis.regular(0.0, 10.0, 1_000_000)) == 1_000_000
    assert len(Axis.listed((10, 20, 30))) == 3
    assert bool(Axis.listed((10,)))


def test_regular_num_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Axis(start=0.0, stop=1.0, num=0)


def test_regular_num_one_requires_equal_start_stop() -> None:
    with pytest.raises(ValueError, match="num` of 1"):
        Axis(start=0.0, stop=10.0, num=1)


def test_regular_num_one_with_equal_start_stop_is_allowed() -> None:
    axis = Axis(start=5.0, stop=5.0, num=1)

    assert axis.coordinate_values == (5.0,)


@pytest.mark.parametrize("data_type", ["tuple", "polygon"])
def test_composite_axis_requires_coordinates(data_type: str) -> None:
    # ADR-0019: a composite axis cannot rely on spec 6.1.1's default (it names
    # the axis, keyed "composite", not a component), so omitting `coordinates`
    # is rejected at construction, reversing ADR-0018.
    pattern = f"a {data_type!r} axis requires `coordinates`"

    with pytest.raises(ValueError, match=pattern):
        Axis(values=((1.0, 2.0),), data_type=data_type)


@pytest.mark.parametrize("data_type", ["tuple", "polygon"])
def test_composite_axis_requires_coordinates_on_decode(data_type: str) -> None:
    blob = b'{"dataType": "%s", "values": [[1.0, 2.0]]}' % data_type.encode()
    pattern = f"a {data_type!r} axis requires `coordinates`"

    with pytest.raises(msgspec.ValidationError, match=pattern):
        msgspec.json.decode(blob, type=Axis)


def test_polygon_axis_requires_at_least_two_coordinates() -> None:
    # RFC 7946 3.1.1: a GeoJSON position has >= 2 components, so a polygon needs
    # >= 2 coordinate identifiers; a single one declares impossible 1-D positions
    # that a self-consistent 1-D polygon would slip past validate() (ADR-0019).
    with pytest.raises(ValueError, match=r"at least 2 `coordinates`, got 1"):
        Axis(values=((((0.0, 0.0),),),), data_type="polygon", coordinates=("x",))


def test_polygon_axis_accepts_three_or_more_coordinates() -> None:
    # The floor is a minimum, so a 3-D polygon (x, y, z) constructs.
    axis = Axis(
        values=((((0.0, 0.0, 5.0),),),),
        data_type="polygon",
        coordinates=("x", "y", "z"),
    )

    assert axis.coordinates == ("x", "y", "z")


@pytest.mark.parametrize("data_type", ["tuple", "polygon"])
def test_composite_axis_rejects_regular_form(data_type: str) -> None:
    # A composite value MUST be an array (spec 6.1.1), but start/stop/num yields
    # evenly spaced numbers, so no value satisfies both. Left unguarded, such an
    # axis decodes clean, validate() stays silent, and the bridges read it as
    # zero positions: a silent wrong answer rather than an error.
    with pytest.raises(ValueError, match=f"a {data_type!r} axis requires `values`"):
        Axis(start=0.0, stop=10.0, num=3, data_type=data_type, coordinates=("x", "y"))


@pytest.mark.parametrize("data_type", ["tuple", "polygon"])
def test_composite_axis_rejects_regular_form_on_decode(data_type: str) -> None:
    blob = (
        b'{"dataType": "%s", "coordinates": ["x", "y"],'
        b' "start": 0, "stop": 10, "num": 3}' % data_type.encode()
    )

    with pytest.raises(
        msgspec.ValidationError, match=f"a {data_type!r} axis requires `values`"
    ):
        msgspec.json.decode(blob, type=Axis)


def test_custom_data_type_keeps_the_regular_form() -> None:
    # The composite guard derives from the "tuple"/"polygon" value MUSTs, and the
    # spec constrains no custom dataType's values, so no MUST reaches this axis:
    # it is conformant and must keep loading.
    ax = Axis(start=0.0, stop=10.0, num=3, data_type="knmi:range")

    assert ax.coordinate_values == (0.0, 5.0, 10.0)


def test_composite_tuple_axis_decodes() -> None:
    blob = (
        b'{"dataType": "tuple", "coordinates": ["t", "x", "y"],'
        b' "values": [["2020-01-01T00:00:00Z", 1, 2]]}'
    )
    ax = msgspec.json.decode(blob, type=Axis)
    assert ax.data_type == "tuple"
    assert ax.coordinates == ("t", "x", "y")
    assert ax.values == (("2020-01-01T00:00:00Z", 1, 2),)


def test_tuple_builder_materializes_positions() -> None:
    ax = Axis.tuple_(
        [("2020-01-01T00:00:00Z", 1.0, 2.0), ("2020-01-02T00:00:00Z", 3.0, 4.0)],
        coordinates=("t", "x", "y"),
    )

    assert ax.data_type == "tuple"
    assert ax.coordinates == ("t", "x", "y")
    assert ax.values == (
        ("2020-01-01T00:00:00Z", 1.0, 2.0),
        ("2020-01-02T00:00:00Z", 3.0, 4.0),
    )


def test_custom_data_type_decodes() -> None:
    # The spec (6.1.1) allows custom extension dataType values; the model accepts
    # any string and treats an unrecognized one as primitive-like (no composite
    # coordinates required).
    blob = b'{"dataType": "knmi:range", "values": ["2022-01-01T04:03:00Z"]}'
    ax = msgspec.json.decode(blob, type=Axis)

    assert ax.data_type == "knmi:range"
    assert ax.values == ("2022-01-01T04:03:00Z",)
