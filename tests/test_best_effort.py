"""Domain-free tests for the best-effort fetching vocabulary.

These exercise the pure strategy reducers and the failure/error values in
isolation, with no tiles or arrays. The ``collect`` orchestration they feed is
covered end-to-end through `TiledNdArray.assemble` in ``test_range.py`` (and by
its own doctests).
"""

import pytest

from covjson_msgspec import (
    FailureKind,
    FetchError,
    FetchFailure,
    Verdict,
    collect_all,
    halt_on_unrecoverable,
    stop_after,
)


def test_fetch_failure_str_reads_naturally() -> None:
    failure = _failure(FailureKind.TRANSIENT, url="http://ex/0", message="boom")

    assert str(failure) == "transient fetching http://ex/0: boom"


@pytest.mark.parametrize("kind", list(FailureKind))
def test_collect_all_always_collects(kind: FailureKind) -> None:
    failure = _failure(kind)

    assert collect_all((), failure) is Verdict.COLLECT
    assert collect_all((failure,) * 5, failure) is Verdict.COLLECT


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FailureKind.TRANSIENT, Verdict.COLLECT),
        (FailureKind.UNRECOVERABLE, Verdict.HALT),
    ],
)
def test_halt_on_unrecoverable(kind: FailureKind, expected: Verdict) -> None:
    assert halt_on_unrecoverable((), _failure(kind)) is expected


@pytest.mark.parametrize(
    ("limit", "prior", "expected"),
    [
        (1, 0, Verdict.HALT),
        (2, 0, Verdict.COLLECT),
        (2, 1, Verdict.HALT),
        (3, 1, Verdict.COLLECT),
    ],
)
def test_stop_after_verdict(limit: int, prior: int, expected: Verdict) -> None:
    strategy = stop_after(limit)
    sofar = (_failure(FailureKind.TRANSIENT),) * prior

    assert strategy(sofar, _failure(FailureKind.TRANSIENT)) is expected


@pytest.mark.parametrize("limit", [0, -1])
def test_stop_after_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="requires limit >= 1"):
        stop_after(limit)


def test_fetcherror_carries_failures_and_summarizes() -> None:
    first = _failure(FailureKind.TRANSIENT, url="a", message="boom")
    second = _failure(FailureKind.UNRECOVERABLE, url="b", message="bad")
    err = FetchError((first, second))

    assert err.failures == (first, second)
    assert str(err) == "transient fetching a: boom (and 1 more)"


def _failure(kind: FailureKind, url: str = "u", message: str = "x") -> FetchFailure:
    return FetchFailure(url=url, kind=kind, message=message)
