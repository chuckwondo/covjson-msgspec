"""Behavioral tests for Coverage / CoverageCollection and the codec helpers."""

import msgspec
import pytest

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    NdArray,
    ObservedProperty,
    Parameter,
    ReferenceSystemConnection,
    Unit,
    VerticalCRS,
    decode,
    decode_coverage,
    decode_coverage_collection,
    encode,
    i18n,
)


def _point_coverage() -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )


def test_coverage_roundtrips() -> None:
    cov = _point_coverage()
    back = msgspec.json.decode(msgspec.json.encode(cov), type=Coverage)

    assert back == cov
    arr = back.ranges["t"]
    assert isinstance(arr, NdArray)
    assert arr.values == (280.0,)


def test_coverage_range_can_be_url_string() -> None:
    blob = (
        b'{"type":"Coverage",'
        b'"domain":"http://ex/domain.covjson",'
        b'"ranges":{"t":"http://ex/t.covjson"}}'
    )
    cov = decode_coverage(blob)

    assert cov.domain == "http://ex/domain.covjson"
    assert cov.ranges["t"] == "http://ex/t.covjson"


def test_effective_domain_type_prefers_inline_domain() -> None:
    cov = Coverage(
        domain=Domain(axes={"x": Axis.listed((1.0,))}, domain_type="Grid"),
        ranges={},
        domain_type="Point",  # a (spec-discouraged) mismatch; the domain wins
    )

    assert cov.effective_domain_type == "Grid"


def test_effective_domain_type_falls_back_for_url_domain() -> None:
    # A URL-reference domain contributes no type, so the coverage-level one (such
    # as a value a collection supplied) is used.
    cov = Coverage(
        domain="http://ex/domain.covjson", ranges={}, domain_type="Trajectory"
    )

    assert cov.effective_domain_type == "Trajectory"


def test_effective_domain_type_is_none_when_unspecified() -> None:
    cov = Coverage(domain=Domain(axes={"x": Axis.listed((1.0,))}), ranges={})

    assert cov.effective_domain_type is None


def test_decode_dispatches_on_type() -> None:
    cov = decode(encode(_point_coverage()))

    assert isinstance(cov, Coverage)


def test_decode_dispatches_collection() -> None:
    collection = CoverageCollection(coverages=(_point_coverage(),))
    back = decode(encode(collection))

    assert isinstance(back, CoverageCollection)
    assert len(back.coverages) == 1


def test_decode_coverage_collection_helper() -> None:
    collection = CoverageCollection(coverages=(_point_coverage(),))
    back = decode_coverage_collection(encode(collection))

    assert back == collection


def test_resolved_coverages_inherits_shared_fields() -> None:
    temp = Parameter.continuous(
        ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    )
    collection = CoverageCollection(
        coverages=(_point_coverage(),),
        domain_type="Point",
        parameters={"t": temp},
    )
    (resolved,) = collection.resolved_coverages()

    assert resolved.domain_type == "Point"
    assert resolved.parameters is not None
    assert resolved.parameters["t"].unit == Unit(symbol="K")


def test_resolved_coverages_keeps_member_overrides() -> None:
    own = Parameter.continuous(
        ObservedProperty(label=i18n("Member temp")), Unit(symbol="degC")
    )
    shared = Parameter.continuous(
        ObservedProperty(label=i18n("Shared temp")), Unit(symbol="K")
    )
    member = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
        domain_type="Grid",
        parameters={"t": own},
    )
    collection = CoverageCollection(
        coverages=(member,),
        domain_type="Point",
        parameters={"t": shared},
    )
    (resolved,) = collection.resolved_coverages()

    assert resolved.domain_type == "Grid"
    assert resolved.parameters is not None
    assert resolved.parameters["t"].unit == Unit(symbol="degC")


def test_resolved_coverages_injects_referencing_into_inline_domain() -> None:
    referencing = (ReferenceSystemConnection(coordinates=("z",), system=VerticalCRS()),)
    collection = CoverageCollection(
        coverages=(_point_coverage(),),
        referencing=referencing,
    )
    (resolved,) = collection.resolved_coverages()

    assert isinstance(resolved.domain, Domain)
    assert resolved.domain.referencing == referencing


def test_resolved_coverages_skips_referencing_for_url_domain() -> None:
    member = Coverage(
        domain="http://ex/domain.covjson",
        ranges={},
    )
    collection = CoverageCollection(
        coverages=(member,),
        referencing=(
            ReferenceSystemConnection(coordinates=("z",), system=VerticalCRS()),
        ),
    )
    (resolved,) = collection.resolved_coverages()

    assert resolved.domain == "http://ex/domain.covjson"


def test_decode_rejects_unknown_type() -> None:
    with pytest.raises(msgspec.ValidationError):
        decode(b'{"type":"Nonsense"}')
