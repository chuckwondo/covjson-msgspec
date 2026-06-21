"""Fast, fully-typed CoverageJSON models built on msgspec."""

from importlib.metadata import PackageNotFoundError, version

from covjson_msgspec.axis import Axis
from covjson_msgspec.coverage import (
    Coverage,
    CoverageCollection,
    CoverageJSON,
    Range,
    decode,
    decode_coverage,
    decode_coverage_collection,
    encode,
)
from covjson_msgspec.domain import Domain
from covjson_msgspec.i18n import I18n, i18n
from covjson_msgspec.parameter import (
    Category,
    CategoryEncoding,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Symbol,
    Unit,
)
from covjson_msgspec.ranges import NdArray, TiledNdArray, TileSet
from covjson_msgspec.referencing import (
    Concept,
    GeographicCRS,
    IdentifierRS,
    ProjectedCRS,
    ReferenceSystem,
    ReferenceSystemConnection,
    TemporalRS,
    VerticalCRS,
)

try:
    __version__ = version("covjson-msgspec")
except PackageNotFoundError:  # pragma: no cover - only during local dev
    __version__ = "0.0.0"

__all__ = [
    "Axis",
    "Category",
    "CategoryEncoding",
    "Concept",
    "Coverage",
    "CoverageCollection",
    "CoverageJSON",
    "Domain",
    "GeographicCRS",
    "I18n",
    "IdentifierRS",
    "NdArray",
    "ObservedProperty",
    "Parameter",
    "ParameterGroup",
    "ProjectedCRS",
    "Range",
    "ReferenceSystem",
    "ReferenceSystemConnection",
    "Symbol",
    "TemporalRS",
    "TileSet",
    "TiledNdArray",
    "Unit",
    "VerticalCRS",
    "__version__",
    "decode",
    "decode_coverage",
    "decode_coverage_collection",
    "encode",
    "i18n",
]
