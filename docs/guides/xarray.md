# xarray

The `[xarray]` extra bridges a `Coverage` to a CF-aware
[`xarray.Dataset`](https://docs.xarray.dev/en/stable/), and back. It is the only
two-way bridge: xarray's labeled, n-dimensional model is close enough to
CoverageJSON's that the round trip preserves the structure.

```python
ds = cov.to_xarray()          # a CF-aware xarray.Dataset (or to_xarray(cov))
cov2 = from_xarray(ds)        # xarray.Dataset -> Coverage
```

The axes become coordinates, each range becomes a data variable, and CF
conventions (units, standard names, the temporal calendar) are applied where the
model carries them. A whole collection maps to a
[`DataTree`](https://docs.xarray.dev/en/stable/):

```python
dt = collection.to_datatree()   # a CoverageCollection -> xarray.DataTree
```

Conversion is where lossy interpretation is allowed to happen (temporal strings are
parsed, values are reshaped), in keeping with the byte-faithful core: decode stays
exact, the bridge is where you opt into xarray's world. One such loss: on a
standard calendar a `±hh:mm` time offset is flattened to naive-UTC (numpy has no
timezone type); if you need the zone kept, resolve those values with
[`to_datetime`](../reference/temporal.md), which preserves the offset. A
non-standard (cftime) calendar drops the offset instead, since such calendars have
no civil UTC to carry one. See the [bridges reference](../reference/bridges.md).
