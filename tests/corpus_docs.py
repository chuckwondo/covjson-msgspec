"""The conformant corpus coverages, shared by the bridge sweeps.

The ``tests`` directory is on ``pythonpath`` (see ``[tool.pytest.ini_options]``
in ``pyproject.toml``), so test modules import this as
``from corpus_docs import ...`` without a ``tests`` package.

Each bridge sweep pushes every one of these through its own conversion, which is
how a gap like #235's Timestamp TypeError or #238's size-1 dimension collision
fails a test rather than waiting to be noticed. The list has one home so the
sweeps cannot drift apart on which documents they cover or how they key them.
"""

import contextlib
import functools
import pathlib

import msgspec

from covjson_msgspec import Coverage, CoverageCollection, decode

CORPUS = pathlib.Path(__file__).parent / "corpus"


@functools.cache
def corpus_coverages() -> tuple[tuple[str, Coverage | CoverageCollection], ...]:
    """The conformant corpus documents that decode to a coverage, by relative path.

    A bridge takes a `Coverage` / `CoverageCollection`, so the bare Domain and
    NdArray documents (and the structural rejects test_corpus.py pins) drop out
    here. The ``negative/`` tree is excluded deliberately: what a bridge does with
    a deliberately malformed document is not a contract.

    Keyed by the corpus-relative path, as test_corpus.py's ``_ids`` does, so two
    nested documents sharing a basename stay distinguishable rather than becoming
    pytest's ``name0`` / ``name1``.

    Cached because every sweep calls it at module load, and the decoded documents
    are immutable, so one decode of the corpus serves them all.

    Returns
    -------
    tuple of (str, Coverage or CoverageCollection)
        Each document's corpus-relative path with the object it decodes to, in
        path order.

    Examples
    --------
    >>> coverages = corpus_coverages()
    >>> bool(coverages)
    True

    Every key is a relative path, never a bare basename:

    >>> [name for name, _ in coverages if "/" not in name]
    []
    """
    # The same globs test_corpus.py uses: playground nests (grid-tiled/a, ...),
    # the covjson-pydantic fixtures are flat.
    paths = (
        *(CORPUS / "playground").rglob("*.covjson"),
        *(CORPUS / "covjson-pydantic").glob("*.json"),
    )
    kept: list[tuple[str, Coverage | CoverageCollection]] = []

    for path in sorted(paths):
        # DecodeError is ValidationError's parent, so a malformed document drops
        # out of the sweep rather than failing collection for the whole module.
        with contextlib.suppress(msgspec.DecodeError):
            obj = decode(path.read_bytes())

            if isinstance(obj, Coverage | CoverageCollection):
                kept.append((str(path.relative_to(CORPUS)), obj))

    return tuple(kept)
