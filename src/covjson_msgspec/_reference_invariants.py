"""Reference-system invariants shared by `refine` and `validate`.

A reference system's ``type`` names one of five modelled kinds or a custom value
(CoverageJSON section 7.2). `KNOWN` is the set of modelled kinds;
`missing_required_member` encodes, in one place, which required member each
modelled kind must carry.

Both consumers read from here, so they cannot disagree:
`covjson_msgspec.referencing.ReferenceSystem.refine` uses
`missing_required_member` to decide whether a known kind is well-formed enough to
project to its precise variant, and `covjson_msgspec.validation` uses it to emit
the matching ``temporal.missing-calendar`` / ``identifier.missing-target-concept``
error. This module lives under a ``_`` prefix, exporting non-underscore names, so
``validation`` can share the rule without importing a ``referencing`` private.

Spec: [Reference system objects](https://github.com/covjson/specification/blob/master/spec.md#5-reference-system-objects).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from covjson_msgspec.referencing import ReferenceSystem

# The five reference-system ``type`` values this library models. Any other value
# is a custom (section 7.2) or unrecognized type, which has no required-member
# rule and is treated as opaque.
KNOWN: frozenset[str] = frozenset(
    {"GeographicCRS", "ProjectedCRS", "VerticalCRS", "TemporalRS", "IdentifierRS"}
)


def missing_required_member(system: ReferenceSystem) -> str | None:
    """Return the wire name of a required member absent from ``system``.

    A modelled kind that omits its required member (``TemporalRS`` without
    ``calendar``, ``IdentifierRS`` without ``targetConcept``) is nonconformant.
    ``None`` means the system is either a well-formed known kind or an
    unrecognized (custom/opaque) type, neither of which is missing anything.

    Parameters
    ----------
    system
        The permissive reference-system core to inspect.

    Returns
    -------
    str or None
        The wire name of the missing required member, or ``None``.

    Examples
    --------
    >>> from covjson_msgspec.referencing import ReferenceSystem
    >>> missing_required_member(ReferenceSystem.temporal(calendar="Gregorian"))
    >>> missing_required_member(ReferenceSystem(type_="TemporalRS"))
    'calendar'
    >>> missing_required_member(ReferenceSystem(type_="uor:HEALPixRS"))
    """
    match system.type_:
        case "TemporalRS" if system.calendar is None:
            return "calendar"

        case "IdentifierRS" if system.target_concept is None:
            return "targetConcept"

        case _:
            return None
