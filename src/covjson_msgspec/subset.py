"""Subset a coverage by integer position (`isel`) or coordinate label (`sel`).

Both narrow a `Coverage` to a sub-region along named axes, returning a new
coverage (the model is immutable). The semantics follow xarray's
`~xarray.Dataset.isel` / `~xarray.Dataset.sel`:

* an **integer** indexer selects a single position and *drops* that axis from the
  ranges (the coordinate is kept as a single-value domain axis, like an xarray
  scalar coordinate);
* a **slice** indexer keeps the axis, narrowed to the selected positions.

`isel` takes integer positions and `slice` objects of positions; `sel` takes
coordinate labels (and label `slice` objects) and maps them to positions before
delegating to `isel`.

Only individual axes (a Grid's ``x`` / ``y`` / ``z`` / ``t``, and the like) are
supported. Subsetting a composite (``"tuple"`` / ``"polygon"``) axis, and
subsetting along an axis stored as a `~covjson_msgspec.range.TiledNdArray` or a
URL reference, are not supported yet and raise.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple, TypeVar

import msgspec

from covjson_msgspec._bridging import require_inline_ndarray
from covjson_msgspec._ndindex import ravel_index, strides
from covjson_msgspec.axis import Axis, AxisValue
from covjson_msgspec.coverage import Coverage, Range
from covjson_msgspec.domain import Domain
from covjson_msgspec.range import NdArray

# An integer position or a slice of positions, as accepted by `isel`.
Indexer = int | slice

# A coordinate label or a slice of labels, as accepted by `sel`.
Label = float | int | str
LabelIndexer = Label | slice

_T = TypeVar("_T")


def isel(
    coverage: Coverage,
    indexers: Mapping[str, Indexer] | None = None,
    /,
    **indexers_kwargs: Indexer,
) -> Coverage:
    """Subset a coverage by integer position along named axes.

    Indexers may be passed as a mapping, as keyword arguments, or both. An
    integer selects a single position and drops that axis from the ranges,
    keeping its coordinate as a single-value domain axis; a `slice` keeps the
    axis, narrowed to the selected positions.

    Parameters
    ----------
    coverage
        The coverage to subset. Its domain must be inline (not a URL reference)
        and its selected ranges must be inline `~covjson_msgspec.range.NdArray`
        values.
    indexers
        A mapping of axis name to an integer position or a `slice` of positions.
    **indexers_kwargs
        Indexers given as keywords, e.g. ``isel(cov, x=0, t=slice(0, 3))``.

    Returns
    -------
    Coverage
        A new coverage narrowed to the selection (``coverage`` itself when no
        indexers are given).

    Raises
    ------
    ValueError
        If the domain is a URL reference, an indexer names an unknown axis, or
        the same axis is given both positionally and as a keyword.
    IndexError
        If an integer indexer is out of bounds for its axis, or a slice
        selects no positions (CoverageJSON forbids an empty axis).
    NotImplementedError
        If an indexer targets a composite (``"tuple"`` / ``"polygon"``) axis.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.grid(
    ...         x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)
    ...     ),
    ...     ranges={
    ...         "v": NdArray(
    ...             data_type="float",
    ...             values=tuple(float(i) for i in range(8)),
    ...             shape=(2, 4),
    ...             axis_names=("y", "x"),
    ...         )
    ...     },
    ... )

    A slice keeps the axis; an integer drops it (here ``y`` becomes a single-value
    coordinate and the range is left varying over ``x`` alone):

    >>> sub = isel(cov, y=0, x=slice(1, 3))
    >>> sub.ranges["v"].axis_names
    ('x',)
    >>> sub.ranges["v"].values
    (1.0, 2.0)
    >>> sub.domain.x.coordinate_values
    (10.0, 20.0)
    >>> sub.domain.y.coordinate_values
    (0.0,)
    """
    if not (selection := _merge_indexers(indexers, indexers_kwargs)):
        return coverage

    domain = _inline_domain(coverage)
    _reject_unsupported_axes(domain, selection)

    resolved = {
        name: _resolve_indexer(domain.axes[name], indexer)
        for name, indexer in selection.items()
    }
    new_axes = {
        name: _select_axis(axis, resolved[name]) if name in resolved else axis
        for name, axis in domain.axes.items()
    }
    new_ranges = {
        key: _subset_range(key, range_, resolved)
        for key, range_ in coverage.ranges.items()
    }

    return msgspec.structs.replace(
        coverage,
        domain=msgspec.structs.replace(domain, axes=new_axes),
        ranges=new_ranges,
    )


def sel(
    coverage: Coverage,
    indexers: Mapping[str, LabelIndexer] | None = None,
    /,
    *,
    method: Literal["nearest"] | None = None,
    **indexers_kwargs: LabelIndexer,
) -> Coverage:
    """Subset a coverage by coordinate label along named axes.

    Maps each label (or label `slice`) to an integer position on its axis, then
    delegates to `isel`, so the drop-axis / keep-axis rules are identical: a
    scalar label drops the axis, a `slice` keeps it.

    Parameters
    ----------
    coverage
        The coverage to subset (same requirements as `isel`).
    indexers
        A mapping of axis name to a coordinate label or a `slice` of labels. A
        label `slice` is inclusive of both bounds.
    method
        How to match a scalar label: ``None`` (default) requires an exact match;
        ``"nearest"`` picks the closest coordinate (numeric axes only). Applies
        only to scalar labels; ignored for a `slice` label.
    **indexers_kwargs
        Indexers given as keywords, e.g. ``sel(cov, x=10.0, method="nearest")``.

    Returns
    -------
    Coverage
        A new coverage narrowed to the selection (``coverage`` itself when no
        indexers are given).

    Raises
    ------
    KeyError
        If an exact label is not found, or a label `slice` matches no
        coordinates.
    ValueError
        If ``method`` is unsupported, plus the `isel` `ValueError` cases.
    TypeError
        If ``method="nearest"`` is used on a non-numeric axis or label.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.grid(
    ...         x=Axis.regular(0.0, 30.0, 4), y=Axis.regular(0.0, 10.0, 2)
    ...     ),
    ...     ranges={
    ...         "v": NdArray(
    ...             data_type="float",
    ...             values=tuple(float(i) for i in range(8)),
    ...             shape=(2, 4),
    ...             axis_names=("y", "x"),
    ...         )
    ...     },
    ... )

    Select by coordinate value, with the nearest ``x`` and an inclusive ``y``
    slice:

    >>> sub = sel(cov, x=11.0, y=slice(0.0, 10.0), method="nearest")
    >>> sub.domain.x.coordinate_values
    (10.0,)
    >>> sub.ranges["v"].values
    (1.0, 5.0)
    """
    if method not in (None, "nearest"):
        msg = f"unsupported method {method!r}; use 'nearest' or None"
        raise ValueError(msg)

    if not (selection := _merge_indexers(indexers, indexers_kwargs)):
        return coverage

    domain = _inline_domain(coverage)
    _reject_unsupported_axes(domain, selection)

    positions = {
        name: _label_to_indexer(name, domain.axes[name], label, method)
        for name, label in selection.items()
    }

    return isel(coverage, positions)


class _AxisSelection(NamedTuple):
    """The resolved positions selected along one axis, and whether to keep it.

    `isel` resolves each indexer to one of these: ``positions`` are the integer
    offsets to take along the axis (in output order), and ``keep_dim`` is False
    only for a scalar integer indexer, whose axis is dropped from the ranges.
    """

    positions: tuple[int, ...]
    keep_dim: bool


def _merge_indexers(
    indexers: Mapping[str, _T] | None, indexers_kwargs: Mapping[str, _T]
) -> dict[str, _T]:
    """Merge the positional mapping and keyword indexers into one dict.

    Mirrors xarray's twin-form indexer API: callers may pass a mapping, keyword
    arguments, or both. An axis given in both forms is ambiguous and rejected.

    Parameters
    ----------
    indexers
        The positional mapping of axis name to indexer, or ``None``.
    indexers_kwargs
        The keyword indexers.

    Returns
    -------
    dict
        The merged indexers.

    Raises
    ------
    ValueError
        If an axis appears in both forms.

    Examples
    --------
    >>> _merge_indexers({"x": 0}, {"y": 1})
    {'x': 0, 'y': 1}
    >>> _merge_indexers({"x": 0}, {"x": 1})
    Traceback (most recent call last):
        ...
    ValueError: indexer for axis 'x' given both positionally and as a keyword
    """
    merged: dict[str, _T] = dict(indexers) if indexers is not None else {}

    for name, indexer in indexers_kwargs.items():
        if name in merged:
            msg = f"indexer for axis {name!r} given both positionally and as a keyword"
            raise ValueError(msg)

        merged[name] = indexer

    return merged


def _inline_domain(coverage: Coverage) -> Domain:
    """Return the coverage's inline `Domain`, or raise if it is a URL reference.

    Subsetting reads the domain's axis coordinates, so a URL-reference domain
    must be resolved first.

    Parameters
    ----------
    coverage
        The coverage being subset.

    Returns
    -------
    Domain
        The inline domain.

    Raises
    ------
    ValueError
        If the domain is a URL-string reference.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Coverage, Domain
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={},
    ... )
    >>> _inline_domain(cov).domain_type
    'Point'
    >>> _inline_domain(Coverage(domain="https://ex/d.json", ranges={}))
    Traceback (most recent call last):
        ...
    ValueError: cannot subset a URL-reference domain; call resolve_references() first
    """
    domain = coverage.domain

    if not isinstance(domain, Domain):
        msg = "cannot subset a URL-reference domain; call resolve_references() first"
        raise ValueError(msg)

    return domain


def _reject_unsupported_axes(domain: Domain, selection: Mapping[str, object]) -> None:
    """Reject indexers naming an unknown axis or a composite axis.

    Subsetting only supports individual axes; an indexer must name a real domain
    axis, and that axis must not be a composite (``"tuple"`` / ``"polygon"``)
    axis, whose coordinates are bundled positions rather than an individual
    dimension.

    Parameters
    ----------
    domain
        The coverage's inline domain.
    selection
        The merged indexers, keyed by axis name (values unused here).

    Raises
    ------
    ValueError
        If an indexer names an axis not in the domain.
    NotImplementedError
        If an indexer names a composite axis.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain
    >>> grid = Domain.grid(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
    >>> _reject_unsupported_axes(grid, {"x": 0})  # ok, returns None
    >>> _reject_unsupported_axes(grid, {"z": 0})
    Traceback (most recent call last):
        ...
    ValueError: cannot subset unknown axis 'z'; domain axes are ['x', 'y']
    """
    for name in selection:
        axis = domain.axes.get(name)

        if axis is None:
            msg = (
                f"cannot subset unknown axis {name!r}; domain axes are "
                f"{sorted(domain.axes)}"
            )
            raise ValueError(msg)

        if axis.data_type in ("tuple", "polygon"):
            msg = (
                f"subsetting the composite {axis.data_type!r} axis {name!r} "
                "is not supported yet"
            )
            raise NotImplementedError(msg)


def _resolve_indexer(axis: Axis, indexer: Indexer) -> _AxisSelection:
    """Resolve one indexer against an axis to the positions it selects.

    A `slice` is expanded to its positions (keeping the axis); an integer is
    normalized (negative indexing allowed) and bounds-checked, selecting one
    position and dropping the axis.

    Parameters
    ----------
    axis
        The domain axis the indexer applies to.
    indexer
        An integer position or a `slice` of positions.

    Returns
    -------
    _AxisSelection
        The selected positions and whether the axis is kept.

    Raises
    ------
    IndexError
        If an integer indexer is out of bounds, or a slice selects no
        positions (CoverageJSON forbids an empty axis).

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> _resolve_indexer(Axis.regular(0.0, 10.0, 5), slice(1, 3))
    _AxisSelection(positions=(1, 2), keep_dim=True)
    >>> _resolve_indexer(Axis.regular(0.0, 10.0, 5), -1)
    _AxisSelection(positions=(4,), keep_dim=False)
    >>> _resolve_indexer(Axis.regular(0.0, 10.0, 5), 5)
    Traceback (most recent call last):
        ...
    IndexError: index 5 is out of bounds for axis of length 5
    >>> _resolve_indexer(Axis.regular(0.0, 10.0, 5), slice(2, 2))
    Traceback (most recent call last):
        ...
    IndexError: slice(2, 2, None) selects no coordinates; an axis cannot be empty
    """
    length = len(axis)

    if isinstance(indexer, slice):
        positions = tuple(range(*indexer.indices(length)))

        # An empty selection cannot be represented: CoverageJSON forbids an
        # empty axis (spec 6.1.1, enforced by Axis.__post_init__). Mirrors the
        # KeyError `sel` raises for a label slice matching no coordinates.
        if not positions:
            msg = f"{indexer!r} selects no coordinates; an axis cannot be empty"
            raise IndexError(msg)

        return _AxisSelection(positions, keep_dim=True)

    position = indexer + length if indexer < 0 else indexer

    if not 0 <= position < length:
        msg = f"index {indexer} is out of bounds for axis of length {length}"
        raise IndexError(msg)

    return _AxisSelection((position,), keep_dim=False)


def _select_axis(axis: Axis, selection: _AxisSelection) -> Axis:
    """Build the value-listing axis holding an axis's selected coordinates.

    The selected coordinate values (and matching cell `~Axis.bounds`, if any) are
    materialized into a listed axis, preserving the source axis's
    `~Axis.coordinates`. A subset of a regular axis becomes listed since the
    selection need not stay evenly spaced.

    Parameters
    ----------
    axis
        The source axis.
    selection
        The positions to keep (their order is preserved).

    Returns
    -------
    Axis
        A listed axis of the selected coordinates.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> _select_axis(Axis.regular(0.0, 30.0, 4), _AxisSelection((1, 3), True)).values
    (10.0, 30.0)
    """
    coords = axis.coordinate_values
    values = tuple(coords[position] for position in selection.positions)
    bounds = None

    if axis.bounds is not None:
        bounds = tuple(
            bound
            for position in selection.positions
            for bound in (axis.bounds[2 * position], axis.bounds[2 * position + 1])
        )

    return Axis.listed(values, coordinates=axis.coordinates, bounds=bounds)


def _subset_range(
    key: str, range_: Range, resolved: Mapping[str, _AxisSelection]
) -> NdArray:
    """Take the selected slab of one range, dropping integer-indexed axes.

    The selection is applied only along the axes this range actually varies over
    (its `~NdArray.axis_names`); other selected axes do not touch it. The new
    row-major values are gathered by walking the cartesian product of the
    selected positions per axis, and axes selected by a scalar integer (their
    `~_AxisSelection.keep_dim` is False) are dropped from ``shape`` /
    ``axis_names``.

    Parameters
    ----------
    key
        The range's key, used in the error when it is not an inline `NdArray`.
    range_
        The range to subset.
    resolved
        The resolved selection per axis name.

    Returns
    -------
    NdArray
        The subset range (``range_`` itself when it varies over no selected
        axis).

    Raises
    ------
    ValueError
        If ``range_`` is not an inline `NdArray` (a `TiledNdArray` or URL
        reference).

    Examples
    --------
    >>> from covjson_msgspec import NdArray
    >>> arr = NdArray(
    ...     data_type="float",
    ...     values=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
    ...     shape=(2, 3),
    ...     axis_names=("y", "x"),
    ... )
    >>> sub = _subset_range("v", arr, {"y": _AxisSelection((1,), keep_dim=False)})
    >>> (sub.axis_names, sub.shape, sub.values)
    (('x',), (3,), (3.0, 4.0, 5.0))
    """
    arr = require_inline_ndarray(key, range_, "subset")

    if all(name not in resolved for name in arr.axis_names):
        return arr

    per_axis = [
        resolved[name].positions if name in resolved else range(size)
        for name, size in zip(arr.axis_names, arr.shape, strict=True)
    ]
    source_strides = strides(arr.shape)
    values = tuple(
        arr.values[ravel_index(index, source_strides)]
        for index in itertools.product(*per_axis)
    )

    kept = [
        not (name in resolved and not resolved[name].keep_dim)
        for name in arr.axis_names
    ]
    axis_names = tuple(
        name for name, keep in zip(arr.axis_names, kept, strict=True) if keep
    )
    shape = tuple(
        len(positions) for positions, keep in zip(per_axis, kept, strict=True) if keep
    )

    return msgspec.structs.replace(
        arr, values=values, shape=shape, axis_names=axis_names
    )


def _label_to_indexer(
    name: str, axis: Axis, label: LabelIndexer, method: Literal["nearest"] | None
) -> Indexer:
    """Map a coordinate label (or label slice) to an integer indexer for `isel`.

    A scalar label resolves to a single position (exact match, or nearest when
    ``method="nearest"``); a label `slice` resolves to a position slice spanning
    the coordinates within its inclusive bounds. ``method`` applies only to
    scalar labels and is ignored for a slice, so one `sel` call may mix a slice
    with nearest-matched scalar labels.

    Parameters
    ----------
    name
        The axis name (for error messages).
    axis
        The axis whose coordinates the label is matched against.
    label
        A coordinate label or an inclusive `slice` of labels.
    method
        ``"nearest"`` for closest-match on a scalar label; otherwise exact.
        Ignored for a slice label.

    Returns
    -------
    int or slice
        The position indexer to hand to `isel`.

    Raises
    ------
    KeyError
        If an exact label is not found or a slice matches nothing.
    TypeError
        If ``method="nearest"`` is used on a non-numeric axis or label.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> ax = Axis.regular(0.0, 30.0, 4)
    >>> _label_to_indexer("x", ax, 20.0, None)
    2
    >>> _label_to_indexer("x", ax, 11.0, "nearest")
    1
    >>> _label_to_indexer("x", ax, slice(10.0, 20.0), None)
    slice(1, 3, None)
    """
    coords = axis.coordinate_values

    if isinstance(label, slice):
        return _label_slice(name, coords, label)

    if method == "nearest":
        return _nearest_position(name, coords, label)

    return _exact_position(name, coords, label)


def _exact_position(name: str, coords: tuple[AxisValue, ...], label: Label) -> int:
    """Return the position of an exact coordinate match, or raise `KeyError`.

    Parameters
    ----------
    name
        The axis name (for the error message).
    coords
        The axis coordinate values.
    label
        The coordinate label to find.

    Returns
    -------
    int
        The position of the first coordinate equal to ``label``.

    Raises
    ------
    KeyError
        If no coordinate equals ``label``.

    Examples
    --------
    >>> _exact_position("x", (0.0, 10.0, 20.0), 10.0)
    1
    >>> _exact_position("x", (0.0, 10.0, 20.0), 5.0)
    Traceback (most recent call last):
        ...
    KeyError: "label 5.0 not found on axis 'x'; pass method='nearest' for the closest"
    """
    try:
        return coords.index(label)
    except ValueError:
        msg = (
            f"label {label!r} not found on axis {name!r}; "
            "pass method='nearest' for the closest"
        )
        raise KeyError(msg) from None


def _nearest_position(name: str, coords: tuple[AxisValue, ...], label: Label) -> int:
    """Return the position of the coordinate closest to ``label`` (numeric axes).

    Parameters
    ----------
    name
        The axis name (for the error message).
    coords
        The axis coordinate values; must all be numeric.
    label
        The numeric coordinate label.

    Returns
    -------
    int
        The position of the coordinate with the smallest absolute difference.

    Raises
    ------
    TypeError
        If the axis or label is non-numeric.

    Examples
    --------
    >>> _nearest_position("x", (0.0, 10.0, 20.0), 11.0)
    1
    """
    numbers = _numeric_coordinates(name, coords)
    target = _as_number(name, label)

    return min(range(len(numbers)), key=lambda i: abs(numbers[i] - target))


def _label_slice(name: str, coords: tuple[AxisValue, ...], label: slice) -> slice:
    """Map an inclusive label slice to the position slice it spans.

    The coordinates falling within the slice's ``[start, stop]`` bounds (either
    bound optional) form a contiguous block on a monotonic axis; the spanning
    position slice is returned.

    Parameters
    ----------
    name
        The axis name (for the error message).
    coords
        The axis coordinate values.
    label
        An inclusive label `slice`; its ``step`` must be ``None``.

    Returns
    -------
    slice
        A position slice covering the matched coordinates.

    Raises
    ------
    ValueError
        If the slice has a ``step``.
    KeyError
        If no coordinate falls within the bounds.

    Examples
    --------
    >>> _label_slice("x", (0.0, 10.0, 20.0, 30.0), slice(10.0, 20.0))
    slice(1, 3, None)
    >>> _label_slice("x", (0.0, 10.0, 20.0), slice(100.0, 200.0))
    Traceback (most recent call last):
        ...
    KeyError: "no coordinates on axis 'x' fall within slice(100.0, 200.0, None)"
    """
    if label.step is not None:
        msg = "a label slice does not support a step"
        raise ValueError(msg)

    matched = [
        i for i, coord in enumerate(coords) if _within(coord, label.start, label.stop)
    ]

    if not matched:
        msg = f"no coordinates on axis {name!r} fall within {label!r}"
        raise KeyError(msg)

    return slice(matched[0], matched[-1] + 1)


def _within(value: AxisValue, start: Label | None, stop: Label | None) -> bool:
    """Whether ``value`` lies within the inclusive ``[start, stop]`` bounds.

    Either bound may be ``None`` (unbounded on that side). The comparison is the
    coordinate's own ordering (numeric or, for time axes, ISO-8601 string order).

    Parameters
    ----------
    value
        The coordinate value to test.
    start, stop
        The inclusive lower and upper bounds, or ``None``.

    Returns
    -------
    bool
        True when ``value`` is within the bounds.

    Examples
    --------
    >>> _within(10.0, 0.0, 20.0)
    True
    >>> _within(30.0, None, 20.0)
    False
    """
    # `value` is a heterogeneous coordinate union; bind it to Any so the ordering
    # comparison type-checks. Composite axes never reach here (rejected upstream),
    # so a mismatched bound type would be caller error and raises at runtime.
    point: Any = value

    return (start is None or start <= point) and (stop is None or point <= stop)


def _numeric_coordinates(name: str, coords: tuple[AxisValue, ...]) -> list[float]:
    """Return ``coords`` as floats, or raise if any coordinate is non-numeric.

    Parameters
    ----------
    name
        The axis name (for the error message).
    coords
        The axis coordinate values.

    Returns
    -------
    list of float
        The coordinates as floats.

    Raises
    ------
    TypeError
        If any coordinate is not an int or float.

    Examples
    --------
    >>> _numeric_coordinates("x", (0, 10, 20))
    [0.0, 10.0, 20.0]
    >>> _numeric_coordinates("t", ("2020-01-01",))
    Traceback (most recent call last):
        ...
    TypeError: method='nearest' needs a numeric axis; 't' is non-numeric
    """
    numbers: list[float] = []

    for coord in coords:
        if isinstance(coord, bool) or not isinstance(coord, (int, float)):
            msg = f"method='nearest' needs a numeric axis; {name!r} is non-numeric"
            raise TypeError(msg)

        numbers.append(float(coord))

    return numbers


def _as_number(name: str, label: Label) -> float:
    """Return a numeric label as a float, or raise if it is non-numeric.

    Parameters
    ----------
    name
        The axis name (for the error message).
    label
        The label to coerce.

    Returns
    -------
    float
        The label as a float.

    Raises
    ------
    TypeError
        If ``label`` is not an int or float.

    Examples
    --------
    >>> _as_number("x", 11)
    11.0
    >>> _as_number("t", "2020-01-01")
    Traceback (most recent call last):
        ...
    TypeError: method='nearest' needs a numeric label for axis 't', got '2020-01-01'
    """
    if isinstance(label, bool) or not isinstance(label, (int, float)):
        msg = f"method='nearest' needs a numeric label for axis {name!r}, got {label!r}"
        raise TypeError(msg)

    return float(label)
