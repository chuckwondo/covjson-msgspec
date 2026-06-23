"""Domain axes.

An axis describes the coordinate values along one domain dimension. A single
`Axis` type models every CoverageJSON shape, and exactly one form must be used:

* **value-listing**: an explicit ``values`` array;
* **regular**: ``start`` / ``stop`` / ``num``, the compact notation for a
  regularly spaced axis; and
* **composite**: ``dataType`` ``"tuple"`` or ``"polygon"`` with named
  ``coordinates`` (used by trajectory and polygon domains).

Builders cover the numeric forms (`Axis.regular`, `Axis.listed`) and the two
composite forms (`Axis.tuple`, `Axis.polygon`).

Spec: [Axis objects](https://github.com/covjson/specification/blob/master/spec.md#611-axis-objects).
"""

from collections.abc import Iterable
from typing import Any, Self

from covjson_msgspec._base import CovJSONStruct

# A primitive value, or a tuple covering both composite forms: "tuple" (a flat
# tuple of primitives) and "polygon" (nested rings of positions).
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

    Examples
    --------
    >>> Axis.regular(0.0, 270.0, 4).coordinate_values
    (0.0, 90.0, 180.0, 270.0)
    >>> Axis(start=0.0, stop=10.0, num=3, values=(1, 2))  # two forms at once
    Traceback (most recent call last):
        ...
    ValueError: Axis requires exactly one of `values` or `start`/`stop`/`num`

    A regular axis decodes from the compact start/stop/num form (camelCase wire
    names map to snake_case attributes):

    >>> import msgspec
    >>> ax = msgspec.json.decode(b'{"start": 0, "stop": 10, "num": 3}', type=Axis)
    >>> ax.coordinate_values
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

        if self.num is not None and self.num < 1:
            msg = "Axis `num` must be a positive integer"
            raise ValueError(msg)

        if self.data_type in ("tuple", "polygon") and self.coordinates is None:
            msg = f"a {self.data_type!r} axis requires `coordinates`"
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
        >>> Axis.regular(0.0, 1.0, 1).coordinate_values
        (0.0,)
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
        """
        return cls(
            values=tuple(values),
            coordinates=None if coordinates is None else tuple(coordinates),
            bounds=None if bounds is None else tuple(bounds),
        )

    @classmethod
    def tuple(
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
        >>> ax = Axis.tuple(
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
