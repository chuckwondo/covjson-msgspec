"""Fast, fully-typed CoverageJSON models built on msgspec."""

from importlib.metadata import PackageNotFoundError, version

from covjson_msgspec.axis import Axis
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
    "Domain",
    "GeographicCRS",
    "I18n",
    "IdentifierRS",
    "ObservedProperty",
    "Parameter",
    "ParameterGroup",
    "ProjectedCRS",
    "ReferenceSystem",
    "ReferenceSystemConnection",
    "Symbol",
    "TemporalRS",
    "Unit",
    "VerticalCRS",
    "__version__",
    "i18n",
]
