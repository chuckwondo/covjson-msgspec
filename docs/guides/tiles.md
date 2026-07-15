# Assembling tiled ranges

A large range can be split across separate tile documents rather than inlined. A
[`TiledNdArray`](../reference/range.md) describes those tiles (as `TileSet`s), and
`assemble` stitches them back into a single `NdArray`, fetching each tile through
the same injected fetcher used for [reference resolution](references.md).

```python
# Sync
report = tiled.assemble(fetch)
array = report.array             # the stitched NdArray

# Async: independent tile fetches run concurrently
report = await tiled.assemble_async(afetch)
array = report.array
```

Like reference resolution, assembly is best-effort and shares the fetcher seam and
error-strategy machinery: pass a `FailureStrategy` to decide how to react to a
failed tile fetch, and read any `TileFailure`s off the `AssembleReport`. Bound
concurrency in the fetcher, exactly as for references. See the
[ranges reference](../reference/range.md).
