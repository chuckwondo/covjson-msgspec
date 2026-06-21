"""Coverages and coverage collections: the top-level CoverageJSON documents.

A `Coverage` pairs a `Domain` with the range values for each parameter. A
`CoverageCollection` holds many coverages that share a common structure, with
``parameters`` / ``domain_type`` / ``parameter_groups`` / ``referencing``
declared once on the collection and *inherited* by each member.

The top-level `decode` / `encode` helpers read and write any CoverageJSON
document, and `decode_coverage` / `decode_coverage_collection` decode a known
document type. Inheritance is applied on demand by
`CoverageCollection.resolved_coverages`, never silently on decode, so a decoded
document round-trips byte-for-byte.
"""

from typing import Final

import msgspec

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.domain import Domain
from covjson_msgspec.parameter import Parameter, ParameterGroup
from covjson_msgspec.range import NdArray, TiledNdArray
from covjson_msgspec.referencing import ReferenceSystemConnection

# A range is inline values (`NdArray` / `TiledNdArray`) or a bare string URL
# referencing the values in a separate document.
Range = NdArray | TiledNdArray | str


class Coverage(CovJSONStruct, frozen=True, tag="Coverage"):
    """A single coverage: a domain plus range values for each parameter.

    ``ranges`` maps each parameter key to its values (an `NdArray`, a
    `TiledNdArray`, or a URL string). A standalone coverage carries its own
    ``parameters``; a coverage inside a `CoverageCollection` may instead inherit
    them (see `CoverageCollection.resolved_coverages`).

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> cov.ranges["t"].values
    (280.0,)

    A coverage round-trips through CoverageJSON (camelCase wire names map to
    snake_case attributes):

    >>> import msgspec
    >>> blob = (
    ...     b'{"type": "Coverage",'
    ...     b' "domain": {"type": "Domain", "domainType": "Point",'
    ...     b' "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}},'
    ...     b' "ranges": {"t": {"type": "NdArray", "dataType": "float",'
    ...     b' "values": [280.0]}}}'
    ... )
    >>> back = msgspec.json.decode(blob, type=Coverage)
    >>> back.domain.domain_type
    'Point'
    >>> back.ranges["t"].values
    (280.0,)
    """

    domain: Domain | str
    ranges: dict[str, Range]
    id: str | None = None
    domain_type: str | None = None
    parameters: dict[str, Parameter] | None = None
    parameter_groups: tuple[ParameterGroup, ...] | None = None


class CoverageCollection(CovJSONStruct, frozen=True, tag="CoverageCollection"):
    """A collection of coverages sharing a common structure.

    Any of ``parameters`` / ``domain_type`` / ``parameter_groups`` /
    ``referencing`` set on the collection are *inherited* by member coverages
    that do not set their own. Call `resolved_coverages` to obtain the member
    coverages with that inheritance applied.

    Examples
    --------
    >>> from covjson_msgspec import (
    ...     Axis, Domain, NdArray, ObservedProperty, Parameter, Unit, i18n
    ... )
    >>> temp = Parameter.continuous(
    ...     ObservedProperty(label=i18n("Air temperature")), Unit(symbol="K")
    ... )
    >>> member = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> collection = CoverageCollection(
    ...     coverages=(member,),
    ...     domain_type="Point",
    ...     parameters={"t": temp},
    ... )

    The member inherits the collection's ``parameters`` and ``domain_type``:

    >>> member.parameters is None
    True
    >>> resolved = collection.resolved_coverages()
    >>> resolved[0].parameters["t"].unit.symbol
    'K'
    >>> resolved[0].domain_type
    'Point'
    """

    coverages: tuple[Coverage, ...]
    domain_type: str | None = None
    parameters: dict[str, Parameter] | None = None
    parameter_groups: tuple[ParameterGroup, ...] | None = None
    referencing: tuple[ReferenceSystemConnection, ...] = ()

    def resolved_coverages(self) -> tuple[Coverage, ...]:
        """Return the member coverages with collection-level fields inherited.

        For each member, any of ``domain_type`` / ``parameters`` /
        ``parameter_groups`` left unset takes the collection's value, and the
        collection's ``referencing`` is injected into a member's inline `Domain`
        when that domain declares none of its own. Members that already set a
        field keep their own value.

        Returns
        -------
        tuple of Coverage
            The member coverages with inheritance applied.
        """
        return tuple(self._resolve(coverage) for coverage in self.coverages)

    def _resolve(self, coverage: Coverage) -> Coverage:
        changes: dict[str, object] = {}

        if coverage.domain_type is None and self.domain_type is not None:
            changes["domain_type"] = self.domain_type

        if coverage.parameters is None and self.parameters is not None:
            changes["parameters"] = self.parameters

        if coverage.parameter_groups is None and self.parameter_groups is not None:
            changes["parameter_groups"] = self.parameter_groups

        # Push shared referencing down into an inline domain that has none.
        domain = coverage.domain

        if isinstance(domain, Domain) and not domain.referencing and self.referencing:
            changes["domain"] = msgspec.structs.replace(
                domain, referencing=self.referencing
            )

        return coverage if not changes else msgspec.structs.replace(coverage, **changes)


# The root of any CoverageJSON document. Domain and the range types are valid
# standalone documents too (e.g. a domain referenced by a coverage's URL range).
CoverageJSON = Coverage | CoverageCollection | Domain | NdArray | TiledNdArray


# Decoders and the encoder are built once and reused: constructing them is the
# costly step, and they are safe to share.
_decoder: Final[msgspec.json.Decoder[CoverageJSON]] = msgspec.json.Decoder(CoverageJSON)
_coverage_decoder: Final[msgspec.json.Decoder[Coverage]] = msgspec.json.Decoder(
    Coverage
)
_collection_decoder: Final[msgspec.json.Decoder[CoverageCollection]] = (
    msgspec.json.Decoder(CoverageCollection)
)
_encoder: Final = msgspec.json.Encoder()


def decode(data: bytes | str) -> CoverageJSON:
    """Decode a CoverageJSON document of any type.

    Parameters
    ----------
    data
        The CoverageJSON document, as ``bytes`` or ``str``.

    Returns
    -------
    Coverage or CoverageCollection or Domain or NdArray or TiledNdArray
        The decoded document, dispatched on its ``type`` member.

    Examples
    --------
    >>> doc = decode(
    ...     b'{"type": "Coverage",'
    ...     b' "domain": {"type": "Domain", "domainType": "Point",'
    ...     b' "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}},'
    ...     b' "ranges": {}}'
    ... )
    >>> type(doc).__name__
    'Coverage'
    """
    return _decoder.decode(data)


def decode_coverage(data: bytes | str) -> Coverage:
    """Decode a CoverageJSON document known to be a `Coverage`.

    Parameters
    ----------
    data
        The CoverageJSON document, as ``bytes`` or ``str``.

    Returns
    -------
    Coverage
        The decoded coverage.
    """
    return _coverage_decoder.decode(data)


def decode_coverage_collection(data: bytes | str) -> CoverageCollection:
    """Decode a CoverageJSON document known to be a `CoverageCollection`.

    Parameters
    ----------
    data
        The CoverageJSON document, as ``bytes`` or ``str``.

    Returns
    -------
    CoverageCollection
        The decoded coverage collection.
    """
    return _collection_decoder.decode(data)


def encode(obj: CoverageJSON) -> bytes:
    """Encode a CoverageJSON document to JSON bytes.

    Parameters
    ----------
    obj
        Any CoverageJSON document.

    Returns
    -------
    bytes
        The JSON encoding, with unset optional fields omitted.

    Examples
    --------
    >>> from covjson_msgspec import Axis, Domain, NdArray
    >>> cov = Coverage(
    ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
    ...     ranges={"t": NdArray(data_type="float", values=(280.0,))},
    ... )
    >>> encode(cov).startswith(b'{"type":"Coverage"')
    True
    """
    return _encoder.encode(obj)
