"""Behavioral tests for Coverage / CoverageCollection and the codec helpers."""

import msgspec
import pytest
from msgspec import UNSET

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    NdArray,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    ReferenceSystemConnection,
    Unit,
    VerticalCRS,
    decode,
    decode_coverage,
    decode_coverage_collection,
    encode,
    i18n,
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
    assert resolved.parameters is not UNSET
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
    assert resolved.parameters is not UNSET
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


@pytest.mark.parametrize("wire_name", ["domainType", "parameters", "parameterGroups"])
@pytest.mark.parametrize(
    ("type_", "head"),
    [
        (Coverage, '"domain": "x", "ranges": {}'),
        (CoverageCollection, '"coverages": []'),
    ],
)
def test_decode_rejects_null_for_inheritance_fields(
    wire_name: str,
    type_: type[Coverage] | type[CoverageCollection],
    head: str,
) -> None:
    # The spec forbids `null` for these omittable members; UNSET typing rejects
    # it loudly at decode rather than silently coercing it to "absent" (which
    # would let a member's explicit `null` inherit the collection's value).
    blob = f"""
    {{
      "type": "{type_.__name__}",
      {head},
      "{wire_name}": null
    }}
    """

    with pytest.raises(msgspec.ValidationError, match="got `null`"):
        msgspec.json.decode(blob, type=type_)


def test_omitted_inheritance_fields_decode_to_unset() -> None:
    cov = decode_coverage(b'{"type":"Coverage","domain":"x","ranges":{}}')

    assert cov.domain_type is UNSET
    assert cov.parameters is UNSET
    assert cov.parameter_groups is UNSET
    # UNSET is omitted on encode, so an absent member round-trips as absent.
    assert b"parameters" not in encode(cov)


def test_present_empty_fields_suppress_inheritance() -> None:
    # A present-but-empty value (`{}` / `()`) is not `UNSET`: the member
    # explicitly declares "none of its own", so the collection's value must NOT
    # be grafted on. This present-vs-absent split is the whole point of the UNSET
    # modeling. It is also the regression tripwire for `_resolve`: were its
    # `is UNSET` checks "simplified" to truthiness, an empty `{}` / `()` (falsy
    # but present) would wrongly inherit, and these assertions would fail.
    shared = Parameter.continuous(
        ObservedProperty(label=i18n("Shared temp")), Unit(symbol="K")
    )
    group = ParameterGroup(members=("t",), label=i18n("Shared group"))
    member = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
        parameters={},
        parameter_groups=(),
    )
    collection = CoverageCollection(
        coverages=(member,),
        parameters={"t": shared},
        parameter_groups=(group,),
    )
    (resolved,) = collection.resolved_coverages()

    assert resolved.parameters == {}
    assert resolved.parameter_groups == ()


def test_iterable_fields_keep_empty_tuple_not_unset() -> None:
    # Scope tripwire: the always-iterable `referencing` / `shape` / `axis_names`
    # keep their `()` defaults (empty is a valid representation), unlike the five
    # inheritance fields that adopted UNSET.
    assert CoverageCollection(coverages=()).referencing == ()

    array = NdArray(data_type="float", values=(1.0,))
    assert array.shape == ()
    assert array.axis_names == ()


@pytest.mark.parametrize(
    ("context_wire", "expected"),
    [
        ('"https://covjson.org/context.jsonld"', "https://covjson.org/context.jsonld"),
        (
            '["https://covjson.org/context.jsonld",{"ex":"https://ex/"}]',
            ("https://covjson.org/context.jsonld", {"ex": "https://ex/"}),
        ),
        (
            # a `null` element (a JSON-LD reset) exercises the `| None` inside the
            # tuple, distinct from a top-level `null`
            '["https://covjson.org/context.jsonld",null]',
            ("https://covjson.org/context.jsonld", None),
        ),
        (
            '{"@vocab":"https://ex/","@version":1.1}',
            {"@vocab": "https://ex/", "@version": 1.1},
        ),
        ("null", None),
    ],
)
def test_context_preserved_through_roundtrip(
    context_wire: str, expected: object
) -> None:
    blob = (
        '{"type":"Coverage","@context":'
        + context_wire
        + ',"domain":"http://ex/d.covjson","ranges":{}}'
    ).encode()
    cov = msgspec.json.decode(blob, type=Coverage)

    assert cov.context == expected
    # The library's faithfulness invariant is value equality, decode(encode(x)) == x
    # (omit_defaults already precludes byte-identity); the union meets it.
    assert msgspec.json.decode(msgspec.json.encode(cov), type=Coverage) == cov


def test_context_absent_is_unset_and_omitted() -> None:
    cov = msgspec.json.decode(
        b'{"type":"Coverage","domain":"http://ex/d.covjson","ranges":{}}',
        type=Coverage,
    )

    assert cov.context is UNSET
    assert b"@context" not in msgspec.json.encode(cov)


def test_context_explicit_null_is_distinct_from_absent() -> None:
    cov = msgspec.json.decode(
        b'{"type":"Coverage","@context":null,'
        b'"domain":"http://ex/d.covjson","ranges":{}}',
        type=Coverage,
    )

    assert cov.context is None
    assert cov.context is not UNSET
    assert b'"@context":null' in msgspec.json.encode(cov)


def test_context_preserved_in_nested_positions() -> None:
    # @context is spec-defined only at the document root (section 8), but the
    # root-able structs are reused nested, so a member coverage / nested domain /
    # nested range carries the field too. The library preserves it wherever it
    # appears rather than dropping it; there is no root-only enforcement in the
    # type (a positional rule that section 8 does not grade as an error anyway).
    blob = (
        b'{"type":"CoverageCollection","coverages":[{'
        b'"type":"Coverage","@context":"https://member/",'
        b'"domain":{"type":"Domain","@context":{"nested":"domain"},'
        b'"domainType":"Point","axes":{"x":{"values":[1.0]}}},'
        b'"ranges":{"t":{"type":"NdArray","@context":["nested-range"],'
        b'"dataType":"float","values":[1.0]}}}]}'
    )
    coll = msgspec.json.decode(blob, type=CoverageCollection)
    member = coll.coverages[0]

    assert member.context == "https://member/"
    assert isinstance(member.domain, Domain)
    assert member.domain.context == {"nested": "domain"}
    nested_range = member.ranges["t"]
    assert isinstance(nested_range, NdArray)
    assert nested_range.context == ("nested-range",)
    round_tripped = msgspec.json.decode(
        msgspec.json.encode(coll), type=CoverageCollection
    )
    assert round_tripped == coll


def _point_coverage() -> Coverage:
    return Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ranges={"t": NdArray(data_type="float", values=(280.0,))},
    )
