"""Tests for the OpenAPI JSON Schema generator (`covjson_msgspec.schema`)."""

from __future__ import annotations

import json
import re

import pytest

from covjson_msgspec import (
    Coverage,
    CoverageCollection,
    CoverageJSON,
    Domain,
    NdArray,
    TiledNdArray,
    component_schemas,
    schema_ref,
)


def test_component_schemas_exposes_the_expected_namespaced_surface() -> None:
    # The exposed component names are a public surface (host apps `$ref` them), so
    # pin the whole set: the 5 root types plus every sub-type they pull in. A change
    # here is a public-OpenAPI-surface change and should fail loudly.
    expected = {
        f"CoverageJSON.{name}"
        for name in (
            "Coverage",
            "CoverageCollection",
            "Domain",
            "NdArray",
            "TiledNdArray",
            "Axis",
            "Category",
            "Concept",
            "ObservedProperty",
            "Parameter",
            "ParameterGroup",
            "ReferenceSystem",
            "ReferenceSystemConnection",
            "Symbol",
            "TileSet",
            "Unit",
        )
    }

    assert set(component_schemas()) == expected


def test_property_names_are_camelcase_wire_names() -> None:
    coverage = component_schemas()["CoverageJSON.Coverage"]

    # The lowerCamelCase wire names, not the snake_case attributes.
    assert "domainType" in coverage["properties"]
    assert "parameterGroups" in coverage["properties"]


def test_every_internal_ref_resolves_to_a_present_component() -> None:
    schemas = component_schemas()

    refs = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(schemas)))

    assert refs <= set(schemas)


@pytest.mark.parametrize(
    ("root_type", "name"),
    [
        (Coverage, "Coverage"),
        (CoverageCollection, "CoverageCollection"),
        (Domain, "Domain"),
        (NdArray, "NdArray"),
        (TiledNdArray, "TiledNdArray"),
    ],
)
def test_schema_ref_points_at_the_namespaced_component(
    root_type: type[CoverageJSON], name: str
) -> None:
    ref = schema_ref(root_type)

    assert ref == {"$ref": f"#/components/schemas/CoverageJSON.{name}"}
    # The ref resolves to a component the generator actually registers.
    assert ref["$ref"].rsplit("/", 1)[-1] in component_schemas()
