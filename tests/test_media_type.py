"""Behavioral tests for the media type constant and HTTP boundary helpers."""

import pytest

from covjson_msgspec import (
    MEDIA_TYPE,
    Axis,
    Coverage,
    Domain,
    NdArray,
    decode,
    decode_response,
    encode_response,
    is_coverage_json_media_type,
    media_type,
)


def test_media_type_value() -> None:
    assert MEDIA_TYPE == "application/prs.coverage+json"
    assert MEDIA_TYPE.endswith("+json")
    assert "prs." in MEDIA_TYPE


@pytest.mark.parametrize(
    "value",
    [
        "application/prs.coverage+json",
        "application/prs.coverage+json; charset=utf-8",
        '  application/prs.coverage+json; profile="http://example.com/p"  ',
        "APPLICATION/PRS.Coverage+JSON",
    ],
)
def test_is_coverage_json_media_type_accepts(value: str) -> None:
    assert is_coverage_json_media_type(value)


@pytest.mark.parametrize(
    "value",
    [
        "application/json",
        "text/json",
        "application/prs.coverage+jsonx",
        "",
    ],
)
def test_is_coverage_json_media_type_rejects(value: str) -> None:
    assert not is_coverage_json_media_type(value)


def test_media_type_builder() -> None:
    assert media_type() == MEDIA_TYPE
    assert media_type("urn:a") == f'{MEDIA_TYPE}; profile="urn:a"'
    assert media_type("urn:a", "urn:b") == f'{MEDIA_TYPE}; profile="urn:a urn:b"'
    # A profiled content type still validates as CoverageJSON.
    assert is_coverage_json_media_type(media_type("urn:a", "urn:b"))


def test_encode_response_returns_body_and_type() -> None:
    body, content_type = encode_response(_coverage())

    assert content_type == MEDIA_TYPE
    assert isinstance(body, bytes)
    # The body is the JSON encoding and round-trips back to a Coverage.
    assert decode(body) == _coverage()


def test_encode_response_with_profile() -> None:
    body, content_type = encode_response(_coverage(), profile="urn:a")
    assert content_type == f'{MEDIA_TYPE}; profile="urn:a"'
    assert decode(body) == _coverage()

    _, content_type = encode_response(_coverage(), profile=["urn:a", "urn:b"])
    assert content_type == f'{MEDIA_TYPE}; profile="urn:a urn:b"'

    # The profiled content type round-trips through the inbound check.
    assert decode_response(body, content_type) == _coverage()


def test_decode_response_with_matching_content_type() -> None:
    body, content_type = encode_response(_coverage())

    assert decode_response(body, content_type) == _coverage()


def test_decode_response_without_content_type_check() -> None:
    body, _ = encode_response(_coverage())

    assert decode_response(body) == _coverage()


def test_decode_response_rejects_wrong_content_type() -> None:
    body, _ = encode_response(_coverage())

    with pytest.raises(ValueError, match="content type"):
        decode_response(body, "application/json")


def _coverage() -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
