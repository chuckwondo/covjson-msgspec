"""Conformance corpus: exercise decode / encode / validate on real documents.

Each document under ``tests/corpus/`` is vendored from an upstream project (see
``tests/corpus/README.md`` for provenance and licensing) and run through two
passes: a thin round-trip (``decode -> encode -> decode`` must be stable) and
``validate(check_values=True)``. The playground corpus is all positive (valid)
CoverageJSON, so validation must report no error-severity issues.
"""

import pathlib

import pytest

from covjson_msgspec import decode, encode, validate
from covjson_msgspec.validation import Severity

_CORPUS = pathlib.Path(__file__).parent / "corpus"
_PLAYGROUND = sorted((_CORPUS / "playground").rglob("*.covjson"))


def _ids(paths: list[pathlib.Path]) -> list[str]:
    return [str(path.relative_to(_CORPUS)) for path in paths]


def test_playground_corpus_is_present() -> None:
    # Guard against a silently empty parametrization (e.g. a partial checkout):
    # the pinned playground snapshot vendors exactly 28 documents.
    assert len(_PLAYGROUND) == 28


@pytest.mark.parametrize("path", _PLAYGROUND, ids=_ids(_PLAYGROUND))
def test_playground_document_round_trips(path: pathlib.Path) -> None:
    obj = decode(path.read_bytes())
    # decode -> encode -> decode is stable once the object is canonical (the
    # first decode coerces lists to tuples and drops foreign members).
    assert decode(encode(obj)) == obj


@pytest.mark.parametrize("path", _PLAYGROUND, ids=_ids(_PLAYGROUND))
def test_playground_document_validates_clean(path: pathlib.Path) -> None:
    issues = validate(decode(path.read_bytes()), check_values=True)
    errors = [issue for issue in issues if issue.severity is Severity.ERROR]

    assert errors == []
