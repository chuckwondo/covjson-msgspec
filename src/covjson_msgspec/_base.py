"""Shared msgspec configuration and wire types for the CoverageJSON model.

Spec: [JSON-LD `@context`][spec-jsonld] (modelled by `JsonLdContext`).

[spec-jsonld]: https://github.com/covjson/specification/blob/master/spec.md#8-json-ld
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import msgspec


class CovJSONStruct(
    msgspec.Struct,
    frozen=True,
    omit_defaults=True,
    rename="camel",
):
    """Common base for all CoverageJSON structs.

    The configuration lives here once (via the class keyword arguments) so the
    whole model is consistent:

    * ``frozen=True``: instances are immutable and, when *every* field is
      hashable, hashable too. In practice many structs carry a ``dict`` member
      (notably i18n ``label``/``description`` maps), which keeps those instances
      unhashable; ``frozen`` still guarantees their attributes cannot be
      rebound. NOTE: msgspec does **not** inherit ``frozen`` (a non-frozen
      struct cannot inherit a frozen one), so every concrete subclass must
      restate ``frozen=True``. A frozen-forcing metaclass was considered to
      drop that repetition and rejected (it breaks ``msgspec.defstruct`` and
      adds runtime magic); ``tests/test_base.py`` enforces the invariant
      instead. See ADR-0012.
    * ``omit_defaults=True``: fields left at their default are dropped on
      encode, keeping output minimal and spec-clean. (Inherited by subclasses.)
    * ``rename="camel"``: snake_case Python attributes map to CoverageJSON's
      lowerCamelCase wire names (``axis_names`` <-> ``axisNames``). CoverageJSON
      uses lowerCamelCase uniformly, so a single ``"camel"`` rule covers every
      field; the rare exceptions (``@context``, a ``type`` data member) use an
      explicit per-field ``name=``. (Inherited by subclasses.)

    Sequence members are modelled as ``tuple`` rather than ``list`` so they are
    immutable and hashable; mapping members stay ``dict`` because there is no
    msgspec-decodable frozen mapping.
    """


# The value shape of a JSON-LD ``@context`` (CoverageJSON section 8): an IRI
# string, an inline context object, an array mixing those, or ``null`` (a context
# reset). Parsed to native Python and preserved verbatim, never interpreted (no
# JSON-LD resolution or default-context application). Section 8 places it at the
# document root, but the root-able structs are reused in nested positions, so a
# nested ``@context`` is preserved there too rather than dropped.
# ``Mapping`` rather than ``dict``: msgspec still builds a plain ``dict`` on
# decode, but the read-only annotation lets a type checker flag mutation of the
# frozen struct member that holds it.
JsonLdContext = (
    str | Mapping[str, Any] | tuple[str | Mapping[str, Any] | None, ...] | None
)
