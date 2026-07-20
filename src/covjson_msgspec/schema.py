"""Generate OpenAPI-compatible JSON Schema for the CoverageJSON types.

FastAPI builds its OpenAPI documentation from the types a route declares, but it
cannot introspect msgspec structs, so a
[`CovJSONResponse`][covjson_msgspec.fastapi.CovJSONResponse] endpoint serves correctly
on the wire yet appears schema-less in Swagger / Redoc.  This module bridges that gap:
`component_schemas` turns the CoverageJSON model types into OpenAPI
``components.schemas`` entries via msgspec's own schema generator, and `schema_ref`
produces the matching ``$ref`` a route response points at. Both are framework-agnostic
(msgspec only); the thin FastAPI wiring that consumes them lives in
`covjson_msgspec.fastapi`, behind the ``[fastapi]`` extra.

Two properties make the output drop cleanly into a host application:

- **Wire-faithful names.** Schema generation honors the same camelCase renaming
  the encoder uses, so property names are the lowerCamelCase wire names
  (``domainType``, ``parameterGroups``), not the snake_case attribute names.
- **Namespaced components.** Every component is named ``CoverageJSON.<Type>`` (for
  example ``CoverageJSON.Coverage``, ``CoverageJSON.Parameter``). Merging them into
  a host app's OpenAPI therefore cannot collide with, or overwrite, a same-named
  component the host already defines (a ``Parameter`` or ``Unit`` of its own).

The generated schema is JSON Schema draft 2020-12, the dialect [OpenAPI
3.1][openapi] adopts (FastAPI's default since 0.99.0). msgspec's schema support is
documented at [msgspec][msgspec-schema].

[openapi]: https://spec.openapis.org/oas/v3.1.0
[msgspec-schema]: https://jcristharif.com/msgspec/
"""

from __future__ import annotations

from typing import Any, Final, get_args

import msgspec

from covjson_msgspec.coverage import CoverageJSON

# Prefix that namespaces our OpenAPI component names, so merging them into a host
# application's schema cannot clash with its own components. The single source of
# truth for both the component keys (`component_schemas`) and the ``$ref`` paths
# (`schema_ref`), so the two can never drift apart.
_NAMESPACE: Final = "CoverageJSON"

# The OpenAPI ``$ref`` template, with msgspec's ``{name}`` placeholder. Passed to
# ``schema_components`` (which applies it to every internal and top-level ref) and
# reused by `schema_ref`.
_REF_TEMPLATE: Final = f"#/components/schemas/{_NAMESPACE}.{{name}}"

# The CoverageJSON root types to surface as top-level components, derived from the
# `CoverageJSON` union so the two cannot drift (a new union member is surfaced
# automatically). Feeding these pulls in every referenced sub-type (Axis,
# Parameter, the CRS types, and so on).
_ROOT_TYPES: Final = get_args(CoverageJSON)


def component_schemas() -> dict[str, dict[str, Any]]:
    """Build OpenAPI ``components.schemas`` entries for the CoverageJSON types.

    Generates a JSON Schema (draft 2020-12) component for every CoverageJSON type
    -- the five root document types plus every sub-type they reference -- keyed by
    a namespaced name (``CoverageJSON.<Type>``) so the mapping can be merged into a
    host application's OpenAPI ``components.schemas`` without colliding with its own
    components. Property names are the lowerCamelCase wire names.

    Returns
    -------
    dict of {str: dict}
        Component name (``CoverageJSON.<Type>``) to its JSON Schema. All internal
        ``$ref``s point at other keys in this same mapping.

    Notes
    -----
    msgspec applies the configured ref template to every ``$ref`` it emits but
    still keys the returned components by the bare type name, so the keys are
    renamed here to match the namespaced refs.

    Examples
    --------
    >>> schemas = component_schemas()
    >>> "CoverageJSON.Coverage" in schemas
    True
    >>> "CoverageJSON.Parameter" in schemas  # a referenced sub-type, pulled in
    True

    Property names are the camelCase wire names, not the snake_case attributes:

    >>> props = schemas["CoverageJSON.Coverage"]["properties"]
    >>> "domainType" in props and "domain_type" not in props and "@context" in props
    True

    Internal references are namespaced too, so they resolve within the mapping:

    >>> schemas["CoverageJSON.Coverage"]["properties"]["domain"]["anyOf"][1]
    {'$ref': '#/components/schemas/CoverageJSON.Domain'}
    """
    _, components = msgspec.json.schema_components(
        _ROOT_TYPES, ref_template=_REF_TEMPLATE
    )

    return {f"{_NAMESPACE}.{name}": schema for name, schema in components.items()}


def schema_ref(type: type[CoverageJSON]) -> dict[str, str]:
    """Return the OpenAPI ``$ref`` for a CoverageJSON root type's component.

    Points at the component `component_schemas` registers for ``type``, so a route
    can reference it from its ``responses`` without hand-typing (and drifting from)
    the path. Accepting only a root document type -- the kind an endpoint actually
    returns -- means the ``$ref`` always resolves to a registered component.

    Parameters
    ----------
    type
        A CoverageJSON root type (`Coverage`, `CoverageCollection`, `Domain`,
        `NdArray`, or `TiledNdArray`).

    Returns
    -------
    dict of {str: str}
        A single-entry ``{"$ref": ...}`` mapping.

    Examples
    --------
    >>> from covjson_msgspec import Coverage, TiledNdArray
    >>> schema_ref(Coverage)
    {'$ref': '#/components/schemas/CoverageJSON.Coverage'}
    >>> schema_ref(TiledNdArray)
    {'$ref': '#/components/schemas/CoverageJSON.TiledNdArray'}
    """
    return {"$ref": _REF_TEMPLATE.format(name=type.__name__)}
