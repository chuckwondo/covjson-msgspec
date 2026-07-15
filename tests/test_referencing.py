"""Behavioral tests for reference systems and connections."""

import msgspec
import pytest

from covjson_msgspec import (
    Concept,
    GeographicCRS,
    IdentifierRS,
    OpaqueRS,
    ProjectedCRS,
    ReferenceSystem,
    ReferenceSystemConnection,
    TemporalRS,
    VerticalCRS,
)


def test_reference_system_refines_to_its_variant() -> None:
    geo = msgspec.json.decode(
        b'{"type": "GeographicCRS", "id": "x"}', type=ReferenceSystem
    )
    assert geo.refine() == GeographicCRS(id="x")

    temporal = msgspec.json.decode(
        b'{"type": "TemporalRS", "calendar": "Gregorian"}', type=ReferenceSystem
    )
    assert temporal.refine() == TemporalRS(calendar="Gregorian")


def test_custom_type_loads_and_refines_opaque() -> None:
    # Spec 7.2: a custom (URI) type MUST still load; refine renders it opaque.
    rs = msgspec.json.decode(b'{"type": "uor:HEALPixRS"}', type=ReferenceSystem)
    assert rs.type_ == "uor:HEALPixRS"
    refined = rs.refine()
    assert isinstance(refined, OpaqueRS)
    assert refined.is_custom()
    # Spec 7.2 acceptance criterion: survives decode -> encode faithfully.
    assert msgspec.json.encode(rs) == b'{"type":"uor:HEALPixRS"}'


def test_connection_roundtrips() -> None:
    rsc = ReferenceSystemConnection(
        coordinates=("x", "y"),
        system=ReferenceSystem.geographic(
            id="http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        ),
    )
    back = msgspec.json.decode(msgspec.json.encode(rsc), type=ReferenceSystemConnection)
    assert back == rsc
    assert isinstance(back.system.refine(), GeographicCRS)


def test_temporal_rs_missing_calendar_loads_and_refines_opaque() -> None:
    # Permissive decode (ADR-0002): a calendar-less temporal RS loads rather than
    # failing at the door; refine renders it opaque (a malformed known type, not a
    # custom one), and validate() reports temporal.missing-calendar.
    rs = msgspec.json.decode(b'{"type": "TemporalRS"}', type=ReferenceSystem)
    refined = rs.refine()
    assert isinstance(refined, OpaqueRS)
    assert refined.type_ == "TemporalRS"
    assert not refined.is_custom()


def test_custom_member_incompatible_with_a_known_field_fails_to_decode() -> None:
    # The documented, narrow limitation: a custom type whose member reuses a known
    # field name at an incompatible JSON type is rejected by the typed core (ADR).
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"type": "uor:X", "calendar": 123}', type=ReferenceSystem)


def test_identifier_rs_target_concept_roundtrips() -> None:
    rs = IdentifierRS(target_concept=Concept(label={"en": "Land cover"}))
    data = msgspec.json.encode(rs)
    assert b'"targetConcept"' in data  # camelCase wire name

    back = msgspec.json.decode(data, type=IdentifierRS)
    assert back.target_concept.label == {"en": "Land cover"}


def test_concept_id_round_trips() -> None:
    # The Spec 5.3 example gives each concept an id (a concept URI); it must survive
    # a decode -> encode round trip (a fidelity gap #113 closes).
    rs = IdentifierRS(
        target_concept=Concept(
            id="http://dbpedia.org/resource/Country", label={"en": "Country"}
        )
    )

    back = msgspec.json.decode(msgspec.json.encode(rs), type=IdentifierRS)
    assert back.target_concept.id == "http://dbpedia.org/resource/Country"


def test_every_known_tag_refines_to_its_variant() -> None:
    # The known tag strings are duplicated across each variant's ``tag=``, refine's
    # match arms, and the builders. Asserting the exact mapping (not just
    # distinctness) also guards against a variant swap between two refine arms.
    expected = [
        (ReferenceSystem.geographic(), GeographicCRS),
        (ReferenceSystem.projected(), ProjectedCRS),
        (ReferenceSystem.vertical(), VerticalCRS),
        (ReferenceSystem.temporal(calendar="Gregorian"), TemporalRS),
        (
            ReferenceSystem.identifier(target_concept=Concept(label={"en": "x"})),
            IdentifierRS,
        ),
    ]

    for core, variant in expected:
        assert type(core.refine()) is variant
