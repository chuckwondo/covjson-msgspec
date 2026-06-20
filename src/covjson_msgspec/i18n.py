"""CoverageJSON internationalized strings (i18n objects).

In CoverageJSON, human-readable text members (``label``, ``description``) are
*language maps*: JSON objects whose keys are RFC 5646 language tags and whose
values are the corresponding strings. The special tag ``"und"`` (undetermined)
labels text whose language is unknown. The spec has no bare-string form for
these members, so we model i18n as ``dict[str, str]`` and provide :func:`i18n`
to build one without hand-writing the mapping.
"""

# A CoverageJSON i18n object: language tag -> string ("und" if undetermined).
# Deliberately a plain ``dict`` (not a NewType): it is exactly a JSON object and
# users benefit from the familiar dict API. This is also why structs carrying a
# label/description are usually unhashable -- a dict member is mutable.
#
# Plain-assignment alias (not the PEP 695 ``type`` statement, which needs 3.12+;
# our floor is 3.11).
I18n = dict[str, str]


def i18n(text: str | None = None, /, **languages: str) -> I18n:
    """Build an `I18n` language map.

    A positional ``text`` is recorded under the undetermined tag ``"und"``;
    keyword arguments supply language-tagged strings. The two may be combined.

    Parameters
    ----------
    text
        Text whose language is undetermined; stored under the ``"und"`` tag.
    **languages
        Language-tagged strings, e.g. ``en="..."`` (keys are RFC 5646 tags).

    Returns
    -------
    I18n
        A language map (``dict[str, str]``) with at least one entry.

    Raises
    ------
    ValueError
        If neither ``text`` nor any language is given (an empty map is not a
        valid i18n object).

    Examples
    --------
    >>> i18n("Air temperature")
    {'und': 'Air temperature'}
    >>> i18n(en="Sea water temperature", de="Meerwassertemperatur")
    {'en': 'Sea water temperature', 'de': 'Meerwassertemperatur'}
    >>> i18n("Air temperature", en="Air temperature")
    {'und': 'Air temperature', 'en': 'Air temperature'}
    >>> i18n()
    Traceback (most recent call last):
        ...
    ValueError: i18n() requires `text` or at least one language
    """
    result: I18n = {}

    if text is not None:
        result["und"] = text

    result.update(languages)

    if not result:
        raise ValueError("i18n() requires `text` or at least one language")

    return result
