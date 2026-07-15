# Subsetting

`isel` and `sel` cut a smaller coverage out of a larger one, in the style of
xarray: `isel` selects by integer position, `sel` by coordinate label. Both are
available as methods on a `Coverage` and as free functions, and both return a new
coverage (the model is immutable).

```python
# By position (integer index or slice), like xarray's .isel
sub = cov.isel(x=0, t=slice(0, 3))

# By label (coordinate value), like xarray's .sel
sub = cov.sel(t="2020-01-01T00:00:00Z", method="nearest")
```

Indexers are keyed by axis name, passed either as keyword arguments (above) or as a
mapping:

```python
from covjson_msgspec import isel, sel

sub = isel(cov, {"x": 0, "t": slice(0, 3)})
sub = sel(cov, {"t": "2020-01-01T00:00:00Z"}, method="nearest")
```

`sel`'s `method="nearest"` snaps a label to the closest coordinate; without it, a
label must match exactly. The result is a coverage subset over the same axes, so
you can chain, encode, or convert it like any other. See the
[subsetting reference](../reference/subset.md).
