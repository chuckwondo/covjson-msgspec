# Design tenets

The recurring principles behind the library's design. Each is stated as a
principle and its operational consequence; the
[architecture decision records](../adr/README.md) hold the specific decisions that
apply them, and [Core concepts](../concepts.md) works the type
model through in detail.

## Dependency injection at the edges

The core never reaches the network or imports a web framework: it accepts a seam
(a callable, a return value) and lets the caller wire in their choice, and it
imports optional dependencies locally inside helpers rather than at module scope.
So one core serves sync and async callers alike, and importing it drags in nothing
heavy.

## A functional core with an imperative shell

The core is pure functions over immutable data: helpers return values (a stream of
issues, a failure record) rather than mutating a shared accumulator or performing
effects. Effects (I/O, raising, sleeping, materializing a stream) live in a thin
shell at the edges: the codec entry points, `validate`'s `mode=`, and the injected
fetcher. Errors are values first, with an opt-in raise confined to the edge.

## Opt-in tiered validation, not `__post_init__`

`__post_init__` is only for local, O(1) invariants about a single object, and only
when a violation leaves the object uninterpretable in isolation. Anything
cross-cutting or data-scanning lives in an opt-in, tiered `validate()`, so decode
stays permissive: a repairable, slightly-nonconformant document still loads. See
[ADR-0002](../adr/0002-opt-in-tiered-validation.md).

## A byte-faithful model, lossy only in bridges

Decode preserves every spec-defined member exactly (temporal values stay raw ISO
8601 strings; numbers keep their precision). Conversions that lose information
happen only in the opt-in export bridges. The one carve-out is foreign members,
which decode drops ([ADR-0012](../adr/0012-foreign-members-dropped-on-decode.md)).

## Typed projection over a faithful core

Where one concrete type encodes several logical types, precise typing is an opt-in
projection (an accessor or a builder), never the stored representation, and never
element-typed subclasses or type guards. The stored form stays faithful; the
precision is a view you ask for. See
[ADR-0004](../adr/0004-ndarray-single-non-generic-class.md) and
[Core concepts](../concepts.md).
