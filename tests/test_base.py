"""Invariants of the shared `CovJSONStruct` base that hold across the model.

msgspec does not inherit ``frozen`` (a non-frozen struct cannot inherit a frozen
one), so every concrete struct must restate ``frozen=True``. A struct that omits
it is silently mutable, violating the immutability the whole model relies on.
This test is the guardrail: it fails if any `CovJSONStruct` descendant is not
frozen.

A frozen-forcing metaclass over the base was considered as an alternative to the
repeated ``frozen=True`` and rejected: any custom metaclass on the base breaks
``msgspec.defstruct(bases=...)`` (it rejects re-processing the pre-slotted
namespace), and it trades an explicit, boring keyword for runtime magic. A test
enforces the same invariant with neither cost.
"""

from __future__ import annotations

from typing import cast

import msgspec

from covjson_msgspec._base import CovJSONStruct  # noqa: PLC2701


def test_every_covjson_struct_is_frozen() -> None:
    # Importing CovJSONStruct runs the package __init__, which imports every
    # struct module, so all concrete structs are defined and thus visible to
    # __subclasses__(). The floor guards that dependency: were __init__ to stop
    # importing them, the walk would be empty and this fails RED, rather than
    # passing silently over nothing.
    structs = _descendants(CovJSONStruct)
    assert len(structs) >= 20, "structs not imported; subclass walk is empty"
    unfrozen = [
        s.__qualname__
        for s in structs
        if not cast("type[msgspec.Struct]", s).__struct_config__.frozen
    ]
    assert not unfrozen, f"missing frozen=True on: {unfrozen}"


def _descendants(cls: type) -> list[type]:
    """Every subclass of ``cls``, transitively.

    Examples
    --------
    >>> class A: pass
    >>> class B(A): pass
    >>> class C(B): pass
    >>> sorted(t.__name__ for t in _descendants(A))
    ['B', 'C']
    """
    return [d for s in cls.__subclasses__() for d in (s, *_descendants(s))]
