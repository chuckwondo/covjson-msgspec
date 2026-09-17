# pandas

The `[pandas]` extra converts a `Coverage` (or a `CoverageCollection`) to a tidy
[`pandas.DataFrame`](https://pandas.pydata.org/docs/). It is one-way: pandas'
flat, tabular model does not carry everything CoverageJSON does, so there is no
`from_pandas`.

```python
df = to_pandas(cov)  # or cov.to_pandas()
```

It suits the domain types that flatten naturally to rows: a point, a point
series, or a trajectory becomes one row per position, with a column per axis and
per parameter. See the [bridges reference](../reference/bridges.md).

A temporal axis under a standard calendar is parsed to pandas datetimes, which
is what you want for slicing and resampling. That parsing is lossy, though: Spec
5.2's reduced forms all promote to the instant they start, so `"2013"` and
`"2013-01-01"` land on the same `Timestamp` and the frame can no longer tell
them apart. Pass `times="raw"` to keep the document's own values instead.

Given a coverage (`cov`) whose `t` axis uses the year form, under a standard
calendar:

```json
"axes": {
  "x": {"values": [1.0]}, "y": {"values": [2.0]},
  "t": {"values": ["2013", "2014"]}
},
"referencing": [
  {"coordinates": ["t"],
   "system": {"type": "TemporalRS", "calendar": "Gregorian"}}
]
```

```python
to_pandas(cov).index[0]               # Timestamp('2013-01-01 00:00:00')
to_pandas(cov, times="raw").index[0]  # '2013'
```

Without the `TemporalRS` there is nothing to parse against, and `t` is left
alone either way.
