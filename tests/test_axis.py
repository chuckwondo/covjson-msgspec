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


def test_regular_num_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Axis(start=0.0, stop=1.0, num=0)


def test_composite_axis_requires_coordinates() -> None:
    with pytest.raises(ValueError, match="requires `coordinates`"):
        Axis(values=((1.0, 2.0),), data_type="tuple")


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
    ax = Axis.tuple(
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
