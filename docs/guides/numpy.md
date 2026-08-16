# numpy

The `[numpy]` extra converts an [`NdArray`](../reference/range.md) to and from a
[`numpy.ndarray`](https://numpy.org/doc/stable/). Unlike the other bridges, this
one is a pair of methods *on `NdArray`* rather than free functions, because it is a
small conversion intrinsic to that leaf value type (an `NdArray` is already
numpy-array-shaped). See [Core concepts](../concepts.md) and the bridge-shape note
in the [design decisions](../design/index.md).

```python
arr = ndarray.to_numpy()                            # NdArray -> numpy.ndarray
nd = NdArray.from_numpy(arr, axis_names=("y", "x")) # numpy.ndarray -> NdArray
```

`to_numpy` reshapes the flat `values` by `shape`; `fill_value` and `as_float`
control how missing data (`None`) is represented. `from_numpy` is a named
constructor: give it the array and its `axis_names`, and it infers the `data_type`
from the array's dtype unless you pass one. See the
[ranges reference](../reference/range.md).

`to_numpy` *projects* values through
[`values_as`][covjson_msgspec.NdArray.values_as] rather than coercing them, so a
value that does not match the range's `dataType` raises `msgspec.ValidationError`
instead of silently becoming something else (a `"1.5"` in a `"float"` range is not
quietly read as `1.5`, and a `1.5` in an `"integer"` range is not truncated to
`1`). To *report* such mismatches rather than raise on the first one, run
[`validate`][covjson_msgspec.validate] with `check_values=True` first; see the
[validation guide](validation.md). A value that is conformant but too large for
the target NumPy dtype (an integer beyond `int64`) raises `OverflowError`, since
that is NumPy's limit rather than the document's.
