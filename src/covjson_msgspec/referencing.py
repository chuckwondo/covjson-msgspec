"""Reference systems and how they connect to a domain's coordinates.

A reference system's ``type`` names one of five modelled kinds (three geospatial
CRSs, a temporal RS, an identifier RS) or a custom value (a compact- or
absolute-URI, CoverageJSON section 7.2). A closed tagged union cannot hold that
open set (a custom ``type`` would fail to decode), so the model splits the two
concerns:

- `ReferenceSystem` is the permissive, faithful *stored* form: a single struct
  with an open ``type_`` and the union of every kind's members as optionals. It
  decodes any reference system in one pass, so a document carrying a custom type
  still loads (custom *members* drop, per ADR-0012).
- `~ReferenceSystem.refine` is the opt-in *typed* projection: it returns a clean
  per-kind variant (`GeographicCRS`, `TemporalRS`, ... or `OpaqueRS` for a custom
  or malformed one) so callers ``match`` on a precise shape rather than a
  grab-bag of optionals.

A `ReferenceSystemConnection` ties a set of coordinate identifiers to the system
that references them. Construction goes through the `ReferenceSystem` builders
(`~ReferenceSystem.temporal`, ...), which pair each ``type`` with its members.

Spec: [Reference system objects](https://github.com/covjson/specification/blob/master/spec.md#5-reference-system-objects),
[Custom types](https://github.com/covjson/specification/blob/master/spec.md#72-custom-types).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import msgspec

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec._reference_invariants import KNOWN, missing_required_member
from covjson_msgspec.i18n import I18n


class Concept(CovJSONStruct, frozen=True):
    """A referenced concept: a label, an optional identifier and description.

    Used for `IdentifierRS.target_concept` and for the values of
    `IdentifierRS.identifiers`. The standard's section 5.3 example gives each
    concept an ``id`` (a concept URI), so it is modelled as an optional member.

    Examples
    --------
    >>> concept = Concept(
    ...     id="http://dbpedia.org/resource/Germany", label={"en": "Germany"}
    ... )
    >>> concept.id
    'http://dbpedia.org/resource/Germany'
    >>> concept.label
    {'en': 'Germany'}
    """

    label: I18n
    id: str | None = None
    description: I18n | None = None


class GeographicCRS(CovJSONStruct, frozen=True, tag="GeographicCRS"):
    """A geographic coordinate reference system (e.g. lon/lat).

    A typed projection produced by `ReferenceSystem.refine`. The standard
    (section 5.1.1) gives a geographic CRS an optional ``id`` and no other member.

    Examples
    --------
    >>> import msgspec
    >>> crs = GeographicCRS(id="http://www.opengis.net/def/crs/OGC/1.3/CRS84")
    >>> msgspec.json.encode(crs)  # type tag first; unset fields are omitted
    b'{"type":"GeographicCRS","id":"http://www.opengis.net/def/crs/OGC/1.3/CRS84"}'
    """

    id: str | None = None


class ProjectedCRS(CovJSONStruct, frozen=True, tag="ProjectedCRS"):
    """A projected coordinate reference system (e.g. a map projection).

    A typed projection produced by `ReferenceSystem.refine`. Carries an optional
    ``id`` (section 5.1.2).

    Examples
    --------
    >>> import msgspec
    >>> crs = ProjectedCRS(id="http://www.opengis.net/def/crs/EPSG/0/27700")
    >>> msgspec.json.encode(crs)
    b'{"type":"ProjectedCRS","id":"http://www.opengis.net/def/crs/EPSG/0/27700"}'
    """

    id: str | None = None


class VerticalCRS(CovJSONStruct, frozen=True, tag="VerticalCRS"):
    """A vertical coordinate reference system (e.g. height or depth).

    A typed projection produced by `ReferenceSystem.refine`. Carries an optional
    ``id`` (section 5.1.3). Full inline CRS definitions (section 5.1.4, a
    ``datum``/``cs`` structure) are left undefined by the standard and are not
    modelled; such members drop on decode, per ADR-0012.

    Examples
    --------
    >>> import msgspec
    >>> crs = msgspec.json.decode(
    ...     b'{"type": "VerticalCRS", "id": "http://example.org/crs/depth"}',
    ...     type=VerticalCRS,
    ... )
    >>> crs.id
    'http://example.org/crs/depth'
    """

    id: str | None = None


class TemporalRS(CovJSONStruct, frozen=True, tag="TemporalRS"):
    """A temporal reference system.

    A typed projection produced by `ReferenceSystem.refine`. ``calendar`` is
    required by the standard (``"Gregorian"`` or a URI) and is guaranteed present
    on a refined ``TemporalRS`` (a core missing it refines to `OpaqueRS`). The
    model carries it verbatim: the calendar string is neither validated nor
    interpreted, and the time values it governs stay opaque ISO 8601 strings on
    their axis (see `covjson_msgspec.axis.AxisValue`). A non-Gregorian calendar
    therefore round-trips untouched; interpretation is opt-in, via
    `covjson_msgspec.temporal.to_datetime` (stdlib) or the export bridges.

    Examples
    --------
    >>> import msgspec
    >>> msgspec.json.encode(TemporalRS(calendar="Gregorian"))
    b'{"type":"TemporalRS","calendar":"Gregorian"}'
    """

    # No default by design: with the base's omit_defaults a default would drop
    # this required member on encode.
    calendar: str
    # Wire name ``timeScale`` (camel rule); optional, defaults to UTC when absent.
    time_scale: str | None = None


class IdentifierRS(CovJSONStruct, frozen=True, tag="IdentifierRS"):
    """An identifier-based reference system (categorical / coded values).

    A typed projection produced by `ReferenceSystem.refine`. ``target_concept``
    (wire ``targetConcept``) is required and guaranteed present on a refined
    ``IdentifierRS`` (a core missing it refines to `OpaqueRS`); ``identifiers``
    maps each identifier string used in the range to the `Concept` it denotes.

    Examples
    --------
    >>> import msgspec
    >>> blob = '''
    ... {
    ...   "type": "IdentifierRS",
    ...   "targetConcept": {"label": {"en": "Land cover"}},
    ...   "identifiers": {
    ...     "1": {"label": {"en": "Water"}},
    ...     "2": {"label": {"en": "Forest"}}
    ...   }
    ... }
    ... '''
    >>> rs = msgspec.json.decode(blob, type=IdentifierRS)
    >>> rs.target_concept.label
    {'en': 'Land cover'}
    >>> rs.identifiers["1"].label
    {'en': 'Water'}
    """

    target_concept: Concept
    id: str | None = None
    label: I18n | None = None
    description: I18n | None = None
    identifiers: Mapping[str, Concept] | None = None


class OpaqueRS(CovJSONStruct, frozen=True):
    """A reference system that could not be given a precise type.

    The catch-all `ReferenceSystem.refine` returns for a custom (section 7.2)
    ``type`` it does not model *and* for a modelled type too malformed to project
    (a ``TemporalRS`` with no ``calendar``, an ``IdentifierRS`` with no
    ``targetConcept``). `is_custom` tells the two apart, and the ``type_`` is
    preserved either way. The standard defines no members for a custom type
    beyond ``type``, so none are carried here; any incidentally-present member is
    still readable on the `ReferenceSystem` core it was refined from.

    Examples
    --------
    >>> OpaqueRS(type_="uor:HEALPixRS").is_custom()  # unmodelled custom type
    True
    >>> OpaqueRS(type_="TemporalRS").is_custom()  # a malformed known type
    False
    """

    type_: str = msgspec.field(name="type")

    def is_custom(self) -> bool:
        """Return whether the ``type_`` is a custom value rather than a known kind.

        ``True`` for a section 7.2 custom type this library does not model;
        ``False`` when the ``type_`` names a known kind (so the system is a
        malformed instance of it, which `covjson_msgspec.validation.validate`
        reports).

        Examples
        --------
        >>> OpaqueRS(type_="uor:HEALPixRS").is_custom()
        True
        >>> OpaqueRS(type_="TemporalRS").is_custom()
        False
        """
        return self.type_ not in KNOWN


ResolvedReferenceSystem = (
    GeographicCRS | ProjectedCRS | VerticalCRS | TemporalRS | IdentifierRS | OpaqueRS
)
"""The return type of `ReferenceSystem.refine`: a closed union of clean per-kind
variants that callers ``match`` on (ideally with a ``case _: assert_never(rs)``
arm, so a future variant forces every reader to update).

This is a projection *output*, not a decode target: decode a reference system into
`ReferenceSystem` (or a whole `~covjson_msgspec.coverage.Coverage`) and call
`~ReferenceSystem.refine`. Decoding into ``ResolvedReferenceSystem`` itself raises,
because the union mixes tagged variants with the untagged `OpaqueRS`.
"""


class ReferenceSystem(CovJSONStruct, frozen=True):
    """The permissive, faithful stored form of a reference system.

    One struct with an open ``type_`` and the union of every modelled kind's
    members as optionals. It decodes any reference system in a single pass, so a
    document carrying a custom ``type`` (section 7.2) still loads. Read it with
    `refine`, which projects it to a precise `ResolvedReferenceSystem` variant.
    Build a known kind with the builders (`geographic`, `temporal`, ...); a custom
    type has no builder (it pairs no required members), so construct one directly
    as ``ReferenceSystem(type_="uor:HEALPixRS")``.

    One narrow decode limitation: because the declared members are typed, any
    reference system whose member reuses a known member name with an incompatible
    JSON type (e.g. ``{"type": "uor:X", "calendar": 123}``) fails to decode. Custom
    member names SHOULD be compact URIs (section 7.1), which never collide, so this
    is rare; it is the price of a typed core over an opaque one.

    Examples
    --------
    >>> import msgspec
    >>> blob = b'{"type": "uor:HEALPixRS", "id": "h"}'  # a custom type still loads
    >>> rs = msgspec.json.decode(blob, type=ReferenceSystem)
    >>> rs.type_
    'uor:HEALPixRS'
    >>> rs.refine()  # projected to the opaque variant
    OpaqueRS(type_='uor:HEALPixRS')
    """

    type_: str = msgspec.field(name="type")
    id: str | None = None
    description: I18n | None = None
    calendar: str | None = None
    time_scale: str | None = None
    target_concept: Concept | None = None
    label: I18n | None = None
    identifiers: Mapping[str, Concept] | None = None

    @classmethod
    def geographic(cls, *, id: str | None = None) -> ReferenceSystem:
        """Build a geographic CRS core (``type`` ``"GeographicCRS"``).

        Examples
        --------
        >>> ReferenceSystem.geographic(id="urn:crs").refine()
        GeographicCRS(id='urn:crs')
        """
        return cls(type_="GeographicCRS", id=id)

    @classmethod
    def projected(cls, *, id: str | None = None) -> ReferenceSystem:
        """Build a projected CRS core (``type`` ``"ProjectedCRS"``).

        Examples
        --------
        >>> ReferenceSystem.projected(id="urn:crs").refine()
        ProjectedCRS(id='urn:crs')
        """
        return cls(type_="ProjectedCRS", id=id)

    @classmethod
    def vertical(cls, *, id: str | None = None) -> ReferenceSystem:
        """Build a vertical CRS core (``type`` ``"VerticalCRS"``).

        Examples
        --------
        >>> ReferenceSystem.vertical(id="urn:crs").refine()
        VerticalCRS(id='urn:crs')
        """
        return cls(type_="VerticalCRS", id=id)

    @classmethod
    def temporal(
        cls, *, calendar: str, time_scale: str | None = None
    ) -> ReferenceSystem:
        """Build a temporal RS core (``type`` ``"TemporalRS"``).

        Examples
        --------
        >>> ReferenceSystem.temporal(calendar="Gregorian").refine()
        TemporalRS(calendar='Gregorian', time_scale=None)
        """
        return cls(type_="TemporalRS", calendar=calendar, time_scale=time_scale)

    @classmethod
    def identifier(
        cls,
        *,
        target_concept: Concept,
        id: str | None = None,
        label: I18n | None = None,
        description: I18n | None = None,
        identifiers: Mapping[str, Concept] | None = None,
    ) -> ReferenceSystem:
        """Build an identifier RS core (``type`` ``"IdentifierRS"``).

        Examples
        --------
        >>> rs = ReferenceSystem.identifier(
        ...     target_concept=Concept(label={"en": "Land cover"})
        ... )
        >>> rs.refine().target_concept.label
        {'en': 'Land cover'}
        """
        return cls(
            type_="IdentifierRS",
            target_concept=target_concept,
            id=id,
            label=label,
            description=description,
            identifiers=identifiers,
        )

    def refine(self) -> ResolvedReferenceSystem:
        """Project this core to its precise `ResolvedReferenceSystem` variant.

        A well-formed known kind projects to its own variant; a custom type or a
        known kind missing its required member projects to `OpaqueRS`. A returned
        `TemporalRS` therefore always has a ``calendar`` and a returned
        `IdentifierRS` always a ``target_concept``.

        Returns
        -------
        ResolvedReferenceSystem
            The precise variant for this system's ``type_``.

        Examples
        --------
        >>> ReferenceSystem.temporal(calendar="Gregorian").refine()
        TemporalRS(calendar='Gregorian', time_scale=None)
        >>> ReferenceSystem(type_="TemporalRS").refine()  # missing calendar
        OpaqueRS(type_='TemporalRS')
        """
        if missing_required_member(self) is not None:
            return OpaqueRS(type_=self.type_)

        match self.type_:
            case "GeographicCRS":
                return GeographicCRS(id=self.id)

            case "ProjectedCRS":
                return ProjectedCRS(id=self.id)

            case "VerticalCRS":
                return VerticalCRS(id=self.id)

            case "TemporalRS":
                # missing_required_member ensured ``calendar`` is present.
                return TemporalRS(
                    calendar=cast(str, self.calendar), time_scale=self.time_scale
                )

            case "IdentifierRS":
                # missing_required_member ensured ``target_concept`` is present.
                return IdentifierRS(
                    target_concept=cast(Concept, self.target_concept),
                    id=self.id,
                    label=self.label,
                    description=self.description,
                    identifiers=self.identifiers,
                )

            case _:
                return OpaqueRS(type_=self.type_)


class ReferenceSystemConnection(CovJSONStruct, frozen=True):
    """Connects a set of coordinate identifiers to their reference system.

    This object has no ``type`` member of its own. ``system`` is the permissive
    `ReferenceSystem` core; call `~ReferenceSystem.refine` on it for a precise
    typed variant.

    Examples
    --------
    >>> import msgspec
    >>> blob = '''
    ... {
    ...   "coordinates": ["t"],
    ...   "system": {"type": "TemporalRS", "calendar": "Gregorian"}
    ... }
    ... '''
    >>> rsc = msgspec.json.decode(blob, type=ReferenceSystemConnection)
    >>> rsc.coordinates
    ('t',)
    >>> rsc.system.refine()  # the precise reference-system variant
    TemporalRS(calendar='Gregorian', time_scale=None)
    """

    coordinates: tuple[str, ...]
    system: ReferenceSystem
