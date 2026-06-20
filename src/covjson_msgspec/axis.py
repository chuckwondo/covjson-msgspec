"""Domain axes.

An axis describes the coordinate values along one domain dimension. CoverageJSON
allows several shapes, none carrying a ``type`` discriminator -- so they cannot
form a msgspec tagged union (msgspec rejects untagged unions of multiple
structs). Instead a single permissive `Axis` struct models every shape and
``__post_init__`` enforces that exactly one numeric form is present:

* **value-listing** -- an explicit ``values`` array;
* **regular (tight-packed)** -- ``start`` / ``stop`` / ``num`` describing ``num``
  evenly spaced values; and
* **composite** -- ``dataType`` ``"tuple"`` or ``"polygon"`` with named
  ``coordinates`` (used by trajectory and polygon domains).

Builders cover the two common numeric forms (`Axis.regular`, `Axis.listed`);
composite axes still *decode* fully -- their builders arrive with the
composite-domain support.
"""

from collections.abc import Iterable
from typing import Any, Literal, Self

from ._base import CovJSONStruct

# A primitive value, or a tuple covering both composite forms -- "tuple" (a flat
# tuple of primitives) and "polygon" (nested rings of positions).
#
# Two msgspec constraints shape this:
#   1. A union may contain at most ONE array-like (list/set/tuple) member, so we
#      cannot give the two composite shapes separate tuple aliases.
#   2. A recursive *type alias* (e.g. ``tuple["AxisValue", ...]``) is not
#      resolved by msgspec on Python 3.11 (the PEP 695 ``type`` statement that
#      would resolve it needs 3.12).
# So composite values use ``tuple[Any, ...]``: the top level decodes to a tuple,
# with ``Any`` for the rare nested polygon interior.
AxisValue = float | int | str | tuple[Any, ...]


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

    A regular axis decodes from the tight-packed form (camelCase wire names map
    to snake_case attributes):

    >>> import msgspec
    >>> ax = msgspec.json.decode(b'{"start": 0, "stop": 10, "num": 3}', type=Axis)
    >>> ax.coordinate_values
    (0.0, 5.0, 10.0)
    """

    values: tuple[AxisValue, ...] | None = None
    start: float | None = None
    stop: float | None = None
    num: int | None = None
    # Wire name ``dataType``; ``None`` means the default "primitive".
    data_type: Literal["primitive", "tuple", "polygon"] | None = None
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
            raise ValueError(
                "Axis requires exactly one of `values` or `start`/`stop`/`num`"
            )

        if self.num is not None and self.num < 1:
            raise ValueError("Axis `num` must be a positive integer")

        if self.data_type in ("tuple", "polygon") and self.coordinates is None:
            raise ValueError(f"a {self.data_type!r} axis requires `coordinates`")

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
        """Build a regular (tight-packed) axis of ``num`` evenly spaced values.

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
