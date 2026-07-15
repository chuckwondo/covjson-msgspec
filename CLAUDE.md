# CLAUDE.md

`covjson-msgspec` is a fast, fully-typed [CoverageJSON](https://covjson.org/)
library built on [msgspec](https://jcristharif.com/msgspec/). The
[documentation](docs/index.md) is the user-facing introduction and the design
narrative; [`CONTRIBUTING.md`](CONTRIBUTING.md) is the contributor source of truth
for setup, project layout, conventions, and process. This file is the agent-facing
spine: it imports those, pulls in the design tenets, and records the working
agreements for how changes should be made.

@CONTRIBUTING.md

@docs/design/tenets.md

## Working agreements

- **Do not `git commit` until the change has been reviewed**, even for pre-agreed
  work. Branch off `main`; do not commit directly to `main`.
- **Draft GitHub issues and present them for review before creating them** (before
  `gh issue create`, and before substantive `gh issue edit` body rewrites).
- **`design`-labeled issues evaluate for an ADR** at the end of their design pass;
  see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the ADR format and
  [`docs/adr/`](docs/adr/) for the records.

## Work order

The agreed sequence for remaining work: features -> benchmarking -> docs and the
covjson-pydantic comparison, cheapest-first within each track. The
`0.1.0: first public release` milestone holds the issues committed to the first
release; deferred or externally-gated items are intentionally left milestone-less.
