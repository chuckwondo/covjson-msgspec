"""Fast, fully-typed CoverageJSON models built on msgspec.

These types model the CoverageJSON format, published as OGC Community Standard
21-069r2. The canonical, navigable specification (with the section anchors linked
from each submodule) lives at
https://github.com/covjson/specification/blob/master/spec.md, and the well-known
domain types at
https://github.com/covjson/specification/blob/master/domain-types.md.
"""

from importlib.metadata import PackageNotFoundError, version

from covjson_msgspec._best_effort import (
    FailureKind,
    FailureStrategy,
    FetchError,
    FetchFailure,
    Verdict,
    collect_all,
    fail_fast,
    halt_on_unrecoverable,
    stop_after,
)
from covjson_msgspec._fetch import AsyncFetch, Fetch, ReferencedDocumentError
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
from covjson_msgspec.media_type import (
    MEDIA_TYPE,
    decode_response,
    encode_response,
    is_coverage_json_media_type,
    media_type,
)
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
from covjson_msgspec.range import (
    AssembleResult,
    NdArray,
    TiledNdArray,
    TileFailure,
    TileSet,
)
from covjson_msgspec.references import (
    ReferenceFailure,
    ResolveResult,
    resolve_references,
    resolve_references_async,
)
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
    "MEDIA_TYPE",
    "AssembleResult",
    "AsyncFetch",
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
    "FailureKind",
    "FailureStrategy",
    "Fetch",
    "FetchError",
    "FetchFailure",
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
    "ReferenceFailure",
    "ReferenceSystem",
    "ReferenceSystemConnection",
    "ReferencedDocumentError",
    "ResolveResult",
    "Severity",
    "Symbol",
    "TemporalRS",
    "TileFailure",
    "TileSet",
    "TiledNdArray",
    "Unit",
    "Verdict",
    "VerticalCRS",
    "__version__",
    "collect_all",
    "decode",
    "decode_coverage",
    "decode_coverage_collection",
    "decode_response",
    "encode",
    "encode_response",
    "fail_fast",
    "from_datatree",
    "from_xarray",
    "halt_on_unrecoverable",
    "i18n",
    "is_coverage_json_media_type",
    "isel",
    "media_type",
    "resolve_references",
    "resolve_references_async",
    "sel",
    "stop_after",
    "to_datatree",
    "to_geojson",
    "to_geopandas",
    "to_pandas",
    "to_xarray",
    "validate",
]
