"""Behavioral tests for the NumPy bridge (NdArray.to_numpy / from_numpy)."""

import math

import numpy as np
import pytest

from covjson_msgspec import NdArray


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
    assert out.filled().tolist() == [1, -9999]


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
    import msgspec

    arr = NdArray.from_numpy(np.array([1.0, np.nan]), ("x",))
    # The NaN became None, so the result is JSON-encodable (no invalid float).
    assert msgspec.json.encode(arr).count(b"null") == 1


def test_to_numpy_typed_ndarray_float() -> None:
    typed: NdArray[float] = NdArray(data_type="float", values=(1.0, 2.0), shape=(2,))

    assert pytest.approx([1.0, 2.0]) == typed.to_numpy().tolist()
