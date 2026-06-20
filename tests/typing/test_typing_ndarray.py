"""Typing-conformance checks, verified by the type-checker matrix in CI.

``assert_type`` is a runtime no-op, so these run (trivially) under pytest too;
their real value is that mypy / basedpyright / pyrefly / ty must all agree on the
public typing guarantees. They mirror the *intended* models until the real ones
land. Kept deliberately conservative for now: the cross-checker-divergent cases
(default-TypeVar inference, union-decode narrowing) are added once we can confirm
each checker's behavior against the matrix.
"""

import sys
from typing import Generic, Literal, assert_type

import msgspec

if sys.version_info >= (3, 13):
    from typing import TypeVar
else:
    from typing_extensions import TypeVar

Scalar = float | int | str
T = TypeVar("T", default=Scalar)


class _CovJSONStruct(msgspec.Struct, frozen=True, rename="camel"):
    pass


class NdArray(_CovJSONStruct, Generic[T], frozen=True, tag="NdArray"):
    data_type: Literal["float", "integer", "string"]
    values: tuple[T | None, ...]


def test_parameterized_decode_value_type() -> None:
    arr = msgspec.json.decode(
        b'{"type":"NdArray","dataType":"float","values":[1.0,null]}',
        type=NdArray[float],
    )
    assert_type(arr, NdArray[float])
    assert_type(arr.values, tuple[float | None, ...])
    assert arr.values == (1.0, None)


def test_explicit_parameter_construction_value_type() -> None:
    arr: NdArray[float] = NdArray(data_type="float", values=(1.0, None))
    assert_type(arr.values, tuple[float | None, ...])
    assert arr.values == (1.0, None)
