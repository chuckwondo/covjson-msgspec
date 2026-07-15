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
