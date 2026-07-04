"""Internal reader for CoverageJSON i18n language maps.

The counterpart to the public `~covjson_msgspec.i18n.i18n` builder: where that
assembles a language map, `display` collapses one back to a single string for
presentation. Kept private (a leaf module, imported by `xarray` and `_repr`)
because its only callers are internal display paths; promote it to public API
only if a user asks. It picks a fixed English-first order rather than taking a
preferred-language argument for the same reason: no caller varies it yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from covjson_msgspec.i18n import I18n


def display(i18n: I18n | None) -> str:
    """Pick one display string from an `~covjson_msgspec.i18n.I18n` language map.

    Collapses a language map to a single string, preferring English (``"en"``),
    then the undetermined tag (``"und"``), then any remaining value. ``None`` or
    an empty map yields the empty string, so the result is always a plain ``str``
    ready to display.

    Parameters
    ----------
    text
        A language map (``language tag -> string``), or ``None``.

    Returns
    -------
    str
        The chosen string; ``""`` for ``None`` or an empty map.

    Examples
    --------
    >>> display({"en": "Air temperature", "de": "Lufttemperatur"})
    'Air temperature'
    >>> display({"und": "Wind"})
    'Wind'
    >>> display({"de": "hallo", "fr": "bonjour"})
    'hallo'
    >>> display(None)
    ''
    """
    if not i18n:
        return ""

    return next(
        (text for lang, text in i18n.items() if lang in {"en", "und"}),
        next(iter(i18n.values())),
    )
