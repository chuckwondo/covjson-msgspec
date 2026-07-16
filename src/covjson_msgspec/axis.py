"""Domain axes.

An axis describes the coordinate values along one domain dimension. A single
`Axis` type models every CoverageJSON shape, and exactly one form must be used:

* **value-listing**: an explicit ``values`` array;
* **regular**: ``start`` / ``stop`` / ``num``, the compact notation for a
  regularly spaced axis; and
* **composite**: ``dataType`` ``"tuple"`` or ``"polygon"`` with named
  ``coordinates`` (used by trajectory and polygon domains).

Builders cover the numeric forms (`Axis.regular`, `Axis.listed`) and the two
composite forms (`Axis.tuple_`, `Axis.polygon`).

Spec: [Axis objects](https://github.com/covjson/specification/blob/master/spec.md#611-axis-objects).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Self

from covjson_msgspec._base import CovJSONStruct

# A primitive value, or a tuple covering both composite forms: "tuple" (a flat
# tuple of primitives) and "polygon" (nested rings of positions).
#
# Temporal coordinates are plain ``str`` here, never ``datetime``: the model
# stores time values as their raw ISO 8601 strings and never parses them, so a
# decode -> encode round trip is byte-faithful (``Z`` vs ``+00:00``, fractional
# seconds, and dates outside numpy's datetime64 range are all preserved). Parsing
# to a ``datetime`` is opt-in: `covjson_msgspec.temporal.to_datetime` (stdlib) or
# the export bridges (pandas/xarray). See the
# `covjson_msgspec.referencing.TemporalRS` calendar for the companion note.
#
# Two msgspec constraints shape this:
#
# 1. A union may contain at most ONE array-like (list/set/tuple) member, so the
#    two composite shapes cannot have separate tuple aliases.
# 2. A recursive type alias (e.g. tuple["AxisValue", ...]) is not resolved by
#    msgspec on Python 3.11 (the PEP 695 "type" statement that would resolve it
#    needs 3.12).
#
# So composite values use tuple[Any, ...]: the top level decodes to a tuple, with
# Any for the rare nested polygon interior.
AxisValue = float | int | str | tuple[Any, ...]

# Nested-sequence shapes accepted by the composite-axis builders. A position is a
# sequence of coordinate values (e.g. x, y); a ring is a sequence of positions
# (closed: first position repeated last); a polygon is a sequence of rings (the
# exterior ring first, then any holes).
RingCoords = Iterable[Iterable[float]]
PolygonCoords = Iterable[RingCoords]


# Modeled as one permissive struct rather than a tagged union: the axis shapes
# share no "type" discriminator and msgspec disallows untagged unions of
# multiple structs. __post_init__ enforces that exactly one form is present.
class Axis(CovJSONStruct, frozen=True):
    """A domain axis in any of its CoverageJSON shapes.

    Notes
    -----
    Temporal coordinates are kept as their raw ISO 8601 strings and are never
    parsed into ``datetime``: decode -> encode is byte-faithful, so ``Z`` vs
    ``+00:00``, fractional seconds, and dates outside numpy's ``datetime64``
    range all survive a round trip. Parsing to a ``datetime`` is opt-in, via
    `covjson_msgspec.temporal.to_datetime` (stdlib) or the pandas/xarray export
    bridges. See `covjson_msgspec.referencing.TemporalRS` for the companion
    calendar note.

    Examples
    --------
    >>> Axis.regular(0.0, 270.0, 4).coordinate_values
    (0.0, 90.0, 180.0, 270.0)
    >>> Axis(start=0.0, stop=10.0, num=3, values=(1, 2))  # two forms at once
    Traceback (most recent call last):
        ...
    ValueError: Axis requires exactly one of `values` or `start`/`stop`/`num`

    ``len()`` gives the coordinate count without materializing a regular
    axis's values. An axis must have at least one coordinate (spec 6.1.1: an
    empty ``values`` array is rejected), so a valid axis never evaluates
    falsy:

    >>> len(Axis.regular(0.0, 270.0, 4))
    4
    >>> Axis(values=())
    Traceback (most recent call last):
        ...
    ValueError: Axis `values` must be non-empty

    A single-coordinate regular axis (``num`` of 1) must have ``start == stop``:

    >>> Axis(start=0.0, stop=10.0, num=1)
    Traceback (most recent call last):
        ...
    ValueError: Axis with `num` of 1 requires equal `start` and `stop`

    A regular axis decodes from the compact start/stop/num form (camelCase wire
    names map to snake_case attributes):

    >>> import msgspec
    >>> ax = msgspec.json.decode(b'{"start": 0, "stop": 10, "num": 3}', type=Axis)
    >>> ax.coordinate_values
    (0.0, 5.0, 10.0)

    The third form is composite: each value is a tuple of named coordinates (here
    a trajectory's ``(t, x, y)`` positions):

    >>> traj = Axis.tuple_(
    ...     [("2020-01-01T00:00:00Z", 1.0, 2.0)], coordinates=("t", "x", "y")
    ... )
    >>> traj.data_type
    'tuple'
    >>> traj.coordinate_values
    (('2020-01-01T00:00:00Z', 1.0, 2.0),)

    A composite axis's ``coordinates`` names its components, so it too must be
    non-empty (spec 6.1.1):

    >>> Axis(values=((1.0, 2.0),), data_type="tuple", coordinates=())
    Traceback (most recent call last):
        ...
    ValueError: Axis `coordinates` must be non-empty

    A composite axis must list its values: the regular form describes evenly
    spaced numbers, which can never be the tuples a ``"tuple"`` axis promises:

    >>> Axis(start=0.0, stop=10.0, num=3, data_type="tuple", coordinates=("x", "y"))
    Traceback (most recent call last):
        ...
    ValueError: a 'tuple' axis requires `values`

    That rule is derived from the ``"tuple"`` / ``"polygon"`` value MUSTs, so it
    reaches only those two: the spec constrains no custom ``dataType``'s values,
    and such an axis keeps both forms.

    >>> Axis(start=0.0, stop=10.0, num=3, data_type="knmi:range").coordinate_values
    (0.0, 5.0, 10.0)
    """

    values: tuple[AxisValue, ...] | None = None
    start: float | None = None
    stop: float | None = None
    num: int | None = None
    # Wire name ``dataType``. The spec defines "primitive" (the default when
    # omitted), "tuple", and "polygon", but explicitly allows custom extension
    # values (spec 6.1.1), so this stays a free string rather than a Literal; an
    # unrecognized value is treated as primitive-like. ``None`` means "primitive".
    data_type: str | None = None
    coordinates: tuple[str, ...] | None = None
    bounds: tuple[float | str, ...] | None = None

    def __post_init__(self) -> None:
        has_values = self.values is not None
        has_regular = (
            self.start is not None and self.stop is not None and self.num is not None
        )

        # Exactly one numeric form: the value list XOR the full regular triple.
        # O(1), so it runs on construction and on decode.
        if has_values == has_regular:
            msg = "Axis requires exactly one of `values` or `start`/`stop`/`num`"
            raise ValueError(msg)

        # Spec 6.1.1: the `values` member is "a non-empty array of axis
        # values". Together with the `num >= 1` check below, every axis has at
        # least one coordinate, so `len(axis)` is never 0 and a valid Axis
        # never evaluates falsy.
        if self.values is not None and not self.values:
            msg = "Axis `values` must be non-empty"
            raise ValueError(msg)

        if self.num is not None and self.num < 1:
            msg = "Axis `num` must be a positive integer"
            raise ValueError(msg)

        # Spec 6.1.1: "If the value of `num` is 1, then `start` and `stop` MUST
        # have identical values." A single-coordinate regular axis is one point,
        # so its bounds cannot differ. Local and O(1), so it belongs here rather
        # than in validate(). Gated on the regular form actually being in use, so
        # a value-listing axis carrying a stray `start`/`num` is not misdiagnosed
        # here (the XOR check above owns that malformation).
        if has_regular and self.num == 1 and self.start != self.stop:
            msg = "Axis with `num` of 1 requires equal `start` and `stop`"
            raise ValueError(msg)

        if self.data_type in ("tuple", "polygon") and self.coordinates is None:
            msg = f"a {self.data_type!r} axis requires `coordinates`"
            raise ValueError(msg)

        # Derived from two spec 6.1.1 MUSTs rather than stated by either: a
        # 'tuple' axis value MUST be "an array of fixed size of primitive values"
        # (a 'polygon' value, "a GeoJSON Polygon coordinate array"), while
        # start/stop/num is "a compact notation for a regularly spaced numeric
        # axis" and so yields only numbers. No value satisfies both, so the pair
        # is unsatisfiable rather than merely odd. Named dataTypes only: the spec
        # defines no value structure for a custom dataType (6.1.1 grants only
        # "Custom values MAY be used"), so no MUST constrains its values and this
        # rule cannot be derived for one. Belongs here rather than in validate()
        # because the contradiction leaves the axis uninterpretable (ADR-0002,
        # ADR-0018).
        if self.data_type in ("tuple", "polygon") and self.values is None:
            msg = f"a {self.data_type!r} axis requires `values`"
            raise ValueError(msg)

        # Spec 6.1.1: `coordinates`, when given, is a non-empty array. Applies to
        # any axis: an empty coordinates array is uninterpretable in isolation,
        # so it is rejected here rather than in validate() (ADR-0002), mirroring
        # the `values` guard above.
        if self.coordinates is not None and not self.coordinates:
            msg = "Axis `coordinates` must be non-empty"
            raise ValueError(msg)

    @property
    def coordinate_values(self) -> tuple[AxisValue, ...]:
        """The explicit coordinate values, materializing the regular form.

        Returns
        -------
        tuple
            For a value-listing axis, the ``values``; for a regular axis, the
            ``num`` evenly spaced values from ``start`` to ``stop`` inclusive.

        Examples
        --------
        >>> Axis.listed((10, 20, 30)).coordinate_values
        (10, 20, 30)
        >>> Axis.regular(5.0, 5.0, 1).coordinate_values  # num 1: start == stop
        (5.0,)
        """
        if self.values is not None:
            return self.values

        # The regular triple is guaranteed present by __post_init__.
        assert self.start is not None
        assert self.stop is not None
        assert self.num is not None

        if self.num == 1:
            return (self.start,)

        step = (self.stop - self.start) / (self.num - 1)
        return tuple(self.start + i * step for i in range(self.num))

    def __len__(self) -> int:
        """The number of coordinates, in any of the axis's forms.

        Unlike ``len(axis.coordinate_values)``, this never materializes a
        regular axis's values: it is O(1) in every form.

        A valid axis is never empty (`__post_init__` rejects an empty
        ``values`` array and a non-positive ``num``, per spec 6.1.1), so the
        length is at least 1 and an `Axis` never evaluates falsy.

        Returns
        -------
        int
            ``len(values)`` for a value-listing or composite axis; ``num`` for
            a regular axis. At least 1.

        Examples
        --------
        >>> len(Axis.listed((10.0, 20.0, 30.0)))
        3
        >>> len(Axis.regular(0.0, 10.0, 5))
        5
        """
        if self.values is not None:
            return len(self.values)

        # __post_init__ guarantees the regular triple is complete when values
        # is None.
        assert self.num is not None
        return self.num

    @classmethod
    def regular(
        cls,
        start: float,
        stop: float,
        num: int,
        *,
        coordinates: Iterable[str] | None = None,
        bounds: Iterable[float | str] | None = None,
    ) -> Self:
        """Build a regularly spaced axis from compact start/stop/num notation.

        Parameters
        ----------
        start
            First coordinate value.
        stop
            Last coordinate value (inclusive).
        num
            Number of evenly spaced values; must be a positive integer.
        coordinates
            Coordinate identifiers this axis provides (defaults to the axis id).
        bounds
            Cell bounds: ``2 * num`` lower/upper values.

        Returns
        -------
        Axis
            A regular axis.

        Examples
        --------
        >>> Axis.regular(0.0, 100.0, 5).coordinate_values
        (0.0, 25.0, 50.0, 75.0, 100.0)
        """
        return cls(
            start=start,
            stop=stop,
            num=num,
            coordinates=None if coordinates is None else tuple(coordinates),
            bounds=None if bounds is None else tuple(bounds),
        )

    @classmethod
    def listed(
        cls,
        values: Iterable[AxisValue],
        *,
        coordinates: Iterable[str] | None = None,
        bounds: Iterable[float | str] | None = None,
    ) -> Self:
        """Build a value-listing axis from explicit values.

        Parameters
        ----------
        values
            The coordinate values.
        coordinates
            Coordinate identifiers this axis provides (defaults to the axis id).
        bounds
            Cell bounds: ``2 * len(values)`` lower/upper values.

        Returns
        -------
        Axis
            A value-listing axis.

        Examples
        --------
        >>> ax = Axis.listed((10.0, 20.0), bounds=(5.0, 15.0, 15.0, 25.0))
        >>> ax.coordinate_values
        (10.0, 20.0)
        >>> ax.bounds
        (5.0, 15.0, 15.0, 25.0)
        """
        return cls(
            values=tuple(values),
            coordinates=None if coordinates is None else tuple(coordinates),
            bounds=None if bounds is None else tuple(bounds),
        )

    # Trailing underscore (PEP 8) avoids shadowing the builtin `tuple`: a class
    # member named `tuple` would resolve ahead of the builtin when msgspec
    # evaluates the `values: tuple[AxisValue, ...]` field annotation in the class
    # namespace, which breaks under Python 3.14's deferred annotations.
    @classmethod
    def tuple_(
        cls,
        values: Iterable[Iterable[float | int | str]],
        *,
        coordinates: Iterable[str],
    ) -> Self:
        """Build a composite tuple axis from positions of coordinate values.

        Used by the Trajectory, MultiPoint, and Section domains, where each axis
        value is a tuple of primitive coordinates (e.g. ``(t, x, y)``) in the
        order given by ``coordinates``. The positions are materialized as tuples.

        Parameters
        ----------
        values
            The positions; each is a sequence of primitive coordinate values
            ordered to match ``coordinates``.
        coordinates
            The coordinate identifiers each position provides (e.g.
            ``("t", "x", "y")``).

        Returns
        -------
        Axis
            A composite axis with ``dataType`` ``"tuple"``.

        Examples
        --------
        >>> ax = Axis.tuple_(
        ...     [("2020-01-01T00:00:00Z", 1.0, 2.0)], coordinates=("t", "x", "y")
        ... )
        >>> ax.data_type
        'tuple'
        >>> ax.values
        (('2020-01-01T00:00:00Z', 1.0, 2.0),)
        """
        return cls(
            data_type="tuple",
            coordinates=tuple(coordinates),
            values=tuple(tuple(position) for position in values),
        )

    @classmethod
    def polygon(
        cls,
        polygons: Iterable[PolygonCoords],
        *,
        coordinates: Iterable[str] = ("x", "y"),
    ) -> Self:
        """Build a composite polygon axis from one or more polygons.

        Used by the Polygon family of domains (see `Domain.polygon` /
        `Domain.multipolygon`). The nested positions are materialized as tuples.

        Parameters
        ----------
        polygons
            The polygons. Each polygon is a sequence of linear rings (the
            exterior ring first, then any holes); each ring is a sequence of
            positions; each position is a sequence of coordinate values ordered
            to match ``coordinates``. Rings should be closed (first position
            repeated last).
        coordinates
            The coordinate identifiers each position provides (default
            ``x`` / ``y``).

        Returns
        -------
        Axis
            A composite axis with ``dataType`` ``"polygon"``.

        Examples
        --------
        >>> ax = Axis.polygon([[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]])
        >>> ax.data_type
        'polygon'
        >>> ax.values
        ((((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),),)
        """
        return cls(
            data_type="polygon",
            coordinates=tuple(coordinates),
            values=tuple(
                tuple(tuple(tuple(position) for position in ring) for ring in polygon)
                for polygon in polygons
            ),
        )
