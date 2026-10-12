"""Behavioral tests for the FastAPI response adapter."""

import asyncio
import inspect

import httpx2
from fastapi import FastAPI, Response
from pydantic import BaseModel

from covjson_msgspec import Axis, Coverage, Domain, NdArray, decode, schema_ref
from covjson_msgspec.fastapi import CovJSONResponse, add_openapi_schemas
from covjson_msgspec.media_type import MEDIA_TYPE

app = FastAPI()


@app.get("/coverage")
def serve_coverage() -> CovJSONResponse:
    return CovJSONResponse(_coverage())


@app.get("/coverage-profiled")
def serve_profiled_coverage() -> CovJSONResponse:
    return CovJSONResponse(_coverage(), profile="urn:example")


def test_route_serves_coverage_json_media_type() -> None:
    response = _get(app, "/coverage")

    assert response.status_code == 200
    assert response.headers["content-type"] == MEDIA_TYPE
    # The body round-trips back through the core decoder to the same document.
    assert decode(response.content) == _coverage()


def test_route_advertises_profile_in_content_type() -> None:
    response = _get(app, "/coverage-profiled")

    assert response.status_code == 200
    assert response.headers["content-type"] == f'{MEDIA_TYPE}; profile="urn:example"'


def test_none_content_renders_empty_body() -> None:
    # Mirrors Starlette's Response: None is an empty body, not the literal null.
    assert CovJSONResponse(None).body == b""


def test_covjsonresponse_forwards_headers_and_owns_its_media_type() -> None:
    response = CovJSONResponse(_coverage(), headers={"x-test": "1"})

    assert response.headers["x-test"] == "1"  # forwarded to the Starlette Response
    assert response.headers["content-type"] == MEDIA_TYPE

    # The class owns the media type: no `media_type` parameter and no `**kwargs`
    # escape hatch, so a caller cannot override it and serve non-CoverageJSON.
    params = inspect.signature(CovJSONResponse.__init__).parameters
    assert "media_type" not in params
    assert inspect.Parameter.VAR_KEYWORD not in {p.kind for p in params.values()}


_documented_app = FastAPI()


@_documented_app.get(
    "/coverage",
    response_class=Response,
    responses={200: {"content": {MEDIA_TYPE: {"schema": schema_ref(Coverage)}}}},
)
def serve_documented() -> CovJSONResponse:
    return CovJSONResponse(_coverage())


add_openapi_schemas(_documented_app)


def test_openapi_documents_the_coverage_endpoint() -> None:
    openapi = _get(_documented_app, "/openapi.json").json()

    # The endpoint is only truly documented if the referenced component is present.
    assert "CoverageJSON.Coverage" in openapi["components"]["schemas"]

    # The base Response keeps the documented content pristine: only our media type,
    # only the component ref (no stray application/json, no raw-body string schema).
    content = openapi["paths"]["/coverage"]["get"]["responses"]["200"]["content"]
    assert content == {MEDIA_TYPE: {"schema": schema_ref(Coverage)}}


def test_openapi_document_is_version_3_1_or_later() -> None:
    # The component schemas are JSON Schema draft 2020-12, the dialect OpenAPI
    # adopted in 3.1, so they describe the API only in a 3.1 or later document
    # (ADR-0009). FastAPI emits 3.1 from 0.99.0. Whatever the declared floor,
    # the bench group's covjson-pydantic requires pydantic 2, which holds every
    # resolution of uv.lock at fastapi 0.100 or later, so this fails on an older
    # fastapi only in an environment built outside the lock.
    version = _documented_app.openapi()["openapi"]

    assert tuple(int(part) for part in version.split(".")[:2]) >= (3, 1)


_response_class_app = FastAPI()


@_response_class_app.get("/coverage", response_class=CovJSONResponse)
def serve_via_response_class() -> CovJSONResponse:
    return CovJSONResponse(_coverage())


add_openapi_schemas(_response_class_app)


def test_covjsonresponse_is_usable_as_response_class() -> None:
    # Regression guard for the spelled-out `status_code`: without it, using
    # CovJSONResponse as a route's response_class makes OpenAPI generation raise an
    # UnboundLocalError. Here generation must simply succeed.
    schemas = _response_class_app.openapi()["components"]["schemas"]

    assert "CoverageJSON.Coverage" in schemas


class Parameter(BaseModel):
    """A host app's own model, named to collide with ours if it were not namespaced."""

    value: float


_collision_app = FastAPI()


@_collision_app.get("/host")
def serve_host() -> Parameter:
    return Parameter(value=1.0)


add_openapi_schemas(_collision_app)


def test_registered_schemas_do_not_clobber_host_components() -> None:
    schemas = _collision_app.openapi()["components"]["schemas"]

    # The host's own `Parameter` and ours coexist under distinct keys.
    assert schemas["Parameter"]["properties"]["value"]["type"] == "number"
    assert "CoverageJSON.Parameter" in schemas


def test_add_openapi_schemas_is_idempotent_and_order_independent() -> None:
    app = FastAPI()

    # Generate the schema BEFORE registering, so this also exercises the
    # order-independent path (the component still appears despite the pre-cache).
    app.openapi()
    add_openapi_schemas(app)
    once = dict(app.openapi()["components"]["schemas"])
    assert "CoverageJSON.Coverage" in once

    # Registering again must not change the exposed set (idempotent).
    add_openapi_schemas(app)
    assert app.openapi()["components"]["schemas"] == once


def _get(app: FastAPI, path: str) -> httpx2.Response:
    """Request ``path`` from ``app`` in process, through httpx2's ASGI transport.

    Starlette's TestClient would do the same, but the HTTP client it imports
    depends on the Starlette release (httpx below 1.2, httpx2 from 1.2), so it
    cannot run across the supported fastapi range with httpx2 alone.
    """

    async def request() -> httpx2.Response:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def _coverage() -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
