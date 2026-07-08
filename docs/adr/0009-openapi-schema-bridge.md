# ADR-0009: OpenAPI schema bridging from the msgspec types

## Status

Accepted

## Context

FastAPI builds its OpenAPI documentation by introspecting the pydantic models a
route declares. This library models CoverageJSON with msgspec, which FastAPI
cannot introspect, so an endpoint returning `CovJSONResponse` (the `[fastapi]`
response adapter from issue #13) serves correctly on the wire but shows no
response schema in Swagger / Redoc. Documenting those endpoints is part of
slotting into the titiler ecosystem.

The mechanism to bridge the gap already ships with a core dependency: msgspec's
`json.schema_components` generates JSON Schema (draft 2020-12) for a set of types
and lets the caller choose the `$ref` template. Two facts make it a good fit.
First, schema generation honors the same `rename="camel"` the encoder uses, so
the generated property names are the lowerCamelCase wire names (`domainType`),
matching the encoded document. Second, draft 2020-12 is the dialect OpenAPI 3.1
adopts, and FastAPI has emitted OpenAPI 3.1.0 by default since 0.99.0
(2023-06-30), well under this project's `fastapi>=0.110` floor.

One hazard is specific to injecting a schema set into someone else's application:
msgspec names components by the bare type name, so our set includes generic names
(`Parameter`, `Unit`, `Domain`, `Category`, `Concept`, `Symbol`) that a host
application plausibly also defines. Merging bare-named components into a host's
OpenAPI `components.schemas` would silently overwrite a same-named host component
(or be overwritten by it), corrupting one side's documentation with no error.

## Decision

Split the bridge into a pure, framework-free generator in the core and a thin
FastAPI adapter, mirroring the existing `media_type.py` / `fastapi.py` split.

- `schema.py` (core, msgspec only, no new dependency): `component_schemas()`
  returns the OpenAPI `components.schemas` entries for the five CoverageJSON root
  types plus every sub-type they reference, and `schema_ref(type)` returns the
  `$ref` a route response points at. Both are framework-agnostic.
- **Namespace every component** under a single `CoverageJSON.` prefix (for
  example `CoverageJSON.Coverage`), sourced from one module constant that feeds
  both the `schema_components` ref template and `schema_ref`. This makes a
  host-application collision unrepresentable rather than a documented caveat.
- `schema_ref(type)` accepts only a root document type (`type[CoverageJSON]`), the
  kind an endpoint actually returns, so the reference it produces always resolves
  to a registered component.
- `fastapi.py` (behind `[fastapi]`): `add_openapi_schemas(app)` wraps the app's
  existing `openapi` callable and merges the components in on every call, so it is
  order-independent and idempotent, and preserves any other customization.
- Target OpenAPI 3.1 / draft 2020-12 only.

## Alternatives considered

- **Bare component names.** Simpler and prettier in Swagger, but they silently
  collide with a host application's own `Parameter` / `Unit` / `Domain`. Rejected:
  a documentation feature must not corrupt the host's documentation. A `prefix=`
  knob for callers who have verified no collision is a possible later addition,
  not a default.
- **An `app.openapi`-mutating helper with no pure core.** Convenient, but it
  buries the reusable value (the schema) inside a framework-specific effect,
  leaving non-FastAPI callers (a Litestar adapter, a static docs build) nothing to
  reuse. Rejected in favor of a pure generator with a thin adapter over it.
- **Auto-wiring every route's response schema.** The helper could try to set the
  response schema on routes returning `CovJSONResponse`, but it cannot reliably
  know which routes those are or which document type each returns. Rejected as too
  magic; the route author references a component explicitly via `schema_ref`.
- **Publishing a standalone CoverageJSON validation schema.** Out of scope: the
  official covjson.org schema is authoritative for validation, and comparing our
  output against it is the separate concern of issue #15.
- **Also supporting OpenAPI 3.0.** Would require dialect down-conversion from
  draft 2020-12. Rejected as unnecessary given the `fastapi>=0.110` floor already
  guarantees 3.1.

## Consequences

- A `CovJSONResponse` endpoint can be fully described in Swagger / Redoc with two
  lines: `add_openapi_schemas(app)` and a `schema_ref(...)` in the route's
  `responses`.
- Component names carry a `CoverageJSON.` prefix, so they read as
  `CoverageJSON.Coverage` in the schema browser. This is the accepted cost of
  collision safety when injecting into an arbitrary host application.
- The output tracks msgspec's schema generator and FastAPI's OpenAPI 3.1 default.
  A host on FastAPI below 0.99.0 (before the floor) would not align; the floor
  rules that out.
- Revisit if a user needs bare (un-namespaced) component names (add an opt-in
  `prefix=`) or OpenAPI 3.0 support (add dialect handling).
