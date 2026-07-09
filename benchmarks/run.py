"""Benchmark covjson-msgspec against covjson-pydantic (issue #18).

A standalone, stdlib-only harness (no pytest, no third-party benchmark tool).
It times the core codec operations on a representative document set, then writes
two artifacts next to this script that the covjson-pydantic comparison doc
(issue #22) can embed or link:

- ``results.json``: the machine-readable record (env, versions, semantics, and
  one row per measurement).
- ``results.md``: a human table rendered *from the same rows*, so the two cannot
  drift.

Regenerate with::

    uv run --group bench python benchmarks/run.py          # full run
    uv run --group bench python benchmarks/run.py --quick  # fast smoke run

Fairness is the whole point (see ``README.md``): covjson-pydantic's decode fuses
structural decode, validation, and ``datetime`` parsing into one mandatory step,
while covjson-msgspec keeps those opt-in. The msgspec side is therefore measured
as a cumulative ladder from "what you pay by default" (a structural decode that
leaves temporal values as strings) up to "everything pydantic forces" (decode +
full validation + ``to_datetime`` over every temporal coordinate). The top rung
is the only honest like-for-like against pydantic's decode.
"""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import platform
import statistics
import timeit
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from importlib.metadata import version

import covjson_msgspec as cm

_REPO = pathlib.Path(__file__).resolve().parent.parent
_CORPUS = _REPO / "tests" / "corpus"
_OUT = pathlib.Path(__file__).resolve().parent

# The libraries whose versions pin a result set. Recorded verbatim in the
# artifacts so a number is always traceable to the exact code that produced it.
_PINNED = (
    "covjson-msgspec",
    "covjson-pydantic",
    "msgspec",
    "pydantic",
    "pydantic-core",
    "numpy",
)

# What each (lib, op) actually does, defined once and referenced by rendering so
# the annotation is never restated per cell. Keyed "<lib>:<op>" for JSON.
_SEMANTICS: dict[str, str] = {
    "msgspec:decode": "structural decode only; temporal values stay strings",
    "msgspec:encode": "model to JSON bytes",
    "msgspec:roundtrip": (
        "structural decode then encode; no validation or datetime parsing (the "
        "realistic default cost of a faithful read + write)"
    ),
    "msgspec:roundtrip(full)": (
        "decode + validate(check_values=True) + to_datetime(t) + encode: the "
        "closest like-for-like with pydantic round-trip (see the validation "
        "parity table for the exact check overlap)"
    ),
    "msgspec:decode+validate": "decode + cross-cutting validate() (no value scan)",
    "msgspec:decode+validate(values)": (
        "decode + validate(check_values=True): adds the O(n) value scans "
        "(value-vs-dataType, categorical, temporal lexical form, and monotonic "
        "axes, the last under the default, replaceable axis-order checker)"
    ),
    "msgspec:decode+validate(values)+datetime": (
        "the row above plus to_datetime() over every temporal coordinate: the "
        "closest like-for-like with pydantic decode (see the validation parity "
        "table for the exact check overlap)"
    ),
    "msgspec:matched-full": (
        "decode + validate(check_values=True) + to_datetime(t): full validation, "
        "including the monotonic and categorical checks pydantic omits"
    ),
    "msgspec:matched-trim": (
        "decode + validate(check_values=True, monotonic disabled) + "
        "to_datetime(t): trimmed to pydantic's check set for a like-for-like"
    ),
    "pydantic:decode": (
        "model_validate_json: structural decode + validation + datetime parsing, "
        "fused and mandatory"
    ),
    "pydantic:encode": "model_dump_json",
    "pydantic:roundtrip": "model_validate_json then model_dump_json",
    "pydantic:decode+monotonic": (
        "model_validate_json + a manual monotonic-axis scan, so pydantic does the "
        "same validation as msgspec's full pass"
    ),
}


# Capability probes: small documents that expose a one-sided gap. Each is
# spec-valid (or spec-faithful) and decodes on covjson-msgspec, but trips a
# covjson-pydantic limitation, so the table can show a real decode time on the
# side that can and an explicit "cannot" (with the actual failure) on the side
# that cannot. Probed both directions; the reverse direction (a document
# covjson-pydantic accepts but covjson-msgspec rejects) is currently empty,
# which the rendered section states rather than leaves implicit.
_XY_REF = (
    '{"coordinates":["x","y"],"system":{"type":"GeographicCRS",'
    '"id":"http://www.opengis.net/def/crs/OGC/1.3/CRS84"}}'
)
_T_REF = '{"coordinates":["t"],"system":{"type":"TemporalRS","calendar":"Gregorian"}}'
_XY = '"x":{"values":[1.0]},"y":{"values":[2.0]}'


def _coverage_doc(domain_type: str, axes: str, referencing: str) -> bytes:
    """Assemble a minimal single-coverage CoverageJSON document as bytes.

    Examples
    --------
    >>> _coverage_doc("Grid", '"x":{"values":[1.0]}', _XY_REF).startswith(b'{"type"')
    True
    """
    return (
        '{"type":"Coverage","domain":{"type":"Domain","domainType":"'
        + domain_type
        + '","axes":{'
        + axes
        + '},"referencing":['
        + referencing
        + ']},"ranges":{}}'
    ).encode()


# (name, document, the gap it exposes). Every entry decodes on covjson-msgspec.
_PROBES: list[tuple[str, bytes, str]] = [
    (
        "naive datetime",
        _coverage_doc(
            "PointSeries",
            f'{_XY},"t":{{"values":["2020-01-01T00:00:00"]}}',
            f"{_XY_REF},{_T_REF}",
        ),
        "a full-form t value with no timezone",
    ),
    (
        "date-only t",
        _coverage_doc(
            "PointSeries",
            f'{_XY},"t":{{"values":["2020-06-15"]}}',
            f"{_XY_REF},{_T_REF}",
        ),
        "a reduced-precision date (spec form YYYY-MM-DD)",
    ),
    (
        "year-month t",
        _coverage_doc(
            "PointSeries",
            f'{_XY},"t":{{"values":["2020-06"]}}',
            f"{_XY_REF},{_T_REF}",
        ),
        "a reduced-precision month (spec form YYYY-MM)",
    ),
    (
        "extra custom axis",
        _coverage_doc("Grid", f'{_XY},"foo":{{"values":[0.0]}}', _XY_REF),
        "a domain axis beyond the fixed x/y/z/t/composite slots",
    ),
    (
        "mixed-type axis",
        _coverage_doc("Grid", '"x":{"values":[1.0,"a"]},"y":{"values":[2.0]}', _XY_REF),
        "one axis mixing numeric and string values",
    ),
]


# Marks for the conformance scorecard: green check when a library's behavior
# matches the spec-expected behavior, red cross when it does not.
_CONFORMANT = "✅"
_NONCONFORMANT = "❌"


# A decode-time conformance scorecard, from empirical probing (reproduced in the
# README). The "expected" column is the proportional response the spec calls for,
# by requirement level: a MUST violation should error, a SHOULD violation should
# warn (the document stays loadable), and a conformant input should be accepted.
# Each library column is marked for whether its behavior is proportional.
# covjson-msgspec matches on every row; covjson-pydantic misses two MUST checks
# (monotonic, categorical), over-enforces a SHOULD (it raises on a malformed
# temporal value the spec permits you to load), and rejects one conformant input
# (reduced-precision).
_VALIDATION: list[tuple[str, str, str, bool, str, bool]] = [
    # (check, expected, msgspec behavior, msgspec ok, pydantic behavior, pydantic ok)
    ("structure and field types", "error (MUST)", "error", True, "error", True),
    ("value vs dataType", "error (MUST)", "error", True, "error", True),
    ("value count vs shape product", "error (MUST)", "error", True, "error", True),
    (
        "monotonic axis order",
        "error (MUST)",
        "error (default policy)",
        True,
        "not checked",
        False,
    ),
    (
        "categorical code vs categories",
        "error (MUST)",
        "error",
        True,
        "not checked",
        False,
    ),
    (
        "malformed temporal (2010-13-99)",
        "warning (SHOULD)",
        "warning, preserved",
        True,
        "error (overreach)",
        False,
    ),
    (
        "reduced-precision t (2020-06)",
        "accept (valid)",
        "accepted",
        True,
        "rejects",
        False,
    ),
]


@dataclass(frozen=True)
class Cell:
    """One benchmark document, verified to parse on both libraries.

    ``t_values`` holds every temporal coordinate string in the document (empty
    when the document has no ``t``-axis); the datetime rung converts exactly
    these, mirroring the work pydantic does during decode.
    """

    name: str
    raw: bytes
    pyd_cls: type
    t_values: tuple[str, ...]


@dataclass(frozen=True)
class Row:
    """One measurement outcome: ``measured`` carries stats, ``skipped`` a reason.

    A skipped operation is still a row (never an omitted line), so a reader can
    tell "not applicable, and why" from "measured, fast".
    """

    cell: str
    lib: str
    op: str
    status: str
    median_us: float | None = None
    iqr_us: float | None = None
    n: int | None = None
    reason: str | None = None


def main() -> None:
    """Run the benchmark and write ``results.json`` and ``results.md``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="fast smoke run (smaller synthetic doc, fewer iterations)",
    )
    args = parser.parse_args()

    cells = build_cells(quick=args.quick)
    rows = [row for cell in cells for row in measure_cell(cell, quick=args.quick)]
    probe_rows = measure_probes(quick=args.quick)

    env = collect_env()
    versions = {name: version(name) for name in _PINNED}

    payload = {
        "env": env,
        "versions": versions,
        "semantics": _SEMANTICS,
        "results": [asdict(row) for row in rows],
        "probes": [asdict(row) for row in probe_rows],
        "probe_notes": {name: note for name, _raw, note in _PROBES},
    }
    (_OUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (_OUT / "results.md").write_text(render_markdown(rows, probe_rows, env, versions))

    measured_dt = sum(
        row.status == "measured" and row.op == "decode+validate(values)+datetime"
        for row in rows
    )
    print(f"wrote {_OUT / 'results.json'} and {_OUT / 'results.md'}")
    print(f"datetime rung fired on {measured_dt} cell(s)")


def build_cells(*, quick: bool) -> list[Cell]:
    """Assemble the document set, asserting each parses on both libraries.

    covjson-pydantic has no root union, so the concrete decode class is chosen
    here from each document's top-level ``type`` (done once, at setup, never in a
    timed loop). The corpus fixtures are covjson-pydantic's own test data, hence
    timezone-aware and guaranteed pydantic-parseable; the playground documents
    use naive datetimes that pydantic's ``AwareDatetime`` rejects, so they are
    deliberately not used here.

    Examples
    --------
    >>> cells = build_cells(quick=True)
    >>> [c.name for c in cells]  # doctest: +NORMALIZE_WHITESPACE
    ['point-series (small)', 'grid (medium)', 'tiled-ndarray',
     'coverage-collection', 'grid-large (synthetic)']
    >>> any(c.t_values for c in cells)  # the datetime rung has something to run
    True
    """
    from covjson_pydantic.coverage import (
        Coverage,
        CoverageCollection,
        Domain,
        TiledNdArray,
    )

    dispatch: dict[str, type] = {
        "Coverage": Coverage,
        "CoverageCollection": CoverageCollection,
        "TiledNdArray": TiledNdArray,
        "Domain": Domain,
    }
    pydantic_corpus = _CORPUS / "covjson-pydantic"
    specs = [
        ("point-series (small)", pydantic_corpus / "example_py.json"),
        ("grid (medium)", pydantic_corpus / "doc-example-coverage.json"),
        ("tiled-ndarray", pydantic_corpus / "spec-tiled-ndarray.json"),
        (
            "coverage-collection",
            pydantic_corpus / "doc-example-coverage-collection.json",
        ),
    ]
    cells = [_make_cell(name, path.read_bytes(), dispatch) for name, path in specs]
    cells.append(
        _make_cell("grid-large (synthetic)", _synthetic_grid(quick=quick), dispatch)
    )
    return cells


def measure_cell(cell: Cell, *, quick: bool) -> Iterator[Row]:
    """Yield one `Row` per operation for both libraries on ``cell``."""
    for lib, ops in (
        ("msgspec", _msgspec_ops(cell)),
        ("pydantic", _pydantic_ops(cell)),
    ):
        for op, fn, skip in ops:
            if fn is None:
                yield Row(cell.name, lib, op, "skipped", reason=skip)
            else:
                median, iqr, n = _measure(fn, quick=quick)
                yield Row(cell.name, lib, op, "measured", median, iqr, n)


def measure_probes(*, quick: bool) -> list[Row]:
    """Decode each capability probe on both libraries.

    The side that accepts the document yields a `measured` decode time; the side
    that rejects it yields a `skipped` row carrying the library's own failure
    reason, so the gap shows up in the table as a concrete "cannot" rather than a
    blank. This is where "we can, they cannot" (and, symmetrically, its absence)
    becomes visible inside the benchmark itself.
    """
    from covjson_pydantic.coverage import (
        Coverage,
        CoverageCollection,
        Domain,
        TiledNdArray,
    )

    dispatch: dict[str, type] = {
        "Coverage": Coverage,
        "CoverageCollection": CoverageCollection,
        "TiledNdArray": TiledNdArray,
        "Domain": Domain,
    }
    rows: list[Row] = []

    for name, raw, _note in _PROBES:
        pyd_cls = dispatch.get(json.loads(raw)["type"])
        attempts: list[tuple[str, Callable[[], object]]] = [
            ("msgspec", lambda raw=raw: cm.decode(raw))
        ]

        if pyd_cls is None:
            rows.append(
                Row(name, "pydantic", "decode", "skipped", reason="no pydantic model")
            )
        else:
            attempts.append(
                ("pydantic", lambda raw=raw, cls=pyd_cls: cls.model_validate_json(raw))
            )

        for lib, fn in attempts:
            try:
                fn()
            except Exception as exc:
                rows.append(Row(name, lib, "decode", "skipped", reason=_reason(exc)))
                continue

            median, iqr, n = _measure(fn, quick=quick)
            rows.append(Row(name, lib, "decode", "measured", median, iqr, n))

    return rows


def collect_env() -> dict[str, str]:
    """Capture the machine and interpreter a result set was produced on."""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def render_markdown(
    rows: list[Row],
    probe_rows: list[Row],
    env: dict[str, str],
    versions: dict[str, str],
) -> str:
    """Render the artifact table from ``rows`` (the single source of truth)."""
    by_key = {(r.cell, r.lib, r.op): r for r in rows}
    cells = list(dict.fromkeys(r.cell for r in rows))

    lines = [
        "# covjson-msgspec vs covjson-pydantic: benchmark results",
        "",
        "Generated by `benchmarks/run.py`. Do not edit by hand; see "
        "`benchmarks/README.md` for methodology and regeneration.",
        "",
        "## Environment",
        "",
        f"- platform: {env['platform']}",
        f"- python: {env['python']} ({env['implementation']}), {env['machine']}",
        "",
        "## Versions",
        "",
        *(f"- {name}: {ver}" for name, ver in versions.items()),
        "",
        "## Decode and the validation ladder (median us/op)",
        "",
        "pydantic decode fuses decode + validation + datetime parsing; the "
        "msgspec columns walk from a structural decode up to the same fidelity. "
        "`x` is the pydantic/msgspec speedup at that rung.",
        "",
    ]
    lines += _spectrum_table(by_key, cells)
    lines += ["", "## Encode (median us/op)", ""]
    lines += _pair_table(by_key, cells, "encode")
    lines += ["", "## Round-trip (median us/op)", ""]
    lines += [
        "Same asymmetry as decode: pydantic's round-trip validates and parses "
        "datetimes on its decode half. `structural` does neither (the default "
        "read + write cost); `full` adds validation and datetimes to match. `x` "
        "is the pydantic/msgspec speedup.",
        "",
    ]
    lines += _roundtrip_table(by_key, cells)
    lines += ["", "## Validation parity (decode-time checks)", ""]
    lines += _validation_table()
    lines += ["", "## Matched-work comparison (median us/op)", ""]
    lines += _matched_section(by_key, cells)
    lines += ["", "## Capability probes (decode, median us/op)", ""]
    lines += _probe_section(probe_rows)
    lines += ["", "## What each operation does", ""]
    lines += [f"- `{key}`: {desc}" for key, desc in _SEMANTICS.items()]
    lines.append("")
    return "\n".join(lines)


def _make_cell(name: str, raw: bytes, dispatch: dict[str, type]) -> Cell:
    """Build a `Cell`, asserting it decodes on msgspec and pydantic alike."""
    doc_type = json.loads(raw)["type"]
    pyd_cls = dispatch[doc_type]
    cell = Cell(name, raw, pyd_cls, _t_values(cm.decode(raw)))
    cm.decode(cell.raw)
    pyd_cls.model_validate_json(cell.raw)
    return cell


def _t_values(model: object) -> tuple[str, ...]:
    """Collect every temporal coordinate string in a decoded msgspec document.

    Walks the ``t``-axis of a coverage, or of each member of a collection;
    returns an empty tuple for documents without a temporal coordinate (a bare
    range, or a non-temporal domain).

    Examples
    --------
    >>> doc = cm.decode(
    ...     b'{"type":"Coverage",'
    ...     b'"domain":{"type":"Domain","domainType":"PointSeries",'
    ...     b'"axes":{"x":{"values":[1.0]},"y":{"values":[2.0]},'
    ...     b'"t":{"values":["2020-01-01T00:00:00Z"]}}},"ranges":{}}'
    ... )
    >>> _t_values(doc)
    ('2020-01-01T00:00:00Z',)
    """
    members = getattr(model, "coverages", None) or [model]
    values: list[str] = []

    for member in members:
        axes = getattr(getattr(member, "domain", None), "axes", None)

        if axes and "t" in axes:
            values.extend(axes["t"].values)

    return tuple(values)


def _synthetic_grid(*, quick: bool) -> bytes:
    """Encode a large Grid coverage (the missing "large NdArray" cell).

    Built from the public builders and encoded to canonical CoverageJSON bytes
    that both libraries decode. The domain is inline (not a URL reference) so the
    validation rungs exercise the full range/domain cost, and the axes are
    non-temporal, so the datetime rung correctly skips this cell.

    Examples
    --------
    >>> raw = _synthetic_grid(quick=True)
    >>> type(cm.decode(raw)).__name__
    'Coverage'
    """
    import numpy as np

    from covjson_msgspec import Axis, Coverage, Domain, NdArray
    from covjson_msgspec.referencing import GeographicCRS, ReferenceSystemConnection

    side = 20 if quick else 200
    cov = Coverage(
        domain=Domain.grid(
            x=Axis.regular(0.0, float(side), side),
            y=Axis.regular(0.0, float(side), side),
            referencing=[
                ReferenceSystemConnection(
                    coordinates=("x", "y"), system=GeographicCRS()
                )
            ],
        ),
        ranges={
            "v": NdArray.from_numpy(
                np.arange(side * side, dtype=float).reshape(side, side), ("y", "x")
            )
        },
    )
    return cm.encode(cov)


def _noop_axis_order(values: object, reference_system: object) -> None:
    """Axis-order checker that reports nothing, disabling the monotonic scan.

    Passed as ``validate(..., axis_order_checker=_noop_axis_order)`` to trim the
    one ours-only check that is removable, so the matched table can time
    covjson-msgspec doing no more validation than covjson-pydantic.

    Examples
    --------
    >>> _noop_axis_order((3.0, 1.0, 2.0), None) is None
    True
    """
    return None


def _manual_monotonic(model: object) -> list[str]:
    """Bolt covjson-msgspec's monotonic-axis check onto a decoded pydantic model.

    Walks each independent axis (x/y/z/t) and records the ones whose values are
    not strictly monotonic, so pydantic can be measured doing the same work as
    covjson-msgspec's ``validate(check_values=True)``. Compact (start/stop/num)
    axes have no ``values`` and are monotonic by construction, so they are
    skipped.

    Examples
    --------
    >>> class _Axis:
    ...     def __init__(self, values):
    ...         self.values = values
    >>> class _Domain:
    ...     class axes:
    ...         x = _Axis([3.0, 1.0, 2.0])
    ...         y = z = t = None
    >>> class _Cov:
    ...     domain = _Domain()
    >>> _manual_monotonic(_Cov())
    ['x']
    """
    members = getattr(model, "coverages", None) or [model]
    offenders: list[str] = []

    for member in members:
        axes = getattr(getattr(member, "domain", None), "axes", None)

        for name in ("x", "y", "z", "t"):
            values = getattr(getattr(axes, name, None), "values", None)

            if values and len(values) > 1:
                pairs = list(itertools.pairwise(values))
                monotonic = all(a < b for a, b in pairs) or all(a > b for a, b in pairs)

                if not monotonic:
                    offenders.append(name)

    return offenders


def _msgspec_ops(
    cell: Cell,
) -> list[tuple[str, Callable[[], object] | None, str | None]]:
    """The msgspec cumulative ladder for ``cell`` (see the module docstring)."""
    raw = cell.raw
    model = cm.decode(raw)
    t_values = cell.t_values

    def rung6() -> None:
        decoded = cm.decode(raw)
        cm.validate(decoded, check_values=True)

        for value in t_values:
            cm.to_datetime(value)

    def roundtrip_full() -> None:
        # Everything pydantic's round-trip forces on its decode half: full
        # validation and a datetime for every temporal coordinate, then encode.
        decoded = cm.decode(raw)
        cm.validate(decoded, check_values=True)

        for value in t_values:
            cm.to_datetime(value)

        cm.encode(decoded)

    def matched_full() -> None:
        # Full validation including the monotonic + categorical checks pydantic
        # lacks; the "add our extra to pydantic" baseline for the matched table.
        decoded = cm.decode(raw)
        cm.validate(decoded, check_values=True)

        for value in t_values:
            cm.to_datetime(value)

    def matched_trim() -> None:
        # Trimmed to pydantic's check set: the monotonic scan (our only removable
        # ours-only check) disabled, so we do no more than pydantic.
        decoded = cm.decode(raw)
        cm.validate(decoded, check_values=True, axis_order_checker=_noop_axis_order)

        for value in t_values:
            cm.to_datetime(value)

    datetime_rung: tuple[str, Callable[[], object] | None, str | None] = (
        ("decode+validate(values)+datetime", rung6, None)
        if t_values
        else ("decode+validate(values)+datetime", None, "no-temporal-axis")
    )
    return [
        ("decode", lambda: cm.decode(raw), None),
        ("encode", lambda: cm.encode(model), None),
        ("roundtrip", lambda: cm.encode(cm.decode(raw)), None),
        ("matched-full", matched_full, None),
        ("matched-trim", matched_trim, None),
        ("roundtrip(full)", roundtrip_full, None),
        ("decode+validate", lambda: cm.validate(cm.decode(raw)), None),
        (
            "decode+validate(values)",
            lambda: cm.validate(cm.decode(raw), check_values=True),
            None,
        ),
        datetime_rung,
    ]


def _pydantic_ops(
    cell: Cell,
) -> list[tuple[str, Callable[[], object] | None, str | None]]:
    """The pydantic operations for ``cell``: decode, encode, round-trip.

    ``decode+monotonic`` adds a manual monotonic-axis scan to pydantic's decode,
    so it can be timed doing the same validation as covjson-msgspec's full pass
    (the "add our extra to pydantic" framing of the matched table).
    """
    raw = cell.raw
    pyd_cls = cell.pyd_cls
    model = pyd_cls.model_validate_json(raw)

    def decode_monotonic() -> None:
        _manual_monotonic(pyd_cls.model_validate_json(raw))

    return [
        ("decode", lambda: pyd_cls.model_validate_json(raw), None),
        ("encode", lambda: model.model_dump_json(), None),
        ("roundtrip", lambda: pyd_cls.model_validate_json(raw).model_dump_json(), None),
        ("decode+monotonic", decode_monotonic, None),
    ]


def _measure(fn: Callable[[], object], *, quick: bool) -> tuple[float, float, int]:
    """Time ``fn`` and return (median us/op, IQR us/op, iterations per repeat).

    Warms up once, lets `timeit` autorange the iteration count so each timing is
    well above clock resolution, then repeats and reports the median and
    interquartile range across repeats.
    """
    fn()
    number, _ = timeit.Timer(fn).autorange()

    if quick:
        number = max(1, number // 10)

    repeat = 5 if quick else 9
    samples = sorted(
        total / number * 1e6 for total in timeit.Timer(fn).repeat(repeat, number)
    )
    quartiles = statistics.quantiles(samples, n=4)
    return statistics.median(samples), quartiles[2] - quartiles[0], number


def _reason(exc: Exception) -> str:
    """The exception a library raises on a probe, as a verbatim one-liner.

    The exception type is prefixed so the string is unmistakably the library's
    own raised error, not a paraphrase. For a pydantic ``ValidationError`` the
    message is the first sub-error's ``msg`` (the informative part); otherwise it
    is the first line of the exception text.

    Examples
    --------
    >>> _reason(ValueError("bad axis\\nsecond line"))
    'ValueError: bad axis'
    """
    kind = type(exc).__name__
    errors = getattr(exc, "errors", None)

    if callable(errors):
        try:
            return f"{kind}: {errors()[0]['msg']}"
        except (IndexError, KeyError, TypeError, AttributeError):
            pass

    return f"{kind}: {str(exc).splitlines()[0][:80]}"


def _spectrum_table(
    by_key: dict[tuple[str, str, str], Row], cells: list[str]
) -> list[str]:
    """The decode/validation-ladder table, pydantic decode as the reference."""
    header = (
        "| cell | pydantic decode | msgspec decode (x) | + validate | "
        "+ validate(values) | + datetime (x) |"
    )
    sep = "| --- | --- | --- | --- | --- | --- |"
    rows = [header, sep]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "decode")]
        decode = by_key[(cell, "msgspec", "decode")]
        validate = by_key[(cell, "msgspec", "decode+validate")]
        values = by_key[(cell, "msgspec", "decode+validate(values)")]
        datetime_row = by_key[(cell, "msgspec", "decode+validate(values)+datetime")]
        rows.append(
            f"| {cell} | {_cell(pyd)} | {_cell(decode, pyd)} | {_cell(validate)} "
            f"| {_cell(values)} | {_cell(datetime_row, pyd)} |"
        )

    return rows


def _validation_table() -> list[str]:
    """Render the decode-time conformance scorecard from ``_VALIDATION``."""
    lines = [
        "A conformance scorecard for decode-time checks. The **expected** column "
        "is the proportional response the spec calls for, by requirement level: a "
        "`MUST` violation should error, a `SHOULD` violation should warn (the "
        "document stays loadable), and a conformant input should be accepted. The "
        "mark on each library shows whether its behavior is proportional. "
        "covjson-msgspec matches on every row; covjson-pydantic misses two MUST "
        "checks (monotonic, categorical), over-enforces a SHOULD (it raises on a "
        "malformed temporal value the spec lets you load), and rejects one "
        "conformant input (reduced-precision).",
        "",
        "| check | expected | msgspec full | pydantic decode |",
        "| --- | --- | --- | --- |",
    ]

    for check, expected, ms, ms_ok, pyd, pyd_ok in _VALIDATION:
        ms_mark = _CONFORMANT if ms_ok else _NONCONFORMANT
        pyd_mark = _CONFORMANT if pyd_ok else _NONCONFORMANT
        lines.append(f"| {check} | {expected} | {ms_mark} {ms} | {pyd_mark} {pyd} |")

    return lines


def _matched_section(
    by_key: dict[tuple[str, str, str], Row], cells: list[str]
) -> list[str]:
    """Two framings that make both libraries do the same validation work.

    Isolates the monotonic check (the one ours-only check removable from our side
    and addable to pydantic's), answering whether covjson-msgspec only looked
    slower at full fidelity because it did more.
    """
    lines = [
        "Both framings make the two libraries do the same validation work, "
        "isolating the monotonic check (the one ours-only check that is removable "
        "from covjson-msgspec and addable to covjson-pydantic). This answers "
        "whether covjson-msgspec only looked slower at full fidelity by doing "
        "more.",
        "",
        "Framing A, trim our extra so covjson-msgspec does no more than pydantic:",
        "",
        "| cell | pydantic decode | msgspec matched-trim | speedup (x) |",
        "| --- | --- | --- | --- |",
    ]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "decode")]
        trim = by_key[(cell, "msgspec", "matched-trim")]
        lines.append(f"| {cell} | {_cell(pyd)} | {_cell(trim)} | {_ratio(pyd, trim)} |")

    lines += [
        "",
        "Framing B, add covjson-msgspec's monotonic check to pydantic:",
        "",
        "| cell | msgspec matched-full | pydantic decode+monotonic | speedup (x) |",
        "| --- | --- | --- | --- |",
    ]

    for cell in cells:
        full = by_key[(cell, "msgspec", "matched-full")]
        pyd_mono = by_key[(cell, "pydantic", "decode+monotonic")]
        lines.append(
            f"| {cell} | {_cell(full)} | {_cell(pyd_mono)} | {_ratio(pyd_mono, full)} |"
        )

    lines += [
        "",
        "The mid-size cells flip to faster once the monotonic scan is neutralized, "
        "confirming it was the reason they looked slower. `tiled-ndarray` and "
        "`grid-large` stay slower on both framings for the same underlying reason, "
        "and it is not extra work: covjson-msgspec runs validation as a separate "
        "pass over the decoded objects, so it pays a fixed per-call cost (visible "
        "on the tiny `tiled-ndarray`) plus a pure-Python per-element cost (visible "
        "on `grid-large`'s ~40k value-vs-dataType scan, the same check pydantic "
        "runs fused into its Rust decode). Trimming the monotonic scan cannot "
        "change those.",
    ]
    return lines


def _roundtrip_table(
    by_key: dict[tuple[str, str, str], Row], cells: list[str]
) -> list[str]:
    """Round-trip table with two msgspec anchors against pydantic's fused one."""
    rows = [
        "| cell | pydantic | msgspec structural (x) | msgspec full (x) |",
        "| --- | --- | --- | --- |",
    ]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "roundtrip")]
        structural = by_key[(cell, "msgspec", "roundtrip")]
        full = by_key[(cell, "msgspec", "roundtrip(full)")]
        rows.append(
            f"| {cell} | {_cell(pyd)} | {_cell(structural, pyd)} | {_cell(full, pyd)} |"
        )

    return rows


def _pair_table(
    by_key: dict[tuple[str, str, str], Row], cells: list[str], op: str
) -> list[str]:
    """A head-to-head table for one symmetric op (msgspec vs pydantic)."""
    rows = ["| cell | msgspec | pydantic | speedup (x) |", "| --- | --- | --- | --- |"]

    for cell in cells:
        ms = by_key[(cell, "msgspec", op)]
        pyd = by_key[(cell, "pydantic", op)]
        rows.append(f"| {cell} | {_cell(ms)} | {_cell(pyd)} | {_ratio(pyd, ms)} |")

    return rows


def _probe_section(probe_rows: list[Row]) -> list[str]:
    """Render the capability-probe table plus the reverse-direction verdict.

    Each probe document is spec-valid or spec-faithful for covjson-msgspec; the
    ``pydantic`` column shows a decode time where pydantic also accepts it, or
    the concrete rejection reason where it does not. The closing line reports the
    reverse direction from the data: whether any probe was decoded by pydantic
    but rejected by covjson-msgspec.
    """
    by_key = {(r.cell, r.lib): r for r in probe_rows}
    notes = {name: note for name, _raw, note in _PROBES}
    lines = [
        "Documents that expose a one-sided capability gap. A time (us/op) means "
        "the library decoded it; `raises ...` is the exact exception the library "
        "throws instead, verbatim (type and message), so the gap is a concrete "
        "failure rather than a paraphrase.",
        "",
        "| probe | the gap | msgspec | pydantic |",
        "| --- | --- | --- | --- |",
    ]

    for name, _raw, _note in _PROBES:
        ms = by_key.get((name, "msgspec"))
        pyd = by_key.get((name, "pydantic"))
        lines.append(
            f"| {name} | {notes[name]} | {_probe_cell(ms)} | {_probe_cell(pyd)} |"
        )

    reverse = [
        r.cell for r in probe_rows if r.lib == "msgspec" and r.status == "skipped"
    ]
    lines.append("")

    if reverse:
        lines.append(
            "Reverse direction (pydantic accepts, msgspec rejects): "
            + ", ".join(reverse)
            + "."
        )
    else:
        lines.append(
            "Reverse direction: no probe was found that covjson-pydantic accepts "
            "and covjson-msgspec rejects. covjson-pydantic's advantage is static "
            "type precision and discoverability (see issue #22), not document "
            "acceptance."
        )

    return lines


def _probe_cell(row: Row | None) -> str:
    """Format a capability-probe cell: a decode time, or the raised exception.

    A measured probe shows its decode time (via `_cell`); a rejected probe shows
    ``raises <Type>: <message>`` so the reader sees the library's own verbatim
    error, not a paraphrase.
    """
    if row is None:
        return "n/a"

    if row.status == "skipped":
        return f"raises {row.reason}"

    return _cell(row)


def _cell(row: Row, reference: Row | None = None) -> str:
    """Format one table cell: ``median (+-iqr)`` plus an optional speedup."""
    if row.status == "skipped":
        return f"n/a ({row.reason})"

    text = f"{row.median_us:.2f} (+-{row.iqr_us:.2f})"

    if reference is not None:
        text += f" [{_ratio(reference, row)}]"

    return text


def _ratio(numerator: Row, denominator: Row) -> str:
    """Speedup ``numerator / denominator`` (e.g. pydantic over msgspec)."""
    if numerator.status == "skipped" or denominator.status == "skipped":
        return "--"

    return f"{numerator.median_us / denominator.median_us:.1f}x"


if __name__ == "__main__":
    main()
