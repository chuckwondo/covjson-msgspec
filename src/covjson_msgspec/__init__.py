"""Fast, fully-typed CoverageJSON models built on msgspec."""

from importlib.metadata import PackageNotFoundError, version

from .i18n import I18n, i18n
from .parameter import (
    Category,
    CategoryEncoding,
    ObservedProperty,
    Parameter,
    ParameterGroup,
    Symbol,
    Unit,
)

try:
    __version__ = version("covjson-msgspec")
except PackageNotFoundError:  # pragma: no cover - only during local dev
    __version__ = "0.0.0"

__all__ = [
    "Category",
    "CategoryEncoding",
    "I18n",
    "ObservedProperty",
    "Parameter",
    "ParameterGroup",
    "Symbol",
    "Unit",
    "__version__",
    "i18n",
]
