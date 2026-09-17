"""Shared sample coverages for the bridge tests.

The ``tests`` directory is on ``pythonpath`` (see ``[tool.pytest.ini_options]``
in ``pyproject.toml``), so test modules import these as
``from samples import ...`` without a ``tests`` package.
"""

from covjson_msgspec import (
    Axis,
    Coverage,
    Domain,
    ReferenceSystem,
    ReferenceSystemConnection,
)


def gregorian_series(values: tuple[str, ...]) -> Coverage:
    """A PointSeries whose ``t`` axis is under a standard-calendar TemporalRS.

    The referencing is what makes the bridges parse ``t``, so it is the whole
    point of the fixture. The ranges are not, so there are none.
    """
    return Coverage(
        domain=Domain.point_series(
            x=Axis.listed((1.0,)),
            y=Axis.listed((2.0,)),
            t=Axis.listed(values),
            referencing=(
                ReferenceSystemConnection(
                    coordinates=("t",),
                    system=ReferenceSystem.temporal(calendar="Gregorian"),
                ),
            ),
        ),
        ranges={},
    )
