# Contributing to covjson-msgspec

This is the contributor guide: how to set up, the conventions the code follows,
and how changes get proposed and reviewed. It is the single source of truth for
those. For the library itself, the [documentation](docs/index.md) is the
user-facing introduction; the [design decisions](docs/design/index.md) and
[design tenets](docs/design/tenets.md) explain why the code is shaped the way it
is.

The canonical task list is GitHub issues under the
`0.1.0: first public release` milestone
(`gh issue list --milestone "0.1.0: first public release"`).

## Development setup

This project uses [uv](https://docs.astral.sh/uv/).

```sh
uv sync                          # core + all bridges + dev tooling
uv sync --group typecheck        # also installs basedpyright (Node runtime)
uv sync --group bench            # also installs the benchmark deps
uv sync --group docs             # also installs the docs toolchain (ProperDocs)

uv run pytest                    # tests + doctests (--doctest-modules is on)
uv run ruff check                # lint
uv run ruff format               # format
uv run mypy                      # strict; blocking in CI
uv run basedpyright              # strict; blocking in CI (needs typecheck group)
uvx ty check                     # informational
uvx pyrefly check                # informational
uv run prek run --all-files      # the pre-commit suite

uv run --group docs properdocs build   # build the docs site (serve: ... serve)
```

Type checking runs four checkers: mypy (strict) and basedpyright (strict) are
blocking; ty and pyrefly are informational. basedpyright bundles a Node runtime
and may not build on every local machine; rely on CI when it cannot run locally.

The Python floor is `>=3.11`, set to match titiler's floor; raising it is gated on
titiler moving first (see [ADR-0001](docs/adr/0001-python-3-11-floor.md)).

## Project layout

A thin core (msgspec plus langcodes, both pure Python) with opt-in bridges, each
lazy-importing its own dependency.

Core model (`src/covjson_msgspec/`):

- `_base.py`: `CovJSONStruct`, the shared wire/codec base (`frozen=True`,
  `omit_defaults=True`, `rename="camel"`). Subclass it only for types that cross
  the CoverageJSON wire.
- Spec structs: `axis.py`, `domain.py`, `range.py`, `coverage.py` (with the codec
  helpers), `referencing.py`, `parameter.py`, `i18n.py`.
- `validation.py`: the opt-in, tiered `validate()`.
- `_fetch.py`: the injected-fetcher seam (`Fetch` / `AsyncFetch`); the core never
  performs I/O.
- `media_type.py` (HTTP helpers), `subset.py` (`isel` / `sel`), `_bridging.py`
  and `_repr.py` (shared helpers).

Bridges, each behind an extra: `numpy` (methods on `NdArray`, in `range.py`),
`xarray.py`, `pandas.py`, `geo.py`. The [design decisions](docs/design/index.md)
cover why a bridge is a method versus a free function in its own module.

## Coding conventions

The crisp rules. Where a rule carries deeper reasoning, it links to
[Conventions, explained](docs/design/index.md#conventions-explained).

Code:

- `from __future__ import annotations` at the top of every module (after the
  docstring). Write bare annotations; quote only the strings inside `cast()`.
- Absolute imports only (relative imports are banned by ruff).
- Do not import another module's `_private` member. To share an internal helper,
  give it a home in a `_`-prefixed module and import its non-underscore name
  ([why](docs/design/index.md#conventions-explained)).
- Place `_private` module functions after the public API; give them full
  numpy-style docstrings with cheap, runnable examples.
- Prefer implicit iteration (comprehensions, generator expressions,
  `itertools.chain`) over explicit `for` / `while` where it stays readable.
- Build behavior from small, single-purpose, composable functions; compose with
  the standard library rather than point-free combinators that fight the type
  checkers.
- A checker/transform helper returns an iterable rather than mutating a shared
  accumulator.
- Trust type contracts: no runtime `isinstance` check against a type the
  parameter's annotation already excludes.
- Assign an exception message to a `msg` variable before `raise X(msg)` (ruff EM).
- Name a bridge for its destination library or format (`to_pandas`, `to_xarray`,
  `to_geojson`); `to_datatree` is the type-named exception.
- snake_case attributes map to lowerCamelCase wire names via `rename="camel"`, so
  ruff N815 is intentionally not applied.
- Put blank lines around block statements (if / for / while / with / try / def /
  class).
- Link the CoverageJSON spec from docstrings and comments where it teaches the
  wire format; centralize the spec and RFC links in the module docstring.
- Prefer colons, parentheses, or a shorter sentence to em-dashes; write a needed
  one as a double hyphen (`--`), never the Unicode character. A definition list
  uses a colon.

Docstrings and doctests:

- Keep doctest lines within 88 characters (ruff E501 checks docstrings); wrap long
  byte blobs via implicit string concatenation.
- Multi-line JSON in docstrings uses `indent=2` with short arrays kept inline. Do
  not reformat verbatim `msgspec.json.format(indent=2)` output.

Tests:

- Place `_helper` functions after all `test_` functions (exception: a helper
  called at module-load time, for example inside a `@pytest.mark.parametrize`
  decorator, must precede its first use).
- Prefer `@pytest.mark.parametrize` over `for` loops inside test functions.

## Contribution process

- Branch off `main`; do not commit directly to `main`.
- Commit messages hard-wrap (about a 50-character subject, body wrapped at ~72).
  Repo Markdown files hard-wrap prose at 80 columns (not code blocks, tables, or
  long URLs). GitHub issue / PR / comment bodies do not hard-wrap.
- Open a pull request against `main`; PR bodies are not hard-wrapped.
- A `design`-labeled issue evaluates for an ADR at the end of its design pass. If
  one is warranted, write it under [`docs/adr/`](docs/adr/) using the
  [template](docs/adr/template.md) (Title, Status, Context, Decision, Alternatives
  considered, Consequences) and link it from the issue.
