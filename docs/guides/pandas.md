# pandas

The `[pandas]` extra converts a `Coverage` (or a `CoverageCollection`) to a tidy
[`pandas.DataFrame`](https://pandas.pydata.org/docs/). It is one-way: pandas'
flat, tabular model does not carry everything CoverageJSON does, so there is no
`from_pandas`.

```python
df = to_pandas(cov)           # or cov.to_pandas()
```

It suits the domain types that flatten naturally to rows: a point, a point series,
or a trajectory becomes one row per position, with a column per axis and per
parameter. See the [bridges reference](../reference/bridges.md).
