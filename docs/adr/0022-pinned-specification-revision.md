# ADR-0022: Conformance is pinned to one specification revision

## Status

Accepted

## Context

CoverageJSON is a living document. It has no dated editions and no normative
version scheme: the community specification is whatever `master` says today, and
its own text still carries a `TODO` section. Every section number this project
cites (in a docstring, an inline comment, a design page, or the conformance
matrix) is therefore a reference into a moving target.

The repository cites the specification heavily. There are 64 links to
`covjson/specification/blob/master/`, plus roughly forty bare prose citations of
the form `Spec 6.1.1:` or `Common Domain Types 2.9`. Until now, the revision all
of those were written against was recorded nowhere.

That is not a theoretical exposure. Two modules linked
`spec.md#92-ranges-object`, a section that does not exist in the specification
at any revision this project has read. It survived every CI run, every `prek`
pass, and every documentation build, because nothing checks an external link. It
was found only by a hand walk of the specification (#200), and by then it had
been wrong long enough that its origin is no longer recoverable.

The obvious anchor, the upstream `0.1.0` tag, does not work. It predates the
normative rewrite: it contains no RFC 2119 keywords at all, so a library that
grades enforcement by requirement level (mandatory to error, recommended to
warning, per [ADR-0002]) has nothing to grade against. The tag names a version
without supplying the content that would make the version meaningful.

The conformance matrix (#150) sharpens the need. Its value is the join of rule,
section, requirement level, and enforcement site. A join whose left-hand side is
"whatever `master` said the day each row was written" is not reproducible, and
an evaluator comparing this library against another cannot check the work.

## Decision

**This project conforms to a single named revision of the specification:**

```text
covjson/specification@2061005546ef7ffe6c3f98bac5b897bfedce3365   (2022-05-27)
```

covering both `spec.md` and `domain-types.md` (the Common CoverageJSON Domain
Types specification, which forms part of it).

Every section number in a docstring, comment, or documentation page means that
revision. A citation is verified against that text, quoted verbatim from it, and
labeled by what the text actually does: **stated** with an RFC 2119 keyword,
**stated without one** (definitional), or **entailed** by what is stated
(inferred). "The spec says MUST" is a claim to check against the pinned text,
not a recollection.

Raising the pin is a deliberate act, never a silent bump. It means re-walking
the specification against the new revision, re-verifying the citations, and
updating the conformance matrix to match. A new revision is adopted because
someone did that work, not because upstream moved.

## Alternatives considered

**Track `master` and cite it live.** Rejected, and it is the status quo this ADR
replaces. It reads as always-current but is the opposite: a renumbering upstream
silently retargets 64 links and forty prose citations at once, with no build
step that would notice. The dead `#92-ranges-object` anchor is the proof that
this failure mode is real and silent.

**Pin to the upstream `0.1.0` tag.** Rejected on content, not on principle. It
would be the natural choice if it worked, but the tag predates the normative
rewrite and states no RFC 2119 requirements, so it cannot support the
requirement-level grading this library's validation tiers rest on.

**Vendor the specification text into the repository as the pin.** Rejected as
the *record* of the decision, though not forever as an enforcement mechanism.
The revision identity is a fact about a decision and belongs in an ADR; a
vendored copy is machinery for checking that citations still resolve, which is a
separate question with its own cost (roughly 85 KB of third-party text plus its
license, following the provenance pattern in `tests/corpus/README.md`).
Recording the pin does not depend on vendoring, so it should not wait for it.

**Cite the OGC rendering (21-069r2) instead.** Rejected. It is a genuinely
versioned document, which is the property this decision wants, but its clause
numbering differs from the community `spec.md` (section 6.1.1 there is clause
9.6.1.1), and the community document is the one the ecosystem and the sibling
implementations read. Where the OGC clause number helps a reader, cite it
alongside rather than instead ([ADR-0011] does this).

## Consequences

- A citation is now falsifiable. "Spec 6.2 states X" resolves to one specific
  text, so a reviewer can check it and a wrong claim is a defect rather than a
  matter of opinion.
- The conformance matrix (#150) is reproducible: its `Source` column grades
  against a fixed document, and an evaluator can repeat the walk.
- Links still read `blob/master/` and therefore still drift. Retargeting all 64
  to the pinned SHA is mechanical follow-up work, tracked in #204 so it does not
  ride along with prose changes.
- Nothing yet *enforces* the pin. A dead anchor or a renumbered section is still
  caught only by review, which is exactly how `#92-ranges-object` survived.
  Mechanical enforcement needs the specification vendored at this revision,
  which is deliberately left as its own decision (#205).
- Upstream fixes do not reach us automatically. That is the intended trade: a
  conformance library should be able to say which text it conformed to, and a
  citation that silently follows upstream cannot.
- The pin is a two-way door. Raising it costs a re-walk, but nothing is
  structurally coupled to this particular SHA, so the cost is proportional to
  the specification's own churn rather than to anything in this codebase.

[ADR-0002]: 0002-opt-in-tiered-validation.md
[ADR-0011]: 0011-axis-ordering-checker-seam.md
