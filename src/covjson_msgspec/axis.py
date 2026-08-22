"""Domain axes.

An axis describes the coordinate values along one domain dimension. A single
`Axis` type models every CoverageJSON shape. We read the two numeric forms as
exclusive, but spec 6.1.1 does not state that, so an axis carrying both decodes
and `covjson_msgspec.validate` reports it as ``axis.form-conflict``
(`covjson_msgspec.validation.AxisFormConflict` carries the derivation):

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

from collections.abc import Iterable, Sequence
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
# multiple structs. __post_init__ enforces that at least one complete form is
# present; their exclusivity is `validate`'s `axis.form-conflict` (ADR-0023).
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

    An axis must supply one complete form. Neither form, or an incomplete
    regular triple, yields no coordinate values at all and is rejected:

    >>> Axis(start=0.0, num=3)  # no `stop`, so nothing is reconstructable
    Traceback (most recent call last):
        ...
    ValueError: Axis requires `values` or all of `start`/`stop`/`num`

    Carrying *both* forms is a different matter: the axis is readable (``values``
    wins), so it decodes and `covjson_msgspec.validate` reports it (ADR-0023).

    >>> Axis(start=0.0, stop=10.0, num=3, values=(1, 2)).coordinate_values
    (1, 2)

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

    Omitting ``coordinates`` on a composite axis is likewise rejected: spec
    6.1.1's default names the axis, nonsensical for a composite (ADR-0019):

    >>> Axis(values=((1.0, 2.0),), data_type="tuple")
    Traceback (most recent call last):
        ...
    ValueError: a 'tuple' axis requires `coordinates`

    A ``"polygon"`` axis needs at least two coordinate identifiers, because a
    GeoJSON position has two or more components (RFC 7946 3.1.1):

    >>> ring = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0))
    >>> Axis(values=((ring,),), data_type="polygon", coordinates=("x",))
    Traceback (most recent call last):
        ...
    ValueError: a 'polygon' axis requires at least 2 `coordinates`, got 1: ('x',)

    A composite axis must list its values: the regular form describes evenly
    spaced numbers, which can never be the tuples a ``"tuple"`` axis promises:

    >>> Axis(start=0.0, stop=10.0, num=3, data_type="tuple", coordinates=("x", "y"))
    Traceback (most recent call last):
        ...
    ValueError: a 'tuple' axis requires `values`

    That rule is derived from the ``"tuple"`` / ``"polygon"`` value MUSTs, so it
    reaches only those two: the spec constrains no custom ``dataType``'s values,
    so such an axis may use either numeric form.

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
        # The axis's form *is* regular, rather than merely carrying stray triple
        # members beside `values`. The regular-form rules below gate on this, so
        # that whether a both-forms axis loads never turns on the stray's
        # magnitude: every such axis is `validate`'s `axis.form-conflict`,
        # whether the stray reads `"num": 0` or `"num": 99` (ADR-0023).
        is_regular = has_regular and not has_values

        # At least one complete numeric form. Spec 6.1.1 states it: 'An axis
        # object MUST have either a `"values"` member or, as a compact notation
        # for a regularly spaced numeric axis, all the members `"start"`,
        # `"stop"`, and `"num"`.' An axis satisfying neither yields no
        # coordinate values at all, so no coherent axis of that shape exists,
        # which is ADR-0002's criterion for rejecting at construction. Note this
        # catches a *partial* triple too: `start` without `stop` reconstructs
        # nothing.
        #
        # Their *exclusivity* is a separate rule, inferred rather than stated,
        # and it lives in `validate` as `axis.form-conflict` (ADR-0023): an axis
        # carrying both is readable, since `coordinate_values` takes `values`.
        if not has_values and not has_regular:
            msg = "Axis requires `values` or all of `start`/`stop`/`num`"
            raise ValueError(msg)

        # Spec 6.1.1: `values`, when given, is a non-empty array.
        if self.values is not None and not self.values:
            msg = "Axis `values` must be non-empty"
            raise ValueError(msg)

        # Spec 6.1.1: `num` is "an integer greater than zero". Checked only once
        # the axis is unambiguously regular. A stray `num` beside `values` is
        # not this axis's form, and its only repair is deletion, so validating
        # its value would tell a publisher to fix a member they should drop.
        # The rule is deferred, not dropped: repair the conflict by dropping
        # `values` and this fires on the resulting regular axis, while the
        # document stays an error meanwhile via `axis.form-conflict`.
        if is_regular and self.num is not None and self.num < 1:
            msg = "Axis `num` must be a positive integer"
            raise ValueError(msg)

        # Spec 6.1.1: with `num` of 1, `start` and `stop` MUST be equal. Same
        # gate, for the same reason.
        if is_regular and self.num == 1 and self.start != self.stop:
            msg = "Axis with `num` of 1 requires equal `start` and `stop`"
            raise ValueError(msg)

        # A 'tuple'/'polygon' axis requires `values`: its values MUST be arrays,
        # never the numbers the regular form yields (spec 6.1.1, ADR-0018).
        if self.data_type in ("tuple", "polygon") and self.values is None:
            msg = f"a {self.data_type!r} axis requires `values`"
            raise ValueError(msg)

        # Spec 6.1.1: `coordinates`, when given, is a non-empty array (any axis).
        if self.coordinates is not None and not self.coordinates:
            msg = "Axis `coordinates` must be non-empty"
            raise ValueError(msg)

        # A composite axis must name its `coordinates` explicitly (ADR-0019).
        if self.data_type in ("tuple", "polygon") and self.coordinates is None:
            msg = f"a {self.data_type!r} axis requires `coordinates`"
            raise ValueError(msg)

        # A 'polygon' axis needs >= 2 coordinate identifiers (RFC 7946 3.1.1,
        # ADR-0019).
        if (
            self.data_type == "polygon"
            and self.coordinates is not None
            and len(self.coordinates) < 2
        ):
            n = len(self.coordinates)
            msg = (
                f"a 'polygon' axis requires at least 2 `coordinates`, "
                f"got {n}: {self.coordinates!r}"
            )
            raise ValueError(msg)

    @property
    def coordinate_values(self) -> Sequence[AxisValue]:
        """The explicit coordinate values, materializing the regular form.

        ``values`` wins when an axis carries both forms. Spec 6.1.1 states no
        tiebreak, but it introduces the triple "as a compact notation for a
        regularly spaced numeric axis" whose elements "MAY be reconstructed",
        making the triple the derived form. Such an axis is reported as
        ``axis.form-conflict`` (ADR-0023); this is what it resolves to meanwhile.

        Returns
        -------
        sequence
            For a value-listing axis, the ``values``; for a regular axis, the
            ``num`` evenly spaced values from ``start`` to ``stop`` inclusive.

        Examples
        --------
        >>> Axis.listed((10, 20, 30)).coordinate_values
        (10, 20, 30)
        >>> Axis.regular(5.0, 5.0, 1).coordinate_values  # num 1: start == stop
        (5.0,)
        >>> Axis(values=(1, 2), start=0.0, stop=10.0, num=3).coordinate_values
        (1, 2)
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

        A valid axis is never empty, so the length is at least 1 and an `Axis`
        never evaluates falsy. Both branches are covered: `__post_init__`
        rejects an empty ``values`` array, and rejects a non-positive ``num`` on
        a *regular* axis, which is the only kind this reads ``num`` for (per
        spec 6.1.1; a stray non-positive ``num`` beside ``values`` is
        `validate`'s ``axis.form-conflict``, and is never read here).

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
