"""Behavioral tests for the FastAPI response adapter."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from covjson_msgspec import Axis, Coverage, Domain, NdArray, decode
from covjson_msgspec.fastapi import CovJSONResponse
from covjson_msgspec.media_type import MEDIA_TYPE

app = FastAPI()


@app.get("/coverage")
def serve_coverage() -> CovJSONResponse:
    return CovJSONResponse(_coverage())


@app.get("/coverage-profiled")
def serve_profiled_coverage() -> CovJSONResponse:
    return CovJSONResponse(_coverage(), profile="urn:example")


client = TestClient(app)


def test_route_serves_coverage_json_media_type() -> None:
    response = client.get("/coverage")

    assert response.status_code == 200
    assert response.headers["content-type"] == MEDIA_TYPE
    # The body round-trips back through the core decoder to the same document.
    assert decode(response.content) == _coverage()


def test_route_advertises_profile_in_content_type() -> None:
    response = client.get("/coverage-profiled")

    assert response.status_code == 200
    assert response.headers["content-type"] == f'{MEDIA_TYPE}; profile="urn:example"'


def test_none_content_renders_empty_body() -> None:
    # Mirrors Starlette's Response: None is an empty body, not the literal null.
    assert CovJSONResponse(None).body == b""


def _coverage() -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
