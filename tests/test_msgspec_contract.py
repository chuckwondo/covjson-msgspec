"""Contract tests for the msgspec behaviors the design depends on.

These guard against regressions (including a msgspec *upgrade* silently
changing a behavior we rely on). They use small standalone structs (mirroring the
library's modeling patterns) rather than the public API, so each behavior is
tested in isolation and a failure points straight at the broken assumption.

Behaviors under test:

1. A bare ``str`` mixed into a tagged-struct union (``domain``/``ranges`` URLs).
2. ``rename="camel"`` on a shared base maps snake_case attrs to lowerCamelCase
   wire names, and composes with tags and unions.
3. ``frozen=True`` prevents attribute rebinding but does NOT make dict-field
   contents immutable: the known mutability hole in ``Coverage.ranges``.
"""

from typing import Literal

import msgspec
import pytest


class _CovJSONStruct(msgspec.Struct, frozen=True, rename="camel"):
    """Shared base: frozen, snake_case attrs <-> lowerCamelCase wire names."""


class NdArray(_CovJSONStruct, frozen=True, tag="NdArray"):
    data_type: Literal["float", "integer", "string"]
    values: tuple[float | int | str | None, ...]
    shape: tuple[int, ...] = ()
    axis_names: tuple[str, ...] = ()


class Domain(_CovJSONStruct, frozen=True, tag="Domain"):
    domain_type: str = ""


def test_str_mixed_into_tagged_union() -> None:
    # domain: Domain | str  (str = external URL reference)
    typ = Domain | str
    assert (
        msgspec.json.decode(b'"http://example.com/d"', type=typ)
        == "http://example.com/d"
    )
    d = msgspec.json.decode(b'{"type":"Domain","domainType":"Grid"}', type=typ)
    assert isinstance(d, Domain) and d.domain_type == "Grid"


def test_rename_camel_roundtrips_with_tag() -> None:
    nd = NdArray(data_type="float", values=(1.0,), axis_names=("x",), shape=(1,))
    out = msgspec.json.encode(nd)
    # wire names are lowerCamelCase; the tag field is untouched
    assert b'"dataType"' in out
    assert b'"axisNames"' in out
    assert b'"type":"NdArray"' in out
    assert b'"data_type"' not in out
    back = msgspec.json.decode(out, type=NdArray)
    assert back.data_type == "float"
    assert back.axis_names == ("x",)


def test_dict_field_contents_remain_mutable_despite_frozen() -> None:
    # frozen=True prevents rebinding the attribute, but a dict field's *contents*
    # are still mutable: the known hole for Coverage.ranges (dict[str, Range]).
    class HasRanges(_CovJSONStruct, frozen=True, tag="HasRanges"):
        ranges: dict[str, int] = msgspec.field(default_factory=dict)

    obj = HasRanges(ranges={"a": 1})
    field = "ranges"
    with pytest.raises((AttributeError, TypeError)):
        setattr(obj, field, {})
    obj.ranges["b"] = 2
    assert obj.ranges == {"a": 1, "b": 2}
