# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs): the detailed,
append-only historical record of cross-cutting decisions whose rationale a
reader could not recover from the code alone. CLAUDE.md (Working agreements) is
the source of truth for when an ADR is warranted; this README covers only the
mechanics of the directory.

## Numbering

ADRs are numbered with a zero-padded, plain-sequential `NNNN` prefix
(`0001-title.md`, `0002-title.md`, ...), assigned in the order they are
accepted, regardless of topic. The number is permanent: a superseded ADR keeps
its file and number and is marked `Superseded by ADR-NNNN` rather than deleted
or renumbered.

`template.md` is the copyable starting point and is intentionally not numbered,
so it never consumes a sequence number.

## Format

Each ADR follows the lightweight template in [template.md](template.md):

- **Title** -- `# ADR-NNNN: <decision>`.
- **Status** -- `Accepted` or `Superseded by ADR-NNNN`.
- **Context** -- the forces at play; what made this a decision worth recording.
- **Decision** -- what we chose, stated plainly.
- **Alternatives considered** -- the real rejected options and why they lost.
- **Consequences** -- what follows, including the costs we accept.

Keep each ADR self-contained, and do not restate conventions already in
CLAUDE.md.

## Index

- [ADR-0001](0001-python-3-11-floor.md) -- Python 3.11 floor, coupled to titiler
- [ADR-0002](0002-opt-in-tiered-validation.md) -- Cross-cutting checks live in
  opt-in `validate()`, not `__post_init__`
- [ADR-0003](0003-issue-code-enum.md) -- `IssueCode` enum, closed because the
  library owns the codes; category matching deferred
- [ADR-0004](0004-ndarray-single-non-generic-class.md) -- `NdArray` as a
  single, non-generic class; element typing via `validate(check_values=True)`

Some decisions are recorded in ADRs that land with their implementation rather
than here; see the issue tracker for the in-flight set.
