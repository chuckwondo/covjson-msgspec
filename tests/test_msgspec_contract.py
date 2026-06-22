"""Contract tests for the msgspec behaviors the design depends on.

These guard against regressions (including a msgspec *upgrade* silently
changing a behavior we rely on). They use small standalone structs (mirroring the
library's modeling patterns) rather than the public API, so each behavior is
tested in isolation and a failure points straight at the broken assumption.
(``__post_init__``-on-decode is covered by the real `Parameter`/`Unit` tests and
so is not duplicated here.)

Behaviors under test:

1. Tagged-union dispatch on the ``"type"`` field.
2. A bare ``str`` mixed into a tagged-struct union (``domain``/``ranges`` URLs).
3. A bare *generic* struct as a union member, plus parameterized decode.
4. A PEP 696 ``TypeVar`` default is ignored on decode, but a ``bound`` is
   honored (the contract behind ``NdArray``'s element type).
5. ``rename="camel"`` on a shared base maps snake_case attrs to lowerCamelCase
   wire names, and composes with generics, tags, and unions.
6. ``msgspec`` decode bypasses ``__call__`` (so a metaclass guard can block
   direct construction without breaking decoding).
7. ``frozen=True`` composes with all the above: instances are immutable and
   hashable when their fields are. Sequence members are tuples (immutable,
   hashable); mapping members stay ``dict`` (the one mutable/unhashable hole).
   NOTE: ``frozen`` is not inherited, so every concrete struct must restate it.
"""

import sys
from typing import Generic, Literal

import msgspec
import pytest

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

Scalar = float | int | str
T = TypeVar("T", default=Scalar)


class _CovJSONStruct(msgspec.Struct, frozen=True, rename="camel"):
    """Shared base: frozen, snake_case attrs <-> lowerCamelCase wire names."""


class NdArray(_CovJSONStruct, Generic[T], frozen=True, tag="NdArray"):
    data_type: Literal["float", "integer", "string"]
    values: tuple[T | None, ...]
    shape: tuple[int, ...] = ()
    axis_names: tuple[str, ...] = ()


class TiledNdArray(_CovJSONStruct, frozen=True, tag="TiledNdArray"):
    data_type: Literal["float", "integer", "string"]
    axis_names: tuple[str, ...]
    shape: tuple[int, ...]


class Domain(_CovJSONStruct, frozen=True, tag="Domain"):
    domain_type: str = ""


def test_tagged_union_dispatch() -> None:
    range_t = NdArray | TiledNdArray
    obj = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"float","values":[1.0,2.0,null]}',
        type=range_t,
    )
    assert isinstance(obj, NdArray)
    assert obj.values == (1.0, 2.0, None)


def test_str_mixed_into_tagged_union() -> None:
    # domain: Domain | str  (str = external URL reference)
    typ = Domain | str
    assert (
        msgspec.json.decode(b'"http://example.com/d"', type=typ)
        == "http://example.com/d"
    )
    d = msgspec.json.decode(b'{"type":"Domain","domainType":"Grid"}', type=typ)
    assert isinstance(d, Domain) and d.domain_type == "Grid"


def test_str_in_range_union() -> None:
    # ranges value: NdArray | TiledNdArray | str
    range_t = NdArray | TiledNdArray | str
    assert msgspec.json.decode(b'"http://t"', type=range_t) == "http://t"
    obj = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"float","values":[]}', type=range_t
    )
    assert isinstance(obj, NdArray)


def test_bare_generic_in_union_and_parameterized_decode() -> None:
    # bare generic struct works as a union member ...
    range_t = NdArray | TiledNdArray
    obj = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"integer","values":[1,2]}', type=range_t
    )
    assert isinstance(obj, NdArray)
    # ... and callers can opt into a typed element decode.
    typed = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"float","values":[1.0,null]}',
        type=NdArray[float],
    )
    assert typed.values == (1.0, None)


def test_parameterized_decode_rejects_wrong_element_type() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(
            b'{"type":"NdArray","dataType":"integer","values":[1.5]}',
            type=NdArray[int],
        )


def test_typevar_default_ignored_but_bound_honored_on_decode() -> None:
    # The contract behind NdArray's `T = TypeVar("T", bound=Scalar,
    # default=Scalar)`: a bare generic decode IGNORES the PEP 696 default
    # (treating the element type as Any), so a default-only TypeVar would let a
    # non-scalar through. A bound, by contrast, IS honored, restoring runtime
    # enforcement. Hence the library sets both.
    DefaultOnly = TypeVar("DefaultOnly", default=Scalar)
    Bounded = TypeVar("Bounded", bound=Scalar, default=Scalar)

    class Loose(_CovJSONStruct, Generic[DefaultOnly], frozen=True):
        values: tuple[DefaultOnly | None, ...]

    class Strict(_CovJSONStruct, Generic[Bounded], frozen=True):
        values: tuple[Bounded | None, ...]

    # Default ignored: a nested array (not a Scalar) is accepted as Any.
    loose = msgspec.json.decode(b'{"values":[[1,2]]}', type=Loose)
    assert loose.values == ([1, 2],)

    # Bound honored: the same payload is rejected.
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"values":[[1,2]]}', type=Strict)


def test_rename_camel_roundtrips_with_generic_and_tag() -> None:
    nd = NdArray(data_type="float", values=(1.0,), axis_names=("x",), shape=(1,))
    out = msgspec.json.encode(nd)
    # wire names are lowerCamelCase; the tag field is untouched
    assert b'"dataType"' in out
    assert b'"axisNames"' in out
    assert b'"type":"NdArray"' in out
    assert b'"data_type"' not in out
    back = msgspec.json.decode(out, type=NdArray[float])
    assert back.data_type == "float"
    assert back.axis_names == ("x",)


def test_frozen_structs_are_immutable() -> None:
    nd = NdArray(data_type="float", values=(1.0,))
    # name via a variable so ruff B010 / mypy don't object to the dynamic set
    field = "data_type"
    with pytest.raises((AttributeError, TypeError)):
        setattr(nd, field, "integer")


def test_frozen_struct_with_tuple_fields_is_hashable() -> None:
    a = NdArray(data_type="float", values=(1.0, None), shape=(2,))
    b = NdArray(data_type="float", values=(1.0, None), shape=(2,))
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_dict_fields_remain_the_mutability_hole() -> None:
    class HasMap(_CovJSONStruct, frozen=True, tag="HasMap"):
        axes: dict[str, int] = msgspec.field(default_factory=dict)

    m = HasMap(axes={"x": 1})
    # frozen prevents rebinding the attribute (name via variable; see above) ...
    field = "axes"
    with pytest.raises((AttributeError, TypeError)):
        setattr(m, field, {})
    # ... but a dict member's contents stay mutable, and the struct is unhashable.
    m.axes["y"] = 2
    assert m.axes == {"x": 1, "y": 2}
    with pytest.raises(TypeError):
        hash(m)


def test_decode_bypasses_call_guard() -> None:
    """A metaclass __call__ guard blocks direct construction but not decode."""

    class GuardedMeta(type(msgspec.Struct)):  # type: ignore[misc]  # dynamic base
        def __call__(cls, *args: object, **kwargs: object) -> object:
            msg = f"construct {cls.__name__} via a builder, not directly"
            raise TypeError(msg)

    class Guarded(msgspec.Struct, metaclass=GuardedMeta, frozen=True, tag="Guarded"):
        x: int

    with pytest.raises(TypeError):
        Guarded(x=1)

    decoded = msgspec.json.decode(b'{"type":"Guarded","x":5}', type=Guarded)
    assert decoded.x == 5
