"""Coverage domains.

A `Domain` describes the coordinate space of a coverage: its named `axes` and
the `referencing` that ties coordinate identifiers to reference systems. The
``domain_type`` names a well-known layout (``"Grid"``, ``"Point"``,
``"Trajectory"``, ...).

Builders construct the common domain types with their expected axes
(`Domain.grid`, `Domain.point`, `Domain.point_series`, `Domain.vertical_profile`,
`Domain.trajectory`), and the ``x`` / ``y`` / ``z`` / ``t`` properties give
convenient access to the standard axes.
"""

from collections.abc import Iterable
from typing import Self

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.axis import Axis
from covjson_msgspec.referencing import ReferenceSystemConnection


def _referencing(
    referencing: Iterable[ReferenceSystemConnection] | None,
) -> tuple[ReferenceSystemConnection, ...]:
    return () if referencing is None else tuple(referencing)


class Domain(CovJSONStruct, frozen=True, tag="Domain"):
    """The coordinate space of a coverage: its axes and their referencing.

    Examples
    --------
    >>> from covjson_msgspec import Axis
    >>> grid = Domain.grid(
    ...     x=Axis.regular(-180.0, 180.0, 5),
    ...     y=Axis.regular(-90.0, 90.0, 3),
    ... )
    >>> grid.domain_type
    'Grid'
    >>> grid.x.coordinate_values
    (-180.0, -90.0, 0.0, 90.0, 180.0)
    >>> sorted(grid.axes)
    ['x', 'y']

    A domain decodes its axes (camelCase wire names map to snake_case):

    >>> import msgspec
    >>> dom = msgspec.json.decode(
    ...     b'{"type": "Domain", "domainType": "Point",'
    ...     b' "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}}',
    ...     type=Domain,
    ... )
    >>> dom.domain_type
    'Point'
    >>> dom.x.coordinate_values
    (1.0,)
    """

    axes: dict[str, Axis]
    domain_type: str | None = None
    referencing: tuple[ReferenceSystemConnection, ...] = ()

    @property
    def x(self) -> Axis | None:
        """The ``x`` axis, if present."""
        return self.axes.get("x")

    @property
    def y(self) -> Axis | None:
        """The ``y`` axis, if present."""
        return self.axes.get("y")

    @property
    def z(self) -> Axis | None:
        """The ``z`` axis, if present."""
        return self.axes.get("z")

    @property
    def t(self) -> Axis | None:
        """The ``t`` axis, if present."""
        return self.axes.get("t")

    @classmethod
    def grid(
        cls,
        *,
        x: Axis,
        y: Axis,
        z: Axis | None = None,
        t: Axis | None = None,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a Grid domain from independent ``x`` / ``y`` (and ``z`` / ``t``).

        Parameters
        ----------
        x, y
            The horizontal axes (required).
        z, t
            Optional vertical and time axes.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A Grid domain.
        """
        axes: dict[str, Axis] = {"x": x, "y": y}

        if z is not None:
            axes["z"] = z

        if t is not None:
            axes["t"] = t

        return cls(axes=axes, domain_type="Grid", referencing=_referencing(referencing))

    @classmethod
    def point(
        cls,
        *,
        x: Axis,
        y: Axis,
        z: Axis | None = None,
        t: Axis | None = None,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a Point domain (single-valued x/y; optional z/t).

        Returns
        -------
        Domain
            A Point domain.
        """
        axes: dict[str, Axis] = {"x": x, "y": y}

        if z is not None:
            axes["z"] = z

        if t is not None:
            axes["t"] = t

        return cls(
            axes=axes, domain_type="Point", referencing=_referencing(referencing)
        )

    @classmethod
    def point_series(
        cls,
        *,
        x: Axis,
        y: Axis,
        t: Axis,
        z: Axis | None = None,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a PointSeries domain (a time series at a single ``x`` / ``y``).

        Parameters
        ----------
        x, y
            The (single-valued) horizontal axes.
        t
            The time axis the series varies over.
        z
            Optional (single-valued) vertical axis.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A PointSeries domain.
        """
        axes: dict[str, Axis] = {"x": x, "y": y, "t": t}

        if z is not None:
            axes["z"] = z

        return cls(
            axes=axes, domain_type="PointSeries", referencing=_referencing(referencing)
        )

    @classmethod
    def vertical_profile(
        cls,
        *,
        x: Axis,
        y: Axis,
        z: Axis,
        t: Axis | None = None,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a VerticalProfile domain (values along ``z`` at one ``x`` / ``y``).

        Parameters
        ----------
        x, y
            The (single-valued) horizontal axes.
        z
            The vertical axis the profile varies over.
        t
            Optional (single-valued) time axis.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A VerticalProfile domain.
        """
        axes: dict[str, Axis] = {"x": x, "y": y, "z": z}

        if t is not None:
            axes["t"] = t

        return cls(
            axes=axes,
            domain_type="VerticalProfile",
            referencing=_referencing(referencing),
        )

    @classmethod
    def trajectory(
        cls,
        composite: Axis,
        *,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a Trajectory domain from a single composite axis.

        Parameters
        ----------
        composite
            The composite (``dataType="tuple"``) axis whose coordinates are the
            trajectory's t/x/y(/z) tuples; stored under the ``"composite"`` key.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A Trajectory domain.
        """
        return cls(
            axes={"composite": composite},
            domain_type="Trajectory",
            referencing=_referencing(referencing),
        )
