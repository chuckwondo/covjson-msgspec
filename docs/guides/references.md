# Resolving references

A CoverageJSON document may defer parts of itself to other URLs: a coverage's
`domain`, or a range, may be a URL string rather than an inline object. Inlining
those means fetching URLs, which is I/O the core does not perform itself. Instead
you inject a **fetcher**, a plain callable mapping a URL to bytes, so caching,
auth, retries, and throttling stay yours.

## Sync and async

```python
# Sync: you supply Fetch = Callable[[str], bytes]
report = coverage.resolve_references(fetch)
resolved = report.value          # the coverage with URL-string parts inlined

# Async: AsyncFetch = Callable[[str], Awaitable[bytes]], so independent fetches
# run concurrently via asyncio.gather (ideal under Starlette / FastAPI / litestar)
report = await coverage.resolve_references_async(afetch)
resolved = report.value
```

The free functions `resolve_references` and `resolve_references_async` do the same
over any document. Each returns a `ResolveReport` carrying the resolved `value` and
any failures.

## Bounded concurrency and error strategy

The async fan-out is unbounded by design: because the fetcher owns all I/O policy,
you bound concurrency *there* (wrap your fetcher with an `asyncio.Semaphore`, or a
limiter such as `aiolimiter`). The [advanced patterns](advanced.md) guide covers
composing fetcher decorators for rate control and back-pressure.

Resolution is best-effort: rather than aborting on the first failed fetch, it
collects failures as values. You choose how to react by passing a `FailureStrategy`
(for example `fail_fast`, or `collect_all` to gather every failure) and reading the
`ReferenceFailure`s off the report. See the
[reference-resolution & fetching reference](../reference/references.md).

Splitting a large *range* across tile documents is handled separately, by
[assembling tiles](tiles.md), which shares this same fetcher seam.
