"""Reference systems and how they connect to a domain's coordinates.

Every reference system carries a ``type`` discriminator, so this maps onto a
clean msgspec tagged union: `ReferenceSystem` is
``GeographicCRS | ProjectedCRS | VerticalCRS | TemporalRS | IdentifierRS``, and
msgspec dispatches on ``type`` natively when decoding. A
`ReferenceSystemConnection` ties a set of coordinate identifiers to the system
that references them.

Per the CoverageJSON standard the three spatial CRS types share the same shape
(an optional ``id`` URI and ``description``); the standard does not define an
embedded coordinate-system (``cs``) object, so CRSs are identified by ``id``.

Spec: [Reference system objects](https://github.com/covjson/specification/blob/master/spec.md#5-reference-system-objects).
"""

from __future__ import annotations

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.i18n import I18n


class GeographicCRS(CovJSONStruct, frozen=True, tag="GeographicCRS"):
    """A geographic coordinate reference system (e.g. lon/lat).

    Examples
    --------
    >>> import msgspec
    >>> crs = GeographicCRS(id="http://www.opengis.net/def/crs/OGC/1.3/CRS84")
    >>> msgspec.json.encode(crs)  # type tag first; unset fields are omitted
    b'{"type":"GeographicCRS","id":"http://www.opengis.net/def/crs/OGC/1.3/CRS84"}'
    """

    id: str | None = None
    description: I18n | None = None


class ProjectedCRS(CovJSONStruct, frozen=True, tag="ProjectedCRS"):
    """A projected coordinate reference system (e.g. a map projection).

    Examples
    --------
    >>> import msgspec
    >>> crs = ProjectedCRS(id="http://www.opengis.net/def/crs/EPSG/0/27700")
    >>> msgspec.json.encode(crs)
    b'{"type":"ProjectedCRS","id":"http://www.opengis.net/def/crs/EPSG/0/27700"}'
    """

    id: str | None = None
    description: I18n | None = None


class VerticalCRS(CovJSONStruct, frozen=True, tag="VerticalCRS"):
    """A vertical coordinate reference system (e.g. height or depth).

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
    description: I18n | None = None


class TemporalRS(CovJSONStruct, frozen=True, tag="TemporalRS"):
    """A temporal reference system.

    ``calendar`` is required by the standard (``"Gregorian"`` or a URI). The
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


class Concept(CovJSONStruct, frozen=True):
    """A referenced concept: a label and an optional description.

    Used for `IdentifierRS.target_concept` and for the values of
    `IdentifierRS.identifiers`.

    Examples
    --------
    >>> concept = Concept(
    ...     label={"en": "Water"}, description={"en": "Open water surface"}
    ... )
    >>> concept.label
    {'en': 'Water'}
    """

    label: I18n
    description: I18n | None = None


class IdentifierRS(CovJSONStruct, frozen=True, tag="IdentifierRS"):
    """An identifier-based reference system (categorical / coded values).

    ``target_concept`` (wire ``targetConcept``) is required; ``identifiers``
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
    identifiers: dict[str, Concept] | None = None


# Tagged union over the ``type`` discriminator; msgspec dispatches natively.
ReferenceSystem = GeographicCRS | ProjectedCRS | VerticalCRS | TemporalRS | IdentifierRS


class ReferenceSystemConnection(CovJSONStruct, frozen=True):
    """Connects a set of coordinate identifiers to their reference system.

    This object has no ``type`` member of its own; ``system`` is the tagged
    union and is dispatched on its own ``type``.

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
    >>> rsc.system  # decoded to the matching reference-system type
    TemporalRS(calendar='Gregorian', time_scale=None)
    """

    coordinates: tuple[str, ...]
    system: ReferenceSystem
