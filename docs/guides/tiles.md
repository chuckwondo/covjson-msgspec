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

Each tile fills one *slot*: the block of cells in the stitched array picked out
by that tile's start index along each axis together with its extent there. A
tile is checked against the slot it was fetched for before it is placed: spec
6.3 requires each tile document's `dataType` and `axisNames` to be the array's,
and its `shape` to match the tile set's `tileShape`, where a `null` entry stands
for the whole axis. The last tile along an axis is truncated to the cells left,
so an edge slot is smaller than `tileShape` along that axis. A tile that does
not match its slot is handled like a failed fetch, and never placed: the default
`fail_fast` raises, and a collecting strategy reports it as a `TileFailure`. So
a tile larger than its slot cannot overwrite the cells belonging to its
neighbors, and one smaller cannot leave a gap that would be indistinguishable
from missing data.

A tiling that cannot be laid out at all is different: it raises `ValueError`
before any tile is fetched, so no `FailureStrategy` sees it. Most of the causes
concern how a tile's URL is built, so the mechanism first: a subdivided axis is
cut into tiles numbered `0, 1, ...` along that axis, and a tile's number along a
given axis is its *ordinal* for that axis, so a tile's ordinals together pick
out the slot it fills. Building a tile's URL replaces each `urlTemplate`
variable with the tile's ordinal for the axis that variable names.

The causes are:

- `axisNames` and `shape` differ in length. Their values are unrelated (one
  holds names, the other extents), but spec 6.3 requires one name per axis, so
  the two arrays must be the same length.
- A `tileShape` entry is neither `null` nor a positive integer, leaving that
  axis with no defined tile count. A `null` entry is legal and means the axis
  is not subdivided at all.
- A `urlTemplate` variable names no subdivided axis, either because no axis
  carries that name or because the axis that does has a `null` `tileShape`
  entry. Either way there is no ordinal to substitute for it.
- Two subdivided axes carry the same name and their ordinals can differ, so the
  single variable that name yields would have to hold both at once. Same-named
  axes that each yield exactly one tile are fine: every ordinal is then `0`.
- The `urlTemplate` expands to the same URL for two different tiles, which
  would make them share a document.

Every cause here but one is a [`validate`](validation.md) finding, so a clean
report rules them out before you call `assemble`. The exception is the duplicate
URL, which has no finding of its own: because a duplicated name is now rejected
earlier, the only way left to reach it is a subdivided axis the template
carries no variable for, which `validate` does report, as
`url-template-missing-variable`. That finding does not imply the `ValueError`,
though: an axis cut into a single tile still requires a variable under spec 6.3,
yet its one tile can collide with nothing, so such a document is flagged and
still assembles. That asymmetry is why `assemble` checks the URLs it built
rather than the template that built them.

Two other failures also arrive before any strategy applies: an out-of-range
`tileset` raises `IndexError`, and choosing the default tile set requires every
tile set to have a countable `tileShape`, not only the one selected.
