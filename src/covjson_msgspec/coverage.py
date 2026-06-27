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

Spec: [Coverage][spec-coverage] and [CoverageCollection][spec-collection] objects.

[spec-coverage]: https://github.com/covjson/specification/blob/master/spec.md#64-coverage-objects
[spec-collection]: https://github.com/covjson/specification/blob/master/spec.md#65-coverage-collection-objects
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal

import msgspec

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.domain import Domain
from covjson_msgspec.parameter import Parameter, ParameterGroup
from covjson_msgspec.range import NdArray, TiledNdArray
from covjson_msgspec.referencing import ReferenceSystemConnection

if TYPE_CHECKING:
    from collections.abc import Mapping

    import geopandas as gpd
    import pandas as pd
    import xarray as xr

    from covjson_msgspec._fetch import AsyncFetch, Fetch

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
    >>> blob = '''
    ... {
    ...   "type": "Coverage",
    ...   "domain": {
    ...     "type": "Domain",
    ...     "domainType": "Point",
    ...     "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
    ...   },
    ...   "ranges": {
    ...     "t": {"type": "NdArray", "dataType": "float", "values": [280.0]}
    ...   }
    ... }
    ... '''
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

    @property
    def effective_domain_type(self) -> str | None:
        """The domain type in effect for this coverage, or ``None`` if unknown.

        CoverageJSON lets ``domainType`` appear in more than one place: on the
        inline `Domain` (its natural home), and on the `Coverage` itself (a
        denormalized copy, used when the domain is an external URL reference, or
        supplied by a `CoverageCollection` that declares the type once for all
        members; see `CoverageCollection.resolved_coverages`). When both are
        present the spec requires them to match, so this prefers the domain's own
        value and falls back to the coverage-level one (which is all that is
        available for a URL-reference domain).

        Returns
        -------
        str or None
            The domain type, e.g. ``"Grid"`` or ``"Trajectory"``.

        Examples
        --------
        An inline domain's own ``domainType`` is used:

        >>> from covjson_msgspec import Axis, Domain
        >>> cov = Coverage(
        ...     domain=Domain.point(x=Axis.listed((1.0,)), y=Axis.listed((2.0,))),
        ...     ranges={},
        ... )
        >>> cov.effective_domain_type
        'Point'

        For a URL-reference domain (which carries no type of its own), the
        coverage-level ``domain_type`` is the fallback:

        >>> ref = Coverage(
        ...     domain="https://example.org/domain.json", ranges={}, domain_type="Grid"
        ... )
        >>> ref.effective_domain_type
        'Grid'
        """
        domain = self.domain
        declared = domain.domain_type if isinstance(domain, Domain) else None

        return declared or self.domain_type

    def to_xarray(self) -> xr.Dataset:
        """Convert this coverage to a CF-aware `xarray.Dataset`.

        Requires the ``xarray`` extra. Thin delegate to
        `covjson_msgspec.xarray.to_xarray`; see it for the full domain/range
        mapping and the conditions it raises on.

        Returns
        -------
        xarray.Dataset
            The coverage as a dataset of parameter variables over the domain's
            coordinate axes.
        """
        from covjson_msgspec.xarray import to_xarray

        return to_xarray(self)

    @classmethod
    def from_xarray(
        cls,
        dataset: xr.Dataset,
        *,
        domain_type: str | None = None,
        x: str | None = None,
        y: str | None = None,
        z: str | None = None,
        t: str | None = None,
        compact_regular: bool = True,
    ) -> Coverage:
        """Build a `Coverage` from an `xarray.Dataset`.

        Requires the ``xarray`` extra. Thin delegate to
        `covjson_msgspec.xarray.from_xarray`; see it for the role detection, the
        override seams, and the documented lossy points.

        Returns
        -------
        Coverage
            A coverage whose domain and ranges mirror the dataset.
        """
        from covjson_msgspec.xarray import from_xarray

        return from_xarray(
            dataset,
            domain_type=domain_type,
            x=x,
            y=y,
            z=z,
            t=t,
            compact_regular=compact_regular,
        )

    def to_pandas(self) -> pd.DataFrame:
        """Convert this coverage to a tidy `pandas.DataFrame`.

        Requires the ``pandas`` extra. Thin delegate to
        `covjson_msgspec.pandas.to_pandas`; see it for the full domain/range
        mapping and the conditions it raises on.

        Returns
        -------
        pandas.DataFrame
            The coverage as a frame of parameter columns over its coordinate
            axes.
        """
        from covjson_msgspec.pandas import to_pandas

        return to_pandas(self)

    def to_geopandas(
        self, *, trajectory_as: Literal["points", "linestring"] = "points"
    ) -> gpd.GeoDataFrame:
        """Convert this coverage to a `geopandas.GeoDataFrame`.

        Requires the ``geo`` extra. Thin delegate to
        `covjson_msgspec.geo.to_geopandas`; see it for the full domain/geometry
        mapping, the ``trajectory_as`` option, and the conditions it raises on.

        Returns
        -------
        geopandas.GeoDataFrame
            The coverage as vector features, one per coverage element.
        """
        from covjson_msgspec.geo import to_geopandas

        return to_geopandas(self, trajectory_as=trajectory_as)

    def to_geojson(
        self, *, trajectory_as: Literal["points", "linestring"] = "points"
    ) -> dict[str, Any]:
        """Convert this coverage to a GeoJSON ``FeatureCollection`` mapping.

        Requires the ``geo`` extra. Thin delegate to
        `covjson_msgspec.geo.to_geojson`; see it for the full domain/geometry
        mapping, the ``trajectory_as`` option, and the conditions it raises on.

        Returns
        -------
        dict
            A GeoJSON ``FeatureCollection`` as a plain mapping.
        """
        from covjson_msgspec.geo import to_geojson

        return to_geojson(self, trajectory_as=trajectory_as)

    def resolve_references(self, fetch: Fetch) -> Coverage:
        """Inline this coverage's URL-string domain and range references.

        Thin delegate to `covjson_msgspec.references.resolve_references`; see it
        for the resolution rules and what it does (and does not) follow.

        Parameters
        ----------
        fetch
            A callable mapping a referenced document's URL to its raw bytes.

        Returns
        -------
        Coverage
            A new coverage with its URL references inlined (this instance
            unchanged when it has none).
        """
        from covjson_msgspec.references import resolve_references

        return resolve_references(self, fetch)

    async def resolve_references_async(self, fetch: AsyncFetch) -> Coverage:
        """Concurrently inline this coverage's URL-string references.

        Thin delegate to `covjson_msgspec.references.resolve_references_async`; the
        awaitable counterpart of `resolve_references`, fetching the references
        concurrently.

        Parameters
        ----------
        fetch
            An awaitable callable mapping a referenced document's URL to its raw
            bytes.

        Returns
        -------
        Coverage
            A new coverage with its URL references inlined (this instance
            unchanged when it has none).
        """
        from covjson_msgspec.references import resolve_references_async

        return await resolve_references_async(self, fetch)

    def isel(
        self,
        indexers: Mapping[str, int | slice] | None = None,
        /,
        **indexers_kwargs: int | slice,
    ) -> Coverage:
        """Subset this coverage by integer position along named axes.

        Thin delegate to `covjson_msgspec.subset.isel`; see it for the selection
        rules (integer drops the axis, slice keeps it) and what is supported.

        Parameters
        ----------
        indexers
            A mapping of axis name to an integer position or a `slice`.
        **indexers_kwargs
            Indexers given as keywords, e.g. ``cov.isel(x=0, t=slice(0, 3))``.

        Returns
        -------
        Coverage
            A new coverage narrowed to the selection.
        """
        from covjson_msgspec.subset import isel

        return isel(self, indexers, **indexers_kwargs)

    def sel(
        self,
        indexers: Mapping[str, float | int | str | slice] | None = None,
        /,
        *,
        method: Literal["nearest"] | None = None,
        **indexers_kwargs: float | int | str | slice,
    ) -> Coverage:
        """Subset this coverage by coordinate label along named axes.

        Thin delegate to `covjson_msgspec.subset.sel`; see it for the matching
        rules (exact or ``method="nearest"``; inclusive label slices) and what is
        supported.

        Parameters
        ----------
        indexers
            A mapping of axis name to a coordinate label or a `slice` of labels.
        method
            ``None`` for an exact match, or ``"nearest"`` for the closest
            coordinate (numeric axes only).
        **indexers_kwargs
            Indexers given as keywords, e.g. ``cov.sel(x=10.0, method="nearest")``.

        Returns
        -------
        Coverage
            A new coverage narrowed to the selection.
        """
        from covjson_msgspec.subset import sel

        return sel(self, indexers, method=method, **indexers_kwargs)

    def _repr_html_(self) -> str:
        """Render an HTML summary of this coverage for Jupyter.

        Thin delegate to `covjson_msgspec._repr.coverage_html`.
        """
        from covjson_msgspec._repr import coverage_html

        return coverage_html(self)


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
        if (
            isinstance(domain := coverage.domain, Domain)
            and not domain.referencing
            and self.referencing
        ):
            changes["domain"] = msgspec.structs.replace(
                domain, referencing=self.referencing
            )

        return msgspec.structs.replace(coverage, **changes) if changes else coverage

    def to_datatree(self) -> xr.DataTree:
        """Convert this collection to an `xarray.DataTree`.

        Requires the ``xarray`` extra. Thin delegate to
        `covjson_msgspec.xarray.to_datatree`; see it for the per-member mapping
        and the conditions it raises on.

        Returns
        -------
        xarray.DataTree
            A tree with one child node per member coverage.
        """
        from covjson_msgspec.xarray import to_datatree

        return to_datatree(self)

    @classmethod
    def from_datatree(
        cls,
        tree: xr.DataTree,
        *,
        domain_type: str | None = None,
        x: str | None = None,
        y: str | None = None,
        z: str | None = None,
        t: str | None = None,
        compact_regular: bool = True,
    ) -> CoverageCollection:
        """Build a `CoverageCollection` from an `xarray.DataTree`.

        Requires the ``xarray`` extra. Thin delegate to
        `covjson_msgspec.xarray.from_datatree`; see it for the per-node
        conversion and the override seams.

        Returns
        -------
        CoverageCollection
            A collection whose members mirror the tree's data-bearing nodes.
        """
        from covjson_msgspec.xarray import from_datatree

        return from_datatree(
            tree,
            domain_type=domain_type,
            x=x,
            y=y,
            z=z,
            t=t,
            compact_regular=compact_regular,
        )

    def to_pandas(self) -> pd.DataFrame:
        """Convert this collection to a single tidy `pandas.DataFrame`.

        Requires the ``pandas`` extra. Thin delegate to
        `covjson_msgspec.pandas.to_pandas`; the resolved members are concatenated
        under a leading ``coverage`` index level. See it for the per-member
        domain/range mapping and the conditions it raises on.

        Returns
        -------
        pandas.DataFrame
            The member coverages stacked into one frame, keyed by coverage.
        """
        from covjson_msgspec.pandas import to_pandas

        return to_pandas(self)

    def to_geopandas(
        self, *, trajectory_as: Literal["points", "linestring"] = "points"
    ) -> gpd.GeoDataFrame:
        """Convert this collection to a single `geopandas.GeoDataFrame`.

        Requires the ``geo`` extra. Thin delegate to
        `covjson_msgspec.geo.to_geopandas`; the resolved members are concatenated
        with a leading ``coverage`` column identifying each. See it for the
        per-member domain/geometry mapping, the ``trajectory_as`` option, and the
        conditions it raises on.

        Returns
        -------
        geopandas.GeoDataFrame
            The member coverages stacked into one frame of vector features.
        """
        from covjson_msgspec.geo import to_geopandas

        return to_geopandas(self, trajectory_as=trajectory_as)

    def to_geojson(
        self, *, trajectory_as: Literal["points", "linestring"] = "points"
    ) -> dict[str, Any]:
        """Convert this collection to a GeoJSON ``FeatureCollection`` mapping.

        Requires the ``geo`` extra. Thin delegate to
        `covjson_msgspec.geo.to_geojson`; every member's features carry a
        ``coverage`` property identifying their source. See it for the per-member
        domain/geometry mapping, the ``trajectory_as`` option, and the conditions
        it raises on.

        Returns
        -------
        dict
            A GeoJSON ``FeatureCollection`` as a plain mapping.
        """
        from covjson_msgspec.geo import to_geojson

        return to_geojson(self, trajectory_as=trajectory_as)

    def resolve_references(self, fetch: Fetch) -> CoverageCollection:
        """Inline every member coverage's URL-string references.

        Thin delegate to `covjson_msgspec.references.resolve_references`; see it
        for the resolution rules. Collection-level inheritance is not applied;
        call `resolved_coverages` first if you need that.

        Parameters
        ----------
        fetch
            A callable mapping a referenced document's URL to its raw bytes.

        Returns
        -------
        CoverageCollection
            A new collection whose members have their URL references inlined.
        """
        from covjson_msgspec.references import resolve_references

        return resolve_references(self, fetch)

    async def resolve_references_async(self, fetch: AsyncFetch) -> CoverageCollection:
        """Concurrently inline every member coverage's URL-string references.

        Thin delegate to `covjson_msgspec.references.resolve_references_async`; the
        awaitable counterpart of `resolve_references`, fetching every member's
        references concurrently.

        Parameters
        ----------
        fetch
            An awaitable callable mapping a referenced document's URL to its raw
            bytes.

        Returns
        -------
        CoverageCollection
            A new collection whose members have their URL references inlined.
        """
        from covjson_msgspec.references import resolve_references_async

        return await resolve_references_async(self, fetch)

    def _repr_html_(self) -> str:
        """Render an HTML summary of this collection for Jupyter.

        Thin delegate to `covjson_msgspec._repr.collection_html`.
        """
        from covjson_msgspec._repr import collection_html

        return collection_html(self)


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
    >>> doc = decode('''
    ... {
    ...   "type": "Coverage",
    ...   "domain": {
    ...     "type": "Domain",
    ...     "domainType": "Point",
    ...     "axes": {"x": {"values": [1.0]}, "y": {"values": [2.0]}}
    ...   },
    ...   "ranges": {}
    ... }
    ... ''')
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
