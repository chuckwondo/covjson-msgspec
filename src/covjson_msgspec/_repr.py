"""Rich HTML reprs for the CoverageJSON models (Jupyter ``_repr_html_``).

Each displayable struct (`Coverage`, `CoverageCollection`, `Domain`, `NdArray`,
`TiledNdArray`, `Parameter`) carries a thin ``_repr_html_`` that delegates to the
matching builder here, so a notebook renders a compact summary card instead of
the raw ``repr``. The builders are pure Python with no third-party dependency:
they emit a self-contained ``<div>`` with a scoped ``<style>`` block and native
``<details>`` sections (collapsible without any JavaScript). Every piece of
dynamic text is HTML-escaped.

This is presentation only: it never decodes, fetches, or materializes large data
(a regular axis is summarized from its ``start``/``stop``/``num``, and value
previews are truncated), so a repr is cheap even for a big coverage.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from covjson_msgspec._i18n import display

if TYPE_CHECKING:
    from covjson_msgspec.axis import Axis
    from covjson_msgspec.coverage import Coverage, CoverageCollection
    from covjson_msgspec.domain import Domain
    from covjson_msgspec.parameter import Parameter, Unit
    from covjson_msgspec.range import NdArray, TiledNdArray

# Scoped once under ``.cj-repr`` so it cannot leak into the surrounding notebook.
# Colors lean on ``currentColor`` (with low-opacity borders) so the card reads in
# both light and dark themes without hardcoding a palette.
_STYLE = """<style>
.cj-repr { font-family: sans-serif; font-size: 0.85em; line-height: 1.4; }
.cj-repr .cj-kind { font-weight: 700; }
.cj-repr .cj-id { opacity: 0.6; margin-left: 0.5em; }
.cj-repr table { border-collapse: collapse; margin: 0.2em 0 0.4em 1em; }
.cj-repr th, .cj-repr td {
  text-align: left; padding: 1px 12px 1px 0; vertical-align: top;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}
.cj-repr th { font-weight: 600; }
.cj-repr summary { cursor: pointer; font-weight: 600; margin-top: 0.2em; }
.cj-repr .cj-empty { opacity: 0.6; font-style: italic; }
</style>"""

# How many values a preview shows before it elides the middle.
_PREVIEW_LIMIT = 6

# Rendered for a missing or empty cell, in place of a blank.
_EMPTY = '<span class="cj-empty">(none)</span>'


def coverage_html(coverage: Coverage) -> str:
    """Render a `Coverage` as an HTML summary card.

    Sections cover the domain's axes, the parameters (when the coverage carries
    its own, rather than inheriting them from a collection), and the ranges. A
    URL-reference domain (a bare string instead of an inline `Domain`) is shown
    as its URL with no axes table.

    Parameters
    ----------
    coverage
        The coverage to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> coverage_html(cov).startswith('<div class="cj-repr">')
    True
    """
    summary = [
        ("Domain type", coverage.effective_domain_type or "?"),
        ("Parameters", str(len(coverage.parameters or {}))),
        ("Ranges", str(len(coverage.ranges))),
    ]
    sections: list[str] = []

    if isinstance(domain := coverage.domain, str):
        sections.append(_section("Domain", _kv_table([("reference", domain)])))
    else:
        sections.append(_axis_section(domain))

    if coverage.parameters:
        sections.append(_parameter_section(coverage.parameters))

    sections.append(_range_section(coverage.ranges))

    return _document("Coverage", coverage.id, summary, sections)


def collection_html(collection: CoverageCollection) -> str:
    """Render a `CoverageCollection` as an HTML summary card.

    Shows the collection-level fields (shared ``domain_type`` and parameters)
    and a per-member table of each coverage's effective domain type.

    Parameters
    ----------
    collection
        The collection to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, CoverageCollection, Domain
    >>> member = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={},
    ... )
    >>> coll = CoverageCollection(coverages=(member,), domain_type="Point")
    >>> "Coverages" in collection_html(coll)
    True
    """
    summary = [
        ("Coverages", str(len(collection.coverages))),
        ("Domain type", collection.domain_type or "?"),
        ("Parameters", str(len(collection.parameters or {}))),
    ]

    sections: list[str] = []

    if collection.parameters:
        sections.append(_parameter_section(collection.parameters))

    member_rows = [
        [str(i), member.effective_domain_type or "?", member.id or ""]
        for i, member in enumerate(collection.coverages)
    ]
    sections.append(_table_section("Members", ["#", "Domain type", "id"], member_rows))

    return _document("CoverageCollection", None, summary, sections)


def domain_html(domain: Domain) -> str:
    """Render a `Domain` as an HTML summary card.

    Parameters
    ----------
    domain
        The domain to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.listed((0.0, 1.0)))
    >>> "Axes" in domain_html(dom)
    True
    """
    summary = [
        ("Domain type", domain.domain_type or "?"),
        ("Axes", str(len(domain.axes))),
    ]

    return _document("Domain", None, summary, [_axis_section(domain)])


def ndarray_html(array: NdArray) -> str:
    """Render an `NdArray` as an HTML summary card with a truncated value preview.

    Parameters
    ----------
    array
        The array to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import NdArray
    >>> arr = NdArray(
    ...     data_type="float", values=(1.0, 2.0), shape=(2,), axis_names=("x",)
    ... )
    >>> "NdArray" in ndarray_html(arr)
    True
    """
    count = len(array.values)
    preview = _value_preview(array.values)
    summary = [
        ("Data type", array.data_type),
        ("Shape", _shape_text(array.shape)),
        ("Axis names", ", ".join(array.axis_names)),
        ("Values", f"{count} ({preview})" if preview else str(count)),
    ]

    return _document("NdArray", None, summary, [])


def tiled_ndarray_html(array: TiledNdArray) -> str:
    """Render a `TiledNdArray` as an HTML summary card listing its tile sets.

    Each tile set row reports its tile shape, the number of tiles that covers
    the full array, and the URL template the tiles are fetched from.

    Parameters
    ----------
    array
        The tiled array to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import TiledNdArray, TileSet
    >>> tiled = TiledNdArray(
    ...     data_type="float",
    ...     axis_names=("x",),
    ...     shape=(2,),
    ...     tile_sets=(TileSet(tile_shape=(1,), url_template="{x}.covjson"),),
    ... )
    >>> "TiledNdArray" in tiled_ndarray_html(tiled)
    True
    """
    from covjson_msgspec.range import tile_count

    summary = [
        ("Data type", array.data_type),
        ("Shape", _shape_text(array.shape)),
        ("Axis names", ", ".join(array.axis_names)),
        ("Tile sets", str(len(array.tile_sets))),
    ]

    rows = [
        [
            _shape_text(tile_set.tile_shape),
            str(tile_count(array.shape, tile_set.tile_shape)),
            tile_set.url_template,
        ]
        for tile_set in array.tile_sets
    ]
    section = _table_section("Tile sets", ["Tile shape", "Tiles", "URL template"], rows)

    return _document("TiledNdArray", None, summary, [section])


def parameter_html(parameter: Parameter) -> str:
    """Render a `Parameter` as an HTML summary card.

    Shows whether the parameter is continuous or categorical, its observed
    property label, and either its unit (continuous) or its categories
    (categorical).

    Parameters
    ----------
    parameter
        The parameter to render.

    Returns
    -------
    str
        A self-contained HTML fragment.

    Examples
    --------
    >>> from covjson_msgspec import ObservedProperty, Parameter, Unit, i18n
    >>> param = Parameter.continuous(
    ...     ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    ... )
    >>> "Air temperature" in parameter_html(param)
    True
    """
    observed = parameter.observed_property
    categories = observed.categories
    summary = [
        ("Kind", "categorical" if categories is not None else "continuous"),
        ("Observed property", display(observed.label) or "?"),
    ]

    sections: list[str] = []

    if categories is not None:
        summary.append(("Categories", str(len(categories))))
        rows = [[category.id, display(category.label)] for category in categories]
        sections.append(_table_section("Categories", ["id", "Label"], rows))
    else:
        summary.append(("Unit", _unit_text(parameter.unit)))

    return _document("Parameter", parameter.id, summary, sections)


# --- internals -------------------------------------------------------------


def _document(
    kind: str,
    id_: str | None,
    summary: list[tuple[str, str]],
    sections: list[str],
) -> str:
    """Wrap a header, a summary key/value table, and sections into the card.

    Parameters
    ----------
    kind
        The struct's display name (e.g. ``"Coverage"``).
    id_
        The object's ``id``, shown faintly beside the kind, or ``None``.
    summary
        Raw key/value pairs for the top summary table (escaped on render by
        `_kv_table`).
    sections
        Pre-rendered ``<details>`` section fragments.

    Returns
    -------
    str
        The full ``<div class="cj-repr">`` fragment, style block included.

    Examples
    --------
    >>> out = _document("Domain", None, [("Axes", "0")], [])
    >>> out.startswith('<div class="cj-repr">') and "Domain" in out
    True
    """
    id_html = f'<span class="cj-id">{_escape(id_)}</span>' if id_ else ""
    header = f'<div><span class="cj-kind">{_escape(kind)}</span>{id_html}</div>'
    body = "".join([header, _kv_table(summary), *sections])
    return f'<div class="cj-repr">{_STYLE}{body}</div>'


def _section(title: str, body: str) -> str:
    """Render a collapsible ``<details>`` section, open by default.

    Parameters
    ----------
    title
        The section heading (shown in the ``<summary>``); escaped here.
    body
        Pre-rendered HTML for the section's contents.

    Returns
    -------
    str
        A ``<details open>`` fragment.

    Examples
    --------
    >>> _section("Axes", "<table></table>")
    '<details open><summary>Axes</summary><table></table></details>'
    """
    return f"<details open><summary>{_escape(title)}</summary>{body}</details>"


def _cell(value: str) -> str:
    """Render one table-cell value: escaped, or a placeholder when empty.

    Every value reaching a table passes through here, so escaping happens in one
    place and cannot be forgotten: arbitrary text (a string range's values, a
    parameter label, a URL) can never inject markup. Builders therefore hand this
    *raw* text, never pre-escaped text (which would double-escape).

    Parameters
    ----------
    value
        The raw cell text. An empty string renders the faint "(none)"
        placeholder instead of a blank cell.

    Returns
    -------
    str
        The HTML-escaped value, or the placeholder span.

    Examples
    --------
    >>> _cell("a & b")
    'a &amp; b'
    >>> _cell("<script>")
    '&lt;script&gt;'
    >>> _cell("")
    '<span class="cj-empty">(none)</span>'
    """
    return _escape(value) if value else _EMPTY


def _kv_table(rows: list[tuple[str, str]]) -> str:
    """Render raw key/value pairs as a two-column table, escaping on render.

    Parameters
    ----------
    rows
        ``(key, value)`` pairs of raw text; both are escaped here (the value via
        `_cell`, so an empty value shows the "(none)" placeholder).

    Returns
    -------
    str
        A ``<table>`` with the key in a ``<th>`` and the value in a ``<td>``.

    Examples
    --------
    >>> _kv_table([("Shape", "(2,)")])
    '<table><tr><th>Shape</th><td>(2,)</td></tr></table>'
    """
    cells = "".join(
        f"<tr><th>{_escape(key)}</th><td>{_cell(value)}</td></tr>"
        for key, value in rows
    )

    return f"<table>{cells}</table>"


def _grid_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a multi-column table with a header row, escaping on render.

    Parameters
    ----------
    headers
        Column headings; escaped here.
    rows
        Each row's raw-text cells; escaped here via `_cell`. An empty ``rows``
        yields a faint "(none)" placeholder instead of a header-only table.

    Returns
    -------
    str
        A ``<table>`` fragment.

    Examples
    --------
    >>> _grid_table(["a"], [["1"], ["2"]])
    '<table><tr><th>a</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>'
    >>> _grid_table(["a"], [])
    '<span class="cj-empty">(none)</span>'
    """
    if not rows:
        return _EMPTY

    head = "<tr>" + "".join(f"<th>{_escape(h)}</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )

    return f"<table>{head}{body}</table>"


def _table_section(title: str, headers: list[str], rows: list[list[str]]) -> str:
    """Render a collapsible section titled ``title (n)`` over a grid table.

    The count in the title is ``len(rows)``, so it cannot drift from the table's
    contents.

    Parameters
    ----------
    title
        The section heading, shown before the row count.
    headers
        The grid's column headings.
    rows
        The grid's raw-text rows.

    Returns
    -------
    str
        A ``<details>`` section whose summary is ``title (len(rows))``.

    Examples
    --------
    >>> _table_section("Axes", ["Axis"], [["x"], ["y"]])
    '<details open><summary>Axes (2)</summary><table>...</table></details>'
    """
    return _section(f"{title} ({len(rows)})", _grid_table(headers, rows))


def _axis_section(domain: Domain) -> str:
    """Render a domain's axes as a collapsible table of name/length/extent.

    Parameters
    ----------
    domain
        The domain whose ``axes`` are summarized.

    Returns
    -------
    str
        A ``<details>`` section titled ``Axes (n)``.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.listed((0.0, 1.0)))
    >>> "<summary>Axes (2)</summary>" in _axis_section(dom)
    True
    """
    rows = [
        [name, str(len(axis)), _axis_detail(axis)] for name, axis in domain.axes.items()
    ]

    return _table_section("Axes", ["Axis", "Length", "Extent"], rows)


def _parameter_section(parameters: dict[str, Parameter]) -> str:
    """Render a mapping of parameters as a collapsible key/label/unit table.

    Parameters
    ----------
    parameters
        The ``key -> Parameter`` mapping from a coverage or collection.

    Returns
    -------
    str
        A ``<details>`` section titled ``Parameters (n)``.

    Examples
    --------
    >>> from covjson_msgspec import ObservedProperty, Parameter, Unit, i18n
    >>> params = {
    ...     "t": Parameter.continuous(
    ...         ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    ...     )
    ... }
    >>> "Air temperature" in _parameter_section(params)
    True
    """
    rows = [
        [
            key,
            display(parameter.observed_property.label),
            _unit_text(parameter.unit),
        ]
        for key, parameter in parameters.items()
    ]

    return _table_section("Parameters", ["Key", "Observed property", "Unit"], rows)


def _range_section(ranges: dict[str, NdArray | TiledNdArray | str]) -> str:
    """Render a coverage's ranges as a collapsible key/type/shape table.

    Parameters
    ----------
    ranges
        The ``key -> range`` mapping, where a range is an `NdArray`, a
        `TiledNdArray`, or a bare URL string.

    Returns
    -------
    str
        A ``<details>`` section titled ``Ranges (n)``.

    Examples
    --------
    >>> from covjson_msgspec import NdArray
    >>> ranges = {"t": NdArray(data_type="float", values=(1.0,))}
    >>> "<summary>Ranges (1)</summary>" in _range_section(ranges)
    True
    """
    rows = [[key, *_range_summary(value)] for key, value in ranges.items()]

    return _table_section("Ranges", ["Key", "Type", "Data type", "Shape"], rows)


def _range_summary(value: NdArray | TiledNdArray | str) -> list[str]:
    """Summarize one range as raw ``[type, data_type, shape]`` cells.

    Parameters
    ----------
    value
        A range: an `NdArray`, a `TiledNdArray`, or a bare URL string.

    Returns
    -------
    list of str
        Three raw-text cells (escaped on render by the table builder). A
        URL-string range is labeled ``reference``, carries its URL in the
        data-type cell, and has an empty shape cell.

    Examples
    --------
    >>> from covjson_msgspec import NdArray
    >>> _range_summary(NdArray(data_type="float", values=(1.0,), shape=(1,)))
    ['NdArray', 'float', '(1,)']
    >>> _range_summary("https://example.org/r.json")
    ['reference', 'https://example.org/r.json', '']
    """
    if isinstance(value, str):
        return ["reference", value, ""]

    return [type(value).__name__, value.data_type, _shape_text(value.shape)]


def _axis_detail(axis: Axis) -> str:
    """Describe an axis's coordinate extent in one short line.

    Parameters
    ----------
    axis
        The axis to describe.

    Returns
    -------
    str
        For a composite axis, its ``dataType`` and coordinate names; for a
        regular axis, ``start`` to ``stop``; otherwise a truncated value preview.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> _axis_detail(Axis.regular(0.0, 10.0, 5))
    '0.0 to 10.0'
    >>> _axis_detail(Axis.listed((1, 2, 3)))
    '1, 2, 3'
    >>> _axis_detail(Axis.tuple_([("t", 1.0, 2.0)], coordinates=("t", "x", "y")))
    'tuple (t, x, y)'
    """
    if axis.data_type in ("tuple", "polygon"):
        coordinates = ", ".join(axis.coordinates or ())
        return f"{axis.data_type} ({coordinates})" if coordinates else axis.data_type

    if axis.values is None:
        return f"{axis.start} to {axis.stop}"

    return _value_preview(axis.values)


def _value_preview(values: tuple[object, ...]) -> str:
    """Format a flat sequence, eliding the middle past ``_PREVIEW_LIMIT``.

    Parameters
    ----------
    values
        The values to preview. ``None`` renders as ``null``.

    Returns
    -------
    str
        A comma-separated preview; long sequences show the head and the last
        value with an ellipsis between.

    Examples
    --------
    >>> _value_preview((1, 2, 3))
    '1, 2, 3'
    >>> _value_preview((None, 1.5))
    'null, 1.5'
    >>> _value_preview(tuple(range(10)))
    '0, 1, 2, 3, 4, ..., 9'
    """
    if len(values) <= _PREVIEW_LIMIT:
        return ", ".join(_format_value(item) for item in values)

    head = ", ".join(_format_value(item) for item in values[: _PREVIEW_LIMIT - 1])
    return f"{head}, ..., {_format_value(values[-1])}"


def _format_value(value: object) -> str:
    """Format one scalar value for display, mapping ``None`` to ``null``.

    Parameters
    ----------
    value
        The value to format.

    Returns
    -------
    str
        ``"null"`` for ``None``, otherwise ``str(value)``.

    Examples
    --------
    >>> _format_value(None)
    'null'
    >>> _format_value(1.5)
    '1.5'
    """
    return "null" if value is None else str(value)


def _shape_text(shape: tuple[int | None, ...]) -> str:
    """Render a shape tuple, naming the empty (scalar) shape.

    Parameters
    ----------
    shape
        An array shape, or a tile shape (whose entries may be ``None`` for an
        un-subdivided axis).

    Returns
    -------
    str
        ``"scalar"`` for the empty shape, else the tuple's ``repr``.

    Examples
    --------
    >>> _shape_text((2, 3))
    '(2, 3)'
    >>> _shape_text((1, None))
    '(1, None)'
    >>> _shape_text(())
    'scalar'
    """
    return str(shape) if shape else "scalar"


def _unit_text(unit: Unit | None) -> str:
    """Describe a `Unit` in one line: its symbol, else its label.

    Parameters
    ----------
    unit
        A `Unit`, or ``None`` (a dimensionless or categorical parameter).

    Returns
    -------
    str
        The unit's symbol (the bare string, or a `Symbol`'s ``value``), else its
        label; ``""`` when there is no unit.

    Examples
    --------
    >>> from covjson_msgspec import Symbol, Unit, i18n
    >>> _unit_text(Unit(symbol="K"))
    'K'
    >>> _unit_text(Unit(symbol=Symbol(value="Cel", type_="http://ex/Cel")))
    'Cel'
    >>> _unit_text(Unit(label=i18n("kelvin")))  # no symbol: falls back to the label
    'kelvin'
    >>> _unit_text(None)
    ''
    """
    if unit is None:
        return ""

    if unit.symbol is not None:
        # ``symbol`` is a bare string or a Symbol (which carries the text in
        # ``value``). getattr, rather than isinstance, keeps this module free of
        # a runtime import of the model types (all are TYPE_CHECKING-only).
        return str(getattr(unit.symbol, "value", unit.symbol))

    return display(unit.label)


def _escape(value: object) -> str:
    """HTML-escape any value's string form for safe embedding.

    Parameters
    ----------
    value
        The value to stringify and escape.

    Returns
    -------
    str
        ``str(value)`` with ``&``, ``<``, ``>``, and quotes escaped.

    Examples
    --------
    >>> _escape("<script>")
    '&lt;script&gt;'
    >>> _escape(3.5)
    '3.5'
    """
    return html.escape(str(value), quote=True)
