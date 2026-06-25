"""Fast, fully-typed CoverageJSON models built on msgspec.

These types model the CoverageJSON format, published as OGC Community Standard
21-069r2. The canonical, navigable specification (with the section anchors linked
from each submodule) lives at
https://github.com/covjson/specification/blob/master/spec.md, and the well-known
domain types at
https://github.com/covjson/specification/blob/master/domain-types.md.
"""

from importlib.metadata import PackageNotFoundError, version

from covjson_msgspec._fetch import Fetch
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
from covjson_msgspec.geo import to_geojson, to_geopandas
from covjson_msgspec.i18n import I18n, i18n
from covjson_msgspec.pandas import to_pandas
from covjson_msgspec.parameter import (
    Category,
    CategoryEncoding,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Symbol,
    Unit,
)
from covjson_msgspec.range import NdArray, TiledNdArray, TileSet
from covjson_msgspec.references import resolve_references
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
from covjson_msgspec.subset import isel, sel
from covjson_msgspec.validation import (
    DOMAIN_TYPE_RULES,
    CovJSONValidationError,
    DomainType,
    DomainTypeRule,
    Issue,
    Severity,
    validate,
)
from covjson_msgspec.xarray import (
    from_datatree,
    from_xarray,
    to_datatree,
    to_xarray,
)

try:
    __version__ = version("covjson-msgspec")
except PackageNotFoundError:  # pragma: no cover - only during local dev
    __version__ = "0.0.0"

__all__ = [
    "DOMAIN_TYPE_RULES",
    "Axis",
    "Category",
    "CategoryEncoding",
    "Concept",
    "CovJSONValidationError",
    "Coverage",
    "CoverageCollection",
    "CoverageJSON",
    "Domain",
    "DomainType",
    "DomainTypeRule",
    "Fetch",
    "GeographicCRS",
    "I18n",
    "IdentifierRS",
    "Issue",
    "NdArray",
    "ObservedProperty",
    "Parameter",
    "ParameterGroup",
    "ProjectedCRS",
    "Range",
    "ReferenceSystem",
    "ReferenceSystemConnection",
    "Severity",
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
    "from_datatree",
    "from_xarray",
    "i18n",
    "isel",
    "resolve_references",
    "sel",
    "to_datatree",
    "to_geojson",
    "to_geopandas",
    "to_pandas",
    "to_xarray",
    "validate",
]
