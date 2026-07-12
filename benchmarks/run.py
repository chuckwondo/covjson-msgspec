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
import contextlib
import functools
import itertools
import json
import pathlib
import platform
import re
import statistics
import timeit
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, cast

import covjson_msgspec as cm

if TYPE_CHECKING:
    from pydantic import BaseModel

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
    "msgspec:decode": (
        "structural decode only: builds the typed model and leaves every temporal "
        "value as its raw ISO 8601 string"
    ),
    "msgspec:encode": "serialize the model back to CoverageJSON bytes",
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
    "pydantic:encode": "model_dump_json: serialize the model to JSON bytes",
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


# Visual marks. A green check / red cross for the conformance scorecard and the
# spec-compliance column; a warning on covjson-pydantic's own time where it skips
# a MUST check (so the number is not like-for-like); and a direction arrow after
# each speedup. GitHub markdown offers no portable text color or cell shading, so
# the arrow's shape (faster / slower / within a rounded 1.0x), not a color, is the
# signal, which also keeps it legible for colorblind readers.
_CONFORMANT = "✅"
_NONCONFORMANT = "❌"
_WARNING = "⚠️"
_FASTER = "⬆️"
_SLOWER = "⬇️"
_EVEN = "🟰"


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
        "tile set consistency (shape, url template)",
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
    pyd_cls: type[BaseModel]
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
    """Measure and write ``results.json``, then render ``results.md``.

    ``--render-only`` skips measurement and re-renders ``results.md`` from the
    committed ``results.json`` and template, so a prose edit costs a second, not a
    full run.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="fast smoke run (smaller synthetic doc, fewer iterations)",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="re-render results.md from the existing results.json, without measuring",
    )
    args = parser.parse_args()

    if args.render_only:
        payload: dict[str, Any] = json.loads((_OUT / "results.json").read_text())
        (_OUT / "results.md").write_text(render(payload))
        print(f"re-rendered {_OUT / 'results.md'} from {_OUT / 'results.json'}")

        return

    cells = build_cells(quick=args.quick)
    rows = [row for cell in cells for row in measure_cell(cell, quick=args.quick)]
    probe_rows = measure_probes(quick=args.quick)

    payload = {
        "env": collect_env(),
        "versions": {name: version(name) for name in _PINNED},
        "semantics": _SEMANTICS,
        "cells": [_cell_stats(cell) for cell in cells],
        "results": [asdict(row) for row in rows],
        "probes": [asdict(row) for row in probe_rows],
        "probe_notes": {name: note for name, _raw, note in _PROBES},
    }
    (_OUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (_OUT / "results.md").write_text(render(payload))

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
    from covjson_pydantic.coverage import Coverage, CoverageCollection
    from covjson_pydantic.domain import Domain
    from covjson_pydantic.ndarray import TiledNdArray

    dispatch: dict[str, type[BaseModel]] = {
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
    from covjson_pydantic.coverage import Coverage, CoverageCollection
    from covjson_pydantic.domain import Domain
    from covjson_pydantic.ndarray import TiledNdArray

    dispatch: dict[str, type[BaseModel]] = {
        "Coverage": Coverage,
        "CoverageCollection": CoverageCollection,
        "TiledNdArray": TiledNdArray,
        "Domain": Domain,
    }
    rows: list[Row] = []

    for name, raw, _note in _PROBES:
        pyd_cls = dispatch.get(json.loads(raw)["type"])
        attempts: list[tuple[str, Callable[[], object]]] = [
            ("msgspec", functools.partial(cm.decode, raw))
        ]

        if pyd_cls is None:
            rows.append(
                Row(name, "pydantic", "decode", "skipped", reason="no pydantic model")
            )
        else:
            attempts.append(
                ("pydantic", functools.partial(pyd_cls.model_validate_json, raw))
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


_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(payload: dict[str, Any]) -> str:
    """Fill the ``results.template.md`` placeholders from a result ``payload``.

    The template holds all prose; every ``{{name}}`` token becomes a generated
    block built from the measured data. No interpretation is baked into the
    generator, so prose can be re-rendered from a committed ``results.json``
    without re-measuring (``run.py --render-only``).
    """
    rows = [Row(**row) for row in payload["results"]]
    probe_rows = [Row(**row) for row in payload["probes"]]
    by_key = {(r.cell, r.lib, r.op): r for r in rows}
    cells = list(dict.fromkeys(r.cell for r in rows))
    omits = {cell["name"]: cell["pydantic_omits"] for cell in payload["cells"]}

    blocks = {
        "environment": _environment_block(payload["env"]),
        "versions": [f"- {name}: {ver}" for name, ver in payload["versions"].items()],
        "document_set_table": _document_set_table(payload["cells"]),
        "decode_ladder_table": _spectrum_table(by_key, cells, omits),
        "encode_table": _pair_table(by_key, cells, "encode"),
        "roundtrip_table": _roundtrip_table(by_key, cells, omits),
        "validation_parity_table": _validation_table(),
        "matched_a_table": _matched_a_table(by_key, cells, omits),
        "matched_b_table": _matched_b_table(by_key, cells, omits),
        "capability_probes_table": _probe_section(probe_rows),
        "operations_glossary": _glossary(),
    }
    rendered = {name: "\n".join(lines) for name, lines in blocks.items()}

    def _fill(match: re.Match[str]) -> str:
        name = match.group(1)

        if name not in rendered:
            msg = f"template references unknown placeholder {{{{{name}}}}}"
            raise KeyError(msg)

        return rendered[name]

    return _PLACEHOLDER.sub(_fill, (_OUT / "results.template.md").read_text())


def _environment_block(env: dict[str, str]) -> list[str]:
    """The platform and interpreter lines for the environment section."""
    return [
        f"- platform: {env['platform']}",
        f"- python: {env['python']} ({env['implementation']}), {env['machine']}",
    ]


def _document_set_table(cells: list[dict[str, Any]]) -> list[str]:
    """Each cell's size, scan load, and covjson-pydantic's spec compliance."""
    lines = [
        "| cell | size (KB) | inline values | temporal coords "
        "| covjson-pydantic spec compliance |",
        "| :--- | ---: | ---: | ---: | :--- |",
    ]
    lines += [
        f"| {cell['name']} | {cell['bytes'] / 1024:.1f} | {cell['inline_values']:,} "
        f"| {cell['temporal_coords']} | {_compliance(cell['pydantic_omits'])} |"
        for cell in cells
    ]

    return lines


def _compliance(omits: list[str]) -> str:
    """A spec-compliance verdict for the document-set table.

    covjson-pydantic performs no monotonic-order or tile-set validation, so on a
    cell where either applies it accepts documents the spec says MUST be rejected,
    a conformance failure called out with a red cross.
    """
    if not omits:
        return f"{_CONFORMANT} compliant"

    return f"{_NONCONFORMANT} non-compliant: skips {', '.join(omits)}"


def _warn(
    cell: str, omits: dict[str, list[str]], *, ignoring: tuple[str, ...] = ()
) -> str:
    """A warning suffix for covjson-pydantic's own cell where it skips a MUST check.

    Placed on covjson-pydantic's column (not the row label) so the mark reads as
    "this time is not like-for-like: pydantic skips validation covjson-msgspec
    performs". ``ignoring`` drops checks a table already equalizes: the
    matched-work tables neutralize monotonic ordering, leaving only a residual
    tile-set omission.
    """
    flagged = [check for check in omits.get(cell, []) if check not in ignoring]

    return f" {_WARNING}" if flagged else ""


def _glossary() -> list[str]:
    """The operations glossary from ``_SEMANTICS``, grouped by library."""
    libraries = (("msgspec", "covjson-msgspec"), ("pydantic", "covjson-pydantic"))
    lines: list[str] = []

    for lib, label in libraries:
        prefix = f"{lib}:"

        if lines:
            lines.append("")

        lines += [f"**{label}**", ""]
        lines += [
            f"- `{key.removeprefix(prefix)}`: {desc}"
            for key, desc in _SEMANTICS.items()
            if key.startswith(prefix)
        ]

    return lines


def _make_cell(name: str, raw: bytes, dispatch: dict[str, type[BaseModel]]) -> Cell:
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


def _cell_stats(cell: Cell) -> dict[str, Any]:
    """Size and scan-load facts for one cell, for the document-set table.

    ``inline_values`` counts every range value the value scan would touch: a
    URL-referenced range (a plain string) or a tiled range keeps its values in
    another document, so it contributes none. ``temporal_coords`` is the number
    of time strings the datetime and lexical-form passes resolve.

    Examples
    --------
    >>> cells = {c.name: c for c in build_cells(quick=True)}
    >>> _cell_stats(cells["tiled-ndarray"])["inline_values"]
    0
    """
    doc = cm.decode(cell.raw)
    members = getattr(doc, "coverages", None) or [doc]
    inline_values = sum(
        len(range_.values)
        for member in members
        for range_ in getattr(member, "ranges", {}).values()
        if getattr(range_, "values", None) is not None
    )

    return {
        "name": cell.name,
        "bytes": len(cell.raw),
        "inline_values": inline_values,
        "temporal_coords": len(cell.t_values),
        "pydantic_omits": _pydantic_omits(doc, members),
    }


def _pydantic_omits(doc: object, members: Sequence[object]) -> list[str]:
    """The MUST-level checks covjson-pydantic omits that apply to this document.

    covjson-pydantic performs neither the monotonic-axis-order check (which
    applies wherever a domain lists a primitive, non-composite axis) nor tile-set
    consistency (it validates no ``TiledNdArray``). Naming them per cell lets the
    timing tables flag, with a warning marker, where a validation-rung comparison
    is not like-for-like: the extra work is spec conformance covjson-pydantic skips.

    Examples
    --------
    >>> cells = {c.name: c for c in build_cells(quick=True)}
    >>> _cell_stats(cells["tiled-ndarray"])["pydantic_omits"]
    ['tile-set consistency']
    >>> _cell_stats(cells["grid-large (synthetic)"])["pydantic_omits"]
    []
    """
    omits: list[str] = []

    if any(
        getattr(axis, "values", None) is not None
        and getattr(axis, "data_type", None) not in ("tuple", "polygon")
        for member in members
        for axis in getattr(getattr(member, "domain", None), "axes", {}).values()
    ):
        omits.append("monotonic axis order")

    if any(getattr(member, "tile_sets", None) for member in (doc, *members)):
        omits.append("tile-set consistency")

    return omits


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
                np.arange(side**2, dtype=float).reshape(side, side), ("y", "x")
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
        with contextlib.suppress(IndexError, KeyError, TypeError, AttributeError):
            details = cast("list[dict[str, Any]]", errors())

            return f"{kind}: {details[0]['msg']}"

    return f"{kind}: {str(exc).splitlines()[0][:80]}"


def _spectrum_table(
    by_key: dict[tuple[str, str, str], Row],
    cells: list[str],
    omits: dict[str, list[str]],
) -> list[str]:
    """The decode/validation-ladder table, pydantic decode as the reference."""
    header = (
        "| cell | pydantic decode (us) | msgspec decode (us, x) | "
        "+ validate (us, x) | + validate(values) (us, x) | + datetime (us, x) |"
    )
    sep = "| :--- | ---: | ---: | ---: | ---: | ---: |"
    rows = [header, sep]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "decode")]
        decode = by_key[(cell, "msgspec", "decode")]
        validate = by_key[(cell, "msgspec", "decode+validate")]
        values = by_key[(cell, "msgspec", "decode+validate(values)")]
        datetime_row = by_key[(cell, "msgspec", "decode+validate(values)+datetime")]

        # A cell with no temporal axis has no datetime rung; its full-fidelity
        # endpoint is the validate(values) rung, so report the final speedup
        # against that rung rather than leaving the last column without one.
        final = (
            f"{_cell(datetime_row)}<br>{_ratio(pyd, values)}"
            if datetime_row.status == "skipped"
            else _cell(datetime_row, pyd)
        )
        pyd_cell = f"{_cell(pyd)}{_warn(cell, omits)}"
        rows.append(
            f"| {cell} | {pyd_cell} | {_cell(decode, pyd)} | {_cell(validate, pyd)} "
            f"| {_cell(values, pyd)} | {final} |"
        )

    return rows


def _validation_table() -> list[str]:
    """The decode-time conformance scorecard table, rendered from ``_VALIDATION``."""
    lines = [
        "| check | expected | msgspec full | pydantic decode |",
        "| --- | --- | --- | --- |",
    ]

    for check, expected, ms, ms_ok, pyd, pyd_ok in _VALIDATION:
        ms_mark = _CONFORMANT if ms_ok else _NONCONFORMANT
        pyd_mark = _CONFORMANT if pyd_ok else _NONCONFORMANT
        lines.append(f"| {check} | {expected} | {ms_mark} {ms} | {pyd_mark} {pyd} |")

    return lines


def _matched_a_table(
    by_key: dict[tuple[str, str, str], Row],
    cells: list[str],
    omits: dict[str, list[str]],
) -> list[str]:
    """Framing A: covjson-msgspec trimmed to pydantic's check set."""
    lines = [
        "| cell | pydantic decode (us) | msgspec matched-trim (us) | speedup |",
        "| :--- | ---: | ---: | ---: |",
    ]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "decode")]
        trim = by_key[(cell, "msgspec", "matched-trim")]
        warn = _warn(cell, omits, ignoring=("monotonic axis order",))
        lines.append(
            f"| {cell} | {_cell(pyd)}{warn} | {_cell(trim)} | {_ratio(pyd, trim)} |"
        )

    return lines


def _matched_b_table(
    by_key: dict[tuple[str, str, str], Row],
    cells: list[str],
    omits: dict[str, list[str]],
) -> list[str]:
    """Framing B: covjson-msgspec's full validation, vs pydantic decode + monotonic."""
    lines = [
        "| cell | msgspec matched-full (us) | pydantic decode+monotonic (us) "
        "| speedup |",
        "| :--- | ---: | ---: | ---: |",
    ]

    for cell in cells:
        full = by_key[(cell, "msgspec", "matched-full")]
        pyd_mono = by_key[(cell, "pydantic", "decode+monotonic")]
        ratio = _ratio(pyd_mono, full)
        warn = _warn(cell, omits, ignoring=("monotonic axis order",))
        lines.append(f"| {cell} | {_cell(full)} | {_cell(pyd_mono)}{warn} | {ratio} |")

    return lines


def _roundtrip_table(
    by_key: dict[tuple[str, str, str], Row],
    cells: list[str],
    omits: dict[str, list[str]],
) -> list[str]:
    """Round-trip table with two msgspec anchors against pydantic's fused one."""
    rows = [
        "| cell | pydantic (us) | msgspec structural (us, x) | msgspec full (us, x) |",
        "| :--- | ---: | ---: | ---: |",
    ]

    for cell in cells:
        pyd = by_key[(cell, "pydantic", "roundtrip")]
        structural = by_key[(cell, "msgspec", "roundtrip")]
        full = by_key[(cell, "msgspec", "roundtrip(full)")]
        pyd_cell = f"{_cell(pyd)}{_warn(cell, omits)}"
        rows.append(
            f"| {cell} | {pyd_cell} | {_cell(structural, pyd)} | {_cell(full, pyd)} |"
        )

    return rows


def _pair_table(
    by_key: dict[tuple[str, str, str], Row], cells: list[str], op: str
) -> list[str]:
    """A head-to-head table for one symmetric op (msgspec vs pydantic)."""
    rows = [
        "| cell | msgspec (us) | pydantic (us) | speedup |",
        "| :--- | ---: | ---: | ---: |",
    ]

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

    return f"raises {row.reason}" if row.status == "skipped" else _cell(row)


def _cell(row: Row, reference: Row | None = None) -> str:
    """Format one table cell: ``median (+-iqr)`` plus an optional speedup."""
    if row.status == "skipped":
        return f"n/a ({row.reason})"

    text = f"{row.median_us:.2f} (+-{row.iqr_us:.2f})"

    if reference is not None:
        # A line break keeps the speedup off the median's line, so the column
        # stays narrow and the two read as distinct facts (no bracket needed).
        text += f"<br>{_ratio(reference, row)}"

    return text


def _ratio(numerator: Row, denominator: Row) -> str:
    """Speedup ``numerator / denominator`` (pydantic over msgspec).

    Where covjson-msgspec is faster (above a rounded 1.0x) the ratio is italic, its
    slant reading as forward motion; slower or within a rounded 1.0x it stays plain.
    A direction arrow follows it. Both cues are portable markdown, since GitHub
    offers no reliable text color or cell shading.
    """
    if numerator.median_us is None or denominator.median_us is None:
        return "--"

    ratio = numerator.median_us / denominator.median_us
    text = f"{ratio:.1f}x"

    if text == "1.0x":
        return f"{text} {_EVEN}"

    if ratio > 1:
        return f"*{text}* {_FASTER}"

    return f"{text} {_SLOWER}"


if __name__ == "__main__":
    main()
