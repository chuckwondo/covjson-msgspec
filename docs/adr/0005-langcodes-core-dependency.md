# ADR-0005: `langcodes` as a core dependency for BCP 47 validation

## Status

Accepted

## Context

CoverageJSON Spec 2 requires every key of an i18n object (a language map used
by `label`, `description`, category labels, and more) to be a BCP 47 (RFC
5646) language tag, or the special tag `"und"`. This was the last
mechanically-checkable MUST left unimplemented after the spec audit that
produced ADR-0001/0002 and the #36/#43 checks, deliberately split out because
it raised a question those PRs didn't need to answer: how should `validate()`
check tag well-formedness?

The architecture's stated tenet is that the core depends only on msgspec;
every other dependency (numpy, xarray, pandas, geopandas) lives behind an
opt-in bridge extra, lazy-imported inside helper bodies. The first plan for
this check honored that tenet literally: a dependency-free regex approximating
the RFC 5646 grammar. But a regex can only check *shape* -- it cannot tell
`"jp"` (well-formed, but not a real IANA-registered language subtag; the real
code for Japanese is `"ja"`) from `"ja"`. For a MUST spec-conformance check,
that is a real correctness gap, not a cosmetic one: `validate()`'s whole
purpose is to tell a caller their document actually conforms, and "shape
looks right" is a materially weaker guarantee than "this is a real tag."

The msgspec-only tenet exists to keep the *install* light: no heavy or native
dependencies pulled in for a caller who only wants to decode and encode
documents. That reason doesn't automatically rule out every possible
dependency; it rules out the kind of dependency the bridges carry (numpy,
xarray, geopandas -- large, sometimes native-compiled, each behind its own
extra). A candidate library that is itself small, pure Python, and adds no
further transitive dependencies in its base install doesn't compromise that
reason, so it's a different question from "should the core stay msgspec-only
no matter what."

`langcodes` (verified at 3.5.1) fits that profile: its base install (without
the optional `[data]` extra, which pulls in `language_data` for language
*display names* and demographics -- not needed here) has zero runtime
dependencies, is pure Python, MIT-licensed, ships a `py.typed` marker
(confirmed: mypy and basedpyright both pass with no override needed), and its
`tag_is_valid()` checks a parsed tag's language/script/region/variant subtags
against the real IANA registry data bundled in the base package.

One further wrinkle surfaced during implementation: `langcodes.tag_is_valid()`
alone is *too* lenient for this use case. Its primary purpose is locale
matching, so `Language.get()` normalizes POSIX-style underscores before
validating (`"en_US"` parses identically to `"en-US"`) -- meaning a bare call
would silently accept `"en_US"`, which is not valid BCP 47 wire syntax (RFC
5646 only allows `-` as the subtag separator). So the check that shipped is a
conjunction: a small structural regex restricting the character set to
letters, digits, and `-` (guarding exactly the leniency `langcodes` grants),
then `langcodes.tag_is_valid()` for the registry-aware semantic check.

## Decision

Add `langcodes>=3.5` to the core `dependencies` in `pyproject.toml` (not a
bridge extra), imported directly at the top of `validation.py` like `msgspec`
itself -- not lazy-imported inside a function, since this is core validation
code, not an opt-in bridge. The validity predicate
(`_is_valid_language_tag` in `validation.py`, `@cache`-wrapped since a
document commonly repeats the same handful of tags) combines a structural
character-set regex with `langcodes.tag_is_valid()` for the semantic check;
`_language_tag_issues` applies it per key and separately flags a
present-but-empty i18n map (`i18n.empty`), the other MUST this issue covers.

## Alternatives considered

**Dependency-free regex approximating the RFC 5646 grammar.** Rejected. It can
only validate shape, not the registry, so it cannot catch real,
common mistakes like a country code used where a language code belongs
(`"jp"` for Japanese). A MUST spec-conformance check that only checks shape is
a materially weaker guarantee than the spec actually requires, and the whole
point of `validate()` is to give a caller a trustworthy answer.

**`langcodes` as a lazy-imported optional extra, following the bridge
pattern.** Rejected. `validate()`'s contract is to deterministically collect
every issue in a document; if this one check's behavior (or its absence)
depended on whether an extra happened to be installed, the same document could
validate cleanly in one environment and fail in another. That non-determinism
is a worse trade than a small, dependency-free package landing in the core.
(This is also why the bridges themselves don't affect `validate()`: they
convert *out* of the model, they don't feed back into what "conformant" means.)

**Wait for `langcodes` to become a core dependency only if a future need
compounds it (e.g. locale display names).** Considered and rejected as
premature-caution: the `[data]` extra (needed for that hypothetical future
need) is exactly the boundary already drawn here, and deferring a
correctness-improving, zero-transitive-dependency package for a check that
exists today has no offsetting benefit.

## Consequences

- `dependencies` now lists two packages instead of one; CLAUDE.md's
  Architecture section is updated to name both and link here, rather than
  asserting "msgspec only."
- This sets the bar for any future core-dependency proposal: small, pure
  Python, zero (or negligible, extra-gated) transitive dependencies, and a
  correctness need that stdlib/regex genuinely cannot meet. A dependency that
  fails any of those still belongs behind a bridge extra, unchanged.
- The bridges' own lazy-import-inside-a-function pattern (numpy, xarray,
  pandas, geo) is unaffected: they remain optional, format-conversion
  conveniences, categorically different from a core spec-conformance check.
- If `langcodes` ever grows heavier (pulls in transitive deps, drops its
  `py.typed` marker, or the `[data]`-free base install stops being sufficient
  for `tag_is_valid()`), that is a gate to revisit this decision, not to
  quietly work around it.
