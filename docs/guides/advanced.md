# Advanced: fetcher patterns and scheduling

The [reference resolution](references.md) and [tile assembly](tiles.md) guides
introduced the injected fetcher. Because the fetcher owns all I/O policy, you can
compose sophisticated behavior around it without the library knowing: bounded
concurrency, rate limiting, back-pressure, and cancellation all live in your
fetcher, not in the core.

## Composing fetcher decorators

An `AsyncFetch` is just `Callable[[str], Awaitable[bytes]]`, so you wrap it to add
policy. A decorator over the `AsyncFetch -> AsyncFetch` seam can add:

- **A shared concurrency limit** across an entire batch (an `asyncio.Semaphore`,
  or `aiolimiter.AsyncLimiter` for a rate, or `aiometer` for both).
- **Token-bucket rate limiting** and **`Retry-After` honoring**, for a well-behaved
  client against a throttling server.
- **Circuit-breaking** and **AIMD back-off** that adapt to observed failures.

These are user-side patterns, not library features: lean on `aiolimiter`,
`aiometer`, or `anyio` rather than re-implementing limiters.

!!! note "Runnable examples land with #32"

    The runnable `adaptive` fetcher closure and the injected `Scheduler` seam for
    bounded, cancellable fan-out are tracked in
    [#32](https://github.com/chuckwondo/covjson-msgspec/issues/32). This guide
    surfaces and explains the patterns; the copy-pasteable code will be added here
    when that lands.

## Choosing an error strategy

Both reference resolution and tile assembly are best-effort: they collect failures
as values rather than aborting on the first. You pick how to react by passing a
`FailureStrategy`:

- `fail_fast`: stop and surface the first failure.
- `collect_all`: gather every failure, so one bad tile does not hide the rest.
- `halt_on_unrecoverable` / `stop_after`: stop on a class of failure, or after a
  count.

Read the resulting failures (`ReferenceFailure`, `TileFailure`) off the report and
decide what to do. See the
[reference-resolution & fetching reference](../reference/references.md).
