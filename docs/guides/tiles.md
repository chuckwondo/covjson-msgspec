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

A tile is checked against the slot it was fetched for before it is placed: spec
6.3 requires each tile document's `dataType` and `axisNames` to be the array's,
and its `shape` to match the tile set's `tileShape`, where a `null` entry stands
for the whole axis. The last tile along an axis is truncated to the cells left.
A tile that does not match its slot is handled like a failed fetch, and never
placed: the default `fail_fast` raises, and a collecting strategy reports it as
a `TileFailure`. So a tile larger than its slot cannot overwrite the cells
belonging to its neighbors, and one smaller cannot leave a gap that would be
indistinguishable from missing data.

A tiling that cannot be laid out at all is different: it raises `ValueError`
before any tile is fetched, so no `FailureStrategy` sees it. The causes are
`axisNames` not matching `shape`, a non-positive `tileShape` entry, a
`urlTemplate` naming a variable that is not a subdivided axis, and a
`urlTemplate` that does not resolve a distinct URL per tile. The first three are
[`validate`](validation.md) findings, so a clean report rules them out first.
The fourth has no finding of its own, though its usual cause does: a subdivided
axis the template carries no variable for is reported as
`url-template-missing-variable`. Only a `urlTemplate` defeated by a repeated
`axisNames` entry escapes `validate` entirely, so assembly alone catches that.

Two other failures also arrive before any strategy applies: an out-of-range
`tileset` raises `IndexError`, and choosing the default tile set requires every
tile set to have a countable `tileShape`, not only the one selected.
