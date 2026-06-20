#!/usr/bin/env bash
#
# pin-precommit-shas.sh -- re-pin every .pre-commit-config.yaml hook `rev:` to
# the commit SHA of its release tag, keeping the tag in a trailing comment:
#
#     rev: v6.0.0                ->  rev: 3e8a87...2c8c # v6.0.0
#     rev: 9257c6...09a9 # v1.2  ->  rev: 9257c6...09a9 # v1.2   (refreshed)
#
# WHY: a mutable tag can be silently repointed if a hook repo is compromised,
# and pre-commit hooks run arbitrary code at commit time -- so we pin to an
# immutable commit SHA. `prek autoupdate` / `pre-commit autoupdate` only ever
# write tags, so run THIS afterward to restore the SHA pins:
#
#     prek autoupdate && scripts/pin-precommit-shas.sh
#
# IDEMPOTENT: the tag is the source of truth -- taken from the trailing comment,
# or from `rev:` itself right after autoupdate -- and is re-resolved to its
# current commit SHA on every run. `repo: local` / `repo: meta` blocks have no
# upstream to resolve, so they are left untouched.
#
# Tags are resolved with `git ls-remote`: no GitHub token, works on any git
# host, and needs no auth for public repositories.
#
# Usage: pin-precommit-shas.sh [path/to/.pre-commit-config.yaml]
#                              (defaults to ./.pre-commit-config.yaml)

set -euo pipefail

readonly config="${1:-.pre-commit-config.yaml}"
[[ -f "$config" ]] || {
  echo "error: $config not found" >&2
  exit 1
}

# --- line patterns ----------------------------------------------------------
# A regex held in a variable must be matched *unquoted* (`=~ $re`) for bash to
# treat it as a pattern; naming them also documents each capture group.

# "  - repo: <url>"  ->  group 1 = repo URL (plus any trailing text).
readonly repo_line_re='^[[:space:]]*-[[:space:]]+repo:[[:space:]]+(.+)$'

# "    rev: <ref> [# <tag>]"  ->  groups:
#   1 = leading indentation        (re-emitted verbatim)
#   2 = current ref                (a SHA or tag; ignored -- we re-resolve)
#   4 = tag from the # comment      (optional; the source of truth if present)
readonly rev_line_re='^([[:space:]]*)rev:[[:space:]]*([^[:space:]#]+)([[:space:]]*#[[:space:]]*(.+))?$'

# --- helpers ----------------------------------------------------------------

# Echo the repo URL if <line> opens a hook block ("- repo: <url>"); else echo
# nothing. Returns success either way, so `$(...)` assignment is set -e safe.
repo_url_from_line() {
  if [[ "$1" =~ $repo_line_re ]]; then
    printf '%s' "${BASH_REMATCH[1]%%[[:space:]]*}" # trim any trailing space
  fi
}

# True unless <url> is a pre-commit pseudo-repo (`local`/`meta`) that has no
# upstream to resolve.
is_remote_repo() {
  [[ -n "$1" && "$1" != "local" && "$1" != "meta" ]]
}

# Echo the commit SHA that <tag> names on <remote> (empty if unresolved). An
# annotated tag has a peeled ref (refs/tags/<tag>^{}) pointing at the commit; a
# lightweight tag does not, so fall back to the plain ref. The commit is the
# object pre-commit checks out for `rev:`.
resolve_tag_to_sha() {
  local remote="$1" tag="$2" sha

  sha="$(git ls-remote "$remote" "refs/tags/${tag}^{}" 2>/dev/null | cut -f1)"
  [[ -n "$sha" ]] || sha="$(git ls-remote "$remote" "refs/tags/${tag}" 2>/dev/null | cut -f1)"
  printf '%s' "$sha"
}

# Echo <line> with its `rev:` re-pinned to a commit SHA (tag kept in a trailing
# comment). If <line> is not a rev line, or the tag cannot be resolved, echo it
# unchanged. <repo> is the enclosing block's URL. Notes each outcome to stderr.
repin_rev_line() {
  local line="$1" repo="$2"

  [[ "$line" =~ $rev_line_re ]] || {
    printf '%s' "$line"
    return
  }

  local indent="${BASH_REMATCH[1]}"
  local tag="${BASH_REMATCH[4]:-${BASH_REMATCH[2]}}" # comment tag, else the ref
  local sha

  sha="$(resolve_tag_to_sha "$repo" "$tag")"

  if [[ -z "$sha" ]]; then
    echo "warn: could not resolve ${repo} tag ${tag}; left unchanged" >&2
    printf '%s' "$line"
  else
    echo "pinned ${repo##*/} ${tag} -> ${sha}" >&2
    printf '%s' "${indent}rev: ${sha} # ${tag}"
  fi
}

# --- rewrite ----------------------------------------------------------------

# Read <config> line by line and echo it back with every remote hook's `rev:`
# re-pinned to its tag's commit SHA. A line-oriented rewrite (not a YAML parser)
# preserves formatting, comments, and key order exactly -- only `rev:` lines
# change. The input file is named right here, next to the loop that reads it.
rewrite_config() {
  local config_file="$1" current_repo="" line url

  while IFS= read -r line || [[ -n "$line" ]]; do
    url="$(repo_url_from_line "$line")"

    if [[ -n "$url" ]]; then
      current_repo="$url" # entered a new hook block
      printf '%s\n' "$line"
    elif is_remote_repo "$current_repo"; then                   # rev: line -> pinned;
      printf '%s\n' "$(repin_rev_line "$line" "$current_repo")" # other lines pass through
    else
      printf '%s\n' "$line" # outside any remote block
    fi
  done <"$config_file"
}

# Rewrite into a temp file, then replace the original only if it actually changed.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

rewrite_config "$config" >"$tmp"

if cmp -s "$config" "$tmp"; then
  echo "$config already current" >&2
else
  mv "$tmp" "$config"
  echo "updated $config" >&2
fi
