"""Tile-set rules and ``urlTemplate`` grammar shared by `assemble` and `validate`.

Spec 6.3 constrains a TileSet in ways neither decode nor ``__post_init__``
enforces, so a decoded `TiledNdArray` may violate any of them. Two are hard
preconditions for laying a tiling out at all (a positive tile size, and a
``urlTemplate`` naming only subdivided axes). The third, a subdivided axis whose
variable the template omits, is a spec MUST that only ``validate`` reports as
such. Assembly notices it only when *that axis* carries more than one tile, since
only then do its tiles collide on one URL; an axis subdivided into a single tile
breaks the MUST while assembling correctly. So the rule needs a consumer judging
conformance, not one judging whether a tiling can be laid out.

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
subjects, so `variables_not_subdivided` names each template variable once, while
`axes_missing_variables` names each offending axis, repeats included.

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
    """Return the subdivided axes the template carries no variable for, in axis order.

    Spec 6.3: "The URI template MUST contain a variable for each axis name whose
    corresponding element in ``"tileShape"`` is not null." Without one, every tile
    along that axis resolves to the same URL, so
    [`validate`][covjson_msgspec.validate] reports
    ``tiled-ndarray.url-template-missing-variable``.

    Each offending *axis* is named, so a repeated entry in ``axis_names`` is
    reported once per axis rather than once per name: the subject here is the axis,
    not the variable that would address it.

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
        The offending axis names, in axis order.

    Examples
    --------
    >>> axes_missing_variables(("t", "x"), (1, None), "tile.covjson")
    ('t',)
    >>> axes_missing_variables(("t", "x"), (1, None), "{t}.covjson")
    ()

    A name repeated in ``axis_names`` is two separate axes, so it is reported
    once for each:

    >>> axes_missing_variables(("x", "x"), (1, 1), "tile.covjson")
    ('x', 'x')
    """
    present = set(_TEMPLATE_VARIABLE_RE.findall(template))

    return tuple(
        name for name in _subdivided_axes(axis_names, tile_shape) if name not in present
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
        The subdivided axes' names, in axis order, duplicates kept.

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
