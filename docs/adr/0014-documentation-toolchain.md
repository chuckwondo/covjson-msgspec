# ADR-0014: Documentation toolchain (ProperDocs + mkdocstrings, static extraction)

## Status

Accepted

## Context

Issue #19 (design-labeled) has to choose a documentation toolchain for the first
release, and issues #21 (write the documentation) and #22 (the covjson-pydantic
comparison) are gated on it. The project's shape constrains the choice more than
taste does:

- It is a fully-typed library whose type signatures are a headline feature
  (`Typing :: Typed`), so the docs must render those signatures faithfully.
- Every module carries `from __future__ import annotations` (a documented
  convention), so all annotations are strings at runtime.
- The opt-in bridges type their public signatures with their dependency (for
  example `to_xarray(coverage: Coverage) -> xr.Dataset`) but import that
  dependency only under `TYPE_CHECKING` (and lazily inside helper bodies), so
  the bridge names have no runtime binding.
- The workflow is pure uv; the README, ADRs, and comparison are already
  Markdown; and code examples are already executable and CI-verified as doctests
  (`--doctest-modules`), so "executable examples" is a solved problem here.

The surrounding ecosystem is also in flux. As of mid-2026 the MkDocs project is
fragmenting: upstream MkDocs 2.0 removes the plugin system entirely, ships
unlicensed, drops YAML config, and offers no migration path, which breaks both
the Material theme and mkdocstrings. In response the ecosystem split into
ProperDocs (a conservative, maintained fork of MkDocs 1.x that preserves the
plugin API), Zensical (a from-scratch rewrite by the Material team, with the
mkdocstrings author aboard, that reads `mkdocs.yml` natively), and MaterialX (a
theme-only continuation). The decision therefore has to name both an extraction
model and an engine whose continuity is credible.

## Decision

Adopt **ProperDocs** (the maintained MkDocs 1.x fork) with the **Material** theme
and **mkdocstrings** for the API reference. mkdocstrings builds that reference
with **griffe**, a static-analysis library that reads a package by *parsing* its
source (its AST) rather than *importing* it, and extracts the modules, classes,
functions, signatures, and docstrings from that parse. That "parse, don't
import" model is exactly why it fits this codebase (see below). Configuration
lives in `properdocs.yml`, and the `docs` dependency group is dev-only tooling.

The decisive property is *static* API extraction. mkdocstrings/griffe parses the
source AST and never imports the package, so it renders
`to_xarray(coverage: Coverage) -> xr.Dataset` exactly as written even when
numpy, xarray, and pandas are not installed at build time. This was verified
empirically: built against `src/` in an environment without any bridge
dependency installed, the signature rendered faithfully. An import-based
extractor would instead have to resolve those string annotations at runtime,
where the bridge names exist only under `TYPE_CHECKING`, and would fall back to
raw strings or require every bridge dependency installed plus typehint
configuration. Static extraction is the tool-level expression of the same design
choice that put those imports under `TYPE_CHECKING` in the first place.

The choice is really "static griffe extraction over a Markdown-native, pure-uv
engine"; the engine is the swappable part. Because `properdocs.yml` is
MkDocs-1.x-schema YAML and mkdocstrings both emits and consumes the Sphinx
`objects.inv` inventory format, the durable investment (Markdown content,
numpy-style docstrings, griffe extraction, and the cross-project inventories) is
portable. Migrating to Zensical or back to MkDocs later is a config rename plus a
dependency swap: a reversible two-way door, so the ecosystem churn is bounded
rather than load-bearing.

## Theme and customization

The theme is a separate axis from the extraction engine: it is the presentation
layer (the page shell, navigation, search UI, and styling) and has no bearing on
how the API reference is extracted. We use **Material for MkDocs**, the de-facto
standard MkDocs theme. Two things earn it the slot. mkdocstrings emits its API
reference in specific CSS classes (`.doc`, `.doc-heading`, `.doc-signature`,
...) that Material styles out of the box, so the reference looks right with no
styling work; and it ships the things every docs site needs (enhanced search, a
light/dark toggle, a responsive layout) at near-zero configuration. Material is
in maintenance mode (fixes through Nov 2026; its forward path is Zensical's
built-in theming), which does not affect using it but does shape how far we
customize it.

Material is built to be customized, along a ladder of increasing power and
increasing coupling:

1. **Config**: `palette` (colors), `font`, `logo`, `favicon`, feature flags. No
   code; upgrade-proof.
2. **`extra_css` overriding Material's CSS custom properties**
   (`--md-primary-fg-color`, the `--md-typeset-*` type scale, per-scheme
   `[data-md-color-scheme]` selectors, and the mkdocstrings `.doc-*` classes).
   One stylesheet, no template coupling.
3. **Template overrides** via `theme.custom_dir`: extend Material's Jinja
   templates (header, footer, landing page). Powerful, but couples to Material's
   internal template structure.
4. **Fork the theme.**

**Decision: customize only at rungs 1 and 2.** Config and CSS-variable overrides
are portable: they survive Material upgrades and, because they do not depend on
Material's template internals, carry over to a future Zensical migration far
better than overridden templates would. Rung 3 is the opposite; it is the
investment most likely to be stranded by Material's maintenance mode. The cap is
also proportionate to the medium: a library's docs site earns more from
legibility and a clear API reference than from a bespoke visual identity, and
rungs 1 and 2 are enough to make the site recognizably the project's own. A
truly bespoke look (one no reader would clock as "a Material site") would need
rung-3 work, which the portability risk does not justify for the first release.

The scaffold wires this seam without committing to a design: a rung-1 palette in
`properdocs.yml` and a rung-2 `docs/stylesheets/extra.css` with the key
variables stubbed and commented. The actual visual design is deferred to #21.

## Alternatives considered

**Sphinx + autodoc (+ MyST).** Rejected. autodoc is import-based; combined with
`from __future__ import annotations` and `TYPE_CHECKING`-only bridge imports,
resolving the bridge return types needs the dependencies installed plus
`autodoc_mock_imports` and typehint configuration, and still fights the
codebase. Choosing Sphinx-but-autodoc would adopt the one extraction model this
repository actively works against.

**Sphinx + sphinx-autoapi (+ MyST).** The strongest Sphinx configuration here,
and static like griffe. It would be the pick if a printed handbook (PDF),
numbered figures or equations (`numfig`), or a back-of-book index (`genindex`)
became first-class goals, since Sphinx does those natively. Rejected for now:
heavier configuration and residual reStructuredText idioms, for capabilities a
small, mostly-prose-plus-API library site does not need. Recorded as the switch
target should those needs arise.

**Quarto + quartodoc.** Also griffe-based, so it renders signatures faithfully
too, and it has the best executable-narrative and multi-format (PDF, book)
story. Rejected: Quarto is a non-Python system binary, which breaks the pure-uv
workflow and adds a CLI to install in CI; quartodoc is younger; and its
executable-docs advantage is largely redundant with the existing doctests. The
pick only if live computational docs or book output become first-class goals.

**pdoc.** Rejected. It is import-based (the same annotation-resolution problem)
and API-reference-only, so it cannot host the narrative pages that #21 and #22
are.

**nbdev.** Rejected. It is a notebook-as-source-of-truth development
methodology, not a docs add-on; adopting it means authoring the library in
notebooks, which is incompatible with the `src/` layout, the four strict type
checkers, and the hand-authored functional core. Its docs are a byproduct of the
methodology, so there is no way to take only the docs.

**Plain MkDocs 1.x (not the fork).** Viable and mature today, but the upstream
is heading toward the plugin-less, incompatible 2.0. ProperDocs is precisely the
maintained continuation of 1.x, and it coexists with the `mkdocs` package rather
than conflicting, so adopting it costs nothing over plain MkDocs while
insulating the project from the 2.0 direction.

**Zensical (now).** The most-adopted successor and the likely long-term
destination, but as of mid-2026 it is pre-1.0, not at feature parity, and its
mkdocstrings support is preliminary (cross-references and backlinks are not yet
implemented). Cross-references across the API matter here, so Zensical is
deferred to the eventual migration target rather than the starting engine.

## Consequences

- The `docs` group is dev-only, so the published package's runtime dependency
  contract is unchanged and ADR-0010's wheel-floor policy does not apply to
  these tools; their floors are simply the versions the toolchain was validated
  against.
- Configuration is `properdocs.yml`. A migration to Zensical or MkDocs is a
  rename (the schema is identical) plus swapping the dependency; the
  reversibility is deliberate, not incidental.
- API extraction never imports the package or the optional bridges, so the docs
  build stays pure-Python and cannot be broken by a bridge dependency's install
  issues.
- The build loads external `objects.inv` inventories (CPython, numpy, xarray,
  pandas) so bridge signatures cross-link into upstream docs, and it emits our
  own inventory for downstream projects (for example titiler-covjson) to link
  into. A full build therefore reaches the network; a hermetic build would
  vendor those inventories.
- Signature formatting reuses the `ruff` already in the `dev` group; without it,
  signatures still render but are not line-wrapped.
- Gaps accepted: no back-of-book index (search plus the API navigation cover
  findability) and no first-class figure numbering (a plugin such as
  mkdocs-caption, added only if numbered diagrams appear). A revisit is warranted
  if PDF or book output, `numfig`, or `genindex` become first-class goals (which
  points to Sphinx + AutoAPI), or if executable computational narrative becomes
  central (which points to Quarto).
- Zensical is the tracked future migration; revisit once its mkdocstrings
  support reaches cross-reference parity.
