# ADR-0021: A duration is an ISO 8601 duration string

## Status

Accepted

## Context

CoverageJSON has no duration type. A range's `"dataType"` MUST be one of
`"float"`, `"integer"`, or `"string"` (spec 6.2), and a primitive axis value
MUST be a number or a string (spec 6.1.1). NumPy has `timedelta64`, xarray and
pandas produce it routinely, and a duration coordinate or a duration data
variable is ordinary in a CF-style dataset. So the bridges have to map a
duration onto one of three types, and the mapping had never been decided.

[#189](../../issues/189) surfaced it as a crash. NumPy makes `timedelta64` a
subtype of `np.integer`, so `NdArray.from_numpy`'s dtype inference called it
`"integer"`, and `int()` then raised on the `datetime.timedelta` its elements
yield. `from_xarray` builds every range through `from_numpy`, so a duration data
variable took the bridge down.

Three facts shaped the choice.

**The library was already answering this question, invisibly, on a second
path.** msgspec encodes a `datetime.timedelta` as an ISO 8601 duration
(`msgspec.json.encode(timedelta(hours=6))` is `b'"PT21600S"'`), and the xarray
axis path leaned on that by storing raw `timedelta` objects in `Axis.values`.
That went unnoticed because the bytes came out right. It was wrong in two ways
regardless: `AxisValue` does not admit `timedelta`, so the stored value was
off-type and the document had one shape going out and another coming back; and
because `.tolist()` yields `int` rather than `timedelta` for `ns`, a nanosecond
duration coordinate was read as an evenly-spaced *numeric* axis and its unit
left the document entirely. Deciding this for `from_numpy` alone would have
added a third spelling rather than settled anything.

**Delegating to msgspec cannot work, and the reason is not a detail.**
`numpy.ndarray.tolist()` returns `int`, not `timedelta`, for the `Y`, `M`, `ns`,
`ps`, `fs`, and `as` units. Handing those to msgspec emits the bare number `1`
for one nanosecond while a day emits `"P1D"`: same concept, two JSON types.
msgspec also cannot express `P1Y` or `P1M` at all, because `timedelta` has no
month or year, and it normalizes everything else through `timedelta`'s
days-and-seconds representation, so six hours becomes `PT21600S`. pandas is no
better: `Timedelta.isoformat()` reports a `timedelta64[15m]` count of 1 as one
minute, silently dropping the dtype's multiple that NumPy itself honours.

**The spec's own example writes a duration this way.** The `statisticalPeriod`
of a daily mean is `"P1D"` (spec 4), not a count of seconds. That is not a
normative clause about ranges, but it is the format CoverageJSON reaches for
when it needs a duration, and it is the unit-preserving form.

## Decision

A duration is carried as an ISO 8601 duration string, formatted from the unit
the source array declares.

- `covjson_msgspec._duration` is the single home for the conversion. Both the
  NumPy bridge (`NdArray.from_numpy`) and the xarray bridge (coordinates, via
  `_coord_values`) call it, so one duration is written one way wherever it
  appears in a document.
- The unit is preserved rather than normalized. For an element value of 1,
  `timedelta64[Y]` becomes `"P1Y"` and `timedelta64[h]` becomes `"PT1H"`, where
  msgspec's `timedelta` encoding writes that hour as `"PT3600S"` and cannot
  write the year at all. A calendar year and a month are not fixed spans, which
  is why NumPy itself refuses to convert them to seconds, and normalizing would
  have to invent a length for them.
- The dtype's multiple is folded into the value, so a `timedelta64[15m]` count
  of 1 is `"PT15M"`.
- A sub-second unit becomes a fractional number of seconds
  (`"PT0.0000015S"`), since ISO 8601 has no designator below the second.
- The sign leads the whole duration (`"-P3D"`), which is where msgspec puts it
  and the only position that parses.
- `from_numpy` infers `"string"` for a duration array and **refuses** an
  explicit `data_type` of `"float"` or `"integer"`, naming
  `array.astype("int64")` as the way to get the raw counts. A duration array
  with no unit (`"generic"`) is refused too: its counts have nothing to be
  counts of.
- A duration coordinate is always a *listed* axis. The compact regular form
  requires `"start"` and `"stop"` to be numbers (spec 6.1.1), so it cannot
  carry a duration at all.

The conversion is deliberately one-way. `to_numpy` does not reconstruct
`timedelta64`, because on the wire a duration range is indistinguishable from
any other `"string"` range, so recovering one would mean sniffing string
contents. `datetime64` already set this precedent.

## Alternatives considered

**`"integer"`, in the array's own unit.** Rejected. It preserves the number and
the ordering and matches what the inference already did, but the unit survives
only in the source dtype, which the document does not record. A reader sees
`1` and cannot tell a day from a nanosecond. That is exactly the silent
lossiness the [byte-faithful tenet](../design/tenets.md) confines to opt-in
bridges, and it is still available deliberately, one `astype` away.

**`"string"` via NumPy's own repr (`"1 days"`).** Rejected. It is the one-line
fix: exclude `timedelta64` from the integer arm and let it fall through to
`str()`. It round-trips through our own model and it does keep the unit, but
`"1 days"` is a NumPy artifact, not a standard duration, so no external
consumer can parse it. A permanent wire-format cost for a temporary saving.

**Delegating to msgspec's `timedelta` encoding.** Rejected on the evidence
above: it cannot express `P1Y` or `P1M`, it silently emits a bare integer for
the `ns`, `ps`, `fs`, and `as` units, and it discards the declared granularity.
Its one merit was that the axis path already did it by accident.

**Raising a clear `ValueError` and deciding later.** Rejected, though it was
the cheapest honest option and the most reversible. The escape hatch it would
point at, `from_numpy(..., data_type=...)`, is not reachable through
`from_xarray`, so refusing would have converted a confusing `TypeError` into an
honest dead end for the CF datasets that motivated the issue.

**Fixing `from_numpy` only, and leaving the axis path alone.** Rejected. It
would have left one document able to spell six hours `"PT21600S"` in an axis,
via msgspec, and `"PT6H"` in a range, and made this ADR false on arrival.

## Consequences

- A duration has one representation, produced in one place. Adding a third
  bridge means calling `to_iso_durations`, not choosing a format again.
- A `timedelta64[ns]` coordinate is now a listed axis of ISO strings instead of
  bare numbers, so its unit reaches the document. Any consumer reading such an
  axis as numeric sees a wire-format change; the previous output recorded no
  unit at all, so it was not recoverable data.
- `Axis.values` no longer holds `datetime.timedelta`, so a coverage from
  `from_xarray` compares equal to the same coverage decoded from its own bytes.
- `to_xarray` returns a duration axis as a string coordinate. Previously an
  unserialized coverage came back as `timedelta64[us]`, but only because
  `Axis.values` held the off-type `timedelta`; the same coverage decoded from
  its own JSON already came back as strings. So this replaces a divergence
  between an object and its own bytes with one answer, rather than removing a
  round-trip that worked. A time axis *does* survive the round trip, because
  the document declares it is time through its temporal reference system, which
  is what `_parse_times` keys on. CoverageJSON has nothing that declares a
  duration axis, so recovering one would mean sniffing every string axis for a
  leading `P` and would misread a string axis whose values legitimately start
  with one.
- Through `from_xarray` specifically, a coarse duration still arrives as
  seconds (`"PT86400S"` for a day). xarray normalizes every coarser-than-second
  duration dtype to `timedelta64[s]` when a `Dataset` is built, so the declared
  unit is gone before the bridge sees it. We are faithful to what we are
  handed; `NdArray.from_numpy` called directly preserves `"P1D"`.
- A caller wanting numbers out of a duration array converts it first. The
  refusal is a `ValueError` that names that remedy.
- Revisit if CoverageJSON gains a duration `dataType`, or if a consumer appears
  that needs the inverse (`to_numpy` reconstructing `timedelta64`), which would
  need an explicit opt-in rather than content sniffing.
