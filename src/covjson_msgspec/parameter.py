"""Parameters and the things they describe: units, observed properties, and
categories.

A CoverageJSON *parameter* describes one variable found in a coverage's ranges.
It comes in two mutually exclusive shapes:

* **continuous**: carries a `Unit`; or
* **categorical**: its `ObservedProperty` lists `Category` values and a
  ``category_encoding`` maps each category id to the integer code(s) used in the
  range. A categorical parameter MUST NOT carry a unit.

Use the builders `Parameter.continuous` and `Parameter.categorical`, which each
expose only the fields valid for that shape. The continuous/categorical rule is
enforced whenever a `Parameter` is created, including when one is decoded.

Spec: [Parameter objects][spec-parameter] and [ParameterGroup objects][spec-group].

[spec-parameter]: https://github.com/covjson/specification/blob/master/spec.md#3-parameter-objects
[spec-group]: https://github.com/covjson/specification/blob/master/spec.md#4-parametergroup-objects
"""

from typing import Self

import msgspec

from covjson_msgspec._base import CovJSONStruct
from covjson_msgspec.i18n import I18n


class Symbol(CovJSONStruct, frozen=True):
    """A unit symbol together with the URI of its coding scheme.

    This is the object form of `Unit.symbol` (the other form is a bare string).
    The coding-scheme URI is the CoverageJSON ``type`` member; the attribute is
    ``type_`` (PEP 8 trailing underscore) to avoid shadowing the builtin, and is
    mapped back to ``type`` on the wire.

    Examples
    --------
    >>> import msgspec
    >>> sym = Symbol(value="Cel", type_="http://www.opengis.net/def/uom/UCUM/Cel")
    >>> msgspec.json.encode(sym)
    b'{"value":"Cel","type":"http://www.opengis.net/def/uom/UCUM/Cel"}'
    """

    value: str
    # Wire name is ``type``; the ``type_`` attribute avoids shadowing the
    # builtin. An explicit ``name=`` overrides the base's "camel" rule.
    type_: str = msgspec.field(name="type")


class Unit(CovJSONStruct, frozen=True):
    """A unit of measurement.

    At least one of ``label`` or ``symbol`` is required (a CoverageJSON rule),
    and ``symbol`` may be a bare string or a `Symbol`.

    Examples
    --------
    >>> Unit(symbol="K")
    Unit(id=None, label=None, symbol='K')
    >>> import msgspec
    >>> msgspec.json.encode(Unit(symbol="K"))  # unset optional fields are omitted
    b'{"symbol":"K"}'
    >>> msgspec.json.decode(b'{"symbol": "K"}', type=Unit).symbol  # bare string
    'K'
    >>> msgspec.json.decode(
    ...     b'{"symbol": {"value": "Cel", "type": "http://example/Cel"}}',
    ...     type=Unit,
    ... ).symbol  # the object form decodes to a Symbol
    Symbol(value='Cel', type_='http://example/Cel')
    >>> Unit()
    Traceback (most recent call last):
        ...
    ValueError: Unit requires at least one of `label` or `symbol`
    """

    id: str | None = None
    label: I18n | None = None
    symbol: str | Symbol | None = None

    def __post_init__(self) -> None:
        # O(1) invariant: cheap enough to always run, on construction and on
        # decode, so a Unit can never exist without a label or symbol.
        if self.label is None and self.symbol is None:
            raise ValueError("Unit requires at least one of `label` or `symbol`")


class Category(CovJSONStruct, frozen=True):
    """One allowed value of a categorical parameter.

    Examples
    --------
    >>> Category(id="1", label={"en": "Water"})
    Category(id='1', label={'en': 'Water'}, description=None)
    """

    id: str
    label: I18n
    description: I18n | None = None


class ObservedProperty(CovJSONStruct, frozen=True):
    """The property that a parameter observes.

    ``categories`` is present only for categorical parameters; its presence is
    what marks a `Parameter` as categorical.

    Examples
    --------
    >>> ObservedProperty(label={"en": "Air temperature"}).categories is None
    True
    """

    label: I18n
    id: str | None = None
    description: I18n | None = None
    # tuple (not list) so the struct stays immutable; see CovJSONStruct.
    categories: tuple[Category, ...] | None = None


# Maps a category id to the integer code (or codes) representing it in a range.
# The multi-code form is a tuple for immutability, consistent with other
# sequence members.
CategoryEncoding = dict[str, int | tuple[int, ...]]


class Parameter(CovJSONStruct, frozen=True, tag="Parameter"):
    """A description of one coverage variable.

    Prefer the builders `Parameter.continuous` and `Parameter.categorical` over
    the raw constructor: each exposes only the fields valid for its kind, so the
    mutually exclusive ``unit`` / ``category_encoding`` pair cannot be mixed up.

    Examples
    --------
    >>> from covjson_msgspec import i18n
    >>> temp = Parameter.continuous(
    ...     ObservedProperty(label=i18n("Air temperature")),
    ...     Unit(symbol="K"),
    ... )
    >>> temp.unit.symbol
    'K'

    A categorical parameter lists categories and encodes them, and must not
    carry a unit (enforced even through the raw constructor):

    >>> land_cover = ObservedProperty(
    ...     label=i18n("Land cover"),
    ...     categories=(Category(id="1", label=i18n("Water")),),
    ... )
    >>> Parameter.categorical(land_cover, {"1": 1}).category_encoding
    {'1': 1}
    >>> Parameter.continuous(land_cover, Unit(symbol="K"))
    Traceback (most recent call last):
        ...
    ValueError: a categorical Parameter must not carry a `unit`

    Decoding maps lowerCamelCase wire names to snake_case attributes, and any
    omitted optional field falls back to its default:

    >>> import msgspec
    >>> blob = (
    ...     b'{"type": "Parameter",'
    ...     b' "observedProperty": {"label": {"en": "Air temperature"}},'
    ...     b' "unit": {"symbol": "K"}}'
    ... )
    >>> param = msgspec.json.decode(blob, type=Parameter)
    >>> param.observed_property.label
    {'en': 'Air temperature'}
    >>> param.category_encoding is None  # omitted optional -> default
    True
    """

    observed_property: ObservedProperty
    id: str | None = None
    label: I18n | None = None
    description: I18n | None = None
    unit: Unit | None = None
    category_encoding: CategoryEncoding | None = None

    def __post_init__(self) -> None:
        # Continuous vs categorical are mutually exclusive; presence of
        # observed_property.categories is the discriminator. Both checks are
        # O(1), so they run on every path (construction and decode).
        categorical = self.observed_property.categories is not None

        if categorical and self.unit is not None:
            raise ValueError("a categorical Parameter must not carry a `unit`")

        if self.category_encoding is not None and not categorical:
            raise ValueError(
                "`category_encoding` requires `observed_property.categories`"
            )

    @classmethod
    def continuous(
        cls,
        observed_property: ObservedProperty,
        unit: Unit,
        *,
        id: str | None = None,
        label: I18n | None = None,
        description: I18n | None = None,
    ) -> Self:
        """Build a continuous (unit-bearing) parameter.

        Parameters
        ----------
        observed_property
            The observed property; must not declare ``categories``.
        unit
            The unit of measurement for the parameter's values.
        id
            Identifier, typically a URI.
        label
            Short human-readable name.
        description
            Longer human-readable description.

        Returns
        -------
        Parameter
            A continuous parameter.

        Raises
        ------
        ValueError
            If ``observed_property`` declares ``categories`` (a categorical
            property cannot be paired with a unit).
        """
        return cls(
            observed_property=observed_property,
            unit=unit,
            id=id,
            label=label,
            description=description,
        )

    @classmethod
    def categorical(
        cls,
        observed_property: ObservedProperty,
        category_encoding: CategoryEncoding,
        *,
        id: str | None = None,
        label: I18n | None = None,
        description: I18n | None = None,
    ) -> Self:
        """Build a categorical parameter (no unit; categories are encoded).

        Parameters
        ----------
        observed_property
            The observed property; must declare ``categories``.
        category_encoding
            Maps each category id to the integer code(s) used in the range.
        id
            Identifier, typically a URI.
        label
            Short human-readable name.
        description
            Longer human-readable description.

        Returns
        -------
        Parameter
            A categorical parameter.

        Raises
        ------
        ValueError
            If ``observed_property`` does not declare ``categories``.
        """
        return cls(
            observed_property=observed_property,
            category_encoding=category_encoding,
            id=id,
            label=label,
            description=description,
        )


class ParameterGroup(CovJSONStruct, frozen=True, tag="ParameterGroup"):
    """A logical grouping of parameters (e.g. the components of a vector).

    ``members`` references parameter keys; at least one of ``label`` or
    ``observed_property`` must be present.

    Examples
    --------
    >>> ParameterGroup(members=("u", "v"), label={"en": "Wind"}).members
    ('u', 'v')
    >>> ParameterGroup(members=("u", "v"))
    Traceback (most recent call last):
        ...
    ValueError: ParameterGroup requires `label` or `observed_property`
    """

    members: tuple[str, ...]
    id: str | None = None
    label: I18n | None = None
    description: I18n | None = None
    observed_property: ObservedProperty | None = None

    def __post_init__(self) -> None:
        if self.label is None and self.observed_property is None:
            raise ValueError("ParameterGroup requires `label` or `observed_property`")
