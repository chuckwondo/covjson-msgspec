"""Row-major (C-order) flat-index arithmetic.

`NdArray` stores its values as a single flat, row-major tuple, so both tile
assembly (`covjson_msgspec.range`) and subsetting (`covjson_msgspec._subset`)
need to translate between that flat sequence and multi-dimensional positions.
These two helpers are that translation. They are pure Python (no numpy) so the
core operations that depend on them stay free of optional dependencies.
"""

from __future__ import annotations

from collections.abc import Sequence


def strides(shape: Sequence[int]) -> Sequence[int]:
    """Return the row-major (C-order) flat-index strides for ``shape``.

    Stride ``i`` is the flat-index step for a one-unit move along axis ``i``: the
    product of the sizes of all later axes. The last axis therefore has stride 1.

    Parameters
    ----------
    shape
        The array shape.

    Returns
    -------
    sequence of int
        One stride per axis (empty for a 0-dimensional shape).

    Examples
    --------
    >>> strides((2, 5, 10))
    (50, 10, 1)
    >>> strides(())
    ()
    """
    out: list[int] = []
    step = 1

    for size in reversed(shape):
        out.append(step)
        step *= size

    out.reverse()
    return tuple(out)


def ravel_index(index: Sequence[int], strides: Sequence[int]) -> int:
    """Return the flat row-major offset of a multi-dimensional ``index``.

    The dot product of ``index`` with ``strides`` (typically from `strides`):
    each axis position is weighted by that axis's flat-index step. A
    0-dimensional index (``()`` against ``()``) maps to offset 0.

    Parameters
    ----------
    index
        The per-axis position.
    strides
        The per-axis flat-index strides, aligned with ``index``.

    Returns
    -------
    int
        The position's offset into the flat, row-major value sequence.

    Examples
    --------
    >>> ravel_index((1, 2, 3), strides((2, 5, 10)))
    73
    >>> ravel_index((), ())
    0
    """
    return sum(i * stride for i, stride in zip(index, strides, strict=True))
