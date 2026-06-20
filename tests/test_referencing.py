"""Behavioral tests for reference systems and connections."""

import msgspec
import pytest

from covjson_msgspec import (
    Concept,
    GeographicCRS,
    IdentifierRS,
    ReferenceSystem,
    ReferenceSystemConnection,
    TemporalRS,
)


def test_reference_system_union_dispatch() -> None:
    geo = msgspec.json.decode(
        b'{"type": "GeographicCRS", "id": "x"}', type=ReferenceSystem
    )
    assert isinstance(geo, GeographicCRS)

    temporal = msgspec.json.decode(
        b'{"type": "TemporalRS", "calendar": "Gregorian"}', type=ReferenceSystem
    )
    assert isinstance(temporal, TemporalRS)


def test_connection_roundtrips() -> None:
    rsc = ReferenceSystemConnection(
        coordinates=("x", "y"),
        system=GeographicCRS(id="http://www.opengis.net/def/crs/OGC/1.3/CRS84"),
    )
    back = msgspec.json.decode(msgspec.json.encode(rsc), type=ReferenceSystemConnection)
    assert back == rsc
    assert isinstance(back.system, GeographicCRS)


def test_temporal_rs_requires_calendar_on_decode() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"type": "TemporalRS"}', type=ReferenceSystem)


def test_identifier_rs_target_concept_roundtrips() -> None:
    rs = IdentifierRS(target_concept=Concept(label={"en": "Land cover"}))
    data = msgspec.json.encode(rs)
    assert b'"targetConcept"' in data  # camelCase wire name

    back = msgspec.json.decode(data, type=IdentifierRS)
    assert back.target_concept.label == {"en": "Land cover"}
