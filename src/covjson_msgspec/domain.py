"""Coverage domains.

A `Domain` describes the coordinate space of a coverage: its named `axes` and
the `referencing` that ties coordinate identifiers to reference systems. The
``domain_type`` names a well-known layout (``"Grid"``, ``"Point"``,
``"Trajectory"``, ...).

Builders construct the common domain types with their expected axes
(`Domain.grid`, `Domain.point`, `Domain.point_series`, `Domain.vertical_profile`,
`Domain.trajectory`), and the ``x`` / ``y`` / ``z`` / ``t`` properties give
convenient access to the standard axes.

Spec: [Domain objects](https://github.com/covjson/specification/blob/master/spec.md#61-domain-objects).
The well-known ``domain_type`` values and their axis rules are validated against
the [Common Domain Types](https://github.com/covjson/specification/blob/master/domain-types.md)
specification (see `covjson_msgspec.validation`).
"""

from collections.abc import Iterable
from typing import Self

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.axis import Axis, PolygonCoords, RingCoords
from covjson_msgspec.referencing import ReferenceSystemConnection


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
    >>> blob = '''
    ... {
    ...   "type": "Domain",
    ...   "domainType": "Point",
    ...   "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
    ... }
    ... '''
    >>> dom = msgspec.json.decode(blob, type=Domain)
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

        Examples
        --------
        >>> dom = Domain.grid(x=Axis.regular(0.0, 10.0, 3), y=Axis.listed((0.0, 1.0)))
        >>> dom.domain_type
        'Grid'
        >>> dom.x.coordinate_values
        (0.0, 5.0, 10.0)
        >>> sorted(dom.axes)
        ['x', 'y']
        """
        return cls(
            axes=_axes(x=x, y=y, z=z, t=t),
            domain_type="Grid",
            referencing=_referencing(referencing),
        )

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

        Examples
        --------
        >>> dom = Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,)))
        >>> dom.domain_type
        'Point'
        >>> (dom.x.coordinate_values, dom.y.coordinate_values)
        ((1.0,), (2.0,))
        """
        return cls(
            axes=_axes(x=x, y=y, z=z, t=t),
            domain_type="Point",
            referencing=_referencing(referencing),
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

        Examples
        --------
        >>> dom = Domain.point_series(
        ...     x=Axis.listed((1.0,)),
        ...     y=Axis.listed((2.0,)),
        ...     t=Axis.listed(("2020-01-01", "2020-01-02", "2020-01-03")),
        ... )
        >>> dom.domain_type
        'PointSeries'
        >>> dom.t.coordinate_values
        ('2020-01-01', '2020-01-02', '2020-01-03')
        """
        return cls(
            axes=_axes(x=x, y=y, t=t, z=z),
            domain_type="PointSeries",
            referencing=_referencing(referencing),
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

        Examples
        --------
        >>> dom = Domain.vertical_profile(
        ...     x=Axis.listed((1.0,)),
        ...     y=Axis.listed((2.0,)),
        ...     z=Axis.listed((10.0, 20.0, 30.0)),
        ... )
        >>> dom.domain_type
        'VerticalProfile'
        >>> dom.z.coordinate_values
        (10.0, 20.0, 30.0)
        """
        return cls(
            axes=_axes(x=x, y=y, z=z, t=t),
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

        Examples
        --------
        >>> composite = Axis.tuple(
        ...     [("2020-01-01T00:00:00Z", 1.0, 10.0)], coordinates=("t", "x", "y")
        ... )
        >>> dom = Domain.trajectory(composite)
        >>> dom.domain_type
        'Trajectory'
        >>> dom.axes["composite"].coordinates
        ('t', 'x', 'y')
        """
        return cls(
            axes={"composite": composite},
            domain_type="Trajectory",
            referencing=_referencing(referencing),
        )

    @classmethod
    def multipoint(
        cls,
        composite: Axis,
        *,
        t: Axis | None = None,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a MultiPoint domain from a composite axis of positions.

        Parameters
        ----------
        composite
            The composite (``dataType="tuple"``) axis whose coordinates are the
            points' x/y(/z) tuples; stored under the ``"composite"`` key.
        t
            Optional single-valued time axis shared by the points.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A MultiPoint domain.

        Examples
        --------
        >>> composite = Axis.tuple([(1.0, 10.0), (2.0, 20.0)], coordinates=("x", "y"))
        >>> dom = Domain.multipoint(composite)
        >>> dom.domain_type
        'MultiPoint'
        >>> dom.axes["composite"].values
        ((1.0, 10.0), (2.0, 20.0))
        """
        return cls(
            axes=_axes(composite=composite, t=t),
            domain_type="MultiPoint",
            referencing=_referencing(referencing),
        )

    @classmethod
    def multipoint_series(
        cls,
        composite: Axis,
        t: Axis,
        *,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a MultiPointSeries domain (the points sampled over time).

        Parameters
        ----------
        composite
            The composite (``dataType="tuple"``) axis whose coordinates are the
            points' x/y(/z) tuples; stored under the ``"composite"`` key.
        t
            The time axis the series varies over.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A MultiPointSeries domain.

        Examples
        --------
        >>> composite = Axis.tuple([(1.0, 10.0), (2.0, 20.0)], coordinates=("x", "y"))
        >>> dom = Domain.multipoint_series(
        ...     composite, Axis.listed(("2020-01-01", "2020-01-02"))
        ... )
        >>> dom.domain_type
        'MultiPointSeries'
        >>> sorted(dom.axes)
        ['composite', 't']
        """
        return cls(
            axes={"composite": composite, "t": t},
            domain_type="MultiPointSeries",
            referencing=_referencing(referencing),
        )

    @classmethod
    def section(
        cls,
        composite: Axis,
        z: Axis,
        *,
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a Section domain (a vertical slice along a trajectory).

        Parameters
        ----------
        composite
            The composite (``dataType="tuple"``) axis whose coordinates are the
            trajectory's t/x/y tuples; stored under the ``"composite"`` key.
        z
            The vertical axis the section varies over.
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A Section domain.

        Examples
        --------
        >>> composite = Axis.tuple(
        ...     [("2020-01-01T00:00:00Z", 1.0, 10.0)], coordinates=("t", "x", "y")
        ... )
        >>> dom = Domain.section(composite, Axis.listed((10.0, 20.0)))
        >>> dom.domain_type
        'Section'
        >>> dom.z.coordinate_values
        (10.0, 20.0)
        """
        return cls(
            axes={"composite": composite, "z": z},
            domain_type="Section",
            referencing=_referencing(referencing),
        )

    @classmethod
    def polygon(
        cls,
        exterior: RingCoords,
        *,
        holes: Iterable[RingCoords] = (),
        z: Axis | None = None,
        t: Axis | None = None,
        coordinates: Iterable[str] = ("x", "y"),
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a Polygon domain from a single polygon.

        Parameters
        ----------
        exterior
            The exterior linear ring: a sequence of positions, each a sequence of
            coordinate values ordered to match ``coordinates``. Should be closed
            (first position repeated last).
        holes
            Interior rings (holes), in the same form as ``exterior``.
        z, t
            Optional single-valued vertical and time axes for the polygon.
        coordinates
            The coordinate identifiers each position provides (default
            ``x`` / ``y``).
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A Polygon domain whose ``composite`` axis holds the one polygon.

        Examples
        --------
        >>> dom = Domain.polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)])
        >>> dom.domain_type
        'Polygon'
        >>> dom.axes["composite"].data_type
        'polygon'
        """
        composite = Axis.polygon([(exterior, *holes)], coordinates=coordinates)
        return cls(
            axes=_axes(composite=composite, z=z, t=t),
            domain_type="Polygon",
            referencing=_referencing(referencing),
        )

    @classmethod
    def multipolygon(
        cls,
        polygons: Iterable[PolygonCoords],
        *,
        z: Axis | None = None,
        t: Axis | None = None,
        coordinates: Iterable[str] = ("x", "y"),
        referencing: Iterable[ReferenceSystemConnection] | None = None,
    ) -> Self:
        """Build a MultiPolygon domain from several polygons.

        Parameters
        ----------
        polygons
            The polygons; each is a sequence of linear rings (the exterior ring
            first, then any holes), in the form `Axis.polygon` accepts.
        z, t
            Optional single-valued vertical and time axes shared by the polygons.
        coordinates
            The coordinate identifiers each position provides (default
            ``x`` / ``y``).
        referencing
            Reference-system connections for the domain's coordinates.

        Returns
        -------
        Domain
            A MultiPolygon domain whose ``composite`` axis holds the polygons.

        Examples
        --------
        >>> square = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]
        >>> other = [[(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0)]]
        >>> dom = Domain.multipolygon([square, other])
        >>> dom.domain_type
        'MultiPolygon'
        >>> len(dom.axes["composite"].values)
        2
        """
        composite = Axis.polygon(polygons, coordinates=coordinates)
        return cls(
            axes=_axes(composite=composite, z=z, t=t),
            domain_type="MultiPolygon",
            referencing=_referencing(referencing),
        )


def _referencing(
    referencing: Iterable[ReferenceSystemConnection] | None,
) -> tuple[ReferenceSystemConnection, ...]:
    return () if referencing is None else tuple(referencing)


def _axes(**candidates: Axis | None) -> dict[str, Axis]:
    # Assemble a name->Axis mapping, dropping the unset (None) optional axes.
    return {name: axis for name, axis in candidates.items() if axis is not None}
