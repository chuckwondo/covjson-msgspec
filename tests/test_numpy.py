"""Behavioral tests for the NumPy bridge (NdArray.to_numpy / from_numpy)."""

import math
from typing import Literal

import msgspec
import numpy as np
import pytest

from covjson_msgspec import NdArray, validate


def test_to_numpy_float_maps_missing_to_nan() -> None:
    arr = NdArray(data_type="float", values=(1.5, None), shape=(2,), axis_names=("x",))
    out = arr.to_numpy()

    assert out.shape == (2,)
    assert out[0] == 1.5
    assert math.isnan(out[1])


def test_to_numpy_integer_returns_masked_array() -> None:
    arr = NdArray(
        data_type="integer", values=(1, None, 3), shape=(3,), axis_names=("x",)
    )
    out = arr.to_numpy()

    assert isinstance(out, np.ma.MaskedArray)
    assert out.dtype == np.int64
    assert np.ma.getmaskarray(out).tolist() == [False, True, False]
    assert out[0] == 1 and out[2] == 3


def test_to_numpy_integer_fill_value() -> None:
    arr = NdArray(data_type="integer", values=(1, None), shape=(2,), axis_names=("x",))
    out = arr.to_numpy(fill_value=-9999)

    assert isinstance(out, np.ma.MaskedArray)
    assert out.fill_value == -9999
    # ty's MaskedArray stubs misread the isinstance-narrowed type; mypy and
    # basedpyright are fine, so these are ty-only suppressions.
    filled = out.filled()  # ty: ignore[invalid-argument-type]
    assert filled.tolist() == [1, -9999]  # ty: ignore[no-matching-overload]


def test_to_numpy_integer_as_float() -> None:
    arr = NdArray(data_type="integer", values=(1, None), shape=(2,), axis_names=("x",))
    out = arr.to_numpy(as_float=True)

    assert out.dtype == np.float64
    assert out[0] == 1.0
    assert math.isnan(out[1])


def test_to_numpy_string_object_array() -> None:
    arr = NdArray(
        data_type="string", values=("a", None, "c"), shape=(3,), axis_names=("x",)
    )
    out = arr.to_numpy()

    assert out.dtype == object
    assert out.tolist() == ["a", None, "c"]


def test_to_numpy_reshapes_to_shape() -> None:
    arr = NdArray(
        data_type="float",
        values=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        shape=(2, 3),
        axis_names=("y", "x"),
    )

    assert arr.to_numpy().shape == (2, 3)


def test_to_numpy_value_count_mismatch_raises_clear_error() -> None:
    # Decoding is permissive, so a value count inconsistent with shape only
    # surfaces at to_numpy; it should report a clear message, not numpy's
    # cryptic "cannot reshape array of size ...".
    arr = NdArray(
        data_type="float", values=(1.0, 2.0, 3.0), shape=(2, 2), axis_names=("y", "x")
    )

    with pytest.raises(ValueError, match=r"3 value\(s\) but shape \(2, 2\) needs 4"):
        arr.to_numpy()


@pytest.mark.parametrize(
    ("data_type", "values"),
    [
        ("float", ("1.5",)),
        ("float", ("a",)),
        ("float", (True,)),
        ("integer", (1.5,)),
        ("string", (1.0,)),
    ],
)
def test_to_numpy_rejects_value_not_matching_data_type(
    data_type: Literal["float", "integer", "string"],
    values: tuple[float | int | str | None, ...],
) -> None:
    # Values are projected, not coerced: without the projection each of these
    # would convert silently (to 1.5, 1.0, a truncated 1, and "1.0"), which is
    # data corruption dressed as a successful conversion.
    arr = NdArray(data_type=data_type, values=values, shape=(1,), axis_names=("x",))

    with pytest.raises(msgspec.ValidationError):
        arr.to_numpy()


def test_to_numpy_float_out_of_range_is_validation_error() -> None:
    # An int no float can represent is not projectable, so it raises the error
    # values_as documents, not a bare OverflowError from the coercion.
    arr = NdArray(data_type="float", values=(10**400,), shape=(1,), axis_names=("x",))

    with pytest.raises(msgspec.ValidationError, match="value out of range for float"):
        arr.to_numpy()


def test_validate_clean_does_not_guarantee_to_numpy() -> None:
    # validate(check_values=True) asks a membership question and keeps a stored
    # int an int, so an oversized int is conformant; to_numpy asks a projection
    # question, and there is no float to project it to. Pinning the pair keeps
    # the two deliberately-different rules from being "unified" by accident.
    arr = NdArray(data_type="float", values=(10**400,), shape=(1,), axis_names=("x",))

    assert validate(arr, check_values=True).ok

    with pytest.raises(msgspec.ValidationError):
        arr.to_numpy()


@pytest.mark.parametrize("as_float", [False, True])
def test_to_numpy_integer_beyond_int64_is_numpys_overflow(as_float: bool) -> None:
    # The other half of the split: this value IS valid CoverageJSON, so it is
    # numpy's dtype limit that rejects it, not our projection.
    arr = NdArray(data_type="integer", values=(10**400,), shape=(1,), axis_names=("x",))

    with pytest.raises(OverflowError):
        arr.to_numpy(as_float=as_float)


def test_to_numpy_as_float_accepts_the_same_documents() -> None:
    # as_float chooses how missing data is represented, never which values are
    # admissible, so the projection keys on data_type alone.
    arr = NdArray(data_type="integer", values=(1, 1.5), shape=(2,), axis_names=("x",))

    with pytest.raises(msgspec.ValidationError):
        arr.to_numpy()

    with pytest.raises(msgspec.ValidationError):
        arr.to_numpy(as_float=True)


def test_to_numpy_projection_error_precedes_shape_error() -> None:
    # Both faults at once: the projection runs first, so the value fault wins.
    arr = NdArray(
        data_type="integer", values=(1, 1.5), shape=(2, 2), axis_names=("y", "x")
    )

    with pytest.raises(msgspec.ValidationError):
        arr.to_numpy()


def test_to_numpy_zero_dimensional() -> None:
    arr = NdArray(data_type="float", values=(42.0,))
    out = arr.to_numpy()

    assert out.shape == ()
    assert out.item() == 42.0


def test_from_numpy_infers_float() -> None:
    arr = NdArray.from_numpy(np.array([[1.0, np.nan]]), ("y", "x"))

    assert arr.data_type == "float"
    assert arr.values == (1.0, None)
    assert arr.shape == (1, 2)
    assert arr.axis_names == ("y", "x")


def test_from_numpy_infers_integer() -> None:
    arr = NdArray.from_numpy(np.array([1, 2, 3], dtype=np.int64), ("x",))

    assert arr.data_type == "integer"
    assert arr.values == (1, 2, 3)


def test_from_numpy_masked_to_none() -> None:
    source = np.ma.MaskedArray(data=[1, 2, 3], mask=[False, True, False])
    arr = NdArray.from_numpy(source, ("x",))

    assert arr.data_type == "integer"
    assert arr.values == (1, None, 3)


def test_from_numpy_infinities_become_none() -> None:
    arr = NdArray.from_numpy(np.array([1.0, np.inf, -np.inf]), ("x",))

    assert arr.values == (1.0, None, None)


@pytest.mark.parametrize(
    ("array", "expected"),
    [
        (np.array([1.5, 2.5]), (1.5, 2.5)),
        (np.array([1.5], dtype=np.float32), (1.5,)),
        (np.array([0.1], dtype=np.float16), (0.0999755859375,)),
        (np.array([1, -2], dtype=np.int8), (1, -2)),
        (np.array([2**64 - 1], dtype=np.uint64), (18446744073709551615,)),
        (np.array(["ab", "cd"]), ("ab", "cd")),
        # longdouble shares dtype kind "f" with float64 but has no lossless
        # Python float, so tolist() returns numpy scalars; it must convert
        # element by element instead.
        (np.array([1.5, 2.5], dtype=np.longdouble), (1.5, 2.5)),
    ],
)
def test_from_numpy_yields_native_python_scalars(
    array: np.ndarray, expected: tuple[float | int | str, ...]
) -> None:
    # A gapless array whose dtype already yields the target type is converted by
    # numpy in one C call. The element types must come out as plain builtins: a
    # leaked numpy scalar compares equal but is not JSON-encodable, so assert the
    # type exactly (``np.float64(1.5) == 1.5``) and prove it encodes.
    arr = NdArray.from_numpy(array, ("x",))

    assert arr.values == expected
    assert all(type(a) is type(b) for a, b in zip(arr.values, expected, strict=True))
    assert msgspec.json.encode(arr)


@pytest.mark.parametrize(
    ("array", "data_type", "expected"),
    [
        # These dtypes convert differently element by element than numpy's
        # whole-array conversion would, so they must not take the native path.
        (np.array(["2020-01-01"], dtype="datetime64[D]"), None, ("2020-01-01",)),
        (np.array([True, False]), None, ("True", "False")),
        (np.array([b"ab"]), None, ("b'ab'",)),
        (np.array([1 + 2j]), None, ("(1+2j)",)),
        # An explicit data_type breaks the dtype-to-target correspondence.
        (np.array([1.0, 2.0]), "string", ("1.0", "2.0")),
        (np.array([1.7, 2.9]), "integer", (1, 2)),
        # The missing-value guard is shared across the branches: without it,
        # None in an object array would reach float(None) and raise TypeError.
        (np.array([1.0, None], dtype=object), "float", (1.0, None)),
        # numpy.isfinite cannot see inside an object array, so a non-finite
        # float hiding in one is caught by converting, not by the dtype scan.
        (np.array([1.0, np.nan], dtype=object), "float", (1.0, None)),
        (np.array([1.0, np.inf, -np.inf], dtype=object), "float", (1.0, None, None)),
        # timedelta64 is an np.integer subtype, so it infers "integer"; tolist()
        # would yield datetime.timedelta, which is not an integer at all.
        (np.array([1, 2], dtype="timedelta64[D]"), "string", ("1 days", "2 days")),
    ],
)
def test_from_numpy_converts_non_native_dtypes_element_by_element(
    array: np.ndarray,
    data_type: Literal["float", "integer", "string"] | None,
    expected: tuple[float | int | str, ...],
) -> None:
    arr = NdArray.from_numpy(array, ("x",), data_type=data_type)

    assert arr.values == expected
    assert msgspec.json.encode(arr)


@pytest.mark.parametrize("data_type", ["integer", "string"])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_from_numpy_nonfinite_under_explicit_data_type(
    data_type: Literal["integer", "string"], value: float
) -> None:
    # The guarantee is "non-finite becomes None so the result is always
    # JSON-encodable", and it holds whatever data_type is asked for. Guarding
    # only the float branch would leave "integer" raising and "string" writing
    # the literal "nan".
    arr = NdArray.from_numpy(np.array([1.0, value]), ("x",), data_type=data_type)

    assert arr.values[1] is None


@pytest.mark.parametrize(
    ("array", "axis_names"),
    [
        (np.array([[1.0, 2.0]]), ("x",)),
        (np.array([1.0]), ("x", "y")),
    ],
)
def test_from_numpy_rejects_axis_name_count_mismatch(
    array: np.ndarray, axis_names: tuple[str, ...]
) -> None:
    # The inverse of to_numpy's shape guard: without this, a wrong-length
    # axis_names would build an NdArray that validate() rejects as
    # ndarray.shape-rank.
    with pytest.raises(ValueError, match=r"name\(s\) but the array is"):
        NdArray.from_numpy(array, axis_names)


def test_from_numpy_explicit_data_type_override() -> None:
    # Integer source, but the parameter is declared categorical as strings.
    arr = NdArray.from_numpy(np.array([1, 2]), ("x",), data_type="string")

    assert arr.data_type == "string"
    assert arr.values == ("1", "2")


def test_roundtrip_float_preserves_values_and_shape() -> None:
    arr = NdArray(
        data_type="float",
        values=(1.0, None, 3.0, 4.0),
        shape=(2, 2),
        axis_names=("y", "x"),
    )
    back = NdArray.from_numpy(arr.to_numpy(), arr.axis_names)

    assert back.values == arr.values
    assert back.shape == arr.shape
    assert back.axis_names == arr.axis_names


def test_roundtrip_integer_via_masked_array() -> None:
    arr = NdArray(
        data_type="integer", values=(1, None, 3), shape=(3,), axis_names=("x",)
    )
    back = NdArray.from_numpy(arr.to_numpy(), arr.axis_names)

    assert back.data_type == "integer"
    assert back.values == (1, None, 3)


def test_encodable_after_from_numpy_with_nan() -> None:
    arr = NdArray.from_numpy(np.array([1.0, np.nan]), ("x",))
    # The NaN became None, so the result is JSON-encodable (no invalid float).
    assert msgspec.json.encode(arr).count(b"null") == 1


def test_to_numpy_float_ndarray() -> None:
    arr = NdArray(data_type="float", values=(1.0, 2.0), shape=(2,))

    assert pytest.approx([1.0, 2.0]) == arr.to_numpy().tolist()
