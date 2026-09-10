"""Tile-set rules and ``urlTemplate`` grammar shared by `assemble` and `validate`.

Spec 6.3 constrains a TileSet in ways neither decode nor ``__post_init__``
enforces, so a decoded `TiledNdArray` may violate any of them. Two make a tiling
impossible to lay out at all: a positive tile size, without which no tile count
exists, and a ``urlTemplate`` naming only subdivided axes, without which a
variable has nothing to expand it with.

The duplicate-name rule, no two subdivided axes sharing a name whose tile
ordinals can differ, is not one of those: such a tiling *can* lay out. Assembly
reads it for a different reason. The substitution is keyed by axis name, so where
two subdivided axes share one the last wins, and whether the collision surfaces
as two slots on one URL depends on which of them comes last: the same defect
either collapses the URLs or expands to distinct ones, on an axis-order accident
rather than a reading of the document. Reading the rule makes the rejection
independent of that order, which waiting for the symptom cannot be.

The missing-variable rule, a subdivided axis whose variable the template omits,
is a spec MUST that only ``validate`` reports as such. Assembly notices it only
when *that axis* carries more than one tile, since only then do its tiles collide
on one URL; an axis subdivided into a single tile breaks the MUST while
assembling correctly. So that rule needs a consumer judging conformance, not one
judging whether a tiling can be laid out.

Spec 6.3's remaining shared rule, ``axisNames`` rank-matching ``shape``, is not
homed here: it is a length comparison, which carries no decision to drift, so
each consumer writes it where it reads it.

Every consumer reads the rules from here, so they cannot disagree:
[`assemble`][covjson_msgspec.TiledNdArray.assemble] turns each into the
``ValueError`` it raises before fetching, `covjson_msgspec.validation` turns each
into the matching ``tiled-ndarray.*`` finding with its JSON Pointer, and
`covjson_msgspec._repr` renders an offending tile set's count as undefined.

That third consumer constrains what may be added here. A repr has to display a
malformed array, because that is exactly when a reader is looking at one, so a
rule reports what is wrong and leaves raising to its caller. (It is also why
`covjson_msgspec.range.tile_count` is left unguarded.) `expand_url_template` may
raise, but it is an operation with a caller-established precondition, not a rule.

This module lives under a ``_`` prefix, exporting non-underscore names, so
``validation`` can share the rules without importing a ``range`` private; it
decides only over primitives, so it imports no consumer and cannot cycle.

The two template rules read the same subdivided-axis set but report different
subjects: `variables_not_subdivided` names each template variable,
`axes_missing_variables` each offending axis name.
`duplicate_subdivided_axes` derives a narrower set of its own, deliberately: an
axis whose tile size is not positive counts as subdivided for the template rules,
but nothing divides its extent, so it has no ordinals to collide and is left out
here. It reads no template at all, since no template satisfies the MUST once two
same-named axes can differ in ordinal, and it does read ``shape``, which the
other two never do, because the ordinals are what collide.

The ``urlTemplate`` grammar is here too: one private pattern that the template
rules read a template's variables with and `expand_url_template` substitutes them
with, so a rule that judges a template and the assembly that expands it cannot
disagree about what a variable is. The pattern itself is never exported.

Spec: [TiledNdArray objects](https://github.com/covjson/specification/blob/master/spec.md#63-tiledndarray-objects).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# A single Level 1 RFC 6570 expression, e.g. ``{t}`` in a tile url template. The
# one home for the grammar, private because what callers need from it is
# exported as functions: the template rules find with it, `expand_url_template`
# substitutes with it.
_TEMPLATE_VARIABLE_RE = re.compile(r"\{([^{}]+)\}")


def non_positive_tile_sizes(
    tile_shape: Sequence[int | None],
) -> Sequence[tuple[int, int]]:
    """Return each tile size that is neither ``None`` nor positive, with its index.

    ``None`` means the axis is whole (one tile) and is never offending; any other
    entry is a divisor, so zero or less defines no tile count. Spec 6.3 says only
    "integer" for a non-null tile size, so positivity is *entailed* rather than
    stated: the section's tile-count formula divides the axis size by the tile
    size, which a value of zero or less cannot satisfy.

    The index and the size travel together because
    [`validate`][covjson_msgspec.validate] needs both, the index for the JSON Pointer
    and the size for ``tiled-ndarray.tile-shape-not-positive``'s payload, and
    re-reading ``tile_shape[i]`` would hand it back an ``int | None`` it would have
    to re-narrow.

    Parameters
    ----------
    tile_shape
        A tile set's ``tile_shape``.

    Returns
    -------
    sequence of (int, int)
        Each offending entry as ``(index, size)``, in axis order.

    Examples
    --------
    >>> non_positive_tile_sizes((1, None, 100))
    ()
    >>> non_positive_tile_sizes((0, None, -1))
    ((0, 0), (2, -1))
    """
    return tuple(
        (i, size) for i, size in enumerate(tile_shape) if size is not None and size < 1
    )


def variables_not_subdivided(
    axis_names: Sequence[str],
    tile_shape: Sequence[int | None],
    template: str,
) -> Sequence[str]:
    """Return the template variables that name no subdivided axis, in template order.

    The converse of `axes_missing_variables`, which spec 6.3 does not state: such a
    variable has no tile ordinal to expand it with, so
    [`assemble`][covjson_msgspec.TiledNdArray.assemble] cannot resolve the tile's URL
    and [`validate`][covjson_msgspec.validate] reports
    ``tiled-ndarray.url-template-unknown-variable``.

    Each name appears once however often the template repeats it, because the
    subject is the variable. Check the rank match first: on a mismatch the
    subdivided-axis set is unreliable and every name here is a guess.

    Parameters
    ----------
    axis_names
        The axis names, aligned with ``tile_shape``.
    tile_shape
        A tile set's ``tile_shape``.
    template
        The tile set's ``url_template``.

    Returns
    -------
    sequence of str
        The offending variable names, in order of first appearance.

    Examples
    --------
    With only ``t`` subdivided, a variable fails either way it can: ``z`` names
    no axis at all, and ``x`` names a real axis that is whole. Neither has a tile
    ordinal, so both are reported:

    >>> variables_not_subdivided(("t", "x"), (1, None), "{z}-{x}.covjson")
    ('z', 'x')

    >>> variables_not_subdivided(("t", "x"), (1, None), "{t}.covjson")
    ()

    Names come back in the order the template introduces them, and a name the
    template repeats is reported once:

    >>> variables_not_subdivided(("x",), (1,), "{b}-{a}-{b}.covjson")
    ('b', 'a')
    """
    subdivided = set(_subdivided_axes(axis_names, tile_shape))

    return tuple(
        name
        for name in dict.fromkeys(_TEMPLATE_VARIABLE_RE.findall(template))
        if name not in subdivided
    )


def axes_missing_variables(
    axis_names: Sequence[str],
    tile_shape: Sequence[int | None],
    template: str,
) -> Sequence[str]:
    """Return the subdivided axis names the template carries no variable for.

    Spec 6.3: "The URI template MUST contain a variable for each axis name whose
    corresponding element in ``"tileShape"`` is not null." Without one, every tile
    along that axis resolves to the same URL, so
    [`validate`][covjson_msgspec.validate] reports
    ``tiled-ndarray.url-template-missing-variable``.

    The MUST says "each axis *name*", not each axis, so a name is returned once
    however many of its axes carry it: adding one ``{name}`` to the template
    satisfies the section for all of them at once. Whether those axes can then
    agree on that variable's value is a separate rule, `duplicate_subdivided_axes`.

    Parameters
    ----------
    axis_names
        The axis names, aligned with ``tile_shape``.
    tile_shape
        A tile set's ``tile_shape``.
    template
        The tile set's ``url_template``.

    Returns
    -------
    sequence of str
        The offending axis names, in order of first appearance.

    Examples
    --------
    >>> axes_missing_variables(("t", "x"), (1, None), "tile.covjson")
    ('t',)
    >>> axes_missing_variables(("t", "x"), (1, None), "{t}.covjson")
    ()

    A name repeated in ``axis_names`` is reported once. Here ``x`` names three
    axes, two of them subdivided, and collapses to a single entry; ``y`` survives
    beside it as a distinct name; and the whole-spanning ``x`` adds nothing, since
    a name is offending once any of its axes is subdivided:

    >>> axes_missing_variables(("x", "x", "y", "x"), (1, 1, 1, None), "tile.covjson")
    ('x', 'y')
    """
    present = set(_TEMPLATE_VARIABLE_RE.findall(template))

    return tuple(
        name
        for name in dict.fromkeys(_subdivided_axes(axis_names, tile_shape))
        if name not in present
    )


def duplicate_subdivided_axes(
    axis_names: Sequence[str],
    tile_shape: Sequence[int | None],
    array_shape: Sequence[int],
) -> Sequence[tuple[str, tuple[int, ...]]]:
    """Return each name whose subdivided axes would need conflicting ordinals.

    Spec 6.3 states no uniqueness rule for ``axisNames``, so this is *entailed*.
    The section's ``urlTemplate`` MUST requires "a variable for each axis name
    whose corresponding element in ``"tileShape"`` is not null", which a shared
    name satisfies with one variable. What it cannot satisfy is the next
    sentence, which fixes that variable's value per axis: "A variable for an axis
    of total size ``totalSize`` ... has as value one of the integers
    ``0, 1, ..., q + r - 1``". A tile offset differing between two same-named
    axes would need one variable to hold both ordinals at once, so
    [`validate`][covjson_msgspec.validate] reports
    ``tiled-ndarray.duplicate-subdivided-axis``.

    That makes the rule narrower than "a name is repeated", in two ways the
    second sentence forces and both of which are checked here:

    * every same-named axis yielding a single tile is fine. Each one's only
      ordinal is ``0``, so one variable holds them all, and the tiling lays out
      correctly.
    * a subdivided axis spanning no cells is fine, whatever the other axes do. It
      contributes no tile start, so the product over the axes generates no URI
      and there is no value for the sentence to constrain. A negative extent is
      *not* fine, but it is a defect of ``shape`` rather than of these axes, and
      it enumerates no tiles for the same reason, so it is passed over here
      rather than reported as a conflict it did not cause.

    Unlike the two template rules, this one never reads the template: once a tile
    offset can differ, no template satisfies the sentence. It reads ``shape``
    instead, which the others do not, because the ordinals are what collide.
    Check the rank match first: on a mismatch the pairing truncates, so a name
    reported here may be the truncation's own.

    Parameters
    ----------
    axis_names
        The axis names, aligned with ``array_shape``.
    tile_shape
        A tile set's ``tile_shape``, rank-matched to ``array_shape``.
    array_shape
        The full array shape.

    Returns
    -------
    sequence of (str, tuple of int)
        Each offending name with the indices of the subdivided axes claiming it,
        in order of the name's first appearance.

    Examples
    --------
    Two cells in tiles of one give each ``x`` axis the ordinals ``0`` and ``1``,
    so the tile at offsets ``(0, 1)`` would need ``{x}`` to be ``0`` and ``1``:

    >>> duplicate_subdivided_axes(("x", "x"), (1, 1), (2, 2))
    (('x', (0, 1)),)

    One axis is enough to break it, since the offsets need only differ somewhere:

    >>> duplicate_subdivided_axes(("x", "x"), (1, 1), (2, 1))
    (('x', (0, 1)),)

    A name whose axes each yield a single tile is satisfiable, every ordinal
    being ``0``, whether that is because the tile spans the axis or because the
    axis is not subdivided at all:

    >>> duplicate_subdivided_axes(("x", "x"), (1, 1), (1, 1))
    ()
    >>> duplicate_subdivided_axes(("x", "x"), (1, None), (2, 2))
    ()

    A subdivided axis spanning no cells enumerates no tiles, so nothing is
    generated for a variable to be ambiguous in. A negative extent enumerates
    none either, and is a defect of ``shape`` rather than of these axes:

    >>> duplicate_subdivided_axes(("x", "x"), (1, 1), (0, 2))
    ()
    >>> duplicate_subdivided_axes(("x", "x"), (1, 1), (-1, 2))
    ()

    Only the subdivided axes are named, and distinct names never collide:

    >>> duplicate_subdivided_axes(("x", "x", "x", "y"), (1, None, 1, 1), (4, 4, 4, 4))
    (('x', (0, 2)),)
    """
    grouped: dict[str, list[tuple[int, bool]]] = {}

    for i, (name, size, tile_size) in enumerate(
        zip(axis_names, array_shape, tile_shape, strict=False)
    ):
        # Deliberately not `_subdivided_axes`' membership test. A null tile size
        # leaves the axis whole, so it interpolates no variable; a non-positive
        # one is subdivided for the template rules (spec 6.3 keys them on "not
        # null") but nothing divides its extent, so it has no ordinal either.
        # Neither can be party to an ordinal conflict, and
        # `non_positive_tile_sizes` owns reporting the second.
        if tile_size is None or tile_size < 1:
            continue

        # `range(0, size, tile_size)` is empty for any size <= 0, so such an axis
        # contributes no tile start and the product over the axes enumerates
        # nothing at all. Zero is a legal empty axis; a negative extent is a
        # defect of `shape` itself, so reporting a conflict here would blame
        # these axes for it.
        if size <= 0:
            return ()

        # `size > tile_size` is the exact test for "more than one tile" and needs
        # no division, so it cannot trip over an extent too large for a float.
        grouped.setdefault(name, []).append((i, size > tile_size))

    return tuple(
        (name, tuple(i for i, _ in entries))
        for name, entries in grouped.items()
        if len(entries) > 1 and any(many_tiles for _, many_tiles in entries)
    )


def expand_url_template(template: str, variables: Mapping[str, int]) -> str:
    """Expand a Level 1 RFC 6570 URL template with integer tile indices.

    Substitutes each ``{name}`` in ``template`` with ``variables[name]``.
    CoverageJSON tile ``urlTemplate`` values are Level 1 (simple ``{var}``
    expansion only) and the values are non-negative tile ordinals, so no
    percent-encoding is needed. It is homed beside the template rules so that
    reading a template's variables and writing them back cannot disagree about
    the grammar.

    Every ``{name}`` must be a key of ``variables``, which
    `covjson_msgspec.range` establishes with `variables_not_subdivided` before
    enumerating any tile, so a name absent here is a layout bug rather than a
    document defect.

    Parameters
    ----------
    template
        The url template, e.g. ``"tiles/{y}-{x}.covjson"``.
    variables
        The tile ordinal for each partitioned axis name.

    Returns
    -------
    str
        The expanded URL.

    Raises
    ------
    ValueError
        If the template names a variable absent from ``variables``. A caller that
        gates on `variables_not_subdivided` first never reaches this, and
        `_tile_layout` does exactly that so it can name every offender at once;
        the guard is here so this function stays diagnosable when called on its
        own, rather than failing as a bare `KeyError` from inside `re.sub`.

    Examples
    --------
    >>> expand_url_template("tiles/{y}-{x}.covjson", {"y": 0, "x": 3})
    'tiles/0-3.covjson'
    >>> expand_url_template("tiles/{t}.covjson", {})
    Traceback (most recent call last):
        ...
    ValueError: url template 'tiles/{t}.covjson' has no value for variable 't'
    """

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)

        if name not in variables:
            msg = f"url template {template!r} has no value for variable {name!r}"
            raise ValueError(msg)

        return str(variables[name])

    return _TEMPLATE_VARIABLE_RE.sub(_substitute, template)


def _subdivided_axes(
    axis_names: Sequence[str], tile_shape: Sequence[int | None]
) -> Sequence[str]:
    """Return the names of the axes a tile set subdivides, in axis order.

    An axis is subdivided when its ``tile_shape`` entry is not ``None``; a ``None``
    entry spans the whole axis. Both template rules read this set, so it is derived
    once: two derivations could disagree, which is exactly what this module exists
    to prevent.

    The zip is deliberately non-strict. The only input on which strictness would
    show is a rank mismatch, where the honest answer is that the pairing is
    meaningless and the caller should have gated on the rank match; raising
    there would be worse than truncating, because
    [`validate`][covjson_msgspec.validate] reaches this and reports a rank mismatch
    rather than raising at one.

    Parameters
    ----------
    axis_names
        The axis names, aligned with ``tile_shape``.
    tile_shape
        A tile set's ``tile_shape``.

    Returns
    -------
    sequence of str
        The subdivided axes' names, in axis order, duplicates kept. Two axes named
        ``x`` are two axes, so both are listed; a caller that reports per name
        deduplicates the result itself (see `axes_missing_variables`) rather than
        narrowing this.

    Examples
    --------
    >>> _subdivided_axes(("t", "z", "x"), (1, None, 2))
    ('t', 'x')
    >>> _subdivided_axes(("t", "x"), (None, None))
    ()

    A rank mismatch truncates rather than raising, pairing only as far as the
    shorter sequence goes:

    >>> _subdivided_axes(("t", "x"), (1,))
    ('t',)
    """
    return tuple(
        name
        for name, tile_size in zip(axis_names, tile_shape, strict=False)
        if tile_size is not None
    )
