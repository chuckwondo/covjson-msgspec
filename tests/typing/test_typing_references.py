"""Typing-conformance checks for resolve_references' overloads.

``assert_type`` is a runtime no-op, so this runs (trivially) under pytest too;
its real value is that the type-checker matrix must agree that the overloads
preserve the input type (Coverage -> Coverage, CoverageCollection ->
CoverageCollection) rather than widening to the union. The inputs are
reference-free, so the fetcher is never actually called at runtime.
"""

from typing import assert_type

from covjson_msgspec import (
    Axis,
    Coverage,
    CoverageCollection,
    Domain,
    resolve_references,
)


def test_resolve_references_preserves_input_type() -> None:
    cov = Coverage(
        domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))), ranges={}
    )
    coll = CoverageCollection(coverages=())

    def fetch(url: str) -> bytes:
        return b""

    assert_type(resolve_references(cov, fetch), Coverage)
    assert_type(resolve_references(coll, fetch), CoverageCollection)
    assert_type(cov.resolve_references(fetch), Coverage)
    assert_type(coll.resolve_references(fetch), CoverageCollection)
