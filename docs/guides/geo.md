# geo and GeoJSON

The `[geo]` extra exports a `Coverage` (or a `CoverageCollection`) to
[GeoJSON](https://geojson.org/) or a
[`geopandas.GeoDataFrame`](https://geopandas.org/). Both are one-way, for the
spatial domain types (polygon, point, trajectory).

```python
gj = to_geojson(cov)          # a GeoJSON FeatureCollection (a dict)
gdf = to_geopandas(cov)       # a geopandas GeoDataFrame
```

Each coverage becomes one feature: a point-like domain yields `Point` geometry
from its `x` / `y` axes, a polygon domain yields `Polygon` geometry, and so on.
A `trajectory_as` argument controls how a trajectory is rendered (as points, by
default). See the [bridges reference](../reference/bridges.md).

The two differ in one place: a temporal coordinate is a real datetime in the
`to_geopandas` frame, but stays the value the document carried in `to_geojson`.
Parsing is lossy for the reduced Spec 5.2 forms, since `"2013"` and
`"2013-01-01"` both become the same instant, so the interchange format keeps the
source text.

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
to_geojson(cov)["features"][0]["properties"]["t"]  # '2013'
to_geopandas(cov)["t"].iloc[0]                     # Timestamp('2013-01-01 00:00:00')
```

Without the `TemporalRS` there is nothing to parse against, and both bridges
leave `t` alone.

`to_geopandas` takes the same `times` option, which is what you want when the
frame itself has to reach JSON: `GeoDataFrame.to_json` hands its values to
stdlib `json`, which has no encoder for a `Timestamp`.

```python
to_geopandas(cov, times="raw")["t"].iloc[0]  # '2013'
to_geopandas(cov, times="raw").to_json()     # the default frame raises here
```

The pandas bridge takes it too: `to_pandas(cov, times="raw")`.
