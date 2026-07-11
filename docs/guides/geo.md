# geo and GeoJSON

The `[geo]` extra exports a `Coverage` (or a `CoverageCollection`) to
[GeoJSON](https://geojson.org/) or a
[`geopandas.GeoDataFrame`](https://geopandas.org/). Both are one-way, for the
spatial domain types (polygon, point, trajectory).

```python
gj = to_geojson(cov)          # a GeoJSON FeatureCollection (a dict)
gdf = to_geopandas(cov)       # a geopandas GeoDataFrame
```

Each coverage becomes one feature: a point-like domain yields `Point` geometry from
its `x` / `y` axes, a polygon domain yields `Polygon` geometry, and so on.
`to_geojson` is a thin wrapper over `to_geopandas`. A `trajectory_as` argument
controls how a trajectory is rendered (as points, by default). See the
[bridges reference](../reference/bridges.md).
