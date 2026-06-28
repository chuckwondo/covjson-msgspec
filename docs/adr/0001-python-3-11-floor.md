# ADR-0001: Python 3.11 floor, coupled to titiler

## Status

Accepted

## Context

`covjson-msgspec` sets `requires-python = ">=3.11"`. Nothing in the library
needs 3.11 as such: the code would run on any later interpreter, and 3.12+ would
let us write a few things more precisely (PEP 695 `type` alias statements, PEP
696 `TypeVar` defaults from `typing` rather than `typing_extensions`). So the
floor is a choice, and the forces pulling on it are worth recording.

The library is built to slot into the titiler-covjson ecosystem (it is the
intended CoverageJSON layer under a titiler-based server). `titiler.core` is
`requires-python >=3.11` (verified against 0.26.0, Nov 2025, whose classifiers
run 3.11 through 3.14). Raising our floor above titiler's would cut the library
off from its primary intended consumers running on 3.11 -- the exact outcome the
floor choice exists to avoid.

The tempting reason to bump to 3.12 would be precise composite typing for the
axis-value model (a recursive alias such as
`float | int | str | tuple[AxisValue, ...]`, plus the polygon interior
currently typed `Any`). That payoff does not actually arrive with 3.12 under the
msgspec we build on. Tested on Python 3.12.3 + msgspec 0.21.1: decoding into a
recursive PEP 695 alias raises `RecursionError: maximum recursion depth exceeded
while analyzing a type`. Independently, msgspec's version-independent "at most
one array-like member per union" rule blocks unioning the tuple form and the
polygon form without collapsing the element back to a recursive alias (the same
`RecursionError`) or to `Any`. So the composite-typing precision is gated on
both a 3.12+ floor *and* a future msgspec that resolves recursive types -- 3.12
alone delivers none of it.

## Decision

Keep the floor at `>=3.11`, deliberately pinned to titiler's floor rather than
to any feature we want. Treat it as a coupling to track, not a technical
requirement: revisit only when titiler raises its own floor.

## Alternatives considered

- **Floor at `>=3.12` to adopt PEP 695 / PEP 696 now.** Rejected on two grounds.
  It would decouple us from titiler's current `>=3.11`, the precise outcome the
  coupling exists to prevent. And the headline payoff (precise recursive
  composite typing) is not delivered by 3.12 with current msgspec anyway, per
  the `RecursionError` finding above; the only real 3.12 gains available today
  are cosmetic (PEP 695 aliases for non-recursive types, `class C[T]` syntax,
  `typing.override`).
- **Float the floor to the latest stable Python with no declared coupling.**
  Same decoupling problem, with no compensating benefit, and it would make the
  ecosystem fit accidental rather than intentional.

## Consequences

- We pay for 3.11 support with less precise typing: type aliases use plain
  assignment instead of PEP 695 `type` statements, and the recursive polygon
  interior is typed `Any` because msgspec cannot resolve a recursive
  plain-assignment alias on 3.11 (no `ForwardRef` support there). PEP 696
  `TypeVar` defaults are imported from `typing_extensions`; that backport
  dependency disappears once the last generic user is removed, independently of
  the floor.
- The floor is a tracked coupling, not a settled endpoint. The gate to revisit
  is titiler raising its `requires-python` past 3.11. Even then, the
  composite-typing half of the payoff additionally waits on msgspec gaining
  recursive-type support; the two gates are independent, and only the
  conjunction makes a bump worthwhile.
