"""Best-effort fetching: failures as values with a pluggable error strategy.

Fetching many independent documents (the tiles of a `TiledNdArray`, or the
domain/range references of a coverage) is all-or-nothing by default: the first
failure aborts the whole batch. That is the right default, but a caller often
wants the documents that did load, with the failures reported, or finer control
such as "tolerate transient errors but stop on an unrecoverable one."

This module provides that as a *functional core*: a fetch failure is a value (a
`FetchFailure`), and how the batch responds to failures is a pure reducer (a
`FailureStrategy`) that, given the failures collected so far and a new one,
returns a `Verdict` (keep collecting, or halt). The canned strategies cover the
common policies (`fail_fast`, `collect_all`, `halt_on_unrecoverable`,
`stop_after`); a caller can supply any pure function of the same shape. When a
strategy halts, the shell raises a `FetchError` carrying the failures collected
so far.

The machinery here is deliberately independent of what is being fetched: it
knows nothing about tiles, arrays, or coverages. A consumer supplies a way to
fetch one item and a way to build its domain-specific failure, and drives the
batch through `collect` (synchronous, lazy) or `collect_async` (concurrent,
eager). The tile-assembly consumer subclasses `FetchFailure` as ``TileFailure``
and reference resolution as ``ReferenceFailure``.

The public names (`FetchFailure`, `FailureKind`, `Verdict`, `FailureStrategy`,
the strategies, and `FetchError`) are re-exported from the top-level
``covjson_msgspec`` package.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Iterable, Sequence
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

import msgspec

from covjson_msgspec._fetch import ReferencedDocumentError


class FailureKind(StrEnum):
    """How recoverable a fetch failure is, so a strategy can react to it.

    A *decode* failure (the fetched bytes are not valid CoverageJSON) is
    ``UNRECOVERABLE``: retrying the same URL will not help. Any other failure a
    caller's fetcher raises (a network error, a missing key) is treated as
    ``TRANSIENT``: it might succeed on a retry, and a strategy may reasonably
    tolerate it.

    Examples
    --------
    >>> FailureKind.UNRECOVERABLE.value
    'unrecoverable'
    """

    TRANSIENT = "transient"
    UNRECOVERABLE = "unrecoverable"


class FetchFailure(msgspec.Struct, frozen=True, kw_only=True):
    """One failed fetch, as a value: the URL, how recoverable it was, and why.

    This is the shared base for every best-effort failure. Consumers subclass it
    to add where the failure happened (a tile's offsets, a reference's slot); the
    strategies and `FetchError` work in terms of this base, so they stay
    independent of what was being fetched.

    ``message`` is the human-readable text of the underlying error; a strategy
    switches on ``kind``, not on the message.

    Attributes
    ----------
    url
        The URL whose fetch (or decode) failed.
    kind
        Whether the failure is transient or unrecoverable (`FailureKind`).
    message
        The underlying error's message.

    Examples
    --------
    >>> failure = FetchFailure(
    ...     url="http://ex/0.covjson", kind=FailureKind.TRANSIENT, message="timed out"
    ... )
    >>> str(failure)
    'transient fetching http://ex/0.covjson: timed out'
    """

    url: str
    kind: FailureKind
    message: str

    def __str__(self) -> str:
        return f"{self.kind} fetching {self.url}: {self.message}"


class Verdict(StrEnum):
    """A strategy's decision about a single failure: keep going, or stop.

    ``COLLECT`` records the failure and continues (the surviving documents are
    still assembled, with holes where fetches failed). ``HALT`` stops the batch;
    the shell then raises a `FetchError` with the failures collected so far.

    Examples
    --------
    >>> Verdict.COLLECT.value
    'collect'
    """

    COLLECT = "collect"
    HALT = "halt"


_C = TypeVar("_C")
_P = TypeVar("_P")
_F = TypeVar("_F", bound=FetchFailure)

#: A pure reducer deciding a batch's response to each failure. Given the failures
#: collected so far and the new one, it returns a `Verdict`. It is generic in the
#: `FetchFailure` subtype, so a consumer can write a strategy over its own failure
#: type; the canned strategies below work for any subtype.
FailureStrategy: TypeAlias = Callable[[Sequence[_F], _F], Verdict]


def fail_fast(sofar: Sequence[FetchFailure], failure: FetchFailure) -> Verdict:
    """Halt on the first failure: the default, all-or-nothing behavior.

    Returns `Verdict.HALT` unconditionally, so the first failed fetch aborts the
    batch and the shell raises a `FetchError` chaining the original exception (its
    ``__cause__``). This is the default strategy for the best-effort helpers.

    Parameters
    ----------
    sofar
        The failures collected before this one (always empty, since this halts on
        the first; present for the `FailureStrategy` shape).
    failure
        The failure just encountered (unused).

    Returns
    -------
    Verdict
        Always `Verdict.HALT`.

    Examples
    --------
    >>> failure = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> fail_fast((), failure)
    <Verdict.HALT: 'halt'>
    """
    return Verdict.HALT


def collect_all(sofar: Sequence[FetchFailure], failure: FetchFailure) -> Verdict:
    """Tolerate every failure: assemble whatever loaded, reporting the rest.

    Never halts, so the batch always runs to completion and the failures are
    returned alongside the surviving documents.

    Parameters
    ----------
    sofar
        The failures collected before this one (unused; present for the
        `FailureStrategy` shape).
    failure
        The failure just encountered (unused).

    Returns
    -------
    Verdict
        Always `Verdict.COLLECT`.

    Examples
    --------
    >>> failure = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> collect_all((), failure)
    <Verdict.COLLECT: 'collect'>
    """
    return Verdict.COLLECT


def halt_on_unrecoverable(
    sofar: Sequence[FetchFailure], failure: FetchFailure
) -> Verdict:
    """Tolerate transient failures, but halt on an unrecoverable one.

    A malformed document (an `UNRECOVERABLE` decode failure) means the batch is
    poisoned, so it stops; a `TRANSIENT` failure is collected and the batch
    continues.

    Parameters
    ----------
    sofar
        The failures collected before this one (unused).
    failure
        The failure just encountered; its ``kind`` decides the verdict.

    Returns
    -------
    Verdict
        `Verdict.HALT` if ``failure.kind`` is `FailureKind.UNRECOVERABLE`,
        otherwise `Verdict.COLLECT`.

    Examples
    --------
    >>> transient = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> halt_on_unrecoverable((), transient)
    <Verdict.COLLECT: 'collect'>
    >>> bad = FetchFailure(url="u", kind=FailureKind.UNRECOVERABLE, message="x")
    >>> halt_on_unrecoverable((), bad)
    <Verdict.HALT: 'halt'>
    """
    unrecoverable = failure.kind is FailureKind.UNRECOVERABLE

    return Verdict.HALT if unrecoverable else Verdict.COLLECT


def stop_after(limit: int) -> FailureStrategy[FetchFailure]:
    """Build a strategy that halts once ``limit`` failures have accumulated.

    The ``limit``-th failure is the last one collected before halting, so the
    resulting `FetchError` carries exactly ``limit`` failures. ``stop_after(1)``
    halts on the very first failure.

    Parameters
    ----------
    limit
        The number of failures to tolerate before halting; must be at least 1.

    Returns
    -------
    FailureStrategy
        A pure reducer over ``(failures_so_far, new_failure)``.

    Raises
    ------
    ValueError
        If ``limit`` is less than 1.

    Examples
    --------
    >>> tolerate_two = stop_after(2)
    >>> failure = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> tolerate_two((), failure)
    <Verdict.COLLECT: 'collect'>
    >>> tolerate_two((failure,), failure)
    <Verdict.HALT: 'halt'>
    >>> stop_after(0)
    Traceback (most recent call last):
        ...
    ValueError: stop_after(limit) requires limit >= 1, got 0
    """
    if limit < 1:
        msg = f"stop_after(limit) requires limit >= 1, got {limit}"
        raise ValueError(msg)

    def strategy(sofar: Sequence[FetchFailure], failure: FetchFailure) -> Verdict:
        return Verdict.HALT if len(sofar) + 1 >= limit else Verdict.COLLECT

    return strategy


class FetchError(Exception):
    """Raised when a best-effort batch halts on a failure.

    The failures collected up to and including the one that halted the batch are
    available on the ``failures`` attribute; the partial result (the documents
    that did load) is deliberately not carried, since halting means "abort, no
    artifact." Use `collect_all` if you want the partial result instead.

    Examples
    --------
    >>> a = FetchFailure(url="a", kind=FailureKind.TRANSIENT, message="boom")
    >>> b = FetchFailure(url="b", kind=FailureKind.UNRECOVERABLE, message="bad")
    >>> err = FetchError((a, b))
    >>> str(err)
    'transient fetching a: boom (and 1 more)'
    >>> len(err.failures)
    2
    """

    def __init__(self, failures: tuple[FetchFailure, ...]) -> None:
        """Store the failures and build a summary from the first one's message."""
        self.failures = failures
        count = len(failures)
        summary = str(failures[0]) if failures else "fetch failed"
        suffix = "" if count <= 1 else f" (and {count - 1} more)"
        super().__init__(f"{summary}{suffix}")


def collect(
    items: Iterable[_C],
    fetch_one: Callable[[_C], _P],
    make_failure: Callable[[_C, Exception, FailureKind], _F],
    strategy: FailureStrategy[_F],
) -> tuple[Sequence[_P], Sequence[_F]]:
    """Fetch each item in turn, folding a strategy over the failures.

    Lazily fetches one item at a time: each is fetched via ``fetch_one`` and, on
    failure, turned into a value via ``make_failure``; the strategy then decides
    whether to keep collecting or halt. Because it is lazy, a halting strategy
    stops fetching at the halt point rather than fetching the whole batch first.

    Parameters
    ----------
    items
        The items to fetch (each carries whatever context ``fetch_one`` and
        ``make_failure`` need, e.g., a URL and a position).
    fetch_one
        Maps one item to its fetched payload, raising on failure.
    make_failure
        Builds a domain-specific `FetchFailure` from the item, the raised
        exception, and its classified `FailureKind`.
    strategy
        The pure reducer deciding the batch's response to each failure.

    Returns
    -------
    tuple
        The successfully fetched payloads (in order), and the collected failures.

    Raises
    ------
    FetchError
        If the strategy returns `Verdict.HALT` for a failure.

    Examples
    --------
    >>> store = {"a": 1, "c": 3}
    >>> def make(url, exc, kind):
    ...     return FetchFailure(url=url, kind=kind, message=str(exc))
    >>> payloads, failures = collect(
    ...     ["a", "b", "c"], store.__getitem__, make, collect_all
    ... )
    >>> payloads
    (1, 3)
    >>> [failure.url for failure in failures]
    ['b']
    """
    outcomes = (
        _attempt(
            functools.partial(fetch_one, item),
            functools.partial(make_failure, item),
        )
        for item in items
    )

    return _fold_outcomes(outcomes, strategy)


async def collect_async(
    items: Iterable[_C],
    fetch_one: Callable[[_C], Awaitable[_P]],
    make_failure: Callable[[_C, Exception, FailureKind], _F],
    strategy: FailureStrategy[_F],
) -> tuple[Sequence[_P], Sequence[_F]]:
    """Concurrently fetch every item, folding a strategy over the failures.

    The awaitable counterpart of `collect`. It launches all fetches at once (via
    `asyncio.gather`) for concurrency, so unlike the synchronous `collect` it is
    eager: every item is fetched before the strategy is folded over the results.
    A child task's `asyncio.CancelledError` is re-raised, never turned into a
    failure, so cancellation is not swallowed.

    Parameters
    ----------
    items
        The items to fetch.
    fetch_one
        Awaitably maps one item to its fetched payload, raising on failure.
    make_failure
        Builds a domain-specific `FetchFailure` from the item, the raised
        exception, and its classified `FailureKind`.
    strategy
        The pure reducer deciding the batch's response to each failure.

    Returns
    -------
    tuple
        The successfully fetched payloads (in order), and the collected failures.

    Raises
    ------
    FetchError
        If the strategy returns `Verdict.HALT` for a failure.

    Examples
    --------
    >>> import asyncio
    >>> store = {"a": 1, "c": 3}
    >>> async def fetch_one(url):
    ...     return store[url]
    >>> def make(url, exc, kind):
    ...     return FetchFailure(url=url, kind=kind, message=str(exc))
    >>> async def main():
    ...     return await collect_async(
    ...         ["a", "b", "c"], fetch_one, make, collect_all
    ...     )
    >>> payloads, failures = asyncio.run(main())
    >>> payloads
    (1, 3)
    >>> [failure.url for failure in failures]
    ['b']
    """
    materialized = list(items)
    results = await asyncio.gather(
        *(fetch_one(item) for item in materialized), return_exceptions=True
    )
    outcomes = (
        _to_outcome(result, functools.partial(make_failure, item))
        for item, result in zip(materialized, results, strict=True)
    )

    return _fold_outcomes(outcomes, strategy)


def _classify(exc: Exception) -> FailureKind:
    """Classify a caught exception as an unrecoverable or transient failure.

    A `ReferencedDocumentError` means the fetched bytes did not decode, which
    retrying cannot fix, so it is `FailureKind.UNRECOVERABLE`. Every other
    exception (a fetcher's own network or lookup error) is `FailureKind.TRANSIENT`.
    Matching the precise decode type matters because a fetcher may itself raise a
    bare `ValueError`, which must not be mistaken for a decode failure.

    Parameters
    ----------
    exc
        The exception a fetch attempt raised.

    Returns
    -------
    FailureKind
        `FailureKind.UNRECOVERABLE` for a decode failure, else
        `FailureKind.TRANSIENT`.

    Examples
    --------
    >>> _classify(ReferencedDocumentError("bad bytes"))
    <FailureKind.UNRECOVERABLE: 'unrecoverable'>
    >>> _classify(ValueError("some fetcher error"))
    <FailureKind.TRANSIENT: 'transient'>
    """
    if isinstance(exc, ReferencedDocumentError):
        return FailureKind.UNRECOVERABLE

    return FailureKind.TRANSIENT


class _Ok(msgspec.Struct, Generic[_P], frozen=True):
    """The outcome of one successful fetch, wrapping its payload.

    Examples
    --------
    >>> _Ok(42).payload
    42
    """

    payload: _P


class _Failed(msgspec.Struct, Generic[_F], frozen=True):
    """The outcome of one failed fetch: its failure value and raised exception.

    The `FetchFailure` value is what gets collected; the original ``exc`` is kept
    only so the shell can chain it (``raise FetchError(...) from exc``) when a
    strategy halts. The public value itself stays exception-free.

    Examples
    --------
    >>> failure = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> _Failed(failure, ValueError("boom")).failure.url
    'u'
    """

    failure: _F
    exc: Exception


def _attempt(
    thunk: Callable[[], _P],
    make_failure: Callable[[Exception, FailureKind], _F],
) -> _Ok[_P] | _Failed[_F]:
    """Run a fetch, turning any failure into a value.

    The single place a fetch/decode exception becomes a `FetchFailure`, so the
    broad ``except`` lives here alone. A `BaseException` that is not an
    `Exception` (a `KeyboardInterrupt`, or `asyncio.CancelledError`) is *not*
    caught, so it propagates.

    Parameters
    ----------
    thunk
        The fetch to run; returns the payload or raises.
    make_failure
        Builds the `FetchFailure` from the exception and its classified
        `FailureKind`.

    Returns
    -------
    _Ok or _Failed
        `_Ok` wrapping the payload on success, else `_Failed` wrapping the
        failure value.

    Examples
    --------
    >>> def make(exc, kind):
    ...     return FetchFailure(url="u", kind=kind, message=str(exc))
    >>> _attempt(lambda: 1 + 1, make).payload
    2
    >>> def boom():
    ...     raise ValueError("nope")
    >>> _attempt(boom, make).failure.kind
    <FailureKind.TRANSIENT: 'transient'>
    """
    try:
        return _Ok(thunk())
    except Exception as exc:  # best-effort: any fetch/decode failure becomes a value
        return _Failed(make_failure(exc, _classify(exc)), exc)


def _to_outcome(
    result: _P | BaseException,
    make_failure: Callable[[Exception, FailureKind], _F],
) -> _Ok[_P] | _Failed[_F]:
    """Turn one `asyncio.gather` result into an outcome, re-raising cancellation.

    `asyncio.gather` with ``return_exceptions=True`` yields either a payload or a
    caught exception. A `BaseException` that is not an `Exception` (notably
    `asyncio.CancelledError`) is re-raised rather than collected, so cancellation
    is never swallowed; any other exception becomes a `FetchFailure`.

    Parameters
    ----------
    result
        One element of a ``gather(..., return_exceptions=True)`` result.
    make_failure
        Builds the `FetchFailure` from the exception and its classified
        `FailureKind`.

    Returns
    -------
    _Ok or _Failed
        `_Ok` wrapping the payload, else `_Failed` wrapping the failure value.

    Raises
    ------
    BaseException
        If ``result`` is a `BaseException` that is not an `Exception`.

    Examples
    --------
    >>> def make(exc, kind):
    ...     return FetchFailure(url="u", kind=kind, message=str(exc))
    >>> _to_outcome(7, make).payload
    7
    >>> _to_outcome(ValueError("nope"), make).failure.kind
    <FailureKind.TRANSIENT: 'transient'>
    """
    if isinstance(result, BaseException):
        if not isinstance(result, Exception):
            raise result

        return _Failed(make_failure(result, _classify(result)), result)

    return _Ok(result)


def _fold_outcomes(
    outcomes: Iterable[_Ok[_P] | _Failed[_F]],
    strategy: FailureStrategy[_F],
) -> tuple[Sequence[_P], Sequence[_F]]:
    """Fold a strategy over a stream of outcomes, halting where it says to.

    Accumulates the successful payloads and the failures; for each failure the
    strategy sees the failures collected *before* it and the new one. The halting
    failure is appended before raising, so a `FetchError` carries it too, chained
    from the original exception (its ``__cause__``). Consumes the outcomes lazily,
    so a caller feeding a generator stops producing (and fetching) once the
    strategy halts.

    Parameters
    ----------
    outcomes
        The per-item outcomes, in order.
    strategy
        The pure reducer deciding the batch's response to each failure.

    Returns
    -------
    tuple
        The successful payloads and the collected failures.

    Raises
    ------
    FetchError
        If the strategy returns `Verdict.HALT` for a failure.

    Examples
    --------
    >>> failure = FetchFailure(url="u", kind=FailureKind.TRANSIENT, message="x")
    >>> outcomes = [_Ok(1), _Failed(failure, ValueError("x")), _Ok(3)]
    >>> successes, failures = _fold_outcomes(outcomes, collect_all)
    >>> successes
    (1, 3)
    >>> [item.url for item in failures]
    ['u']
    """
    successes: list[_P] = []
    failures: list[_F] = []

    for outcome in outcomes:
        if isinstance(outcome, _Ok):
            successes.append(outcome.payload)
        else:
            verdict = strategy(tuple(failures), outcome.failure)
            failures.append(outcome.failure)

            if verdict is Verdict.HALT:
                raise FetchError(tuple(failures)) from outcome.exc

    return tuple(successes), tuple(failures)
