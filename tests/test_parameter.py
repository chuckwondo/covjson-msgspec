"""Behavioral tests for the parameter cluster (encode/decode + invariants).

Doctests in the module cover the happy-path ergonomics; these focus on
round-tripping through JSON and on the invariants holding when data arrives via
``decode`` (not just via the builders)."""

import msgspec
import pytest

from covjson_msgspec import (
    Category,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Symbol,
    Unit,
    i18n,
)


def test_continuous_parameter_roundtrips() -> None:
    param = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")),
        Unit(symbol="K"),
    )
    data = msgspec.json.encode(param)
    # rename -> lowerCamelCase wire names; tag present
    assert b'"type":"Parameter"' in data
    assert b'"observedProperty"' in data
    assert msgspec.json.decode(data, type=Parameter) == param


def test_symbol_object_form_roundtrips() -> None:
    unit = Unit(symbol=Symbol(value="Cel", type_="http://example/Cel"))
    back = msgspec.json.decode(msgspec.json.encode(unit), type=Unit)
    assert isinstance(back.symbol, Symbol)
    assert back.symbol.type_ == "http://example/Cel"


def test_unit_requires_label_or_symbol_on_decode() -> None:
    with pytest.raises((msgspec.ValidationError, ValueError)):
        msgspec.json.decode(b'{"id":"x"}', type=Unit)


def test_categorical_parameter_with_unit_rejected_on_decode() -> None:
    # A categorical parameter that also carries a unit must be rejected even
    # when it arrives as JSON, via __post_init__ during decode.
    payload = {
        "type": "Parameter",
        "observedProperty": {
            "label": {"en": "Land cover"},
            "categories": [{"id": "1", "label": {"en": "Water"}}],
        },
        "categoryEncoding": {"1": 1},
        "unit": {"symbol": "K"},
    }

    with pytest.raises((msgspec.ValidationError, ValueError)):
        msgspec.json.decode(msgspec.json.encode(payload), type=Parameter)


def test_parameter_group_requires_label_or_observed_property() -> None:
    with pytest.raises((msgspec.ValidationError, ValueError)):
        ParameterGroup(members=("u", "v"))


def test_symbol_is_hashable() -> None:
    # No dict members -> a frozen Symbol is hashable and usable in a set.
    a = Symbol(value="Cel", type_="http://example/Cel")
    b = Symbol(value="Cel", type_="http://example/Cel")
    assert a == b
    assert len({a, b}) == 1


def test_category_with_i18n_label_is_unhashable() -> None:
    # The i18n label is a dict, so the struct is immutable but not hashable.
    with pytest.raises(TypeError):
        hash(Category(id="1", label=i18n("Water")))
