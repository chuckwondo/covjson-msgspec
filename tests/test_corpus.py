"""Conformance corpus: exercise decode / encode / validate on real documents.

Each document under ``tests/corpus/`` is vendored from an upstream project (see
``tests/corpus/README.md`` for provenance and licensing) and run through two
passes: a thin round-trip (``decode -> encode -> decode`` must be stable) and
``validate(check_values=True)``. The playground corpus is all positive (valid)
CoverageJSON, so validation must report no error-severity issues.
"""

import pathlib
import tomllib

import msgspec
import pytest

from covjson_msgspec import CoverageJSON, decode, encode, validate
from covjson_msgspec.validation import Severity

_CORPUS = pathlib.Path(__file__).parent / "corpus"
_PLAYGROUND = sorted((_CORPUS / "playground").rglob("*.covjson"))

_PYDANTIC = _CORPUS / "covjson-pydantic"
_PYDANTIC_FILES = sorted(_PYDANTIC.glob("*.json"))
_MANIFEST = tomllib.loads((_PYDANTIC / "manifest.toml").read_text())
# Negatives are enumerated in the manifest; every other fixture is positive.
_STRUCTURAL_REJECT = {entry["file"] for entry in _MANIFEST["structural_reject"]}
_VALIDATE_REJECT = {
    entry["file"]: set(entry["codes"]) for entry in _MANIFEST["validate_reject"]
}

_NEGATIVE = _CORPUS / "negative"
_NEGATIVE_FILES = sorted(_NEGATIVE.glob("*.json"))
# Hand-authored docs, each targeting one validate() (code, severity) pair.
_NEGATIVE_ISSUES = {
    entry["file"]: {(issue["code"], issue["severity"]) for issue in entry["issues"]}
    for entry in tomllib.loads((_NEGATIVE / "manifest.toml").read_text())["case"]
}


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
    assert _error_codes(decode(path.read_bytes())) == set()


def test_covjson_pydantic_corpus_is_present() -> None:
    # The pinned covjson-pydantic snapshot vendors exactly 50 fixtures; the
    # manifest must reference only files that exist, with no double classification.
    assert len(_PYDANTIC_FILES) == 50

    classified = _STRUCTURAL_REJECT | set(_VALIDATE_REJECT)
    names = {path.name for path in _PYDANTIC_FILES}

    assert classified <= names
    assert _STRUCTURAL_REJECT.isdisjoint(_VALIDATE_REJECT)


@pytest.mark.parametrize("path", _PYDANTIC_FILES, ids=_ids(_PYDANTIC_FILES))
def test_covjson_pydantic_fixture_matches_manifest(path: pathlib.Path) -> None:
    raw = path.read_bytes()

    if path.name in _STRUCTURAL_REJECT:
        # Not a root document, or a malformed one: decode must reject it.
        with pytest.raises((msgspec.ValidationError, ValueError)):
            decode(raw)

        return

    # Everything else decodes and round-trips (positive and validate-reject alike).
    obj = decode(raw)

    assert decode(encode(obj)) == obj

    if path.name in _VALIDATE_REJECT:
        assert _error_codes(obj) == _VALIDATE_REJECT[path.name]
    else:
        assert _error_codes(obj) == set()


def test_negative_corpus_is_present() -> None:
    # Every hand-authored document is classified, and vice versa.
    assert _NEGATIVE_FILES
    assert {path.name for path in _NEGATIVE_FILES} == set(_NEGATIVE_ISSUES)


@pytest.mark.parametrize("path", _NEGATIVE_FILES, ids=_ids(_NEGATIVE_FILES))
def test_negative_document_flags_expected_issues(path: pathlib.Path) -> None:
    obj = decode(path.read_bytes())

    # The docs are valid CoverageJSON (they decode and round-trip); what makes
    # them negative is the validate() issues they carry.
    assert decode(encode(obj)) == obj
    assert _issues(obj) == _NEGATIVE_ISSUES[path.name]


def _error_codes(obj: CoverageJSON) -> set[str]:
    return {
        issue.code
        for issue in validate(obj, check_values=True)
        if issue.severity is Severity.ERROR
    }


def _issues(obj: CoverageJSON) -> set[tuple[str, str]]:
    return {
        (issue.code, issue.severity.value) for issue in validate(obj, check_values=True)
    }
